#!/usr/bin/env python3
"""權證分點：每個券商分點每天買賣了哪些權證（推到 wt-data 分支）

一天的資料：
  1. 權證對照表（TaiwanStockInfoWithWarrantSummary，一次拿全部）：權證 → 標的股票、認購／認售、行使比例、履約價、到期日、權證收盤、標的收盤
  2. 每個券商分點一次查詢（taiwan_stock_warrant_trading_daily_report，用分點代號查，回傳它當天買賣的所有權證）
     → 依「標的股票」合計：[認購買進金額, 認購賣出金額, 認售買進金額, 認售賣出金額, 認購約當股數淨額, 認售約當股數淨額, 權證檔數]
       約當股數＝權證單位數 × 行使比例（等於這些權證相當於多少股標的）
存成 d/日期.json，從最近一天往回補 BACK 個交易日。
每輪查詢上限依 FinMind 這個小時還剩多少自動決定，保留一些給其他排程。
"""
import json, os, re, sys, time, datetime, urllib.request, urllib.parse, urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txo_scrape as T

OUT = sys.argv[1] if len(sys.argv) > 1 else 'wt-out'
BACK = int(os.environ.get('BACK', '60'))
RESERVE = int(os.environ.get('RESERVE', '1500'))
CAP = int(os.environ.get('CAP', '4000'))
DEADLINE = time.time() + float(os.environ.get('MAX_MIN', '100')) * 60
SP = 'https://api.finmindtrade.com/api/v4/taiwan_stock_warrant_trading_daily_report'
MIN_MONEY = 30000        # 一個分點在一檔標的的權證買賣合計 < 3 萬元就不存


def jload(p, d):
    try:
        with open(os.path.join(OUT, p)) as f: return json.load(f)
    except Exception: return d


def jsave(p, o):
    fp = os.path.join(OUT, p); os.makedirs(os.path.dirname(fp) or '.', exist_ok=True)
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


def main():
    os.makedirs(OUT, exist_ok=True)
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
    stop = False; got = 0
    for di, d in enumerate(reversed(days)):
        fp = 'd/' + d + '.json'; day = jload(fp, {})
        if 'm' not in day:
            try: ws = T.api({'dataset': 'TaiwanStockInfoWithWarrantSummary', 'start_date': d})
            except T.RateLimited as e: T.log('停止', e); break
            ws = [r for r in ws if r.get('date') == d]
            if len(ws) < 1000:
                T.log(d, '權證對照表還沒出來（', len(ws), '）'); continue
            m = {}; tc = {}
            for r in ws:
                tg = r.get('target_stock_id') or ''
                if not re.fullmatch(r'\d{4}', tg): continue
                m[r['stock_id']] = [tg, 'P' if '售' in (r.get('type') or '') else 'C', r.get('exercise_ratio') or 0, r.get('fulfillment_price') or 0, r.get('end_date') or '', r.get('close') or 0]
                if r.get('target_close'): tc[tg] = r['target_close']
            day = {'m': m, 'tc': tc, 'b': {}, 'done': []}
            jsave(fp, day); T.log(d, '權證', len(m), '檔，標的', len(tc), '檔')
        m = day['m']; done = set(day['done'])
        newest = di < 3
        todo = [t for t in traders if t not in done and (newest or act.get(t, [0, 0])[0] < 3 or act[t][1] > 0)]
        if not todo: continue
        T.log(d, '要查的分點', len(todo))
        for t in todo:
            if time.time() > DEADLINE or T.NREQ[0] >= T.REQ_BUDGET - 3: stop = True; break
            try: rows = special({'securities_trader_id': t, 'date': d})
            except T.RateLimited as e: T.log('停止', e); stop = True; break
            except Exception as e: T.log(t, d, '失敗', str(e)[:60]); continue
            agg = {}
            for r in rows:
                w = m.get(r.get('stock_id'))
                if not w: continue
                a = agg.setdefault(w[0], [0, 0, 0, 0, 0.0, 0.0, set()])
                bq = r.get('buy') or 0; sq = r.get('sell') or 0; px = r.get('price') or 0
                k = 0 if w[1] == 'C' else 2
                a[k] += bq * px; a[k + 1] += sq * px
                a[4 if w[1] == 'C' else 5] += (bq - sq) * w[2]
                a[6].add(r['stock_id'])
            keep = {tg: [round(a[0]), round(a[1]), round(a[2]), round(a[3]), round(a[4]), round(a[5]), len(a[6])] for tg, a in agg.items() if a[0] + a[1] + a[2] + a[3] >= MIN_MONEY}
            if keep: day['b'][t] = keep
            done.add(t); got += 1
            st = act.setdefault(t, [0, 0]); st[0] += 1; st[1] += 1 if rows else 0
            if got % 150 == 0: day['done'] = sorted(done); jsave(fp, day); jsave('meta.json', meta); T.log(d, '已查', len(done), '查詢', T.NREQ[0])
        day['done'] = sorted(done); jsave(fp, day); jsave('meta.json', meta)
        T.log(d, '完成', len(done), '／', len(traders), '有權證進出的分點', len(day['b']))
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
