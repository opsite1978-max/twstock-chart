#!/usr/bin/env python3
"""
台指選擇權多空力道（TXO FORCE）原始資料 → data/force/

每一個日曆日的台指選擇權逐筆成交（FinMind TaiwanOptionTick，含夜盤）：
  1. 用「價格跳動法」推估每筆是主動買還是主動賣（比上一筆同一檔高＝買方追價，低＝賣方殺價，
     一樣價＝沿用上一筆的方向；當天第一筆無法判斷）。FinMind 逐筆資料沒有買賣報價，所以只能推估。
  2. 每筆乘上該檔選擇權的 Delta（用「前一次結算價」反推的隱含波動率＋當分鐘的台指期價格算，不偷看未來），
     換算成「等值大台口數」：主動買買權、主動賣賣權＝正；主動賣買權、主動買賣權＝負。
  3. 每分鐘加總成：淨 Delta 力道、總 Delta 量、買權主動買／主動賣口數、賣權主動買／主動賣口數、無法判斷口數，
     並附台指期（成交量最大的單一月份，和看盤軟體一樣不扣價差）的開高低收與台指選擇權波動率指數。

  逐筆資料每筆成交記兩次（買賣雙方），所以用當天日盤逐筆總量 ÷ 日成交資訊的成交量算出倍數再除回來。

輸出：
  data/force/m1/YYYY-MM-DD.json   每個日曆日的 1 分鐘資料（完成後不再改動）
  data/force/m5/YYYY-MM.json      依交易日、日盤／夜盤整理好的 5 分鐘資料（網頁主要讀這個）
  data/force/daily.json           每個交易日的未平倉／成交量／隱含波動率／偏斜等結構資料
  data/force/strikes.json         最新一個交易日各結算日、各履約價的未平倉、增減、Gamma 加權、最大痛點
  data/force/inst.json            三大法人期貨、選擇權部位
  data/force/index.json           檔案清單、交易日曆、資料品質統計

FinMind 憑證從環境變數 FINMIND_TOKEN 讀取（GitHub 加密密碼），程式不會印出或存檔。
"""
import json, os, re, sys, math, time, datetime, urllib.request, urllib.error, urllib.parse, statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'force')
M1 = os.path.join(OUT, 'm1')
M5 = os.path.join(OUT, 'm5')
TW = datetime.timezone(datetime.timedelta(hours=8))
TOKEN = os.environ.get('FINMIND_TOKEN', '').strip()
API = 'https://api.finmindtrade.com/api/v4/data'
START = os.environ.get('FORCE_START', '2026-01-05')
REQ_BUDGET = int(os.environ.get('REQ_BUDGET', '900'))
MAX_FILES = int(os.environ.get('MAX_FILES', '60'))
MAX_MIN = float(os.environ.get('MAX_MINUTES', '95'))
T_START = time.time()
TXEQ = 50 / 200          # 一口台指選擇權（每點 50 元）換成大台（每點 200 元）
NREQ = [0]
LOG = []
COLS = ['t', 'o', 'h', 'l', 'c', 'net', 'gross', 'cb', 'cs', 'pb', 'ps', 'unk', 'vix', 'v']
VER = 2                  # 2：台指期用「當下的近月」、晚上夜盤 18:00 前不用當天結算價
OPT_AVAIL = 18 * 60      # 選擇權日成交（結算價）保守視為台灣時間 18:00 才取得
QUOTA_MAX = int(os.environ.get('QUOTA_MAX', '3000'))   # 本小時 FinMind 已用超過這個數字就不跑，留給其他功能
MODE = (sys.argv[1] if len(sys.argv) > 1 else 'update')


class RateLimited(Exception):
    pass


def quota_used():
    """查本小時已用的 FinMind 次數（查詢本身不算次數）；查不到回傳 None"""
    try:
        req = urllib.request.Request('https://api.web.finmindtrade.com/v2/user_info', headers={'Authorization': 'Bearer ' + TOKEN})
        with urllib.request.urlopen(req, timeout=30) as r:
            j = json.loads(r.read())
        return int(j.get('user_count') or 0), int(j.get('api_request_limit_hour') or j.get('api_request_limit') or 0)
    except Exception:
        return None


def quota_ok():
    q = quota_used()
    if q is None:
        log('查不到本小時用量，保守起見只用很少的次數')
        return min(REQ_BUDGET, 40)
    used, lim = q
    log('本小時 FinMind 已用', used, '／上限', lim)
    if used >= QUOTA_MAX:
        log('超過保護線', QUOTA_MAX, '，這次不跑，留給其他功能')
        return 0
    return min(REQ_BUDGET, max(0, QUOTA_MAX - used))


def clean(t):
    t = str(t)
    if TOKEN:
        t = t.replace(TOKEN, '***')
    return t


def log(*a):
    line = clean(' '.join(str(x) for x in a))
    print(line, flush=True)
    LOG.append(datetime.datetime.now(TW).strftime('%m-%d %H:%M:%S ') + line)


def api(params, timeout=300):
    if NREQ[0] >= REQ_BUDGET:
        raise RateLimited('本次查詢次數用完')
    req = urllib.request.Request(API + '?' + urllib.parse.urlencode(params), headers={'Authorization': 'Bearer ' + TOKEN})
    err = None
    for attempt in range(4):
        NREQ[0] += 1
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                j = json.loads(r.read())
            if j.get('status') == 402:
                raise RateLimited('已達 FinMind 每小時查詢上限')
            if j.get('status') not in (200, None):
                raise RuntimeError('status %s %s' % (j.get('status'), str(j.get('msg'))[:80]))
            return j.get('data') or []
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:200]
            if e.code in (402, 429) or 'upper limit' in body.lower():
                raise RateLimited('已達 FinMind 每小時查詢上限')
            err = 'HTTP %s %s' % (e.code, body)
            time.sleep(5 * (attempt + 1))
        except RateLimited:
            raise
        except Exception as e:
            err = e
            time.sleep(5 * (attempt + 1))
    raise RuntimeError('request failed %s %s: %s' % (params.get('dataset'), params.get('start_date'), clean(err)[:160]))


# ───────── 選擇權數學（Black-76，利率視為 0） ─────────
SQ2PI = math.sqrt(2 * math.pi)
SQ2 = math.sqrt(2)


def ncdf(x):
    return 0.5 * (1 + math.erf(x / SQ2))


def npdf(x):
    return math.exp(-x * x / 2) / SQ2PI


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
    for _ in range(55):
        m = (lo + hi) / 2
        if b76(F, K, T, m, call) > price:
            hi = m
        else:
            lo = m
    return (lo + hi) / 2


def d1_of(F, K, T, v):
    sd = v * math.sqrt(T)
    return (math.log(F / K) + sd * sd / 2) / sd, sd


def delta(F, K, T, v, call):
    if v <= 0 or T <= 0:
        return (1.0 if F > K else 0.0) if call else (-1.0 if F < K else 0.0)
    d1, _ = d1_of(F, K, T, v)
    return ncdf(d1) if call else ncdf(d1) - 1


def gamma(F, K, T, v):
    if not v or v <= 0 or T <= 0:
        return 0.0
    d1, sd = d1_of(F, K, T, v)
    return npdf(d1) / (F * sd)


# ───────── 結算日 ─────────
CAL = []
CALSET = set()


def nth_weekday(y, m, wd, n):
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
            e = nth_weekday(y, mo, 2, 3)
        else:
            e = nth_weekday(y, mo, 2 if m.group(3) == 'W' else 4, int(m.group(4)))
    except ValueError:
        return None
    # 遇到休市順延到下一個交易日
    if CAL and e <= CAL[-1] and e not in CALSET:
        for d in CAL:
            if d > e:
                return d
    return e


def prev_td(x):
    p = None
    for d in CAL:
        if d >= x:
            break
        p = d
    return p


def next_td(x):
    for d in CAL:
        if d > x:
            return d
    # 日曆之後：往後找下一個平日
    d = datetime.date.fromisoformat(x) + datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d.isoformat()


def ts(date, minute):
    """台灣時間的日期＋分鐘 → 秒（把台灣時間當成 UTC 存，網頁直接用 UTC 函式讀就是台灣時間）"""
    d = datetime.date.fromisoformat(date)
    return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp()) + minute * 60


def exp_ts(exp):
    return ts(exp, 13 * 60 + 30)


YEAR_S = 365 * 86400


# ───────── 結算價 → 各合約的隱含波動率曲線 ─────────
OPT_DAILY = {}
SMILE = {}
FUT_REF = {}


def opt_daily(d):
    if d not in OPT_DAILY:
        OPT_DAILY[d] = api({'dataset': 'TaiwanOptionDaily', 'data_id': 'TXO', 'start_date': d, 'end_date': d})
    return OPT_DAILY[d]


def chain(rows):
    """日盤資料整理成 合約 → 履約價 → 買賣權"""
    by = {}
    for r in rows:
        if r.get('trading_session') != 'position':
            continue
        code = str(r.get('contract_date') or '').strip()
        if not re.fullmatch(r'\d{6}(?:[WF]\d)?', code):
            continue
        K = float(r['strike_price'])
        s = by.setdefault(code, {}).setdefault(K, {'K': K, 'coi': 0, 'poi': 0, 'cpx': 0.0, 'ppx': 0.0, 'cv': 0, 'pv': 0})
        px = float(r.get('settlement_price') or 0) or float(r.get('close') or 0)
        if str(r.get('call_put')).lower() in ('call', '買權', 'c'):
            s['coi'] = int(r.get('open_interest') or 0); s['cpx'] = px; s['cv'] = int(r.get('volume') or 0)
        else:
            s['poi'] = int(r.get('open_interest') or 0); s['ppx'] = px; s['pv'] = int(r.get('volume') or 0)
    return by


def build_smile(d):
    """d 日收盤結算價 → {合約: {exp, F, Ks, ivs}}；之後的逐筆用這條曲線算 Delta（不偷看未來）"""
    if d in SMILE:
        return SMILE[d]
    rows = opt_daily(d)
    out = {}
    t_set = ts(d, 13 * 60 + 45)
    for code, S in chain(rows).items():
        exp = contract_exp(code)
        if not exp or exp <= d:
            continue
        T = max(600, exp_ts(exp) - t_set) / YEAR_S
        L = sorted(S.values(), key=lambda s: s['K'])
        F, best = None, 1e18
        for s in L:
            if s['cpx'] > 0 and s['ppx'] > 0:
                g = abs(s['cpx'] - s['ppx'])
                if g < best:
                    best, F = g, s['K'] + s['cpx'] - s['ppx']
        if F is None:
            continue
        Ks, ivs = [], []
        for s in L:
            if abs(s['K'] / F - 1) > 0.2:
                continue
            call = s['K'] >= F
            px, opx = (s['cpx'], s['ppx']) if call else (s['ppx'], s['cpx'])
            iv = implied_vol(px, F, s['K'], T, call) if px > 0 else None
            if iv is None and opx > 0:
                iv = implied_vol(opx, F, s['K'], T, not call)
            if iv is not None and 0.03 < iv < 2.5:
                Ks.append(s['K']); ivs.append(iv)
        if len(Ks) < 3:
            continue
        out[code] = {'exp': exp, 'F': F, 'Ks': Ks, 'ivs': ivs, 'T': T}
    SMILE[d] = out
    return out


def iv_at(sm, K):
    Ks, ivs = sm['Ks'], sm['ivs']
    if K <= Ks[0]:
        return ivs[0]
    if K >= Ks[-1]:
        return ivs[-1]
    lo, hi = 0, len(Ks) - 1
    while hi - lo > 1:
        m = (lo + hi) // 2
        if Ks[m] <= K:
            lo = m
        else:
            hi = m
    a, b = Ks[lo], Ks[hi]
    return ivs[lo] + (ivs[hi] - ivs[lo]) * (K - a) / (b - a) if b > a else ivs[lo]


def fut_daily_all(start, end):
    """台指期每天日盤收盤後的近月收盤（結算日當天用下一個月份），和結算價同時取得，算遠期價縮放用"""
    rows = api({'dataset': 'TaiwanFuturesDaily', 'data_id': 'TX', 'start_date': start, 'end_date': end})
    best = {}
    for r in rows:
        c = str(r.get('contract_date') or '').strip()
        if r.get('trading_session') != 'position' or not re.fullmatch(r'\d{6}', c) or not (r.get('close') or 0) > 0:
            continue
        e = contract_exp(c)
        if not e or e <= r['date']:
            continue
        cur = best.get(r['date'])
        if not cur or c < str(cur.get('contract_date')).strip():
            best[r['date']] = r
    return {d: float(r['close']) for d, r in best.items()}


# ───────── 單一日曆日的逐筆 → 每分鐘 ─────────
def session_windows(x):
    """這個日曆日有哪些交易時段（分鐘，含頭含尾）：凌晨夜盤、日盤、晚上夜盤"""
    is_td = x in CALSET
    w = []
    if is_td or (prev_td(x) and datetime.date.fromisoformat(x).weekday() == 5 and prev_td(x) == (datetime.date.fromisoformat(x) - datetime.timedelta(days=1)).isoformat()):
        w.append((0, 300))        # 00:00–05:00 屬於前一晚開始的夜盤
    if is_td:
        w.append((525, 825))      # 08:45–13:45 日盤
        w.append((900, 1439))     # 15:00–24:00 夜盤前半（屬於下一個交易日）
    return w


def near_month(codes, x, m):
    """日曆日 x、第 m 分鐘當下的近月：還沒結算的最小月份（只用當下就知道的結算日，不看整段成交量）"""
    t = ts(x, m)
    best = None
    for c in codes:
        e = contract_exp(c)
        if e and exp_ts(e) > t and (best is None or c < best):
            best = c
    return best


def tx_minutes(x):
    """台指期 1 分 K：每一分鐘取當下的近月（和看盤軟體一樣，不扣價差）"""
    rows = api({'dataset': 'TaiwanFuturesKBar', 'data_id': 'TX', 'start_date': x, 'end_date': x})
    by = {}
    for r in rows:
        c = str(r.get('contract_date') or '').strip()
        if not re.fullmatch(r'\d{6}', c):
            continue
        hh, mm = str(r['minute'])[:2], str(r['minute'])[3:5]
        m = int(hh) * 60 + int(mm)
        by.setdefault(c, {})[m] = (float(r['open']), float(r['high']), float(r['low']), float(r['close']), float(r.get('volume') or 0))
    out = {}
    codes = list(by)
    for lo, hi in session_windows(x):
        for m in range(lo, hi + 1):
            c = near_month(codes, x, m)
            if c and m in by[c]:
                out[m] = by[c][m]
    return out


def vix_minutes(x):
    if x < '2026-03-01':
        return {}
    try:
        rows = api({'dataset': 'TaiwanOptionVix', 'start_date': x, 'end_date': x})
    except RateLimited:
        raise
    except Exception as e:
        log('vix fail', x, e)
        return {}
    out = {}
    for r in rows:
        t = str(r.get('time') or '')
        if len(t) >= 5 and r.get('vix'):
            out[int(t[:2]) * 60 + int(t[3:5])] = float(r['vix'])
    return out


def process_file(x, fut_ref, dup_hist):
    windows = session_windows(x)
    if not windows:
        return None
    rows = api({'dataset': 'TaiwanOptionTick', 'data_id': 'TXO', 'start_date': x}, timeout=600)
    if not rows:
        log('no ticks', x)
        return None
    tx = tx_minutes(x)
    if not tx:
        log('no futures kbar', x)
        return None
    vix = vix_minutes(x)
    # 每個時段用哪一天的結算價（都是這筆成交當下已經公布的）
    pa = prev_td(x)                      # 凌晨夜盤、日盤：前一個交易日收盤
    sm_a = build_smile(pa) if pa else {}
    ref_a = fut_ref.get(pa)
    sm_b, ref_b = sm_a, ref_a
    if x in CALSET:                      # 晚上夜盤：今天日盤收盤已經公布
        try:
            sm_b = build_smile(x) or sm_a
            ref_b = fut_ref.get(x) or ref_a
        except RateLimited:
            raise
        except Exception as e:
            log('smile fail', x, e)
    if not sm_a or not ref_a:
        log('no smile for', x, pa)
        return None
    # 台指期每分鐘價格（沒成交的分鐘沿用上一分鐘）
    allmin = []
    for lo, hi in windows:
        allmin.extend(range(lo, hi + 1))
    px = {}
    last = None
    first_c = None
    for m in sorted(tx):
        first_c = tx[m][0]
        break
    for m in allmin:
        if m in tx:
            last = tx[m][3]
        px[m] = last if last is not None else first_c
    # 依時間排序（同一秒保持原順序）
    rows.sort(key=lambda r: r['date'])
    acc = {}
    state = {}
    dcache = {}
    vol_day = 0
    n_cls = n_all = n_fb = 0
    win_of = {}
    for lo, hi in windows:
        for m in range(lo, hi + 1):
            win_of[m] = (lo, hi)
    for r in rows:
        t = r['date']
        m = int(t[11:13]) * 60 + int(t[14:16])
        if m not in win_of:
            continue
        v = r.get('volume') or 0
        if v <= 0:
            continue
        code = str(r.get('contract_date') or '').strip()
        K = float(r['ExercisePrice'])
        call = r['PutCall'] == 'C'
        p = float(r['price'])
        key = (code, K, call)
        st = state.get(key)
        side = 0
        if st:
            if p > st[0]:
                side = 1
            elif p < st[0]:
                side = -1
            else:
                side = st[1]
        state[key] = (p, side)
        if 525 <= m <= 825:
            vol_day += v
        a = acc.get(m)
        if a is None:
            a = acc[m] = [0.0, 0.0, 0, 0, 0, 0, 0]
        n_all += v
        if side == 0:
            a[6] += v
            continue
        n_cls += v
        if call:
            a[2 if side > 0 else 3] += v
        else:
            a[4 if side > 0 else 5] += v
        dk = (code, K, call, m)
        dl = dcache.get(dk)
        if dl is None:
            night_b = m >= OPT_AVAIL
            sm, ref = (sm_b, ref_b) if night_b else (sm_a, ref_a)
            c = sm.get(code)
            fb = False
            if c is None:
                # 新掛牌的週選：借用到期日最接近的合約的波動率曲線
                exp = contract_exp(code)
                if exp and sm:
                    c = min(sm.values(), key=lambda q: abs(day_diff(q['exp'], exp)))
                    fb = True
            if c is None or not px.get(m):
                dl = 0.0
            else:
                exp = contract_exp(code) or c['exp']
                T = max(600, exp_ts(exp) - (ts(x, m) + 30)) / YEAR_S
                F = c['F'] * px[m] / ref
                dl = delta(F, K, T, iv_at(c, K), call)
            dcache[dk] = dl
            if fb:
                dcache[('fb',) + dk] = 1
        if ('fb',) + dk in dcache:
            n_fb += v
        a[0] += side * dl * v * TXEQ
        a[1] += abs(dl) * v * TXEQ
    # 逐筆每筆記兩次：用日盤總量對照日成交資訊算倍數
    dup = None
    if x in CALSET:
        try:
            dv = sum(int(r.get('volume') or 0) for r in opt_daily(x) if r.get('trading_session') == 'position')
            if dv > 0 and vol_day > 0:
                dup = vol_day / dv
        except RateLimited:
            raise
        except Exception as e:
            log('daily volume fail', x, e)
    used = dup if dup and 1.4 <= dup <= 2.6 else (statistics.median(dup_hist[-20:]) if dup_hist else 2.0)
    if dup and 1.4 <= dup <= 2.6:
        dup_hist.append(dup)
    bars = []
    last_c = None
    for m in allmin:
        b = tx.get(m)
        a = acc.get(m)
        if b is None and a is None and last_c is None:
            continue
        if b is None:
            if last_c is None:
                continue
            o = h = l = c = last_c
            tv = 0
        else:
            o, h, l, c, tv = b[0], b[1], b[2], b[3], int(b[4])
        last_c = c
        a = a or [0.0, 0.0, 0, 0, 0, 0, 0]
        vx = None
        if vix:
            for k in range(m, m - 3, -1):
                if k in vix:
                    vx = vix[k]; break
        bars.append([m, o, h, l, c, round(a[0] / used, 2), round(a[1] / used, 2),
                     round(a[2] / used, 1), round(a[3] / used, 1), round(a[4] / used, 1), round(a[5] / used, 1), round(a[6] / used, 1), vx, tv])
    last_tick = rows[-1]['date'][11:19] if rows else ''
    now = datetime.datetime.now(TW)
    xdate = datetime.date.fromisoformat(x)
    done_after = datetime.datetime(xdate.year, xdate.month, xdate.day, 9, 0, tzinfo=TW) + datetime.timedelta(days=1 if x in CALSET else 0)
    complete = now >= done_after and (x not in CALSET or last_tick >= '23:00')
    return {'d': x, 'complete': complete, 'dup': round(dup, 3) if dup else None, 'dupUsed': round(used, 3),
            'cls': round(n_cls / n_all, 3) if n_all else None, 'fb': round(n_fb / n_cls, 3) if n_cls else None,
            'lastTick': last_tick, 'settleA': pa, 'settleB': x if x in CALSET else pa, 'ver': VER, 'priceVer': VER,
            'note': '晚上夜盤 18:00 前沿用前一交易日結算價；台指期每分鐘取當下近月',
            'cols': ['m'] + COLS[1:], 'bars': bars}


def day_diff(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


# ───────── 每個交易日的結構資料（日盤收盤後） ─────────
def structure(d, prev):
    rows = opt_daily(d)
    ch = chain(rows)
    sm = build_smile(d)
    if not ch or not sm:
        return None, None
    F_ref = None
    exps = sorted(((contract_exp(c), c) for c in ch if contract_exp(c) and contract_exp(c) > d))
    if not exps:
        return None, None
    oC = oP = vC = vP = 0
    cs = ps = 0.0
    aggK = {}
    for c, S in ch.items():
        for s in S.values():
            oC += s['coi']; oP += s['poi']; vC += s['cv']; vP += s['pv']
            cs += s['K'] * s['coi']; ps += s['K'] * s['poi']
            g = aggK.setdefault(s['K'], [0, 0])
            g[0] += s['coi']; g[1] += s['poi']
    near = next((c for e, c in exps if c in sm), None)
    F_ref = sm[near]['F'] if near else None
    # 價平隱含波動率：最近結算日（剩 2 天以上）；25 Delta 偏斜：剩 5 天以上最近的合約
    def pick(min_days):
        for e, c in exps:
            if c in sm and day_diff(d, e) >= min_days:
                return c
        return None
    atm_iv = skew = None
    ca = pick(2)
    if ca:
        atm_iv = iv_at(sm[ca], sm[ca]['F'])
    cs5 = pick(5)
    if cs5:
        q = sm[cs5]
        F, T = q['F'], q['T']
        # 找 Delta 最接近 +0.25 的買權與 −0.25 的賣權的波動率
        bc = bp = None
        for K, v in zip(q['Ks'], q['ivs']):
            dc = delta(F, K, T, v, True)
            dp = dc - 1
            if K >= F and (bc is None or abs(dc - 0.25) < abs(bc[0] - 0.25)):
                bc = (dc, v)
            if K <= F and (bp is None or abs(dp + 0.25) < abs(bp[0] + 0.25)):
                bp = (dp, v)
        if bc and bp and abs(bc[0] - 0.25) < 0.1 and abs(bp[0] + 0.25) < 0.1:
            skew = bp[1] - bc[1]
    # 現價上下 10% 內的最大未平倉履約價
    if F_ref:
        rng = [k for k in aggK if abs(k / F_ref - 1) <= 0.1]
    else:
        rng = list(aggK)
    cw = max((k for k in rng if not F_ref or k >= F_ref), key=lambda k: aggK[k][0], default=None)
    pw = max((k for k in rng if not F_ref or k <= F_ref), key=lambda k: aggK[k][1], default=None)
    rec = {'d': d, 'oC': oC, 'oP': oP, 'vC': vC, 'vP': vP,
           'cC': round(cs / oC) if oC else None, 'cP': round(ps / oP) if oP else None,
           'cw': cw, 'pw': pw, 'iv': round(atm_iv * 100, 2) if atm_iv else None,
           'skew': round(skew * 100, 2) if skew is not None else None, 'F': round(F_ref, 1) if F_ref else None,
           'near': near}
    return rec, ch


def strike_map(d, ch, prev_ch, fut_close):
    sm = build_smile(d)
    out = []
    t_set = ts(d, 13 * 60 + 45)
    allK = {}
    for code in sorted(ch, key=lambda c: (contract_exp(c) or '9', c)):
        exp = contract_exp(code)
        if not exp or exp <= d or code not in sm:
            continue
        q = sm[code]
        F, T = q['F'], max(600, exp_ts(exp) - t_set) / YEAR_S
        S = ch[code]
        P = (prev_ch or {}).get(code, {})
        rows = []
        for K in sorted(S):
            s = S[K]
            if abs(K / F - 1) > 0.12 or (s['coi'] + s['poi'] + s['cv'] + s['pv']) == 0:
                continue
            v = iv_at(q, K)
            g = gamma(F, K, T, v)
            g1 = g * 0.01 * F * TXEQ           # 每漲跌 1%，每口要調整幾口大台
            dc = delta(F, K, T, v, True)
            p = P.get(K)
            row = [K, s['coi'], s['poi'], s['coi'] - (p['coi'] if p else 0) if P else None, s['poi'] - (p['poi'] if p else 0) if P else None,
                   s['cv'], s['pv'], round(g1 * (s['coi'] + s['poi']), 1), round(dc * s['coi'] * TXEQ, 1), round((dc - 1) * s['poi'] * TXEQ, 1),
                   round(v * 100, 1)]
            rows.append(row)
            a = allK.setdefault(K, [K, 0, 0, 0, 0, 0, 0, 0.0, 0.0, 0.0, None])
            for i in (1, 2, 5, 6):
                a[i] += row[i]
            for i in (3, 4):
                if row[i] is not None:
                    a[i] = (a[i] or 0) + row[i]
            for i in (7, 8, 9):
                a[i] = round(a[i] + row[i], 1)
        if not rows:
            continue
        # 最大痛點：結算在哪個履約價時，買方拿回的總價值最少
        pain, pv = None, 1e30
        for x in rows:
            tot = sum(r[1] * (x[0] - r[0]) for r in rows if x[0] > r[0]) + sum(r[2] * (r[0] - x[0]) for r in rows if x[0] < r[0])
            if tot < pv:
                pv, pain = tot, x[0]
        out.append({'code': code, 'exp': exp, 'days': day_diff(d, exp), 'F': round(F, 1), 'iv': round(iv_at(q, F) * 100, 2),
                    'pain': pain, 'rows': rows})
    allrows = [allK[k] for k in sorted(allK)]
    return {'d': d, 'fut': fut_close, 'cols': ['K', 'coi', 'poi', 'dcoi', 'dpoi', 'cv', 'pv', 'gOI', 'dC', 'dP', 'iv'],
            'note': 'gOI＝每漲跌 1% 需要調整的等值大台口數（不分方向）；dC、dP＝Delta 加權未平倉（等值大台口數）',
            'exps': out, 'all': allrows}


# ───────── 法人 ─────────
def inst_data(start):
    W = {'TX': 1.0, 'MTX': 0.25, 'TMF': 0.05}
    days = {}
    for fid, w in W.items():
        for r in api({'dataset': 'TaiwanFuturesInstitutionalInvestors', 'data_id': fid, 'start_date': start}):
            net = (r.get('long_open_interest_balance_volume') or 0) - (r.get('short_open_interest_balance_volume') or 0)
            e = days.setdefault(r['date'], {'d': r['date'], 'fut': {}, 'opt': {}})
            who = r['institutional_investors']
            e['fut'][who] = round(e['fut'].get(who, 0) + net * w, 2)
    CP = lambda v: 'C' if str(v).strip().lower() in ('call', '買權', 'c') else 'P' if str(v).strip().lower() in ('put', '賣權', 'p') else None
    for r in api({'dataset': 'TaiwanOptionInstitutionalInvestors', 'data_id': 'TXO', 'start_date': start}):
        cp = CP(r.get('call_put'))
        if not cp:
            continue
        e = days.setdefault(r['date'], {'d': r['date'], 'fut': {}, 'opt': {}})
        o = e['opt'].setdefault(r['institutional_investors'], {})
        o['l' + cp] = int(r.get('long_open_interest_balance_volume') or 0)
        o['s' + cp] = int(r.get('short_open_interest_balance_volume') or 0)
        o['L' + cp] = round(float(r.get('long_open_interest_balance_amount') or 0))
        o['S' + cp] = round(float(r.get('short_open_interest_balance_amount') or 0))
    return [days[d] for d in sorted(days)]


# ───────── 日曆日 → 交易時段 5 分鐘 ─────────
def build_m5():
    files = sorted(f[:-5] for f in os.listdir(M1) if f.endswith('.json'))
    sess = {}
    for x in files:
        j = json.load(open(os.path.join(M1, x + '.json'), encoding='utf-8'))
        for b in j['bars']:
            m = b[0]
            if m <= 300:
                td, s = (x if x in CALSET else next_td(x)), 'N'
            elif 525 <= m <= 825:
                td, s = x, 'D'
            elif m >= 900:
                td, s = next_td(x), 'N'
            else:
                continue
            sess.setdefault((td, s), []).append([ts(x, m)] + b[1:])
    months = {}
    for (td, s), bars in sess.items():
        bars.sort(key=lambda b: b[0])
        agg = []
        cur = None
        for b in bars:
            k = b[0] // 300 * 300
            if cur is None or cur[0] != k:
                cur = [k, b[1], b[2], b[3], b[4], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, None, 0]
                agg.append(cur)
            cur[2] = max(cur[2], b[2]); cur[3] = min(cur[3], b[3]); cur[4] = b[4]
            for i in range(5, 12):
                cur[i] += b[i]
            if b[12] is not None:
                cur[12] = b[12]
            cur[13] += b[13] if len(b) > 13 else 0
        for a in agg:
            for i in range(5, 12):
                a[i] = round(a[i], 2 if i < 7 else 1)
        months.setdefault(td[:7], []).append({'td': td, 's': s, 'bars': agg})
    os.makedirs(M5, exist_ok=True)
    for mo, lst in months.items():
        lst.sort(key=lambda q: (q['td'], 0 if q['s'] == 'N' else 1))
        txt = json.dumps({'cols': COLS, 'sessions': lst}, ensure_ascii=False, separators=(',', ':'))
        p = os.path.join(M5, mo + '.json')
        if not os.path.exists(p) or open(p, encoding='utf-8').read() != txt:
            open(p, 'w', encoding='utf-8').write(txt)
    return files, sorted(months)


def load_json(p, default):
    try:
        return json.load(open(p, encoding='utf-8'))
    except Exception:
        return default


def setup_calendar():
    today = datetime.datetime.now(TW).date().isoformat()
    s0 = (datetime.date.fromisoformat(START) - datetime.timedelta(days=40)).isoformat()
    cal_rows = api({'dataset': 'TaiwanStockTradingDate', 'start_date': s0})
    CAL.extend(sorted({r['date'] for r in cal_rows if r['date'] <= today}))
    CALSET.update(CAL)
    return today, s0


def save_log():
    open(os.path.join(OUT, '_lastrun.txt'), 'w', encoding='utf-8').write('\n'.join(LOG[-300:]) + '\n')


def update_meta(**kw):
    p = os.path.join(OUT, 'index.json')
    meta = load_json(p, {})
    meta.update(kw)
    meta['updated'] = datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M')
    json.dump(meta, open(p, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))


def old_price_files():
    out = []
    for f in sorted(os.listdir(M1)):
        if not f.endswith('.json'):
            continue
        j = load_json(os.path.join(M1, f), {})
        if j.get('bars') and (j.get('priceVer') or 1) < VER:
            out.append(f[:-5])
    return out


def cmd_fixprice():
    """舊檔只重抓台指期 1 分 K，把價格換成「當下近月」；選擇權力道欄位不動（影響極小，見 _compare.json）"""
    global REQ_BUDGET
    todo = old_price_files()
    if not todo:
        log('價格都已是新版，不用查詢'); save_log(); return
    REQ_BUDGET = min(quota_ok(), int(os.environ.get('FIX_N', '30')) + 2)
    if REQ_BUDGET <= 2:
        save_log(); return
    setup_calendar()
    done = 0
    try:
        for x in sorted(todo, reverse=True):
            p = os.path.join(M1, x + '.json')
            j = load_json(p, {})
            tx = tx_minutes(x)
            if not tx:
                log('no kbar', x); continue
            last = None
            changed = 0
            for b in j['bars']:
                nb = tx.get(b[0])
                if nb:
                    if (b[1], b[2], b[3], b[4]) != nb[:4]:
                        changed += 1
                    b[1], b[2], b[3], b[4] = nb[0], nb[1], nb[2], nb[3]
                    if len(b) > 13:
                        b[13] = int(nb[4])
                    last = nb[3]
                elif last is not None:
                    b[1] = b[2] = b[3] = b[4] = last
                    if len(b) > 13:
                        b[13] = 0
            j['priceVer'] = VER
            json.dump(j, open(p, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
            done += 1
            log('price fixed', x, 'changed minutes', changed, 'req', NREQ[0])
    except RateLimited as e:
        log('STOP', e)
    build_m5()
    update_meta(priceFixPending=len(old_price_files()))
    log('fixprice done', done, 'requests', NREQ[0]); save_log()


def cmd_compare():
    """用新規則重算最近幾天，和舊結果比較差多少，結果存 _compare.json（新結果也直接存回）"""
    global REQ_BUDGET
    REQ_BUDGET = min(quota_ok(), 40)
    if REQ_BUDGET <= 0:
        save_log(); return
    today, s0 = setup_calendar()
    fut_ref = fut_daily_all(s0, today)
    days = [d for d in CAL if d < today][-int(os.environ.get('CMP_N', '5')):]
    meta = load_json(os.path.join(OUT, 'index.json'), {})
    dup_hist = meta.get('dupHist', [])
    res_all = []
    try:
        for x in days:
            old = load_json(os.path.join(M1, x + '.json'), {})
            new = process_file(x, fut_ref, dup_hist)
            if not old.get('bars') or not new:
                continue
            o = {b[0]: b for b in old['bars']}
            pairs = [(o[b[0]], b) for b in new['bars'] if b[0] in o]
            def corr(a, b):
                if len(a) < 10:
                    return None
                ma, mb = statistics.mean(a), statistics.mean(b)
                va = sum((x - ma) ** 2 for x in a) ** .5; vb = sum((y - mb) ** 2 for y in b) ** .5
                return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (va * vb), 4) if va and vb else None
            seg = {'全部': lambda m: True, '晚上 15:00–18:00': lambda m: 900 <= m < OPT_AVAIL, '其他時間': lambda m: not (900 <= m < OPT_AVAIL)}
            r = {'d': x}
            for name, f in seg.items():
                pp = [(a, b) for a, b in pairs if f(a[0])]
                if not pp:
                    continue
                an, bn = [a[5] for a, b in pp], [b[5] for a, b in pp]
                mabs = statistics.mean(abs(v) for v in an) or 1e-9
                r[name] = {'n': len(pp), 'corr': corr(an, bn), 'diff%': round(statistics.mean(abs(a - b) for a, b in zip(an, bn)) / mabs * 100, 2),
                           'priceChanged': sum(1 for a, b in pp if a[4] != b[4])}
            res_all.append(r)
            log('compare', json.dumps(r, ensure_ascii=False))
            json.dump(new, open(os.path.join(M1, x + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    except RateLimited as e:
        log('STOP', e)
    json.dump({'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'), 'days': res_all}, open(os.path.join(OUT, '_compare.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    build_m5()
    update_meta(priceFixPending=len(old_price_files()))
    log('compare done requests', NREQ[0]); save_log()


def main():
    global REQ_BUDGET
    if not TOKEN:
        log('沒有 FINMIND_TOKEN'); return
    os.makedirs(M1, exist_ok=True)
    if MODE == 'fixprice':
        return cmd_fixprice()
    if MODE == 'compare':
        return cmd_compare()
    REQ_BUDGET = quota_ok()
    if REQ_BUDGET <= 0:
        save_log(); return
    today = datetime.datetime.now(TW).date().isoformat()
    s0 = (datetime.date.fromisoformat(START) - datetime.timedelta(days=40)).isoformat()
    cal_rows = api({'dataset': 'TaiwanStockTradingDate', 'start_date': s0})
    CAL.extend(sorted({r['date'] for r in cal_rows if r['date'] <= today}))
    CALSET.update(CAL)
    fut_ref = fut_daily_all(s0, today)
    meta = load_json(os.path.join(OUT, 'index.json'), {})
    dup_hist = meta.get('dupHist', [])
    # 要處理的日曆日：交易日＋週五夜盤延續到的週六
    targets = []
    for d in CAL:
        if d < START:
            continue
        targets.append(d)
        dd = datetime.date.fromisoformat(d)
        if dd.weekday() == 4:
            sat = (dd + datetime.timedelta(days=1)).isoformat()
            if sat <= today:
                targets.append(sat)
    targets.sort()
    todo = []
    for x in targets:
        p = os.path.join(M1, x + '.json')
        j = load_json(p, None)
        if j and j.get('complete') and (not j.get('bars') or len(j['cols']) >= 14):
            continue
        todo.append(x)
    # 先補最近的，再往回補歷史
    recent = [x for x in todo if x >= (datetime.date.fromisoformat(today) - datetime.timedelta(days=6)).isoformat()]
    older = [x for x in todo if x not in recent]
    order = sorted(recent) + sorted(older, reverse=True)
    log('targets', len(targets), 'todo', len(todo), 'this run max', MAX_FILES)
    done = 0
    try:
        for x in order:
            if done >= MAX_FILES or (time.time() - T_START) / 60 > MAX_MIN:
                log('time/file budget reached'); break
            t0 = time.time()
            try:
                res = process_file(x, fut_ref, dup_hist)
            except RateLimited:
                raise
            except Exception as e:
                log('fail', x, e); continue
            if res is None:
                # 沒資料的週六（例如沒有夜盤）也記下來，避免每次重抓
                if datetime.date.fromisoformat(x).weekday() == 5 and x < today:
                    json.dump({'d': x, 'complete': True, 'bars': [], 'cols': ['m'] + COLS[1:]}, open(os.path.join(M1, x + '.json'), 'w'), separators=(',', ':'))
                continue
            json.dump(res, open(os.path.join(M1, x + '.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
            done += 1
            log('file', x, 'bars', len(res['bars']), 'dup', res['dup'], 'cls', res['cls'], 'fb', res['fb'], 'last', res['lastTick'],
                'complete', res['complete'], '%.0fs' % (time.time() - t0), 'req', NREQ[0])
            OPT_DAILY.clear() if len(OPT_DAILY) > 6 else None
    except RateLimited as e:
        log('STOP', e)
    files, months = build_m5()
    # 結構資料：每個交易日
    daily = {x['d']: x for x in load_json(os.path.join(OUT, 'daily.json'), {}).get('days', [])}
    tds = [d for d in CAL if d >= START]
    prev_ch = None
    last_ch = None
    try:
        need = [d for d in tds if d not in daily or d >= tds[-2]]
        for d in need:
            if (time.time() - T_START) / 60 > MAX_MIN + 15:
                break
            rec, ch = structure(d, None)
            if rec:
                rec['fc'] = fut_ref.get(d)
                daily[d] = rec
            OPT_DAILY.pop(prev_td(prev_td(d)) or '', None)
        # 最新一天的履約價地圖
        # 今天盤後資料還沒出來時（例如早上），往回找最近一個已經有選擇權日成交資料的交易日
        for last in reversed(tds[-3:]):
            _, ch = structure(last, None)
            if not ch:
                log('strikes: no option daily yet', last)
                continue
            pv = prev_td(last)
            pch = chain(opt_daily(pv)) if pv else None
            json.dump(strike_map(last, ch, pch, fut_ref.get(last)), open(os.path.join(OUT, 'strikes.json'), 'w', encoding='utf-8'),
                      ensure_ascii=False, separators=(',', ':'))
            log('strikes written', last)
            break
    except RateLimited as e:
        log('STOP structure', e)
    except Exception as e:
        log('structure fail', e)
    json.dump({'days': [daily[d] for d in sorted(daily)]}, open(os.path.join(OUT, 'daily.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    try:
        inst = inst_data((datetime.date.fromisoformat(START) - datetime.timedelta(days=200)).isoformat())
        json.dump({'days': inst}, open(os.path.join(OUT, 'inst.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    except Exception as e:
        log('inst fail', e)
    # 資料品質統計
    q = []
    for x in files[-40:]:
        j = load_json(os.path.join(M1, x + '.json'), {})
        if j.get('bars'):
            q.append({'d': x, 'dup': j.get('dup'), 'cls': j.get('cls'), 'fb': j.get('fb'), 'last': j.get('lastTick'), 'complete': j.get('complete')})
    meta = {'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'), 'start': START, 'files': files, 'months': months,
            'cal': [d for d in CAL if d >= (datetime.date.fromisoformat(START) - datetime.timedelta(days=10)).isoformat()],
            'dupHist': dup_hist[-60:], 'quality': q, 'pending': len([x for x in order]) - done,
            'level': 'B-', 'cols': COLS, 'priceFixPending': len(old_price_files()), 'ver': VER}
    json.dump(meta, open(os.path.join(OUT, 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    open(os.path.join(OUT, '_lastrun.txt'), 'w', encoding='utf-8').write('\n'.join(LOG[-300:]) + '\n')
    log('done files', done, 'requests', NREQ[0], 'minutes %.1f' % ((time.time() - T_START) / 60))


if __name__ == '__main__':
    main()
