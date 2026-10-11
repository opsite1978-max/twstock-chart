#!/usr/bin/env python3
"""
三大法人每日買賣超（證交所 T86、櫃買中心三大法人買賣明細）→ data/insti/days/<日期>.json，
再彙整成選股用的 data/insti/summary.json。在 GitHub Actions 上執行，只用標準函式庫。

  python3 scripts/insti_scrape.py update     補抓最近約 95 天缺少的交易日、產生 summary.json

每日檔：{"d":"YYYY-MM-DD","r":{"代號":[外資淨買張, 投信淨買張, 自營商淨買張], ...}}
summary.json：每檔最近 5／10／20／60 個交易日的外資、投信淨買張數，以及外資＋投信連續買超天數
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAYS = os.path.join(ROOT, 'data', 'insti', 'days')
OUT = os.path.join(ROOT, 'data', 'insti')
UNIV = os.path.join(ROOT, 'insider', 'universe.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TW = datetime.timezone(datetime.timedelta(hours=8))
LOOKBACK_DAYS = 95
WINDOWS = (5, 10, 20, 60)
GAP = 3.2   # 證交所對連續查詢很敏感，放慢

_last = [0.0]
def get_json(url):
    wait = GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                s = r.read().decode('utf-8-sig', 'replace')
            _last[0] = time.time()
            return json.loads(s)
        except Exception as e:
            _last[0] = time.time()
            print('  retry', url[:90], type(e).__name__, e, flush=True)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError('fetch failed')

def n(x):
    try:
        return int(str(x).replace(',', '').strip() or 0)
    except ValueError:
        return 0

def lots(sh):
    return int(round(sh / 1000))

def fetch_twse(d):
    j = get_json('https://www.twse.com.tw/rwd/zh/fund/T86?date=%s&selectType=ALLBUT0999&response=json' % d.strftime('%Y%m%d'))
    if j.get('stat') != 'OK' or not j.get('data'):
        return None
    f = j['fields']
    def idx(key, default):
        for i, name in enumerate(f):
            if name.startswith(key):
                return i
        return default
    i_f, i_fd, i_t, i_d = idx('外陸資買賣超股數', 4), idx('外資自營商買賣超股數', 7), idx('投信買賣超股數', 10), idx('自營商買賣超股數', 11)
    out = {}
    for r in j['data']:
        code = r[0].strip()
        out[code] = [lots(n(r[i_f]) + n(r[i_fd])), lots(n(r[i_t])), lots(n(r[i_d]))]
    return out

def fetch_tpex(d):
    roc = '%d/%02d/%02d' % (d.year - 1911, d.month, d.day)
    j = get_json('https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php?l=zh-tw&se=EW&t=D&d=%s&o=json' % roc)
    t = (j.get('tables') or [{}])[0]
    data = t.get('data') or j.get('aaData') or []
    if not data:
        return None
    if t.get('date') and t['date'] != roc:
        return None   # 回傳的不是要的那天（例如假日回最近一天）
    out = {}
    for r in data:
        code = r[0].strip()
        # 欄位：2-4 外資(不含自營) 5-7 外資自營 8-10 外資合計 11-13 投信 14-16 自營自行 17-19 自營避險 20-22 自營合計 23 合計
        out[code] = [lots(n(r[10])), lots(n(r[13])), lots(n(r[22]))]
    return out

def universe():
    if not os.path.exists(UNIV):
        return None
    return {s['code'] for s in json.load(open(UNIV, encoding='utf-8'))['stocks']}

def cmd_update():
    os.makedirs(DAYS, exist_ok=True)
    nonef = os.path.join(DAYS, '_none.json')
    none = set(json.load(open(nonef))) if os.path.exists(nonef) else set()
    now = datetime.datetime.now(TW)
    today = now.date()
    want = universe()
    t0 = time.time()
    got = 0
    for k in range(LOOKBACK_DAYS):
        d = today - datetime.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        key = d.isoformat()
        if key in none or os.path.exists(os.path.join(DAYS, key + '.json')):
            continue
        if d == today and now.hour < 15:
            continue   # 當天資料下午才公布
        if time.time() - t0 > 1500:
            print('time budget reached', flush=True); break
        try:
            a, b = fetch_twse(d), fetch_tpex(d)
        except Exception as e:
            print('FAIL', key, e, flush=True); continue
        if a is None and b is None:
            if d < today:
                none.add(key)   # 休市
            print(key, 'no data', flush=True); continue
        if a is None or b is None:
            print(key, 'only one market published yet, retry later', flush=True); continue
        rows = {}
        for src in (a, b):
            for code, v in src.items():
                if want is None or code in want:
                    rows[code] = v
        with open(os.path.join(DAYS, key + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'d': key, 'r': rows}, f, separators=(',', ':'))
        got += 1
        print(key, 'stocks', len(rows), flush=True)
    with open(nonef, 'w') as f:
        json.dump(sorted(x for x in none if x >= (today - datetime.timedelta(days=LOOKBACK_DAYS + 10)).isoformat()), f)
    # 清掉太舊的日檔
    cutoff = (today - datetime.timedelta(days=LOOKBACK_DAYS + 10)).isoformat()
    for fn in os.listdir(DAYS):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn) and fn[:10] < cutoff:
            os.remove(os.path.join(DAYS, fn))
    print('fetched days', got, flush=True)
    build()

def build():
    files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))[-max(WINDOWS):]
    days = [json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in files]
    days.reverse()   # 新到舊
    codes = set()
    for d in days:
        codes.update(d['r'])
    rows = {}
    for c in sorted(codes):
        f_sum, t_sum = [], []
        for w in WINDOWS:
            seg = [d['r'].get(c) for d in days[:w]]
            f_sum.append(sum(v[0] for v in seg if v))
            t_sum.append(sum(v[1] for v in seg if v))
        streak = 0
        for d in days:
            v = d['r'].get(c)
            if v and v[0] + v[1] > 0:
                streak += 1
            else:
                break
        rows[c] = f_sum + t_sum + [streak]
    out = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'),
           'latest': days[0]['d'] if days else '', 'from': days[-1]['d'] if days else '', 'days': len(days),
           'windows': list(WINDOWS),
           'cols': ['外資%d日' % w for w in WINDOWS] + ['投信%d日' % w for w in WINDOWS] + ['外資投信連買天數'],
           'rows': rows}
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print('summary', len(rows), 'stocks', len(days), 'days', out['from'], '~', out['latest'], flush=True)

if __name__ == '__main__':
    a = sys.argv[1:] or ['update']
    if a[0] == 'update':
        cmd_update()
    elif a[0] == 'build':
        build()
