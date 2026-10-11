#!/usr/bin/env python3
"""
權證每日成交 → 依「標的股票」彙總，找出近期權證大買的標的。在 GitHub Actions 上執行，只用標準函式庫。

來源：
  上市：證交所每日收盤行情 type=0999（認購權證）、0999P（認售權證），每檔權證都附標的代號
  上櫃：櫃買中心每日收盤行情 se=WW（權證），只有權證名稱，用名稱開頭比對標的（例：振曜群益5A售01 → 振曜）

每日檔 data/warrant/days/<日期>.json：{"d":日期,"r":{標的代號:[認購金額, 認購漲價金額, 認購跌價金額, 認售金額, 認售漲價金額, 認售跌價金額, 認購檔數]}}
  金額單位：萬元。「漲價金額」＝當天權證價格上漲的那些權證的成交金額，代表買方追價；「跌價金額」反之。
summary.json：每檔標的最近 1／5／10／20 日與前 60 日基準的彙總
"""
import json, os, re, sys, time, datetime, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DAYS = os.path.join(ROOT, 'data', 'warrant', 'days')
OUT = os.path.join(ROOT, 'data', 'warrant')
UNIV = os.path.join(ROOT, 'insider', 'universe.json')
ALIAS = os.path.join(OUT, 'alias.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TW = datetime.timezone(datetime.timedelta(hours=8))
LOOKBACK_DAYS = 120
WINDOWS = (1, 5, 10, 20)
BASE = 60
DETAIL_DAYS = 22
DETAIL_MIN = 100000   # 逐檔明細只留單日成交 10 萬元以上的權證（約占總成交金額九成以上），節省空間
GAP = 3.2

LOG = []
_print = print
def print(*a, **k):
    _print(*a, **k)
    LOG.append(datetime.datetime.now(TW).strftime('%m-%d %H:%M:%S ') + ' '.join(str(x) for x in a))

_last = [0.0]
def get_json(url):
    wait = GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                s = r.read().decode('utf-8-sig', 'replace')
            _last[0] = time.time()
            return json.loads(s)
        except Exception as e:
            _last[0] = time.time()
            print('  retry', url[:100], type(e).__name__, str(e)[:80], flush=True)
            time.sleep(15 * (attempt + 1))
    raise RuntimeError('fetch failed')

def num(x):
    try:
        return float(str(x).replace(',', '').strip())
    except ValueError:
        return 0.0

def direction(s):
    """漲跌欄位 → +1／-1／0"""
    s = str(s)
    if 'color:red' in s or s.strip().startswith('+'):
        return 1
    if 'color:green' in s or s.strip().startswith('-'):
        return -1
    t = re.sub(r'<[^>]+>', '', s).strip()
    return 1 if t == '+' else -1 if t == '-' else 0

def find_table(j, key):
    for t in j.get('tables') or []:
        if t and t.get('data') and key in (t.get('title') or ''):
            return t
    return None

def fetch_twse(d, typ):
    j = get_json('https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date=%s&type=%s&response=json' % (d.strftime('%Y%m%d'), typ))
    if j.get('stat') not in (None, 'OK'):
        return None
    t = find_table(j, '權證')
    if not t:
        return None
    f = t['fields']
    i_code, i_val, i_dir, i_chg = f.index('證券代號'), f.index('成交金額'), f.index('漲跌(+/-)'), f.index('漲跌價差')
    i_u, i_un, i_close, i_vol = f.index('標的代號'), f.index('標的名稱'), f.index('收盤價'), f.index('成交股數')
    rows = []
    for r in t['data']:
        val = num(r[i_val])
        dirn = direction(r[i_dir]) if num(r[i_chg]) else 0
        st = lambda x: str(x if x is not None else '').strip()
        rows.append((st(r[i_u]), st(r[i_un]), val, dirn, st(r[i_code]), num(r[i_close]) or None, num(r[i_vol])))
    return rows

def fetch_tpex(d):
    roc = '%d/%02d/%02d' % (d.year - 1911, d.month, d.day)
    j = get_json('https://www.tpex.org.tw/web/stock/aftertrading/otc_quotes_no1430/stk_wn1430_result.php?l=zh-tw&o=json&d=%s&se=WW' % roc)
    t = (j.get('tables') or [{}])[0]
    data = t.get('data') or j.get('aaData') or []
    if not data:
        return None
    if t.get('date') and t['date'] != roc:
        return None
    rows = []
    for r in data:
        code, name, chg, val = str(r[0] or '').strip(), str(r[1] or '').strip(), r[3], num(r[8])
        kind = 'P' if '售' in name else 'C' if '購' in name else ''
        if not kind:
            continue   # 牛熊證等略過
        dv = num(chg) if re.match(r'^[+-]?\d', str(chg).strip()) else 0
        rows.append((code, name, kind, val, 1 if dv > 0 else -1 if dv < 0 else 0, num(r[2]) or None, num(r[7])))
    return rows

def fetch_tpex_map():
    """櫃買中心當日權證行情（只有最新一天，但每檔都附標的代號）→ 權證代號：標的代號"""
    try:
        d = get_json('https://www.tpex.org.tw/openapi/v1/tpex_warrant_quts')
        return {x['Code'].strip(): x['UnderlyingStockCode'].strip() for x in d if x.get('Code') and x.get('UnderlyingStockCode')}
    except Exception as e:
        print('tpex warrant map failed', e, flush=True)
        return {}

def roc_date(s):
    s = str(s).strip()
    if not re.fullmatch(r'\d{7}', s):
        return ''
    return '%d-%s-%s' % (int(s[:3]) + 1911, s[3:5], s[5:7])

def fetch_meta():
    """上市、上櫃權證基本資料：履約價、最後交易日、行使比例、認購／認售"""
    meta = {}
    for url in ('https://openapi.twse.com.tw/v1/opendata/t187ap37_L', 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap37_O'):
        try:
            for x in get_json(url):
                c = (x.get('權證代號') or '').strip()
                if not c:
                    continue
                meta[c] = [x.get('權證簡稱', '').strip(), 'P' if '售' in x.get('權證類型', '') else 'C',
                           num(x.get('最新履約價格(元)/履約指數')), roc_date(x.get('最後交易日')),
                           num(x.get('最新標的履約配發數量(每仟單位權證)')) / 1000]
        except Exception as e:
            print('meta failed', url[:60], e, flush=True)
    return meta

def load_alias():
    a = {}
    if os.path.exists(UNIV):
        for s in json.load(open(UNIV, encoding='utf-8'))['stocks']:
            a[s['name']] = s['code']
    if os.path.exists(ALIAS):
        a.update(json.load(open(ALIAS, encoding='utf-8')))
    return a

def match_name(name, alias, keys):
    for k in keys:   # 已依長度由長到短排序
        if name.startswith(k):
            return alias[k]
    return None

def add(agg, code, kind, val, dirn):
    v = agg.setdefault(code, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0])
    base = 0 if kind == 'C' else 3
    v[base] += val
    if dirn > 0:
        v[base + 1] += val
    elif dirn < 0:
        v[base + 2] += val
    if kind == 'C' and val > 0:
        v[6] += 1

def cmd_update():
    os.makedirs(DAYS, exist_ok=True)
    nonef = os.path.join(DAYS, '_none.json')
    none = set(json.load(open(nonef))) if os.path.exists(nonef) else set()
    univ = {s['code'] for s in json.load(open(UNIV, encoding='utf-8'))['stocks']} if os.path.exists(UNIV) else None
    alias = load_alias()
    wmapf = os.path.join(OUT, 'wmap.json')
    wmap = json.load(open(wmapf)) if os.path.exists(wmapf) else {}
    wmap.update(fetch_tpex_map())
    now = datetime.datetime.now(TW)
    today = now.date()
    t0, got = time.time(), 0
    for k in range(LOOKBACK_DAYS):
        d = today - datetime.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        key = d.isoformat()
        fp = os.path.join(DAYS, key + '.json')
        if key in none:
            continue
        if os.path.exists(fp):
            if k > 40 or '"w":' in open(fp, encoding='utf-8').read():
                continue   # 已有；最近約 25 個交易日若是舊格式（沒有逐檔明細）就重抓
        if d == today and now.hour < 15:
            continue
        if time.time() - t0 > 2100:
            print('time budget reached', flush=True); break
        try:
            calls = fetch_twse(d, '0999')
            if calls is None:
                if d < today:
                    none.add(key)
                print(key, 'no data (holiday?)', flush=True); continue
            puts = fetch_twse(d, '0999P') or []
            otc = fetch_tpex(d)
        except Exception as e:
            print('FAIL', key, e, flush=True); continue
        if otc is None:
            if (today - d).days <= 3:
                print(key, 'OTC not published yet, retry later', flush=True); continue
            print(key, 'OTC data unavailable, saving listed warrants only', flush=True)
            otc = []
        agg, wl = {}, []
        for kind, rows in (('C', calls), ('P', puts)):
            for ucode, uname, val, dirn, wcode, close, vol in rows:
                if uname and ucode and re.fullmatch(r'[0-9A-Z]{4,6}', ucode):
                    alias.setdefault(uname, ucode)   # 權證用的標的簡稱 → 代號
                add(agg, ucode, kind, val, dirn)
                if val >= DETAIL_MIN:
                    wl.append([wcode, ucode, kind, round(val), dirn, close, round(vol / 1000)])
        keys = sorted(alias, key=len, reverse=True)
        miss = 0
        for code, name, kind, val, dirn, close, vol in otc:
            u = wmap.get(code) or match_name(name, alias, keys)
            if not u:
                miss += val > 0; continue
            wmap.setdefault(code, u)
            add(agg, u, kind, val, dirn)
            if val >= DETAIL_MIN:
                wl.append([code, u, kind, round(val), dirn, close, round(vol / 1000)])
        rows = {c: [round(x / 1e4) for x in v[:6]] + [v[6]] for c, v in agg.items()
                if (univ is None or c in univ) and (v[0] or v[3])}
        with open(os.path.join(DAYS, key + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'d': key, 'r': rows, 'w': [x for x in wl if univ is None or x[1] in univ]}, f, separators=(',', ':'))
        got += 1
        print(key, 'underlyings', len(rows), 'otc unmatched traded', miss, flush=True)
    with open(nonef, 'w') as f:
        json.dump(sorted(x for x in none if x >= (today - datetime.timedelta(days=LOOKBACK_DAYS + 10)).isoformat()), f)
    with open(wmapf, 'w') as f:
        json.dump(wmap, f, separators=(',', ':'))
    # 只有最近 25 個交易日保留逐檔權證明細，舊的拿掉以節省空間；最近 25 天若缺明細就重抓
    files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
    for i, fn in enumerate(reversed(files)):
        fp = os.path.join(DAYS, fn); d = json.load(open(fp, encoding='utf-8'))
        if i >= DETAIL_DAYS and 'w' in d:
            del d['w']; json.dump(d, open(fp, 'w', encoding='utf-8'), separators=(',', ':'))
    with open(ALIAS, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in sorted(alias.items())}, f, ensure_ascii=False, separators=(',', ':'))
    cutoff = (today - datetime.timedelta(days=LOOKBACK_DAYS + 10)).isoformat()
    for fn in os.listdir(DAYS):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn) and fn[:10] < cutoff:
            os.remove(os.path.join(DAYS, fn))
    print('fetched days', got, flush=True)
    build()
    with open(os.path.join(OUT, '_lastrun.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG[-300:]) + '\n')

def build():
    files = sorted(fn for fn in os.listdir(DAYS) if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn))
    days = [json.load(open(os.path.join(DAYS, fn), encoding='utf-8')) for fn in files][::-1]   # 新到舊
    codes = set()
    for d in days[:max(WINDOWS)]:
        codes.update(d['r'])
    rows = {}
    for c in sorted(codes):
        out = []
        for w in WINDOWS:
            seg = [d['r'].get(c) for d in days[:w]]
            out.append([sum(v[i] for v in seg if v) for i in range(6)])
        # 基準：最近 20 日之前的 60 個交易日，每日平均認購、認售金額
        base = [d['r'].get(c) for d in days[max(WINDOWS):max(WINDOWS) + BASE]]
        nb = len(base)
        bc = round(sum(v[0] for v in base if v) / nb, 1) if nb else None
        bp = round(sum(v[3] for v in base if v) / nb, 1) if nb else None
        latest = days[0]['r'].get(c) if days else None
        rows[c] = {'w': out, 'bc': bc, 'bp': bp, 'n': latest[6] if latest else 0,
                   'daily': [[d['r'][c][0], d['r'][c][3]] if c in d['r'] else [0, 0] for d in days[:20]][::-1]}
    s = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'),
         'latest': days[0]['d'] if days else '', 'dates': [d['d'] for d in days[:20]][::-1],
         'baseDays': min(BASE, max(0, len(days) - max(WINDOWS))), 'windows': list(WINDOWS),
         'note': '金額單位萬元；w[i]=[認購, 認購漲價, 認購跌價, 認售, 認售漲價, 認售跌價]；bc/bp=基準期每日平均認購/認售金額',
         'rows': rows}
    with open(os.path.join(OUT, 'summary.json'), 'w', encoding='utf-8') as f:
        json.dump(s, f, ensure_ascii=False, separators=(',', ':'))
    print('summary', len(rows), 'underlyings', len(days), 'days, base', s['baseDays'], flush=True)
    build_detail(days)

def issuer_of(name, alias_keys):
    for k in alias_keys:
        if name.startswith(k):
            rest = name[len(k):]
            m = re.match(r'([^\d]+)', rest)
            return m.group(1) if m else ''
    m = re.match(r'(.+?)(元大|凱基|群益|統一|富邦|國泰|永豐金|永豐|兆豐|元富|中信|台新|康和|第一|華南|國票|玉山|日盛|宏遠|亞東|摩根大通|麥格理|法興|口袋|北富銀|新光|大華)', name)
    return m.group(2) if m else ''

def build_detail(days):
    """每檔標的一個檔：每日認購／認售分布（全部天數）＋最近 20 日逐檔權證彙總"""
    ud = os.path.join(OUT, 'u')
    os.makedirs(ud, exist_ok=True)
    meta = fetch_meta()   # 權證基本資料每次重新下載，不存進專案
    alias = load_alias(); keys = sorted(alias, key=len, reverse=True)
    det = [d for d in days[:20] if 'w' in d]
    used = set()
    per = {}
    for d in det:
        for wcode, u, kind, val, dirn, close, vol in d['w']:
            used.add(wcode)
            p = per.setdefault(u, {}).setdefault(wcode, {'k': kind, 'v': 0, 'up': 0, 'dn': 0, 'vol': 0, 'c': None, 'cd': ''})
            p['v'] += val; p['vol'] += vol
            if dirn > 0: p['up'] += val
            elif dirn < 0: p['dn'] += val
            if close and d['d'] > p['cd']:
                p['c'], p['cd'] = close, d['d']
    asc = days[::-1]
    codes = set(per) | set(c for d in days[:20] for c in d['r'])
    for fn in os.listdir(ud):
        if fn.endswith('.json') and fn[:-5] not in codes:
            os.remove(os.path.join(ud, fn))
    for c in codes:
        series = [[d['d']] + (d['r'][c][:6] if c in d['r'] else [0] * 6) for d in asc]
        ws, iss = [], {}
        for wcode, p in per.get(c, {}).items():   # 發行商分布用全部權證計算
            m = meta.get(wcode, [])
            k = (issuer_of(m[0], keys) if m and m[0] else '') or '其他'
            o = iss.setdefault(k, [0, 0, 0, 0, 0])
            if p['k'] == 'C':
                o[0] += p['v']; o[1] += p['up']; o[2] += p['dn']
            else:
                o[3] += p['v']
            o[4] += 1
        iss = [[k] + [round(x / 1e4, 1) for x in v[:4]] + [v[4]] for k, v in sorted(iss.items(), key=lambda kv: -(kv[1][0] + kv[1][3]))]
        for wcode, p in sorted(per.get(c, {}).items(), key=lambda kv: -kv[1]['v'])[:60]:
            m = meta.get(wcode, [])
            name = m[0] if m else ''
            ws.append([wcode, name, p['k'], round(p['v'] / 1e4, 1), round(p['up'] / 1e4, 1), round(p['dn'] / 1e4, 1), p['vol'], p['c'],
                       m[2] if m else None, m[3] if m else '', m[4] if m else None, issuer_of(name, keys) if name else ''])
        with open(os.path.join(ud, c + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'code': c, 'detailFrom': det[-1]['d'] if det else '', 'detailTo': det[0]['d'] if det else '',
                       'cols': ['日期', '認購', '認購漲價', '認購跌價', '認售', '認售漲價', '認售跌價'], 'days': series,
                       'wcols': ['權證代號', '名稱', '類型', '成交萬元', '漲價成交萬元', '跌價成交萬元', '成交張數', '最新收盤', '履約價', '最後交易日', '行使比例', '發行商'],
                       'warrants': ws, 'icols': ['發行商', '認購成交萬元', '認購漲價萬元', '認購跌價萬元', '認售成交萬元', '權證檔數'], 'issuers': iss},
                      f, ensure_ascii=False, separators=(',', ':'))
    print('detail files', len(codes), 'detail days', len(det), 'meta', len(meta), flush=True)

if __name__ == '__main__':
    a = sys.argv[1:] or ['update']
    if a[0] == 'update':
        cmd_update()
    elif a[0] == 'build':
        build()
