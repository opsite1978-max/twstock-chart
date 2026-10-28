#!/usr/bin/env python3
"""
布林大底選股用的長期收盤價（FinMind TaiwanStockPrice，未還原），分批慢慢抓，避免一次用掉太多額度。
每檔只留布林大底需要的東西：週收盤、月收盤、以及接在每日行情檔前面的約 300 個交易日日收盤。
  → data/price/long/<代號>.json  {"t":抓取日, "cut":接到哪一天為止, "d0":日期, "d":[日收盤…], "w0":週一, "w":[週收盤…], "m0":"YYYY-MM", "m":[月收盤…]}
之後每天的新資料由 price_scrape.py 用每日行情檔接上，不必再向 FinMind 要。

  python3 scripts/long_hist.py run [每輪檔數]
在 GitHub Actions 上執行，只用標準函式庫；憑證放在 FINMIND_TOKEN（不會寫進任何檔案）。
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAYS = os.path.join(ROOT, 'data', 'price', 'days')
LONG = os.path.join(ROOT, 'data', 'price', 'long')
UNIV = os.path.join(ROOT, 'insider', 'universe.json')
TW = datetime.timezone(datetime.timedelta(hours=8))
START = '2000-01-01'           # 和戰神看盤預設的資料起點相同
KEEP_DAILY = 300            # 每日行情檔前面再留幾個交易日（布林大底日 K 要 750＋60 根）
API = 'https://api.finmindtrade.com/api/v4/data'

def log(*a):
    print(' '.join(str(x) for x in a), flush=True)

def fm(params, token):
    q = dict(params)
    if token:
        q['token'] = token
    req = urllib.request.Request(API + '?' + urllib.parse.urlencode(q), headers={'User-Agent': 'twstock-chart long_hist'})
    for k in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.loads(r.read().decode('utf-8'))
            if j.get('status') == 402:
                raise RuntimeError('QUOTA')
            if j.get('status') != 200:
                raise RuntimeError(j.get('msg') or 'status %s' % j.get('status'))
            return j.get('data') or []
        except RuntimeError as e:
            if str(e) == 'QUOTA':
                raise
            if k == 2:
                raise
        except Exception:
            if k == 2:
                raise
        time.sleep(5 * (k + 1))

def monday(d):
    x = datetime.date.fromisoformat(d)
    return (x - datetime.timedelta(days=x.weekday())).isoformat()

def compact(rows, cut):
    """rows: FinMind 日資料（已排序）；只取 cut 之前（含）的部分"""
    rows = [r for r in rows if r.get('close') and r['date'] <= cut]
    if not rows:
        return None
    w, m = [], []
    for r in rows:
        wk, mo = monday(r['date']), r['date'][:7]
        if w and w[-1][0] == wk: w[-1][1] = r['close']
        else: w.append([wk, r['close']])
        if m and m[-1][0] == mo: m[-1][1] = r['close']
        else: m.append([mo, r['close']])
    d = rows[-KEEP_DAILY:]
    return {'d0': d[0]['date'], 'dd': [r['date'] for r in d], 'd': [r['close'] for r in d],
            'w0': w[0][0], 'w': [x[1] for x in w], 'wk': [x[0] for x in w],
            'm0': m[0][0], 'm': [x[1] for x in m], 'mk': [x[0] for x in m]}

def run(batch):
    token = os.environ.get('FINMIND_TOKEN', '').strip()
    os.makedirs(LONG, exist_ok=True)
    files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
    first = files[0][:10]                       # 每日行情檔從哪天開始
    cut = (datetime.date.fromisoformat(first) - datetime.timedelta(days=1)).isoformat()
    codes = [s['code'] for s in json.load(open(UNIV, encoding='utf-8'))['stocks']]
    todo = [c for c in codes if not os.path.exists(os.path.join(LONG, c + '.json'))]
    log('long history: done', len(codes) - len(todo), '/', len(codes), 'todo', len(todo), 'cut', cut)
    got = 0
    for c in todo[:batch]:
        try:
            rows = fm({'dataset': 'TaiwanStockPrice', 'data_id': c, 'start_date': START, 'end_date': cut}, token)
        except RuntimeError as e:
            if str(e) == 'QUOTA':
                log('quota reached, stop'); break
            log('FAIL', c, e); continue
        except Exception as e:
            log('FAIL', c, e); continue
        rows.sort(key=lambda r: r['date'])
        out = compact(rows, cut) or {}
        out.update({'t': datetime.datetime.now(TW).date().isoformat(), 'cut': cut})
        with open(os.path.join(LONG, c + '.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, separators=(',', ':'))
        got += 1
        time.sleep(1.2)
    done = sum(1 for c in codes if os.path.exists(os.path.join(LONG, c + '.json')))
    with open(os.path.join(LONG, '_status.json'), 'w', encoding='utf-8') as f:
        json.dump({'done': done, 'total': len(codes), 'cut': cut, 't': datetime.datetime.now(TW).isoformat(timespec='minutes')}, f)
    log('fetched', got, 'now', done, '/', len(codes))

if __name__ == '__main__':
    a = sys.argv[1:] or ['run']
    if a[0] == 'run':
        run(int(a[1]) if len(a) > 1 else 270)
