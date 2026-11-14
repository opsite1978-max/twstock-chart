"""權證佈局整理：從 d/日期.json（每個分點每天在每檔標的的權證進出）做出網頁要的檔案

  wt.json        各標的 1／5／20 日被佈局多少、回本股價、剩餘天數、疑似知情佈局、高勝率波段權證分點、門檻
  wu/代號.json   這檔標的每天每個分點的權證買賣、回本股價、剩餘天數，和目前還抱著的權證（畫圖、推演能放多久）
  wb/分點.json   這個分點的權證佈局紀錄、每次之後標的漲跌、勝率、目前還抱著的權證

回本股價（損益兩平）：認購＝履約價 ＋ 權證買價 ÷ 行使比例；認售＝履約價 − 權證買價 ÷ 行使比例。
剩餘天數：從買進那天到權證最後交易日的平日天數（約等於交易日）。

看多金額＝認購淨買 − 認售淨買（買認售是看空）；約當股數＝權證單位 × 行使比例。
外資券商、券商總公司（代號最後一碼是 0，多半是自營、造市或法人帳戶）不列入佈局分析，只顯示合計。
"""
import json, os, re, shutil, statistics as ST

TH = {
    'ep': 500000,          # 一次「佈局」：單一分點一天在一檔標的看多（或看空）淨額 ≥ 50 萬元
    'ep_gap': 5,           # 同一分點同一檔同方向 5 天內只算一次
    'hor': 10, 'up': 5.0, 'dn': 5.0,   # 之後 10 個交易日：先漲 5% 算贏、先跌 5% 算輸，都沒碰到看第 10 天漲跌
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


# 一筆（分點、標的、日）的欄位（內部用加總，輸出時再換成平均）
# 0 看多淨額 1 認購淨 2 認售淨 3 約當股數淨 4 進出總額 5 認購買 6 認購賣 7 認售買 8 認售賣
# 9 認購買進約當股數 10 Σ回本價×約當股數 11 Σ剩餘天數×金額 12 Σ價外%×金額 13 認售買進約當股數 14 15 16 同左（認售）
NF = 17


def addw(a, cp, ratio, K, exp, d, S, bq, sq, bm, sm):
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
    a[0] = a[1] - a[2]; a[4] += bm + sm


def avg(a):
    """內部加總 → 輸出：[看多, 認購淨, 認售淨, 約當淨, 認購買, 認購賣, 認售買, 認售賣, 認購買約當, 認購回本價, 認購剩餘天, 認購價外%, 認售買約當, 認售回本價, 認售剩餘天, 認售價外%]"""
    r = lambda v, n=0: round(v, n) if n else round(v)
    return [r(a[0]), r(a[1]), r(a[2]), r(a[3]), r(a[5]), r(a[6]), r(a[7]), r(a[8]),
            r(a[9]), r(a[10] / a[9], 2) if a[9] else None, r(a[11] / a[5]) if a[5] and a[11] else None, r(a[12] / a[5], 1) if a[5] and a[12] else None,
            r(a[13]), r(a[14] / a[13], 2) if a[13] else None, r(a[15] / a[7]) if a[7] and a[15] else None, r(a[16] / a[7], 1) if a[7] and a[16] else None]


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
    nd = len(D); last = D[-1]
    F = [t for t, d in enumerate(D) if d in DATA and isfull(DATA[d])]
    FS = set(F)
    KD = {b: kind(b, names.get(b, b)) for b in names}
    U = {}; BT = {}; FT = {}; NT = {}; POS = {}
    for t in F:
        d = D[t]; V = DATA[d]['v']
        for b, rows in DATA[d]['w'].items():
            k = KD.get(b, 'b'); per = {}
            for wc, bq, sq, bm, sm in rows:
                m = WM.get(wc)
                if not m: continue
                tg, cp, ratio, K, exp = m
                S = (V.get(tg) or [None])[0]
                addw(per.setdefault(tg, [0.0] * NF), cp, ratio, K, exp, d, S, bq, sq, bm, sm)
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

    # ── 目前還抱著的權證（只看這段期間買賣相抵後還有剩的；更早買的不知道）──
    # 每個分點、每檔標的：[認購約當股數, 認購回本價, 認購剩餘天數, 認購最早到期, 認售…]（剩餘天數以約當股數加權）
    HOLD = {}
    for b, P in POS.items():
        for wc, (net, bu, bm) in P.items():
            m = WM.get(wc)
            if net <= 0 or not bu or not m or not m[2] or not m[3]: continue
            tg, cp, ratio, K, exp = m
            if exp and exp < last: continue                      # 已經到期
            eq = net * ratio; be = K + bm / bu / ratio if cp == 'C' else K - bm / bu / ratio
            dl = wdays(last, exp) if exp else None
            h = HOLD.setdefault(b, {}).setdefault(tg, [0.0, 0.0, 0.0, None, 0.0, 0.0, 0.0, None, 0.0, 0.0, 0.0, 0.0])
            o = 0 if cp == 'C' else 4
            h[o] += eq; h[o + 1] += be * eq; h[10 + (o > 0)] += K * eq
            if dl is not None:
                h[o + 2] += dl * eq; h[8 + (o > 0)] += eq
                if h[o + 3] is None or exp < h[o + 3]: h[o + 3] = exp
    def hout(h):
        r = []
        for o in (0, 4):
            w = h[8 + (o > 0)]
            r += [round(h[o]), round(h[o + 1] / h[o], 2) if h[o] else None, round(h[o + 2] / w) if w else None, h[o + 3], round(h[10 + (o > 0)] / h[o], 2) if h[o] else None]
        return r
    HO = {b: {tg: hout(h) for tg, h in T_.items() if h[0] >= 1000 or h[4] >= 1000} for b, T_ in HOLD.items()}   # [認購 約當, 回本價, 剩餘天, 最早到期, 履約價, 認售 同左]

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

    # ── 佈局事件與之後的結果 ──
    def outcome(tg, t, sgn):
        c0 = close(tg, t)
        if not c0: return None
        for j in range(t + 1, min(nd, t + TH['hor'] + 1)):
            c = close(tg, j)
            if not c: continue
            r = (c / c0 - 1) * 100 * sgn
            if r >= TH['up']: return (1, r, j - t)
            if r <= -TH['dn']: return (0, r, j - t)
        if t + TH['hor'] >= nd: return None            # 還沒滿 10 天
        c = close(tg, t + TH['hor'])
        if not c: return None
        r = (c / c0 - 1) * 100 * sgn
        return (1 if r > 0 else 0, r, TH['hor'])
    EP = {}
    for b, S in BT.items():
        last = {}
        for t in sorted(S):
            for tg, a in S[t].items():
                if abs(a[0]) < TH['ep']: continue
                sg = 1 if a[0] > 0 else -1
                if t - last.get((tg, sg), -99) < TH['ep_gap']: continue
                last[(tg, sg)] = t
                o = outcome(tg, t, sg)
                EP.setdefault(b, []).append([t, tg, sg, round(a[0]), round(a[3]), o[0] if o else None, round(o[1], 2) if o else None, o[2] if o else None])
    done = [e for L in EP.values() for e in L if e[5] is not None]
    base = round(sum(e[5] for e in done) / len(done) * 100, 1) if done else None
    base_r = round(ST.mean(e[6] for e in done), 2) if done else None
    stats = {}
    for b, L in EP.items():
        f = [e for e in L if e[5] is not None]
        if not f: continue
        w = sum(e[5] for e in f); n = len(f)
        stats[b] = [n, w, round(w / n * 100), round(ST.mean(e[6] for e in f), 2), round((w + 1) / (n + 2) * 100, 1), len(L)]
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
            wr = lambda bs: round(sum(sum(a2[b]) for b in bs) / sum(len(a2[b]) for b in bs) * 100, 1)
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
                    A[13], A[14], A[15], round(sh[1] / g[7] * 100) if g[7] else None]
        if o: summ[tg] = {'c': close(tg, nd - 1), 'r1': None, 'k': o}
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
            if b in flipper: tags.append('隔日沖型')
            s_ = stats.get(b)
            AL.append([b, tg, round(bull), round(eq), round(eqp, 2), tags, sorted(set(ts))[0], s_[2] if s_ and s_[0] >= 3 else None, s_[0] if s_ else 0, be, dl, otm])
    AL.sort(key=lambda r: -(abs(r[2]) * (1 + 0.5 * ('新進場' in r[5]) + 0.5 * ('集中押注' in r[5]) + 0.3 * ('短天期' in r[5])) * (0.4 if '隔日沖型' in r[5] else 1)))

    # 目前（最近 5 天）高勝率分點的佈局
    hold = {}
    for b in good:
        S = BT.get(b, {}); agg = {}
        for t in W5:
            for tg, a in S.get(t, {}).items(): agg[tg] = agg.get(tg, 0) + a[0]
        hold[b] = [[tg, round(v)] for tg, v in sorted(agg.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 100000][:5]

    used = set(good) | {r[0] for r in AL} | {x[0] for s in summ.values() for k in s['k'].values() for x in k[7] + k[8]}
    out = {'date': D[-1], 'days': D, 'fd': [D[t] for t in F], 'pro': sorted(b for b in pro if b in used),
           'th': TH, 'base': base, 'baseR': base_r, 'nep': len(done), 'persist': persist,
           'n': {b: names.get(b, b) for b in used}, 'u': summ, 'al': AL[:300],
           'good': [[b] + stats[b] + [hold.get(b, [])] for b in good], 'flip': sorted(b for b in flipper if b in used)}
    with open(os.path.join(OUT, 'wt.json'), 'w') as f: json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

    # ── 明細檔 ──
    for sub in ('wu', 'wb'):
        shutil.rmtree(os.path.join(OUT, sub), ignore_errors=True); os.makedirs(os.path.join(OUT, sub))
    for tg, S in U.items():
        r = {}; nm = {}; tot = {}
        for t, bs in S.items():
            for b, a in bs.items():
                if b not in pro:
                    g = tot.setdefault(t, [0.0] * NF)
                    for i in range(NF): g[i] += a[i]
                if abs(a[0]) < 50000 and a[4] < 200000: continue
                r.setdefault(b, []).append([t] + avg(a)); nm[b] = names.get(b, b)
        ho = {b: HO[b][tg] for b in HO if tg in HO[b] and KD.get(b, 'b') == 'b'}
        for b in ho: nm.setdefault(b, names.get(b, b))
        HT = [0.0, 0.0, 0.0, None, 0.0, 0.0, 0.0, None, 0.0, 0.0, 0.0, 0.0]
        for b, H in ((b, HOLD[b][tg]) for b in ho if b not in pro):
            for i in (0, 1, 2, 4, 5, 6, 8, 9, 10, 11): HT[i] += H[i]
            for i in (3, 7):
                if H[i] and (not HT[i] or H[i] < HT[i]): HT[i] = H[i]
        o = {'d': D, 'c': [close(tg, t) for t in range(nd)], 'v': [px(tg, t)[1] for t in range(nd)],
             'f': [round(FT.get(tg, {}).get(t, [0, 0])[0]) for t in range(nd)], 'n': nm,
             'st': {b: stats[b][:4] for b in nm if b in stats}, 'flip': [b for b in nm if b in flipper], 'pro': [b for b in nm if b in pro], 'full': F,
             'r': r, 'T': {t: avg(g) for t, g in tot.items()}, 'h': ho, 'H': hout(HT) if HT[0] or HT[4] else None}
        with open(os.path.join(OUT, 'wu', tg + '.json'), 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
    for b, S in BT.items():
        rows = [[t, tg, round(a[0]), round(a[1]), round(a[2]), round(a[3])] for t, tgs in S.items() for tg, a in tgs.items() if abs(a[0]) >= 50000 or a[4] >= 200000]
        if not rows and b not in EP: continue
        tgs = {x[1] for x in rows} | {e[1] for e in EP.get(b, [])} | set(HO.get(b, {}))
        o = {'d': D, 'id': b, 'n': names.get(b, b), 'st': stats.get(b), 'flip': b in flipper, 'pro': b in pro, 'nt': round(ST.mean(NT[b])) if b in NT else None,
             'fl': flip.get(b), 'ep': EP.get(b, []), 'r': rows, 'h': HO.get(b, {}), 'full': F,
             'c': {tg: [close(tg, t) for t in range(nd)] for tg in tgs}}
        with open(os.path.join(OUT, 'wb', safe(b) + '.json'), 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
    log('權證整理：完整', len(F), '天（時間軸', nd, '天），權證專業戶', len(pro), '，標的', len(summ), '，佈局事件', len(done), '基準勝率', base, '，高勝率分點', len(good), '，疑似知情', len(AL), '，延續性', persist)
