#!/usr/bin/env python3
"""
權證分點（FinMind sponsor：taiwan_stock_warrant_trading_daily_report，依券商分點查詢）
→ 原始資料存在另一個分支的 raw/<日期>.json.gz（不放網站）
→ 計算每個分點「買進認購權證後，標的股票 5／10／20 日表現」的勝率，輸出 data/wbranch/summary.json

  python3 scripts/wbranch_scrape.py fetch RAWDIR      補抓缺少的交易日（每次有查詢次數與時間上限）
  python3 scripts/wbranch_scrape.py build RAWDIR      計算勝率與近期動向

FinMind 憑證從環境變數 FINMIND_TOKEN 讀取（GitHub 加密密碼），程式不會印出或存檔。
"""
import json, os, re, sys, gzip, time, datetime, threading, urllib.request, urllib.error, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts'))
OUT = os.path.join(ROOT, 'data', 'wbranch')
PRICE_DAYS = os.path.join(ROOT, 'data', 'price', 'days')
UNIV = os.path.join(ROOT, 'insider', 'universe.json')
TW = datetime.timezone(datetime.timedelta(hours=8))
TOKEN = os.environ.get('FINMIND_TOKEN', '').strip()
API = 'https://api.finmindtrade.com/api/v4/'
BACK_DAYS = int(os.environ.get('BACK_DAYS', '120'))
REQ_BUDGET = int(os.environ.get('REQ_BUDGET', '4800'))     # 每次執行最多查詢次數（每小時上限 6000，留給看盤頁用）
TIME_BUDGET = int(os.environ.get('TIME_BUDGET', '2700'))
THREADS = 3
MM_WARRANTS = 300          # 單日交易超過 300 檔不同權證的分點視為發行商造市／自營，排除
EVENT_MIN = 500000         # 單一分點單日淨買某標的的認購權證 ≥ 50 萬元，才算一次「進場」
HORIZONS = (5, 10, 20)
LOG = []
_lock = threading.Lock()
_nreq = [0]

def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line, flush=True)
    LOG.append(datetime.datetime.now(TW).strftime('%m-%d %H:%M:%S ') + line)

def api(path, params):
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(API + path + '?' + q, headers={'Authorization': 'Bearer ' + TOKEN})
    for attempt in range(4):
        with _lock:
            _nreq[0] += 1
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                j = json.loads(r.read().decode('utf-8'))
            return j.get('data') or []
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:200]
            if e.code in (402, 429) or 'limit' in body.lower():
                log('rate limited, wait', body); time.sleep(120)
            elif e.code == 400 and 'level' in body.lower():
                raise RuntimeError('FinMind 權限不足：' + body)
            else:
                time.sleep(5 * (attempt + 1))
        except Exception as e:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError('request failed ' + path + str(params.get('securities_trader_id', '')))

def trading_days():
    days = sorted(fn[:10] for fn in os.listdir(PRICE_DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
    return days[-BACK_DAYS:]

def brokers(rawdir):
    p = os.path.join(rawdir, 'brokers.json')
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < 7 * 86400:
        return json.load(open(p, encoding='utf-8'))
    rows = api('data', {'dataset': 'TaiwanSecuritiesTraderInfo'})
    b = {r['securities_trader_id']: r['securities_trader'] for r in rows}
    json.dump(b, open(p, 'w', encoding='utf-8'), ensure_ascii=False)
    return b

def fetch_day(day, blist):
    """一天、全部分點 → {分點: {權證: [買進元, 賣出元]}}"""
    out = {}
    def one(bid):
        rows = api('taiwan_stock_warrant_trading_daily_report', {'securities_trader_id': bid, 'date': day})
        agg = {}
        for r in rows:
            w = agg.setdefault(r['stock_id'], [0, 0])
            p = float(r.get('price') or 0)
            w[0] += int(r.get('buy') or 0) * p
            w[1] += int(r.get('sell') or 0) * p
        return bid, {k: [round(v[0]), round(v[1])] for k, v in agg.items() if v[0] or v[1]}
    with ThreadPoolExecutor(THREADS) as ex:
        for bid, agg in ex.map(one, blist):
            if agg:
                out[bid] = agg
    return out

def cmd_fetch(rawdir):
    if not TOKEN:
        log('沒有 FINMIND_TOKEN，略過'); return
    os.makedirs(rawdir, exist_ok=True)
    t0 = time.time()
    bl = brokers(rawdir)
    ids = sorted(bl)
    have = {fn[:10] for fn in os.listdir(rawdir) if fn.endswith('.json.gz')}
    todo = [d for d in reversed(trading_days()) if d not in have]   # 新到舊
    log('brokers', len(ids), 'days to fetch', len(todo))
    for day in todo:
        if _nreq[0] + len(ids) > REQ_BUDGET or time.time() - t0 > TIME_BUDGET:
            log('budget reached, requests', _nreq[0]); break
        t1 = time.time()
        try:
            data = fetch_day(day, ids)
        except RuntimeError as e:
            log('STOP', e); break
        # 排除造市／自營分點，只存一般分點
        mm = [b for b, a in data.items() if len(a) > MM_WARRANTS]
        for b in mm:
            del data[b]
        with gzip.open(os.path.join(rawdir, day + '.json.gz'), 'wt', encoding='utf-8') as f:
            json.dump({'d': day, 'mm': mm, 'b': data}, f, separators=(',', ':'))
        log(day, 'branches', len(data), 'market makers excluded', len(mm), 'in', int(time.time() - t1), 's, requests', _nreq[0])
    log('fetch done, requests', _nreq[0], 'time', int(time.time() - t0), 's')

# ───────── 計算 ─────────
def warrant_map():
    """權證代號 → (標的代號, C/P)。來源：權證每日明細、櫃買對照表、權證基本資料"""
    import warrant_scrape as ws
    m = {}
    wdir = os.path.join(ROOT, 'data', 'warrant')
    for fn in os.listdir(os.path.join(wdir, 'days')):
        if fn.endswith('.json') and fn[0] == '2':
            d = json.load(open(os.path.join(wdir, 'days', fn), encoding='utf-8'))
            for x in d.get('w', []):
                m[x[0]] = (x[1], x[2])
    wmap = json.load(open(os.path.join(wdir, 'wmap.json'))) if os.path.exists(os.path.join(wdir, 'wmap.json')) else {}
    alias = ws.load_alias(); keys = sorted(alias, key=len, reverse=True)
    try:
        meta = ws.fetch_meta()
    except Exception as e:
        log('meta failed', e); meta = {}
    for c, v in meta.items():
        if c in m:
            continue
        u = wmap.get(c) or ws.match_name(v[0], alias, keys)
        if u:
            m[c] = (u, v[1])
    for c, u in wmap.items():
        if c not in m:
            m[c] = (u, 'P' if c.endswith('P') else 'C')
    log('warrant map', len(m))
    return m

def prices():
    files = sorted(fn for fn in os.listdir(PRICE_DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
    dates = [f[:10] for f in files]
    close = {}
    for i, fn in enumerate(files):
        for c, v in json.load(open(os.path.join(PRICE_DAYS, fn), encoding='utf-8'))['r'].items():
            close.setdefault(c, {})[dates[i]] = v[3]
    return dates, close

def cmd_build(rawdir):
    os.makedirs(OUT, exist_ok=True)
    files = sorted(fn for fn in os.listdir(rawdir) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json\.gz', fn)) if os.path.isdir(rawdir) else []
    if not files:
        log('no raw data yet'); return
    bnames = json.load(open(os.path.join(rawdir, 'brokers.json'), encoding='utf-8')) if os.path.exists(os.path.join(rawdir, 'brokers.json')) else {}
    wm = warrant_map()
    dates, close = prices()
    didx = {d: i for i, d in enumerate(dates)}
    univ = {s['code']: s for s in json.load(open(UNIV, encoding='utf-8'))['stocks']} if os.path.exists(UNIV) else {}
    events = []     # (日期, 分點, 標的, 認購淨買元, 認售淨買元)
    unmapped = 0
    for fn in files:
        d = json.load(gzip.open(os.path.join(rawdir, fn), 'rt', encoding='utf-8'))
        day = d['d']
        for b, ws_ in d['b'].items():
            per = {}
            for w, (bv, sv) in ws_.items():
                u = wm.get(w)
                if not u:
                    unmapped += 1; continue
                o = per.setdefault(u[0], [0, 0])
                o[0 if u[1] == 'C' else 1] += bv - sv
            for u, (cn, pn) in per.items():
                if cn >= EVENT_MIN or cn <= -EVENT_MIN or pn >= EVENT_MIN:
                    events.append((day, b, u, cn, pn))
    log('raw days', len(files), 'events', len(events), 'unmapped warrant rows', unmapped)
    # 勝率：認購淨買 ≥ 門檻 → 看標的之後 N 日報酬
    stats = {}
    for day, b, u, cn, pn in events:
        if cn < EVENT_MIN or day not in didx or u not in close:
            continue
        i = didx[day]; c0 = close[u].get(day)
        if not c0:
            continue
        s = stats.setdefault(b, {h: [] for h in HORIZONS})
        for h in HORIZONS:
            if i + h < len(dates):
                c1 = close[u].get(dates[i + h])
                if c1:
                    s[h].append((c1 / c0 - 1) * 100)
    br = {}
    for b, s in stats.items():
        row = [bnames.get(b, b)]
        for h in HORIZONS:
            a = s[h]
            row += [len(a), round(sum(1 for x in a if x > 0) / len(a) * 100, 1) if a else None, round(sum(a) / len(a), 2) if a else None]
        br[b] = row
    # 近期動向：最近 10 個交易日的進出（認購淨買賣、認售淨買）
    rdays = sorted({e[0] for e in events})[-10:]
    recent = [[e[0], e[1], e[2], round(e[3] / 1e4), round(e[4] / 1e4)] for e in events if e[0] in rdays]
    out = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'from': files[0][:10], 'to': files[-1][:10],
           'days': len(files), 'eventMin': EVENT_MIN, 'horizons': list(HORIZONS),
           'bcols': ['分點名稱'] + [x % h for h in HORIZONS for x in ('%d日次數', '%d日勝率', '%d日平均報酬')],
           'branches': br, 'recentDays': rdays,
           'rcols': ['日期', '分點代號', '標的', '認購淨買萬元', '認售淨買萬元'], 'recent': recent,
           'names': {c: [univ[c]['name'], univ[c].get('ind', '')] for c in {e[2] for e in events if e[0] in rdays} if c in univ}}
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log('build branches', len(br), 'recent events', len(recent))

if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    a = sys.argv[1:]
    raw = a[1] if len(a) > 1 else 'raw'
    if a[0] == 'fetch':
        cmd_fetch(raw)
    elif a[0] == 'build':
        cmd_build(raw)
    with open(os.path.join(OUT, '_lastrun_%s.txt' % a[0]), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG[-300:]) + '\n')
