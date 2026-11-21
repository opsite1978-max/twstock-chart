#!/usr/bin/env python3
"""
抓公開資訊觀測站「買回自己公司股份彙總統計表」(t35sc09)，上市＋上櫃，近 3 年董事會決議的庫藏股，
存成 data/treasury.json 給網頁讀。在 GitHub Actions 上執行，只用標準函式庫。
每列＝[決議日, 目的, 預定股數, 價格下限, 價格上限, 期間起, 期間迄, 是否完畢, 已買回股數, 執行率%, 已買回金額, 平均價, 占已發行%, 未完畢原因]
目的：1 轉讓員工、2 認股權或可轉債轉換、3 維護公司信用及股東權益
"""
import json, os, re, html, time, datetime, urllib.request, urllib.parse
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'treasury.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TW = datetime.timezone(datetime.timedelta(hours=8))

def roc(d): return '%d%02d%02d' % (d.year - 1911, d.month, d.day)
def iso(s):
    m = re.fullmatch(r'(\d{2,3})/(\d{1,2})/(\d{1,2})', s.strip())
    return '%04d-%02d-%02d' % (int(m.group(1)) + 1911, int(m.group(2)), int(m.group(3))) if m else None
def num(s):
    s = s.replace(',', '').replace('&nbsp;', '').strip()
    try: return float(s) if '.' in s else int(s)
    except ValueError: return None

def fetch(typek, d1, d2):
    data = {'encodeURIComponent': 1, 'step': 1, 'firstin': 1, 'off': 1, 'TYPEK': typek, 'd1': roc(d1), 'd2': roc(d2), 'RD': 1}
    for k in range(4):
        try:
            req = urllib.request.Request('https://mopsov.twse.com.tw/mops/web/ajax_t35sc09', data=urllib.parse.urlencode(data).encode(),
                                         headers={'User-Agent': UA, 'Referer': 'https://mopsov.twse.com.tw/mops/web/t35sc09'})
            t = urllib.request.urlopen(req, timeout=120).read().decode('utf-8', 'ignore')
            if '買回自己公司股份彙總統計表' in t: return t
            print(typek, 'unexpected page', len(t))
        except Exception as e: print(typek, 'err', e)
        time.sleep(10 * (k + 1))
    return None

def parse(t):
    rows = []
    for tr in re.findall(r"<tr class=\s*'(?:even|odd)'[^>]*>(.*?)</tr>", t, re.S):
        tds = [html.unescape(re.sub(r'<[^>]+>', '', x)).strip() for x in re.findall(r'<td[^>]*>(.*?)</td>', tr, re.S)]
        if len(tds) < 20 or not re.fullmatch(r'\d{4}', tds[1]) or not iso(tds[3]): continue
        rows.append((tds[1], tds[2], [iso(tds[3]), num(tds[4]), num(tds[6]), num(tds[7]), num(tds[8]), iso(tds[9]), iso(tds[10]), tds[11],
                                       num(tds[13]), num(tds[15]), num(tds[16]), num(tds[17]), num(tds[18]), tds[19][:120]]))
    return rows

def main():
    today = datetime.datetime.now(TW).date(); d1 = today - datetime.timedelta(days=3 * 365)
    out = {}
    got = 0
    for typek, mk in (('sii', '上市'), ('otc', '上櫃')):
        t = fetch(typek, d1, today)
        if not t: continue
        rs = parse(t); got += len(rs); print(typek, len(rs))
        for code, name, r in rs:
            o = out.setdefault(code, {'n': name, 'm': mk, 'r': []}); o['r'].append(r)
        time.sleep(3)
    if not got: raise SystemExit('抓不到資料')
    for o in out.values(): o['r'].sort(key=lambda r: r[0] or '', reverse=True)
    json.dump({'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'from': d1.isoformat(),
               'cols': ['決議日', '目的', '預定股數', '價格下限', '價格上限', '期間起', '期間迄', '是否完畢', '已買回股數', '執行率', '已買回金額', '平均價', '占已發行', '原因'],
               'rows': out}, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    print('companies', len(out), 'records', got)

if __name__ == '__main__': main()
