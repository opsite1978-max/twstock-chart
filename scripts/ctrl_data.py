# 主力成本線（合約層級）：每天晚上在 GitHub 上計算，結果存到 data/ctrl/cost.json
#
# 做法
# 1. 每個月份合約、每個交易日（前一晚夜盤＋當天日盤）的成交量加權均價與價位分布，用台指期一分K算出來。
# 2. 前十大交易人的部位分成「近月」和「其他月份」（全部月份 − 近月 − 週契約），多單、空單分開。
# 3. 每個月份合約各自記帳：部位增加的口數，依當天這個合約的成交價位分布加進去；部位減少就等比例減少（平均成本不變）。
# 4. 合約到期就結清；次月部位接著變成近月，成本一路延續，不用假設轉倉價格。
#    台指期每個月份最多存在兩個月左右，所以算兩個多月後，所有部位都是資料期間內建立的，成本不受起算日影響。
# 5. 外資只有全部月份合計的淨部位，依市場近月／次月未平倉比例分到兩個月份，再用同樣方法記帳。
import os, sys, json, time, datetime, urllib.request, urllib.parse, collections, statistics as st

TOKEN = os.environ.get('FINMIND_TOKEN', '')
OUT = 'data/ctrl'
CACHE = f'{OUT}/kbar_cache.json'
START = '2025-01-02'
BIN = 20
BUDGET = int(os.environ.get('REQ_BUDGET', '700'))
NREQ = [0]

def api(params):
    if NREQ[0] >= BUDGET: raise RuntimeError('budget')
    NREQ[0] += 1; time.sleep(0.6)                       # 慢慢查，避免短時間大量查詢
    u = 'https://api.finmindtrade.com/api/v4/data?' + urllib.parse.urlencode(dict(params, token=TOKEN))
    err = None
    for k in range(3):
        try:
            with urllib.request.urlopen(u, timeout=180) as r:
                j = json.loads(r.read().decode())
            if j.get('status') not in (200, None): raise RuntimeError(f"status {j.get('status')} {str(j.get('msg'))[:80]}")
            return j.get('data') or []
        except Exception as e:
            err = e; time.sleep(4 * (k + 1))
    raise RuntimeError(f"{params.get('dataset')} {params.get('start_date')}: {str(err)[:120]}")

def third_wed(y, m):
    d = datetime.date(y, m, 1); d += datetime.timedelta(days=(2 - d.weekday()) % 7 + 14); return d.isoformat()
def exp_of(c): return third_wed(int(c[:4]), int(c[4:6])) if len(c) == 6 and c.isdigit() else None
def dshift(d, n): return (datetime.date.fromisoformat(d) + datetime.timedelta(days=n)).isoformat()
TODAY = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).date().isoformat()

os.makedirs(OUT, exist_ok=True)
cache = {}
if os.path.exists(CACHE):
    try: cache = json.load(open(CACHE))
    except Exception: cache = {}

# ── 日資料 ──
tx = api({'dataset': 'TaiwanFuturesDaily', 'data_id': 'TX', 'start_date': dshift(START, -60)})
lt = api({'dataset': 'TaiwanFuturesOpenInterestLargeTraders', 'data_id': 'TX', 'start_date': START})
inst = api({'dataset': 'TaiwanFuturesInstitutionalInvestors', 'data_id': 'TX', 'start_date': START})
pos = collections.defaultdict(dict)        # 日 → 合約 → 日盤資料
for r in tx:
    c = str(r.get('contract_date', '')).strip()
    if r.get('trading_session') == 'position' and exp_of(c): pos[r['date']][c] = r
TD = sorted(d for d in pos if d >= START)
print('trading days', len(TD), TD[0], TD[-1])

# ── 一分K → 每個日曆日、每個時段、每個合約的量、金額、價位分布 ──
def fetch_day(date):
    rows = api({'dataset': 'TaiwanFuturesKBar', 'data_id': 'TX', 'start_date': date, 'end_date': date})
    out = {}
    for r in rows:
        c = str(r.get('contract_date', '')).strip(); v = float(r.get('volume') or 0); p = float(r.get('close') or 0)
        if not exp_of(c) or v <= 0 or p <= 0: continue
        hm = str(r.get('minute', '')).split(' ')[-1].split(':'); m = int(hm[0]) * 60 + int(hm[1])
        part = 'D' if 525 <= m <= 825 else 'N1' if m >= 900 else 'N2' if m < 300 else None
        if not part: continue
        o = out.setdefault(part, {}).setdefault(c, {'v': 0, 'a': 0, 'p': {}})
        o['v'] += v; o['a'] += v * p; b = str(int(p // BIN) * BIN); o['p'][b] = o['p'].get(b, 0) + v
    keep = sorted(c for c in {c for part in out.values() for c in part} if exp_of(c) >= dshift(date, -1))[:3]   # 只留最近三個月份
    return {part: {c: o for c, o in cs.items() if c in keep} for part, cs in out.items()}
need = set()
for i, d in enumerate(TD):
    need.add(d)
    prev = TD[i - 1] if i else dshift(d, -1)
    x = dshift(prev, 1)
    while x < d: need.add(x); x = dshift(x, 1)      # 前一晚夜盤過午夜的部分（含週六凌晨）
    need.add(prev)
fetched = 0
try:
    for d in sorted(need):
        fresh = d >= dshift(TODAY, -3)
        if d in cache and not fresh: continue
        cache[d] = fetch_day(d); fetched += 1
        if fetched % 50 == 0: print('fetched', fetched, d); json.dump(cache, open(CACHE, 'w'), separators=(',', ':'))
except RuntimeError as e:
    print('stop fetching:', e)
json.dump(cache, open(CACHE, 'w'), separators=(',', ':'))
print('minute days fetched', fetched, 'requests', NREQ[0])

def merge(dst, src):
    for c, o in src.items():
        t = dst.setdefault(c, {'v': 0, 'a': 0, 'p': {}}); t['v'] += o['v']; t['a'] += o['a']
        for b, v in o['p'].items(): t['p'][b] = t['p'].get(b, 0) + v
# 交易日 T＝前一個交易日 15:00 夜盤 → T 日 13:45 日盤
SESS = {}
for i, d in enumerate(TD):
    if d not in cache: continue
    prev = TD[i - 1] if i else None
    agg = {}
    if prev and prev in cache: merge(agg, cache[prev].get('N1', {}))
    x = dshift(prev, 1) if prev else d
    while x <= d:
        if x in cache: merge(agg, cache[x].get('N2', {}))
        x = dshift(x, 1)
    merge(agg, cache[d].get('D', {}))
    SESS[d] = agg
print('sessions with minute data', len(SESS))

def prof(d, c):
    """這個交易日、這個合約的價位分布（比例），沒有分K就用日K的 (高+低+收)/3"""
    o = SESS.get(d, {}).get(c)
    if o and o['v'] > 0: return {int(b): v / o['v'] for b, v in o['p'].items()}, o['a'] / o['v']
    r = pos.get(d, {}).get(c)
    if r and r.get('close'):
        p = (r['max'] + r['min'] + r['close']) / 3; return {int(p // BIN) * BIN: 1.0}, p
    return None, None

# ── 合約層級記帳 ──
class Book:
    def __init__(s): s.b = {}       # 合約 → {'q':口數, 'c':平均成本, 'lots':{價位:口數}}
    def set(s, d, targets):
        for c in list(s.b):
            if c not in targets: del s.b[c]                       # 到期或不再持有 → 結清
        for c, q in targets.items():
            q = max(0.0, float(q)); b = s.b.setdefault(c, {'q': 0.0, 'c': None, 'lots': {}})
            dq = q - b['q']
            if dq > 0:
                pr, vw = prof(d, c)
                if pr is None: pr, vw = ({int(b['c'] // BIN) * BIN: 1.0}, b['c']) if b['c'] else (None, None)
                if pr is None: continue
                b['c'] = vw if not b['q'] else (b['q'] * b['c'] + dq * vw) / q
                for k, w in pr.items(): b['lots'][k] = b['lots'].get(k, 0) + dq * w
            elif dq < 0 and b['q'] > 0:
                f = q / b['q']; b['lots'] = {k: v * f for k, v in b['lots'].items() if v * f > 1e-6}
            b['q'] = q
    def cost(s, c=None):
        bs = [s.b[c]] if c else list(s.b.values()); bs = [b for b in bs if b['q'] > 0 and b['c']]
        q = sum(b['q'] for b in bs); return (sum(b['q'] * b['c'] for b in bs) / q if q else None), q
    def lots(s):
        out = collections.Counter()
        for b in s.b.values():
            for k, v in b['lots'].items(): out[k] += v
        return out

LT = collections.defaultdict(dict)
for r in lt:
    ct = str(r.get('contract_type', '')).strip()
    LT[r['date']]['all' if ct == 'all' else 'week' if ct == 'week' else 'near'] = dict(r, ct=ct)
FI = {r['date']: (r['long_open_interest_balance_volume'] or 0) - (r['short_open_interest_balance_volume'] or 0) for r in inst if r.get('institutional_investors') == '外資'}

def pct_price(lots, p):
    """部位價位分布由低往高累積到 p 比例的價位（取價格區間中間）"""
    tot = sum(lots.values())
    if tot <= 0: return None
    acc = 0.0
    for k in sorted(lots):
        acc += lots[k]
        if acc >= p * tot: return k + BIN / 2
    return None

BL, BS, BF = Book(), Book(), Book(); fsign = 0
days = []
for d in TD:
    cs = sorted(c for c in pos[d] if exp_of(c) >= d)
    if len(cs) < 2: continue
    mn = cs[0]; live = [c for c in cs if exp_of(c) > d]
    row = {'d': d, 'c0': mn, 'o': pos[d][mn]['open'], 'h': pos[d][mn]['max'], 'l': pos[d][mn]['min'], 'c': pos[d][mn]['close']}
    for c in cs[:2]:
        _, vw = prof(d, c); row['vw' + ('N' if c == mn else 'F')] = round(vw, 1) if vw else None
    e = LT.get(d, {})
    if 'all' in e and 'near' in e:
        nc = e['near']['ct']; rest = [c for c in cs if c > nc]; fc = rest[0] if rest else None
        def g(k, side): return float(e[k].get(f'{side}_top10_trader_open_interest') or 0) if k in e else 0.0
        for book, side, key in ((BL, 'buy', 'L'), (BS, 'sell', 'S')):
            qn = g('near', side); qf = max(0.0, g('all', side) - qn - g('week', side))
            book.set(d, {nc: qn, **({fc: qf} if fc else {})})
            cn, _ = book.cost(nc); cf, _ = book.cost(fc) if fc else (None, 0); ca, qa = book.cost()
            row.update({f'q{key}N': qn, f'q{key}F': qf, f'c{key}N': cn and round(cn), f'c{key}F': cf and round(cf), f'c{key}': ca and round(ca)})
        row['nc'] = nc
    if d in FI:
        net = FI[d]; sg = (net > 0) - (net < 0)
        if sg != fsign: BF = Book(); fsign = sg
        oi = {c: float(pos[d][c].get('open_interest') or 0) for c in live[:2]}; tot = sum(oi.values()) or 1
        BF.set(d, {c: abs(net) * oi[c] / tot for c in live[:2]})
        cf, _ = BF.cost(); row.update({'fi': net, 'cFI': cf and round(cf)})
    # 主力底線：前十大交易人多單全部持倉的價位分布，由低往高累積到 20% 的價位（跌破＝八成主力多單套牢）
    # 回測（2025/6～2026/10）：前一天收在線上、當天碰到 30 次，10 天後平均 +3.8%、87% 收高
    if 'qLN' in row:
        v = pct_price(BL.lots(), 0.20); row['fl'] = v and round(v)
    days.append(row)

# 只輸出熱身期之後（約兩個半月，前面的部位起算前就存在，成本不準）
WARM = 50
out_days = days[WARM:]

# ── 回測：價格碰到成本線，當天收盤有沒有守住 ──
def evaluate(key, shift=1.0):
    n = h = sup = supn = res = resn = 0
    for i in range(1, len(out_days)):
        L = out_days[i - 1].get(key); p = out_days[i]
        if not L: continue
        L *= shift; c0 = out_days[i - 1]['c']
        if c0 > L and p['l'] <= L: supn += 1; sup += p['c'] > L
        elif c0 < L and p['h'] >= L: resn += 1; res += p['c'] < L
    n = supn + resn; h = sup + res
    return {'n': n, 'hold': round(h / n * 100, 1) if n else None, 'sup': [sup, supn], 'res': [res, resn]}
cl = [r['c'] for r in out_days]
for i, r in enumerate(out_days): r['_ma20'] = st.mean(cl[max(0, i - 19):i + 1])
stats = {k: {'line': evaluate(k), 'up1.5%': evaluate(k, 1.015), 'dn1.5%': evaluate(k, 0.985)} for k in ('cL', 'cS', 'cLN', 'cSN', 'cFI', '_ma20', 'fl')}
for r in out_days: r.pop('_ma20', None)
for k, v in stats.items(): print('backtest', k, v)

lots = lambda bk: [[k, round(v)] for k, v in sorted(bk.lots().items()) if v >= 1]
res = {
    'updated': (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M'),
    'bin': BIN, 'start': out_days[0]['d'] if out_days else None,
    'days': out_days,
    'dist': {'d': days[-1]['d'] if days else None, 'L': lots(BL), 'S': lots(BS), 'F': lots(BF), 'fsign': fsign},
    'stats': stats,
    'minuteDays': len(SESS), 'tradingDays': len(TD),
}
json.dump(res, open(f'{OUT}/cost.json', 'w'), ensure_ascii=False, separators=(',', ':'))
L = days[-1]
print('last', {k: L.get(k) for k in ('d', 'c', 'nc', 'qLN', 'qLF', 'qSN', 'qSF', 'cLN', 'cLF', 'cSN', 'cSF', 'cL', 'cS', 'fi', 'cFI', 'vwN', 'vwF')})
