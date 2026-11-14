"""權證佈局整理：從 d/日期.json（每個分點每天在每檔標的的權證進出）做出網頁要的檔案

  wt.json        各標的 1／5／20 日被佈局多少、回本股價、剩餘天數、疑似知情佈局、高勝率波段權證分點、門檻
  wu/代號.json   這檔標的每天每個分點的權證買賣、回本股價、剩餘天數，和目前還抱著的權證（畫圖、推演能放多久）
  wb/分點.json   這個分點的權證佈局紀錄、每次之後標的漲跌、勝率、目前還抱著的權證

回本股價（損益兩平）：認購＝履約價 ＋ 權證買價 ÷ 行使比例；認售＝履約價 − 權證買價 ÷ 行使比例。
剩餘天數：從買進那天到權證最後交易日的平日天數（約等於交易日）。

看多金額＝認購淨買 − 認售淨買（買認售是看空）；約當股數＝權證單位 × 行使比例。
外資券商、券商總公司（代號最後一碼是 0，多半是自營、造市或法人帳戶）不列入佈局分析，只顯示合計。
"""
import json, math, os, re, shutil, statistics as ST

TH = {
    'ep': 500000,          # 一次「佈局」：單一分點一天在一檔標的買進認購（或認售）≥ 50 萬元
    'ep_gap': 5,           # 同一分點同一檔同方向 5 天內只算一次
    'hor': 10, 'hit': 30,  # 買進後第 10 個交易日，權證比買價漲 ≥ 30% 算「抓到行情」
    'min_n': 5,            # 至少 5 次佈局才排勝率
    'big': 3000000,        # 疑似知情：5 日淨額 ≥ 300 萬
    'eqp': 1.0,            # 或約當股數 ≥ 5 日成交量 1%
    'focus': 60,           # 集中押注：這檔佔這個分點 5 日權證進出 ≥ 60%
    'new_look': 15,        # 新進場：前 15 天沒碰過這檔的權證
    'flip': 60,            # 權證隔日沖型：大買後隔天賣掉 ≥ 6 成的比例 ≥ 60%
    'pro': 60,             # 權證專業戶：平均每天進出 ≥ 60 檔標的
    'good_days': 20,       # 至少累積 20 個完整交易日才排高勝率分點
    'short': 30,           # 短天期：買進的權證平均剩 ≤ 30 個交易日到期
    'otm': 15,             # 深價外：履約價離股價 ≥ 15%
    'hv_min': 12,          # 歷史波動率至少要 12 個日報酬
    'tr_min': 300000,      # 一筆權證交易買進 ≥ 30 萬才統計
    'dp_min': 4,           # 深價外交易至少 4 筆才排名
    'hard': 20,            # 低勝算重押：以平常波動，20 個交易日內漲（跌）到回本的機率 < 20%
}
FOREIGN = ('麥格理', '美林', '摩根', '高盛', '野村', '花旗', '瑞銀', '大和', '法銀', '匯豐', '瑞士', '德意志', '星展', '里昂', '法興', '巴克萊', '渣打', '港商', '美商', '新加坡', '日商', '英商')


def kind(bid, name):
    if any(k in (name or '') for k in FOREIGN): return 'f'
    return 'h' if bid.endswith('0') else 'b'


def safe(b): return re.sub(r'[^0-9A-Za-z]', '_', b)


def jl(p):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return None


def wdays(a, b, _c={}):
    """a 之後到 b（含）有幾個平日（約等於交易日，不扣國定假日）"""
    k = (a, b)
    if k not in _c:
        import datetime as dt
        x, y = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        if y <= x: _c[k] = 0
        else:
            n = (y - x).days; w, r = divmod(n, 7); c = w * 5; wd = x.weekday()
            for i in range(1, r + 1):
                if (wd + i) % 7 < 5: c += 1
            _c[k] = c
    return _c[k]


# ── 權證評價（Black-Scholes；權證價格 ÷ 行使比例＝每股權利金）──
R_F = 0.015
SQ2 = math.sqrt(2)


def ncdf(x): return 0.5 * (1 + math.erf(x / SQ2))


def bs(cp, S, K, T, s):
    if T <= 1e-6 or s <= 1e-6: return max(0.0, S - K) if cp == 'C' else max(0.0, K - S)
    v = s * math.sqrt(T); d1 = (math.log(S / K) + (R_F + s * s / 2) * T) / v; d2 = d1 - v; df = math.exp(-R_F * T)
    return S * ncdf(d1) - K * df * ncdf(d2) if cp == 'C' else K * df * ncdf(-d2) - S * ncdf(-d1)


def delta(cp, S, K, T, s):
    if T <= 1e-6: return (1.0 if S > K else 0.0) if cp == 'C' else (-1.0 if S < K else 0.0)
    d1 = (math.log(S / K) + (R_F + s * s / 2) * T) / (s * math.sqrt(T))
    return ncdf(d1) if cp == 'C' else ncdf(d1) - 1


def impl_vol(cp, S, K, T, P):
    lo, hi = 0.02, 4.0
    if bs(cp, S, K, T, lo) > P or bs(cp, S, K, T, hi) < P: return None
    for _ in range(24):
        m = (lo + hi) / 2
        if bs(cp, S, K, T, m) < P: lo = m
        else: hi = m
    return (lo + hi) / 2


def need_move(cp, S, K, T, iv, P, h):
    """波動率不變下，h 個交易日後股價要到哪裡權證才回到 P → 要漲（認售：要跌）幾 %"""
    Th = max(T - h / 245, 0)
    f = lambda x: bs(cp, x, K, Th, iv) - P
    if cp == 'C':
        lo, hi = S * 0.3, S * 6
        if f(hi) < 0: return None
        if f(lo) > 0: return -70.0
        for _ in range(26):
            m = (lo + hi) / 2
            if f(m) < 0: lo = m
            else: hi = m
        return ((lo + hi) / 2 / S - 1) * 100
    lo, hi = S * 0.02, S * 3
    if f(lo) < 0: return None
    if f(hi) > 0: return -70.0
    for _ in range(26):
        m = (lo + hi) / 2
        if f(m) > 0: lo = m
        else: hi = m
    return (1 - (lo + hi) / 2 / S) * 100


def flat_days(cp, S, K, n, iv, V):
    """股價不動、波動率不變，幾個交易日後權證會掉到 V 的 7 成（到期都不會就回傳 n）"""
    if bs(cp, S, K, 0, iv) >= 0.7 * V: return n
    lo, hi = 0, n
    while hi - lo > 1:
        m = (lo + hi) // 2
        if bs(cp, S, K, (n - m) / 245, iv) <= 0.7 * V: hi = m
        else: lo = m
    return hi


def opt(cp, S, K, n, P):
    """買進當下的評價 → [隱含波動率%, 有效槓桿, 每天時間價值流失%, 10 天需漲%, 20 天需漲%, 股價不動幾天掉 3 成]"""
    if not (S and K and P and n and n > 0): return None
    T = n / 245; iv = impl_vol(cp, S, K, T, P)
    if iv is None: return None
    lev = abs(delta(cp, S, K, T, iv)) * S / P
    th = (P - bs(cp, S, K, max(T - 1 / 245, 0), iv)) / P * 100
    n10, n20 = need_move(cp, S, K, T, iv, P, 10), need_move(cp, S, K, T, iv, P, 20)
    if n20 is None: return None
    return [round(iv * 100, 1), round(lev, 1), round(th, 2), round(n10, 1) if n10 is not None else None, round(n20, 1), flat_days(cp, S, K, n, iv, P)]


def prob(cp, need, hv, h=20):
    """以平常的波動（歷史波動率），h 個交易日後股價漲（跌）超過 need% 的機率"""
    if need is None or not hv: return None
    z = math.log(1 + need / 100) if cp == 'C' else math.log(max(1e-4, 1 - need / 100))
    sd = hv * math.sqrt(h / 245)
    return (1 - ncdf(z / sd)) * 100 if cp == 'C' else ncdf(z / sd) * 100


# 一筆（分點、標的、日）的欄位（內部用加總，輸出時再換成平均）
# 0 看多淨額 1 認購淨 2 認售淨 3 約當股數淨 4 進出總額 5 認購買 6 認購賣 7 認售買 8 認售賣
# 9 認購買進約當股數 10 Σ回本價×約當股數 11 Σ剩餘天數×金額 12 Σ價外%×金額 13 認售買進約當股數 14 15 16 同左（認售）
# 17 認購有評價的買進金額 18 Σ隱含波動率 19 Σ槓桿 20 Σ每天時間價值% 21 Σ20 天需漲% 22 Σ不動撐幾天 23 Σ機率 24 機率的權重；25～32 同左（認售）
NF = 33
PM = [-10, -5, 0, 3, 5, 8, 10, 15, 20, 30]     # 理論獲利表：股票往押的方向走幾 %
PH = [3, 5, 10, 20]                           # 理論獲利表：買進後幾個交易日


def addw(a, cp, ratio, K, exp, d, S, bq, sq, bm, sm, g, hv):
    eb, es = bq * ratio, sq * ratio
    if cp == 'C':
        a[1] += bm - sm; a[5] += bm; a[6] += sm; a[3] += eb - es
        if bq and ratio and K:
            be = K + bm / bq / ratio; a[9] += eb; a[10] += be * eb
            a[11] += wdays(d, exp) * bm if exp else 0
            if S: a[12] += (K / S - 1) * 100 * bm
    else:
        a[2] += bm - sm; a[7] += bm; a[8] += sm; a[3] -= eb - es
        if bq and ratio and K:
            be = K - bm / bq / ratio; a[13] += eb; a[14] += be * eb
            a[15] += wdays(d, exp) * bm if exp else 0
            if S: a[16] += (1 - K / S) * 100 * bm
    if g and bm:
        o = 17 if cp == 'C' else 25
        a[o] += bm; a[o + 1] += g[0] * bm; a[o + 2] += g[1] * bm; a[o + 3] += g[2] * bm; a[o + 4] += g[4] * bm; a[o + 5] += g[5] * bm
        p = prob(cp, g[4], hv)
        if p is not None: a[o + 6] += p * bm; a[o + 7] += bm
    a[0] = a[1] - a[2]; a[4] += bm + sm


def avg(a):
    """內部加總 → 輸出：
    0 看多 1 認購淨 2 認售淨 3 約當淨 4 認購買 5 認購賣 6 認售買 7 認售賣 8 認購買約當 9 認購到期回本價 10 認購剩餘天 11 認購價外%
    12 認售買約當 13 認售到期回本價 14 認售剩餘天 15 認售價外%
    16 認購隱含波動率% 17 槓桿 18 每天時間價值% 19 20 天需漲% 20 不動撐幾天 21 20 天達成機率%；22～27 同左（認售）"""
    r = lambda v, n=0: round(v, n) if n else round(v)
    out = [r(a[0]), r(a[1]), r(a[2]), r(a[3]), r(a[5]), r(a[6]), r(a[7]), r(a[8]),
           r(a[9]), r(a[10] / a[9], 2) if a[9] else None, r(a[11] / a[5]) if a[5] and a[11] else None, r(a[12] / a[5], 1) if a[5] and a[12] else None,
           r(a[13]), r(a[14] / a[13], 2) if a[13] else None, r(a[15] / a[7]) if a[7] and a[15] else None, r(a[16] / a[7], 1) if a[7] and a[16] else None]
    for o in (17, 25):
        w = a[o]
        out += [r(a[o + 1] / w, 1), r(a[o + 2] / w, 1), r(a[o + 3] / w, 2), r(a[o + 4] / w, 1), r(a[o + 5] / w), r(a[o + 6] / a[o + 7]) if a[o + 7] else None] if w else [None] * 6
    return out


def build(OUT, days, meta, log):
    names = meta.get('traders', {})
    WM = jl(os.path.join(OUT, 'wmap.json')) or {}
    # 時間軸：交易日曆（有收盤價的天都放進來）；分點資料只用「新格式、整天查完」的天
    DATA = {}
    for d in days:
        x = jl(os.path.join(OUT, 'd', d + '.json'))
        if x and x.get('v'): DATA[d] = x
    isfull = lambda x: x.get('mv') == 3 and x.get('full') and x.get('w') is not None
    fulls = [d for d in days if d in DATA and isfull(DATA[d])]
    if not fulls: log('權證整理：還沒有完整的一天'); return
    D = [d for d in days if d >= fulls[0]]
    nd = len(D); LAST = D[-1]
    F = [t for t, d in enumerate(D) if d in DATA and isfull(DATA[d])]
    FS = set(F)
    KD = {b: kind(b, names.get(b, b)) for b in names}
    # 歷史波動率（最近 20 個交易日、年化）：用每天存的收盤價
    DI = {d: i for i, d in enumerate(days)}
    _hv = {}
    def hv(tg, d):
        k = (tg, d)
        if k not in _hv:
            i = DI[d]; cs = [((DATA.get(days[j]) or {}).get('v', {}).get(tg) or [None])[0] for j in range(max(0, i - 20), i + 1)]
            rs = [math.log(y / x) for x, y in zip(cs, cs[1:]) if x and y]
            _hv[k] = ST.pstdev(rs) * math.sqrt(245) if len(rs) >= TH['hv_min'] else None
        return _hv[k]
    # 每檔權證每天的評價（用當天所有分點的平均買價；算過的存在 g/日期.json，不用重算）
    G = {}; IVL = {}
    os.makedirs(os.path.join(OUT, 'g'), exist_ok=True)
    for t in F:
        d = D[t]; fp = os.path.join(OUT, 'g', d + '.json'); g = jl(fp)
        if g is None:
            V = DATA[d]['v']; agg = {}
            for rows in DATA[d]['w'].values():
                for wc, bq, sq, bm, sm in rows:
                    if bq: x = agg.setdefault(wc, [0, 0.0]); x[0] += bq; x[1] += bm
            g = {}
            for wc, (bq, bm) in agg.items():
                m = WM.get(wc)
                if not m or bm < 30000 or not m[2] or not m[3] or not m[4]: continue
                S = (V.get(m[0]) or [None])[0]
                r = opt(m[1], S, m[3], wdays(d, m[4]), bm / bq / m[2]) if S else None
                if r: g[wc] = r
            with open(fp, 'w') as f: json.dump(g, f, separators=(',', ':'))
        G[d] = g
        for wc, r in g.items(): IVL[wc] = r[0] / 100
    for fn in os.listdir(os.path.join(OUT, 'g')):
        if fn[:10] < days[0]: os.remove(os.path.join(OUT, 'g', fn))
    U = {}; BT = {}; FT = {}; NT = {}; POS = {}
    for t in F:
        d = D[t]; V = DATA[d]['v']; g = G[d]
        for b, rows in DATA[d]['w'].items():
            k = KD.get(b, 'b'); per = {}
            for wc, bq, sq, bm, sm in rows:
                m = WM.get(wc)
                if not m: continue
                tg, cp, ratio, K, exp = m
                S = (V.get(tg) or [None])[0]
                addw(per.setdefault(tg, [0.0] * NF), cp, ratio, K, exp, d, S, bq, sq, bm, sm, g.get(wc), hv(tg, d))
                if k == 'b':
                    p = POS.setdefault(b, {}).setdefault(wc, [0, 0, 0.0]); p[0] += bq - sq; p[1] += bq; p[2] += bm
            if k == 'b': NT.setdefault(b, []).append(sum(1 for a in per.values() if a[4] >= 30000))
            for tg, a in per.items():
                if a[4] < 30000: continue
                if k != 'b':
                    f = FT.setdefault(tg, {}).setdefault(t, [0, 0]); f[0] += a[0]; f[1] += a[3]; continue
                U.setdefault(tg, {}).setdefault(t, {})[b] = a
                BT.setdefault(b, {}).setdefault(t, {})[tg] = a
    # 權證專業戶：平均每天進出 ≥ 60 檔標的的權證（多半是做價差、造市避險的帳戶，不是押單一檔）
    pro = {b for b, L in NT.items() if ST.mean(L) >= TH['pro']}
    px = lambda tg, t: ((DATA.get(D[t]) or {}).get('v', {}).get(tg) or [None, 0, 0])
    close = lambda tg, t: px(tg, t)[0]

    # ── 理論獲利表：一般分點買的權證，買進後 h 天、股票往押的方向走 m%，權證大約賺賠多少（Black-Scholes、隱含波動率不變）──
    # 每天算一次存在 g2/日期.json（加總後的值，不用重算）；依「價外程度 × 剩餘天數」分組
    os.makedirs(os.path.join(OUT, 'g2'), exist_ok=True)
    NG = len(PM) * len(PH)
    def okey(cp, S, K, dl):
        o = (K / S - 1) * 100 if cp == 'C' else (1 - K / S) * 100
        return ('A' if o <= 5 else 'B' if o <= 15 else 'C') + ('1' if dl <= 60 else '2' if dl <= 120 else '3')
    THEO = {}
    for t in F:
        d = D[t]; fp = os.path.join(OUT, 'g2', d + '.json'); g2 = jl(fp)
        if g2 is None:
            V = DATA[d]['v']; agg = {}
            for b, rows in DATA[d]['w'].items():
                if KD.get(b, 'b') != 'b' or b in pro: continue
                for wc, bq, sq, bm, sm in rows:
                    if bq: x = agg.setdefault(wc, [0, 0.0]); x[0] += bq; x[1] += bm
            g2 = {'u': {}, 'b': {}}
            for wc, (bq, bm) in agg.items():
                m = WM.get(wc); gg = G[d].get(wc)
                if not m or not gg or bm < 50000 or not m[4]: continue
                tg, cp, ratio, K, exp = m; S = (V.get(tg) or [None])[0]; dl = wdays(d, exp)
                if not S or dl <= 3: continue
                iv = gg[0] / 100; T_ = dl / 245; P0 = bs(cp, S, K, T_, iv)
                if P0 <= 0: continue
                sg = 1 if cp == 'C' else -1
                row = [bm] + [(bs(cp, S * (1 + sg * mv / 100), K, max(T_ - h / 245, 0), iv) / P0 - 1) * 100 * bm for h in PH for mv in PM]
                for key in (tg + '|' + cp, cp + '|' + okey(cp, S, K, dl), cp + '|all'):
                    dst = g2['u' if '|' in key and key.split('|')[0] == tg else 'b'].setdefault(key, [0.0] * (NG + 1))
                    for i in range(NG + 1): dst[i] += row[i]
            for k in ('u', 'b'):
                g2[k] = {x: [round(v, 1) for v in y] for x, y in g2[k].items()}
            with open(fp, 'w') as f: json.dump(g2, f, separators=(',', ':'))
        THEO[t] = g2
    for fn in os.listdir(os.path.join(OUT, 'g2')):
        if fn[:10] < days[0]: os.remove(os.path.join(OUT, 'g2', fn))
    def theo(keys, part, win):
        acc = [0.0] * (NG + 1)
        for t in win:
            for k in keys:
                x = THEO[t][part].get(k)
                if x:
                    for i in range(NG + 1): acc[i] += x[i]
        return [round(acc[0])] + [round(v / acc[0], 1) for v in acc[1:]] if acc[0] else None

    # ── 目前還抱著的權證（只看這段期間買賣相抵後還有剩的；更早買的不知道）──
    # 以最新收盤價、這檔權證最近一次的隱含波動率重新評價：估計損益、20 天內要漲多少才回本、股價不動幾天掉 3 成
    # 每個分點、每檔標的、認購／認售各一組加總：
    # 0 約當股數 1 Σ到期回本價 2 Σ剩餘天 3 最早到期 4 Σ履約價 5 剩餘天權重 6 成本 7 Σ損益% 8 Σ20 天需漲% 9 Σ不動撐幾天 10 Σ每天時間價值% 11 Σ槓桿 12 Σ機率 13 機率權重 14 評價權重
    HN = 15
    HOLD = {}
    for b, P in POS.items():
        for wc, (net, bu, bm) in P.items():
            m = WM.get(wc)
            if net <= 0 or not bu or not m or not m[2] or not m[3]: continue
            tg, cp, ratio, K, exp = m
            if exp and exp <= LAST: continue                      # 已經到期
            pc = bm / bu / ratio; eq = net * ratio; cost = net * bm / bu
            be = K + pc if cp == 'C' else K - pc
            dl = wdays(LAST, exp) if exp else None
            h = HOLD.setdefault(b, {}).setdefault(tg, {}).setdefault(cp, [0.0] * HN)
            if h[3] == 0.0: h[3] = None
            h[0] += eq; h[1] += be * eq; h[4] += K * eq; h[6] += cost
            if dl is not None:
                h[2] += dl * eq; h[5] += eq
                if h[3] is None or exp < h[3]: h[3] = exp
            S = close(tg, nd - 1); iv = IVL.get(wc)
            if S and iv and dl:
                T = dl / 245; Vn = bs(cp, S, K, T, iv)
                n20 = need_move(cp, S, K, T, iv, pc, 20)
                if Vn > 0 and n20 is not None:
                    h[14] += cost; h[7] += (Vn / pc - 1) * 100 * cost; h[8] += n20 * cost
                    h[9] += flat_days(cp, S, K, dl, iv, Vn) * cost
                    h[10] += (Vn - bs(cp, S, K, max(T - 1 / 245, 0), iv)) / Vn * 100 * cost
                    h[11] += abs(delta(cp, S, K, T, iv)) * S / Vn * cost
                    p = prob(cp, n20, hv(tg, LAST))
                    if p is not None: h[12] += p * cost; h[13] += cost
    def hside(h):
        """→ [約當股數, 到期回本價, 剩餘天, 最早到期, 履約價, 估計損益%, 20 天需漲%, 不動撐幾天, 每天時間價值%, 槓桿, 20 天達成機率%]"""
        if not h or h[0] <= 0: return [0, None, None, None, None, None, None, None, None, None, None]
        w = h[14]
        return [round(h[0]), round(h[1] / h[0], 2), round(h[2] / h[5]) if h[5] else None, h[3], round(h[4] / h[0], 2),
                round(h[7] / w, 1) if w else None, round(h[8] / w, 1) if w else None, round(h[9] / w) if w else None,
                round(h[10] / w, 2) if w else None, round(h[11] / w, 1) if w else None, round(h[12] / h[13]) if h[13] else None]
    def hout(hh): return hside(hh.get('C')) + hside(hh.get('P'))      # 認購 0～10、認售 11～21
    def hadd(tot, hh):
        for cp, h in hh.items():
            t_ = tot.setdefault(cp, [0.0] * HN)
            if t_[3] == 0.0: t_[3] = None
            for i in range(HN):
                if i == 3:
                    if h[3] and (t_[3] is None or h[3] < t_[3]): t_[3] = h[3]
                else: t_[i] += h[i]
    HO = {b: {tg: hout(hh) for tg, hh in T_.items() if max(x[0] for x in hh.values()) >= 1000} for b, T_ in HOLD.items()}

    # ── 權證隔日沖型分點：大買（≥ 50 萬）後隔天賣掉 ≥ 6 成 ──
    flip = {}
    for b, S in BT.items():
        for t in range(nd - 1):
            if t not in S or t + 1 not in FS: continue
            for tg, a in S[t].items():
                if a[0] < TH['ep']: continue
                f = flip.setdefault(b, [0, 0]); f[0] += 1
                nx = S.get(t + 1, {}).get(tg)
                if nx and nx[0] <= -0.6 * a[0]: f[1] += 1
    flipper = {b for b, (n, r) in flip.items() if n >= 4 and r / n * 100 >= TH['flip']}

    # ── 佈局事件與之後的結果（看權證本身，不是看股票）──
    # 一次佈局：單一分點一天買進同方向權證 ≥ 50 萬（同一檔同方向 5 天內只算一次）
    # 結果：買進後第 10 個交易日，這些權證的成交均價比買價漲了多少（買進金額加權）；漲 ≥ 30% 算「抓到行情」
    # 事前機率：用買進當天的隱含波動率、這檔平常的波動，估第 10 天權證漲 30% 的機率；實際勝率明顯高於事前機率，才代表他們真的比別人知道得多
    PXW = {}
    for t in F:
        for rows in DATA[D[t]]['w'].values():
            for wc, bq, sq, bm, sm in rows:
                if bq + sq: x = PXW.setdefault(wc, {}).setdefault(t, [0, 0.0]); x[0] += bq + sq; x[1] += bm + sm
    PXW = {wc: {t: m / q for t, (q, m) in v.items()} for wc, v in PXW.items()}
    H = TH['hor']; t0w5 = F[-5:][0]
    def wout(b, t, tg, sg):
        d = D[t]; rows = DATA[d]['w'].get(b, []); S = close(tg, t); hvv = hv(tg, d)
        tot = cov = rs = mx = ew = es = 0.0
        for wc, bq, sq, bm, sm in rows:
            m = WM.get(wc)
            if not m or m[0] != tg or not bq or (m[1] == 'C') != (sg > 0): continue
            p0 = bm / bq; tot += bm
            ser = PXW.get(wc, {})
            if t + H < nd:
                p1 = next((ser[k] for k in (t + H, t + H - 1, t + H + 1, t + H - 2, t + H + 2) if k in ser and t < k < nd), None)
                if p1:
                    cov += bm; rs += (p1 / p0 - 1) * bm
                    mx += (max(v for k, v in ser.items() if t < k <= t + H) / p0 - 1) * bm
            g = G.get(d, {}).get(wc)
            if g and S and m[2] and m[3] and m[4]:
                n_ = wdays(d, m[4]); T_ = n_ / 245
                nm = need_move(m[1], S, m[3], T_, g[0] / 100, p0 / m[2] * (1 + TH['hit'] / 100), H)
                pr = prob(m[1], nm, hvv, H) if nm is not None and n_ > H else None
                if pr is not None: ew += bm; es += pr * bm
        if not tot: return None
        ex = round(es / ew) if ew else None
        xp = wpath(b, t, tg, sg, rows, S)
        if t + H >= nd: return [None, None, None, ex, xp]
        if cov < 0.6 * tot: return [None, None, None, ex, xp]
        r = rs / cov * 100
        return [1 if r >= TH['hit'] else 0, round(r, 1), round(mx / cov * 100, 1), ex, xp]
    def wpath(b, t, tg, sg, rows, S):
        """實際路徑：買進後每天股票（往押的方向）漲幾 %、權證漲幾 %；第一次權證 +30／50／100% 是第幾天、當時股票漲幾 %"""
        ws = []; ow = od = wsum = 0.0
        for wc, bq, sq, bm, sm in rows:
            m = WM.get(wc)
            if not m or m[0] != tg or not bq or (m[1] == 'C') != (sg > 0): continue
            ws.append((wc, bm / bq, bm)); wsum += bm
            if S and m[3] and m[4]:
                ow += ((m[3] / S - 1) * 100 if sg > 0 else (1 - m[3] / S) * 100) * bm; od += wdays(D[t], m[4]) * bm
        if not ws or not S: return None
        path = {}
        for k in range(t + 1, min(nd, t + 21)):
            c = close(tg, k)
            if not c: continue
            cv = rv = 0.0
            for wc, p0, bm in ws:
                p1 = PXW.get(wc, {}).get(k)
                if p1: cv += bm; rv += (p1 / p0 - 1) * bm
            if cv >= 0.6 * wsum: path[k - t] = (round(sg * (c / S - 1) * 100, 2), round(rv / cv * 100, 1))
        hit = {}
        for tgt in (30, 50, 100):
            k = next((k for k in sorted(path) if path[k][1] >= tgt), None)
            hit[tgt] = [k, path[k][0]] if k else None
        return {'o': round(ow / wsum, 1), 'dl': round(od / wsum), 'p': path, 'h': hit, 'end': t + 20 < nd}
    EP = {}
    for b, Sb in BT.items():
        lastep = {}
        for t in sorted(Sb):
            for tg, a in Sb[t].items():
                for sg, money in ((1, a[5]), (-1, a[7])):
                    if money < TH['ep']: continue
                    if t - lastep.get((tg, sg), -99) < TH['ep_gap']: continue
                    o = wout(b, t, tg, sg)
                    if not o: continue
                    lastep[(tg, sg)] = t
                    EP.setdefault(b, []).append([t, tg, sg, round(money), round(a[3]), o[0], o[1], o[2], o[3], o[4]])
    # ── 每筆權證交易：分點自己的進出（從第一次買進到幾乎賣光算一筆），算真正的損益 ──
    # 賣出超過這段期間買進的部分（更早就有的庫存）不算。還沒賣完的，用最近的成交均價估。
    def lastpx(wc):
        ser = PXW.get(wc, {})
        for k in range(nd - 1, max(-1, nd - 4), -1):
            if k in ser: return ser[k]
        return None
    TR = {}                     # 分點 → 交易列表
    for b in BT:
        if KD.get(b, 'b') != 'b': continue
        st_ = {}
        for t in F:
            for wc, bq, sq, bm, sm in DATA[D[t]]['w'].get(b, []):
                m = WM.get(wc)
                if not m: continue
                x = st_.get(wc)
                if bq:
                    if x is None or x['pos'] <= 0.05 * x['mx']:
                        if x is not None and x['bu']: TR.setdefault(b, []).append(x)
                        S0 = close(m[0], t); gg = G.get(D[t], {}).get(wc)
                        x = st_[wc] = {'wc': wc, 't0': t, 't1': None, 'pos': 0, 'mx': 0, 'cost': 0.0, 'bu': 0, 'bm': 0.0, 'su': 0, 'sm': 0.0, 'pnl': 0.0,
                                       'otm': ((m[3] / S0 - 1) * 100 if m[1] == 'C' else (1 - m[3] / S0) * 100) if S0 and m[3] else None,
                                       'dl': wdays(D[t], m[4]) if m[4] else None, 'iv': gg[0] if gg else None, 'S0': S0}
                    x['pos'] += bq; x['cost'] += bm; x['bu'] += bq; x['bm'] += bm; x['mx'] = max(x['mx'], x['pos'])
                if sq and x is not None and x['pos'] > 0:
                    u = min(sq, x['pos']); ac = x['cost'] / x['pos']; px_ = sm / sq
                    x['pnl'] += (px_ - ac) * u; x['cost'] -= ac * u; x['pos'] -= u; x['su'] += u; x['sm'] += px_ * u
                    if x['pos'] <= 0.05 * x['mx']: x['t1'] = t
        for x in st_.values():
            if x['bu']: TR.setdefault(b, []).append(x)
    for b, L in TR.items():
        for x in L:
            lp = lastpx(x['wc']) if x['t1'] is None else None
            x['unr'] = (lp * x['pos'] - x['cost']) if lp and x['pos'] > 0 else 0.0
            x['lp'] = lp
            x['ret'] = (x['pnl'] + x['unr']) / x['bm'] * 100 if x['bm'] else 0
            x['age'] = (x['t1'] if x['t1'] is not None else nd - 1) - x['t0']
    # 深價外（價外 ≥ 15%）交易的分點勝率：已出場的，或還沒出場但已滿 10 天（用現價估）
    # 當天買當天賣光的（當沖）不算佈局，不列入
    def judged(x): return x['bm'] >= TH['tr_min'] and x['t1'] != x['t0'] and (x['t1'] is not None or x['age'] >= TH['hor'])
    DP = {}
    for b, L in TR.items():
        if b in pro: continue
        dl_ = [x for x in L if judged(x) and x['otm'] is not None and x['otm'] >= TH['otm']]
        al_ = [x for x in L if judged(x)]
        def sm_(z):
            if not z: return None
            w = sum(1 for x in z if x['ret'] > 0); n = len(z)
            return [n, w, round(w / n * 100), round(sum(x['pnl'] + x['unr'] for x in z) / sum(x['bm'] for x in z) * 100, 1),
                    round(sum(x['pnl'] + x['unr'] for x in z)), round(ST.median(x['age'] for x in z)), round((w + 1) / (n + 2) * 100, 1), sum(1 for x in z if x['ret'] >= TH['hit'])]
        DP[b] = {'d': sm_(dl_), 'a': sm_(al_)}
    alld = [x for b, L in TR.items() if b not in pro for x in L if judged(x) and x['otm'] is not None and x['otm'] >= TH['otm']]
    dp_base = round(sum(1 for x in alld if x['ret'] > 0) / len(alld) * 100, 1) if alld else None
    dp_rank = sorted([b for b, v in DP.items() if v['d'] and v['d'][0] >= TH['dp_min']], key=lambda b: (-DP[b]['d'][6], -DP[b]['d'][3]))[:60]
    def trow(b, x):
        m = WM[x['wc']]
        return [b, x['wc'], m[0], m[1], m[3], m[4], x['t0'], x['t1'], round(x['bm']), round(x['bu'] / 1000), round(x['bm'] / x['bu'], 3) if x['bu'] else None,
                round(x['su'] / 1000), round(x['sm'] / x['su'], 3) if x['su'] else None, round(x['pnl'] + x['unr']), round(x['ret'], 1),
                round(x['otm'], 1) if x['otm'] is not None else None, x['dl'], x['iv'], round(x['lp'], 3) if x['lp'] else None, round(x['pos'] / 1000)]
    # 最近 5 個交易日新進場的權證（≥ 30 萬），附上這個分點過去深價外交易的成績
    NW = []
    for b, L in TR.items():
        if b in pro: continue
        for x in L:
            if x['t0'] >= t0w5 and x['bm'] >= TH['tr_min'] and x['t1'] != x['t0']:
                NW.append(trow(b, x))
    NW.sort(key=lambda r: -(r[8] * (1 + (DP.get(r[0], {}).get('d') or [0] * 7)[6] / 50)))
    done = [e for L in EP.values() for e in L if e[5] is not None]
    base = round(sum(e[5] for e in done) / len(done) * 100, 1) if done else None
    base_r = round(ST.mean(e[6] for e in done), 2) if done else None
    ee = [e[8] for e in done if e[8] is not None]
    base_e = round(ST.mean(ee), 1) if ee else None
    stats = {}
    for b, L in EP.items():
        f = [e for e in L if e[5] is not None]
        if not f: continue
        w = sum(e[5] for e in f); n = len(f); fe = [e[8] for e in f if e[8] is not None]
        # [次數, 抓到次數, 勝率%, 平均 10 日權證報酬%, 調整後勝率%, 全部佈局次數, 事前機率平均%]
        stats[b] = [n, w, round(w / n * 100), round(ST.mean(e[6] for e in f), 1), round((w + 1) / (n + 2) * 100, 1), len(L), round(ST.mean(fe)) if fe else None]
    # 前後兩段是否一致（勝率有沒有延續性）
    persist = None
    if len(F) >= 30:
        mid = F[len(F) // 2]; a1 = {}; a2 = {}
        for b, L in EP.items():
            for e in L:
                if e[5] is None: continue
                (a1 if e[0] < mid else a2).setdefault(b, []).append(e[5])
        both = [b for b in a1 if len(a1[b]) >= 3 and b in a2 and len(a2[b]) >= 3]
        if len(both) >= 10:
            r1 = sorted(both, key=lambda b: -sum(a1[b]) / len(a1[b]))
            top = r1[:max(3, len(r1) // 4)]; rest = r1[len(top):]
            wr = lambda bl: round(sum(sum(a2[b]) for b in bl) / sum(len(a2[b]) for b in bl) * 100, 1)
            persist = {'n': len(both), 'top': wr(top), 'rest': wr(rest), 'ntop': len(top)}
    good = sorted([b for b, s in stats.items() if s[0] >= TH['min_n'] and s[3] > 0 and b not in flipper and b not in pro and len(F) >= TH['good_days'] and s[4] >= (base or 50) + 5], key=lambda b: (-stats[b][4], -stats[b][3]))[:40]

    # ── 各標的 1／5／20 日（一般分點，不含權證專業戶）──
    def wsum(lst):
        g = [0.0] * NF
        for a in lst:
            for i in range(NF): g[i] += a[i]
        return g
    SH = {}
    for t in F:
        d = D[t]
        for b, rows in DATA[d]['w'].items():
            if KD.get(b, 'b') != 'b' or b in pro: continue
            for wc, bq, sq, bm, sm in rows:
                m = WM.get(wc)
                if m and bm and m[4] and wdays(d, m[4]) <= TH['short']:
                    x = SH.setdefault((m[0], t), [0.0, 0.0]); x[m[1] == 'P'] += bm
    summ = {}
    for tg, S in U.items():
        o = {}
        for K in (1, 5, 20):
            win = F[-K:]; agg = {}
            for t in win:
                for b, a in S.get(t, {}).items():
                    if b in pro: continue
                    g = agg.setdefault(b, [0.0] * NF)
                    for i in range(NF): g[i] += a[i]
            if not agg: continue
            vol = sum(px(tg, t)[1] for t in win) * 1000
            g = wsum(agg.values()); A = avg(g)
            nb = sum(1 for a in agg.values() if a[0] > 0); ns = sum(1 for a in agg.values() if a[0] < 0)
            top = sorted(agg.items(), key=lambda kv: -kv[1][0])[:3]
            bot = sorted(agg.items(), key=lambda kv: kv[1][0])[:2]
            ft = sum(FT.get(tg, {}).get(t, [0, 0])[0] for t in win)
            # 短天期：買進的權證剩 ≤ 30 個交易日到期，佔買進金額的比例
            sh = [sum(SH.get((tg, t), (0, 0))[i] for t in win) for i in (0, 1)]
            o[K] = [round(g[0]), round(g[1]), round(g[2]), round(g[3]), round(g[3] / vol * 100, 2) if vol else None, nb, ns,
                    [[b, round(a[0])] for b, a in top if a[0] > 0], [[b, round(a[0])] for b, a in bot if a[0] < 0], round(ft),
                    A[9], A[10], A[11], round(sh[0] / g[5] * 100) if g[5] else None,
                    A[13], A[14], A[15], round(sh[1] / g[7] * 100) if g[7] else None] + A[16:28]
        if o: summ[tg] = {'c': close(tg, nd - 1), 'r1': None, 'k': o, 'hv': round(hv(tg, LAST) * 100, 1) if hv(tg, LAST) else None}
        c1, c0 = close(tg, nd - 1), close(tg, nd - 2) if nd >= 2 else None
        if tg in summ and c1 and c0: summ[tg]['r1'] = round((c1 / c0 - 1) * 100, 2)
        if tg in summ and nd >= 6 and close(tg, nd - 6) and c1: summ[tg]['r5'] = round((c1 / close(tg, nd - 6) - 1) * 100, 2)

    # ── 疑似知情佈局（最近 5 個完整交易日） ──
    AL = []
    W5 = F[-5:]; t0 = W5[0]
    for b, S in BT.items():
        if b in pro: continue
        tot = {}; gross_all = 0
        for t in W5:
            for tg, a in S.get(t, {}).items():
                g = tot.setdefault(tg, [[0.0] * NF, []])
                for i in range(NF): g[0][i] += a[i]
                gross_all += a[4]
                if a[0]: g[1].append(t)
        for tg, (g, ts) in tot.items():
            bull, eq, gross = g[0], g[3], g[4]
            if abs(bull) < TH['ep']: continue
            vol = sum(px(tg, t)[1] for t in W5) * 1000
            eqp = abs(eq) / vol * 100 if vol else 0
            A = avg(g); o = 9 if bull > 0 else 13          # 看多看認購買進、看空看認售買進
            be, dl, otm = A[o], A[o + 1], A[o + 2]
            q = 16 if bull > 0 else 22
            iv, th, n20, fl, p20 = A[q], A[q + 2], A[q + 3], A[q + 4], A[q + 5]
            tags = []
            if abs(bull) >= TH['big']: tags.append('大額')
            if eqp >= TH['eqp']: tags.append('約當佔量高')
            if gross_all and gross / gross_all * 100 >= TH['focus']: tags.append('集中押注')
            prev = [t for t in F if t0 - TH['new_look'] <= t < t0]
            if len(prev) >= 5 and all(tg not in S.get(t, {}) for t in prev): tags.append('新進場')
            if len(set(ts)) >= 3: tags.append('連續買' if bull > 0 else '連續賣')
            if not ({'大額', '約當佔量高'} & set(tags)) or len(tags) < 2: continue
            if dl is not None and dl <= TH['short']: tags.append('短天期')
            if otm is not None and otm >= TH['otm']: tags.append('深價外')
            if p20 is not None and p20 < TH['hard']: tags.append('低勝算重押')
            if b in flipper: tags.append('隔日沖型')
            s_ = stats.get(b)
            AL.append([b, tg, round(bull), round(eq), round(eqp, 2), tags, sorted(set(ts))[0], s_[2] if s_ and s_[0] >= 3 else None, s_[0] if s_ else 0, be, dl, otm, n20, p20, iv, th, fl])
    AL.sort(key=lambda r: -(abs(r[2]) * (1 + 0.5 * ('新進場' in r[5]) + 0.5 * ('集中押注' in r[5]) + 0.3 * ('短天期' in r[5]) + 0.3 * ('低勝算重押' in r[5])) * (0.4 if '隔日沖型' in r[5] else 1)))

    # 目前（最近 5 天）高勝率分點的佈局
    hold = {}
    for b in good:
        S = BT.get(b, {}); agg = {}
        for t in W5:
            for tg, a in S.get(t, {}).items(): agg[tg] = agg.get(tg, 0) + a[0]
        hold[b] = [[tg, round(v)] for tg, v in sorted(agg.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 100000][:5]

    used = set(good) | {r[0] for r in AL} | set(dp_rank) | {r[0] for r in NW[:400]} | {x[0] for s in summ.values() for k in s['k'].values() for x in k[7] + k[8]}
    # ── 獲利地圖：股票要漲多少、幾天，權證分點才賺得到錢 ──
    W20 = F[-20:]
    bk = lambda cp: {k: theo([cp + '|' + k], 'b', W20) for k in [o + d_ for o in 'ABC' for d_ in '123'] + ['all']}
    emp = {'n': 0, 'grid': {}, 'hit': {}, 'hitb': {}}
    eps = [e for b, L in EP.items() if b not in pro for e in L if e[9] and e[9]['p']]
    emp['n'] = len(eps)
    SB = [-5, 0, 5, 10, 15, 20]          # 股票漲幅分組的界線
    def sbin(x): return sum(1 for v in SB if x > v)
    for h in (3, 5, 10, 20):
        cells = {}
        for e in eps:
            pt = e[9]['p'].get(h)
            if pt: cells.setdefault(sbin(pt[0]), []).append(pt[1])
        emp['grid'][h] = {i: [round(ST.median(v), 1), len(v), round(sum(1 for x in v if x >= TH['hit']) / len(v) * 100)] for i, v in cells.items()}
    def hstat(lst, tgt):
        full = [e for e in lst if e[9]['end']]
        got = [e[9]['h'][tgt] for e in full if e[9]['h'].get(tgt)]
        if len(full) < 5: return None
        r = [len(full), round(len(got) / len(full) * 100)]
        if len(got) >= 3:
            dd = sorted(g[0] for g in got); mm = sorted(g[1] for g in got)
            q = lambda a, p: a[min(len(a) - 1, int(len(a) * p))]
            r += [ST.median(dd), q(dd, .25), q(dd, .75), round(ST.median(mm), 1), round(q(mm, .25), 1), round(q(mm, .75), 1)]
        return r
    for tgt in (30, 50, 100):
        emp['hit'][tgt] = hstat(eps, tgt)
        for k in [o + d_ for o in 'ABC' for d_ in '123']:
            sub = [e for e in eps if (('A' if e[9]['o'] <= 5 else 'B' if e[9]['o'] <= 15 else 'C') + ('1' if e[9]['dl'] <= 60 else '2' if e[9]['dl'] <= 120 else '3')) == k]
            st_ = hstat(sub, tgt)
            if st_: emp['hitb'].setdefault(k, {})[tgt] = st_
    PMAP = {'pm': PM, 'ph': PH, 'sb': SB, 'win': [D[W20[0]], D[W20[-1]]], 'C': bk('C'), 'P': bk('P'), 'emp': emp}

    out = {'date': D[-1], 'days': D, 'fd': [D[t] for t in F], 'baseE': base_e, 'pro': sorted(b for b in pro if b in used),
           'th': TH, 'base': base, 'baseR': base_r, 'nep': len(done), 'persist': persist,
           'n': {b: names.get(b, b) for b in used}, 'u': summ, 'al': AL[:300],
           'good': [[b] + stats[b] + [hold.get(b, [])] for b in good], 'flip': sorted(b for b in flipper if b in used), 'pmap': PMAP,
           'dp': [[b, names.get(b, b), DP[b]['d'], DP[b]['a']] for b in dp_rank], 'dpBase': dp_base, 'nw': NW[:400],
           'dpr': {r[0]: DP[r[0]]['d'] for r in NW[:400] if DP.get(r[0], {}).get('d')}}
    with open(os.path.join(OUT, 'wt.json'), 'w') as f: json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

    # ── 明細檔 ──
    for sub in ('wu', 'wb'):
        shutil.rmtree(os.path.join(OUT, sub), ignore_errors=True); os.makedirs(os.path.join(OUT, sub))
    TRU = {}
    for b, L in TR.items():
        for x in L:
            if x['t0'] >= W20[0] and x['bm'] >= 200000 and x['t1'] != x['t0']: TRU.setdefault(WM[x['wc']][0], []).append(trow(b, x))
    for v in TRU.values(): v.sort(key=lambda r: (-r[6], -r[8]))
    for tg, S in U.items():
        r = {}; nm = {}; tot = {}
        for t, bmap in S.items():
            for b, a in bmap.items():
                if b not in pro:
                    g = tot.setdefault(t, [0.0] * NF)
                    for i in range(NF): g[i] += a[i]
                if abs(a[0]) < 50000 and a[4] < 200000: continue
                r.setdefault(b, []).append([t] + avg(a)); nm[b] = names.get(b, b)
        ho = {b: HO[b][tg] for b in HO if tg in HO[b] and KD.get(b, 'b') == 'b'}
        for b in ho: nm.setdefault(b, names.get(b, b))
        for r_ in TRU.get(tg, [])[:80]: nm.setdefault(r_[0], names.get(r_[0], r_[0]))
        HT = {}
        for b in ho:
            if b not in pro: hadd(HT, HOLD[b][tg])
        o = {'d': D, 'c': [close(tg, t) for t in range(nd)], 'v': [px(tg, t)[1] for t in range(nd)],
             'f': [round(FT.get(tg, {}).get(t, [0, 0])[0]) for t in range(nd)], 'n': nm,
             'st': {b: stats[b][:4] + [stats[b][6]] for b in nm if b in stats}, 'flip': [b for b in nm if b in flipper], 'pro': [b for b in nm if b in pro], 'full': F,
             'r': r, 'T': {t: avg(g) for t, g in tot.items()}, 'h': ho, 'H': hout(HT) if HT else None, 'th': {cp: theo([tg + '|' + cp], 'u', W20) for cp in 'CP'}, 'tr': TRU.get(tg, [])[:80], 'hv': round(hv(tg, LAST) * 100, 1) if hv(tg, LAST) else None}
        with open(os.path.join(OUT, 'wu', tg + '.json'), 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
    for b, S in BT.items():
        rows = [[t, tg, round(a[0]), round(a[1]), round(a[2]), round(a[3])] for t, tgs in S.items() for tg, a in tgs.items() if abs(a[0]) >= 50000 or a[4] >= 200000]
        if not rows and b not in EP and b not in TR: continue
        tgs = {x[1] for x in rows} | {e[1] for e in EP.get(b, [])} | set(HO.get(b, {})) | {WM[x['wc']][0] for x in TR.get(b, []) if x['bm'] >= 100000}
        o = {'d': D, 'id': b, 'n': names.get(b, b), 'st': stats.get(b), 'flip': b in flipper, 'pro': b in pro, 'nt': round(ST.mean(NT[b])) if b in NT else None,
             'fl': flip.get(b), 'ep': [e[:9] for e in EP.get(b, [])], 'tr': [trow(b, x) for x in sorted(TR.get(b, []), key=lambda x: -x['t0']) if x['bm'] >= 100000][:150], 'dp': DP.get(b), 'r': rows, 'h': HO.get(b, {}), 'full': F,
             'c': {tg: [close(tg, t) for t in range(nd)] for tg in tgs}}
        with open(os.path.join(OUT, 'wb', safe(b) + '.json'), 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
    log('權證交易', sum(len(L) for L in TR.values()), '筆，深價外排名', len(dp_rank), '，深價外基準勝率', dp_base, '，最近新進場', len(NW))
    log('權證整理：完整', len(F), '天（時間軸', nd, '天），權證專業戶', len(pro), '，標的', len(summ), '，佈局事件', len(done), '基準勝率', base, '，高勝率分點', len(good), '，疑似知情', len(AL), '，延續性', persist)
