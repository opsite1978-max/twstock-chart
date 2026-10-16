# 暫時用：遠近月價差震盪指標回測（日線＋小時線）
import os, json, time, urllib.request, urllib.parse, datetime, collections, statistics as st
T = os.environ['T']
NREQ = [0]
def js(p):
    NREQ[0] += 1; time.sleep(0.5)                       # 慢慢查，避免短時間大量查詢
    u = 'https://api.finmindtrade.com/api/v4/data?' + urllib.parse.urlencode(dict(p, token=T))
    for k in range(3):
        try:
            with urllib.request.urlopen(u, timeout=180) as r: return json.loads(r.read().decode()).get('data') or []
        except Exception as e: err = e; time.sleep(3)
    print('ERR', p.get('dataset'), p.get('start_date'), str(err)[:100]); return []

def third_wed(y, m):
    d = datetime.date(y, m, 1); d += datetime.timedelta(days=(2 - d.weekday()) % 7 + 14); return d.isoformat()
def exp_of(c): return third_wed(int(c[:4]), int(c[4:6])) if len(c) == 6 and c.isdigit() else None

# ───── 日線 ─────
tx = js({'dataset': 'TaiwanFuturesDaily', 'data_id': 'TX', 'start_date': '2019-01-01'})
ix = js({'dataset': 'TaiwanStockPrice', 'data_id': 'TAIEX', 'start_date': '2019-01-01'})
IX = {r['date']: r['close'] for r in ix if r.get('close')}
print('daily rows', len(tx), 'taiex', len(IX))
by = collections.defaultdict(list)
for r in tx:
    c = str(r.get('contract_date', '')).strip()
    if r.get('trading_session') == 'position' and exp_of(c) and r.get('close', 0) > 0: by[r['date']].append(dict(r, c=c))
DY = []
for d in sorted(by):
    l = sorted([r for r in by[d] if exp_of(r['c']) > d], key=lambda r: r['c'])
    if len(l) < 2 or d not in IX: continue
    DY.append(dict(d=d, c=l[0]['close'], sp=l[1]['close'] - l[0]['close'], bs=l[0]['close'] - IX[d], ix=IX[d], exp=exp_of(l[0]['c'])))
print('daily bars', len(DY), DY[0]['d'], DY[-1]['d'])

def zser(v, n):
    out = []
    for i in range(len(v)):
        w = v[max(0, i - n + 1):i + 1]
        if len(w) < max(5, n // 2): out.append(None); continue
        m = st.mean(w); s = st.pstdev(w) or 1e-9; out.append((v[i] - m) / s)
    return out
def pct(v, n):
    out = []
    for i in range(len(v)):
        w = v[max(0, i - n + 1):i + 1]
        if len(w) < max(5, n // 2): out.append(None); continue
        out.append(sum(1 for x in w if x <= v[i]) / len(w) * 100)
    return out

def test(name, osc, px, lo, hi, fwd, base_fwd, cross_ok=None):
    # 低點訊號：前 3 根內曾 ≤ 下界，這根開始往上；高點訊號：前 3 根內曾 ≥ 上界，這根開始往下
    L, H = [], []
    for i in range(4, len(osc) - fwd):
        o = osc[i]; p = osc[i - 1]
        if o is None or p is None: continue
        if cross_ok and not cross_ok(i, i + fwd): continue
        r = (px[i + fwd] / px[i] - 1) * 100
        if any(osc[j] is not None and osc[j] <= lo for j in range(i - 3, i)) and o > p and (not L or i - L[-1][0] > fwd): L.append((i, r))
        if any(osc[j] is not None and osc[j] >= hi for j in range(i - 3, i)) and o < p and (not H or i - H[-1][0] > fwd): H.append((i, r))
    f = lambda S: f"n={len(S):3d} avg {st.mean([x[1] for x in S]) if S else 0:+.2f}% up {sum(1 for x in S if x[1] > 0) / len(S) * 100 if S else 0:4.0f}%"
    print(f"  {name:34s} 低點訊號 {f(L)} | 高點訊號 {f(H)} | 基準 {base_fwd:+.2f}%")

px = [r['ix'] for r in DY]
def nocross(i, j): return DY[i]['exp'] == DY[min(j, len(DY) - 1)]['exp'] or True
for fwd in (3, 5, 10):
    base = st.mean([(px[i + fwd] / px[i] - 1) * 100 for i in range(len(px) - fwd)])
    print(f'\n日線 未來 {fwd} 天（加權指數）')
    for key, lab in (('sp', '遠近月價差'), ('bs', '近月正逆價差')):
        v = [r[key] for r in DY]
        for n in (20, 60):
            z = zser(v, n); test(f'{lab} z{n} ±2', z, px, -2, 2, fwd, base); test(f'{lab} z{n} ±1.5', z, px, -1.5, 1.5, fwd, base)
            pr = pct(v, n); test(f'{lab} 百分位{n} 10/90', pr, px, 10, 90, fwd, base)
# 相關：價差 z 和未來報酬
for key in ('sp', 'bs'):
    v = [r[key] for r in DY]; z = zser(v, 20)
    pairs = [(z[i], (px[i + 5] / px[i] - 1) * 100) for i in range(len(px) - 5) if z[i] is not None]
    xs, ys = zip(*pairs); mx, my = st.mean(xs), st.mean(ys)
    cor = sum((a - mx) * (b - my) for a, b in pairs) / (len(pairs) * st.pstdev(xs) * st.pstdev(ys))
    print(f'{key} z20 與未來 5 天報酬相關係數 {cor:+.3f}  (n={len(pairs)})')
    for a, b in ((-9, -2), (-2, -1), (-1, 0), (0, 1), (1, 2), (2, 9)):
        s = [y for x, y in pairs if a <= x < b]
        print(f'   z 在 {a:+d}～{b:+d}: n={len(s):4d} 未來5天平均 {st.mean(s) if s else 0:+.2f}%')

# ───── 小時線（最近約 100 個交易日）─────
days = [r['d'] for r in DY][-100:]
qd = sorted(set(days) | {(datetime.date.fromisoformat(d) + datetime.timedelta(days=1)).isoformat() for d in days})
rows = []
for d in qd:
    for r in js({'dataset': 'TaiwanFuturesKBar', 'data_id': 'TX', 'start_date': d, 'end_date': d}):
        c = str(r.get('contract_date', '')).strip()
        if not exp_of(c) or not (r.get('close') or 0) > 0: continue
        hm = str(r['minute']).split(' ')[-1].split(':'); m = int(hm[0]) * 60 + int(hm[1])
        rows.append((r['date'], m, c, float(r['close'])))
print('\nminute rows', len(rows), 'requests so far', NREQ[0])
# 切成小時 K（日盤 08:45 起、夜盤 15:00 起）
def ts(d, m): return datetime.datetime.fromisoformat(d) + datetime.timedelta(minutes=m)
bars = collections.OrderedDict()
for d, m, c, cl in rows:
    if 525 <= m <= 825: key = ts(d, 525) + datetime.timedelta(minutes=(min(m, 824) - 525) // 60 * 60)
    elif m >= 900: key = ts(d, 900) + datetime.timedelta(minutes=(m - 900) // 60 * 60)
    elif m < 300: key = ts(d, 900) - datetime.timedelta(days=1) + datetime.timedelta(minutes=(m + 1440 - 900) // 60 * 60)
    else: continue
    bars.setdefault(key, collections.defaultdict(dict))[c][ts(d, m)] = cl
HB, prev = [], None
for k in sorted(bars):
    b = bars[k]; bd = k.date().isoformat()
    live = sorted(c for c in b if exp_of(c) > bd or (exp_of(c) == bd and k.hour >= 15))
    live = [c for c in live if not (exp_of(c) == bd and k.hour < 15)]
    if not live: continue
    n = live[0]; px = b[n][max(b[n])]
    sp = None
    if len(live) >= 2:
        f = live[1]; common = set(b[n]) & set(b[f])
        if common: t = max(common); sp = b[f][t] - b[n][t]
    fresh = sp is not None
    if sp is None: sp = prev
    if sp is None: continue
    prev = sp; HB.append(dict(t=k, px=px, c=n, sp=sp, fresh=fresh))
print('hourly bars', len(HB), 'fresh spread share', round(sum(1 for h in HB if h['fresh']) / max(1, len(HB)), 3), HB[0]['t'], HB[-1]['t'])
hp = [h['px'] for h in HB]
def same_c(i, j): return HB[i]['c'] == HB[j]['c']
for fwd in (4, 10, 19):
    base = st.mean([(hp[i + fwd] / hp[i] - 1) * 100 for i in range(len(hp) - fwd) if same_c(i, i + fwd)])
    print(f'\n小時線 未來 {fwd} 根')
    v = [h['sp'] for h in HB]
    for n in (60, 120):
        z = zser(v, n); test(f'價差 z{n} ±2', z, hp, -2, 2, fwd, base, same_c); test(f'價差 z{n} ±1.5', z, hp, -1.5, 1.5, fwd, base, same_c)
        pr = pct(v, n); test(f'價差 百分位{n} 10/90', pr, hp, 10, 90, fwd, base, same_c)
v = [h['sp'] for h in HB]; z = zser(v, 60)
pairs = [(z[i], (hp[i + 10] / hp[i] - 1) * 100) for i in range(len(hp) - 10) if z[i] is not None and same_c(i, i + 10)]
xs, ys = zip(*pairs); mx, my = st.mean(xs), st.mean(ys)
print('hourly z60 vs fwd10 corr', round(sum((a - mx) * (b - my) for a, b in pairs) / (len(pairs) * st.pstdev(xs) * st.pstdev(ys)), 3), 'n', len(pairs))
print('last hourly', [(str(h['t']), h['px'], round(h['sp'])) for h in HB[-6:]])
print('requests', NREQ[0])
