#!/usr/bin/env python3
"""
融資看盤資料 → data/margin/daily.json

每天：加權指數（開高低收、成交金額）＋ 全市場融資餘額（金額，億元）、融資張數、融券張數。
資料來源 FinMind：TaiwanStockPrice（TAIEX）、TaiwanStockTotalMarginPurchaseShortSale。
第一次從 2005 年抓，之後只補最近 20 天。FinMind 憑證從環境變數 FINMIND_TOKEN 讀取，不會印出或存檔。
"""
import os, json, time, datetime, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'margin', 'daily.json')
TOKEN = os.environ.get('FINMIND_TOKEN', '').strip()
TW = datetime.timezone(datetime.timedelta(hours=8))


def api(**q):
    q['token'] = TOKEN
    u = 'https://api.finmindtrade.com/api/v4/data?' + urllib.parse.urlencode(q)
    err = None
    for k in range(4):
        try:
            with urllib.request.urlopen(u, timeout=120) as r:
                j = json.loads(r.read())
            if j.get('status') not in (200, None):
                raise RuntimeError(str(j.get('msg'))[:80])
            return j.get('data') or []
        except Exception as e:
            err = e; time.sleep(5 * (k + 1))
    raise RuntimeError(f"{q.get('dataset')} {q.get('start_date')}: {str(err).replace(TOKEN, '***')[:150]}")


def main():
    old = {}
    if os.path.exists(OUT):
        try:
            j = json.load(open(OUT))
            old = {r[0]: r for r in j['rows']}
        except Exception:
            old = {}
    today = datetime.datetime.now(TW).date()
    start = (max(old) if old else '2005-01-01')
    if old:
        start = (datetime.date.fromisoformat(max(old)) - datetime.timedelta(days=20)).isoformat()
    years = range(int(start[:4]), today.year + 1)
    px, mg = {}, {}
    for y in years:
        s = max(start, f'{y}-01-01'); e = f'{y}-12-31'
        for r in api(dataset='TaiwanStockPrice', data_id='TAIEX', start_date=s, end_date=e):
            px[r['date']] = (r['open'], r['max'], r['min'], r['close'], round((r.get('Trading_money') or 0) / 1e8, 1))
        for r in api(dataset='TaiwanStockTotalMarginPurchaseShortSale', start_date=s, end_date=e):
            d = mg.setdefault(r['date'], {})
            d[r['name']] = r.get('TodayBalance')
        time.sleep(1)
    rows = dict(old)
    for d, p in px.items():
        m = mg.get(d, {})
        money = m.get('MarginPurchaseMoney')
        prev = rows.get(d)
        rows[d] = [d, *p,
                   round(money / 1e8, 2) if money else (prev[6] if prev and len(prev) > 6 else None),
                   m.get('MarginPurchase') if m.get('MarginPurchase') is not None else (prev[7] if prev and len(prev) > 7 else None),
                   m.get('ShortSale') if m.get('ShortSale') is not None else (prev[8] if prev and len(prev) > 8 else None)]
    out = sorted(rows.values())
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    json.dump({'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'),
               'cols': ['d', 'o', 'h', 'l', 'c', 'amt', 'mBal', 'mLots', 'sLots'],
               'note': 'amt＝成交金額（億元）、mBal＝融資餘額（億元）、mLots＝融資餘額（張）、sLots＝融券餘額（張）',
               'rows': out}, open(OUT, 'w'), separators=(',', ':'))
    last = out[-1] if out else None
    print('rows', len(out), 'last', last)


if __name__ == '__main__':
    main()
