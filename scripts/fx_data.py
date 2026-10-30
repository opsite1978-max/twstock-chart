#!/usr/bin/env python3
"""
外資期現貨總覽 → data/fx/fx.json

  1. 期貨三大法人（FinMind TaiwanFuturesInstitutionalInvestors，日盤）：每個商品的多方／空方交易口數、多方／空方未平倉口數
  2. 期貨三大法人夜盤（TaiwanFuturesInstitutionalInvestorsAfterHours）：多方／空方交易口數（期交所夜盤沒有公布法人未平倉）
     ※ FinMind 夜盤的日期＝它所屬的下一個交易日（例如 10/29 15:00～10/30 05:00 記在 10/30）
  3. 期貨每日行情（TaiwanFuturesDaily）：近月收盤、漲跌、成交量、全部月份未平倉；夜盤收盤與成交量
  4. 現貨三大法人買賣超金額（TaiwanStockTotalInstitutionalInvestors）
  5. 現貨產業：用本專案每天整理的 data/insti/days（各股外資、投信買賣超張數）× data/price/days（收盤價）
     依產業加總外資、投信買賣超金額，並算產業成交值加權漲跌幅（不用 FinMind）

FinMind 憑證從環境變數 FINMIND_TOKEN 讀取（GitHub 加密密碼），不會印出或存檔。每次約 50 次查詢。
"""
import json, os, sys, time, datetime, urllib.request, urllib.error, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'fx')
TW = datetime.timezone(datetime.timedelta(hours=8))
TOKEN = os.environ.get('FINMIND_TOKEN', '').strip()
API = 'https://api.finmindtrade.com/api/v4/data'
KEEP = 160   # 保留幾個交易日
PRODUCTS = [
    ('TX', '臺股期貨', 'idx', 1.0), ('MTX', '小型臺指', 'idx', 0.25), ('TMF', '微型臺指', 'idx', 0.05),
    ('TE', '電子期', 'sec', 0), ('TF', '金融期', 'sec', 0), ('XIF', '非金電期', 'sec', 0), ('GTF', '櫃買期', 'sec', 0),
    ('ZEF', '小型電子期', 'sec', 0), ('ZFF', '小型金融期', 'sec', 0), ('SOF', '半導體30期', 'sec', 0), ('SHF', '航運期', 'sec', 0),
    ('BTF', '生技期', 'sec', 0), ('T5F', '臺灣50期', 'sec', 0), ('E4F', '中型100期', 'sec', 0), ('G2F', '永續期', 'sec', 0), ('F1F', '富櫃200期', 'sec', 0),
]
WHO = {'外資': 'F', '外資及陸資': 'F', '投信': 'T', '自營商': 'D'}
NREQ = [0]


class RateLimited(Exception):
    pass


def log(*a):
    print(' '.join(str(x) for x in a), flush=True)


def api(params):
    q = dict(params)
    req = urllib.request.Request(API + '?' + urllib.parse.urlencode(q), headers={'Authorization': 'Bearer ' + TOKEN, 'User-Agent': 'twstock-chart fx'})
    err = None
    for attempt in range(3):
        NREQ[0] += 1
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                j = json.loads(r.read())
            if j.get('status') == 402:
                raise RateLimited('FinMind 每小時查詢上限')
            if j.get('status') not in (200, None):
                raise RuntimeError('status %s %s' % (j.get('status'), str(j.get('msg'))[:80]))
            return j.get('data') or []
        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8', 'replace')[:200]
            if e.code in (402, 429) or 'upper limit' in body.lower():
                raise RateLimited('FinMind 每小時查詢上限')
            err = 'HTTP %s %s' % (e.code, body)
        except RateLimited:
            raise
        except Exception as e:
            err = e
        time.sleep(4 * (attempt + 1))
    raise RuntimeError(str(err))


def num(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def load_old():
    try:
        return json.load(open(os.path.join(OUT, 'fx.json'), encoding='utf-8'))
    except Exception:
        return None


def futures_part(start, old):
    day = {k: dict(v) for k, v in (old or {}).get('day', {}).items()}       # day[d][fid] = {who: [多交易, 空交易, 多未平倉, 空未平倉]}
    night = {k: dict(v) for k, v in (old or {}).get('night', {}).items()}   # night[d][fid] = {who: [多交易, 空交易]}
    px = {k: dict(v) for k, v in (old or {}).get('px', {}).items()}         # px[d][fid] = [近月收, 近月漲跌, 近月量, 全部未平倉, 夜盤收, 夜盤量, 近月月份]
    have = []
    for fid, name, kind, w in PRODUCTS:
        got = 0
        try:
            for r in api({'dataset': 'TaiwanFuturesInstitutionalInvestors', 'data_id': fid, 'start_date': start}):
                who = WHO.get(str(r.get('institutional_investors', '')).strip())
                if not who:
                    continue
                day.setdefault(r['date'], {}).setdefault(fid, {})[who] = [int(num(r.get('long_deal_volume'))), int(num(r.get('short_deal_volume'))),
                                                                          int(num(r.get('long_open_interest_balance_volume'))), int(num(r.get('short_open_interest_balance_volume')))]
                got += 1
            for r in api({'dataset': 'TaiwanFuturesInstitutionalInvestorsAfterHours', 'data_id': fid, 'start_date': start}):
                who = WHO.get(str(r.get('institutional_investors', '')).strip())
                if not who:
                    continue
                night.setdefault(r['date'], {}).setdefault(fid, {})[who] = [int(num(r.get('long_deal_volume'))), int(num(r.get('short_deal_volume')))]
            rows = api({'dataset': 'TaiwanFuturesDaily', 'data_id': fid, 'start_date': start})
        except RateLimited:
            raise
        except Exception as e:
            log('skip', fid, e)
            continue
        best, ah, oi = {}, {}, {}
        for r in rows:
            cd = str(r.get('contract_date') or '').strip()
            if not (len(cd) == 6 and cd.isdigit()):
                continue
            c = num(r.get('close'))
            v = num(r.get('volume'))
            if r.get('trading_session') == 'after_market':
                if c > 0:
                    ah[(r['date'], cd)] = (c, v)
                continue
            oi[r['date']] = oi.get(r['date'], 0) + num(r.get('open_interest'))
            if c > 0 and (r['date'] not in best or v > best[r['date']][2]):
                best[r['date']] = (c, num(r.get('spread')), v, cd)
        for d, (c, sp, v, cd) in best.items():
            a = ah.get((d, cd))
            px.setdefault(d, {})[fid] = [c, sp, int(v), int(oi.get(d, 0)), a[0] if a else None, int(a[1]) if a else None, cd]
        # 只有夜盤、日盤還沒開的那天（早上更新時）
        for (d, cd), (c, v) in ah.items():
            if d in best:
                continue
            cur = px.setdefault(d, {}).get(fid)
            if not cur or (cur[5] or 0) < v:
                px[d][fid] = [None, None, None, None, c, int(v), cd]
        if got:
            have.append(fid)
        log(fid, name, 'inst rows', got, 'price days', len(best))
    return day, night, px, have


def spot_part(start, old):
    spot = dict((old or {}).get('spot', {}))   # spot[d] = {F:[買,賣], T:[買,賣], D:[買,賣]}（元）
    raw = {}
    for r in api({'dataset': 'TaiwanStockTotalInstitutionalInvestors', 'start_date': start}):
        raw.setdefault(r['date'], {})[str(r.get('name', ''))] = (num(r.get('buy')), num(r.get('sell')))
    for d, m in raw.items():
        e = {}
        for nm, (b, s) in m.items():
            k = 'F' if nm.startswith('Foreign') else 'T' if nm == 'Investment_Trust' else 'D' if nm.startswith('Dealer') else None
            if not k:
                continue
            a = e.setdefault(k, [0, 0])
            a[0] += b
            a[1] += s
        if e:
            spot[d] = {k: [round(v[0]), round(v[1])] for k, v in e.items()}
    return spot


def sector_part():
    """各產業外資、投信買賣超金額（億）、成交值加權漲跌幅；用專案內的每日檔，不查 FinMind"""
    idir = os.path.join(ROOT, 'data', 'insti', 'days')
    pdir = os.path.join(ROOT, 'data', 'price', 'days')
    univ = {s['code']: s for s in json.load(open(os.path.join(ROOT, 'insider', 'universe.json'), encoding='utf-8'))['stocks']}
    pfiles = sorted(f for f in os.listdir(pdir) if f[:4].isdigit())
    ifiles = sorted(f for f in os.listdir(idir) if f[:4].isdigit())
    pdates = [f[:10] for f in pfiles]
    dates = [f[:10] for f in ifiles if f[:10] in pdates][-20:]
    if not dates:
        return None
    cache = {}

    def price(d):
        if d not in cache:
            cache[d] = json.load(open(os.path.join(pdir, d + '.json'), encoding='utf-8'))['r']
        return cache[d]
    per = []   # 每天：{產業: [外資億, 投信億, 加權漲跌分子, 成交值]}
    for d in dates:
        ins = json.load(open(os.path.join(idir, d + '.json'), encoding='utf-8'))['r']
        pr = price(d)
        k = pdates.index(d)
        prev = price(pdates[k - 1]) if k > 0 else {}
        agg = {}
        for code, s in univ.items():
            ind = s.get('ind') or '其他'
            p = pr.get(code)
            if not p or not p[3]:
                continue
            c, v = p[3], p[4] or 0
            a = agg.setdefault(ind, [0.0, 0.0, 0.0, 0.0, 0])
            f = ins.get(code)
            if f:
                a[0] += f[0] * 1000 * c / 1e8
                a[1] += f[1] * 1000 * c / 1e8
            pp = prev.get(code)
            if pp and pp[3]:
                val = v * 1000 * c
                a[2] += (c / pp[3] - 1) * 100 * val
                a[3] += val
            a[4] += 1
        per.append(agg)
    inds = sorted(set().union(*[set(a) for a in per]))
    rows = []
    for ind in inds:
        g = [a.get(ind, [0, 0, 0, 0, 0]) for a in per]
        last = g[-1]
        chg = lambda a: round(a[2] / a[3], 2) if a[3] else None
        c5 = 1.0
        for a in g[-5:]:
            x = chg(a)
            if x is not None:
                c5 *= 1 + x / 100
        rows.append({'ind': ind, 'n': last[4], 'chg': chg(last), 'chg5': round((c5 - 1) * 100, 2),
                     'f1': round(last[0], 2), 'f5': round(sum(a[0] for a in g[-5:]), 2), 'f20': round(sum(a[0] for a in g), 2),
                     't1': round(last[1], 2), 't5': round(sum(a[1] for a in g[-5:]), 2), 'val': round(last[3] / 1e8, 1),
                     'fs': [round(a[0], 2) for a in g]})
    return {'d': dates[-1], 'dates': dates, 'rows': rows}


def main():
    old = load_old()
    now = datetime.datetime.now(TW)
    start = (now.date() - datetime.timedelta(days=240 if not old else 25)).isoformat()
    status = 'ok'
    day, night, px, have = (old or {}).get('day', {}), (old or {}).get('night', {}), (old or {}).get('px', {}), (old or {}).get('have', [])
    spot = (old or {}).get('spot', {})
    try:
        day, night, px, have = futures_part(start, old)
        spot = spot_part(start, old)
    except RateLimited as e:
        status = str(e)
        log('stop:', e)
    sector = None
    try:
        sector = sector_part()
    except Exception as e:
        log('sector fail', e)
    keep = sorted(set(day) | set(night) | set(px) | set(spot))[-KEEP:]
    out = {
        'updated': now.isoformat(timespec='minutes'), 'status': status, 'nreq': NREQ[0],
        'products': [{'id': f, 'name': n, 'kind': k, 'w': w} for f, n, k, w in PRODUCTS],
        'have': have,
        'day': {d: day[d] for d in keep if d in day}, 'night': {d: night[d] for d in keep if d in night},
        'px': {d: px[d] for d in keep if d in px}, 'spot': {d: spot[d] for d in keep if d in spot},
        'sector': sector or (old or {}).get('sector'),
    }
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, 'fx.json'), 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    log('done; requests', NREQ[0], 'days', len(keep), 'have', have, 'status', status)
    if status != 'ok' and not old:
        sys.exit(1)


if __name__ == '__main__':
    main()
