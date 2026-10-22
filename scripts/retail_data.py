#!/usr/bin/env python3
"""
散戶指標原始資料 → data/retail/

散戶沒有每分鐘的官方資料，所以用「小單」代表散戶（一筆成交口數很少的單子，大戶、法人很少這樣下單）：
  期貨：小台（MTX）、微台（TMF）逐筆成交，一筆 ≤ 2 口算小單（逐筆資料每筆記兩次，口數先除以 2）。
        用價格跳動推估主動買賣（比上一筆高＝主動買、低＝主動賣、一樣價沿用上一筆方向），換算大台口數（小台 ×0.25、微台 ×0.05）。
  選擇權：台指選擇權逐筆成交，一筆 ≤ 2 口算小單；方向一樣用價格跳動推估，乘上 Delta 換成等值大台口數
         （Delta 用前一次結算價反推的隱含波動率＋當分鐘台指期價格，和多空力道同一套，不偷看未來）。
  也記大單（> 2 口）當對照。
每天另外存期交所公布的「三大法人以外」的成交淨口數（官方的散戶＝全市場扣掉三大法人），用來檢查小單推估準不準。

輸出：
  data/retail/m1/YYYY-MM-DD.json  每個日曆日每分鐘：fs 期貨小單淨買、fl 期貨大單淨買、fsg 期貨小單總量、os 選擇權小單淨 Delta、ol 選擇權大單淨 Delta、osg 選擇權小單總 Delta（大台口數）
  data/retail/m5/YYYY-MM.json     依交易日、日盤／夜盤整理好的 5 分鐘資料（和 data/force/m5 的時間一一對應）
  data/retail/daily.json          每天官方散戶（非法人）成交淨口數：期貨（大台約當）、買權、賣權
FinMind 憑證從環境變數 FINMIND_TOKEN 讀取，不會印出或存檔。
"""
import os, sys, json, time, datetime, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import force_data as fd

OUT = os.path.join(fd.ROOT, 'data', 'retail')
M1 = os.path.join(OUT, 'm1')
M5 = os.path.join(OUT, 'm5')
FM1 = os.path.join(fd.ROOT, 'data', 'force', 'm1')
START = os.environ.get('RETAIL_START', '2026-01-05')
MAX_MIN = float(os.environ.get('MAX_MINUTES', '100'))
MAX_FILES = int(os.environ.get('MAX_FILES', '400'))
SMALL = 2
WF = {'MTX': 0.25, 'TMF': 0.05}
COLS = ['m', 'fs', 'fl', 'fsg', 'os', 'ol', 'osg']
T0 = time.time()


def fut_minutes(x, windows):
    """小台、微台逐筆 → 每分鐘 小單淨買、大單淨買、小單總量（大台口數）"""
    win = set()
    for lo, hi in windows:
        win.update(range(lo, hi + 1))
    acc = {}
    for fid, w in WF.items():
        rows = fd.api({'dataset': 'TaiwanFuturesTick', 'data_id': fid, 'start_date': x, 'end_date': x}, timeout=600)
        rows = [r for r in rows if str(r.get('date', '')).startswith(x)]
        rows.sort(key=lambda r: r['date'])
        state = {}
        for r in rows:
            c = str(r.get('contract_date') or '').strip()
            if not re.fullmatch(r'\d{6}', c):
                continue                                        # 價差單不算
            t = r['date']; m = int(t[11:13]) * 60 + int(t[14:16])
            if m not in win:
                continue
            v = (r.get('volume') or 0) / 2                      # 每筆記兩次
            if v <= 0:
                continue
            p = float(r['price'])
            st = state.get(c)
            side = 0
            if st:
                side = 1 if p > st[0] else -1 if p < st[0] else st[1]
            state[c] = (p, side)
            a = acc.setdefault(m, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
            if v <= SMALL:
                a[2] += v * w
                a[0] += side * v * w
            else:
                a[1] += side * v * w
    return acc


def opt_minutes(x, windows, fut_ref, px, used):
    rows = fd.api({'dataset': 'TaiwanOptionTick', 'data_id': 'TXO', 'start_date': x}, timeout=600)
    pa = fd.prev_td(x)
    sm_a = fd.build_smile(pa) if pa else {}
    ref_a = fut_ref.get(pa)
    sm_b, ref_b = sm_a, ref_a
    if x in fd.CALSET:
        try:
            sm_b = fd.build_smile(x) or sm_a
            ref_b = fut_ref.get(x) or ref_a
        except fd.RateLimited:
            raise
        except Exception as e:
            fd.log('smile fail', x, e)
    if not sm_a or not ref_a:
        return None
    win = set()
    for lo, hi in windows:
        win.update(range(lo, hi + 1))
    rows.sort(key=lambda r: r['date'])
    acc, state, dcache = {}, {}, {}
    for r in rows:
        t = r['date']
        if not t.startswith(x):
            continue
        m = int(t[11:13]) * 60 + int(t[14:16])
        if m not in win:
            continue
        v = r.get('volume') or 0
        if v <= 0:
            continue
        code = str(r.get('contract_date') or '').strip()
        K = float(r['ExercisePrice']); call = r['PutCall'] == 'C'; p = float(r['price'])
        key = (code, K, call)
        st = state.get(key)
        side = 0
        if st:
            side = 1 if p > st[0] else -1 if p < st[0] else st[1]
        state[key] = (p, side)
        if side == 0:
            continue
        dk = (code, K, call, m)
        dl = dcache.get(dk)
        if dl is None:
            sm, ref = (sm_b, ref_b) if m >= fd.OPT_AVAIL else (sm_a, ref_a)
            c = sm.get(code)
            if c is None:
                exp = fd.contract_exp(code)
                if exp and sm:
                    c = min(sm.values(), key=lambda q: abs(fd.day_diff(q['exp'], exp)))
            if c is None or not px.get(m):
                dl = 0.0
            else:
                exp = fd.contract_exp(code) or c['exp']
                T = max(600, fd.exp_ts(exp) - (fd.ts(x, m) + 30)) / fd.YEAR_S
                dl = fd.delta(c['F'] * px[m] / ref, K, T, fd.iv_at(c, K), call)
            dcache[dk] = dl
        a = acc.setdefault(m, [0.0, 0.0, 0.0])
        d = dl * fd.TXEQ / used
        if v <= SMALL:
            a[0] += side * d * v; a[2] += abs(d) * v
        else:
            a[1] += side * d * v
    return acc


def process(x, fut_ref):
    fj = fd.load_json(os.path.join(FM1, x + '.json'), None)
    if not fj or not fj.get('bars'):
        return None
    windows = fd.session_windows(x)
    if not windows:
        return None
    ci = fj['cols'].index('c')
    px, last = {}, None
    have = {b[0]: b[ci] for b in fj['bars']}
    for lo, hi in windows:
        for m in range(lo, hi + 1):
            if m in have:
                last = have[m]
            px[m] = last
    used = fj.get('dupUsed') or 2.0
    fa = fut_minutes(x, windows)
    oa = opt_minutes(x, windows, fut_ref, px, used)
    if oa is None:
        fd.log('no smile', x); return None
    bars = []
    for b in fj['bars']:
        m = b[0]
        f = fa.get(m, [0, 0, 0]); o = oa.get(m, [0, 0, 0])
        bars.append([m, round(f[0], 3), round(f[1], 3), round(f[2], 3), round(o[0], 3), round(o[1], 3), round(o[2], 3)])
    return {'d': x, 'complete': bool(fj.get('complete')), 'cols': COLS, 'bars': bars}


def build_m5():
    files = sorted(f[:-5] for f in os.listdir(M1) if f.endswith('.json'))
    sess = {}
    for x in files:
        j = fd.load_json(os.path.join(M1, x + '.json'), None)
        if not j:
            continue
        for b in j['bars']:
            m = b[0]
            if m <= 300:
                td, s = (x if x in fd.CALSET else fd.next_td(x)), 'N'
            elif 525 <= m <= 825:
                td, s = x, 'D'
            elif m >= 900:
                td, s = fd.next_td(x), 'N'
            else:
                continue
            sess.setdefault((td, s), []).append([fd.ts(x, m)] + b[1:])
    months = {}
    for (td, s), bars in sess.items():
        bars.sort(key=lambda b: b[0])
        agg, cur = [], None
        for b in bars:
            k = b[0] // 300 * 300
            if cur is None or cur[0] != k:
                cur = [k, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]; agg.append(cur)
            for i in range(1, 7):
                cur[i] += b[i]
        for a in agg:
            for i in range(1, 7):
                a[i] = round(a[i], 2)
        months.setdefault(td[:7], []).append({'td': td, 's': s, 'bars': agg})
    os.makedirs(M5, exist_ok=True)
    for mo, lst in months.items():
        lst.sort(key=lambda q: (q['td'], 0 if q['s'] == 'N' else 1))
        txt = json.dumps({'cols': ['t'] + COLS[1:], 'sessions': lst}, separators=(',', ':'))
        p = os.path.join(M5, mo + '.json')
        if not os.path.exists(p) or open(p).read() != txt:
            open(p, 'w').write(txt)
    return files, sorted(months)


def official_daily(s0, today):
    """官方散戶＝全市場扣掉三大法人：散戶淨買口數＝−（三大法人買進成交口數 − 賣出成交口數）"""
    out = {}
    for fid, w in (('TX', 1.0), ('MTX', 0.25), ('TMF', 0.05)):
        for r in fd.api({'dataset': 'TaiwanFuturesInstitutionalInvestors', 'data_id': fid, 'start_date': s0, 'end_date': today}):
            e = out.setdefault(r['date'], {'d': r['date'], 'f': 0.0, 'fS': 0.0, 'c': 0, 'p': 0})
            net = (r.get('long_deal_volume') or 0) - (r.get('short_deal_volume') or 0)
            e['f'] -= net * w
            if fid != 'TX':
                e['fS'] -= net * w                              # 只看小台＋微台
    for r in fd.api({'dataset': 'TaiwanOptionInstitutionalInvestors', 'data_id': 'TXO', 'start_date': s0, 'end_date': today}):
        e = out.setdefault(r['date'], {'d': r['date'], 'f': 0.0, 'fS': 0.0, 'c': 0, 'p': 0})
        net = (r.get('long_deal_volume') or 0) - (r.get('short_deal_volume') or 0)
        k = 'c' if str(r.get('call_put')) in ('買權', 'call', 'Call', 'C') else 'p'
        e[k] -= net
    for e in out.values():
        e['f'] = round(e['f'], 2); e['fS'] = round(e['fS'], 2)
    return [out[d] for d in sorted(out)]


def main():
    if not fd.TOKEN:
        fd.log('沒有 FINMIND_TOKEN'); return
    os.makedirs(M1, exist_ok=True)
    fd.REQ_BUDGET = fd.quota_ok()
    if fd.REQ_BUDGET <= 0:
        return
    fd.START = START
    today, s0 = fd.setup_calendar()
    fut_ref = fd.fut_daily_all(s0, today)
    targets = sorted(f[:-5] for f in os.listdir(FM1) if f.endswith('.json') and f[:-5] >= START)
    todo = []
    for x in targets:
        j = fd.load_json(os.path.join(M1, x + '.json'), None)
        fj = fd.load_json(os.path.join(FM1, x + '.json'), None)
        if j and j.get('complete') and fj and len(j['bars']) == len(fj.get('bars') or []):
            continue
        if fj and fj.get('bars'):
            todo.append(x)
    recent = [x for x in todo if x >= (datetime.date.fromisoformat(today) - datetime.timedelta(days=6)).isoformat()]
    order = sorted(recent) + sorted([x for x in todo if x not in recent], reverse=True)
    fd.log('retail targets', len(targets), 'todo', len(todo))
    done = 0
    try:
        for x in order:
            if done >= MAX_FILES or (time.time() - T0) / 60 > MAX_MIN:
                fd.log('time budget reached'); break
            t1 = time.time()
            try:
                res = process(x, fut_ref)
            except fd.RateLimited:
                raise
            except Exception as e:
                fd.log('fail', x, e); continue
            if res is None:
                continue
            json.dump(res, open(os.path.join(M1, x + '.json'), 'w'), separators=(',', ':'))
            done += 1
            fd.log('retail', x, 'bars', len(res['bars']), 'complete', res['complete'], '%.0fs' % (time.time() - t1), 'req', fd.NREQ[0])
            if len(fd.OPT_DAILY) > 6:
                fd.OPT_DAILY.clear()
    except fd.RateLimited as e:
        fd.log('STOP', e)
    files, months = build_m5()
    try:
        daily = official_daily((datetime.date.fromisoformat(START) - datetime.timedelta(days=30)).isoformat(), today)
        json.dump({'note': '官方散戶（全市場扣掉三大法人）成交淨口數：f＝期貨（大台約當，含大台）、fS＝小台＋微台（大台約當）、c＝買權淨買口數、p＝賣權淨買口數', 'days': daily},
                  open(os.path.join(OUT, 'daily.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    except fd.RateLimited:
        pass
    except Exception as e:
        fd.log('official daily fail', e)
    json.dump({'updated': datetime.datetime.now(fd.TW).strftime('%Y-%m-%d %H:%M'), 'start': START, 'files': files, 'months': months,
               'pending': len(order) - done, 'small': SMALL, 'cols': ['t'] + COLS[1:]},
              open(os.path.join(OUT, 'index.json'), 'w'), ensure_ascii=False, separators=(',', ':'))
    open(os.path.join(OUT, '_lastrun.txt'), 'w').write('\n'.join(fd.LOG[-300:]) + '\n')
    fd.log('done', done, 'requests', fd.NREQ[0], 'minutes %.1f' % ((time.time() - T0) / 60))


if __name__ == '__main__':
    main()
