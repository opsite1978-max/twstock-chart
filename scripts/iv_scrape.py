#!/usr/bin/env python3
"""
台指選擇權隱含波動率歷史 → data/txo/iv.json（給 iv.html 隱含波動率頁）

每個交易日：用收盤後各結算日台指選擇權的買權、賣權結算價，
  1. 以買賣權平價找出該合約的遠期價；
  2. 用價外那一邊（遠期價以下用賣權、以上用買權）反推 Black-76 隱含波動率，
     在遠期價上下兩個履約價之間內插，得到「價平隱含波動率」；
  3. 把前後兩個到期日的變異數依時間內插到 30 天 → iv30（概念同台指 VIX，曲線較平順）；
     另存「距結算 5 天以上的最近合約」價平隱含波動率 → near（對週選比較敏感）。

查詢一次抓一週（7 個日曆天）的全部 TXO 資料，缺的日期才抓，每次最多 REQ_BUDGET 次查詢，
不會吃掉網站其他功能的 FinMind 額度。

  python3 scripts/iv_scrape.py update

FinMind 憑證從環境變數 FINMIND_TOKEN 讀取（GitHub 加密密碼），程式不會印出或存檔。
"""
import json, os, sys, math, time, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txo_scrape as T   # 共用：查詢（含限流）、結算日推算、Black-76 反推波動率

ROOT = T.ROOT
OUT = os.path.join(ROOT, 'data', 'txo')
IVF = os.path.join(OUT, 'iv.json')
TW = T.TW
BACK_DAYS = int(os.environ.get('IV_BACK_DAYS', '420'))      # 日曆天，約 290 個交易日
T.REQ_BUDGET = int(os.environ.get('REQ_BUDGET', '45'))
CHUNK = 7


def atm_iv(S, F, Ty):
    """S: {K: {'c': 價, 'p': 價}}，回傳遠期價 F 的價平隱含波動率"""
    ks = sorted(k for k, o in S.items() if o.get('c', 0) > 0 and o.get('p', 0) > 0)
    if len(ks) < 3:
        return None
    lo = max((k for k in ks if k <= F), default=None)
    hi = min((k for k in ks if k >= F), default=None)

    def iv_at(k):
        c = T.implied_vol(S[k]['c'], F, k, Ty, True)
        p = T.implied_vol(S[k]['p'], F, k, Ty, False)
        if k < F:
            return p if p is not None else c
        if k > F:
            return c if c is not None else p
        return (c + p) / 2 if c is not None and p is not None else (c if c is not None else p)

    if lo is not None and hi is not None and hi != lo:
        a, b = iv_at(lo), iv_at(hi)
        if a is None or b is None:
            return a if a is not None else b
        return a + (b - a) * (F - lo) / (hi - lo)
    return iv_at(lo if lo is not None else hi)


def iv_day(date, rows, cal):
    by = {}
    for r in rows:
        if r.get('trading_session') != 'position':
            continue
        code = str(r.get('contract_date') or '').strip()
        exp = T.contract_exp(code)
        if not exp:
            continue
        exp = T.adjust_exp(exp, cal)
        if exp <= date:
            continue
        px = float(r.get('settlement_price') or 0) or float(r.get('close') or 0)
        if px <= 0:
            continue
        K = float(r['strike_price'])
        e = by.setdefault(code, {'exp': exp, 'S': {}})
        e['S'].setdefault(K, {})['c' if r.get('call_put') == 'call' else 'p'] = px
    pts = []
    for code, e in by.items():
        days = T.day_diff(date, e['exp']) - 0.01
        if days < 3 or days > 120:
            continue
        Ty = days / 365
        both = [(k, o) for k, o in e['S'].items() if o.get('c', 0) > 0 and o.get('p', 0) > 0]
        if len(both) < 3:
            continue
        k0, o0 = min(both, key=lambda x: abs(x[1]['c'] - x[1]['p']))
        F = k0 + o0['c'] - o0['p']
        iv = atm_iv(e['S'], F, Ty)
        if iv and 0.02 < iv < 2:
            pts.append({'T': Ty, 'days': days, 'iv': iv, 'code': code})
    if not pts:
        return None
    pts.sort(key=lambda p: p['T'])
    near = next((p for p in pts if p['days'] >= 5), pts[0])
    T30 = 30 / 365
    i = next((j for j, p in enumerate(pts) if p['T'] >= T30), None)
    if i is None:
        iv30 = pts[-1]['iv']
    elif i == 0:
        iv30 = pts[0]['iv']
    else:
        a, b = pts[i - 1], pts[i]
        w = a['iv'] ** 2 * a['T'] + (b['iv'] ** 2 * b['T'] - a['iv'] ** 2 * a['T']) * (T30 - a['T']) / (b['T'] - a['T'])
        iv30 = math.sqrt(max(w, 0) / T30)
    return {'iv30': round(iv30 * 100, 2), 'near': round(near['iv'] * 100, 2), 'nc': near['code']}


def save(hist):
    days = [dict(d=d, **hist[d]) for d in sorted(hist)]
    json.dump({'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M'), 'days': days},
              open(IVF, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    open(os.path.join(OUT, '_lastrun_iv.txt'), 'w', encoding='utf-8').write('\n'.join(T.LOG[-200:]) + '\n')


def cmd_update():
    if not T.TOKEN:
        T.log('沒有 FINMIND_TOKEN'); return
    os.makedirs(OUT, exist_ok=True)
    hist = {}
    if os.path.exists(IVF):
        for x in json.load(open(IVF, encoding='utf-8')).get('days', []):
            hist[x.pop('d')] = x
    now = datetime.datetime.now(TW)
    today = now.date().isoformat()
    start = (now.date() - datetime.timedelta(days=BACK_DAYS)).isoformat()
    cal = sorted({r['date'] for r in T.api({'dataset': 'TaiwanStockTradingDate', 'start_date': start})})
    T.CALSET.update(cal)
    past = [d for d in cal if start <= d <= today]
    # 今天收盤資料約 15:00 後才完整；當天的值在 17 點前算的會在下次重算
    todo = [d for d in past if d not in hist or (d == past[-1] and not hist[d].get('final'))]
    T.log('trading days', len(past), 'have', len(hist), 'todo', len(todo))
    todo.sort(reverse=True)                     # 先補最近的
    done = 0
    try:
        while todo:
            end = todo[0]
            st = (datetime.date.fromisoformat(end) - datetime.timedelta(days=CHUNK - 1)).isoformat()
            rows = T.api({'dataset': 'TaiwanOptionDaily', 'data_id': 'TXO', 'start_date': st, 'end_date': end})
            byd = {}
            for r in rows:
                byd.setdefault(r['date'], []).append(r)
            chunk_days = [d for d in todo if st <= d <= end]
            for d in chunk_days:
                todo.remove(d)
                res = iv_day(d, byd.get(d, []), cal) if byd.get(d) else None
                if not res:
                    T.log('no data', d); continue
                res['final'] = d < today or now.hour >= 17
                hist[d] = res
                done += 1
            T.log('chunk', st, end, 'rows', len(rows), 'done', done)
            save(hist)
            time.sleep(2)
    except T.RateLimited as e:
        T.log('stop:', e)
    T.log('finished, computed', done, 'left', len(todo))
    save(hist)


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'update':
        cmd_update()
    else:
        print(__doc__)
