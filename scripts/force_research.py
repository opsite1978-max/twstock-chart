#!/usr/bin/env python3
"""
台指選擇權多空力道：研究與嚴格驗證（只讀網站上已存的資料，完全不連 FinMind）

讀 data/force/m5/*.json（每 5 分鐘：台指期開高低收、選擇權主動成交 Delta 淨流量…），
在 5 分、15 分、60 分 K 棒上計算：
  資金流引擎   F＝淨流量力道（和網頁同一套算法）、I＝多空失衡（淨流量 ÷ 總流量）、R＝價格調整後的資金流殘差
  市場狀態引擎 多頭趨勢、空頭趨勢、區間震盪、恐慌擴張、趨勢轉換（只用當下已知資訊）
  衰竭引擎     七個分項各自 0～100，再平均成衰竭分數
  訊號引擎     A 原始 GO、B 極端直接反向、C 極端退潮後反向、D 衰竭＋價格確認、E D 只在非趨勢行情、
               F 順勢延續、G 恐慌修復、H 60 分極端＋15 分確認
  回測引擎     下一根開盤進場；交易時間持有／實際經過時間／同時段出場；失效價停損（含跳空）；
               成本 2/4/6/10 點；同方向持倉中不重複進場（去叢集）；按交易日區塊重抽的 95% 信賴區間；
               同時段、同市場狀態的配對基準；每月穩定度；日盤／夜盤；逐月滾動（前 3 個月選參數、下 1 個月驗證）

  python3 scripts/force_research.py          產生 data/force/research.json
  python3 scripts/force_research.py test     因果性測試：只用前半段資料算出的數值，必須和用全部資料算出的一樣
"""
import json, os, sys, math, bisect, random, datetime, statistics as stt
from collections import deque, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'force')
OUT = os.path.join(DATA, 'research.json')
FREEZE = '2026-10-18'          # 規則凍結日（台灣時間 10/18 00:00 之後進場的交易才算「真正沒看過的新資料」）
FREEZE_T = int(datetime.datetime(2026, 10, 18, tzinfo=datetime.timezone.utc).timestamp())
COSTS = [2, 4, 6, 10]
ATANH6 = math.atanh(0.6)
TW = datetime.timezone(datetime.timedelta(hours=8))


# ───────── 讀資料、整理成交易時段 ─────────
def load_sessions():
    idx = json.load(open(os.path.join(DATA, 'index.json'), encoding='utf-8'))
    sess = []
    for mo in idx.get('months', []):
        j = json.load(open(os.path.join(DATA, 'm5', mo + '.json'), encoding='utf-8'))
        cols = j['cols']
        for s in j['sessions']:
            bars = []
            for r in s['bars']:
                d = dict(zip(cols, r))
                d.setdefault('v', 0)
                bars.append(d)
            if bars:
                sess.append({'td': s['td'], 's': s['s'], 'bars': bars})
    sess.sort(key=lambda q: q['bars'][0]['t'])
    return sess, idx


def base_series(sess):
    out = []
    for si, s in enumerate(sess):
        s0 = s['bars'][0]['t']
        for b in s['bars']:
            x = dict(b)
            x.update(td=s['td'], s=s['s'], si=si, k=(b['t'] - s0) // 300, te=b['t'] + 300)
            out.append(x)
    for i, b in enumerate(out):
        b['bi'] = i
    return out


def aggregate(base, tf):
    if tf == 5:
        return [dict(b, b0=b['bi'], b1=b['bi']) for b in base]
    out, cur = [], None
    for b in base:
        s0 = base[b['bi'] - b['k']]['t']
        k = (b['t'] - s0) // (tf * 60)
        key = (b['si'], k)
        if cur is None or cur['key'] != key:
            cur = {'key': key, 't': s0 + k * tf * 60, 'o': b['o'], 'h': b['h'], 'l': b['l'], 'c': b['c'],
                   'net': 0.0, 'gross': 0.0, 'cb': 0.0, 'cs': 0.0, 'pb': 0.0, 'ps': 0.0, 'unk': 0.0, 'vix': None, 'v': 0,
                   'td': b['td'], 's': b['s'], 'si': b['si'], 'k': k, 'b0': b['bi']}
            out.append(cur)
        cur['h'] = max(cur['h'], b['h']); cur['l'] = min(cur['l'], b['l']); cur['c'] = b['c']
        for f in ('net', 'gross', 'cb', 'cs', 'pb', 'ps', 'unk', 'v'):
            cur[f] += b[f] or 0
        if b['vix'] is not None:
            cur['vix'] = b['vix']
        cur['b1'] = b['bi']; cur['te'] = b['te']
    return out


# ───────── 工具 ─────────
def median(a):
    return stt.median(a) if a else None


class RollPct:
    """因果百分位：x 在「過去」的值裡排第幾（0～100），再把 x 放進歷史"""
    def __init__(self, n=3000, minn=100):
        self.q = deque(); self.s = []; self.n = n; self.minn = minn

    def push(self, x):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return None
        p = bisect.bisect_left(self.s, x) / len(self.s) * 100 if len(self.s) >= self.minn else None
        bisect.insort(self.s, x); self.q.append(x)
        if len(self.q) > self.n:
            v = self.q.popleft(); del self.s[bisect.bisect_left(self.s, v)]
        return p


def slot_z(bars, vals, W=30, MINH=8):
    """每根 K 棒和「過去 30 個同類時段、同一根序號」比的穩健標準分數（中位數、絕對離差）"""
    slots = {}
    z = []
    for b, x in zip(bars, vals):
        h = slots.setdefault((b['s'], b['k']), deque())
        zz = None
        if x is not None and len(h) >= MINH:
            hl = list(h); m = stt.median(hl); mad = stt.median([abs(v - m) for v in hl]) * 1.4826
            if mad > 0:
                zz = (x - m) / mad
            else:
                sd = stt.pstdev(hl); zz = (x - m) / sd if sd > 0 else 0.0
            zz = max(-8.0, min(8.0, zz))
        z.append(zz)
        if x is not None:
            h.append(x)
            if len(h) > W:
                h.popleft()
    return z


def calibrate(z, span=5, q=0.90):
    """指數移動平均 → 依過去分布校準到 ±60 → 壓縮成 −100～+100（和網頁相同）"""
    a = 2 / (span + 1)
    ema = None; hist = deque(); qv = None; F = []; sig = []; sg = None
    for i, x in enumerate(z):
        f = None
        if x is not None:
            ema = x if ema is None else ema + a * (x - ema)
            hist.append(abs(ema))
            if len(hist) > 1500:
                hist.popleft()
            if len(hist) >= 120 and (qv is None or i % 25 == 0):
                s = sorted(hist); qv = s[min(len(s) - 1, int(len(s) * q))] or 1e-9
            if qv:
                f = 100 * math.tanh(ATANH6 * ema / qv)
        if f is not None:
            sg = f if sg is None else sg + 0.2 * (f - sg)
        F.append(f); sig.append(sg if f is not None else None)
    return F, sig


# ───────── 特徵（全部只用當下以前的資料） ─────────
def features(bars):
    n = len(bars)
    net = [b['net'] for b in bars]
    imb = [b['net'] / (b['gross'] + 1e-6) if b['gross'] > 0 else 0.0 for b in bars]
    ret = [(b['c'] - b['o']) / b['o'] if b['o'] else 0.0 for b in bars]
    zN = slot_z(bars, net); zI = slot_z(bars, imb); zR = slot_z(bars, ret)
    F, sig = calibrate(zN)
    FI, _ = calibrate(zI)
    # 價格調整後的資金流殘差：zN − β·z報酬，β 只用過去 500 根估計
    resid = []
    xs, ys = deque(), deque()
    sx = sy = sxx = sxy = 0.0
    beta = []
    for i in range(n):
        r = None; bt = None
        if zN[i] is not None and zR[i] is not None:
            if len(xs) >= 100 and sxx - sx * sx / len(xs) > 1e-9:
                m = len(xs); bt = (sxy - sx * sy / m) / (sxx - sx * sx / m)
                r = zN[i] - bt * zR[i]
            xs.append(zR[i]); ys.append(zN[i]); sx += zR[i]; sy += zN[i]; sxx += zR[i] ** 2; sxy += zR[i] * zN[i]
            if len(xs) > 500:
                a, b_ = xs.popleft(), ys.popleft(); sx -= a; sy -= b_; sxx -= a * a; sxy -= a * b_
        resid.append(r); beta.append(bt)
    FR, _ = calibrate(resid)
    # 技術面：指數均線、真實波幅、效率比
    ema20 = []; ema50 = []; e20 = e50 = None; atr = []; at = None; pc = None
    for b in bars:
        e20 = b['c'] if e20 is None else e20 + 2 / 21 * (b['c'] - e20)
        e50 = b['c'] if e50 is None else e50 + 2 / 51 * (b['c'] - e50)
        tr = b['h'] - b['l'] if pc is None else max(b['h'] - b['l'], abs(b['h'] - pc), abs(b['l'] - pc))
        at = tr if at is None else at + (tr - at) / 14
        pc = b['c']; ema20.append(e20); ema50.append(e50); atr.append(max(at, 1.0))
    er = [None] * n
    for i in range(20, n):
        mv = sum(abs(bars[k]['c'] - bars[k - 1]['c']) for k in range(i - 19, i + 1))
        er[i] = abs(bars[i]['c'] - bars[i - 20]['c']) / mv if mv > 0 else 0.0
    # 日內均價（每個時段重新累積）
    vwap = []; cv = cvv = 0.0; last_si = None
    for b in bars:
        if b['si'] != last_si:
            cv = cvv = 0.0; last_si = b['si']
        cv += b['c'] * (b['v'] or 0); cvv += (b['v'] or 0)
        vwap.append(cv / cvv if cvv > 0 else b['c'])
    # 恐慌指數（同網頁）＋市場狀態
    pD, pV, pP, pRV, pER = RollPct(2000), RollPct(2000), RollPct(2000), RollPct(2000), RollPct(1000, 60)
    panic = []; regime = []
    for i, b in enumerate(bars):
        drop = -(b['c'] - bars[i - 6]['c']) / bars[i - 6]['c'] if i >= 6 else None
        dv = b['vix'] - bars[i - 6]['vix'] if i >= 6 and b['vix'] is not None and bars[i - 6]['vix'] is not None else None
        tot = b['cb'] + b['cs'] + b['pb'] + b['ps']
        put = b['pb'] / tot if tot > 0 else None
        rv = None
        if i >= 12:
            r = [math.log(bars[k]['c'] / bars[k - 1]['c']) for k in range(i - 11, i + 1)]
            rv = stt.pstdev(r)
        parts = [p for p in (pD.push(drop), pV.push(dv), pP.push(put), pRV.push(rv)) if p is not None]
        if F[i] is not None:
            parts.append((100 - F[i]) / 2)
        pn = sum(parts) / len(parts) if len(parts) >= 3 else None
        panic.append(pn)
        erp = pER.push(er[i])
        slope = ema50[i] - ema50[i - 10] if i >= 10 else 0
        if pn is not None and pn >= 80:
            rg = 'PANIC'
        elif erp is None:
            rg = None
        elif erp >= 60 and slope > 0 and b['c'] > ema50[i]:
            rg = 'TREND_UP'
        elif erp >= 60 and slope < 0 and b['c'] < ema50[i]:
            rg = 'TREND_DOWN'
        elif erp <= 40:
            rg = 'RANGE'
        else:
            rg = 'TRANSITION'
        regime.append(rg)
    # 衰竭分數：最近 12 根曾有 |F| ≥ 60 的極端方向 d，七個分項各自轉成 0～100
    comps = {k: RollPct(3000, 80) for k in ('d1', 'd2', 'vw', 'ext', 'put')}
    exh = []; exd = []; parts_all = []
    for i, b in enumerate(bars):
        win = [F[k] for k in range(max(0, i - 11), i + 1) if F[k] is not None]
        d = 0
        if win:
            if max(win) >= 60 and (min(win) > -60 or max(win) >= -min(win)):
                d = 1
            elif min(win) <= -60:
                d = -1
        p = {}
        if d and F[i] is not None and i >= 24:
            dF = [d * F[k] if F[k] is not None else None for k in range(i - 23, i + 1)]
            peak = max(x for x in dF[-12:] if x is not None)
            dur = sum(1 for x in dF[-12:] if x is not None and x >= 60)
            p['極端程度與持續'] = max(0.0, min(100.0, (peak - 60) / 40 * 50 + dur / 12 * 50))
            if F[i - 1] is not None and F[i - 2] is not None:
                d1 = -d * (F[i] - F[i - 1]); d2 = -d * ((F[i] - F[i - 1]) - (F[i - 1] - F[i - 2]))
                p['力道減弱'] = comps['d1'].push(d1); p['減弱加速'] = comps['d2'].push(d2)
            cs = [bars[k]['c'] for k in range(i - 23, i + 1)]
            fv = [x for x in dF if x is not None]
            if max(cs) > min(cs) and fv and max(fv) > min(fv):
                pr = (cs[-1] - min(cs)) / (max(cs) - min(cs)) if d > 0 else (max(cs) - cs[-1]) / (max(cs) - min(cs))
                fr = (dF[-1] - min(fv)) / (max(fv) - min(fv))
                p['價格與資金流背離'] = (pr - fr + 1) / 2 * 100
            p['偏離日內均價'] = comps['vw'].push(d * (b['c'] - vwap[i]) / atr[i])
            p['價格延伸幅度'] = comps['ext'].push(d * (b['c'] - ema20[i]) / atr[i])
            tot = b['cb'] + b['cs'] + b['pb'] + b['ps']
            prev = [bars[k] for k in range(i - 12, i)]
            ptot = sum(x['cb'] + x['cs'] + x['pb'] + x['ps'] for x in prev)
            if tot > 0 and ptot > 0:
                hedge = (b['pb'] / tot - sum(x['pb'] for x in prev) / ptot) if d > 0 else (b['cb'] / tot - sum(x['cb'] for x in prev) / ptot)
                p['選擇權避險結構'] = comps['put'].push(hedge)
        p = {k: v for k, v in p.items() if v is not None}
        exh.append(sum(p.values()) / len(p) if len(p) >= 4 else None)
        exd.append(d); parts_all.append(p)
    return {'F': F, 'sig': sig, 'FI': FI, 'FR': FR, 'zN': zN, 'beta': beta, 'panic': panic, 'regime': regime,
            'exh': exh, 'exd': exd, 'exparts': parts_all, 'atr': atr, 'ema20': ema20, 'vwap': vwap}


# ───────── 訊號 ─────────
def signals(bars, X, strat, P, ctx=None):
    F, sig, rg, ex, exd = X['F'], X['sig'], X['regime'], X['exh'], X['exd']
    out = []
    n = len(bars)
    L = 24; lastL = lastS = -10 ** 9
    for i in range(3, n):
        f, f1, f2 = F[i], F[i - 1], F[i - 2]
        if f is None or f1 is None or f2 is None or sig[i] is None:
            continue
        b, pb = bars[i], bars[i - 1]
        lo12 = min(bars[k]['l'] for k in range(max(0, i - 11), i + 1)); hi12 = max(bars[k]['h'] for k in range(max(0, i - 11), i + 1))
        if strat == 'A':
            if b['k'] < 2 or i < L:
                continue
            seg = [(F[k], k) for k in range(i - L, i) if F[k] is not None]
            if not seg:
                continue
            mn, iMn = min(seg); mx, iMx = max(seg)
            pan = any((X['panic'][k] or 0) >= 80 for k in range(i - L, i))
            tr = min(bars[k]['l'] for k in range(i - L, i)); pk = max(bars[k]['h'] for k in range(i - L, i))
            if (mn <= -60 or pan) and f - mn >= 30 and f > sig[i] and f > f1 > f2 and b['c'] > pb['h'] and i - lastL >= 12:
                out.append((i, 1, tr)); lastL = i
            if mx >= 60 and mx - f >= 30 and f < sig[i] and f < f1 < f2 and b['c'] < pb['l'] and i - lastS >= 12:
                out.append((i, -1, pk)); lastS = i
        elif strat == 'B':
            T = P.get('T', 60)
            if f1 < T <= f:
                out.append((i, -1, hi12))
            if f1 > -T >= f:
                out.append((i, 1, lo12))
        elif strat == 'C':
            T = P.get('T', 60)
            w = [F[k] for k in range(max(0, i - 12), i) if F[k] is not None]
            if w and max(w) >= T and f1 >= T - 20 > f:
                out.append((i, -1, hi12))
            if w and min(w) <= -T and f1 <= -(T - 20) < f:
                out.append((i, 1, lo12))
        elif strat in ('D', 'E'):
            E = P.get('E', 70)
            if ex[i] is None or ex[i] < E or exd[i] == 0:
                continue
            d = exd[i]
            if strat == 'E' and rg[i] in ('PANIC', 'TREND_UP' if d > 0 else 'TREND_DOWN'):
                continue
            if d > 0 and f < sig[i] and f < f1 and b['c'] < pb['l']:
                out.append((i, -1, hi12))
            if d < 0 and f > sig[i] and f > f1 and b['c'] > pb['h']:
                out.append((i, 1, lo12))
        elif strat == 'F':
            w = [F[k] for k in range(max(0, i - 12), i) if F[k] is not None]
            if not w:
                continue
            if rg[i] == 'TREND_UP' and min(w) < 0 and f1 < 20 <= f and f > sig[i] and b['c'] > pb['h']:
                out.append((i, 1, lo12))
            if rg[i] == 'TREND_DOWN' and max(w) > 0 and f1 > -20 >= f and f < sig[i] and b['c'] < pb['l']:
                out.append((i, -1, hi12))
        elif strat == 'G':
            if i < 24:
                continue
            pan = any((X['panic'][k] or 0) >= 80 for k in range(i - 24, i))
            w = [F[k] for k in range(i - 24, i) if F[k] is not None]
            lo24 = min(bars[k]['l'] for k in range(i - 24, i + 1))
            lo3 = min(bars[k]['l'] for k in range(i - 2, i + 1))
            if pan and w and min(w) <= -60 and f > f1 > f2 and lo3 > lo24 and b['c'] > pb['h']:
                out.append((i, 1, lo24))
        elif strat == 'H':
            T = P.get('T', 60)
            h60 = ctx                       # 每根 15 分 K 棒當下「已收盤」的最近 4 根 60 分力道
            w = h60[i]
            if not w or b['k'] < 1:
                continue
            hi16 = max(bars[k]['h'] for k in range(max(0, i - 15), i + 1)); lo16 = min(bars[k]['l'] for k in range(max(0, i - 15), i + 1))
            if max(w) >= T and f < sig[i] and f < f1 and b['c'] < pb['l']:
                out.append((i, -1, hi16))
            if min(w) <= -T and f > sig[i] and f > f1 and b['c'] > pb['h']:
                out.append((i, 1, lo16))
    return out


def h60_context(b15, b60, F60):
    """15 分 K 棒收盤當下，已經收盤的最近 4 根 60 分 K 棒的力道（不偷看還沒收的 60 分 K）"""
    ends = [b['te'] for b in b60]
    out = []
    j = 0
    for b in b15:
        while j < len(ends) and ends[j] <= b['te']:
            j += 1
        w = [F60[k] for k in range(max(0, j - 4), j) if F60[k] is not None]
        out.append(w)
    return out


# ───────── 回測 ─────────
def first_at(base_t, t):
    return bisect.bisect_left(base_t, t)


def simulate(base, base_t, bars, sigs, H, method='trade', stop=True):
    """回傳每筆交易（點數，未扣成本）；同方向持倉中的新訊號不進場"""
    nb = H // 5
    trades = []
    busy = {1: -1, -1: -1}
    for i, d, sp in sigs:
        j = first_at(base_t, bars[i]['te'])
        if j >= len(base):
            continue
        if j <= busy[d]:
            continue
        e = base[j]['o']
        if method == 'trade':
            x = j + nb - 1
        elif method == 'session':
            x = j + nb - 1
            si = base[j]['si']
            while x > j and (x >= len(base) or base[x]['si'] != si):
                x -= 1
        else:                                                   # 實際經過時間
            x = first_at(base_t, base[j]['t'] + H * 60) - 1
            if x < j:
                x = j
        if x >= len(base):
            continue
        out_px = base[x]['c']; hit = False; mfe = mae = 0.0
        gap = 0.0
        for k in range(j, x + 1):
            bk = base[k]
            if d > 0:
                mfe = max(mfe, bk['h'] - e); mae = max(mae, e - bk['l'])
            else:
                mfe = max(mfe, e - bk['l']); mae = max(mae, bk['h'] - e)
            if stop and ((d > 0 and bk['l'] <= sp) or (d < 0 and bk['h'] >= sp)):
                fill = (min(bk['o'], sp) if d > 0 else max(bk['o'], sp)) if k > j else sp
                if k > j and ((d > 0 and bk['o'] < sp) or (d < 0 and bk['o'] > sp)):
                    gap = abs(bk['o'] - sp)
                out_px = fill; hit = True; x = k
                break
        if stop and ((d > 0 and e <= sp) or (d < 0 and e >= sp)):
            out_px = e; hit = True; x = j
        trades.append({'i': i, 'j': j, 'x': x, 'd': d, 'pts': d * (out_px - e), 'mfe': mfe, 'mae': mae, 'stop': hit, 'gap': gap,
                       'td': base[j]['td'], 's': base[j]['s'], 'mo': base[j]['td'][:7], 't': base[j]['t'], 'delay': None})
        busy[d] = x
    return trades


def block_ci(trades, cost, reps=2000, seed=7):
    days = defaultdict(list)
    for t in trades:
        days[t['td']].append(t['pts'] - cost)
    keys = list(days)
    if len(keys) < 5:
        return None
    rnd = random.Random(seed)
    means = []
    for _ in range(reps):
        s = n = 0
        for _k in range(len(keys)):
            v = days[keys[rnd.randrange(len(keys))]]; s += sum(v); n += len(v)
        means.append(s / n if n else 0)
    means.sort()
    return [round(means[int(reps * 0.025)], 1), round(means[int(reps * 0.975)], 1)]


def summarize(trades, cost, base_m=None):
    v = [t['pts'] - cost for t in trades]
    if not v:
        return {'n': 0}
    w = [x for x in v if x > 0]; l = [x for x in v if x <= 0]
    eq = pk = dd = 0.0
    for x in v:
        eq += x; pk = max(pk, eq); dd = min(dd, eq - pk)
    mo = defaultdict(list)
    for t, x in zip(trades, v):
        mo[t['mo']].append(x)
    mpos = sum(1 for k in mo if sum(mo[k]) > 0)
    dn = {s: [x for t, x in zip(trades, v) if t['s'] == s] for s in ('D', 'N')}
    r = {'n': len(v), 'mean': round(stt.mean(v), 1), 'med': round(stt.median(v), 1), 'win': round(len(w) / len(v) * 100, 1),
         'pf': round(sum(w) / -sum(l), 2) if l and sum(l) < 0 else None, 'mdd': round(dd), 'sum': round(sum(v)),
         'mfe': round(stt.mean(t['mfe'] for t in trades)), 'mae': round(stt.mean(t['mae'] for t in trades)),
         'stopPct': round(sum(1 for t in trades if t['stop']) / len(trades) * 100, 1),
         'ci': block_ci(trades, cost), 'months': len(mo), 'monthsPos': mpos,
         'monthly': {k: round(sum(x) / len(x), 1) for k, x in sorted(mo.items())},
         'day': round(stt.mean(dn['D']), 1) if dn['D'] else None, 'night': round(stt.mean(dn['N']), 1) if dn['N'] else None,
         'nDay': len(dn['D']), 'nNight': len(dn['N'])}
    if base_m is not None:
        bm = [base_m.get((t['d'], t['s'], t.get('rg'))) for t in trades]
        bm = [x for x in bm if x is not None]
        if bm:
            r['matched'] = round(stt.mean(bm) - cost, 1); r['excess'] = round(r['mean'] - r['matched'], 1)
    return r


def matched_baseline(base, base_t, bars, X, H):
    """配對基準：同一時段（日盤／夜盤）、同一市場狀態下，隨便哪一根都進場的平均（不停損）"""
    nb = H // 5
    acc = defaultdict(list)
    for i, b in enumerate(bars):
        j = first_at(base_t, b['te'])
        x = j + nb - 1
        if x >= len(base):
            continue
        r = base[x]['c'] - base[j]['o']
        rg = X['regime'][i]
        for d in (1, -1):
            acc[(d, base[j]['s'], rg)].append(d * r)
    return {k: stt.mean(v) for k, v in acc.items() if len(v) >= 30}


def spearman(a, b):
    if len(a) < 30:
        return None
    def rank(x):
        o = sorted(range(len(x)), key=lambda k: x[k]); r = [0] * len(x)
        for p, k in enumerate(o):
            r[k] = p
        return r
    ra, rb = rank(a), rank(b)
    ma = (len(a) - 1) / 2
    num = sum((x - ma) * (y - ma) for x, y in zip(ra, rb)); den = sum((x - ma) ** 2 for x in ra)
    return num / den if den else None


def ic_table(base, base_t, bars, X, H, keys):
    """每個特徵和「下一根開盤進場、持有 H 分鐘」報酬的等級相關，逐月計算"""
    nb = H // 5
    fwd = []
    for b in bars:
        j = first_at(base_t, b['te']); x = j + nb - 1
        fwd.append(base[x]['c'] - base[j]['o'] if x < len(base) else None)
    res = {}
    for name, ser, sign in keys:
        mo = defaultdict(lambda: ([], []))
        for i, b in enumerate(bars):
            v = ser[i]
            if v is None or fwd[i] is None:
                continue
            a, c = mo[b['td'][:7]]; a.append(v); c.append(fwd[i] * sign(i) if callable(sign) else fwd[i] * sign)
        ics = {k: spearman(*v) for k, v in sorted(mo.items())}
        ics = {k: round(v, 3) for k, v in ics.items() if v is not None}
        vals = list(ics.values())
        tstat = (stt.mean(vals) / (stt.stdev(vals) / math.sqrt(len(vals)))) if len(vals) >= 3 and stt.stdev(vals) > 0 else None
        res[name] = {'mean': round(stt.mean(vals), 3) if vals else None, 'pos': sum(1 for v in vals if v > 0), 'months': len(vals),
                     't': round(tstat, 2) if tstat is not None else None, 'monthly': ics}
    return res


STRATS = [('A', '原始 GO'), ('B', '極端直接反向'), ('C', '極端退潮後反向'), ('D', '衰竭＋價格確認'),
          ('E', '衰竭＋價格確認（只在非趨勢行情）'), ('F', '順勢延續'), ('G', '恐慌修復（只做多）'), ('H', '60 分極端＋15 分確認')]
GRID = {'B': [{'T': 50}, {'T': 60}, {'T': 70}], 'C': [{'T': 50}, {'T': 60}, {'T': 70}],
        'D': [{'E': 60}, {'E': 70}, {'E': 80}], 'E': [{'E': 60}, {'E': 70}, {'E': 80}], 'H': [{'T': 50}, {'T': 60}, {'T': 70}]}
DEFAULT = {'B': {'T': 60}, 'C': {'T': 60}, 'D': {'E': 70}, 'E': {'E': 70}, 'H': {'T': 60}}


def months_between(a, b):
    out = []; y, m = int(a[:4]), int(a[5:7])
    while '%04d-%02d' % (y, m) <= b:
        out.append('%04d-%02d' % (y, m)); m += 1
        if m > 12:
            y += 1; m = 1
    return out


def walk_forward(base, base_t, bars, X, strat, H, ctx, cost=2):
    """逐月滾動：用前 3 個月選參數（平均淨報酬最高、至少 8 筆），套到下一個月；只統計下一個月的交易"""
    grid = GRID.get(strat)
    if not grid:
        return None
    cache = {}
    for p in grid:
        key = json.dumps(p, sort_keys=True)
        tr = simulate(base, base_t, bars, signals(bars, X, strat, p, ctx), H)
        cache[key] = tr
    mos = sorted({b['td'][:7] for b in bars})
    oos = []; picks = {}
    for k in range(3, len(mos)):
        train = set(mos[k - 3:k]); test = mos[k]
        best, bv = json.dumps(DEFAULT[strat], sort_keys=True), -1e9
        for key, tr in cache.items():
            v = [t['pts'] - cost for t in tr if t['mo'] in train]
            if len(v) >= 8 and stt.mean(v) > bv:
                best, bv = key, stt.mean(v)
        picks[test] = json.loads(best)
        oos += [t for t in cache[best] if t['mo'] == test]
    oos.sort(key=lambda t: t['t'])
    return {'picks': picks, 'stats': summarize(oos, cost)}


def run():
    sess, idx = load_sessions()
    base = base_series(sess)
    base_t = [b['t'] for b in base]
    res = {'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'), 'freeze': FREEZE,
           'range': [base[0]['td'], base[-1]['td']], 'bars5': len(base), 'strategies': dict(STRATS), 'tf': {}}
    TFS = {}
    for tf in (5, 15, 60):
        bars = aggregate(base, tf)
        X = features(bars)
        TFS[tf] = (bars, X)
    ctx15 = h60_context(TFS[15][0], TFS[60][0], TFS[60][1]['F'])
    for tf in (5, 15, 60):
        bars, X = TFS[tf]
        for i, b in enumerate(bars):
            b['rg'] = X['regime'][i]
        R = {'bars': len(bars), 'ic': {}, 'regimes': {}, 'exhParts': {}, 'exhBuckets': {}, 'strat': {}}
        # 資金流引擎三種寫法的預測力比較
        for H in (60, 240):
            R['ic'][str(H)] = ic_table(base, base_t, bars, X, H, [('淨流量力道', X['F'], 1), ('多空失衡', X['FI'], 1), ('資金流殘差', X['FR'], 1)])
        # 市場狀態：各狀態出現比例與之後 60 分鐘平均漲跌
        bm60 = matched_baseline(base, base_t, bars, X, 60)
        cnt = defaultdict(int)
        for r in X['regime']:
            cnt[r] += 1
        for rg in ('TREND_UP', 'TREND_DOWN', 'RANGE', 'PANIC', 'TRANSITION'):
            R['regimes'][rg] = {'pct': round(cnt[rg] / len(bars) * 100, 1),
                                'day': round(bm60[(1, 'D', rg)], 1) if (1, 'D', rg) in bm60 else None,
                                'night': round(bm60[(1, 'N', rg)], 1) if (1, 'N', rg) in bm60 else None}
        # 衰竭分項：各自和「反轉方向報酬」的等級相關（只看有極端的 K 棒）
        partnames = ['極端程度與持續', '力道減弱', '減弱加速', '價格與資金流背離', '偏離日內均價', '價格延伸幅度', '選擇權避險結構']
        ser = {p: [X['exparts'][i].get(p) for i in range(len(bars))] for p in partnames}
        ser['衰竭分數（合計）'] = X['exh']
        R['exhParts'] = ic_table(base, base_t, bars, X, 60, [(p, ser[p], (lambda i, d=X['exd']: -d[i] if d[i] else 0)) for p in ser])
        # 衰竭分數分區
        nb = 60 // 5
        bk = {'0–39': [], '40–69': [], '70–84': [], '85–100': []}
        for i, b in enumerate(bars):
            s = X['exh'][i]
            if s is None or not X['exd'][i]:
                continue
            j = first_at(base_t, b['te']); x = j + nb - 1
            if x >= len(base):
                continue
            r = -X['exd'][i] * (base[x]['c'] - base[j]['o'])
            key = '0–39' if s < 40 else '40–69' if s < 70 else '70–84' if s < 85 else '85–100'
            bk[key].append(r)
        R['exhBuckets'] = {k: {'n': len(v), 'mean': round(stt.mean(v), 1) if v else None, 'win': round(sum(1 for x in v if x > 0) / len(v) * 100, 1) if v else None} for k, v in bk.items()}
        # 策略
        bm = {H: matched_baseline(base, base_t, bars, X, H) for H in (60, 240)}
        for code, name in STRATS:
            if code == 'H' and tf != 15:
                continue
            P = DEFAULT.get(code, {})
            sg = signals(bars, X, code, P, ctx15 if code == 'H' else None)
            S = {'n_signals': len(sg), 'params': P}
            for H in (60, 240):
                tr = simulate(base, base_t, bars, sg, H)
                for t in tr:
                    t['rg'] = bars[t['i']]['rg']
                trn = simulate(base, base_t, bars, sg, H, stop=False)
                for t in trn:
                    t['rg'] = bars[t['i']]['rg']
                past = [t for t in tr if t['t'] < FREEZE_T]; fwd = [t for t in tr if t['t'] >= FREEZE_T]
                S[str(H)] = {'stop': summarize(past, 2), 'nostop': summarize([t for t in trn if t['t'] < FREEZE_T], 2, bm[H]),
                             'costs': {str(c): summarize(past, c) for c in COSTS},
                             'session': summarize([t for t in simulate(base, base_t, bars, sg, H, 'session') if t['t'] < FREEZE_T], 2),
                             'elapsed': summarize([t for t in simulate(base, base_t, bars, sg, H, 'elapsed') if t['t'] < FREEZE_T], 2),
                             'byRegime': {rg: summarize([t for t in past if t['rg'] == rg], 2) for rg in ('TREND_UP', 'TREND_DOWN', 'RANGE', 'PANIC', 'TRANSITION')},
                             'byDir': {'long': summarize([t for t in past if t['d'] > 0], 2), 'short': summarize([t for t in past if t['d'] < 0], 2)},
                             'forward': summarize(fwd, 2)}
                for k in ('stop', 'nostop', 'session', 'elapsed', 'forward'):
                    S[str(H)][k].pop('monthly', None) if k != 'stop' else None
            wf = walk_forward(base, base_t, [b for b in bars], X, code, 60, ctx15 if code == 'H' else None)
            if wf:
                S['wf'] = wf
            R['strat'][code] = S
        # 目前狀態（給頁面顯示）
        last = len(bars) - 1
        R['now'] = {'t': bars[last]['t'], 'td': bars[last]['td'], 's': bars[last]['s'], 'F': X['F'][last] and round(X['F'][last], 1),
                    'FR': X['FR'][last] and round(X['FR'][last], 1), 'FI': X['FI'][last] and round(X['FI'][last], 1),
                    'regime': X['regime'][last], 'exh': X['exh'][last] and round(X['exh'][last], 1), 'exd': X['exd'][last],
                    'exparts': {k: round(v, 1) for k, v in X['exparts'][last].items()}}
        # 給頁面畫圖：最近 300 根的殘差、衰竭、狀態
        k0 = max(0, len(bars) - 300)
        R['series'] = {'t': [b['t'] for b in bars[k0:]], 'c': [b['c'] for b in bars[k0:]],
                       'F': [None if v is None else round(v, 1) for v in X['F'][k0:]], 'FR': [None if v is None else round(v, 1) for v in X['FR'][k0:]],
                       'exh': [None if v is None else round(v) for v in X['exh'][k0:]], 'exd': X['exd'][k0:], 'rg': X['regime'][k0:]}
        res['tf'][str(tf)] = R
    json.dump(res, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    return res


def selftest():
    """因果性：用前 70% 的資料算出的每一個數值，必須和用全部資料算出的完全一樣（代表沒有偷看未來）"""
    sess, _ = load_sessions()
    base = base_series(sess)
    ok = True
    for tf in (5, 15, 60):
        full = aggregate(base, tf)
        cut = int(len(full) * 0.7)
        part = aggregate(base[:full[cut]['b0']], tf)
        Xf, Xp = features(full), features(part)
        for key in ('F', 'sig', 'FI', 'FR', 'panic', 'regime', 'exh'):
            a, b = Xf[key][:len(part)], Xp[key]
            bad = sum(1 for x, y in zip(a, b) if (x is None) != (y is None) or (x is not None and (x != y if isinstance(x, str) else abs(x - y) > 1e-9)))
            print('tf', tf, key, '不一致', bad, '／', len(part))
            ok = ok and bad == 0
        sf = signals(full, Xf, 'D', {'E': 70}); sp = signals(part, Xp, 'D', {'E': 70})
        sf = [s for s in sf if s[0] < len(part) - 1]; sp = [s for s in sp if s[0] < len(part) - 1]
        print('tf', tf, '訊號一致', sf == sp, len(sp)); ok = ok and sf == sp
    print('因果性測試', '通過' if ok else '失敗')
    return ok


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'test':
        sys.exit(0 if selftest() else 1)
    r = run()
    print('done', r['range'], r['bars5'])
