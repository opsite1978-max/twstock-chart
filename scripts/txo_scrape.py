#!/usr/bin/env python3
"""
台指選擇權每日總 Gamma 曝險（GEX）歷史 → data/txo/history.json

現價用台指期近月日盤收盤；各合約由買賣權價格反推的遠期價整體平移，讓最近結算日的遠期價等於台指期收盤。

每個交易日：用當天收盤後的全部台指選擇權（各結算日）未平倉與結算價，
反推隱含波動率（Black-76），算出每個履約價的 Gamma 曝險，再整理成
  總 Gamma 曝險（在台指期收盤價）、零 Gamma（翻轉點）、看漲牆、看跌牆、
  最近結算日的最大痛點、未平倉賣權／買權比、價平隱含波動率，
並附上台指期近月當天的開高低收，網頁用來統計「負 Gamma 之後是不是比較容易大漲大跌」。

計算方式與 txo.html 網頁相同（買權記正、賣權記負，每點 50 元，指數變動 1% 的避險金額）。

  python3 scripts/txo_scrape.py update      補算缺少的交易日（預設往回約一年）

FinMind 憑證從環境變數 FINMIND_TOKEN 讀取（GitHub 加密密碼），程式不會印出或存檔。
"""
import json, os, re, sys, math, time, datetime, urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'txo')
HIST = os.path.join(OUT, 'history.json')
TW = datetime.timezone(datetime.timedelta(hours=8))
TOKEN = os.environ.get('FINMIND_TOKEN', '').strip()
API = 'https://api.finmindtrade.com/api/v4/data'
BACK_DAYS = int(os.environ.get('BACK_DAYS', '250'))
REQ_BUDGET = int(os.environ.get('REQ_BUDGET', '400'))
MULT = 50
LOG = []
NREQ = [0]


class RateLimited(Exception):
    pass


def clean(t):
    t = re.sub(r'"token_tail"\s*:\s*"[^"]*"', '"token_tail":"***"', str(t))
    if TOKEN:
        t = t.replace(TOKEN, '***').replace(TOKEN[-8:], '***')
    return t


def log(*a):
    line = clean(' '.join(str(x) for x in a))
    print(line, flush=True)
    LOG.append(datetime.datetime.now(TW).strftime('%m-%d %H:%M:%S ') + line)


def api(params):
    if NREQ[0] >= REQ_BUDGET:
        raise RateLimited('本次查詢次數用完')
    req = urllib.request.Request(API + '?' + urllib.parse.urlencode(params), headers={'Authorization': 'Bearer ' + TOKEN})
    for attempt in range(4):
        NREQ[0] += 1
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                j = json.loads(r.read().decode('utf-8'))
            if j.get('status') == 402:
                raise RateLimited('已達 FinMind 每小時查詢上限')
            return j.get('data') or []
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:200]
            if e.code in (402, 429) or 'upper limit' in body.lower():
                raise RateLimited('已達 FinMind 每小時查詢上限')
            time.sleep(5 * (attempt + 1))
        except RateLimited:
            raise
        except Exception:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError('request failed ' + params.get('dataset', ''))


# ───────── 選擇權數學（Black-76，利率視為 0） ─────────
SQ2PI = math.sqrt(2 * math.pi)
npdf = lambda x: math.exp(-x * x / 2) / SQ2PI
ncdf = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))


def b76(F, K, T, v, call):
    if v <= 0 or T <= 0:
        return max(0.0, F - K if call else K - F)
    sd = v * math.sqrt(T)
    d1 = (math.log(F / K) + sd * sd / 2) / sd
    d2 = d1 - sd
    return F * ncdf(d1) - K * ncdf(d2) if call else K * ncdf(-d2) - F * ncdf(-d1)


def implied_vol(price, F, K, T, call):
    intr = max(0.0, F - K if call else K - F)
    if not (price > intr + 0.05) or T <= 0:
        return None
    lo, hi = 0.005, 4.0
    if b76(F, K, T, hi, call) < price:
        return None
    for _ in range(60):
        m = (lo + hi) / 2
        if b76(F, K, T, m, call) > price:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2


def gamma(F, K, T, v):
    if not v or v <= 0 or T <= 0:
        return 0.0
    sd = v * math.sqrt(T)
    d1 = (math.log(F / K) + sd * sd / 2) / sd
    return npdf(d1) / (F * sd)


def gex_of(g, oi, F):
    return g * oi * MULT * F * F * 0.01


# ───────── 結算日 ─────────
def nth_weekday(y, m, wd, n):          # wd: 0=週一 … 2=週三 4=週五
    first = datetime.date(y, m, 1)
    day = 1 + ((wd - first.weekday()) % 7) + (n - 1) * 7
    return datetime.date(y, m, day).isoformat()


def contract_exp(code):
    m = re.fullmatch(r'(\d{4})(\d{2})(?:([WF])(\d))?', code)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    try:
        if not m.group(3):
            return nth_weekday(y, mo, 2, 3)
        return nth_weekday(y, mo, 2 if m.group(3) == 'W' else 4, int(m.group(4)))
    except ValueError:
        return None


def adjust_exp(exp, cal):
    if cal and exp <= cal[-1] and exp not in CALSET:
        for d in cal:
            if d > exp:
                return d
    return exp


CALSET = set()


def day_diff(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


# ───────── 單日計算 ─────────
def analyse_day(date, rows, P, cal):
    by = {}
    for r in rows:
        if r.get('trading_session') != 'position':
            continue
        code = str(r.get('contract_date') or '').strip()
        exp = contract_exp(code)
        if not exp:
            continue
        exp = adjust_exp(exp, cal)
        if exp <= date:
            continue
        K = float(r['strike_price'])
        e = by.setdefault(code, {'exp': exp, 'S': {}})
        s = e['S'].setdefault(K, {'K': K, 'coi': 0, 'poi': 0, 'cpx': 0.0, 'ppx': 0.0})
        px = float(r.get('settlement_price') or 0) or float(r.get('close') or 0)
        if r['call_put'] == 'call':
            s['coi'] = int(r.get('open_interest') or 0); s['cpx'] = px
        else:
            s['poi'] = int(r.get('open_interest') or 0); s['ppx'] = px
    # 先算每個合約由買賣權價格反推的遠期價，再整體平移，讓最近結算日的遠期價等於台指期收盤 P
    def parity(S):
        F, best = None, 1e18
        for s in S:
            if s['cpx'] > 0 and s['ppx'] > 0:
                d = abs(s['cpx'] - s['ppx'])
                if d < best:
                    best, F = d, s['K'] + s['cpx'] - s['ppx']
        return F
    off = 0.0
    if P:
        for code, e in sorted(by.items(), key=lambda kv: kv[1]['exp']):
            f0 = parity(sorted(e['S'].values(), key=lambda s: s['K']))
            if f0 is not None and sum(s['coi'] + s['poi'] for s in e['S'].values()) > 0:
                off = P - f0
                break
    exps = []
    for code, e in by.items():
        S = sorted(e['S'].values(), key=lambda s: s['K'])
        if sum(s['coi'] + s['poi'] for s in S) <= 0:
            continue
        T = max(0.15, day_diff(date, e['exp']) - 0.01) / 365
        F = parity(S)
        if F is None:
            continue
        F += off
        # 只對有未平倉、或價平附近的履約價反推波動率（節省時間）
        for s in S:
            s['iv'] = None
            if s['coi'] + s['poi'] == 0 and abs(s['K'] - F) > F * 0.03:
                continue
            call = s['K'] >= F
            px, opx = (s['cpx'], s['ppx']) if call else (s['ppx'], s['cpx'])
            iv = implied_vol(px, F, s['K'], T, call) if px > 0 else None
            if iv is None and opx > 0:
                iv = implied_vol(opx, F, s['K'], T, not call)
            s['iv'] = iv
        known = [s for s in S if s['iv'] is not None]
        if not known:
            continue
        for i, s in enumerate(S):
            if s['iv'] is not None:
                continue
            a = next((S[j] for j in range(i - 1, -1, -1) if S[j]['iv'] is not None), None)
            b = next((S[j] for j in range(i + 1, len(S)) if S[j]['iv'] is not None), None)
            s['iv'] = a['iv'] + (b['iv'] - a['iv']) * (s['K'] - a['K']) / (b['K'] - a['K']) if a and b else (a or b)['iv']
        for s in S:
            g = gamma(F, s['K'], T, s['iv'])
            s['gc'] = gex_of(g, s['coi'], F)
            s['gp'] = -gex_of(g, s['poi'], F)
        atm = min(S, key=lambda s: abs(s['K'] - F))
        exps.append({'code': code, 'exp': e['exp'], 'T': T, 'F': F, 'S': S, 'iv': atm['iv']})
    if not exps:
        return None
    exps.sort(key=lambda a: (a['exp'], a['code']))
    near = exps[0]
    Fref = near['F']
    if not P:
        P = Fref

    def total_at(x, lst):
        t = 0.0
        for a in lst:
            Fx = a['F'] * x / Fref
            for s in a['S']:
                net = s['coi'] - s['poi']
                if net:
                    t += gex_of(gamma(Fx, s['K'], a['T'], s['iv']), net, Fx)
        return t

    # 全部結算日合計：各履約價
    agg = {}
    for a in exps:
        for s in a['S']:
            x = agg.setdefault(s['K'], {'K': s['K'], 'gc': 0.0, 'gp': 0.0, 'coi': 0, 'poi': 0})
            x['gc'] += s['gc']; x['gp'] += s['gp']; x['coi'] += s['coi']; x['poi'] += s['poi']
    rows_all = sorted(agg.values(), key=lambda x: x['K'])
    above = [r for r in rows_all if r['K'] >= P] or rows_all
    below = [r for r in rows_all if r['K'] <= P] or rows_all
    cw = max(above, key=lambda r: r['gc'])['K']
    pw = min(below, key=lambda r: r['gp'])['K']
    # 零 Gamma：現價上下 10% 掃描，取最靠近現價的翻轉點
    N = 101
    xs = [P * 0.9 + P * 0.2 * i / (N - 1) for i in range(N)]
    ys = [total_at(x, exps) for x in xs]
    flip = None
    for i in range(1, N):
        y0, y1 = ys[i - 1], ys[i]
        if (y0 <= 0 < y1) or (y0 >= 0 > y1):
            x = xs[i - 1] + (xs[i] - xs[i - 1]) * (0 - y0) / (y1 - y0)
            if flip is None or abs(x - P) < abs(flip - P):
                flip = x
    # 最近結算日的最大痛點
    NS = near['S']
    pain, pv = None, 1e30
    for x in NS:
        t = sum(r['coi'] * (x['K'] - r['K']) for r in NS if x['K'] > r['K']) + sum(r['poi'] * (r['K'] - x['K']) for r in NS if x['K'] < r['K'])
        if t < pv:
            pv, pain = t, x['K']
    oic = sum(r['coi'] for r in rows_all); oip = sum(r['poi'] for r in rows_all)
    # 成交量賣權／買權比（日盤＋這一天的夜盤，全部合約）
    vc = sum(int(r.get('volume') or 0) for r in rows if r.get('call_put') == 'call')
    vp = sum(int(r.get('volume') or 0) for r in rows if r.get('call_put') == 'put')
    return {
        'gex': round(total_at(P, exps)), 'gexNear': round(total_at(P, [near])),
        'flip': round(flip) if flip else None, 'cw': cw, 'pw': pw, 'pain': pain,
        'pcr': round(oip / oic, 3) if oic else None, 'pcrV': round(vp / vc, 3) if vc else None, 'iv': round(near['iv'] * 100, 2) if near['iv'] else None,
        'near': near['code'], 'nearExp': near['exp'], 'oi': oic + oip, 'F': round(Fref, 1),
    }


def fut_bars(start, end):
    rows = api({'dataset': 'TaiwanFuturesDaily', 'data_id': 'TX', 'start_date': start, 'end_date': end})
    by = {}
    for r in rows:
        c = str(r.get('contract_date') or '').strip()
        if r.get('trading_session') != 'position' or not re.fullmatch(r'\d{6}', c) or not (r.get('close') or 0) > 0:
            continue
        cur = by.get(r['date'])
        if not cur or (r.get('volume') or 0) > (cur.get('volume') or 0):
            by[r['date']] = r
    return {d: [r['open'], r['max'], r['min'], r['close'], str(r['contract_date']).strip()] for d, r in by.items()}


def cmd_update():
    if not TOKEN:
        log('沒有 FINMIND_TOKEN'); return
    os.makedirs(OUT, exist_ok=True)
    hist = {}
    if os.path.exists(HIST):
        for x in json.load(open(HIST, encoding='utf-8')).get('days', []):
            hist[x['d']] = x
    today = datetime.datetime.now(TW).date().isoformat()
    cal_rows = api({'dataset': 'TaiwanStockTradingDate', 'start_date': (datetime.date.today() - datetime.timedelta(days=int(BACK_DAYS * 1.6) + 30)).isoformat()})
    cal = sorted({r['date'] for r in cal_rows})
    CALSET.update(cal)
    past = [d for d in cal if d <= today][-BACK_DAYS:]
    fut = fut_bars(past[0], today)
    log('trading days', len(past), 'have', len(hist), 'fut days', len(fut))
    todo = [d for d in past if d not in hist or 'pcrV' not in hist[d] or d == past[-1] and not hist[d].get('final')]
    todo.sort(reverse=True)                          # 先算最近的
    done = 0
    try:
        for d in todo:
            rows = api({'dataset': 'TaiwanOptionDaily', 'data_id': 'TXO', 'start_date': d, 'end_date': d})
            if not any(r.get('trading_session') == 'position' and (r.get('open_interest') or 0) > 0 for r in rows):
                log('no data yet', d); continue
            f = fut.get(d)
            res = analyse_day(d, rows, f[3] if f else None, cal)   # 現價＝台指期近月日盤收盤
            if not res:
                log('cannot analyse', d); continue
            rec = {'d': d}
            if f:
                rec.update({'o': f[0], 'h': f[1], 'l': f[2], 'c': f[3], 'fc': f[4]})
            rec.update(res)
            rec['ix'] = res.pop('F', None) if 'F' in res else None
            rec.pop('F', None)
            rec['final'] = d < today or datetime.datetime.now(TW).hour >= 17
            hist[d] = rec
            done += 1
            if done % 20 == 0:
                log('progress', done, d)
                save(hist)
    except RateLimited as e:
        log('STOP', e)
    # 補上已存日期但缺期貨價的
    for d, x in hist.items():
        if 'c' not in x and d in fut:
            f = fut[d]; x.update({'o': f[0], 'h': f[1], 'l': f[2], 'c': f[3], 'fc': f[4]})
    save(hist)
    log('done', done, 'requests', NREQ[0], 'total days', len(hist))


def cmd_chips():
    """台指期籌碼：三大法人、前十大交易人、小台與微台散戶多空比 → data/txo/chips.json"""
    if not TOKEN:
        log('沒有 FINMIND_TOKEN'); return
    today = datetime.datetime.now(TW).date()
    start = (today - datetime.timedelta(days=int(BACK_DAYS * 1.5) + 10)).isoformat()
    W = {'TX': 1.0, 'MTX': 0.25, 'TMF': 0.05}           # 換算成大台口數
    inst = {}
    for fid in W:
        for r in api({'dataset': 'TaiwanFuturesInstitutionalInvestors', 'data_id': fid, 'start_date': start}):
            net = (r.get('long_open_interest_balance_volume') or 0) - (r.get('short_open_interest_balance_volume') or 0)
            inst.setdefault(r['date'], {}).setdefault(fid, {})[r['institutional_investors']] = net
    oi = {}
    for fid in ('MTX', 'TMF'):
        for r in api({'dataset': 'TaiwanFuturesDaily', 'data_id': fid, 'start_date': start}):
            c = str(r.get('contract_date') or '')
            if r.get('trading_session') != 'position' or '/' in c:
                continue
            oi.setdefault(r['date'], {}).setdefault(fid, 0)
            oi[r['date']][fid] += int(r.get('open_interest') or 0)
    big = {}
    for r in api({'dataset': 'TaiwanFuturesOpenInterestLargeTraders', 'data_id': 'TX', 'start_date': start}):
        if r.get('contract_type') != 'all':
            continue
        big[r['date']] = [r['buy_top10_trader_open_interest'] - r['sell_top10_trader_open_interest'],
                          r['buy_top5_trader_open_interest'] - r['sell_top5_trader_open_interest'],
                          r['buy_top10_specific_open_interest'] - r['sell_top10_specific_open_interest'], r['market_open_interest']]
    days = []
    for d in sorted(inst):
        x = inst[d]; rec = {'d': d}
        for who, key in (('外資', 'fi'), ('投信', 'it'), ('自營商', 'dl')):
            rec[key] = round(sum(x.get(f, {}).get(who, 0) * w for f, w in W.items()), 1)
        for fid, key in (('MTX', 'mtx'), ('TMF', 'tmf')):
            tot = oi.get(d, {}).get(fid)
            inst_net = sum(x.get(fid, {}).values())
            if tot:
                rec[key + 'R'] = round(-inst_net / tot * 100, 2)        # 散戶多空比（%）：散戶多單減空單，占全市場未平倉
                rec[key + 'OI'] = tot
        rec['fiTX'] = x.get('TX', {}).get('外資')
        if d in big:
            rec['t10'], rec['t5'], rec['s10'], rec['txOI'] = big[d]
        days.append(rec)
    json.dump({'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'), 'days': days},
              open(os.path.join(OUT, 'chips.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    log('chips days', len(days), 'requests', NREQ[0])


def save(hist):
    days = [hist[d] for d in sorted(hist)]
    json.dump({'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'), 'days': days},
              open(HIST, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    open(os.path.join(OUT, '_lastrun.txt'), 'w', encoding='utf-8').write('\n'.join(LOG[-200:]) + '\n')


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'update':
        cmd_update()
    elif len(sys.argv) > 1 and sys.argv[1] == 'chips':
        cmd_chips()
    else:
        print(__doc__)
