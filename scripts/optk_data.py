#!/usr/bin/env python3
"""
選擇權 K 線資料（台指選擇權 TXO，週選＋月選，買權／賣權每一個履約價）

每個日曆日的逐筆成交（FinMind TaiwanOptionTick，data_id=TXO，含夜盤）→ 每一檔（結算月份／週別 × 履約價 × 買賣權）的 1 分 K。
網頁再自己合成 5／15／30／60 分、4 小時、日、週、月 K（日 K 分「全盤（含前一晚夜盤）」和「只看日盤」）。
另外用 TaiwanOptionDaily 補上每天的未平倉量和結算價。

逐筆資料每筆成交記兩次（買賣雙方），量除以 2（和 force_data.py 實測的倍數一致，約 1.96～2.0）。

輸出放在 optk-data 分支（每次只留一個提交，不進 main、不觸發網站重建、不會讓倉庫越來越大），網頁從 raw.githubusercontent.com 讀：
  index.json                         各結算日、各履約價的最新價、漲跌、當日量、未平倉（查詢清單）
  s/<結算代碼>/<履約價><C|P>.json    {"m": [[分鐘, 開, 高, 低, 收, 量], ...], "oi": [[日期, 未平倉, 結算價], ...]}
                                     分鐘＝台灣時間當成 UTC 的「分鐘數」（×60 就是秒）
  meta.json                          處理過的日曆日

FinMind 憑證從環境變數 FINMIND_TOKEN 讀取（GitHub 加密密碼），程式不會印出或存檔。
"""
import json, os, re, sys, time, datetime, subprocess, urllib.request, urllib.error, urllib.parse, shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get('FINMIND_TOKEN', '').strip()
API = 'https://api.finmindtrade.com/api/v4/data'
TW = datetime.timezone(datetime.timedelta(hours=8))
WORK = os.environ.get('OPTK_DIR', '/tmp/optk')
BRANCH = os.environ.get('OPTK_BRANCH', 'optk-data')
BACKFILL = int(os.environ.get('BACKFILL_DAYS', '0') or 0)      # 往回補幾個日曆日（第一次用）
RECENT = int(os.environ.get('RECENT_DAYS', '3') or 3)          # 每次重抓最近幾個日曆日（夜盤資料比較晚公布）
KEEP_EXPIRED = int(os.environ.get('KEEP_EXPIRED_DAYS', '30'))  # 結算後還保留幾天（回頭檢討用）
MAX_MIN = float(os.environ.get('MAX_MINUTES', '100'))
PUSH = os.environ.get('PUSH', '1') == '1'
T0 = time.time()
NREQ = [0]


def log(*a):
    s = ' '.join(str(x) for x in a)
    if TOKEN:
        s = s.replace(TOKEN, '***')
    print(datetime.datetime.now(TW).strftime('%H:%M:%S'), s, flush=True)


def api(params, timeout=300, tries=3):
    q = dict(params, token=TOKEN)
    url = API + '?' + urllib.parse.urlencode(q)
    for k in range(tries):
        try:
            NREQ[0] += 1
            with urllib.request.urlopen(url, timeout=timeout) as r:
                j = json.loads(r.read().decode())
            if j.get('status') == 200:
                return j.get('data') or []
            msg = str(j.get('msg'))
            if 'limit' in msg.lower() or 'upper' in msg.lower():
                raise RuntimeError('額度用完：' + msg[:80])
            log('api status', j.get('status'), msg[:120], params.get('dataset'), params.get('start_date'))
        except urllib.error.HTTPError as e:
            log('api http', e.code, params.get('dataset'), params.get('start_date'))
            if e.code in (402, 429):
                raise RuntimeError('額度用完')
        except RuntimeError:
            raise
        except Exception as e:
            log('api err', repr(e)[:160], params.get('dataset'), params.get('start_date'))
        time.sleep(5 * (k + 1))
    return None


# ───────── 交易日曆、結算日 ─────────
CAL, CALSET = [], set()


def load_cal():
    global CAL, CALSET
    cal = []
    try:
        cal = json.load(open(os.path.join(ROOT, 'data', 'force', 'index.json'))).get('cal') or []
    except Exception:
        pass
    s0 = (datetime.date.today() - datetime.timedelta(days=max(BACKFILL, 30) + 20)).isoformat()
    if not cal or cal[0] > s0:
        rows = api({'dataset': 'TaiwanStockTradingDate', 'start_date': s0}) or []
        cal = sorted(set(cal) | {str(r['date'])[:10] for r in rows})
    CAL = sorted(cal)
    CALSET = set(CAL)


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
        e = nth_weekday(y, mo, 2, 3) if not m.group(3) else nth_weekday(y, mo, 2 if m.group(3) == 'W' else 4, int(m.group(4)))
    except ValueError:
        return None
    if CAL and e <= CAL[-1] and e not in CALSET:          # 遇到休市順延
        for d in CAL:
            if d > e:
                return d
    return e


def next_td(x):
    for d in CAL:
        if d > x:
            return d
    d = datetime.date.fromisoformat(x) + datetime.timedelta(days=1)
    while d.weekday() >= 5:
        d += datetime.timedelta(days=1)
    return d.isoformat()


def day_min0(x):
    """日曆日 x 的 00:00 → 分鐘數（台灣時間當成 UTC）"""
    d = datetime.date.fromisoformat(x)
    return int(datetime.datetime(d.year, d.month, d.day, tzinfo=datetime.timezone.utc).timestamp()) // 60


def trade_day(tmin):
    """這一分鐘屬於哪個交易日：15:00 以後的夜盤算下一個交易日，凌晨夜盤算當天（週六凌晨算下週一）"""
    d = datetime.datetime.fromtimestamp(tmin * 60, datetime.timezone.utc)
    x, m = d.date().isoformat(), d.hour * 60 + d.minute
    if m >= 900:
        return next_td(x)
    if m <= 330 and x not in CALSET:
        return next_td(x)
    return x


# ───────── 資料 ─────────
SER = {}          # (code, K, cp) -> {'m': {t: [o,h,l,c,v]}, 'oi': {date: [oi, settle]}}
META = {'days': {}}


def sfile(code, K, cp):
    return os.path.join(WORK, 's', code, '%d%s.json' % (K, cp))


def load_prev():
    shutil.rmtree(WORK, ignore_errors=True)
    repo = os.environ.get('GITHUB_REPOSITORY')
    if repo:
        r = subprocess.run(['git', 'clone', '-q', '--depth', '1', '--branch', BRANCH, f'https://github.com/{repo}.git', WORK], capture_output=True, text=True)
        if r.returncode:
            log('沒有舊資料（第一次）', r.stderr.strip()[-120:])
    os.makedirs(WORK, exist_ok=True)
    shutil.rmtree(os.path.join(WORK, '.git'), ignore_errors=True)
    try:
        META.update(json.load(open(os.path.join(WORK, 'meta.json'))))
    except Exception:
        pass
    n = 0
    base = os.path.join(WORK, 's')
    if os.path.isdir(base):
        for code in os.listdir(base):
            for fn in os.listdir(os.path.join(base, code)):
                m = re.fullmatch(r'(\d+)([CP])\.json', fn)
                if not m:
                    continue
                try:
                    j = json.load(open(os.path.join(base, code, fn)))
                except Exception:
                    continue
                SER[(code, int(m.group(1)), m.group(2))] = {'m': {b[0]: b[1:] for b in j.get('m', [])}, 'oi': {r[0]: r[1:] for r in j.get('oi', [])}}
                n += 1
    log('載入舊資料', n, '檔', len(META['days']), '個日曆日')


def norm_k(v):
    k = float(v)
    return int(round(k))


def process_day(x):
    rows = api({'dataset': 'TaiwanOptionTick', 'data_id': 'TXO', 'start_date': x}, timeout=600)
    if rows is None:
        return None
    m0 = day_min0(x)
    bars = {}
    last = ''
    for r in rows:
        v = r.get('volume') or 0
        p = r.get('price')
        if v <= 0 or not p:
            continue
        t = str(r['date'])
        if t[:10] != x:
            continue
        code = str(r.get('contract_date') or '').strip()
        if not re.fullmatch(r'\d{6}(?:[WF]\d)?', code):
            continue
        key = (code, norm_k(r['ExercisePrice']), 'C' if r['PutCall'] == 'C' else 'P')
        tm = m0 + int(t[11:13]) * 60 + int(t[14:16])
        b = bars.setdefault(key, {})
        a = b.get(tm)
        p = float(p)
        if a is None:
            b[tm] = [p, p, p, p, v]
        else:
            if p > a[1]: a[1] = p
            if p < a[2]: a[2] = p
            a[3] = p; a[4] += v
        if t > last:
            last = t
    # 逐筆每筆記兩次 → 量除以 2
    for b in bars.values():
        for a in b.values():
            a[4] = (a[4] + 1) // 2
    # 取代這個日曆日的舊資料（之前處理過才需要清）
    lo, hi = m0, m0 + 1440
    if x in META['days']:
        for s in SER.values():
            if s['m']:
                for t in [t for t in s['m'] if lo <= t < hi]:
                    del s['m'][t]
    for key, b in bars.items():
        SER.setdefault(key, {'m': {}, 'oi': {}})['m'].update(b)
    log('日曆日', x, '逐筆', len(rows), '檔', len(bars), '最後一筆', last[11:19])
    return {'n': len(rows), 'last': last[11:19], 'series': len(bars)}


def add_oi(d0, d1):
    rows = api({'dataset': 'TaiwanOptionDaily', 'data_id': 'TXO', 'start_date': d0, 'end_date': d1}, timeout=600) or []
    n = 0
    for r in rows:
        if r.get('trading_session') != 'position':
            continue
        code = str(r.get('contract_date') or '').strip()
        key = (code, norm_k(r['strike_price']), 'C' if str(r.get('call_put')).lower() == 'call' else 'P')
        s = SER.get(key)
        oi = int(r.get('open_interest') or 0)
        if s is None:
            if not oi:
                continue
            s = SER[key] = {'m': {}, 'oi': {}}
        s['oi'][str(r['date'])[:10]] = [oi, float(r.get('settlement_price') or 0)]
        n += 1
    log('未平倉', d0, '～', d1, n, '筆')


def kind_label(code, exp):
    m = re.fullmatch(r'(\d{4})(\d{2})(?:([WF])(\d))?', code)
    mo = int(m.group(2))
    e = exp[5:].replace('-', '/') if exp else ''
    if not m.group(3):
        return 'M', f'{mo}月 月選（{e}）'
    wd = '週三' if m.group(3) == 'W' else '週五'
    return m.group(3), f'{mo}月 第{m.group(4)}個{wd}（{e}）'


def build_index():
    today = datetime.datetime.now(TW).date().isoformat()
    cut = (datetime.date.fromisoformat(today) - datetime.timedelta(days=KEEP_EXPIRED)).isoformat()
    # 清掉太舊的結算
    for key in [k for k in SER if (contract_exp(k[0]) or '9999') < cut]:
        del SER[key]
    # 最新交易日
    last_td = None
    for s in SER.values():
        if s['m']:
            t = max(s['m'])
            td = trade_day(t)
            if last_td is None or td > last_td:
                last_td = td
    cons = {}
    for (code, K, cp), s in SER.items():
        ms = s['m']
        c = cons.setdefault(code, {})
        row = c.setdefault(K, {})
        info = {}
        if ms:
            ts_ = sorted(ms)
            lt = ts_[-1]
            info['c'] = ms[lt][3]
            info['t'] = lt
            vol, prev = 0, None
            for t in reversed(ts_):
                td = trade_day(t)
                if td == last_td:
                    vol += ms[t][4]
                elif td < last_td:
                    prev = ms[t][3]
                    break
            info['v'] = vol
            if prev is not None and trade_day(lt) == last_td:
                info['ch'] = round(info['c'] - prev, 2)
            info['n'] = len(ms)
        if s['oi']:
            d = max(s['oi'])
            info['oi'] = s['oi'][d][0]
            info['st'] = s['oi'][d][1]
        row[cp] = info
    fut = None
    try:
        sj = json.load(open(os.path.join(ROOT, 'data', 'force', 'strikes.json')))
        fut = {'px': sj.get('fut'), 'd': sj.get('d')}
    except Exception:
        pass
    out = []
    for code in sorted(cons, key=lambda c: (contract_exp(c) or '9999', c)):
        exp = contract_exp(code)
        kd, lb = kind_label(code, exp)
        rows = []
        tv = 0
        for K in sorted(cons[code]):
            r = cons[code][K]
            C, P = r.get('C', {}), r.get('P', {})
            if not C.get('n') and not P.get('n'):
                continue
            rows.append([K, C.get('c'), C.get('ch'), C.get('v'), C.get('oi'), P.get('c'), P.get('ch'), P.get('v'), P.get('oi'), 1 if C.get('n') else 0, 1 if P.get('n') else 0])
            tv += (C.get('v') or 0) + (P.get('v') or 0)
        if rows:
            out.append({'code': code, 'exp': exp, 'kind': kd, 'label': lb, 'expired': bool(exp and exp < today), 'vol': tv, 'rows': rows})
    return {'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'), 'lastTd': last_td, 'fut': fut,
            'cols': ['履約價', '買權收', '買權漲跌', '買權量', '買權未平倉', '賣權收', '賣權漲跌', '賣權量', '賣權未平倉', '買權有K線', '賣權有K線'],
            'contracts': out}


def save_all(index):
    base = os.path.join(WORK, 's')
    shutil.rmtree(base, ignore_errors=True)
    nf = nb = 0
    for (code, K, cp), s in SER.items():
        if not s['m'] and not s['oi']:
            continue
        os.makedirs(os.path.join(base, code), exist_ok=True)
        m = [[t] + [round(x, 2) if isinstance(x, float) else x for x in s['m'][t]] for t in sorted(s['m'])]
        oi = [[d] + s['oi'][d] for d in sorted(s['oi'])]
        with open(sfile(code, K, cp), 'w') as f:
            json.dump({'m': m, 'oi': oi}, f, separators=(',', ':'))
        nf += 1; nb += len(m)
    json.dump(index, open(os.path.join(WORK, 'index.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    json.dump(META, open(os.path.join(WORK, 'meta.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    sz = sum(os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(WORK) for f in fs)
    log('存檔', nf, '檔', nb, '根 1 分 K，共', round(sz / 1e6, 1), 'MB')


def push():
    repo = os.environ.get('GITHUB_REPOSITORY'); tok = os.environ.get('GH_TOKEN')
    if not PUSH or not repo or not tok:
        log('不推送'); return
    sh = lambda *c: subprocess.run(c, cwd=WORK, capture_output=True, text=True)
    sh('git', 'init', '-q'); sh('git', 'checkout', '-q', '-b', BRANCH); sh('git', 'add', '-A')
    sh('git', '-c', 'user.name=optk-bot', '-c', 'user.email=optk-bot@users.noreply.github.com', 'commit', '-q', '-m', 'optk ' + datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'))
    r = sh('git', 'push', '-qf', f'https://x-access-token:{tok}@github.com/{repo}', BRANCH)
    log('推送', '失敗 ' + r.stderr[-200:] if r.returncode else '完成')


def main():
    if not TOKEN:
        log('沒有 FINMIND_TOKEN'); sys.exit(1)
    load_cal()
    load_prev()
    today = datetime.datetime.now(TW).date()
    back = BACKFILL if BACKFILL else RECENT
    days = [(today - datetime.timedelta(days=k)).isoformat() for k in range(back, -1, -1)]
    # 一般更新：最近幾天一定重抓；之前沒抓完整的也補
    recent = set(days[-(RECENT + 1):])
    todo = [x for x in days if x in recent or not META['days'].get(x, {}).get('ok')]
    # 只處理有交易時段的日曆日（交易日，或週六凌晨的夜盤）
    def has_session(x):
        if x in CALSET:
            return True
        d = datetime.date.fromisoformat(x)
        return d.weekday() == 5 and (d - datetime.timedelta(days=1)).isoformat() in CALSET
    todo = [x for x in todo if has_session(x) or x > (CAL[-1] if CAL else '')]
    log('要處理', len(todo), '個日曆日', todo[:3], '…', todo[-3:])
    done = []
    for x in todo:
        if (time.time() - T0) / 60 > MAX_MIN:
            log('到時間上限，下次接著做'); break
        try:
            r = process_day(x)
        except RuntimeError as e:
            log(str(e)); break
        if r is None:
            continue
        complete = r['last'] >= '23:59' if x in CALSET else True
        META['days'][x] = {'n': r['n'], 'last': r['last'], 'ok': bool(complete and x < (today - datetime.timedelta(days=1)).isoformat())}
        done.append(x)
    if done:
        try:
            add_oi(min(done), max(done))
        except RuntimeError as e:
            log(str(e))
    idx = build_index()
    save_all(idx)
    log('FinMind 用了', NREQ[0], '次；花', round((time.time() - T0) / 60, 1), '分鐘')
    push()


if __name__ == '__main__':
    main()
