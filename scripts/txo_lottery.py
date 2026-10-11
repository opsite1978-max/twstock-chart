#!/usr/bin/env python3
"""
結算日「樂透選擇權」回測資料 → data/txo/lottery.json

對過去約一年的每一個台指選擇權結算日（週三、週五週選與月選）：
  前一個交易日（D）收盤後的籌碼：零 Gamma、最大賣權／買權未平倉履約價、Gamma 曝險牆、最大痛點
  D 晚上的夜盤（FinMind 記在結算日 E 的 after_market）：台指期夜盤收盤、最低、最高
  在夜盤收盤價的總 Gamma 曝險（只算即將結算的合約、以及全部合約）
  結算日 E：台指期日盤開盤、到期合約各履約價的開盤價、最高價、成交量
  結算價（由到期合約收盤前的買賣權價格反推）
網頁依這些資料統計「夜盤跌破零 Gamma 與最大賣權牆時，結算日買價外賣權」的結果。

  python3 scripts/txo_lottery.py update
"""
import json, os, sys, re, datetime, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txo_scrape as T

OUT = os.path.join(T.OUT, 'lottery.json')
BACK = int(os.environ.get('BACK_DAYS', '250'))


def chain(rows):
    """{code: {K: {...}}}（只取日盤 position）"""
    by = {}
    for r in rows:
        if r.get('trading_session') != 'position':
            continue
        code = str(r.get('contract_date') or '').strip()
        if not T.contract_exp(code):
            continue
        K = float(r['strike_price'])
        s = by.setdefault(code, {}).setdefault(K, {'K': K})
        side = 'c' if r['call_put'] == 'call' else 'p'
        s[side] = {'o': float(r.get('open') or 0), 'h': float(r.get('max') or 0), 'l': float(r.get('min') or 0),
                   'cl': float(r.get('close') or 0), 'st': float(r.get('settlement_price') or 0),
                   'v': int(r.get('volume') or 0), 'oi': int(r.get('open_interest') or 0)}
    return by


def settle_est(S, guess):
    """到期日收盤前的買賣權收盤價反推結算價：K + 買權 − 賣權，取價平附近幾檔的中位數"""
    xs = []
    for K, s in S.items():
        c, p = s.get('c'), s.get('p')
        if c and p and c['v'] > 0 and p['v'] > 0 and abs(K - guess) < guess * 0.02:
            xs.append((abs(c['cl'] - p['cl']), K + c['cl'] - p['cl']))
    if xs:
        xs.sort()
        return statistics.median(v for _, v in xs[:5])
    return None


def cmd_update():
    if not T.TOKEN:
        T.log('沒有 FINMIND_TOKEN'); return
    today = datetime.datetime.now(T.TW).date().isoformat()
    cal_rows = T.api({'dataset': 'TaiwanStockTradingDate', 'start_date': (datetime.date.today() - datetime.timedelta(days=int(BACK * 1.6) + 30)).isoformat()})
    cal = sorted({r['date'] for r in cal_rows}); T.CALSET.update(cal)
    past = [d for d in cal if d <= today][-BACK:]
    old = {}
    if os.path.exists(OUT):
        for x in json.load(open(OUT, encoding='utf-8')).get('rows', []):
            old[(x['E'], x['code'])] = x
    # 台指期：日盤＋夜盤（夜盤記在下一個交易日）
    fr = T.api({'dataset': 'TaiwanFuturesDaily', 'data_id': 'TX', 'start_date': past[0], 'end_date': today})
    fut = {}
    for r in fr:
        c = str(r.get('contract_date') or '').strip()
        if not re.fullmatch(r'\d{6}', c) or not (r.get('close') or 0) > 0:
            continue
        k = (r['date'], r.get('trading_session'))
        if k not in fut or (r.get('volume') or 0) > (fut[k].get('volume') or 0):
            fut[k] = r
    rows_cache = {}

    def day_rows(d):
        if d not in rows_cache:
            rows_cache[d] = T.api({'dataset': 'TaiwanOptionDaily', 'data_id': 'TXO', 'start_date': d, 'end_date': d})
        return rows_cache[d]

    out = dict(old)
    try:
        for i in range(1, len(past)):
            E, D = past[i], past[i - 1]
            if E >= today and datetime.datetime.now(T.TW).hour < 15:
                continue
            # 這一天結算的合約（由合約代號推算，遇假日順延）
            rowsD = day_rows(D)
            chD = chain(rowsD)
            codes = [c for c in chD if T.adjust_exp(T.contract_exp(c), cal) == E]
            codes = [c for c in codes if (E, c) not in old]
            if not codes:
                rows_cache.pop(D, None); continue
            rowsE = day_rows(E)
            chE = chain(rowsE)
            fD, fE, fN = fut.get((D, 'position')), fut.get((E, 'position')), fut.get((E, 'after_market'))
            if not fD or not fE:
                continue
            P_D = float(fD['close'])
            N = float(fN['close']) if fN else None
            for code in codes:
                SD, SE = chD.get(code, {}), chE.get(code, {})
                if not SE:
                    continue
                # D 收盤的 Gamma 計算（沿用 txo_scrape 的方法）
                resD = T.analyse_day(D, rowsD, P_D, cal)
                near_only = [r for r in rowsD if str(r.get('contract_date') or '').strip() == code]
                resN_near = T.analyse_day(D, near_only, N or P_D, cal)
                resN_all = T.analyse_day(D, rowsD, N or P_D, cal)
                resD_near = T.analyse_day(D, near_only, P_D, cal)
                oiP = max(SD.values(), key=lambda s: (s.get('p') or {}).get('oi', 0))['K'] if SD else None
                oiC = max(SD.values(), key=lambda s: (s.get('c') or {}).get('oi', 0))['K'] if SD else None
                O = float(fE['open'])
                st = settle_est(SE, float(fE['close']))
                opts = []
                for K, s in sorted(SE.items()):
                    for side in ('p', 'c'):
                        x = s.get(side)
                        if not x or not x['o'] > 0:
                            continue
                        dist = (K - O) / O * 100
                        if (side == 'p' and -4 <= dist <= 0.5) or (side == 'c' and -0.5 <= dist <= 4):
                            opts.append([side, K, x['o'], x['h'], x['l'], x['cl'], x['v']])
                out[(E, code)] = {
                    'E': E, 'D': D, 'code': code, 'cD': P_D, 'n': N,
                    'nl': float(fN['min']) if fN else None, 'nh': float(fN['max']) if fN else None,
                    'o': O, 'h': float(fE['max']), 'l': float(fE['min']), 'c': float(fE['close']), 'st': round(st, 1) if st else None,
                    'flipAll': resD and resD['flip'], 'flipNear': resD_near and resD_near['flip'],
                    'gexNearD': resD_near and resD_near['gex'], 'gexNearN': resN_near and resN_near['gex'],
                    'gexAllN': resN_all and resN_all['gex'], 'pwOI': oiP, 'cwOI': oiC,
                    'pwG': resD_near and resD_near['pw'], 'cwG': resD_near and resD_near['cw'],
                    'pain': resD_near and resD_near['pain'], 'opts': opts,
                }
                T.log('ok', E, code, 'night', N, 'settle', st)
            rows_cache.pop(D, None)
    except T.RateLimited as e:
        T.log('STOP', e)
    rows = sorted(out.values(), key=lambda x: (x['E'], x['code']))
    json.dump({'updated': datetime.datetime.now(T.TW).strftime('%Y-%m-%d %H:%M'), 'rows': rows},
              open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    open(os.path.join(T.OUT, '_lastrun_lottery.txt'), 'w', encoding='utf-8').write('\n'.join(T.LOG[-300:]) + '\n')
    T.log('rows', len(rows), 'requests', T.NREQ[0])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'update':
        cmd_update()
    else:
        print(__doc__)
