#!/usr/bin/env python3
"""
台指期盤中錄影：交易時段每分鐘抓一次 FinMind 即時報價，存成每分鐘 K 棒＋選擇權資金流推估，
推到 live-data 分支（只保留最新一份，不進 main、不觸發網站重建），網頁從 raw.githubusercontent.com 讀。
用途：FinMind 的分 K、逐筆要收盤後（夜盤更晚）才公布，網頁打開時就能先看到完整的日盤＋夜盤。

每分鐘 3 個額度（台指期＋最近 2 個結算日的選擇權），一小時約 180 次。
一次最多跑 5 小時 40 分，時段還沒結束就自己再開下一次。
"""
import os, sys, json, time, math, re, datetime, subprocess, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get('FINMIND_TOKEN', '').strip()
TW = datetime.timezone(datetime.timedelta(hours=8))
MAX_RUN = float(os.environ.get('MAX_RUN_MIN', '340')) * 60
POLL = int(os.environ.get('POLL_SEC', '60'))
T0 = time.time()
OUT = '/tmp/live/tx_live.json'
LOG = []


def log(*a):
    s = ' '.join(str(x) for x in a)
    if TOKEN:
        s = s.replace(TOKEN, '***')
    print(datetime.datetime.now(TW).strftime('%H:%M:%S'), s, flush=True)
    LOG.append(s)


def snap(path, did):
    u = 'https://api.finmindtrade.com/api/v4/' + path + '?' + urllib.parse.urlencode({'data_id': did, 'token': TOKEN})
    with urllib.request.urlopen(u, timeout=40) as r:
        j = json.loads(r.read())
    if j.get('status') != 200:
        raise RuntimeError(str(j.get('msg'))[:80])
    return j.get('data') or []


def tm_of(r):
    try:
        return datetime.datetime.fromisoformat(str(r['date'])[:19]).replace(tzinfo=TW).timestamp()
    except Exception:
        return 0


def pick_fut(rows):
    c = [r for r in rows if re.fullmatch(r'TXF[A-L]\d', str(r.get('futures_id'))) and (r.get('close') or 0) > 0]
    if not c:
        return None
    newest = max(tm_of(r) for r in c)
    c = [r for r in c if newest - tm_of(r) < 600]
    return max(c, key=lambda r: r.get('total_volume') or 0)


def session(now):
    """現在的時段：('D', 日期) 08:45–13:45、('N', 開盤日期) 15:00–隔天 05:00；不在盤中回 None"""
    wd, m, d = now.weekday(), now.hour * 60 + now.minute, now.date().isoformat()
    if wd <= 4 and 525 <= m <= 826:
        return 'D', d, 525
    if wd <= 4 and m >= 900:
        return 'N', d, 900
    if 1 <= wd <= 5 and m <= 301:
        return 'N', (now.date() - datetime.timedelta(days=1)).isoformat(), 900
    return None


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


MC, MP = 'ABCDEFGHIJKL', 'MNOPQRSTUVWX'


def product(code):
    m = re.fullmatch(r'(\d{4})(\d{2})(?:([WF])(\d))?', code)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    prod = 'TXO' if not m.group(3) else ('TX' + m.group(4) if m.group(3) == 'W' else 'TX' + 'UVXYZ'[int(m.group(4)) - 1])
    return prod, MC[mo - 1] + str(y % 10), MP[mo - 1] + str(y % 10)


def exp_ts(exp):
    return datetime.datetime.fromisoformat(exp).replace(hour=13, minute=30, tzinfo=TW).timestamp()


def push(data):
    os.makedirs('/tmp/live', exist_ok=True)
    json.dump(data, open(OUT, 'w'), separators=(',', ':'))
    open('/tmp/live/log.txt', 'w').write('\n'.join(LOG[-200:]) + '\n')
    repo = os.environ.get('GITHUB_REPOSITORY'); tok = os.environ.get('GH_TOKEN')
    if not repo or not tok:
        return
    sh = lambda *c: subprocess.run(c, cwd='/tmp/live', capture_output=True, text=True)
    subprocess.run(['rm', '-rf', '/tmp/live/.git'])                     # 每次都只留一個提交
    sh('git', 'init', '-q'); sh('git', 'checkout', '-q', '-b', 'live-data'); sh('git', 'add', '-A')
    sh('git', '-c', 'user.name=live-bot', '-c', 'user.email=live-bot@users.noreply.github.com', 'commit', '-q', '-m', 'live')
    r = sh('git', 'push', '-qf', f'https://x-access-token:{tok}@github.com/{repo}', 'live-data')
    if r.returncode:
        log('push fail', r.stderr[-200:])


def load_prev():
    """接著上一次（或同一時段先前的錄影）繼續"""
    repo = os.environ.get('GITHUB_REPOSITORY')
    if not repo:
        return {}
    try:
        with urllib.request.urlopen(f'https://raw.githubusercontent.com/{repo}/live-data/tx_live.json?t={int(time.time())}', timeout=30) as r:
            return json.loads(r.read())
    except Exception:
        return {}


def main():
    if not TOKEN:
        log('沒有 FINMIND_TOKEN'); return
    data = load_prev()
    sess = data.get('sessions', {})
    strikes = json.load(open(os.path.join(ROOT, 'data', 'force', 'strikes.json')))
    st = {}                     # 時段 → 上一次的累計量
    last_push = 0
    while True:
        now = datetime.datetime.now(TW)
        ses = session(now)
        if time.time() - T0 > MAX_RUN:
            log('到達單次時間上限')
            nxt = ses or session(now + datetime.timedelta(minutes=10))
            if nxt and os.environ.get('GH_TOKEN'):
                subprocess.run(['gh', 'workflow', 'run', 'live-rec.yml', '--ref', 'main'], capture_output=True)
                log('時段還沒結束，已接著開下一次')
            break
        if not ses:
            # 開盤前 20 分鐘內等開盤；其他時間結束
            soon = session(now + datetime.timedelta(minutes=20))
            if soon:
                time.sleep(30); continue
            log('不在交易時段，結束'); break
        s, x, a = ses
        key = s + ':' + x
        loop0 = time.time()
        try:
            nowS = time.time()
            exps = [e for e in strikes['exps'] if exp_ts(e['exp']) > nowS][:2]
            prods = {}
            for e in exps:
                p = product(e['code'])
                if p:
                    prods.setdefault(p[0], []).append((e, p))
            fr = snap('taiwan_futures_snapshot', 'TX')
            ors = {}
            for pid in prods:
                try:
                    ors[pid] = snap('taiwan_options_snapshot', pid)
                except Exception as ex:
                    log('opt fail', pid, ex); ors[pid] = []
            f = pick_fut(fr)
            start = datetime.datetime.fromisoformat(x).replace(tzinfo=TW).timestamp() + a * 60
            if not f or tm_of(f) < start:
                log('還沒有這個時段的成交', len(fr), f and f.get('futures_id'), f and f.get('date'))
                raise RuntimeError('skip')
            px = float(f['close'])
            # 台灣時間當成 UTC 的秒（和網站其他資料一樣），取整到分鐘
            tmin = int(datetime.datetime(now.year, now.month, now.day, now.hour, now.minute, tzinfo=datetime.timezone.utc).timestamp())
            S = sess.setdefault(key, {'s': s, 'x': x, 'bars': []})
            P = st.setdefault(key, {'fut': None, 'pv': {}, 'hi': None, 'lo': None})
            bars = S['bars']
            if not bars or bars[-1][0] != tmin:
                bars.append([tmin, px, px, px, px, 0.0, 0.0, 0])
            b = bars[-1]
            b[2] = max(b[2], px); b[3] = min(b[3], px); b[4] = px
            hi, lo = float(f.get('high') or px), float(f.get('low') or px)
            if P['hi'] is not None and hi > P['hi']:
                b[2] = max(b[2], hi)
            if P['lo'] is not None and lo < P['lo']:
                b[3] = min(b[3], lo)
            P['hi'], P['lo'] = hi, lo
            tv = int(f.get('total_volume') or 0)
            if P['fut'] is not None and tv >= P['fut']:
                b[7] += tv - P['fut']
            P['fut'] = tv
            for pid, lst in prods.items():
                for r in ors.get(pid, []):
                    m = re.fullmatch(r'[A-Z0-9]{3}(\d+)([A-X])(\d)', str(r.get('options_id')))
                    if not m:
                        continue
                    suf = m.group(2) + m.group(3)
                    it = next((q for q in lst if suf in (q[1][1], q[1][2])), None)
                    if not it:
                        continue
                    e, (_, cl, pl) = it
                    call = suf == cl; K = float(m.group(1)); oid = r['options_id']
                    tv2 = int(r.get('total_volume') or 0); pv = P['pv'].get(oid); P['pv'][oid] = tv2
                    if pv is None or tv2 <= pv:
                        continue
                    dv = tv2 - pv; c = float(r.get('close') or 0); bp = float(r.get('buy_price') or 0); sp = float(r.get('sell_price') or 0)
                    tt = int(r.get('TickType') or 0)
                    side = 1 if tt == 1 else -1 if tt == 2 else 0
                    if not side and bp > 0 and sp > 0:
                        side = 1 if c >= sp else -1 if c <= bp else (1 if c > (bp + sp) / 2 else -1 if c < (bp + sp) / 2 else 0)
                    if not side:
                        continue
                    row = next((rr for rr in e['rows'] if rr[0] == K), None)
                    iv = (row[10] if row and row[10] else e['iv']) / 100
                    T = max(600, exp_ts(e['exp']) - nowS) / (365 * 86400)
                    F = e['F'] * px / (strikes.get('fut') or e['F'])
                    sd = iv * math.sqrt(T); d1 = (math.log(F / K) + sd * sd / 2) / sd
                    dl = ncdf(d1) if call else ncdf(d1) - 1
                    b[5] += side * dl * dv * 0.25; b[6] += abs(dl) * dv * 0.25
            b[5] = round(b[5], 2); b[6] = round(b[6], 2)
        except Exception as ex:
            if str(ex) != 'skip':
                log('poll fail', repr(ex)[:200])
        # 只留最近 4 個時段
        for k in sorted(sess)[:-4]:
            del sess[k]
        if time.time() - last_push > 240:
            log('push', {k: len(v['bars']) for k, v in sess.items()})
            data = {'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M:%S'), 'cols': ['t', 'o', 'h', 'l', 'c', 'net', 'gross', 'v'],
                    'note': '台灣時間當成 UTC 的秒；net、gross＝選擇權主動成交 Delta 推估（大台口數），每分鐘抓一次即時報價', 'sessions': sess}
            push(data); last_push = time.time()
        time.sleep(max(5, POLL - (time.time() - loop0)))
    data = {'updated': datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M:%S'), 'cols': ['t', 'o', 'h', 'l', 'c', 'net', 'gross', 'v'],
            'note': '台灣時間當成 UTC 的秒；net、gross＝選擇權主動成交 Delta 推估（大台口數），每分鐘抓一次即時報價', 'sessions': sess}
    push(data)


if __name__ == '__main__':
    main()
