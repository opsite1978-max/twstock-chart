#!/usr/bin/env python3
"""權證分點：每個券商分點每天買賣了哪些權證（推到 wt-data 分支）

一天的資料：
  1. 權證對照表（證交所、櫃買中心權證基本資料）：權證 → 標的股票、認購／認售、行使比例、履約價、最後交易日
  2. 每個券商分點一次查詢（taiwan_stock_warrant_trading_daily_report，用分點代號查，回傳它當天買賣的所有權證）
     → 每檔權證存 [權證代號, 買進單位, 賣出單位, 買進金額, 賣出金額]，整理時再換算成標的、約當股數、回本股價、剩餘天數
存成 d/日期.json，從最近一天往回補 BACK 個交易日。
每輪查詢上限依 FinMind 這個小時還剩多少自動決定，保留一些給其他排程。
"""
import gzip, json, os, re, sys, time, datetime, urllib.request, urllib.parse, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txo_scrape as T

OUT = sys.argv[1] if len(sys.argv) > 1 else 'wt-out'
BACK = int(os.environ.get('BACK', '250'))
RESERVE = int(os.environ.get('RESERVE', '1500'))
CAP = int(os.environ.get('CAP', '4000'))
DEADLINE = time.time() + float(os.environ.get('MAX_MIN', '100')) * 60
SP = 'https://api.finmindtrade.com/api/v4/taiwan_stock_warrant_trading_daily_report'
MIN_W = 10000            # 一個分點一天在一檔權證買賣合計 < 1 萬元就不存


def jload(p, d):
    """每天的資料（d/日期.json）存成 gzip 壓縮檔（.json.gz），舊的未壓縮檔也讀得到"""
    if p.startswith('d/'):
        try:
            with gzip.open(os.path.join(OUT, p + '.gz'), 'rt', encoding='utf-8') as f: return json.load(f)
        except Exception: pass
    try:
        with open(os.path.join(OUT, p)) as f: return json.load(f)
    except Exception: return d


def jsave(p, o):
    fp = os.path.join(OUT, p); os.makedirs(os.path.dirname(fp) or '.', exist_ok=True)
    if p.startswith('d/'):
        with gzip.open(fp + '.gz', 'wt', encoding='utf-8', compresslevel=6) as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))
        if os.path.exists(fp): os.remove(fp)
        return
    with open(fp, 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))


def quota_left():
    try:
        req = urllib.request.Request('https://api.web.finmindtrade.com/v2/user_info', headers={'Authorization': 'Bearer ' + T.TOKEN})
        with urllib.request.urlopen(req, timeout=30) as r: j = json.loads(r.read().decode())
        return int(j.get('api_request_limit_hour') or 6000) - int(j.get('user_count') or 0)
    except Exception as e:
        T.log('查不到剩餘次數', str(e)[:60]); return 2500


def special(params):
    if T.NREQ[0] >= T.REQ_BUDGET: raise T.RateLimited('本次查詢次數用完')
    req = urllib.request.Request(SP + '?' + urllib.parse.urlencode(params), headers={'Authorization': 'Bearer ' + T.TOKEN})
    for k in range(4):
        T.NREQ[0] += 1
        try:
            with urllib.request.urlopen(req, timeout=180) as r: j = json.loads(r.read().decode('utf-8'))
            if j.get('status') == 402: raise T.RateLimited('已達 FinMind 每小時查詢上限')
            return j.get('data') or []
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:200]
            if e.code in (402, 429) or 'upper limit' in body.lower(): raise T.RateLimited('已達 FinMind 每小時查詢上限')
            if e.code == 400: return []
            time.sleep(5 * (k + 1))
        except T.RateLimited: raise
        except Exception: time.sleep(5 * (k + 1))
    raise RuntimeError('warrant report failed')


def fetch_json(u):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=120) as r: return json.loads(r.read().decode('utf-8'))
        except Exception as e:
            T.log('讀取失敗', u, str(e)[:60]); time.sleep(5 + 5 * k)
    return []


def wclose(px, d):
    """權證當天的收盤價（有成交的才存；權證代號 6 碼、開頭 03～08、70～73）"""
    return {r['stock_id']: r['close'] for r in px
            if r.get('date') == d and len(r['stock_id']) == 6 and r['stock_id'][:2] in WPFX and r.get('close') and r.get('Trading_Volume')}


WPFX = {'03', '04', '05', '06', '07', '08', '70', '71', '72', '73'}


def roc(d):
    d = (d or '').strip()
    if re.fullmatch(r'\d{7}', d): return '%d-%s-%s' % (int(d[:3]) + 1911, d[3:5], d[5:])
    if re.fullmatch(r'\d{8}', d): return d[:4] + '-' + d[4:6] + '-' + d[6:]
    return ''


def warrant_map(meta):
    """權證 → [標的代號, C/P, 行使比例, 履約價, 最後交易日]。來源：證交所、櫃買中心權證基本資料（每天更新一次，舊的保留）"""
    wm = jload('wmap.json', {})
    if meta.get('wmd') == datetime.date.today().isoformat() and meta.get('hmd') == meta['wmd'] and len(wm) > 5000: return wm
    if not meta.get('names') or time.time() - meta.get('nu', 0) > 7 * 86400:
        info = T.api({'dataset': 'TaiwanStockInfo'})
        meta['names'] = {r['stock_name']: r['stock_id'] for r in info if re.fullmatch(r'\d{4}', r.get('stock_id', ''))}; meta['nu'] = int(time.time())
    nm = meta['names']; n0 = len(wm); miss = {}
    for u in ('https://openapi.twse.com.tw/v1/opendata/t187ap37_L', 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap37_O'):
        for r in fetch_json(u):
            code = (r.get('權證代號') or '').strip(); tgn = (r.get('標的證券/指數') or '').strip()
            tg = nm.get(tgn)
            if not code: continue
            if not tg: miss[tgn] = miss.get(tgn, 0) + 1; continue
            try: ratio = float(r.get('最新標的履約配發數量(每仟單位權證)') or 0) / 1000; k = float(r.get('最新履約價格(元)/履約指數') or 0)
            except ValueError: ratio, k = 0, 0
            wm[code] = [tg, 'P' if '售' in (r.get('權證類型') or '') else 'C', ratio, k, roc(r.get('最後交易日'))]
    for r in fetch_json('https://www.tpex.org.tw/openapi/v1/tpex_warrant_issue'):
        code = (r.get('Code') or '').strip(); tg = (r.get('UnderlyingStockCode') or '').strip()
        if code and re.fullmatch(r'\d{4}', tg) and code not in wm:
            try: ratio = float(r.get('Latest ExerciseRatio') or 0); k = float(r.get('LatestExercisePrice') or 0)
            except ValueError: ratio, k = 0, 0
            wm[code] = [tg, 'P' if '售' in (r.get('Type') or '') else 'C', ratio, k, roc(r.get('ExpiryDate'))]
    T.log('權證對照表', len(wm), '檔（新增', len(wm) - n0, '），找不到標的的名稱（多半是指數、ETF）', len(miss), sorted(miss.items(), key=lambda kv: -kv[1])[:8])
    # 已經下市（到期）的權證：證交所、櫃買只列現在還在交易的，舊的用 FinMind 權證標的對照表（依上市日期查，一次一個月）補
    # 這份資料沒有履約價，所以舊權證只能算金額、約當股數和實際損益，算不出價外程度和隱含波動率
    today = datetime.date.today()
    first = today - datetime.timedelta(days=int(BACK * 1.5) + 760)
    hm = meta.setdefault('hm', [])                 # 已經查過的月份
    mons = []; y, mth = first.year, first.month
    while (y, mth) <= (today.year, today.month):
        mons.append('%d-%02d' % (y, mth)); mth += 1
        if mth > 12: y += 1; mth = 1
    recent = set(mons[-2:]); add = 0; chk = [0, 0]
    for mo in mons:
        if mo in hm and mo not in recent: continue
        if T.NREQ[0] >= T.REQ_BUDGET - 50: break
        y_, m_ = map(int, mo.split('-')); end = (datetime.date(y_ + (m_ == 12), m_ % 12 + 1, 1) - datetime.timedelta(days=1)).isoformat()
        try: rows = T.api({'dataset': 'TaiwanStockInfoWithWarrantSummary', 'start_date': mo + '-01', 'end_date': end})
        except Exception as e: T.log('權證歷史對照表失敗', mo, str(e)[:60]); break
        for r in rows:
            code = r.get('stock_id'); tg = r.get('target_stock_id')
            if not code or not tg: continue
            cp = 'P' if '售' in (r.get('type') or '') else 'C'; ratio = r.get('exercise_ratio') or 0; ed = r.get('end_date') or ''
            if code in wm:
                chk[0] += 1; chk[1] += abs((wm[code][2] or 0) - ratio) < 1e-6 + 0.02 * ratio
                continue
            wm[code] = [tg, cp, ratio, (r.get('fulfillment_price') or None), ed]; add += 1
        if mo not in hm: hm.append(mo)
    if all(mo in hm for mo in mons): meta['hmd'] = today.isoformat()
    T.log('權證歷史對照表：新增', add, '檔（已下市的舊權證），和交易所資料重疊', chk[0], '檔、行使比例一致', chk[1])
    if len(wm) > 5000: jsave('wmap.json', wm); meta['wmd'] = datetime.date.today().isoformat()
    return wm


def main():
    os.makedirs(OUT, exist_ok=True)
    # 舊的未壓縮日檔一次轉成壓縮檔
    dd = os.path.join(OUT, 'd')
    for fn in (os.listdir(dd) if os.path.isdir(dd) else []):
        if fn.endswith('.json'): jsave('d/' + fn, jload('d/' + fn, {}))
    left = quota_left(); T.REQ_BUDGET = max(0, min(CAP, left - RESERVE))
    T.log('FinMind 這小時剩', left, '次，本輪用', T.REQ_BUDGET)
    if T.REQ_BUDGET < 150: open('wt_more', 'w').write('1'); return
    meta = jload('meta.json', {})
    if not meta.get('traders') or time.time() - meta.get('tu', 0) > 7 * 86400:
        info = T.api({'dataset': 'TaiwanSecuritiesTraderInfo'})
        meta['traders'] = {r['securities_trader_id']: r['securities_trader'] for r in info if r.get('securities_trader_id')}
        meta['tu'] = int(time.time()); T.log('券商分點', len(meta['traders']))
    act = meta.setdefault('act', {})          # 分點 → [查過幾天, 有權證交易的天數]
    cal = sorted({r['date'] for r in T.api({'dataset': 'TaiwanStockTradingDate', 'start_date': (datetime.date.today() - datetime.timedelta(days=int(BACK * 1.6) + 20)).isoformat()})})
    today = datetime.datetime.now(T.TW).date().isoformat()
    days = [d for d in cal if d <= today][-BACK:]
    traders = sorted(meta['traders'])
    WM = warrant_map(meta)
    if len(WM) < 5000: T.log('權證對照表太少，先不查'); return
    # 先把整段期間每天的收盤價補齊（一天一次查詢），算歷史波動率要用
    for d in days:
        fp = 'd/' + d + '.json'; day = jload(fp, {})
        if day.get('v') and 'wp' in day: continue
        try: px = T.api({'dataset': 'TaiwanStockPrice', 'start_date': d, 'end_date': d})
        except T.RateLimited as e: T.log('停止', e); break
        day['v'] = {r['stock_id']: [r['close'], round((r.get('Trading_Volume') or 0) / 1000), round((r.get('Trading_money') or 0) / 1e8, 3)] for r in px if r.get('date') == d and re.fullmatch(r'\d{4}', r['stock_id']) and r.get('close')}
        day['wp'] = wclose(px, d)
        if day.get('mv') != 3: day.update({'mv': 3, 'w': {}, 'done': []})
        jsave(fp, day)
    stop = False; got = 0
    for di, d in enumerate(reversed(days)):
        fp = 'd/' + d + '.json'; day = jload(fp, {})
        if day.get('mv') != 3:          # 舊格式（只有合計、沒有每檔權證的價格）重查
            day = {'mv': 3, 'w': {}, 'done': [], 'v': day.get('v')} if day.get('v') else {'mv': 3, 'w': {}, 'done': []}
            jsave(fp, day)
        if 'v' not in day:
            try: px = T.api({'dataset': 'TaiwanStockPrice', 'start_date': d, 'end_date': d})
            except T.RateLimited as e: T.log('停止', e); break
            day['v'] = {r['stock_id']: [r['close'], round((r.get('Trading_Volume') or 0) / 1000), round((r.get('Trading_money') or 0) / 1e8, 3)] for r in px if r.get('date') == d and re.fullmatch(r'\d{4}', r['stock_id']) and r.get('close')}
            day['wp'] = wclose(px, d)
            jsave(fp, day)
        m = WM; done = set(day['done'])
        newest = di < 3
        todo = [t for t in traders if t not in done and (newest or act.get(t, [0, 0])[0] < 3 or act[t][1] > 0)]
        if not todo:
            if not day.get('full'): day['full'] = 1; jsave(fp, day)
            continue
        T.log(d, '要查的分點', len(todo))
        for t in todo:
            if time.time() > DEADLINE or T.NREQ[0] >= T.REQ_BUDGET - 3: stop = True; break
            try: rows = special({'securities_trader_id': t, 'date': d})
            except T.RateLimited as e: T.log('停止', e); stop = True; break
            except Exception as e: T.log(t, d, '失敗', str(e)[:60]); continue
            # 每檔權證：[權證代號, 買進單位, 賣出單位, 買進金額, 賣出金額]（標的、履約價、到期日在整理時用對照表查）
            agg = {}
            for r in rows:
                wc = r.get('stock_id')
                if wc not in m: continue
                a = agg.setdefault(wc, [0, 0, 0.0, 0.0])
                bq = r.get('buy') or 0; sq = r.get('sell') or 0; px = r.get('price') or 0
                a[0] += bq; a[1] += sq; a[2] += bq * px; a[3] += sq * px
            keep = [[wc, a[0], a[1], round(a[2]), round(a[3])] for wc, a in agg.items() if a[2] + a[3] >= MIN_W]
            if keep: day['w'][t] = keep
            done.add(t); got += 1
            st = act.setdefault(t, [0, 0]); st[0] += 1; st[1] += 1 if rows else 0
            if got % 150 == 0: day['done'] = sorted(done); jsave(fp, day); jsave('meta.json', meta); T.log(d, '已查', len(done), '查詢', T.NREQ[0])
        if not stop: day['full'] = 1
        day['done'] = sorted(done); jsave(fp, day); jsave('meta.json', meta)
        T.log(d, '完成', len(done), '／', len(traders), '有權證進出的分點', len(day['w']), '筆數', sum(len(x) for x in day['w'].values()))
        if stop: break
    for fn in os.listdir(os.path.join(OUT, 'd')) if os.path.isdir(os.path.join(OUT, 'd')) else []:
        if fn[:10] < days[0]: os.remove(os.path.join(OUT, 'd', fn))
    jsave('meta.json', meta)
    T.log('本輪查詢', T.NREQ[0], '分點', got)
    if stop and got > 30: open('wt_more', 'w').write('1')
    try:
        import warrant_build
        warrant_build.build(OUT, days, meta, T.log)
    except Exception as e:
        import traceback; T.log('權證整理失敗', e); traceback.print_exc()


main()
