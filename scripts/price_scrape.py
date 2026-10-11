#!/usr/bin/env python3
"""
全市場每日股價與成交量（證交所 MI_INDEX、櫃買中心每日收盤行情）→ data/price/days/<日期>.json
並計算技術面選股指標與策略回測 → data/price/screen.json
在 GitHub Actions 上執行，只用標準函式庫。

  python3 scripts/price_scrape.py update     補抓缺少的交易日，然後 build
  python3 scripts/price_scrape.py build      只重算指標與回測

每日檔：{"d":日期,"r":{代號:[開, 高, 低, 收, 成交張數]}}
"""
import json, os, re, sys, time, math, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAYS = os.path.join(ROOT, 'data', 'price', 'days')
OUT = os.path.join(ROOT, 'data', 'price')
UNIV = os.path.join(ROOT, 'insider', 'universe.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TW = datetime.timezone(datetime.timedelta(hours=8))
LOOKBACK_CAL_DAYS = 480     # 約 330 個交易日
GAP = 3.2
LOG = []

def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line, flush=True)
    LOG.append(datetime.datetime.now(TW).strftime('%m-%d %H:%M:%S ') + line)

_last = [0.0]
def get_json(url):
    wait = GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                s = r.read().decode('utf-8-sig', 'replace')
            _last[0] = time.time()
            return json.loads(s)
        except Exception as e:
            _last[0] = time.time()
            log('  retry', url[:100], type(e).__name__, str(e)[:80])
            time.sleep(15 * (attempt + 1))
    raise RuntimeError('fetch failed')

def num(x):
    try:
        v = float(str(x).replace(',', '').strip())
        return v if math.isfinite(v) else None
    except ValueError:
        return None

def fetch_twse(d):
    j = get_json('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=%s&type=ALLBUT0999&response=json' % d.strftime('%Y%m%d'))
    if j.get('stat') not in (None, 'OK'):
        return None
    for t in j.get('tables') or []:
        if not t or not t.get('data') or '每日收盤行情' not in (t.get('title') or ''):
            continue
        f = t['fields']
        ic, io, ih, il, icl, iv = (f.index('證券代號'), f.index('開盤價'), f.index('最高價'), f.index('最低價'),
                                   f.index('收盤價'), f.index('成交股數'))
        out = {}
        for r in t['data']:
            c = str(r[ic] or '').strip()
            o, h, l, cl, v = num(r[io]), num(r[ih]), num(r[il]), num(r[icl]), num(r[iv])
            if cl:
                out[c] = [o or cl, h or cl, l or cl, cl, round((v or 0) / 1000)]
        return out
    return None

def fetch_tpex(d):
    roc = '%d/%02d/%02d' % (d.year - 1911, d.month, d.day)
    j = get_json('https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&o=json&d=%s&se=EW' % roc)
    t = (j.get('tables') or [{}])[0]
    data = t.get('data') or j.get('aaData') or []
    if not data or (t.get('date') and t['date'] != roc):
        return None
    out = {}
    for r in data:
        c = str(r[0] or '').strip()
        cl, o, h, l, v = num(r[2]), num(r[4]), num(r[5]), num(r[6]), num(r[7])
        if cl:
            out[c] = [o or cl, h or cl, l or cl, cl, round((v or 0) / 1000)]
    return out

def universe():
    if not os.path.exists(UNIV):
        return None, {}
    u = json.load(open(UNIV, encoding='utf-8'))['stocks']
    return {s['code'] for s in u}, {s['code']: s for s in u}

def cmd_update():
    os.makedirs(DAYS, exist_ok=True)
    nonef = os.path.join(DAYS, '_none.json')
    none = set(json.load(open(nonef))) if os.path.exists(nonef) else set()
    want, _ = universe()
    now = datetime.datetime.now(TW)
    today = now.date()
    t0, got = time.time(), 0
    for k in range(LOOKBACK_CAL_DAYS):
        d = today - datetime.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        key = d.isoformat()
        if key in none or os.path.exists(os.path.join(DAYS, key + '.json')):
            continue
        if d == today and now.hour < 15:
            continue
        if time.time() - t0 > 2400:
            log('time budget reached'); break
        try:
            a = fetch_twse(d)
            if a is None:
                if d < today:
                    none.add(key)
                log(key, 'no data (holiday?)'); continue
            b = fetch_tpex(d)
        except Exception as e:
            log('FAIL', key, e); continue
        if b is None:
            if (today - d).days <= 3:
                log(key, 'OTC not published yet, retry later'); continue
            log(key, 'OTC unavailable, listed only'); b = {}
        rows = {c: v for src in (a, b) for c, v in src.items() if want is None or c in want}
        with open(os.path.join(DAYS, key + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'d': key, 'r': rows}, f, separators=(',', ':'))
        got += 1
        log(key, 'stocks', len(rows))
    with open(nonef, 'w') as f:
        json.dump(sorted(none), f)
    cutoff = (today - datetime.timedelta(days=LOOKBACK_CAL_DAYS + 10)).isoformat()
    for fn in os.listdir(DAYS):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn) and fn[:10] < cutoff:
            os.remove(os.path.join(DAYS, fn))
    log('fetched days', got)
    build()
    with open(os.path.join(OUT, '_lastrun.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG[-300:]) + '\n')

# ───────── 指標 ─────────
def rolling_mean(a, n):
    out = [None] * len(a); acc = 0.0
    for i, v in enumerate(a):
        acc += v
        if i >= n:
            acc -= a[i - n]
        if i >= n - 1:
            out[i] = acc / n
    return out

class Series:
    """一檔股票的對齊序列；停牌日沿用前一天收盤、成交量記 0"""
    def __init__(self, O, H, L, C, V):
        first = next((i for i, c in enumerate(C) if c is not None), len(C))
        self.off = first
        c0 = C[first] if first < len(C) else None
        o, h, l, c, v = [], [], [], [], []
        last = c0
        for i in range(first, len(C)):
            if C[i] is None:
                o.append(last); h.append(last); l.append(last); c.append(last); v.append(0)
            else:
                last = C[i]; o.append(O[i]); h.append(H[i]); l.append(L[i]); c.append(C[i]); v.append(V[i] or 0)
        self.O, self.H, self.L, self.C, self.V = o, h, l, c, v
        self.ma = {k: rolling_mean(c, k) for k in (5, 10, 20, 60, 120)}
        self.vm = {k: rolling_mean([float(x) for x in v], k) for k in (5, 20, 60)}

def indicators(S, gi):
    """第 gi 天（全域索引）的技術指標"""
    i = gi - S.off
    C, H, L, V, ma, vm = S.C, S.H, S.L, S.V, S.ma, S.vm
    if i < 125:
        return None
    m = {k: ma[k][i] for k in ma}
    v5, v20, v60 = vm[5][i], vm[20][i], vm[60][i]
    if not v60:
        return None
    x20 = 0
    for k in range(30):
        j = i - k
        if C[j] <= ma[20][j]:
            break
        x20 += 1
    if x20 == 30:
        x20 = 99
    pre = V[i - 64:i - 4]; base = V[max(0, i - 184):i - 64]
    quiet = (sum(pre) / len(pre)) / (sum(base) / len(base)) if base and sum(base) > 0 else None
    hs, ls = H[i - 64:i - 4], L[i - 64:i - 4]
    rng = (max(hs) - min(ls)) / min(ls) * 100 if min(ls) > 0 else None
    prevmax60 = max(C[i - 60:i])
    low120 = min(L[i - 119:i + 1])
    bull = m[5] > m[10] > m[20] > m[60]
    bull_prev = ma[5][i-1] > ma[10][i-1] > ma[20][i-1] > ma[60][i-1]
    touch = any(L[j] <= ma[20][j] * 1.02 for j in range(i - 2, i + 1))
    return {
        'c': C[i], 'chg': (C[i] / C[i - 1] - 1) * 100 if C[i - 1] else 0, 'v': V[i],
        'ma5': m[5], 'ma20': m[20], 'ma60': m[60], 'ma120': m[120],
        'ma20up': m[20] > ma[20][i - 5], 'ma60up': m[60] > ma[60][i - 5],
        'v5': v5, 'v20': v20, 'v60': v60, 'x20': x20, 'quiet': quiet, 'rng': rng,
        'hi60': C[i] > prevmax60, 'lowdist': (C[i] / low120 - 1) * 100 if low120 > 0 else None,
        'bull': bull, 'bullNew': bull and not bull_prev, 'touch20': touch,
        'ret5': (C[i] / C[i - 5] - 1) * 100, 'ret20': (C[i] / C[i - 20] - 1) * 100,
    }

# 策略預設條件（網頁上可調；回測用這組預設值）
def s_quiet_ma20(x):   # 量縮後帶量站上月線
    return 1 <= x['x20'] <= 3 and x['v5'] >= 2 * x['v60'] and x['quiet'] is not None and x['quiet'] <= 0.8
def s_bull_new(x):     # 均線多頭排列剛成形
    return x['bullNew'] and x['v5'] >= 1.2 * x['v20']
def s_break60(x):      # 帶量突破 60 日新高
    return x['hi60'] and x['v'] >= 2 * x['v20'] and x['chg'] > 0
def s_bottom(x):       # 低檔爆量長紅
    return x['lowdist'] is not None and x['lowdist'] <= 15 and x['chg'] >= 5 and x['v'] >= 3 * x['v20']
def s_pullback(x):     # 多頭回測月線、量縮
    return x['ma20up'] and x['ma60up'] and x['c'] > x['ma60'] and x['c'] > x['ma20'] and x['touch20'] and x['v5'] < x['v20']
def s_first_vol(x):    # 長期整理後首次放量
    return (x['quiet'] is not None and x['quiet'] <= 0.7 and x['rng'] is not None and x['rng'] <= 20
            and x['v'] >= 3 * x['v60'] and x['chg'] > 3)
STRATS = [('quietMa20', s_quiet_ma20), ('bullNew', s_bull_new), ('break60', s_break60),
          ('bottom', s_bottom), ('pullback', s_pullback), ('firstVol', s_first_vol)]
MIN_V20 = 300   # 回測只看 20 日均量 300 張以上，避免冷門股

def build():
    want, info = universe()
    files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
    days = [json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in files]
    n = len(days)
    if n < 130:
        log('not enough days for indicators', n)
    dates = [d['d'] for d in days]
    codes = sorted(set(c for d in days[-5:] for c in d['r']))
    latest = {}
    stats = {k: {h: [] for h in (5, 10, 20)} for k, _ in STRATS}
    bench = {h: [] for h in (5, 10, 20)}
    for c in codes:
        O, H, L, C, V = [], [], [], [], []
        for d in days:
            r = d['r'].get(c)
            if r:
                O.append(r[0]); H.append(r[1]); L.append(r[2]); C.append(r[3]); V.append(r[4])
            else:
                O.append(None); H.append(None); L.append(None); C.append(None); V.append(None)
        if C[-1] is None:
            continue
        S = Series(O, H, L, C, V)
        x = indicators(S, n - 1)
        if x:
            latest[c] = x
        # 回測：每一天用預設條件判斷，記錄之後 5／10／20 日報酬
        for gi in range(S.off + 125, n - 5):
            xi = indicators(S, gi)
            if not xi or xi['v20'] < MIN_V20 or C[gi] is None:
                continue
            i = gi - S.off
            fw = {h: (S.C[i + h] / S.C[i] - 1) * 100 if gi + h < n else None for h in (5, 10, 20)}
            for h in (5, 10, 20):
                if fw[h] is not None and gi % 5 == 0:
                    bench[h].append(fw[h])   # 基準：所有股票、每 5 天取樣一次
            for k, f in STRATS:
                if f(xi):
                    for h in (5, 10, 20):
                        if fw[h] is not None:
                            stats[k][h].append(fw[h])
    def summ(a):
        if not a:
            return None
        s = sorted(a)
        return {'n': len(a), 'win': round(sum(1 for v in a if v > 0) / len(a) * 100, 1),
                'avg': round(sum(a) / len(a), 2), 'med': round(s[len(s) // 2], 2)}
    bt = {k: {str(h): summ(stats[k][h]) for h in (5, 10, 20)} for k, _ in STRATS}
    bt['_bench'] = {str(h): summ(bench[h]) for h in (5, 10, 20)}
    keys = ['c', 'chg', 'v', 'ma5', 'ma20', 'ma60', 'ma120', 'ma20up', 'ma60up', 'v5', 'v20', 'v60', 'x20', 'quiet', 'rng',
            'hi60', 'lowdist', 'bull', 'bullNew', 'touch20', 'ret5', 'ret20']
    def r2(v):
        if isinstance(v, bool):
            return 1 if v else 0
        if v is None:
            return None
        return round(v, 2) if abs(v) < 1000 else round(v)
    rows = []
    for c, x in latest.items():
        u = info.get(c, {})
        rows.append([c, u.get('name', ''), u.get('market', ''), u.get('ind', '')] + [r2(x[k]) for k in keys])
    out = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'latest': dates[-1] if dates else '',
           'days': n, 'from': dates[0] if dates else '', 'btFrom': dates[125] if n > 125 else '', 'btMinV20': MIN_V20,
           'cols': ['code', 'name', 'market', 'ind'] + keys, 'rows': rows, 'backtest': bt}
    with open(os.path.join(OUT, 'screen.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log('build', len(rows), 'stocks', n, 'days', 'backtest', {k: (bt[k]['10'] or {}).get('n') for k, _ in STRATS})

if __name__ == '__main__':
    a = sys.argv[1:] or ['update']
    os.makedirs(OUT, exist_ok=True)
    if a[0] == 'update':
        cmd_update()
    elif a[0] == 'build':
        build()
