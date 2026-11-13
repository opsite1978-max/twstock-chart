"""異常分點：從每天存下的券商分點明細（d/日期.json 的 b 欄）找出不尋常的買賣。

輸出（都在 chain-data 分支）：
  brk.json        最近 5 個交易日的異常清單、族群同步、分點買賣超排行、隔日沖統計、門檻
  br/代號.json    每檔股票最近 20 天的主要分點（公司小視窗用）
  bk/分點.json    每個分點最近 20 天在各股票的買賣（分點視窗用）

異常的種類（門檻在 TH，全部用「佔當天成交量」來比，大型股和小型股才公平）：
  獨買／獨賣   單一分點一天買超（賣超）≥ 成交量 10%，而且金額 ≥ 3,000 萬
  新面孔       前 10 天在這檔幾乎沒出現過的分點，突然買超 ≥ 成交量 5%（≥ 2,000 萬）
  連續買／賣   同一分點最近 5 天有 4 天以上同方向，合計 ≥ 5 天成交量 5%（≥ 5,000 萬），今天也還在同方向
  隔日沖進場   過去「今天大買、隔天就賣掉一半以上」比例很高的分點，今天又大買（≥ 成交量 3%）→ 明天小心賣壓
  當沖大戶     同一分點當天買和賣都 ≥ 成交量 10%、淨額很小
  族群同步     同一個分點同一天在同一個產業鏈環節買超 3 檔以上（每檔 ≥ 成交量 2%、≥ 2,000 萬）
  逆勢吃貨／逆勢出貨（附加標籤）  股價跌 2% 以上卻大買、漲 2% 以上卻大賣
"""
import json, os, re, shutil

TH = {
    'solo': 10.0, 'solo_v': 0.3,          # 獨買獨賣：佔量 %、億
    'new': 5.0, 'new_v': 0.2, 'new_look': 10, 'new_min': 5,
    'run_days': 5, 'run_hit': 4, 'run': 5.0, 'run_v': 0.5,
    'hd_buy': 3.0, 'hd_buy_v': 0.1, 'hd_back': 0.6, 'hd_min_ev': 4, 'hd_rate': 50,
    'dt': 10.0, 'dt_v': 0.3, 'dt_net': 0.3,
    'grp': 2.0, 'grp_v': 0.2, 'grp_n': 3,
    'contra': 2.0,
}
SHOW_DAYS = 5


def safe(bid): return re.sub(r'[^0-9A-Za-z]', '_', bid)


def build(OUT, D, data, ch, stocks, brokers, log):
    bdays = [d for d in D if len(data[d].get('b', {})) > 50]
    if not bdays: log('異常分點：還沒有分點明細'); return
    nb = len(bdays)
    # 每檔每天：{分點: [買, 賣, 買均價, 賣均價]}、成交量、收盤、漲跌
    B = {}
    for t, d in enumerate(bdays):
        for c, det in data[d]['b'].items():
            p = data[d]['p'].get(c)
            if not p or not p[2]: continue
            B.setdefault(c, {})[t] = ({e[0]: e[1:] for e in det}, p[2], p[0], p[1])

    def val(net, e, close):      # 億
        px = (e[2] if net > 0 else e[3]) or close
        return net * px * 1000 / 1e8

    # ── 隔日沖統計：今天大買，下一個交易日賣掉 ≥ 6 成 ──
    hd = {}
    for c, S in B.items():
        for t in range(nb - 1):
            if t not in S or t + 1 not in S: continue
            br, vol, close, _ = S[t]; br2 = S[t + 1][0]
            for bid, e in br.items():
                net = e[0] - e[1]
                if net <= 0 or net / vol * 100 < TH['hd_buy'] or val(net, e, close) < TH['hd_buy_v']: continue
                h = hd.setdefault(bid, [0, 0]); h[0] += 1
                e2 = br2.get(bid)
                if e2 and (e2[0] - e2[1]) <= -TH['hd_back'] * net: h[1] += 1
    hd_tag = {b for b, (n, r) in hd.items() if n >= TH['hd_min_ev'] and r / n * 100 >= TH['hd_rate']}

    # 產業鏈環節
    seg_of = {}
    for ind in ch['inds']:
        for col in ind['cols']:
            for sg in col['segs']:
                groups = [(sg['n'], sg.get('s', []))] + [(sg['n'] + '・' + sb['n'], sb.get('s', [])) for sb in sg.get('sub', [])]
                for nm, cs in groups:
                    for c in cs: seg_of.setdefault(c, []).append((ind['name'], col['t'], nm, len(cs)))

    A = {}; G = []; RK = []
    for t in range(max(0, nb - SHOW_DAYS), nb):
        gbuy = {}; tot = {}
        for c, S in B.items():
            if t not in S: continue
            br, vol, close, chg = S[t]
            prev = [k for k in range(max(0, t - TH['new_look']), t) if k in S]
            win = [k for k in range(t - TH['run_days'] + 1, t + 1) if k in S]
            vwin = sum(S[k][1] for k in win)
            for bid, e in br.items():
                net = e[0] - e[1]; sh = net / vol * 100; v = val(net, e, close)
                a = tot.setdefault(bid, [0.0, 0, []]); a[0] += v
                if abs(v) >= 0.05: a[1] += 1; a[2].append((v, c))
                tp = []
                if abs(sh) >= TH['solo'] and abs(v) >= TH['solo_v']: tp.append('獨買' if net > 0 else '獨賣')
                if net > 0 and sh >= TH['new'] and v >= TH['new_v'] and len(prev) >= TH['new_min'] and all(bid not in S[k][0] for k in prev): tp.append('新面孔')
                if len(win) >= TH['run_hit'] and net != 0:
                    sg = 1 if net > 0 else -1
                    hits = [k for k in win if bid in S[k][0] and sg * (S[k][0][bid][0] - S[k][0][bid][1]) > 0]
                    cum = sum(S[k][0][bid][0] - S[k][0][bid][1] for k in hits)
                    cv = sum(val(S[k][0][bid][0] - S[k][0][bid][1], S[k][0][bid], S[k][2]) for k in hits)
                    if len(hits) >= TH['run_hit'] and sg * cum / vwin * 100 >= TH['run'] and sg * cv >= TH['run_v']:
                        tp.append('連續買' if sg > 0 else '連續賣')
                if bid in hd_tag and net > 0 and sh >= TH['hd_buy'] and v >= TH['hd_buy_v']: tp.append('隔日沖進場')
                g = min(e[0], e[1])
                if g / vol * 100 >= TH['dt'] and g * close * 1000 / 1e8 >= TH['dt_v'] and abs(net) < TH['dt_net'] * max(e[0], e[1]): tp.append('當沖大戶')
                if tp and tp != ['當沖大戶']:
                    if net > 0 and chg <= -TH['contra']: tp.append('逆勢吃貨')
                    if net < 0 and chg >= TH['contra']: tp.append('逆勢出貨')
                if tp:
                    A.setdefault(t, []).append([c, bid, tp, net, round(sh, 1), round(v, 2), e[2] if net > 0 else e[3], close, chg, e[0], e[1]])
                if net > 0 and sh >= TH['grp'] and v >= TH['grp_v']:
                    gbuy.setdefault(bid, []).append((c, v))
        # 族群同步：同一分點在同一環節買 3 檔以上
        for bid, lst in gbuy.items():
            if len(lst) < TH['grp_n']: continue
            vm = dict(lst); best = {}
            for c in vm:
                for (ind, col, seg, size) in seg_of.get(c, []):
                    best.setdefault((ind, col, seg, size), set()).add(c)
            seen = set()
            for (ind, col, seg, size), cs in sorted(best.items(), key=lambda kv: (-len(kv[1]), kv[0][3])):
                if len(cs) < TH['grp_n']: break
                key = frozenset(cs)
                if any(key <= s for s in seen): continue
                seen.add(key)
                G.append([t, bid, ind, col, seg, sorted(cs, key=lambda c: -vm[c]), round(sum(vm[c] for c in cs), 2)])
        # 分點當天買賣超排行（產業鏈股票合計）
        top = sorted(tot.items(), key=lambda kv: -kv[1][0])
        fmt = lambda kv: [kv[0], round(kv[1][0], 2), kv[1][1], [c for _, c in sorted(kv[1][2], key=lambda x: -x[0] if kv[1][0] > 0 else x[0])[:4]]]
        RK.append([t, [fmt(kv) for kv in top[:20] if kv[1][0] > 0], [fmt(kv) for kv in top[::-1][:20] if kv[1][0] < 0]])

    rows = []
    for t, lst in A.items():
        for r in lst: rows.append([t] + r)
    rows.sort(key=lambda r: (-r[0], -abs(r[5])))
    used = {r[2] for r in rows} | {g[1] for g in G} | {x[0] for rk in RK for side in rk[1:] for x in side}
    first = max(0, nb - SHOW_DAYS)
    out = {'date': bdays[-1], 'days': bdays, 'show': list(range(first, nb)), 'th': TH,
           'n': {b: brokers.get(b, b) for b in used},
           'hd': {b: hd[b] for b in used if b in hd}, 'hdTag': sorted(hd_tag),
           'a': rows, 'g': G, 'rk': RK}
    with open(os.path.join(OUT, 'brk.json'), 'w') as f: json.dump(out, f, ensure_ascii=False, separators=(',', ':'))

    # 每檔、每個分點的明細檔
    for sub in ('br', 'bk'):
        shutil.rmtree(os.path.join(OUT, sub), ignore_errors=True); os.makedirs(os.path.join(OUT, sub))
    BK = {}
    for c, S in B.items():
        r = {}; nm = {}
        for t, (br, vol, close, chg) in S.items():
            for bid, e in br.items():
                r.setdefault(bid, []).append([t] + e); nm[bid] = brokers.get(bid, bid)
                BK.setdefault(bid, []).append([t, c] + e)
        o = {'d': bdays, 'v': [S[t][1] if t in S else 0 for t in range(nb)], 'c': [S[t][2] if t in S else 0 for t in range(nb)],
             'n': nm, 'hd': {b: hd[b] for b in nm if b in hd}, 'r': r}
        with open(os.path.join(OUT, 'br', c + '.json'), 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
    for bid, lst in BK.items():
        cs = {x[1] for x in lst}
        o = {'d': bdays, 'id': bid, 'n': brokers.get(bid, bid), 'hd': hd.get(bid), 'tag': bid in hd_tag, 'r': lst,
             'vol': {c: [B[c][t][1] if t in B[c] else 0 for t in range(nb)] for c in cs}}
        with open(os.path.join(OUT, 'bk', safe(bid) + '.json'), 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
    cnt = {}
    for r in rows:
        if r[0] == nb - 1:
            for k in r[3]: cnt[k] = cnt.get(k, 0) + 1
    log('異常分點：分點明細', nb, '天，最新一天', cnt, '族群同步', sum(1 for g in G if g[0] == nb - 1), '隔日沖常客', len(hd_tag), '分點檔', len(BK))
