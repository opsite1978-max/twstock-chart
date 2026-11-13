#!/usr/bin/env python3
"""產業鏈地圖＋資金流向資料（推到 chain-data 分支，給 chain.html 讀）

一、產業鏈（每 7 天更新一次）：櫃買中心「產業價值鏈資訊平台」ic.tpex.org.tw，48 個產業的上游／中游／下游、
    每個環節（有些還分子環節）的上市、上櫃公司。→ chain.json
二、每天（收盤後）：FinMind
    - 全市場收盤價、成交量、成交金額（TaiwanStockPrice，不指定代號一次拿全部）
    - 全市場三大法人買賣（TaiwanStockInstitutionalInvestorsBuySell）
    - 市值、本益比、最新月營收與去年同月（算年增）
    - 主力買賣超：產業鏈裡、當天成交金額 ≥ 3,000 萬的個股，逐檔讀券商分點（TaiwanStockTradingDailyReport，一次一檔一天），
      主力＝當天買超最多的前 15 個分點的買超合計 − 賣超最多的前 15 個分點的賣超合計（台灣看盤軟體常用的定義）。
    每天的原始結果存成 d/日期.json，保留最近 30 個交易日；再整理成 stocks.json（每檔 1／5／20 日的漲跌、資金、法人、主力、集中度）。
FinMind 憑證從環境變數 FINMIND_TOKEN 讀取，不會印出或存檔。
"""
import json, os, re, sys, time, datetime, html, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import txo_scrape as T

OUT = sys.argv[1] if len(sys.argv) > 1 else 'chain-out'
T.REQ_BUDGET = int(os.environ.get('REQ_BUDGET', '2600'))
DEADLINE = time.time() + float(os.environ.get('MAX_MIN', '150')) * 60
KEEP_DAYS = 30
ZL_MIN_MONEY = 0.3      # 億元：成交金額門檻，低於這個不讀分點
BR_DAYS = 20            # 分點明細保留幾天
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'}


def jload(p, d):
    try:
        with open(os.path.join(OUT, p)) as f: return json.load(f)
    except Exception: return d


def jsave(p, o):
    fp = os.path.join(OUT, p); os.makedirs(os.path.dirname(fp) or '.', exist_ok=True)
    with open(fp, 'w') as f: json.dump(o, f, ensure_ascii=False, separators=(',', ':'))


def is_stock(c): return bool(re.fullmatch(r'\d{4}', c or ''))


# ───────── 一、產業鏈 ─────────
def fetch(u):
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r: return r.read().decode('utf-8', 'replace')
        except Exception as e:
            T.log('讀取失敗', u, str(e)[:80]); time.sleep(5 + 5 * k)
    return ''


def txt(s): return re.sub(r'\s+', '', html.unescape(re.sub(r'<br\s*/?>', '', re.sub(r'<(?!br)[^>]+>', '', s))))


def companies(block):
    """一段公司清單 → [(代號, 市場)]；只留上市、上櫃"""
    out = []; cat = ''
    for m in re.finditer(r"<b>([^<]*?)公司\(\d+家\)</b>|stk_code=(\w+)", block):
        if m.group(1) is not None: cat = m.group(1); continue
        if '興櫃' in cat or '創櫃' in cat: continue
        mk = '上市' if '上市' in cat else '上櫃' if '上櫃' in cat else ''
        if mk and is_stock(m.group(2)): out.append([m.group(2), mk])
    seen = set(); res = []
    for c, mk in out:
        if c not in seen: seen.add(c); res.append([c, mk])
    return res


def parse_industry(code, name, h):
    i = h.find('id="main_ic_panel"')
    if i < 0: return None
    j = h.find('id="companyList_', i)
    panel = h[i:j if j > 0 else len(h)]
    cols = []
    seg_re = r'(?:id="ic_link_(\w+)"[^>]*|class="company-chain-panel-blank"[^>]*)>(.*?)</div>'
    for blk in re.split(r'<div class="chain(?: chain\d+)?">', panel)[1:]:
        t = re.search(r'chain-title-panel">(.*?)<div class="chain-space"', blk, re.S)
        if t: parts = [txt(x) for x in re.split(r'<[^>]+>', t.group(1)) if txt(x)]
        else: parts = []
        title = '・'.join(dict.fromkeys(parts))
        segs = [{'id': m.group(1) or '', 'n': txt(m.group(2))} for m in re.finditer(seg_re, blk, re.S)]
        if segs: cols.append({'t': title, 'segs': segs})
    if not cols:   # 沒有上中下游的產業（金融、航運…）：全部環節放成一欄
        segs = [{'id': m.group(1) or '', 'n': txt(m.group(2))} for m in re.finditer(seg_re, panel, re.S)]
        if segs: cols = [{'t': '產業組成', 'segs': segs}]
    # 欄名空白時：三欄依序叫上游／中游／下游，其他叫第幾層
    if any(not c['t'] for c in cols):
        names = ['上游', '中游', '下游'] if len(cols) == 3 else ['上游', '下游'] if len(cols) == 2 else ['第%d層' % (k + 1) for k in range(len(cols))]
        for k, c in enumerate(cols):
            if not c['t']: c['t'] = names[k] if k < len(names) else '第%d層' % (k + 1)
    # 每個環節的公司清單（有子環節的另外拆）
    for col in cols:
        for sg in col['segs']:
            a = h.find('id="companyList_' + sg['id'] + '"') if sg['id'] else -1
            if a < 0: sg['s'] = []; continue
            b = h.find('id="companyList_', a + 20)
            block = h[a:b if b > 0 else len(h)]
            subs = [(m.group(1), txt(re.sub(r'&#9658;|\(\d+家\)|（\d+家）', '', m.group(2)))) for m in re.finditer(r'id="sc_link_(\w+)"[^>]*>(.*?)</div>', block, re.S)]
            if subs:
                sg['sub'] = []
                allc = {}
                for sid, sn in subs:
                    c = block.find('id="sc_company_' + sid + '"')
                    if c < 0: continue
                    d = block.find('id="sc_company_', c + 20)
                    lst = companies(block[c:d if d > 0 else len(block)])
                    sg['sub'].append({'id': sid, 'n': re.sub(r'\(.*?\)$', '', sn), 's': [x[0] for x in lst]})
                    for x in lst: allc[x[0]] = x[1]
                sg['s'] = list(allc)
            else:
                sg['s'] = [x[0] for x in companies(block)]
    return {'code': code, 'name': name, 'cols': cols}


def build_chain():
    old = jload('chain.json', None)
    if old and old.get('pv') == 2 and time.time() - old.get('u', 0) < 6.5 * 86400 and os.environ.get('CHAIN_FORCE') != '1':
        return old
    h = fetch('https://ic.tpex.org.tw/introduce.php?ic=D000')
    opts = re.findall(r"<option value='(\w+)'[^>]*>([^<]+)</option>", h)
    inds = []
    for code, name in opts:
        page = h if code == 'D000' else fetch('https://ic.tpex.org.tw/introduce.php?ic=' + code)
        ind = parse_industry(code, txt(name), page) if page else None
        if ind and ind['cols']:
            n = len({c for col in ind['cols'] for sg in col['segs'] for c in sg.get('s', [])})
            T.log('產業', code, name, '欄', [c['t'] for c in ind['cols']], '公司', n)
            inds.append(ind)
        time.sleep(0.6)
    if len(inds) < 30 and old: T.log('產業鏈抓到的太少，沿用舊的'); return old
    T.log('產業鏈共', len(inds), '個產業')
    ch = {'u': int(time.time()), 'pv': 2, 'src': '櫃買中心產業價值鏈資訊平台', 'inds': inds}
    jsave('chain.json', ch)
    return ch


# ───────── 二、每日資金與主力 ─────────
def zl_one(code, date):
    """一檔一天的券商分點。回傳 (主力摘要, 分點明細, 分點名稱)
    主力摘要 [主力買賣超張, 主力買賣超億, 有交易的分點數]
    分點明細：買超前 15、賣超前 15、買賣都很大（當沖）前 5 個分點，每個 [分點代號, 買張, 賣張, 買均價, 賣均價]"""
    rows = T.api({'dataset': 'TaiwanStockTradingDailyReport', 'data_id': code, 'start_date': date})
    br = {}; names = {}
    for r in rows:
        if r.get('date') != date: continue
        k = r.get('securities_trader_id') or r.get('securities_trader')
        names[k] = r.get('securities_trader') or k
        b = br.setdefault(k, [0, 0, 0.0, 0.0])     # 買股數, 賣股數, 買金額, 賣金額
        px = r.get('price') or 0; bq = r.get('buy') or 0; sq = r.get('sell') or 0
        b[0] += bq; b[1] += sq; b[2] += bq * px; b[3] += sq * px
    if not br: return None, [], {}
    net = lambda k: br[k][0] - br[k][1]
    ks = sorted(br, key=net)
    sell = [k for k in ks[:15] if net(k) < 0]; buy = [k for k in ks[::-1][:15] if net(k) > 0]
    zq = sum(net(k) for k in buy + sell)
    za = sum(br[k][2] - br[k][3] for k in buy + sell)
    pick = list(dict.fromkeys(buy + sell + sorted(br, key=lambda k: -min(br[k][0], br[k][1]))[:5]))
    det = []
    for k in pick:
        b = br[k]
        if b[0] + b[1] < 1000: continue
        det.append([k, round(b[0] / 1000), round(b[1] / 1000), round(b[2] / b[0], 2) if b[0] else 0, round(b[3] / b[1], 2) if b[1] else 0])
    return [round(zq / 1000), round(za / 1e8, 3), len(br)], det, {k: names[k] for k in pick}


def main():
    os.makedirs(OUT, exist_ok=True)
    ch = build_chain()
    chain_codes = sorted({c for ind in ch['inds'] for col in ind['cols'] for sg in col['segs'] for c in sg.get('s', [])})
    T.log('產業鏈公司', len(chain_codes))
    today = datetime.datetime.now(T.TW).date().isoformat()
    cal = sorted({r['date'] for r in T.api({'dataset': 'TaiwanStockTradingDate', 'start_date': (datetime.date.today() - datetime.timedelta(days=60)).isoformat()})})
    days = [d for d in cal if d <= today][-KEEP_DAYS:]
    # 價格、法人：缺的日子補
    for d in reversed(days):
        fp = 'd/' + d + '.json'; day = jload(fp, None)
        if day and day.get('p') and day.get('f'): continue
        if time.time() > DEADLINE: break
        try:
            px = T.api({'dataset': 'TaiwanStockPrice', 'start_date': d, 'end_date': d})
            inst = T.api({'dataset': 'TaiwanStockInstitutionalInvestorsBuySell', 'start_date': d, 'end_date': d})
        except T.RateLimited as e: T.log('停止', e); break
        p = {}
        for r in px:
            c = r['stock_id']
            if r.get('date') == d and is_stock(c) and r.get('close'):
                prev = r['close'] - (r.get('spread') or 0)
                p[c] = [r['close'], round((r['close'] / prev - 1) * 100, 2) if prev > 0 else 0, round((r.get('Trading_Volume') or 0) / 1000), round((r.get('Trading_money') or 0) / 1e8, 3)]
        if not p: T.log(d, '還沒有收盤資料'); continue
        f = {}
        for r in inst:
            c = r['stock_id']
            if r.get('date') != d or c not in p: continue
            nm = r.get('name', ''); net = ((r.get('buy') or 0) - (r.get('sell') or 0)) * p[c][0] / 1e8
            k = 0 if nm.startswith('Foreign') else 1 if nm == 'Investment_Trust' else 2
            a = f.setdefault(c, [0.0, 0.0, 0.0]); a[k] += net
        f = {c: [round(x, 3) for x in v] for c, v in f.items()}
        day = day or {}; day.update({'p': p, 'f': f}); day.setdefault('z', {})
        jsave(fp, day); T.log(d, '價格', len(p), '法人', len(f))
    # 市值、本益比、月營收
    meta = jload('meta.json', {})
    if meta.get('date') != days[-1] or meta.get('mv') != 2 or not meta.get('cap') or not meta.get('yoy'):
        try:
            # 全市場查詢一次只給一天：今天的市值還沒出來就往前找
            mv = []
            for d in reversed(days[-4:]):
                mv = T.api({'dataset': 'TaiwanStockMarketValue', 'start_date': d, 'end_date': d})
                if len(mv) > 500: break
            pe = T.api({'dataset': 'TaiwanStockPER', 'start_date': days[-1], 'end_date': days[-1]})
            # 月營收：資料日期是公布月份的 1 號；本月還沒公布夠多就用上個月，再抓去年同月
            td = datetime.date.today(); rv = []
            for back in (0, 1):
                y, m = td.year, td.month - back
                if m <= 0: y, m = y - 1, m + 12
                cur = T.api({'dataset': 'TaiwanStockMonthRevenue', 'start_date': '%d-%02d-01' % (y, m), 'end_date': '%d-%02d-01' % (y, m)})
                if len(cur) > 800:
                    old = T.api({'dataset': 'TaiwanStockMonthRevenue', 'start_date': '%d-%02d-01' % (y - 1, m), 'end_date': '%d-%02d-01' % (y - 1, m)})
                    rv = cur + old; break
            info = T.api({'dataset': 'TaiwanStockInfo'})
            cap = {r['stock_id']: round(r['market_value'] / 1e8) for r in mv if is_stock(r['stock_id'])}
            per = {r['stock_id']: [r.get('PER') or 0, r.get('PBR') or 0, r.get('dividend_yield') or 0] for r in pe if is_stock(r['stock_id'])}
            rev = {}
            for r in rv:
                if is_stock(r['stock_id']): rev.setdefault(r['stock_id'], {})[r['revenue_year'] * 100 + r['revenue_month']] = r['revenue']
            yoy = {}
            for c, m in rev.items():
                k = max(m); prev = m.get(k - 100)
                if prev and prev > 0: yoy[c] = [round((m[k] / prev - 1) * 100, 1), k % 100]
            names = {}; ind = {}
            for r in info:
                if is_stock(r['stock_id']): names[r['stock_id']] = r['stock_name']; ind[r['stock_id']] = r.get('industry_category', '')
            meta = {'date': days[-1], 'mv': 2, 'cap': cap, 'pe': per, 'yoy': yoy, 'name': names, 'ind': ind}
            jsave('meta.json', meta)
        except Exception as e:
            T.log('市值／本益比／營收失敗', e)
    # 主力與分點明細：新的日子先算，再往回補（只補最近 BR_DAYS 天）
    brokers = jload('brokers.json', {})
    stop = False
    for d in reversed(days[-BR_DAYS:]):
        fp = 'd/' + d + '.json'; day = jload(fp, None)
        if not day or not day.get('p'): continue
        day.setdefault('z', {}); day.setdefault('b', {})
        need = [c for c in chain_codes if c in day['p'] and day['p'][c][3] >= ZL_MIN_MONEY and c not in day['b']]
        if not need: continue
        T.log(d, '分點要讀', len(need), '檔')
        n = 0
        for c in sorted(need, key=lambda c: -day['p'][c][3]):
            if time.time() > DEADLINE or T.NREQ[0] >= T.REQ_BUDGET - 5: stop = True; break
            try: z, det, nm = zl_one(c, d)
            except T.RateLimited as e: T.log('停止', e); stop = True; break
            except Exception as e: T.log(c, d, '失敗', str(e)[:80]); continue
            day['z'][c] = z or [0, 0, 0]; day['b'][c] = det; brokers.update(nm); n += 1
            if n % 100 == 0: jsave(fp, day); jsave('brokers.json', brokers); T.log(d, '分點', n, '／', len(need), '查詢', T.NREQ[0])
        jsave(fp, day); jsave('brokers.json', brokers); T.log(d, '分點完成', n, '查詢累計', T.NREQ[0])
        if stop: break
    # 刪掉太舊的
    dd = os.path.join(OUT, 'd')
    for fn in os.listdir(dd) if os.path.isdir(dd) else []:
        if fn[:10] < days[0]: os.remove(os.path.join(dd, fn))
    # 整理成 stocks.json
    D = [d for d in days if jload('d/' + d + '.json', None)]
    data = {d: jload('d/' + d + '.json', {}) for d in D}
    out = {}
    codes = set(chain_codes) | set(data[D[-1]]['p']) if D else set()
    for c in codes:
        P = [data[d]['p'].get(c) for d in D]
        if not P[-1]: continue
        def ret(k):
            js = [i for i in range(len(D)) if P[i]]
            if len(js) <= k: return None
            return round((P[js[-1]][0] / P[js[-1 - k]][0] - 1) * 100, 2)
        def ssum(get, k):
            v = [get(d) for d in D[-k:]]; v = [x for x in v if x is not None]
            return (round(sum(v), 3), len(v)) if v else (None, 0)
        o = {'c': P[-1][0], 'r1': P[-1][1], 'r5': ret(5), 'r20': ret(20), 'm': P[-1][3]}
        ms = [data[d]['p'][c][3] for d in D[-20:] if c in data[d]['p']]
        o['m20'] = round(sum(ms) / len(ms), 3) if ms else None
        m5 = ms[-5:]; o['m5'] = round(sum(m5) / len(m5), 3) if m5 else None
        for k in (1, 5, 20):
            o['f%d' % k] = ssum(lambda d: sum(data[d].get('f', {}).get(c, [0, 0, 0])) if c in data[d]['p'] else None, k)[0]
            o['fF%d' % k] = ssum(lambda d: data[d].get('f', {}).get(c, [0, 0, 0])[0] if c in data[d]['p'] else None, k)[0]
            o['fT%d' % k] = ssum(lambda d: data[d].get('f', {}).get(c, [0, 0, 0])[1] if c in data[d]['p'] else None, k)[0]
            zq, nz = ssum(lambda d: data[d].get('z', {}).get(c, [None])[0] if c in data[d].get('z', {}) else None, k)
            za, _ = ssum(lambda d: data[d].get('z', {}).get(c, [0, None])[1] if c in data[d].get('z', {}) else None, k)
            vol, _ = ssum(lambda d: data[d]['p'][c][2] if c in data[d]['p'] and c in data[d].get('z', {}) else None, k)
            o['z%d' % k] = zq; o['za%d' % k] = za; o['zn%d' % k] = nz; o['v%d' % k] = vol
            o['k%d' % k] = round(zq / vol * 100, 1) if zq is not None and vol else None
        out[c] = o
    meta = jload('meta.json', {})
    for c, o in out.items():
        if c in meta.get('cap', {}): o['cap'] = meta['cap'][c]
        if c in meta.get('pe', {}): o['pe'], o['pb'], o['dy'] = meta['pe'][c]
        if c in meta.get('yoy', {}): o['yoy'], o['ym'] = meta['yoy'][c]
        o['n'] = meta.get('name', {}).get(c, '')
    zdays = [d for d in D if len(data[d].get('z', {})) > 50]
    jsave('stocks.json', {'u': int(time.time()), 'date': D[-1] if D else '', 'days': D, 'zdays': zdays, 's': out})
    T.log('完成：', len(out), '檔，交易日', len(D), '，主力有算的日子', len(zdays), '，查詢', T.NREQ[0])
    try:
        import branch_anomaly
        branch_anomaly.build(OUT, D, data, ch, out, jload('brokers.json', {}), T.log)
    except Exception as e:
        import traceback; T.log('異常分點整理失敗', e); traceback.print_exc()


main()
