#!/usr/bin/env python3
"""起漲點回測用的歷史分點資料（推到 brbt-data 分支，只給研究用）

抽樣：產業鏈裡 20 日均成交金額 ≥ 0.5 億的股票，依市值排序後等距抽 SAMPLE 檔（大中小型都有）。
每檔每天：[買超分點家數, 賣超分點家數, 買超前 15＋賣超前 15 的 [分點, 買張, 賣張, 買均價, 賣均價]]
日期：最近 DAYS 個交易日，從最近往回補（每一輪先把所有股票的同一天補齊）。
另外存每檔的日 K（px/代號.json）。
每輪查詢上限依 FinMind 這個小時還剩多少自動決定，留 1,200 次給其他排程。
"""
import json, os, sys, time, datetime, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txo_scrape as T

OUT = sys.argv[1] if len(sys.argv) > 1 else 'brbt-out'
SAMPLE = int(os.environ.get('SAMPLE', '150'))
DAYS = int(os.environ.get('DAYS', '200'))
DEADLINE = time.time() + float(os.environ.get('MAX_MIN', '100')) * 60


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


def one(code, date):
    rows = T.api({'dataset': 'TaiwanStockTradingDailyReport', 'data_id': code, 'start_date': date})
    br = {}
    for r in rows:
        if r.get('date') != date: continue
        k = r.get('securities_trader_id') or r.get('securities_trader')
        b = br.setdefault(k, [0, 0, 0.0, 0.0]); px = r.get('price') or 0; bq = r.get('buy') or 0; sq = r.get('sell') or 0
        b[0] += bq; b[1] += sq; b[2] += bq * px; b[3] += sq * px
    if not br: return None
    net = lambda k: br[k][0] - br[k][1]
    ks = sorted(br, key=net)
    sell = [k for k in ks[:15] if net(k) < 0]; buy = [k for k in ks[::-1][:15] if net(k) > 0]
    det = [[k, round(br[k][0] / 1000), round(br[k][1] / 1000), round(br[k][2] / br[k][0], 2) if br[k][0] else 0, round(br[k][3] / br[k][1], 2) if br[k][1] else 0] for k in buy + sell]
    return [sum(1 for k in br if net(k) > 0), sum(1 for k in br if net(k) < 0), det]


def main():
    os.makedirs(OUT, exist_ok=True)
    left = quota_left(); T.REQ_BUDGET = max(0, min(4800, left - int(os.environ.get('RESERVE', '1200'))))
    T.log('FinMind 這小時剩', left, '次，本輪用', T.REQ_BUDGET)
    if T.REQ_BUDGET < 200: open('brbt_more', 'w').write('1'); return
    meta = jload('meta.json', None)
    if not meta:
        st = json.load(urllib.request.urlopen('https://raw.githubusercontent.com/opsite1978-max/twstock-chart/chain-data/stocks.json', timeout=60))['s']
        ch = json.load(urllib.request.urlopen('https://raw.githubusercontent.com/opsite1978-max/twstock-chart/chain-data/chain.json', timeout=60))
        cc = {c for ind in ch['inds'] for col in ind['cols'] for sg in col['segs'] for c in sg.get('s', [])}
        pool = sorted([c for c in cc if c in st and (st[c].get('m20') or 0) >= 0.5 and st[c].get('cap')], key=lambda c: -st[c]['cap'])
        step = len(pool) / SAMPLE
        codes = sorted({pool[int(i * step)] for i in range(SAMPLE)})
        cal = sorted({r['date'] for r in T.api({'dataset': 'TaiwanStockTradingDate', 'start_date': (datetime.date.today() - datetime.timedelta(days=int(DAYS * 1.6) + 30)).isoformat()})})
        today = datetime.datetime.now(T.TW).date().isoformat()
        days = [d for d in cal if d <= today][-DAYS:]
        meta = {'codes': codes, 'days': days, 'pool': len(pool)}
        jsave('meta.json', meta); T.log('抽樣', len(codes), '檔（共', len(pool), '檔），', days[0], '～', days[-1])
    codes, days = meta['codes'], meta['days']
    # 日 K（多抓 60 天前面的、和到今天為止的後面）
    for c in codes:
        px = jload('px/' + c + '.json', None)
        if px and px.get('to', '') >= datetime.date.today().isoformat(): continue
        if T.NREQ[0] >= T.REQ_BUDGET - 5: break
        start = (datetime.date.fromisoformat(days[0]) - datetime.timedelta(days=120)).isoformat()
        rows = T.api({'dataset': 'TaiwanStockPrice', 'data_id': c, 'start_date': start})
        jsave('px/' + c + '.json', {'to': datetime.date.today().isoformat(), 'r': [[r['date'], r['open'], r['max'], r['min'], r['close'], r['Trading_Volume']] for r in rows]})
    store = {c: jload('bt/' + c + '.json', {}) for c in codes}
    done = 0; stop = False
    for d in reversed(days):
        for c in codes:
            if d in store[c]: continue
            if time.time() > DEADLINE or T.NREQ[0] >= T.REQ_BUDGET - 5: stop = True; break
            try: store[c][d] = one(c, d)
            except T.RateLimited as e: T.log('停止', e); stop = True; break
            except Exception as e: T.log(c, d, '失敗', str(e)[:60]); continue
            done += 1
        if stop: break
    for c in codes: jsave('bt/' + c + '.json', store[c])
    have = sum(len(v) for v in store.values()); need = len(codes) * len(days)
    T.log('本輪讀了', done, '筆；累計', have, '／', need)
    if have < need and done > 50: open('brbt_more', 'w').write('1')


main()
