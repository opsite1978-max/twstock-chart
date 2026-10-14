# 暫時用：比較幾種「主力成本」估算方法，哪一種的成本線比較像真的支撐／壓力
import os, json, urllib.request, urllib.parse, datetime, collections, statistics as st
T = os.environ['T']
def js(p):
    u = 'https://api.finmindtrade.com/api/v4/data?' + urllib.parse.urlencode(dict(p, token=T))
    with urllib.request.urlopen(u, timeout=180) as r: return json.loads(r.read().decode()).get('data') or []
S = '2025-01-01'
inst = js({'dataset': 'TaiwanFuturesInstitutionalInvestors', 'data_id': 'TX', 'start_date': S})
ah = js({'dataset': 'TaiwanFuturesInstitutionalInvestorsAfterHours', 'data_id': 'TX', 'start_date': S})
lt = js({'dataset': 'TaiwanFuturesOpenInterestLargeTraders', 'data_id': 'TX', 'start_date': S})
tx = js({'dataset': 'TaiwanFuturesDaily', 'data_id': 'TX', 'start_date': S})
print('rows', len(inst), len(ah), len(lt), len(tx))

def third_wed(y, m):
    d = datetime.date(y, m, 1); d += datetime.timedelta(days=(2 - d.weekday()) % 7 + 14); return d.isoformat()
def exp_of(c): return third_wed(int(c[:4]), int(c[4:6])) if len(c) == 6 and c.isdigit() else None

by = collections.defaultdict(list)
for r in tx:
    c = str(r.get('contract_date', '')).strip()
    if r.get('trading_session') == 'position' and exp_of(c) and r.get('close', 0) > 0: by[r['date']].append(dict(r, c=c))
days = sorted(by)
P = []
for d in days:
    l = sorted([r for r in by[d] if exp_of(r['c']) >= d], key=lambda r: r['c'])
    if not l: continue
    if len(l) < 2: continue
    n = l[0]; P.append(dict(d=d, o=n['open'], h=n['max'], l=n['min'], c=n['close'], exp=exp_of(n['c']), stl=l[1]['close']))
idx = {p['d']: i for i, p in enumerate(P)}

# 法人成交（日盤＋夜盤，夜盤日期＝下一個交易日）
F = collections.defaultdict(lambda: dict(bq=0, ba=0, sq=0, sa=0)); ALL = collections.defaultdict(lambda: [0, 0])
for src in (inst, ah):
    for r in src:
        a = ALL[r['date']]; a[0] += (r.get('long_deal_amount') or 0) + (r.get('short_deal_amount') or 0); a[1] += (r.get('long_deal_volume') or 0) + (r.get('short_deal_volume') or 0)
        if r['institutional_investors'] != '外資': continue
        f = F[r['date']]; f['bq'] += r.get('long_deal_volume') or 0; f['ba'] += r.get('long_deal_amount') or 0; f['sq'] += r.get('short_deal_volume') or 0; f['sa'] += r.get('short_deal_amount') or 0
fpos = {r['date']: r['long_open_interest_balance_volume'] - r['short_open_interest_balance_volume'] for r in inst if r['institutional_investors'] == '外資'}
LT = {r['date']: r for r in lt if r.get('contract_type') == 'all'}

for p in P:
    d = p['d']; f = F.get(d); a = ALL.get(d)
    p['typ'] = (p['h'] + p['l'] + p['c']) / 3
    p['vw'] = a[0] * 1000 / (a[1] * 200) if a and a[1] else p['typ']
    p['pb'] = f['ba'] * 1000 / (f['bq'] * 200) if f and f['bq'] else p['vw']
    p['ps'] = f['sa'] * 1000 / (f['sq'] * 200) if f and f['sq'] else p['vw']
    p['fnet'] = (f['bq'] - f['sq']) if f else None
    p['fi'] = fpos.get(d)
    r = LT.get(d)
    p['tL'] = r['buy_top10_trader_open_interest'] if r else None; p['tS'] = r['sell_top10_trader_open_interest'] if r else None
    p['t10'] = p['tL'] - p['tS'] if r else None
    p['settle'] = d == p['exp']

# 檢查：外資成交淨額 ≈ 未平倉變化？成交均價落在當天高低之間？
dd = [abs((P[i]['fi'] - P[i - 1]['fi']) - P[i]['fnet']) for i in range(1, len(P)) if P[i]['fi'] is not None and P[i - 1]['fi'] is not None and P[i]['fnet'] is not None]
print('foreign |dOI - net deal| median', st.median(dd), 'mean', round(st.mean(dd)))
inr = sum(1 for p in P if p['l'] - 300 <= p['vw'] <= p['h'] + 300) / len(P); print('vwap within day range(+-300 for night):', round(inr, 3))
print('vwap - typ median abs', round(st.median([abs(p['vw'] - p['typ']) for p in P])))

def cost_series(key, pb, ps, mode='avg', reset=False):
    out, prev, cost, lots = [], None, None, collections.deque()
    for p in P:
        q = p[key]
        if q is None: out.append(cost); continue
        b, s = p[pb], p[ps]
        if mode == 'avg':
            if prev is None or prev == 0 or (q > 0) != (prev > 0) or cost is None: cost = b if q > 0 else s
            elif abs(q) > abs(prev): px = b if q > prev else s; cost = (abs(prev) * cost + abs(q - prev) * px) / abs(q)
        else:  # 先進先出
            if prev is None or prev == 0 or (q > 0) != (prev > 0): lots = collections.deque([[abs(q), b if q > 0 else s]])
            elif abs(q) > abs(prev): lots.append([abs(q) - abs(prev), b if q > prev else s])
            else:
                k = abs(prev) - abs(q)
                while k > 0 and lots:
                    if lots[0][0] <= k: k -= lots[0][0]; lots.popleft()
                    else: lots[0][0] -= k; k = 0
            tot = sum(x[0] for x in lots); cost = sum(x[0] * x[1] for x in lots) / tot if tot else (b if q >= 0 else s)
            if reset and p['settle']: lots = collections.deque([[abs(q), p['stl']]]); cost = p['stl']
        if mode == 'avg' and reset and p['settle']: cost = p['stl']
        prev = q; out.append(cost)
    return out

def ema(vals, n):
    out, e = [], None
    for v in vals: e = v if e is None else e + (v - e) * 2 / (n + 1); out.append(e)
    return out
def sma(vals, n): return [st.mean(vals[max(0, i - n + 1):i + 1]) for i in range(len(vals))]

def evaluate(lv, start=60):
    up = [0, 0, []]; dn = [0, 0, []]
    for i in range(max(1, start), len(P)):
        L = lv[i - 1]
        if L is None: continue
        c0, p = P[i - 1]['c'], P[i]
        fwd = P[i + 3]['c'] - p['c'] if i + 3 < len(P) else None
        if c0 > L and p['l'] <= L:
            up[0] += 1; up[1] += p['c'] > L
            if fwd is not None: up[2].append(fwd)
        elif c0 < L and p['h'] >= L:
            dn[0] += 1; dn[1] += p['c'] < L
            if fwd is not None: dn[2].append(-fwd)
    n = up[0] + dn[0]; h = up[1] + dn[1]; fw = up[2] + dn[2]
    return f"touch {n:3d}  hold {h / n * 100 if n else 0:5.1f}%  (support {up[1]}/{up[0]}, resist {dn[1]}/{dn[0]})  3d move away {st.mean(fw) if fw else 0:+6.0f}  away>0 {sum(1 for x in fw if x > 0) / len(fw) * 100 if fw else 0:4.1f}%"

C = P[-1]['c']
cl = [p['c'] for p in P]
series = {
  '外資 網頁新法': cost_series('fi', 'pb', 'ps', 'avg', True),
  '前十大多單 網頁新法': cost_series('tL', 'vw', 'vw', 'avg', True),
  '前十大空單 網頁新法': cost_series('tS', 'vw', 'vw', 'avg', True),
  '外資 舊法(高低收均價,不重設)': cost_series('fi', 'typ', 'typ'),
  '前十大淨 舊法': cost_series('t10', 'typ', 'typ'),
  '對照 20日均線': sma(cl, 20), '對照 10日均線': sma(cl, 10),
}
print('\nlast day', P[-1]['d'], 'close', C, 'foreign net', P[-1]['fi'], 'top10 net', P[-1]['t10'], 'L', P[-1]['tL'], 'S', P[-1]['tS'])
for k, v in series.items():
    s1 = [x * 1.015 if x else None for x in v]; s2 = [x * 0.985 if x else None for x in v]
    print(f"\n{k}: now {v[-1]:.0f}" if v[-1] else f"\n{k}: now -")
    print('   level    ', evaluate(v)); print('   +1.5%    ', evaluate(s1)); print('   -1.5%    ', evaluate(s2))
# 最近 30 天的每日數值
print('\nrecent daily')
keys = ['外資 網頁新法', '前十大多單 網頁新法', '前十大空單 網頁新法']
for i in range(len(P) - 12, len(P)):
    p = P[i]
    print(p['d'], f"c={p['c']:.0f} vw={p['vw']:.0f} pb={p['pb']:.0f} ps={p['ps']:.0f} fi={p['fi']} L={p['tL']} S={p['tS']}", ' '.join(f"{series[k][i]:.0f}" if series[k][i] else '-' for k in keys))
