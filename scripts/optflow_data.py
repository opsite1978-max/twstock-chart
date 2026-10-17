# 選擇權多空力道：主力（外資）與散戶（非法人），每天晚上在 GitHub 計算 → data/ctrl/optflow.json
#
# 方向曝險＝（買權淨部位）−（賣權淨部位），用未平倉「契約金額」加權（權利金越貴、越接近價平，影響越大）
#   做多力道：買進買權、賣出賣權；做空力道：買進賣權、賣出買權
# 原始力道＝方向曝險 ÷ 這群人四種部位金額合計（−1～+1），再依歷史分布放大成 −100～+100（0＝中性）
# 主力＝外資；散戶＝全市場 − 三大法人（所有非法人交易人）；另外附前十大交易人（只有口數）
import os, json, time, datetime, urllib.request, urllib.parse, collections, statistics as st

TOKEN = os.environ.get('FINMIND_TOKEN', '')
OUT = 'data/ctrl'; CACHE = f'{OUT}/opt_mkt_cache.json'
START = os.environ.get('OPT_START', '2024-01-02')
BUDGET = int(os.environ.get('REQ_BUDGET', '800')); NREQ = [0]
def api(params):
    if NREQ[0] >= BUDGET: raise RuntimeError('budget')
    NREQ[0] += 1; time.sleep(0.6)
    u = 'https://api.finmindtrade.com/api/v4/data?' + urllib.parse.urlencode(dict(params, token=TOKEN))
    err = None
    for k in range(3):
        try:
            with urllib.request.urlopen(u, timeout=240) as r: j = json.loads(r.read().decode())
            if j.get('status') not in (200, None): raise RuntimeError(f"status {j.get('status')} {str(j.get('msg'))[:80]}")
            return j.get('data') or []
        except Exception as e: err = e; time.sleep(4 * (k + 1))
    raise RuntimeError(f"{params.get('dataset')} {params.get('start_date')}: {str(err)[:120]}")
TODAY = (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).date().isoformat()
os.makedirs(OUT, exist_ok=True)
cache = {}
if os.path.exists(CACHE):
    try: cache = json.load(open(CACHE))
    except Exception: cache = {}

inst = api({'dataset': 'TaiwanOptionInstitutionalInvestors', 'data_id': 'TXO', 'start_date': START})
lt = api({'dataset': 'TaiwanOptionOpenInterestLargeTraders', 'data_id': 'TXO', 'start_date': START})
ix = api({'dataset': 'TaiwanStockPrice', 'data_id': 'TAIEX', 'start_date': START})
IX = {r['date']: r['close'] for r in ix if r.get('close')}
if lt: print('LT keys', sorted(lt[0].keys()))
CP = lambda v: 'C' if str(v).strip().lower() in ('call', '買權', 'c') else 'P' if str(v).strip().lower() in ('put', '賣權', 'p') else None
I = collections.defaultdict(lambda: collections.defaultdict(dict))     # 日 → 法人 → {LC,SC,LP,SP 金額, 口數}
for r in inst:
    cp = CP(r.get('call_put'));
    if not cp: continue
    e = I[r['date']][r['institutional_investors']]
    e['L' + cp] = float(r.get('long_open_interest_balance_amount') or 0); e['S' + cp] = float(r.get('short_open_interest_balance_amount') or 0)
    e['l' + cp] = float(r.get('long_open_interest_balance_volume') or 0); e['s' + cp] = float(r.get('short_open_interest_balance_volume') or 0)
days = sorted(d for d in I if d >= START)
print('inst days', len(days), days[0], days[-1])

# 全市場未平倉口數與金額（每天一次，存快取）
done = 0
try:
    for d in sorted(days, reverse=True):                                    # 先算最近的
        if d in cache and d < (datetime.date.fromisoformat(TODAY) - datetime.timedelta(days=3)).isoformat(): continue
        rows = api({'dataset': 'TaiwanOptionDaily', 'data_id': 'TXO', 'start_date': d, 'end_date': d})
        a = {'oC': 0, 'oP': 0, 'aC': 0.0, 'aP': 0.0}
        for x in rows:
            if x.get('trading_session') != 'position': continue
            cp = CP(x.get('call_put')); oi = x.get('open_interest') or 0
            if not cp or not oi: continue
            a['o' + cp] += oi; a['a' + cp] += oi * (x.get('settlement_price') or 0) * 50 / 1000
        if a['oC'] and a['oP']: cache[d] = a
        done += 1
        if done % 50 == 0: print('fetched', done, d); json.dump(cache, open(CACHE, 'w'), separators=(',', ':'))
except RuntimeError as e: print('stop:', e)
json.dump(cache, open(CACHE, 'w'), separators=(',', ':'))
print('market days fetched', done, 'cached', len(cache), 'requests', NREQ[0])

# 前十大交易人（口數）：全部月份
T10 = collections.defaultdict(dict)
for r in lt:
    if str(r.get('contract_type', '')).strip() != 'all': continue
    cp = None
    for k in ('put_call', 'call_put', 'option_type', 'name'):
        if k in r and CP(r[k]): cp = CP(r[k]); break
    if not cp: continue
    T10[r['date']][cp] = r

def raw(LC, SC, LP, SP):
    g = LC + SC + LP + SP
    return ((LC - SC) - (LP - SP)) / g if g > 0 else None

rows = []
for d in days:
    e = I[d]; f = e.get('外資', {}); m = cache.get(d)
    if not f or 'LC' not in f or 'LP' not in f: continue
    row = {'d': d, 'ix': IX.get(d)}
    row['fr'] = raw(f['LC'], f['SC'], f['LP'], f['SP'])
    row['fLC'], row['fSC'], row['fLP'], row['fSP'] = (round(f[k]) for k in ('LC', 'SC', 'LP', 'SP'))
    row['fnC'] = f['lC'] - f['sC']; row['fnP'] = f['lP'] - f['sP']           # 外資買權、賣權淨口數
    if m:
        tot = {k: sum(v.get(k, 0) for v in e.values()) for k in ('LC', 'SC', 'LP', 'SP')}
        R = {'LC': m['aC'] - tot['LC'], 'SC': m['aC'] - tot['SC'], 'LP': m['aP'] - tot['LP'], 'SP': m['aP'] - tot['SP']}
        if min(R.values()) >= 0: row['rr'] = raw(R['LC'], R['SC'], R['LP'], R['SP'])
        row['oC'], row['oP'] = m['oC'], m['oP']
    t = T10.get(d, {})
    if 'C' in t and 'P' in t:
        g = lambda cp, side: float(t[cp].get(f'{side}_top10_trader_open_interest') or 0)
        row['tr'] = raw(g('C', 'buy'), g('C', 'sell'), g('P', 'buy'), g('P', 'sell'))
    rows.append(row)

# 放大成 −100～+100：歷史 |原始力道| 第 95 百分位 → 80
def scale(key, out):
    v = sorted(abs(r[key]) for r in rows if r.get(key) is not None)
    if not v: return None
    p95 = v[int(len(v) * 0.95)] or 1e-9
    for r in rows:
        if r.get(key) is not None: r[out] = round(max(-100, min(100, r[key] / p95 * 80)), 1)
    return p95
P95 = {'f': scale('fr', 'F'), 'r': scale('rr', 'R'), 't': scale('tr', 'T')}

# 回測：力道和未來 5、10 天加權指數漲跌
def back(key):
    res = {}
    px = [r['ix'] for r in rows]
    for n in (5, 10):
        pairs = [(rows[i][key], (px[i + n] / px[i] - 1) * 100) for i in range(len(rows) - n) if rows[i].get(key) is not None and px[i] and px[i + n]]
        if len(pairs) < 30: continue
        xs, ys = zip(*pairs); mx, my = st.mean(xs), st.mean(ys)
        cor = sum((a - mx) * (b - my) for a, b in pairs) / (len(pairs) * st.pstdev(xs) * st.pstdev(ys))
        bucket = {}
        for lo, hi, lab in ((-101, -50, '≤−50'), (-50, 0, '−50～0'), (0, 50, '0～50'), (50, 101, '≥50')):
            s = [y for x, y in pairs if lo < x <= hi]
            bucket[lab] = {'n': len(s), 'avg': round(st.mean(s), 2) if s else None, 'up': round(sum(1 for y in s if y > 0) / len(s) * 100) if s else None}
        res[str(n)] = {'cor': round(cor, 3), 'n': len(pairs), 'base': round(my, 2), 'buckets': bucket}
    return res
stats = {'F': back('F'), 'R': back('R'), 'T': back('T')}
for k, v in stats.items(): print('backtest', k, json.dumps(v, ensure_ascii=False))
json.dump({'updated': (datetime.datetime.utcnow() + datetime.timedelta(hours=8)).strftime('%Y-%m-%d %H:%M'), 'p95': P95, 'stats': stats,
           'days': [{k: v for k, v in r.items() if k in ('d', 'ix', 'F', 'R', 'T', 'fr', 'rr', 'tr', 'fLC', 'fSC', 'fLP', 'fSP', 'fnC', 'fnP', 'oC', 'oP')} for r in rows]},
          open(f'{OUT}/optflow.json', 'w'), ensure_ascii=False, separators=(',', ':'))
print('last', rows[-1] if rows else None)
