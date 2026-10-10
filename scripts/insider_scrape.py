#!/usr/bin/env python3
"""
抓公開資訊觀測站「內部人持股異動事後申報表」(query6_1) 與
「董監事持股餘額明細」(stapap1)，存成 data/insider/<代號>.json 給網頁讀。

在 GitHub Actions 上執行（使用者不需要自己跑）。只用 Python 標準函式庫。
- 追蹤清單：insider/watchlist.txt（一行一個代號，# 之後是註解）
- 每檔回補近 BACK_MONTHS 個月；已抓到的月份不重抓，最近兩個月會定期重抓（補申報、更正）
- 每次執行有時間上限，做不完下次接著做
"""
import json, os, re, sys, time, html, datetime, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'insider')
WATCH = os.path.join(ROOT, 'insider', 'watchlist.txt')
BACK_MONTHS = int(os.environ.get('BACK_MONTHS', '36'))
BUDGET_SEC = int(os.environ.get('BUDGET_SEC', '2400'))
GAP = float(os.environ.get('GAP_SEC', '1.6'))
HOST = 'https://mopsov.twse.com.tw/mops/web/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TW = datetime.timezone(datetime.timedelta(hours=8))
T0 = time.time()

class Blocked(Exception):
    pass

_last = [0.0]
def post(prog, params):
    wait = GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    body = dict(encodeURIComponent='1', step='1', firstin='1', off='1', TYPEK='all', isnew='false')
    body.update(params)
    req = urllib.request.Request(HOST + 'ajax_' + prog, data=urllib.parse.urlencode(body).encode(),
                                 headers={'User-Agent': UA, 'Referer': HOST + prog,
                                          'Content-Type': 'application/x-www-form-urlencoded'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                s = r.read().decode('utf-8', 'replace')
            _last[0] = time.time()
            if 'FOR SECURITY REASONS' in s or '查詢過於頻繁' in s or '頻繁' in s[:3000] and len(s) < 3000:
                raise Blocked(s[:200])
            return s
        except Blocked:
            raise
        except Exception as e:
            _last[0] = time.time()
            print('  retry', prog, params, e, flush=True)
            time.sleep(8 * (attempt + 1))
    raise RuntimeError('request failed')

def strip(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()

def num(x):
    x = x.replace(',', '').strip()
    try:
        return int(float(x))
    except ValueError:
        return 0

def cells(tr):
    return re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S | re.I)

def nums_of(cell):
    parts = re.split(r'<br\s*/?>', cell, flags=re.I)
    out = []
    for p in parts:
        t = strip(p)
        if re.fullmatch(r'-?[\d,]+(\.\d+)?', t):
            out.append(num(t))
    return out

NO_DATA = ('查無', '無資料', '尚未申報', '不存在', '無此公司', '無符合')

def parse_query6(s):
    """回傳 (公司名稱, 市場別, rows)；rows=None 代表查無資料"""
    m = re.search(r'本資料由\s*\(([^)]*)\)\s*(\S+)\s*公司提供', strip(s))
    mkt, name = (m.group(1), m.group(2)) if m else ('', '')
    rows = []
    for tr in re.split(r'<tr', s, flags=re.I)[1:]:
        c = cells(tr)
        if len(c) < 7:
            continue
        role, nm, kind = strip(c[0]), strip(c[1]), strip(c[2])
        a, b, d, e = nums_of(c[3]), nums_of(c[4]), nums_of(c[5]), c[6]
        en = nums_of(e)
        if len(a) < 5 or len(b) < 5 or len(d) < 5 or len(en) < 5:
            continue
        note = ''
        mm = re.search(r'身份別備註：(.*)', strip(e))
        if mm:
            note = mm.group(1).strip()
        rows.append([role, nm, kind] + a[:5] + b[:5] + d[:5] + en[:5] + [note])
    if not rows:
        t = strip(s)
        if any(k in t for k in NO_DATA) or len(t) < 400:
            return name, mkt, None
    return name, mkt, rows

def parse_stapap1(s):
    t = strip(s)
    def g(label):
        m = re.search(label + r'\s*([\d,]+)', t)
        return num(m.group(1)) if m else None
    tot, pl = g('全體董監持股合計'), g('全體董監持股設質合計')
    if tot is None:
        return None
    return {'total': tot, 'pledge': pl or 0}

def ym_list(today):
    # 某月資料在次月 15 日前申報；15 日（含）以前只把「上上個月」當作已到期
    y, m = today.year, today.month
    last = (y, m - 1) if today.day > 15 else (y, m - 2)
    ly, lm = last
    while lm <= 0:
        lm += 12; ly -= 1
    out = []
    yy, mm = ly, lm
    for _ in range(BACK_MONTHS):
        out.append('%04d-%02d' % (yy, mm))
        mm -= 1
        if mm == 0:
            mm = 12; yy -= 1
    return out  # 新到舊

def roc(ym):
    y, m = ym.split('-')
    return str(int(y) - 1911), m

def load(code):
    p = os.path.join(DATA, code + '.json')
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return json.load(f)
    return {'code': code, 'name': '', 'market': '', 'months': {}}

def save(d):
    os.makedirs(DATA, exist_ok=True)
    d['months'] = dict(sorted(d['months'].items(), reverse=True))
    with open(os.path.join(DATA, d['code'] + '.json'), 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, separators=(',', ':'))

def watchlist():
    codes = []
    if os.path.exists(WATCH):
        for line in open(WATCH, encoding='utf-8'):
            c = line.split('#')[0].strip()
            if re.fullmatch(r'[0-9A-Z]{4,6}', c) and c not in codes:
                codes.append(c)
    return codes

def need(rec, ym, months, now):
    if rec is None:
        return True
    idx = months.index(ym)
    fetched = datetime.datetime.fromisoformat(rec.get('at', '2000-01-01T00:00:00+08:00'))
    age_days = (now - fetched).total_seconds() / 86400
    if rec.get('s') == 'none':
        # 尚未申報：最近兩個月每 12 小時重試；更舊的每 20 天重試一次
        return age_days > (0.5 if idx < 2 else 20)
    if idx < 2:
        return age_days > 3  # 最近兩個月：補申報、更正
    return False

def write_index(codes):
    idx = []
    for c in codes:
        d = load(c)
        ok = [k for k, v in d['months'].items() if v.get('s') == 'ok']
        idx.append({'code': c, 'name': d.get('name', ''), 'market': d.get('market', ''),
                    'months': len(ok), 'latest': max(ok) if ok else ''})
    with open(os.path.join(DATA, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump({'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'),
                   'stocks': idx}, f, ensure_ascii=False, indent=0)

def main():
    now = datetime.datetime.now(TW)
    months = ym_list(now.date())
    codes = watchlist()
    os.makedirs(DATA, exist_ok=True)
    print('watchlist', len(codes), 'months', months[-1], '~', months[0], flush=True)
    done_all = True
    try:
        # 先做「新股票」與「最近月份」，讓網頁盡快可用；再往回補舊月份
        order = []
        for ym_i, ym in enumerate(months):
            for c in codes:
                order.append((ym_i, c, ym))
        order.sort(key=lambda t: (t[0] > 1, t[0], t[1]))
        cache = {}
        dirty = set()
        for _, code, ym in order:
            if time.time() - T0 > BUDGET_SEC:
                done_all = False
                print('time budget reached', flush=True)
                break
            d = cache.get(code) or load(code)
            cache[code] = d
            rec = d['months'].get(ym)
            if not need(rec, ym, months, now):
                continue
            y, m = roc(ym)
            s = post('query6_1', {'co_id': code, 'year': y, 'month': m})
            name, mkt, rows = parse_query6(s)
            if name and not d.get('name'):
                d['name'], d['market'] = name, mkt
            stamp = now.isoformat(timespec='minutes')
            if rows is None:
                d['months'][ym] = {'s': 'none', 'at': stamp}
                print(code, ym, 'none', flush=True)
            else:
                rec = {'s': 'ok', 'at': stamp, 'r': rows}
                try:
                    st = parse_stapap1(post('stapap1', {'co_id': code, 'year': y, 'month': m}))
                    if st:
                        rec['dir'] = st
                except Blocked:
                    raise
                except Exception as e:
                    print('  stapap1 fail', code, ym, e, flush=True)
                d['months'][ym] = rec
                print(code, ym, len(rows), 'rows', flush=True)
            dirty.add(code)
            if len(dirty) and int(time.time()) % 5 == 0:
                for c in list(dirty):
                    save(cache[c])
                dirty.clear()
        for c in dirty:
            save(cache[c])
    except Blocked as e:
        done_all = False
        print('BLOCKED by MOPS, stop this run:', e, flush=True)
        for c, d in cache.items():
            save(d)
    write_index(codes)
    print('done' if done_all else 'partial', 'in', int(time.time() - T0), 's', flush=True)

if __name__ == '__main__':
    main()
