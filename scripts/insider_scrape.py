#!/usr/bin/env python3
"""
抓公開資訊觀測站「內部人持股異動事後申報表」(query6_1) 與「董監事持股餘額明細」(stapap1)，
存成 data/insider/<代號>.json 給網頁讀。在 GitHub Actions 上執行，使用者不需要自己跑。只用標準函式庫。

用法（工作流程會呼叫）：
  python3 scripts/insider_scrape.py universe            更新全市場股票清單 insider/universe.json
  python3 scripts/insider_scrape.py scrape I N OUTDIR   第 I 組（共 N 組）抓資料，有變動的檔案寫到 OUTDIR
  python3 scripts/insider_scrape.py index               重建 data/insider/index.json

每檔 JSON（v2，只存「有異動的人」以節省空間）：
  {code,name,market,v:2,
   months:{ "YYYY-MM": {s:"ok"|"none", at, c:[異動列...], dir:{total,pledge}} },
   latest:{ym, r:[該月全部內部人列]}}
  列＝[身份別, 姓名, 所屬本人姓名, 選任時, 上月持股, 增加集中, 增加其他, 設質, 減少集中, 減少其他, 解質, 本月持股, 本月設質]
"""
import json, os, re, sys, time, html, zlib, datetime, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'insider')
WATCH = os.path.join(ROOT, 'insider', 'watchlist.txt')
UNIV = os.path.join(ROOT, 'insider', 'universe.json')
BACK_MONTHS = int(os.environ.get('BACK_MONTHS', '36'))
BUDGET_SEC = int(os.environ.get('BUDGET_SEC', '2400'))
GAP = float(os.environ.get('GAP_SEC', '1.0'))
HOST = 'https://mopsov.twse.com.tw/mops/web/'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TW = datetime.timezone(datetime.timedelta(hours=8))
T0 = time.time()
LOG = []

def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line, flush=True)
    LOG.append(datetime.datetime.now(TW).strftime('%m-%d %H:%M:%S ') + line)

class Blocked(Exception):
    pass

_last = [0.0]
def post(prog, params):
    wait = GAP - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    body = dict(encodeURIComponent='1', step='1', firstin='1', off='1', TYPEK='all', isnew='false')
    body.update(params)
    req = urllib.request.Request(HOST + 'ajax_' + prog, data=urllib.parse.urlencode(body).encode(),
                                 headers={'User-Agent': UA, 'Referer': HOST + prog,
                                          'Content-Type': 'application/x-www-form-urlencoded'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                s = r.read().decode('utf-8', 'replace')
            _last[0] = time.time()
            if 'FOR SECURITY REASONS' in s or '查詢過於頻繁' in s or ('頻繁' in s[:3000] and len(s) < 3000):
                raise Blocked(s[:200])
            return s
        except Blocked:
            raise
        except Exception as e:
            _last[0] = time.time()
            log('  retry', prog, params.get('co_id'), params.get('year'), params.get('month'), type(e).__name__, e)
            time.sleep(10 * (attempt + 1))
    raise RuntimeError('request failed')

def strip(s):
    s = re.sub(r'<[^>]+>', ' ', s)
    return re.sub(r'\s+', ' ', html.unescape(s)).strip()

def num(x):
    x = x.replace(',', '').strip()
    try:
        return int(float(x))
    except ValueError:
        return 0

def cells(tr):
    return re.findall(r'<td[^>]*>(.*?)</td>', tr, flags=re.S | re.I)

def nums_of(cell):
    out = []
    for p in re.split(r'<br\s*/?>', cell, flags=re.I):
        t = strip(p)
        if re.fullmatch(r'-?[\d,]+(\.\d+)?', t):
            out.append(num(t))
    return out

NO_DATA = ('查無', '無資料', '尚未申報', '不存在', '無此公司', '無符合')
OWNER_ROLES = ('本人', '之法人代表人')

def parse_query6(s):
    """回傳 (公司名稱, 市場別, rows)；rows=None 代表查無資料。rows 為 v2 列格式（只含普通股）"""
    m = re.search(r'本資料由\s*\(([^)]*)\)\s*(\S+)\s*公司提供', strip(s))
    mkt, name = (m.group(1), m.group(2)) if m else ('', '')
    rows, owner, seen_any = [], '', False
    for tr in re.split(r'<tr', s, flags=re.I)[1:]:
        c = cells(tr)
        if len(c) < 7:
            continue
        role, nm, kind = strip(c[0]), strip(c[1]), strip(c[2])
        a, b, d, e = nums_of(c[3]), nums_of(c[4]), nums_of(c[5]), nums_of(c[6])
        if len(a) < 5 or len(b) < 5 or len(d) < 5 or len(e) < 5:
            continue
        seen_any = True
        if role.endswith(OWNER_ROLES) and nm:
            owner = nm
        if kind not in ('普通股', ''):
            continue
        rows.append([role, nm, '' if nm else owner,
                     a[0], a[1], b[0], b[1], b[4], d[0], d[1], d[4], e[0], e[3]])
    if not seen_any:
        t = strip(s)
        if any(k in t for k in NO_DATA) or len(t) < 400:
            return name, mkt, None
    return name, mkt, rows

def parse_stapap1(s):
    t = strip(s)
    def g(label):
        m = re.search(label + r'\s*([\d,]+)', t)
        return num(m.group(1)) if m else None
    tot, pl = g('全體董監持股合計'), g('全體董監持股設質合計')
    if tot is None:
        return None
    return {'total': tot, 'pledge': pl or 0}

def changed(r):
    return any(r[5:11])

def ym_list(today):
    # 某月資料在次月 15 日前申報，很多公司月初就申報，所以上個月一開始就抓（還沒申報的會定期重試）
    yy, mm = today.year, today.month - 1
    if mm == 0:
        mm = 12; yy -= 1
    out = []
    for _ in range(BACK_MONTHS):
        out.append('%04d-%02d' % (yy, mm))
        mm -= 1
        if mm == 0:
            mm = 12; yy -= 1
    return out  # 新到舊

def roc(ym):
    y, m = ym.split('-')
    return str(int(y) - 1911), m

def v1_to_v2(d):
    """把舊格式（每月全部列）轉成 v2"""
    if d.get('v') == 2:
        return d
    latest = None
    for ym in sorted(d.get('months', {}), reverse=True):
        rec = d['months'][ym]
        if rec.get('s') != 'ok' or 'r' not in rec:
            continue
        rows, owner = [], ''
        for r in rec['r']:
            if r[0].endswith(OWNER_ROLES) and r[1]:
                owner = r[1]
            if r[2] not in ('普通股', ''):
                continue
            rows.append([r[0], r[1], '' if r[1] else owner, r[3], r[4], r[8], r[9], r[12], r[13], r[14], r[17], r[18], r[21]])
        if latest is None:
            latest = {'ym': ym, 'r': rows}
        rec['c'] = [r for r in rows if changed(r)]
        del rec['r']
    d['v'] = 2
    if latest:
        d['latest'] = latest
    return d

def load(code):
    p = os.path.join(DATA, code + '.json')
    if os.path.exists(p):
        with open(p, encoding='utf-8') as f:
            return v1_to_v2(json.load(f))
    return {'code': code, 'name': '', 'market': '', 'v': 2, 'months': {}}

def dump(d, folder):
    os.makedirs(folder, exist_ok=True)
    d['months'] = dict(sorted(d['months'].items(), reverse=True))
    d = {k: d[k] for k in ('code', 'name', 'market', 'v', 'latest', 'months') if k in d}
    with open(os.path.join(folder, d['code'] + '.json'), 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, separators=(',', ':'))

def read_codes(path):
    codes = []
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            c = line.split('#')[0].strip()
            if re.fullmatch(r'[0-9A-Z]{4,6}', c) and c not in codes:
                codes.append(c)
    return codes

def universe():
    u = []
    if os.path.exists(UNIV):
        u = json.load(open(UNIV, encoding='utf-8')).get('stocks', [])
    return u

def need(rec, idx, now):
    if rec is None:
        return True
    fetched = datetime.datetime.fromisoformat(rec.get('at', '2000-01-01T00:00:00+08:00'))
    age_days = (now - fetched).total_seconds() / 86400
    if rec.get('s') == 'none':
        if idx < 2:
            return age_days > 0.5          # 最近兩個月還沒申報：每 12 小時重試
        return rec.get('n', 1) < 2 and age_days > 20   # 更舊的月份查無資料：20 天後再確認一次就不再試
    if idx < 2:
        return age_days > 3                # 最近兩個月：補申報、更正
    return False

def cmd_universe():
    """從 FinMind 取得上市、上櫃普通股清單（不需憑證）"""
    url = 'https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockInfo'
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=60) as r:
            j = json.loads(r.read().decode('utf-8'))
        rows = j.get('data') or []
    except Exception as e:
        log('universe fetch failed', e)
        return
    skip_ind = ('ETF', 'ETN', '受益證券', '存託憑證', 'Index', '大盤', '所有證券', '指數投資證券')
    out = {}
    for r in rows:
        c, t, ind = r.get('stock_id', ''), r.get('type', ''), r.get('industry_category', '') or ''
        if t not in ('twse', 'tpex') or not re.fullmatch(r'[1-9]\d{3}', c):
            continue
        if c.startswith('91') or any(k in ind for k in skip_ind):
            continue
        prev = out.get(c)
        if not prev or (r.get('date') or '') > prev['d']:
            out[c] = {'code': c, 'name': r.get('stock_name', ''), 'market': '上市' if t == 'twse' else '上櫃',
                      'ind': ind, 'd': r.get('date') or ''}
    if len(out) < 1000:
        log('universe too small, keep old', len(out))
        return
    stocks = [{k: v for k, v in s.items() if k != 'd'} for s in sorted(out.values(), key=lambda s: s['code'])]
    os.makedirs(os.path.dirname(UNIV), exist_ok=True)
    old = universe()
    if [s['code'] for s in old] == [s['code'] for s in stocks]:
        log('universe unchanged', len(stocks))
        return
    with open(UNIV, 'w', encoding='utf-8') as f:
        json.dump({'updated': datetime.date.today().isoformat(), 'stocks': stocks}, f, ensure_ascii=False, indent=0)
    log('universe updated', len(stocks))

def shard_of(code, n):
    return zlib.crc32(code.encode()) % n

def cmd_scrape(shard, nshards, outdir):
    now = datetime.datetime.now(TW)
    months = ym_list(now.date())
    watch = read_codes(WATCH)
    codes = list(dict.fromkeys(watch + [s['code'] for s in universe()]))
    mine = [c for c in codes if shard_of(c, nshards) == shard]
    wset = set(watch)
    log('shard', shard, '/', nshards, 'stocks', len(mine), 'months', months[-1], '~', months[0])
    # 優先順序：全部股票最近兩個月 → 自選股三年 → 全部股票一年 → 全部股票三年
    def tier(code, i):
        if i < 2: return 0
        if code in wset: return 1
        if i < 12: return 2
        return 3
    order = sorted(((tier(c, i), i, c, ym) for i, ym in enumerate(months) for c in mine))
    status, cache, dirty = 'done', {}, set()
    fails = n_req = n_ok = n_none = 0
    for _, idx, code, ym in order:
        if time.time() - T0 > BUDGET_SEC:
            status = 'partial (time budget)'
            break
        d = cache.get(code) or load(code)
        cache[code] = d
        rec = d['months'].get(ym)
        if not need(rec, idx, now):
            continue
        # 上市（櫃）前的月份：較新的連續 3 個月都查無資料，就不再往前抓
        if idx >= 2 and rec is None and idx + 3 < len(months) + 3:
            newer = [d['months'].get(months[j]) for j in range(max(0, idx - 3), idx)]
            if len(newer) == 3 and all(v and v.get('s') == 'none' for v in newer):
                d['months'][ym] = {'s': 'none', 'at': now.isoformat(timespec='minutes'), 'n': 2, 'skip': 1}
                dirty.add(code)
                continue
        y, m = roc(ym)
        try:
            n_req += 1
            name, mkt, rows = parse_query6(post('query6_1', {'co_id': code, 'year': y, 'month': m}))
        except Blocked as e:
            status = 'partial (blocked: %s)' % str(e)[:80]
            log('BLOCKED', code, ym, str(e)[:200])
            break
        except Exception as e:
            fails += 1
            log('FAIL', code, ym, type(e).__name__, e)
            if fails >= 6:
                status = 'partial (too many failures)'
                break
            time.sleep(30)
            continue
        fails = 0
        if name and not d.get('name'):
            d['name'], d['market'] = name, mkt
        stamp = now.isoformat(timespec='minutes')
        if rows is None:
            d['months'][ym] = {'s': 'none', 'at': stamp, 'n': (rec or {}).get('n', 0) + 1}
            n_none += 1
        else:
            new = {'s': 'ok', 'at': stamp, 'c': [r for r in rows if changed(r)]}
            try:
                st = parse_stapap1(post('stapap1', {'co_id': code, 'year': y, 'month': m}))
                if st:
                    new['dir'] = st
            except Blocked as e:
                status = 'partial (blocked: %s)' % str(e)[:80]
                d['months'][ym] = new
                dirty.add(code)
                break
            except Exception as e:
                log('  stapap1 fail', code, ym, type(e).__name__, e)
            d['months'][ym] = new
            if not d.get('latest') or ym >= d['latest']['ym']:
                d['latest'] = {'ym': ym, 'r': rows}
            n_ok += 1
        dirty.add(code)
        if (n_ok + n_none) % 50 == 0:
            log('progress', n_ok, 'ok', n_none, 'none', int(time.time() - T0), 's')
    for c in dirty:
        dump(cache[c], outdir)
    log('result shard', shard, status, 'requests', n_req, 'ok', n_ok, 'none', n_none, 'files', len(dirty), 'in', int(time.time() - T0), 's')
    with open(os.path.join(outdir, '_log_%d.txt' % shard), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG[-200:]) + '\n')

def cmd_index():
    watch = read_codes(WATCH)
    names = {s['code']: s for s in universe()}
    idx = []
    for fn in sorted(os.listdir(DATA)):
        if not re.fullmatch(r'[0-9A-Z]{4,6}\.json', fn):
            continue
        raw_v2 = '"v":2' in open(os.path.join(DATA, fn), encoding='utf-8').read(120)
        d = load(fn[:-5])
        if not raw_v2:
            dump(d, DATA)  # 舊格式順便轉換
        ok = [k for k, v in d['months'].items() if v.get('s') == 'ok']
        u = names.get(d['code'], {})
        # 最近 6 個已申報月份的集中市場淨買賣股數（同一人兼多職會重複揭露，先去重）
        net = []
        for ym in sorted(ok)[-6:]:
            seen, tot = set(), 0
            for r in d['months'][ym].get('c', []):
                key = (r[1], r[2], tuple(r[3:]))
                if key in seen:
                    continue
                seen.add(key)
                tot += r[5] - r[8]
            net.append([ym, tot])
        idx.append({'code': d['code'], 'name': d.get('name') or u.get('name', ''),
                    'market': u.get('market') or d.get('market', ''), 'months': len(ok), 'latest': max(ok) if ok else '',
                    'net': net})
    have = {s['code'] for s in idx}
    total = len(set(names) | set(watch))
    with open(os.path.join(DATA, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump({'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'watch': watch,
                   'total': total, 'pending': sorted((set(names) | set(watch)) - have),
                   'stocks': idx}, f, ensure_ascii=False, separators=(',', ':'))
    log('index', len(idx), 'of', total)

if __name__ == '__main__':
    a = sys.argv[1:] or ['scrape', '0', '1', DATA]
    if a[0] == 'universe':
        cmd_universe()
    elif a[0] == 'scrape':
        cmd_scrape(int(a[1]), int(a[2]), a[3] if len(a) > 3 else DATA)
    elif a[0] == 'index':
        cmd_index()
