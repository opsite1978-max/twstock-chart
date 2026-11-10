"""美元／台幣分 K 資料：每天收盤後存一份，給戰神看盤的 5／15／30／60 分、4 小時 K 用。

來源
- 鉅亨網（台北外匯市場即時成交，09:00～16:00）：1 分 K 只提供「當天」，所以每天收盤後存下來，累積成歷史。
- Yahoo 財經 TWD=X：只用來補「還沒開始存之前」的歷史（5 分 K 近 60 天、60 分 K 近 2 年），
  只取台北 09:00～16:00、而且只取鉅亨日 K 有開市的日子。鉅亨有的日子一律用鉅亨。

輸出（推到 usdtwd-data 分支）
- m5.json  {"u": 更新時間, "d": {"YYYY-MM-DD": ["C" 或 "Y", [[分鐘, 開, 高, 低, 收], ...]]}}
- m60.json 同上，60 分 K
分鐘＝台北時間從 00:00 起算的分鐘數，K 棒的開始時間。
"""
import json, os, sys, time, urllib.request, datetime as DT

UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'}
A, E = 540, 960          # 09:00～16:00
OUT = sys.argv[1] if len(sys.argv) > 1 else 'usdtwd'
KEEP5, KEEP60 = 500, 1600


def get(u, tries=3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:
            print('  讀取失敗', u[:90], str(e)[:120])
            time.sleep(3 + k * 5)
    return None


def tw(t):
    d = DT.datetime.fromtimestamp(t, DT.timezone.utc) + DT.timedelta(hours=8)
    return d.strftime('%Y-%m-%d'), d.hour * 60 + d.minute


def cnyes(res, frm=None, to=None):
    u = f'https://ws.api.cnyes.com/ws/api/v1/charting/history?resolution={res}&symbol=FX:USDTWD:FOREX&quote=1'
    if frm: u += f'&from={frm}&to={to}'
    j = get(u)
    d = (j or {}).get('data') or {}
    t = d.get('t') or []
    rows = []
    for i, x in enumerate(t):
        o, h, l, c = (d.get(k)[i] if d.get(k) else None for k in 'ohlc')
        if c is None: continue
        o = o if o is not None else c; h = h if h is not None else max(o, c); l = l if l is not None else min(o, c)
        rows.append((x, o, h, l, c))
    rows.sort()
    return rows


def yahoo(iv, rng):
    for host in ('query1', 'query2'):
        j = get(f'https://{host}.finance.yahoo.com/v8/finance/chart/TWD=X?interval={iv}&range={rng}')
        try:
            r = j['chart']['result'][0]; q = r['indicators']['quote'][0]
            out = []
            for i, t in enumerate(r.get('timestamp') or []):
                o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
                if None in (o, h, l, c) or c <= 0: continue
                out.append((t, o, h, l, c))
            return out
        except Exception:
            continue
    return []


def bucket(rows, P):
    """[(t, o, h, l, c)] → {日期: [[分鐘, o, h, l, c], ...]}，只留 09:00～16:00"""
    days = {}
    for t, o, h, l, c in rows:
        d, m = tw(t)
        if m < A or m >= E: continue
        b = A + (m - A) // P * P
        bars = days.setdefault(d, {})
        k = bars.get(b)
        if k is None: bars[b] = [b, o, h, l, c]
        else: k[2] = max(k[2], h); k[3] = min(k[3], l); k[4] = c
    r4 = lambda x: round(x, 4)
    return {d: [[b[0], r4(b[1]), r4(b[2]), r4(b[3]), r4(b[4])] for _, b in sorted(v.items())] for d, v in days.items()}


def regroup(bars, P):
    out = {}
    for m, o, h, l, c in bars:
        b = A + (m - A) // P * P
        k = out.get(b)
        if k is None: out[b] = [b, o, h, l, c]
        else: k[2] = max(k[2], h); k[3] = min(k[3], l); k[4] = c
    return [out[b] for b in sorted(out)]


def load(name):
    try:
        with open(os.path.join(OUT, name)) as f: return json.load(f).get('d', {})
    except Exception:
        return {}


def main():
    os.makedirs(OUT, exist_ok=True)
    m5, m60 = load('m5.json'), load('m60.json')
    now = int(time.time())
    today, nowm = tw(now)

    # 開市日：鉅亨日 K（台北外匯市場有成交的日子）
    open_days = {tw(t)[0] for t, *_ in cnyes('D', now, now - 86400 * 365 * 3)}
    print('鉅亨日 K 開市日', len(open_days))

    # 鉅亨當天 1 分 K → 5 分、60 分
    c1 = cnyes('1')
    got = bucket(c1, 5)
    for d, bars in got.items():
        done = d < today or nowm >= E
        m5[d] = ['C' if done else 'c', bars]
        m60[d] = ['C' if done else 'c', regroup(bars, 60)]
        print('鉅亨', d, len(bars), '根 5 分 K', '（收盤）' if done else '（盤中，之後會覆蓋）')

    # Yahoo 補歷史（只補鉅亨沒有的日子）
    y5 = bucket(yahoo('5m', '60d'), 5)
    y60 = bucket(yahoo('60m', '730d'), 60)
    add5 = add60 = 0
    for d, bars in y5.items():
        if d in open_days and d != today and (d not in m5 or m5[d][0] in ('Y', 'c')) and len(bars) >= 20:
            if m5.get(d) != ['Y', bars]: add5 += 1
            m5[d] = ['Y', bars]
    for d, bars in y60.items():
        if d in open_days and d != today and (d not in m60 or m60[d][0] in ('Y', 'c')) and len(bars) >= 4:
            if m60.get(d) != ['Y', bars]: add60 += 1
            m60[d] = ['Y', bars]
    print('Yahoo 補 5 分 K', add5, '天、60 分 K', add60, '天')

    # 比對：同一天鉅亨和 Yahoo 都有的 5 分 K，收盤價差多少
    diffs = []
    for d, bars in got.items():
        yb = {b[0]: b[4] for b in y5.get(d, [])}
        diffs += [abs(b[4] - yb[b[0]]) for b in bars if b[0] in yb]
    if diffs:
        diffs.sort()
        print('鉅亨對 Yahoo 同時段 5 分 K 收盤差：中位', round(diffs[len(diffs) // 2], 4), '最大', round(diffs[-1], 4), '共', len(diffs), '根')

    for name, d, keep in (('m5.json', m5, KEEP5), ('m60.json', m60, KEEP60)):
        ks = sorted(d)[-keep:]
        with open(os.path.join(OUT, name), 'w') as f:
            json.dump({'u': now, 'd': {k: d[k] for k in ks}}, f, separators=(',', ':'))
    print('完成：5 分 K', len(m5), '天，60 分 K', len(m60), '天')


main()
