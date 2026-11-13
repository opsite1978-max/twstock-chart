"""權證佈局整理：從 d/日期.json（每個分點每天在每檔標的的權證進出）做出網頁要的檔案

  wt.json        各標的 1／5／20 日被佈局多少、疑似知情佈局、高勝率波段權證分點、門檻
  wu/代號.json   這檔標的每天每個分點的權證淨買（畫「佈局時間 × 買賣量」圖）
  wb/分點.json   這個分點的權證佈局紀錄、每次之後標的漲跌、勝率

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


def build(OUT, days, meta, log):
    names = meta.get('traders', {})
    D = []; DATA = {}
    for d in days:
        x = jl(os.path.join(OUT, 'd', d + '.json'))
        if x and x.get('full') and x.get('b') is not None and x.get('v'): D.append(d); DATA[d] = x
    if not D: log('權證整理：還沒有完整的一天'); return
    nd = len(D)
    KD = {b: kind(b, names.get(b, b)) for b in names}
    # 每檔標的每天：{分點: [看多金額, 認購淨, 認售淨, 約當股數, 進出總額]}
    U = {}; BT = {}; FT = {}
    for t, d in enumerate(D):
        for b, tgs in DATA[d]['b'].items():
            k = KD.get(b, 'b')
            for tg, a in tgs.items():
                cn = a[0] - a[1]; pn = a[2] - a[3]; bull = cn - pn; eq = a[4] - a[5]; gross = a[0] + a[1] + a[2] + a[3]
                if k != 'b':
                    f = FT.setdefault(tg, {}).setdefault(t, [0, 0]); f[0] += bull; f[1] += eq; continue
                U.setdefault(tg, {}).setdefault(t, {})[b] = [bull, cn, pn, eq, gross]
                BT.setdefault(b, {}).setdefault(t, {})[tg] = [bull, cn, pn, eq, gross]
    px = lambda tg, t: (DATA[D[t]]['v'].get(tg) or [None, 0, 0])
    close = lambda tg, t: px(tg, t)[0]

    # ── 權證隔日沖型分點：大買（≥ 50 萬）後隔天賣掉 ≥ 6 成 ──
    flip = {}
    for b, S in BT.items():
        for t in range(nd - 1):
            if t not in S: continue
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
    if nd >= 30:
        mid = nd // 2; a1 = {}; a2 = {}
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
    good = sorted([b for b, s in stats.items() if s[0] >= TH['min_n'] and s[3] > 0 and b not in flipper and s[4] >= (base or 50) + 5], key=lambda b: (-stats[b][4], -stats[b][3]))[:40]

    # ── 各標的 1／5／20 日 ──
    summ = {}
    for tg, S in U.items():
        o = {}
        for K in (1, 5, 20):
            t0 = max(0, nd - K); agg = {}
            for t in range(t0, nd):
                for b, a in S.get(t, {}).items():
                    g = agg.setdefault(b, [0, 0, 0, 0, 0])
                    for i in range(5): g[i] += a[i]
            if not agg: continue
            vol = sum(px(tg, t)[1] for t in range(t0, nd)) * 1000
            bull = sum(a[0] for a in agg.values()); cn = sum(a[1] for a in agg.values()); pn = sum(a[2] for a in agg.values()); eq = sum(a[3] for a in agg.values())
            nb = sum(1 for a in agg.values() if a[0] > 0); ns = sum(1 for a in agg.values() if a[0] < 0)
            top = sorted(agg.items(), key=lambda kv: -kv[1][0])[:3]
            bot = sorted(agg.items(), key=lambda kv: kv[1][0])[:2]
            ft = sum(FT.get(tg, {}).get(t, [0, 0])[0] for t in range(t0, nd))
            o[K] = [round(bull), round(cn), round(pn), round(eq), round(eq / vol * 100, 2) if vol else None, nb, ns,
                    [[b, round(a[0])] for b, a in top if a[0] > 0], [[b, round(a[0])] for b, a in bot if a[0] < 0], round(ft)]
        if o: summ[tg] = {'c': close(tg, nd - 1), 'r1': None, 'k': o}
        c1, c0 = close(tg, nd - 1), close(tg, nd - 2) if nd >= 2 else None
        if tg in summ and c1 and c0: summ[tg]['r1'] = round((c1 / c0 - 1) * 100, 2)
        if tg in summ and nd >= 6 and close(tg, nd - 6) and c1: summ[tg]['r5'] = round((c1 / close(tg, nd - 6) - 1) * 100, 2)

    # ── 疑似知情佈局（最近 5 天） ──
    AL = []
    t0 = max(0, nd - 5)
    for b, S in BT.items():
        tot = {}; gross_all = 0
        for t in range(t0, nd):
            for tg, a in S.get(t, {}).items():
                g = tot.setdefault(tg, [0, 0, 0, []]); g[0] += a[0]; g[1] += a[3]; g[2] += a[4]; gross_all += a[4]
                if a[0]: g[3].append(t)
        for tg, (bull, eq, gross, ts) in tot.items():
            if abs(bull) < TH['ep']: continue
            vol = sum(px(tg, t)[1] for t in range(t0, nd)) * 1000
            eqp = abs(eq) / vol * 100 if vol else 0
            tags = []
            if abs(bull) >= TH['big']: tags.append('大額')
            if eqp >= TH['eqp']: tags.append('約當佔量高')
            if gross_all and gross / gross_all * 100 >= TH['focus']: tags.append('集中押注')
            prev = range(max(0, t0 - TH['new_look']), t0)
            if t0 >= 5 and all(tg not in S.get(t, {}) for t in prev): tags.append('新進場')
            if len(set(ts)) >= 3: tags.append('連續買' if bull > 0 else '連續賣')
            if not ({'大額', '約當佔量高'} & set(tags)) or len(tags) < 2: continue
            if b in flipper: tags.append('隔日沖型')
            s = stats.get(b)
            AL.append([b, tg, round(bull), round(eq), round(eqp, 2), tags, sorted(set(ts))[0], s[2] if s and s[0] >= 3 else None, s[0] if s else 0])
    AL.sort(key=lambda r: -(abs(r[2]) * (1 + 0.5 * ('新進場' in r[5]) + 0.5 * ('集中押注' in r[5])) * (0.4 if '隔日沖型' in r[5] else 1)))

    # 目前（最近 5 天）高勝率分點的佈局
    hold = {}
    for b in good:
        S = BT.get(b, {}); agg = {}
        for t in range(t0, nd):
            for tg, a in S.get(t, {}).items(): agg[tg] = agg.get(tg, 0) + a[0]
        hold[b] = [[tg, round(v)] for tg, v in sorted(agg.items(), key=lambda kv: -abs(kv[1])) if abs(v) >= 100000][:5]

    used = set(good) | {r[0] for r in AL} | {x[0] for s in summ.values() for k in s['k'].values() for x in k[7] + k[8]}
    out = {'date': D[-1], 'days': D, 'th': TH, 'base': base, 'baseR': base_r, 'nep': len(done), 'persist': persist,
           'n': {b: names.get(b, b) for b in used}, 'u': summ, 'al': AL[:300],
           'good': [[b] + stats[b] + [hold.get(b, [])] for b in good], 'flip': sorted(b for b in flipper if b in used)}
    with open(os.path.join(OUT, 'wt.json'), 'w') as f: json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

    # ── 明細檔 ──
    for sub in ('wu', 'wb'):
        shutil.rmtree(os.path.join(OUT, sub), ignore_errors=True); os.makedirs(os.path.join(OUT, sub))
    for tg, S in U.items():
        r = {}; nm = {}
        for t, bs in S.items():
            for b, a in bs.items():
                if abs(a[0]) < 50000 and a[4] < 200000: continue
                r.setdefault(b, []).append([t, round(a[0]), round(a[1]), round(a[2]), round(a[3])]); nm[b] = names.get(b, b)
        o = {'d': D, 'c': [close(tg, t) for t in range(nd)], 'v': [px(tg, t)[1] for t in range(nd)],
             'f': [round(FT.get(tg, {}).get(t, [0, 0])[0]) for t in range(nd)], 'n': nm,
             'st': {b: stats[b][:4] for b in nm if b in stats}, 'flip': [b for b in nm if b in flipper], 'r': r}
        with open(os.path.join(OUT, 'wu', tg + '.json'), 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
    for b, S in BT.items():
        rows = [[t, tg, round(a[0]), round(a[1]), round(a[2]), round(a[3])] for t, tgs in S.items() for tg, a in tgs.items() if abs(a[0]) >= 50000 or a[4] >= 200000]
        if not rows and b not in EP: continue
        o = {'d': D, 'id': b, 'n': names.get(b, b), 'st': stats.get(b), 'flip': b in flipper, 'fl': flip.get(b), 'ep': EP.get(b, []), 'r': rows,
             'c': {tg: [close(tg, t) for t in range(nd)] for tg in {x[1] for x in rows} | {e[1] for e in EP.get(b, [])}}}
        with open(os.path.join(OUT, 'wb', safe(b) + '.json'), 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
    log('權證整理：完整', nd, '天，標的', len(summ), '，佈局事件', len(done), '基準勝率', base, '，高勝率分點', len(good), '，疑似知情', len(AL), '，延續性', persist)
