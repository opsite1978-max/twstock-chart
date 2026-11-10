"""美股日 K（EODHD）：每天美股收盤後更新清單裡的股票，給新的戰神看盤讀。

- 金鑰放在 GitHub 密碼設定 EODHD_TOKEN；沒有設定時用官方 demo 金鑰（只有 AAPL、TSLA、AMZN 等少數幾檔能抓）。
- 每檔每天只用 1 次：已經有的資料只補最近幾天。免費版每天 20 次、只給一年日 K。
- 輸出到 eodhd-data 分支：index.json（清單、名稱、最新日期）、eod/代號.json（[日期, 開, 高, 低, 收, 量, 還原收盤]）。
- EODHD_INTRADAY=1（付費 Active trader 以上）時另外抓 5 分、60 分 K（每檔每次 5 次額度）。
"""
import json, os, sys, time, urllib.request, urllib.parse, datetime as DT

TOKEN = os.environ.get('EODHD_TOKEN') or 'demo'
BUDGET = int(os.environ.get('EODHD_BUDGET') or (18 if TOKEN != 'demo' else 6))
INTRA = os.environ.get('EODHD_INTRADAY') == '1'
OUT = sys.argv[1] if len(sys.argv) > 1 else 'eodhd-out'
used = 0


def api(path, **q):
    global used
    used += 5 if path.startswith('intraday') else 1
    q.update(api_token=TOKEN, fmt='json')
    u = 'https://eodhd.com/api/' + path + '?' + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        print('  ', path, 'HTTP', e.code, e.read().decode('utf-8', 'replace')[:120])
    except Exception as e:
        print('  ', path, '失敗', str(e)[:120])
    return None


def load(p, default):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return default


def main():
    os.makedirs(os.path.join(OUT, 'eod'), exist_ok=True)
    items = []
    for line in open('eodhd/list.txt', encoding='utf-8'):
        line = line.strip()
        if not line or line.startswith('#'): continue
        code, _, name = line.partition(' ')
        items.append((code.upper(), name.strip() or code))
    idx = load(os.path.join(OUT, 'index.json'), {'s': {}})
    S = idx.get('s', {})
    today = DT.date.today().isoformat()
    # 最久沒更新的先抓，額度不夠時明天接著抓
    order = sorted(items, key=lambda it: (S.get(it[0], {}).get('t', 0)))
    for code, name in order:
        if used + 1 > BUDGET:
            print('今天額度用完（', used, '），其餘明天再抓'); break
        f = os.path.join(OUT, 'eod', code + '.json')
        rows = load(f, [])
        frm = (DT.date.fromisoformat(rows[-1][0]) - DT.timedelta(days=7)).isoformat() if rows else '1990-01-01'
        d = api('eod/' + code, **{'from': frm, 'period': 'd'})
        if not isinstance(d, list):
            S.setdefault(code, {})['err'] = '讀取失敗（免費金鑰沒有這檔，或代號不對）'; S[code]['t'] = int(time.time())
            S[code]['n'] = name; continue
        new = {x['date']: [x['date'], x['open'], x['high'], x['low'], x['close'], x.get('volume') or 0, x.get('adjusted_close') or x['close']] for x in d if x.get('close')}
        m = {r[0]: r for r in rows}; m.update(new)
        rows = [m[k] for k in sorted(m)]
        # 還原收盤有變（除權息、分割）時，舊資料要整段重抓才正確：付費版自動重抓，免費版先保留
        with open(f, 'w') as fh: json.dump(rows, fh, separators=(',', ':'))
        S[code] = {'n': name, 't': int(time.time()), 'last': rows[-1][0] if rows else '', 'rows': len(rows)}
        print(code, name, '新增', len(new), '天，共', len(rows), '天，最新', S[code]['last'])
        if INTRA and used + 10 <= BUDGET:
            for iv in ('5m', '1h'):
                fi = os.path.join(OUT, 'intra', iv, code + '.json'); os.makedirs(os.path.dirname(fi), exist_ok=True)
                old = load(fi, [])
                frm_t = old[-1][0] - 3 * 86400 if old else int(time.time()) - (590 if iv == '5m' else 7000) * 86400
                d = api('intraday/' + code, interval=iv, **{'from': frm_t})
                if isinstance(d, list):
                    mm = {r[0]: r for r in old}
                    for x in d:
                        if x.get('close') is not None: mm[x['timestamp']] = [x['timestamp'], x['open'], x['high'], x['low'], x['close'], x.get('volume') or 0]
                    keep = sorted(mm)[-(30000 if iv == '5m' else 20000):]
                    with open(fi, 'w') as fh: json.dump([mm[k] for k in keep], fh, separators=(',', ':'))
                    S[code]['i' + iv] = len(keep)
    for code in list(S):
        if code not in {c for c, _ in items}: del S[code]
    idx = {'u': int(time.time()), 'demo': TOKEN == 'demo', 'used': used, 's': S, 'order': [c for c, _ in items], 'names': {c: n for c, n in items}}
    with open(os.path.join(OUT, 'index.json'), 'w') as fh: json.dump(idx, fh, ensure_ascii=False, separators=(',', ':'))
    print('完成，用了', used, '次額度', '（demo 金鑰）' if TOKEN == 'demo' else '')


main()
