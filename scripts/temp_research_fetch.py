#!/usr/bin/env python3
"""個股溫度研究用：全市場流動性夠的股票，2019 年起的日 K（推到 temp-data 分支，只給研究用）
每檔一次查詢；每輪依 FinMind 這個小時還剩多少自動決定，留一些給其他排程。
"""
import json, os, sys, time, datetime, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txo_scrape as T

OUT = sys.argv[1] if len(sys.argv) > 1 else 'temp-out'
START = os.environ.get('START', '2019-01-01')
DEADLINE = time.time() + float(os.environ.get('MAX_MIN', '90')) * 60


def quota_left():
    try:
        req = urllib.request.Request('https://api.web.finmindtrade.com/v2/user_info', headers={'Authorization': 'Bearer ' + T.TOKEN})
        with urllib.request.urlopen(req, timeout=30) as r: j = json.loads(r.read().decode())
        return int(j.get('api_request_limit_hour') or 6000) - int(j.get('user_count') or 0)
    except Exception: return 2500


def main():
    os.makedirs(os.path.join(OUT, 'px'), exist_ok=True)
    left = quota_left(); T.REQ_BUDGET = max(0, min(int(os.environ.get('CAP', '1500')), left - int(os.environ.get('RESERVE', '1500'))))
    T.log('FinMind 這小時剩', left, '次，本輪用', T.REQ_BUDGET)
    st = json.load(urllib.request.urlopen('https://raw.githubusercontent.com/opsite1978-max/twstock-chart/chain-data/stocks.json', timeout=60))['s']
    codes = sorted(c for c, v in st.items() if (v.get('m20') or 0) >= 0.3 and v.get('cap'))
    json.dump({'codes': codes, 'cap': {c: st[c]['cap'] for c in codes}, 'n': {c: st[c].get('n') for c in codes}}, open(os.path.join(OUT, 'meta.json'), 'w'), ensure_ascii=False)
    today = datetime.date.today().isoformat(); done = 0; todo = 0
    for c in codes:
        fp = os.path.join(OUT, 'px', c + '.json')
        if os.path.exists(fp) and json.load(open(fp)).get('to', '') >= today: continue
        todo += 1
        if time.time() > DEADLINE or T.NREQ[0] >= T.REQ_BUDGET - 3: continue
        try: rows = T.api({'dataset': 'TaiwanStockPrice', 'data_id': c, 'start_date': START})
        except T.RateLimited as e: T.log('停止', e); break
        except Exception as e: T.log(c, '失敗', str(e)[:60]); continue
        json.dump({'to': today, 'r': [[r['date'], r['open'], r['max'], r['min'], r['close'], r['Trading_Volume'], r.get('Trading_money') or 0] for r in rows]}, open(fp, 'w'), separators=(',', ':'))
        done += 1
    T.log('股票', len(codes), '檔，本輪抓了', done, '檔，還沒抓的', todo - done)
    if todo - done > 0: open('temp_more', 'w').write('1')


main()
