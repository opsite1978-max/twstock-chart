#!/usr/bin/env python3
"""
全市場每日股價與成交量（證交所 MI_INDEX、櫃買中心每日收盤行情）→ data/price/days/<日期>.json
並計算技術面選股指標與策略回測 → data/price/screen.json
另算戰神看盤主圖 VWB PRO 通道（全市場） → data/price/vwb.json（戰神選股用）
另算布林大底（日／週／月，接 data/price/long 的長期收盤） → data/price/bbot.json（布林大底選股用）
另算尋龍底龍穴能量 → data/price/lx.json（尋龍底選股用）
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
LOOKBACK_CAL_DAYS = 900     # 約 610 個交易日（VWB PRO 的 ATR 要 500 根）
SCREEN_DAYS = 335           # 技術選股與回測只用最近這麼多天（和原本一樣）
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
        if time.time() - t0 > 1500:   # 一次最多抓 25 分鐘（存檔小一點、比較不會推送失敗），剩下的下一輪再補
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
    old = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn) and fn[:10] < cutoff)
    roll_long(old)          # 要刪的日子先併進長期收盤檔，才不會斷掉
    for fn in old:
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
    days_all = [json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in files]
    build_vwb(days_all, info)
    build_bbot(days_all, info)
    build_jgn(days_all, info)
    try: build_lx(days_all, info)
    except Exception as ex: log('lx failed', ex)
    days = days_all[-SCREEN_DAYS:]
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

# ───────── 戰神看盤 VWB PRO 通道（和網頁 calcVWB 同一套算法，預設參數） ─────────
VWB_LEN, VWB_SMOOTH, VWB_ATR = 50, 50, 500
VWB_MULTS = (1.0, 3.0, 6.0, 9.0)

def _sma(a, n):
    out = [None] * len(a); s = 0.0; bad = 0
    for i, x in enumerate(a):
        if x is None: bad += 1
        else: s += x
        if i >= n:
            y = a[i - n]
            if y is None: bad -= 1
            else: s -= y
        if i >= n - 1 and bad == 0:
            out[i] = s / n
    return out

def _ema(a, n):
    """Pine 的 ta.ema：先用 SMA 起頭"""
    al = 2 / (n + 1); sm = _sma(a, n); out = [None] * len(a); prev = None
    for i, x in enumerate(a):
        v = sm[i] if prev is None else (None if x is None else al * x + (1 - al) * prev)
        out[i] = v; prev = v
    return out

def vwb_calc(H, L, C, V):
    n = len(C)
    pv = [C[i] * V[i] for i in range(n)]
    avgV, avgPV, emaB = _sma([float(v) for v in V], VWB_LEN), _sma(pv, VWB_LEN), _ema(C, VWB_LEN)
    raw = [None] * n
    for i in range(n):
        if avgV[i] is not None and avgV[i] > 0 and emaB[i] is not None:
            raw[i] = (avgPV[i] / avgV[i] + emaB[i]) / 2
        else:
            raw[i] = emaB[i]
    tE = _ema(raw, VWB_SMOOTH)
    trend = [None if raw[i] is None else tE[i] for i in range(n)]
    tr = [H[0] - L[0]] + [max(H[i] - L[i], abs(H[i] - C[i - 1]), abs(L[i] - C[i - 1])) for i in range(1, n)]
    atr = _sma(tr, VWB_ATR)
    return trend, atr

def build_vwb(days, info):
    dates = [d['d'] for d in days]; nd = len(days)
    codes = sorted(set(c for d in days[-3:] for c in d['r']))
    rows = []; short = 0
    for c in codes:
        H, L, C, V, D = [], [], [], [], []
        for d in days:          # 停牌日不補（和 FinMind 日 K 一樣沒有那一天）
            r = d['r'].get(c)
            if r and r[3]:
                H.append(r[1]); L.append(r[2]); C.append(r[3]); V.append(r[4] or 0); D.append(d['d'])
        if not C or D[-1] != dates[-1]:
            continue
        u = info.get(c, {})
        base = [c, u.get('name', ''), u.get('market', ''), u.get('ind', ''), C[-1], round((C[-1] / C[-2] - 1) * 100, 2) if len(C) > 1 and C[-2] else 0,
                round(sum(V[-20:]) / min(20, len(V)))]
        trend, atr = vwb_calc(H, L, C, V)
        i = len(C) - 1
        # 一天漲跌超過漲跌停（±10%）＝分割、減資、面額變更等：未還原價格會讓通道失真
        # 記成「日期:倍數」（取變動最大的一次），網頁用倍數判斷嚴重程度
        split, worst = '', 0.0
        for j in range(max(1, i - VWB_ATR - 120), i + 1):
            if C[j - 1]:
                r = C[j] / C[j - 1]
                if not (0.88 <= r <= 1.12) and abs(math.log(r)) > worst:
                    worst = abs(math.log(r)); split = D[j] + ':' + str(round(r, 3))
        if trend[i] is None or atr[i] is None or i < 1 or atr[i - 1] is None:
            short += 1
            rows.append(base + [None] * 13 + [split]); continue
        band = lambda k, j: trend[j] - atr[j] * k
        l1, l3, l6, l9 = (band(m, i) for m in VWB_MULTS)
        u1, u9 = trend[i] + atr[i] * VWB_MULTS[0], trend[i] + atr[i] * VWB_MULTS[3]
        # 多空狀態：收盤站上內通道上緣＝多、跌破下緣＝空（和主圖 K 棒顏色一樣）
        st = 0
        for j in range(len(C)):
            if trend[j] is None or atr[j] is None: continue
            if C[j] > trend[j] + atr[j]: st = 1
            elif C[j] < trend[j] - atr[j]: st = -1
        def below_days(k):
            n_ = 0
            for j in range(i, -1, -1):
                if trend[j] is None or atr[j] is None or C[j] >= band(k, j): break
                n_ += 1
            return n_
        bd = [below_days(m) for m in VWB_MULTS]   # 連續幾天收在 −1／−3／−6／−9 倍下方
        r2 = lambda v: round(v, 2) if abs(v) < 1000 else round(v, 1)
        rows.append(base + [r2(trend[i]), r2(l1), r2(l3), r2(l6), r2(l9), r2(u1), r2(u9), st] + bd + [round(atr[i] / C[i] * 100, 2), split])
    out = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'latest': dates[-1] if dates else '', 'days': nd,
           'from': dates[0] if dates else '', 'params': {'length': VWB_LEN, 'smooth': VWB_SMOOTH, 'atr': VWB_ATR, 'mults': VWB_MULTS},
           'cols': ['code', 'name', 'market', 'ind', 'c', 'chg', 'v20', 'trend', 'l1', 'l3', 'l6', 'l9', 'u1', 'u9', 'state', 'b1', 'b3', 'b6', 'b9', 'atrPct', 'split'],
           'rows': rows}
    with open(os.path.join(OUT, 'vwb.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log('vwb', len(rows), 'stocks', short, 'need more history', nd, 'days')

# ───────── 布林大底（和網頁 calcBBOT 同一套：深度 × 帶寬百分位） ─────────
LONG = os.path.join(ROOT, 'data', 'price', 'long')
KEEP_DAILY = 300

def _monday(d):
    x = datetime.date.fromisoformat(d)
    return (x - datetime.timedelta(days=x.weekday())).isoformat()

def roll_long(old_files):
    """把即將刪掉的每日行情併進 data/price/long/<代號>.json（日收盤只留最後 KEEP_DAILY 個）"""
    if not old_files or not os.path.isdir(LONG):
        return
    days = [json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in old_files]
    for fn in os.listdir(LONG):
        if not fn.endswith('.json') or fn.startswith('_'):
            continue
        p = os.path.join(LONG, fn); code = fn[:-5]
        L = json.load(open(p, encoding='utf-8'))
        changed = False
        for d in days:
            r = d['r'].get(code); dd = d['d']
            if not r or not r[3] or dd <= L.get('cut', ''):
                continue
            if 'd' not in L:
                L.update({'d0': dd, 'dd': [], 'd': [], 'w0': _monday(dd), 'w': [], 'wk': [], 'm0': dd[:7], 'm': [], 'mk': []})
            L['dd'].append(dd); L['d'].append(r[3])
            wk, mo = _monday(dd), dd[:7]
            o, h, l, v = r[0] or r[3], r[1] or r[3], r[2] or r[3], r[4] or 0
            for K, C_, O_, H_, L_, V_, key in (('wk', 'w', 'wo', 'wh', 'wl', 'wv', wk), ('mk', 'm', 'mo', 'mh', 'ml', 'mv', mo)):
                ohlc = H_ in L and len(L[H_]) == len(L[K])
                vol = V_ in L and len(L[V_]) == len(L[K])
                if L[K] and L[K][-1] == key:
                    L[C_][-1] = r[3]
                    if ohlc: L[H_][-1] = max(L[H_][-1], h); L[L_][-1] = min(L[L_][-1], l)
                    if vol: L[V_][-1] += v
                else:
                    L[K].append(key); L[C_].append(r[3])
                    if ohlc: L[O_].append(o); L[H_].append(h); L[L_].append(l)
                    if vol: L[V_].append(v)
            L['cut'] = dd; changed = True
        if changed:
            L['dd'], L['d'] = L['dd'][-KEEP_DAILY:], L['d'][-KEEP_DAILY:]
            L['d0'] = L['dd'][0]
            with open(p, 'w', encoding='utf-8') as f:
                json.dump(L, f, separators=(',', ':'))

def bbot_series(C, last_n=8):
    """回傳最後 last_n 根的布林大底值（不夠資料的是 None）"""
    n = len(C); bw = [None] * n
    for i in range(19, n):
        w = C[i - 19:i + 1]; m = sum(w) / 20
        if m: bw[i] = 4 * math.sqrt(sum((x - m) ** 2 for x in w) / 20) / m
    out = []
    for i in range(max(59, n - last_n), n):
        w = C[i - 59:i + 1]; m = sum(w) / 60; sd = math.sqrt(sum((x - m) ** 2 for x in w) / 60)
        depth = max(0.0, min(3.0, (m - C[i]) / sd)) / 3 if sd > 0 else 0.0
        win = [x for x in bw[max(19, i - 749):i + 1] if x is not None]
        if len(win) < 100 or bw[i] is None:
            out.append(None); continue
        below = sum(1 for x in win if x < bw[i])
        out.append(depth * below / len(win) * 100)
    return [None] * (last_n - len(out)) + out

def build_bbot(days, info):
    dates = [d['d'] for d in days]
    if not dates:
        return
    codes = sorted(set(c for d in days[-3:] for c in d['r']))
    st = {}
    try: st = json.load(open(os.path.join(LONG, '_status.json'), encoding='utf-8'))
    except Exception: pass
    rows = []; full = 0
    def summ(v):
        cur = v[-1]
        if cur is None:
            return [None, None, None, 0]
        prev = v[-2]
        mx5 = max([x for x in v[-5:] if x is not None] or [None]) if any(x is not None for x in v[-5:]) else None
        run = 0
        for x in reversed(v):
            if x is not None and x >= 50: run += 1
            else: break
        r1 = lambda x: None if x is None else round(x, 1)
        return [r1(cur), r1(prev), r1(mx5), run]
    for c in codes:
        C, D, V = [], [], []
        for d in days:
            r = d['r'].get(c)
            if r and r[3]:
                C.append(r[3]); D.append(d['d']); V.append(r[4] or 0)
        if not C or D[-1] != dates[-1]:
            continue
        L = None
        p = os.path.join(LONG, c + '.json')
        if os.path.exists(p):
            try: L = json.load(open(p, encoding='utf-8'))
            except Exception: L = None
        has = bool(L and L.get('d'))
        dC = (L['d'] if has else []) + C
        wk, wc = (list(L['wk']), list(L['w'])) if has else ([], [])
        mk, mc = (list(L['mk']), list(L['m'])) if has else ([], [])
        for dd, x in zip(D, C):
            k = _monday(dd)
            if wk and wk[-1] == k: wc[-1] = x
            else: wk.append(k); wc.append(x)
            k = dd[:7]
            if mk and mk[-1] == k: mc[-1] = x
            else: mk.append(k); mc.append(x)
        u = info.get(c, {})
        row = [c, u.get('name', ''), u.get('market', ''), u.get('ind', ''), C[-1],
               round((C[-1] / C[-2] - 1) * 100, 2) if len(C) > 1 and C[-2] else 0, round(sum(V[-20:]) / min(20, len(V))), 1 if L is not None else 0]
        for ser in (dC, wc, mc):
            row += summ(bbot_series(ser)) if len(ser) >= 60 else [None, None, None, 0]
        rows.append(row); full += 1 if L is not None else 0
    out = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'latest': dates[-1], 'long': st,
           'cols': ['code', 'name', 'market', 'ind', 'c', 'chg', 'v20', 'long',
                    'd', 'dPrev', 'dMax5', 'dRun', 'w', 'wPrev', 'wMax5', 'wRun', 'm', 'mPrev', 'mMax5', 'mRun'],
           'rows': rows}
    with open(os.path.join(OUT, 'bbot.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log('bbot', len(rows), 'stocks', full, 'with long history')

# ───────── ★機構牛（和網頁 calcJGN 同一套預設） ─────────
JGN_BRK, JGN_VOL, JGN_UP, JGN_NEAR, JGN_LOOK = 60, 2.0, 4.0, 1.5, 20

def build_jgn(days, info):
    dates = [d['d'] for d in days]
    if not dates:
        return
    codes = sorted(set(c for d in days[-3:] for c in d['r']))
    rows = []
    for c in codes:
        O, H, L, C, V, D = [], [], [], [], [], []
        for d in days:
            r = d['r'].get(c)
            if r and r[3]:
                O.append(r[0]); H.append(r[1]); L.append(r[2]); C.append(r[3]); V.append(r[4] or 0); D.append(d['d'])
        n = len(C)
        if n < JGN_BRK + 30 or D[-1] != dates[-1]:
            continue
        mid = [(3 * C[i] + L[i] + O[i] + H[i]) / 6 for i in range(n)]
        NL = [None] * n
        for i in range(20, n):
            NL[i] = (mid[i - 20] + sum((20 - k) * mid[i - k] for k in range(19))) / 210
        ML = [None] * n
        for i in range(25, n):
            ML[i] = sum(NL[i - 5:i + 1]) / 6
        vm = rolling_mean([float(x) for x in V], 20); m20 = rolling_mean(C, 20); m250 = rolling_mean(C, 250)
        sig = None; last = -99
        for i in range(max(JGN_BRK + 1, n - JGN_LOOK - 6), n):
            if not C[i - 1] or C[i] / C[i - 1] > 1.12:     # 超過漲停＝分割合併等，不算
                continue
            star = (C[i] > max(H[i - JGN_BRK:i]) and vm[i - 1] and V[i] >= JGN_VOL * vm[i - 1]
                    and (C[i] / C[i - 1] - 1) * 100 > JGN_UP and (H[i] / C[i] - 1) * 100 <= JGN_NEAR and i - last > 5)
            if star:
                last = i
                if i >= n - JGN_LOOK:
                    hot = (m250[i] is not None and C[i] > m250[i] * 1.6) or C[i] > m20[i] * 1.25
                    sig = (i, hot, V[i] / vm[i - 1], max(H[i - JGN_BRK:i]))
        if not sig:
            continue
        i, hot, vr, lvl = sig
        ex = ''
        for j in range(i + 1, n):
            if NL[j - 1] is not None and ML[j - 1] is not None and NL[j - 1] >= ML[j - 1] and NL[j] < ML[j]:
                ex = D[j]; break
        u = info.get(c, {})
        rows.append([c, u.get('name', ''), u.get('market', ''), u.get('ind', ''), C[-1],
                     round((C[-1] / C[-2] - 1) * 100, 2) if C[-2] else 0, round(sum(V[-20:]) / 20),
                     D[i], n - 1 - i, C[i], round((C[-1] / C[i] - 1) * 100, 2), round(vr, 1), lvl,
                     round((C[i] / C[i - 1] - 1) * 100, 2), 1 if hot else 0, ex, round((C[-1] / lvl - 1) * 100, 2)])
    out = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'latest': dates[-1],
           'params': {'brk': JGN_BRK, 'vol': JGN_VOL, 'up': JGN_UP, 'near': JGN_NEAR, 'look': JGN_LOOK},
           'cols': ['code', 'name', 'market', 'ind', 'c', 'chg', 'v20', 'sigDate', 'ago', 'sigC', 'since', 'volX', 'lvl', 'sigUp', 'hot', 'exit', 'vsLvl'],
           'rows': rows}
    with open(os.path.join(OUT, 'jgn.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log('jgn', len(rows), 'stocks with signal in last', JGN_LOOK, 'days')

# ───────── 尋龍底（和網頁 calcLX 同一套算法） ─────────

def _roll_ext(a, n, big):
    """TDX HHV/LLV：視窗不滿也照算（和網頁 hhv/llv 一樣），big=True 取最大"""
    from collections import deque
    q = deque(); out = [0.0] * len(a)
    for i, x in enumerate(a):
        while q and ((a[q[-1]] <= x) if big else (a[q[-1]] >= x)):
            q.pop()
        q.append(i)
        if q[0] <= i - n:
            q.popleft()
        out[i] = a[q[0]]
    return out

def _sma_strict(a, n):
    out = [None] * len(a); s = 0.0; bad = 0
    for i, x in enumerate(a):
        if x is None: bad += 1
        else: s += x
        if i >= n:
            y = a[i - n]
            if y is None: bad -= 1
            else: s -= y
        if i >= n - 1 and bad == 0:
            out[i] = s / n
    return out

def _tdx_sma(a, N, M):
    out = [None] * len(a); y = None
    for i, x in enumerate(a):
        if x is None: out[i] = y; continue
        y = x if y is None else (M * x + (N - M) * y) / N; out[i] = y
    return out

def lx_calc(O, H, L, C, buy_min=0.25):
    n = len(C)
    hv = lambda a, N: _roll_ext(a, N, True)
    lv = lambda a, N: _roll_ext(a, N, False)
    V1, V2, V3 = _sma_strict(hv(H, 485), 17), _sma_strict(hv(H, 222), 17), _sma_strict(hv(H, 96), 17)
    V4, V5, V6 = _sma_strict(lv(L, 485), 17), _sma_strict(lv(L, 222), 17), _sma_strict(lv(L, 96), 17)
    def comb(w):
        o = [None] * n
        for i in range(n):
            v = (V4[i], V5[i], V6[i], V1[i], V2[i], V3[i])
            if None not in v: o[i] = sum(a * b for a, b in zip(v, w)) / 6
        return o
    V7 = _sma_strict(comb((.96, .96, .96, .558, .558, .558)), 17)
    V8 = _sma_strict(comb((1.25, 1.23, 1.2, .55, .55, .65)), 17)
    V9 = _sma_strict(comb((1.3, 1.3, 1.3, .68, .68, .68)), 17)
    ta = [None if None in (V7[i], V8[i], V9[i]) else (V7[i] * 3 + V8[i] * 2 + V9[i]) / 6 * 1.738 for i in range(n)]
    VB = _sma_strict(ta, 17)
    dA = [None] + [abs(L[i] - L[i - 1]) for i in range(1, n)]
    dU = [None] + [max(L[i] - L[i - 1], 0.0) for i in range(1, n)]
    sa, su = _tdx_sma(dA, 3, 1), _tdx_sma(dU, 3, 1)
    vf0 = []
    for i in range(n):
        ve = sa[i] / su[i] * 100 if su[i] is not None and su[i] > 0 else 0.0
        vf0.append(ve * 10 if VB[i] is not None and C[i] * 1.35 <= VB[i] else ve / 10)
    VF = _sma_strict(vf0, 3)
    V10 = lv(L, 30)
    VFz = [(-1e300 if x is None else x) for x in VF]
    V11 = hv(VFz, 30)
    sraw = []
    for i in range(n):
        if L[i] <= V10[i]:
            sraw.append(None if VF[i] is None or V11[i] <= -1e299 else (VF[i] + V11[i] * 2) / 2)
        else:
            sraw.append(0.0)
    SS = [0.0 if (x is None or x < 1e-6) else x for x in _sma_strict(sraw, 3)]
    m5, m10 = rolling_mean(C, 5), rolling_mean(C, 10)
    sigs = []; lb = lend = lch = ldd = -99
    H20 = hv(H, 20)
    for i in range(1, n):
        if SS[i] == 0 and SS[i - 1] > 0: lend = i
        j = i - 1
        if m10[j] is not None and C[j] and m5[j] and (m5[j] - C[j]) / C[j] > 0.04 and (m10[j] - m5[j]) / m5[j] > 0.04: lch = i
        if C[i] < 0.85 * H20[i]: ldd = i
        if i - lend < 3 and C[i] > H[i - 1] and i - lb > 10:
            lb = i
            dtc = (C[i] - O[i]) / (2 * (H[i] - L[i]) + abs(C[i] - O[i]) + 0.0003)
            ch = i - lch < 10
            sigs.append((i, 2 if ch and dtc > buy_min else 1 if (ch or i - ldd < 10) else 0))
    return SS, sigs

LX_LOOKS = {'d': 20, 'w': 10, 'm': 6}     # 日 K 看最近 20 根、週 K 10 根、月 K 6 根內的龍穴

def lx_summary(D, O, H, L, C, look):
    n = len(C)
    if n < 60:
        return None
    SS, sigs = lx_calc(O, H, L, C)
    e = n
    while e > 0 and SS[e - 1] == 0 and n - e <= look: e -= 1
    if e == 0 or SS[e - 1] == 0:
        return None
    b = e - 1
    while b > 0 and SS[b - 1] > 0: b -= 1
    pk = max(SS[b:e]); pki = b + SS[b:e].index(pk)
    sg = [x for x in sigs if x[0] >= b]
    si, st = (sg[-1] if sg else (None, None))
    return [round(pk), round(SS[-1]), D[pki], -1 if e == n else n - 1 - e, e - b,
            st, (n - 1 - si) if si is not None else None,
            round((C[-1] / C[si] - 1) * 100, 2) if si is not None else None]

def _agg(D, O, H, L, C, keyf, LP, pre):
    """每日 → 週／月 K；前面接 data/price/long 的長期週／月資料（有高低點就用，沒有就用收盤代替）"""
    K, o, h, l, c = [], [], [], [], []
    if LP and LP.get(pre):
        kk, cc = LP[pre + 'k'], LP[pre]
        ok = (pre + 'h') in LP and len(LP[pre + 'h']) == len(cc)
        K, c = list(kk), list(cc)
        o, h, l = (list(LP[pre + 'o']), list(LP[pre + 'h']), list(LP[pre + 'l'])) if ok else (list(cc), list(cc), list(cc))
    for i in range(len(C)):
        k = keyf(D[i])
        if K and K[-1] == k:
            h[-1] = max(h[-1], H[i]); l[-1] = min(l[-1], L[i]); c[-1] = C[i]
        elif K and k < K[-1]:
            continue
        else:
            K.append(k); o.append(O[i]); h.append(H[i]); l.append(L[i]); c.append(C[i])
    return K, o, h, l, c

def build_lx(days, info):
    dates = [d['d'] for d in days]
    if not dates:
        return
    codes = sorted(set(c for d in days[-3:] for c in d['r']))
    rows = []; nl = [0, 0, 0]
    for c in codes:
        O, H, L, C, V, D = [], [], [], [], [], []
        for d in days:
            r = d['r'].get(c)
            if r and r[3] and r[1] and r[2]:
                O.append(r[0] or r[3]); H.append(r[1]); L.append(r[2]); C.append(r[3]); V.append(r[4] or 0); D.append(d['d'])
        n = len(C)
        if n < 60 or D[-1] != dates[-1]:
            continue
        LP = None
        p = os.path.join(LONG, c + '.json')
        if os.path.exists(p):
            try: LP = json.load(open(p, encoding='utf-8'))
            except Exception: LP = None
        lf = 0 if not (LP and LP.get('w')) else 2 if 'wh' in LP else 1
        res = {'d': lx_summary(D, O, H, L, C, LX_LOOKS['d'])}
        for t, keyf in (('w', _monday), ('m', lambda x: x[:7])):
            K, o, h, l, cc = _agg(D, O, H, L, C, keyf, LP, t)
            res[t] = lx_summary(K, o, h, l, cc, LX_LOOKS[t])
        if not any(res.values()):
            continue
        nl[lf] += 1
        v20 = sum(V[-20:]) / 20
        u = info.get(c, {})
        rows.append([c, u.get('name', ''), u.get('market', ''), u.get('ind', ''), C[-1],
                     round((C[-1] / C[-2] - 1) * 100, 2) if C[-2] else 0, round(v20), round(V[-1] / v20, 2) if v20 else 0,
                     round((C[-1] / max(H[-60:]) - 1) * 100, 1), lf, res['d'], res['w'], res['m']])
    st = {}
    try: st = json.load(open(os.path.join(LONG, '_status.json'), encoding='utf-8'))
    except Exception: pass
    out = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'latest': dates[-1], 'looks': LX_LOOKS, 'long': st,
           'cols': ['code', 'name', 'market', 'ind', 'c', 'chg', 'v20', 'vr', 'dd60', 'lf', 'd', 'w', 'm'],
           'tcols': ['pk', 'ss', 'pkDate', 'ended', 'len', 'sig', 'sigAgo', 'since'],
           'rows': rows}
    with open(os.path.join(OUT, 'lx.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log('lx', len(rows), 'stocks; long none/close/ohlc', nl)

if __name__ == '__main__':
    a = sys.argv[1:] or ['update']
    os.makedirs(OUT, exist_ok=True)
    if a[0] == 'update':
        cmd_update()
    elif a[0] == 'build':
        build()
    elif a[0] == 'bbot':
        want, info = universe()
        files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
        build_bbot([json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in files], info)
    elif a[0] == 'jgn':
        want, info = universe()
        files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
        build_jgn([json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in files], info)
    elif a[0] == 'lx':
        want, info = universe()
        files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
        build_lx([json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in files], info)
    elif a[0] == 'vwb':
        want, info = universe()
        files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
        build_vwb([json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in files], info)
