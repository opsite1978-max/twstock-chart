#!/usr/bin/env python3
"""台指選擇權波動率指數（臺指 VIX）長期歷史：自己用每天的 TXO 結算價依 Cboe VIX 方法算，
再和 FinMind 官方 VIX（2026-03 起才有）比對、校正。

輸出（vix-data 分支）
- calc.json   {日期: [A, B]}  A＝只用月選擇權（傳統做法）；B＝含週選擇權，取最接近 30 天的兩個到期日
- vix.json    {"u": 時間, "fit": 校正係數, "rows": [[日期, 開, 高, 低, 收, 來源], ...]}
              來源 O＝官方（FinMind，2026-03 起，有開高低收）；C＝自算並校正到官方刻度（只有收盤）
- taiex.json  [[日期, 收盤], ...]（回測用）

FinMind 憑證從環境變數 FINMIND_TOKEN 讀取，不會印出或存檔。
"""
import json, os, sys, math, time, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txo_scrape as T

OUT = sys.argv[1] if len(sys.argv) > 1 else 'vix-out'
START = os.environ.get('VIX_START', '2012-01-01')
T.REQ_BUDGET = int(os.environ.get('REQ_BUDGET', '400'))
R = 0.015
DEADLINE = time.time() + float(os.environ.get('MAX_MIN', '150')) * 60


def load(name, default):
    try:
        with open(os.path.join(OUT, name)) as f: return json.load(f)
    except Exception: return default


def save(name, obj):
    with open(os.path.join(OUT, name), 'w') as f: json.dump(obj, f, separators=(',', ':'))


def sigma2(S, F, Ty):
    """Cboe 方法的單一到期日變異數。S: {K: {'c':價,'p':價}}"""
    ks = sorted(S)
    below = [k for k in ks if k <= F]
    if not below: return None
    K0 = below[-1]
    pts = []
    # 賣權：K0 往下
    zero = 0
    for k in reversed([k for k in ks if k < K0]):
        p = S[k].get('p', 0)
        if p < 0.15:
            zero += 1
            if zero >= 2: break
            continue
        zero = 0; pts.append((k, p))
    zero = 0
    for k in [k for k in ks if k > K0]:
        c = S[k].get('c', 0)
        if c < 0.15:
            zero += 1
            if zero >= 2: break
            continue
        zero = 0; pts.append((k, c))
    o = S[K0]
    if o.get('c', 0) > 0 and o.get('p', 0) > 0: pts.append((K0, (o['c'] + o['p']) / 2))
    elif o.get('c', 0) > 0 or o.get('p', 0) > 0: pts.append((K0, o.get('c', 0) or o.get('p', 0)))
    pts.sort()
    if len(pts) < 5: return None
    tot = 0.0
    for i, (k, q) in enumerate(pts):
        if i == 0: dk = pts[1][0] - k
        elif i == len(pts) - 1: dk = k - pts[i - 1][0]
        else: dk = (pts[i + 1][0] - pts[i - 1][0]) / 2
        tot += dk / (k * k) * math.exp(R * Ty) * q
    return 2 / Ty * tot - 1 / Ty * (F / K0 - 1) ** 2


def vix_day(date, rows, cal):
    by = {}
    for r in rows:
        if r.get('trading_session') not in (None, '', 'position'): continue
        code = str(r.get('contract_date') or '').strip()
        exp = T.contract_exp(code)
        if not exp: continue
        exp = T.adjust_exp(exp, cal)
        if exp <= date: continue
        px = float(r.get('settlement_price') or 0) or float(r.get('close') or 0)
        if px <= 0: continue
        e = by.setdefault(code, {'exp': exp, 'S': {}, 'm': len(code) == 6})
        e['S'].setdefault(float(r['strike_price']), {})['c' if r.get('call_put') == 'call' else 'p'] = px
    terms = []
    for code, e in by.items():
        days = T.day_diff(date, e['exp']) - 5 / 24
        if days < 1 or days > 120: continue
        Ty = days / 365
        both = [(k, o) for k, o in e['S'].items() if o.get('c', 0) > 0 and o.get('p', 0) > 0]
        if len(both) < 3: continue
        k0, o0 = min(both, key=lambda x: abs(x[1]['c'] - x[1]['p']))
        F = k0 + math.exp(R * Ty) * (o0['c'] - o0['p'])
        s2 = sigma2(e['S'], F, Ty)
        if s2 and s2 > 0: terms.append({'d': days, 'T': Ty, 's2': s2, 'm': e['m']})

    def combo(cands):
        cands = sorted([t for t in cands if t['d'] >= 7], key=lambda t: t['d'])
        if not cands: return None
        if len(cands) == 1: v = cands[0]['s2']
        else:
            a = next((t for t in reversed(cands) if t['d'] <= 30), cands[0])
            b = next((t for t in cands if t['d'] > a['d'] and t['d'] >= 30), None) or next((t for t in cands if t['d'] > a['d']), None)
            if not b: v = a['s2']
            else:
                N1, N2, N30 = a['d'], b['d'], 30
                v = (a['T'] * a['s2'] * (N2 - N30) / (N2 - N1) + b['T'] * b['s2'] * (N30 - N1) / (N2 - N1)) * 365 / N30
        return round(100 * math.sqrt(v), 2) if v > 0 else None
    A = combo([t for t in terms if t['m']])
    B = combo(terms)
    return [A, B] if (A or B) else None


def months(a, b):
    y, m = int(a[:4]), int(a[5:7])
    while '%04d-%02d' % (y, m) <= b[:7]:
        s = '%04d-%02d-01' % (y, m)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        e = (datetime.date(ny, nm, 1) - datetime.timedelta(days=1)).isoformat()
        yield s, e
        y, m = ny, nm


def main():
    os.makedirs(OUT, exist_ok=True)
    today = datetime.datetime.now(T.TW).date().isoformat()
    cal = sorted({r['date'] for r in T.api({'dataset': 'TaiwanStockTradingDate', 'start_date': '2005-01-01'})})
    T.CALSET.update(cal)
    calc = load('calc.json', {})
    # 1. 自算（缺的月份才抓；最近一個月每次重算）
    for s, e in months(START, today):
        days = [d for d in cal if s <= d <= min(e, today)]
        if not days: continue
        if all(d in calc for d in days) and e < today: continue
        if time.time() > DEADLINE: T.log('時間到，下次接著算'); break
        try:
            rows = T.api({'dataset': 'TaiwanOptionDaily', 'data_id': 'TXO', 'start_date': s, 'end_date': e})
        except T.RateLimited as ex:
            T.log('停止：', ex); break
        except Exception as ex:
            T.log('失敗', s, ex); continue
        byd = {}
        for r in rows: byd.setdefault(r['date'], []).append(r)
        n = 0
        for d in days:
            if byd.get(d):
                v = vix_day(d, byd[d], cal)
                if v: calc[d] = v; n += 1
        T.log('月', s[:7], '資料', len(rows), '算出', n, '天')
        save('calc.json', calc)
        del rows, byd
    # 2. 官方（FinMind 15 秒資料，2026-03 起）→ 日開高低收
    off = load('official.json', {})
    for s, e in months('2026-03-01', today):
        if all(d in off for d in cal if s <= d <= min(e, today)) and e < today: continue
        try:
            rows = T.api({'dataset': 'TaiwanOptionVix', 'start_date': s, 'end_date': min(e, today)})
        except Exception as ex:
            T.log('官方 VIX 失敗', s, ex); continue
        byd = {}
        for r in rows:
            if r.get('vix'): byd.setdefault(r['date'], []).append((r['time'], float(r['vix'])))
        for d, xs in byd.items():
            xs.sort(); v = [x[1] for x in xs]
            off[d] = [v[0], max(v), min(v), v[-1]]
    save('official.json', off)
    # 3. 比對、校正：官方收盤 ≈ a + b × 自算
    fit = {}
    for idx, nm in ((0, 'A'), (1, 'B')):
        pairs = [(calc[d][idx], off[d][3]) for d in off if d in calc and calc[d][idx]]
        if len(pairs) < 20: continue
        xs, ys = [p[0] for p in pairs], [p[1] for p in pairs]
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        sxx = sum((x - mx) ** 2 for x in xs); sxy = sum((x - mx) * (y - my) for x, y in pairs); syy = sum((y - my) ** 2 for y in ys)
        b = sxy / sxx; a = my - b * mx; r = sxy / math.sqrt(sxx * syy)
        mae_raw = sum(abs(x - y) for x, y in pairs) / len(pairs)
        mae_fit = sum(abs(a + b * x - y) for x, y in pairs) / len(pairs)
        # 方向一致：每天漲跌方向相同的比例
        ds = sorted(d for d in off if d in calc and calc[d][idx])
        same = sum(1 for i in range(1, len(ds)) if (calc[ds[i]][idx] - calc[ds[i - 1]][idx]) * (off[ds[i]][3] - off[ds[i - 1]][3]) > 0) / max(1, len(ds) - 1)
        fit[nm] = {'a': round(a, 4), 'b': round(b, 4), 'r': round(r, 4), 'n': len(pairs), 'mae_raw': round(mae_raw, 2), 'mae_fit': round(mae_fit, 2), 'dir': round(same, 3)}
        T.log('比對', nm, fit[nm])
    best = max(fit, key=lambda k: fit[k]['r']) if fit else 'B'
    idx = 0 if best == 'A' else 1
    f = fit.get(best, {'a': 0, 'b': 1})
    rows = []
    for d in sorted(set(calc) | set(off)):
        if d in off: o = off[d]; rows.append([d, o[0], o[1], o[2], o[3], 'O'])
        elif calc.get(d) and calc[d][idx]:
            v = round(f['a'] + f['b'] * calc[d][idx], 2); rows.append([d, v, v, v, v, 'C'])
    save('vix.json', {'u': int(time.time()), 'use': best, 'fit': fit, 'rows': rows})
    # 4. 加權指數（回測用）
    try:
        tx = T.api({'dataset': 'TaiwanStockPrice', 'data_id': 'TAIEX', 'start_date': '2011-06-01'})
        save('taiex.json', [[r['date'], r['close'], r['max'], r['min']] for r in tx if r.get('close')])
    except Exception as ex:
        T.log('加權指數失敗', ex)
    T.log('完成：自算', len(calc), '天，官方', len(off), '天，採用', best, '，共', len(rows), '天')


main()
