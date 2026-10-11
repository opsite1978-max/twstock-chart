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
GAP = 3.2

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
    i_u, i_un = f.index('標的代號'), f.index('標的名稱')
    rows = []
    for r in t['data']:
        val = num(r[i_val])
        dirn = direction(r[i_dir]) if num(r[i_chg]) else 0
        rows.append((r[i_u].strip(), r[i_un].strip(), val, dirn))
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
        code, name, chg, val = r[0].strip(), r[1].strip(), r[3], num(r[8])
        kind = 'P' if '售' in name else 'C' if '購' in name else ''
        if not kind:
            continue   # 牛熊證等略過
        dv = num(chg) if re.match(r'^[+-]?\d', str(chg).strip()) else 0
        rows.append((code, name, kind, val, 1 if dv > 0 else -1 if dv < 0 else 0))
    return rows

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
    now = datetime.datetime.now(TW)
    today = now.date()
    t0, got = time.time(), 0
    for k in range(LOOKBACK_DAYS):
        d = today - datetime.timedelta(days=k)
        if d.weekday() >= 5:
            continue
        key = d.isoformat()
        if key in none or os.path.exists(os.path.join(DAYS, key + '.json')):
            continue
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
            print(key, 'OTC not published yet, retry later', flush=True); continue
        agg = {}
        for kind, rows in (('C', calls), ('P', puts)):
            for ucode, uname, val, dirn in rows:
                if uname and ucode and re.fullmatch(r'[0-9A-Z]{4,6}', ucode):
                    alias.setdefault(uname, ucode)   # 權證用的標的簡稱 → 代號
                add(agg, ucode, kind, val, dirn)
        keys = sorted(alias, key=len, reverse=True)
        miss = 0
        for code, name, kind, val, dirn in otc:
            u = match_name(name, alias, keys)
            if not u:
                miss += val > 0; continue
            add(agg, u, kind, val, dirn)
        rows = {c: [round(x / 1e4) for x in v[:6]] + [v[6]] for c, v in agg.items()
                if (univ is None or c in univ) and (v[0] or v[3])}
        with open(os.path.join(DAYS, key + '.json'), 'w', encoding='utf-8') as f:
            json.dump({'d': key, 'r': rows}, f, separators=(',', ':'))
        got += 1
        print(key, 'underlyings', len(rows), 'otc unmatched traded', miss, flush=True)
    with open(nonef, 'w') as f:
        json.dump(sorted(x for x in none if x >= (today - datetime.timedelta(days=LOOKBACK_DAYS + 10)).isoformat()), f)
    with open(ALIAS, 'w', encoding='utf-8') as f:
        json.dump({k: v for k, v in sorted(alias.items())}, f, ensure_ascii=False, separators=(',', ':'))
    cutoff = (today - datetime.timedelta(days=LOOKBACK_DAYS + 10)).isoformat()
    for fn in os.listdir(DAYS):
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', fn) and fn[:10] < cutoff:
            os.remove(os.path.join(DAYS, fn))
    print('fetched days', got, flush=True)
    build()

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

if __name__ == '__main__':
    a = sys.argv[1:] or ['update']
    if a[0] == 'update':
        cmd_update()
    elif a[0] == 'build':
        build()
