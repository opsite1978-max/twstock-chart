#!/usr/bin/env python3
"""
選擇權 K 線盤中錄影：交易時段每分鐘抓一次台指選擇權即時報價（月選 TXO＋還沒結算的各週選），
把每一檔（履約價 × 買賣權）整理成近似的 1 分 K，推到 optk-live 分支（只留一個提交）。
FinMind 的逐筆資料要收盤後才公布，網頁先用這裡的資料補上「今天還沒公布」的部分；
逐筆公布後（optk_data.py）就以逐筆算出來的準確 K 線為準。

近似的方法：每分鐘看一次成交價和累計量，量有增加才記一根；開＝上一次看到的價，
期間如果當天最高（低）價有創新，最高（低）就用它。所以盤中 K 線的影線可能比實際短一點。

每分鐘用 1＋週選檔數 次額度（約 3～5 次），一小時約 300 次。
"""
import os, sys, json, time, re, datetime, subprocess, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = os.environ.get('FINMIND_TOKEN', '').strip()
TW = datetime.timezone(datetime.timedelta(hours=8))
MAX_RUN = float(os.environ.get('MAX_RUN_MIN', '340')) * 60
POLL = int(os.environ.get('POLL_SEC', '60'))
BRANCH = 'optk-live'
WORK = '/tmp/optk-live'
T0 = time.time()
MC, MP = 'ABCDEFGHIJKL', 'MNOPQRSTUVWX'


def log(*a):
    s = ' '.join(str(x) for x in a)
    if TOKEN:
        s = s.replace(TOKEN, '***')
    print(datetime.datetime.now(TW).strftime('%H:%M:%S'), s, flush=True)


def snap(did):
    u = 'https://api.finmindtrade.com/api/v4/taiwan_options_snapshot?' + urllib.parse.urlencode({'data_id': did, 'token': TOKEN})
    with urllib.request.urlopen(u, timeout=40) as r:
        j = json.loads(r.read())
    if j.get('status') != 200:
        raise RuntimeError(str(j.get('msg'))[:80])
    return j.get('data') or []


def session(now):
    wd, m, d = now.weekday(), now.hour * 60 + now.minute, now.date().isoformat()
    if wd <= 4 and 525 <= m <= 826:
        return 'D', d, 525
    if wd <= 4 and m >= 900:
        return 'N', d, 900
    if 1 <= wd <= 5 and m <= 301:
        return 'N', (now.date() - datetime.timedelta(days=1)).isoformat(), 900
    return None


def product(code):
    m = re.fullmatch(r'(\d{4})(\d{2})(?:([WF])(\d))?', code)
    if not m:
        return None
    y, mo = int(m.group(1)), int(m.group(2))
    prod = 'TXO' if not m.group(3) else ('TX' + m.group(4) if m.group(3) == 'W' else 'TX' + 'UVXYZ'[int(m.group(4)) - 1])
    return prod, MC[mo - 1] + str(y % 10), MP[mo - 1] + str(y % 10)


def minute_of(s):
    """'2026-11-06 11:43:45.712' → 分鐘數（台灣時間當成 UTC）"""
    try:
        d = datetime.datetime.fromisoformat(str(s)[:19]).replace(tzinfo=datetime.timezone.utc)
        return int(d.timestamp()) // 60
    except Exception:
        return None


def now_min():
    n = datetime.datetime.now(TW)
    return int(datetime.datetime(n.year, n.month, n.day, n.hour, n.minute, tzinfo=datetime.timezone.utc).timestamp()) // 60


def load_prev():
    repo = os.environ.get('GITHUB_REPOSITORY')
    out = {}
    if not repo:
        return out
    try:
        with urllib.request.urlopen(f'https://raw.githubusercontent.com/{repo}/{BRANCH}/all.json?t={int(time.time())}', timeout=30) as r:
            j = json.loads(r.read())
        for code, s in (j.get('c') or {}).items():
            out[code] = {k: [list(b) for b in v] for k, v in s.items()}
    except Exception:
        pass
    return out


def push(data):
    os.makedirs(WORK, exist_ok=True)
    for f in os.listdir(WORK):
        if f.endswith('.json'):
            os.remove(os.path.join(WORK, f))
    upd = datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M:%S')
    for code, s in data.items():
        json.dump({'updated': upd, 'code': code, 's': s}, open(os.path.join(WORK, code + '.json'), 'w'), separators=(',', ':'))
    json.dump({'updated': upd, 'c': data}, open(os.path.join(WORK, 'all.json'), 'w'), separators=(',', ':'))
    repo = os.environ.get('GITHUB_REPOSITORY'); tok = os.environ.get('GH_TOKEN')
    if not repo or not tok:
        return
    sh = lambda *c: subprocess.run(c, cwd=WORK, capture_output=True, text=True)
    subprocess.run(['rm', '-rf', os.path.join(WORK, '.git')])
    sh('git', 'init', '-q'); sh('git', 'checkout', '-q', '-b', BRANCH); sh('git', 'add', '-A')
    sh('git', '-c', 'user.name=optk-live', '-c', 'user.email=optk-live@users.noreply.github.com', 'commit', '-q', '-m', 'live')
    r = sh('git', 'push', '-qf', f'https://x-access-token:{tok}@github.com/{repo}', BRANCH)
    if r.returncode:
        log('push fail', r.stderr[-200:])


def main():
    if not TOKEN:
        log('沒有 FINMIND_TOKEN'); return
    data = load_prev()                 # code -> {"41700C": [[t,o,h,l,c,v], ...]}
    st = {}                            # options_id -> [累計量, 最高, 最低, 最後價, 時段]
    last_push = 0
    while True:
        now = datetime.datetime.now(TW)
        ses = session(now)
        if time.time() - T0 > MAX_RUN:
            nxt = ses or session(now + datetime.timedelta(minutes=10))
            if nxt and os.environ.get('GH_TOKEN'):
                subprocess.run(['gh', 'workflow', 'run', 'optk-live.yml', '--ref', 'main'], capture_output=True)
                log('時段還沒結束，已接著開下一次')
            break
        if not ses:
            if session(now + datetime.timedelta(minutes=20)):
                time.sleep(30); continue
            log('不在交易時段，結束'); break
        s, x, a = ses
        start = minute_of(x + ' 00:00:00') + a
        loop0 = time.time()
        try:
            strikes = json.load(open(os.path.join(ROOT, 'data', 'force', 'strikes.json')))
            today = now.date().isoformat()
            exps = [e for e in strikes['exps'] if e['exp'] >= today]
            prods = {}
            for e in exps:
                p = product(e['code'])
                if p:
                    prods.setdefault(p[0], []).append((e['code'], p[1], p[2]))
            nrow = 0
            for pid, lst in prods.items():
                try:
                    rows = snap(pid)
                except Exception as ex:
                    log('snap fail', pid, ex); continue
                for r in rows:
                    m = re.fullmatch(r'[A-Z0-9]{3}(\d+)([A-X])(\d)', str(r.get('options_id')))
                    if not m:
                        continue
                    suf = m.group(2) + m.group(3)
                    it = next((q for q in lst if suf in (q[1], q[2])), None)
                    if not it:
                        continue
                    tm = minute_of(r.get('date'))
                    if tm is None or tm < start:
                        continue                 # 這個時段還沒成交
                    code, cl, _ = it
                    cp = 'C' if suf == cl else 'P'
                    key = str(int(m.group(1))) + cp
                    oid = r['options_id']
                    tv = int(r.get('total_volume') or 0)
                    c = float(r.get('close') or 0)
                    hi, lo = float(r.get('high') or 0), float(r.get('low') or 0)
                    if c <= 0:
                        continue
                    p = st.get(oid)
                    first = False
                    if p is None or p[4] != start or tv < p[0]:
                        # 第一次看到：剛開盤（3 分鐘內）就從 0 算、開盤價用時段開盤；
                        # 中途才開始錄（或重新接手）就只記下目前累計量，從下一分鐘開始算，避免整段量灌進一根
                        fresh = now_min() - start <= 3 or (p is not None and p[4] != start)
                        if fresh:
                            p = st[oid] = [0, hi, lo, float(r.get('open') or c), start, tm]
                            first = True
                        else:
                            st[oid] = [tv, hi, lo, c, start, tm]
                            continue
                    dv = tv - p[0]
                    if dv > 0:
                        bars = data.setdefault(code, {}).setdefault(key, [])
                        if bars and bars[-1][0] == tm:
                            b = bars[-1]
                        elif bars and bars[-1][0] > tm:
                            b = None
                        else:
                            # 開盤價：上一筆成交在 2 分鐘內才接上一筆的價；冷門檔隔很久才成交，就用這一筆的價，避免畫出假的長紅長黑
                            o = p[3] if (first or (p[5] is not None and tm - p[5] <= 2)) else c
                            b = [tm, o, max(o, c), min(o, c), c, 0]
                            bars.append(b)
                        if b is not None:
                            b[2] = max(b[2], c); b[3] = min(b[3], c); b[4] = c; b[5] += dv
                            if first and hi > 0:
                                b[2] = max(b[2], hi); b[3] = min(b[3], lo) if lo > 0 else b[3]
                            elif hi > p[1] > 0:
                                b[2] = max(b[2], hi)
                            elif lo > 0 and p[2] > 0 and lo < p[2]:
                                b[3] = min(b[3], lo)
                        nrow += 1
                    p[0], p[1], p[2], p[3] = tv, hi, lo, c
                    if dv > 0:
                        p[5] = tm
            # 只留最近 30 小時
            cut = now_min() - 30 * 60
            for code in list(data):
                if code not in [q[0] for v in prods.values() for q in v]:
                    del data[code]; continue
                for k in list(data[code]):
                    data[code][k] = [b for b in data[code][k] if b[0] >= cut]
                    if not data[code][k]:
                        del data[code][k]
        except Exception as ex:
            log('poll fail', repr(ex)[:200])
        if time.time() - last_push > 110:
            log('push', {c: len(v) for c, v in data.items()})
            push(data); last_push = time.time()
        time.sleep(max(5, POLL - (time.time() - loop0)))
    push(data)


if __name__ == '__main__':
    main()
