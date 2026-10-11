#!/usr/bin/env python3
"""
集保戶股權分散表（神秘金字塔用的同一份資料）→ data/holders/<代號>.json 與選股用的 data/holders/screen.json
在 GitHub Actions 上執行，只用標準函式庫。

  python3 scripts/holders_scrape.py latest              下載集保開放資料（全市場最新一週，一次一個檔）
  python3 scripts/holders_scrape.py backfill I N OUT    第 I 組（共 N 組）回補歷史週別（集保查詢頁，約一年）
  python3 scripts/holders_scrape.py build               合併後產生 screen.json、index.json

每檔 JSON：{code,name,v:1,w:{"YYYY-MM-DD":[總人數, 總股數, 人數×15級..., 持股比例×15級(萬分之一個百分點，整數)]}, nd:[查無資料的日期]}
持股分級（股）：1:1-999 2:1千-5千 3:5千-1萬 4:1-1.5萬 5:1.5-2萬 6:2-3萬 7:3-4萬 8:4-5萬
              9:5-10萬 10:10-20萬 11:20-40萬 12:40-60萬 13:60-80萬 14:80-100萬 15:100萬以上
"""
import json, os, re, sys, io, csv, time, zlib, datetime, http.cookiejar, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'data', 'holders')
WATCH = os.path.join(ROOT, 'insider', 'watchlist.txt')
UNIV = os.path.join(ROOT, 'insider', 'universe.json')
BUDGET_SEC = int(os.environ.get('BUDGET_SEC', '2400'))
GAP = float(os.environ.get('GAP_SEC', '0.6'))
QRY = 'https://www.tdcc.com.tw/portal/zh/smWeb/qryStock'
OD = 'https://opendata.tdcc.com.tw/getOD.ashx?id=1-5'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TW = datetime.timezone(datetime.timedelta(hours=8))
T0 = time.time()
LOG = []
SCREEN_WEEKS = 13

def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line, flush=True)
    LOG.append(datetime.datetime.now(TW).strftime('%m-%d %H:%M:%S ') + line)

def iso(d8):
    return '%s-%s-%s' % (d8[:4], d8[4:6], d8[6:8])

def read_codes(path):
    out = []
    if os.path.exists(path):
        for line in open(path, encoding='utf-8'):
            c = line.split('#')[0].strip()
            if re.fullmatch(r'[0-9A-Z]{4,6}', c) and c not in out:
                out.append(c)
    return out

def universe():
    u = json.load(open(UNIV, encoding='utf-8'))['stocks'] if os.path.exists(UNIV) else []
    names = {s['code']: s for s in u}
    codes = list(dict.fromkeys(read_codes(WATCH) + [s['code'] for s in u]))
    return codes, names

def load(code):
    p = os.path.join(DATA, code + '.json')
    if os.path.exists(p):
        return json.load(open(p, encoding='utf-8'))
    return {'code': code, 'name': '', 'v': 1, 'w': {}, 'nd': []}

def dump(d, folder):
    os.makedirs(folder, exist_ok=True)
    d['w'] = dict(sorted(d['w'].items()))
    d['nd'] = sorted(set(d.get('nd', [])))
    with open(os.path.join(folder, d['code'] + '.json'), 'w', encoding='utf-8') as f:
        f.write('{"code":%s,"name":%s,"v":1,"nd":%s,"w":{\n' % (
            json.dumps(d['code']), json.dumps(d.get('name', ''), ensure_ascii=False), json.dumps(d['nd'])))
        f.write(',\n'.join('%s:%s' % (json.dumps(k), json.dumps(v, separators=(',', ':'))) for k, v in d['w'].items()))
        f.write('\n}}\n')

def record(levels):
    """levels: {級距(1..17): (人數, 股數)} → 存檔用的一列"""
    tot_p, tot_s = levels.get(17, (0, 0))
    if not tot_s:
        tot_s = sum(levels.get(i, (0, 0))[1] for i in range(1, 16))
        tot_p = sum(levels.get(i, (0, 0))[0] for i in range(1, 16))
    if not tot_s:
        return None
    ppl = [levels.get(i, (0, 0))[0] for i in range(1, 16)]
    pct = [round(levels.get(i, (0, 0))[1] / tot_s * 1e6) for i in range(1, 16)]
    return [tot_p, tot_s] + ppl + pct

# ───────── 最新一週：集保開放資料（全市場一個檔） ─────────
def cmd_latest():
    codes, names = universe()
    want = set(codes)
    req = urllib.request.Request(OD, headers={'User-Agent': UA})
    raw = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read().decode('utf-8-sig', 'replace')
            break
        except Exception as e:
            log('opendata retry', e); time.sleep(20)
    if not raw:
        log('opendata failed'); return
    by = {}
    date = None
    for row in csv.reader(io.StringIO(raw)):
        if len(row) < 6 or not row[0].isdigit():
            continue
        code = row[1].strip()
        if code not in want:
            continue
        date = row[0]
        try:
            by.setdefault(code, {})[int(row[2])] = (int(row[3]), int(row[4]))
        except ValueError:
            continue
    if not date:
        log('opendata empty'); return
    d_iso = iso(date)
    n = 0
    for code, lv in by.items():
        rec = record(lv)
        if not rec:
            continue
        d = load(code)
        if d['w'].get(d_iso) == rec:
            continue
        d['w'][d_iso] = rec
        if not d.get('name') and code in names:
            d['name'] = names[code]['name']
        dump(d, DATA); n += 1
    log('opendata', d_iso, 'stocks', len(by), 'updated', n)

# ───────── 歷史週別：集保查詢頁 ─────────
class Tdcc:
    def __init__(self):
        self.cj = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))
        self.tok = None
        self.dates = []
        self.last = 0.0

    def _open(self, data=None):
        wait = GAP - (time.time() - self.last)
        if wait > 0:
            time.sleep(wait)
        req = urllib.request.Request(QRY, data=data, headers={'User-Agent': UA, 'Referer': QRY})
        try:
            with self.op.open(req, timeout=60) as r:
                s = r.read().decode('utf-8', 'replace')
        finally:
            self.last = time.time()
        m = re.search(r'name="SYNCHRONIZER_TOKEN"[^>]*value="([^"]+)"', s)
        if m:
            self.tok = m.group(1)
        return s

    def start(self):
        s = self._open()
        self.dates = re.findall(r'<option value="(\d{8})"', s)
        return self.dates

    def query(self, code, d8):
        for attempt in range(3):
            try:
                if not self.tok:
                    self.start()
                body = urllib.parse.urlencode({
                    'SYNCHRONIZER_TOKEN': self.tok, 'SYNCHRONIZER_URI': '/portal/zh/smWeb/qryStock', 'method': 'submit',
                    'firDate': self.dates[0], 'scaDate': d8, 'sqlMethod': 'StockNo', 'stockNo': code, 'stockName': ''}).encode()
                s = self._open(body)
                rows = re.findall(r'<td align="center">(\d+)</td>\s*<td align="center">[^<]*</td>\s*'
                                  r'<td align="right">([\d,]*)</td>\s*<td align="right">(-?[\d,]*)</td>', s)
                if rows:
                    lv = {int(a): (int(b.replace(',', '') or 0), int(c.replace(',', '') or 0)) for a, b, c in rows}
                    name = re.search(r'證券名稱：\s*([^<\s]+)', s)
                    return record(lv), (name.group(1) if name else '')
                if '查無' in s or '無此' in s:
                    return None, ''
                self.tok = None   # 頁面不對，重新取得表單
                raise RuntimeError('no table')
            except Exception as e:
                log('  retry', code, d8, type(e).__name__, str(e)[:80])
                self.tok = None
                time.sleep(8 * (attempt + 1))
        raise RuntimeError('query failed')

def shard_of(code, n):
    return zlib.crc32(code.encode()) % n

def cmd_backfill(shard, nshards, outdir):
    codes, names = universe()
    watch = set(read_codes(WATCH))
    mine = [c for c in codes if shard_of(c, nshards) == shard]
    t = Tdcc()
    dates = t.start()
    if not dates:
        log('no date list'); return
    log('shard', shard, '/', nshards, 'stocks', len(mine), 'weeks', len(dates), dates[-1], '~', dates[0])
    # 優先：全部股票近 13 週（選股用）→ 自選股一年 → 全部股票一年
    def tier(code, i):
        if i < SCREEN_WEEKS: return 0
        return 1 if code in watch else 2
    order = sorted((tier(c, i), i, c, d8) for i, d8 in enumerate(dates) for c in mine)
    cache, dirty = {}, set()
    status, n_req, n_ok, n_none, fails = 'done', 0, 0, 0, 0
    for _, i, code, d8 in order:
        if time.time() - T0 > BUDGET_SEC:
            status = 'partial (time budget)'; break
        d = cache.get(code) or load(code)
        cache[code] = d
        k = iso(d8)
        if k in d['w'] or k in d.get('nd', []):
            continue
        # 上市（櫃）前：較新的連續 3 週都查無資料，就不再往前
        if i >= 3 and all(iso(dates[j]) in d.get('nd', []) for j in range(i - 3, i)):
            d.setdefault('nd', []).append(k); dirty.add(code); continue
        try:
            n_req += 1
            rec, name = t.query(code, d8)
            fails = 0
        except Exception as e:
            fails += 1
            log('FAIL', code, d8, e)
            if fails >= 5:
                status = 'partial (too many failures, maybe blocked)'; break
            time.sleep(30); continue
        if rec:
            d['w'][k] = rec; n_ok += 1
            if name and not d.get('name'):
                d['name'] = (names.get(code) or {}).get('name') or name
        else:
            d.setdefault('nd', []).append(k); n_none += 1
        dirty.add(code)
        if (n_ok + n_none) % 200 == 0:
            log('progress', n_ok, 'ok', n_none, 'none', int(time.time() - T0), 's')
    for c in dirty:
        dump(cache[c], outdir)
    log('result shard', shard, status, 'requests', n_req, 'ok', n_ok, 'none', n_none, 'files', len(dirty), 'in', int(time.time() - T0), 's')
    with open(os.path.join(outdir, '_log_%d.txt' % shard), 'w', encoding='utf-8') as f:
        f.write('\n'.join(LOG[-200:]) + '\n')

# ───────── 選股用摘要 ─────────
def fetch_json(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode('utf-8-sig'))

def num(x):
    try:
        return float(str(x).replace(',', '').strip())
    except ValueError:
        return None

def latest_quotes():
    """上市、上櫃最新收盤價與成交張數（失敗就略過）"""
    q = {}
    try:
        for r in fetch_json('https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'):
            c, v = num(r.get('ClosingPrice')), num(r.get('TradeVolume'))
            if c:
                q[r['Code']] = [c, round((v or 0) / 1000)]
    except Exception as e:
        log('twse quotes failed', e)
    try:
        for r in fetch_json('https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'):
            code = r.get('SecuritiesCompanyCode') or r.get('Code')
            c = num(r.get('Close') or r.get('ClosingPrice'))
            v = num(r.get('TradingShares') or r.get('TradeVolume'))
            if code and c:
                q[code] = [c, round((v or 0) / 1000)]
    except Exception as e:
        log('tpex quotes failed', e)
    log('quotes', len(q))
    return q

# 累計門檻：大戶（張以上）、散戶（張以下）→ 對應的級距
BIG = {'100': range(9, 15), '400': range(11, 15), '1000': range(14, 15)}   # 索引 0 起算：第 10~15 級、第 12~15 級、第 15 級
SMALL = {'5': range(0, 2), '10': range(0, 3), '50': range(0, 8), '100': range(0, 9)}

def cmd_build():
    codes, names = universe()
    files = [f[:-5] for f in os.listdir(DATA) if re.fullmatch(r'[0-9A-Z]{4,6}\.json', f)]
    alld = {}
    for c in files:
        alld[c] = load(c)
    # 共同週別：出現次數最多的最近 13 個日期
    cnt = {}
    for d in alld.values():
        for k in list(d['w'])[-SCREEN_WEEKS - 4:]:
            cnt[k] = cnt.get(k, 0) + 1
    common = sorted(k for k, v in cnt.items() if v >= max(50, len(alld) * 0.3))[-SCREEN_WEEKS:]
    quotes = latest_quotes()
    keys = ['b100', 'b400', 'b1000', 's5', 's10', 's50', 's100', 'ppl']
    rows = []
    for c in sorted(alld):
        d = alld[c]
        series = {k: [] for k in keys}
        have = 0
        for k in common:
            rec = d['w'].get(k)
            if not rec:
                for s in series.values(): s.append(None)
                continue
            have += 1
            pct = rec[17:32]
            for nm, rg in BIG.items():
                series['b' + nm].append(round(sum(pct[i] for i in rg) / 100))
            for nm, rg in SMALL.items():
                series['s' + nm].append(round(sum(pct[i] for i in rg) / 100))
            series['ppl'].append(rec[0])
        if not have:
            continue
        u = names.get(c, {})
        rows.append([c, u.get('name') or d.get('name', ''), u.get('market', ''), u.get('ind', ''),
                     quotes.get(c, [None, None])[0], quotes.get(c, [None, None])[1]] + [series[k] for k in keys])
    out = {'updated': datetime.datetime.now(TW).isoformat(timespec='minutes'), 'dates': common,
           'cols': ['code', 'name', 'market', 'ind', 'close', 'vol'] + keys,
           'note': '持股比例單位：萬分之一（100 = 1%）', 'rows': rows}
    with open(os.path.join(DATA, 'screen.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    full = sum(1 for d in alld.values() if len(d['w']) >= 45)
    idx = {'updated': out['updated'], 'total': len(codes), 'have': len(alld), 'fullYear': full,
           'latest': common[-1] if common else '', 'watch': read_codes(WATCH),
           'stocks': [[c, (names.get(c) or {}).get('name') or alld[c].get('name', '')] for c in sorted(alld)]}
    with open(os.path.join(DATA, 'index.json'), 'w', encoding='utf-8') as f:
        json.dump(idx, f, ensure_ascii=False, separators=(',', ':'))
    log('build', len(rows), 'rows', 'weeks', common[:1], '~', common[-1:], 'full-year', full)

if __name__ == '__main__':
    a = sys.argv[1:]
    os.makedirs(DATA, exist_ok=True)
    if a[0] == 'latest':
        cmd_latest()
    elif a[0] == 'backfill':
        cmd_backfill(int(a[1]), int(a[2]), a[3])
    elif a[0] == 'build':
        cmd_build()
