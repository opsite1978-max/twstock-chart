#!/usr/bin/env python3
"""臺指 VIX 動態上下界的轉折訊號回測（讀 vix_build.py 的輸出，不連網）。

上下界：中線＝波動率的 n 日指數平均；上界／下界＝中線 ± k × n 日標準差（也試 log 版本）。
買訊（恐慌退潮）：波動率最近 L 天內站上上界，今天跌回上界以下且比昨天低。
買訊 2（恐慌高點回落）：最近 L 天高點在上界以上，今天比高點回落 x%。
賣訊（自滿結束）：波動率最近 L 天內跌破下界，今天回到下界以上且比昨天高。
賣訊 2（恐慌剛開始）：波動率今天由下往上穿越上界。
評估：之後 5／10／20 天加權指數報酬（和全部日子的平均比）、勝率、是不是真的轉折（訊號日離前後 10 天最低點／最高點 2% 內）。
訓練 2012～2019，測試 2020 以後。
"""
import json, os, sys, math, statistics as ST

D = sys.argv[1] if len(sys.argv) > 1 else 'vix-out'
vj = json.load(open(os.path.join(D, 'calc.json')))
tx = {r[0]: r[1] for r in json.load(open(os.path.join(D, 'taiex.json')))}
use = json.load(open(os.path.join(D, 'vix.json'))).get('use', 'B')
IDX = 0 if use == 'A' else 1
dates = [d for d in sorted(vj) if d in tx and vj[d][IDX]]
V = [vj[d][IDX] for d in dates]
P = [tx[d] for d in dates]
N = len(dates)
print('資料', dates[0], '～', dates[-1], N, '天，用', use)


def ema(x, n):
    a = 2 / (n + 1); o = []; m = None
    for v in x:
        m = v if m is None else m + a * (v - m); o.append(m)
    return o


def bands(x, n, k, logv=False, mid='ema'):
    y = [math.log(v) for v in x] if logv else x
    m = ema(y, n) if mid == 'ema' else [ST.mean(y[max(0, i - n + 1):i + 1]) for i in range(len(y))]
    sd = [ST.pstdev(y[max(0, i - n + 1):i + 1]) if i >= 2 else 0 for i in range(len(y))]
    U = [m[i] + k * sd[i] for i in range(len(y))]; L = [m[i] - k * sd[i] for i in range(len(y))]
    if logv: U = [math.exp(u) for u in U]; L = [math.exp(l) for l in L]; m = [math.exp(v) for v in m]
    return m, U, L


def fwd(i, h):
    return P[i + h] / P[i] - 1 if i + h < N else None


def is_turn(i, low=True):
    a, b = max(0, i - 10), min(N, i + 11)
    w = P[a:b]
    return (P[i] <= min(w) * 1.02) if low else (P[i] >= max(w) * 0.98)


def signals(n, k, L, rule, logv, mid, x=0.15):
    m, U, Lo = bands(V, n, k, logv, mid)
    out = []; last = -99
    for i in range(max(n, L) + 1, N):
        s = False
        if rule == 'buy_back':      s = any(V[j] > U[j] for j in range(i - L, i)) and V[i] < U[i] and V[i] < V[i - 1]
        elif rule == 'buy_peak':
            j = max(range(i - L, i + 1), key=lambda j: V[j]); s = V[j] > U[j] and V[i] <= V[j] * (1 - x) and V[i - 1] > V[j] * (1 - x)
        elif rule == 'sell_back':   s = any(V[j] < Lo[j] for j in range(i - L, i)) and V[i] > Lo[i] and V[i] > V[i - 1]
        elif rule == 'sell_break':  s = V[i] > U[i] and V[i - 1] <= U[i - 1]
        elif rule == 'buy_break':   s = V[i] > U[i] and V[i - 1] <= U[i - 1]
        if s and i - last >= 10: out.append(i); last = i
    return out


def stats(idx, buy, a, b):
    sel = [i for i in idx if a <= dates[i] < b]
    res = {'n': len(sel)}
    for h in (5, 10, 20):
        r = [fwd(i, h) for i in sel if fwd(i, h) is not None]
        base = [fwd(i, h) for i in range(N) if a <= dates[i] < b and fwd(i, h) is not None]
        if not r: return res
        sgn = 1 if buy else -1
        res[h] = (round(ST.mean(r) * 100, 2), round(ST.mean(base) * 100, 2), round(sum(1 for v in r if sgn * v > 0) / len(r) * 100))
    res['turn'] = round(sum(1 for i in sel if is_turn(i, buy)) / max(1, len(sel)) * 100)
    return res


TR, TE = ('2012-01-01', '2020-01-01'), ('2020-01-01', '2099-01-01')
rows = []
for rule, buy in (('buy_back', True), ('buy_peak', True), ('sell_back', False), ('sell_break', False)):
    for n in (10, 20, 40):
        for k in (1.5, 2.0, 2.5):
            for L in (3, 5, 10):
                for logv in (False, True):
                    idx = signals(n, k, L, rule, logv, 'ema')
                    a, b = stats(idx, buy, *TR), stats(idx, buy, *TE)
                    if a.get('n', 0) < 8 or 10 not in a: continue
                    edge = (a[10][0] - a[10][1]) * (1 if buy else -1)
                    rows.append((rule, n, k, L, logv, edge, a, b))
for rule in ('buy_back', 'buy_peak', 'sell_back', 'sell_break'):
    rs = sorted([r for r in rows if r[0] == rule], key=lambda r: -r[5])
    print('\n==', rule, '（依訓練期 10 天超額報酬排序，前 6）')
    for r in rs[:6]:
        print(f'  n={r[1]} k={r[2]} L={r[3]} log={int(r[4])} | 訓練 {r[6]} | 測試 {r[7]}')
    # 穩健度：所有參數組合在測試期的平均超額
    te = [(r[7][10][0] - r[7][10][1]) * (1 if rule.startswith('buy') else -1) for r in rs if 10 in r[7]]
    if te: print('  所有參數在測試期 10 天超額報酬：平均', round(ST.mean(te), 2), '% 、為正的比例', round(sum(1 for v in te if v > 0) / len(te) * 100), '%')
# 目前狀態
for n, k in ((20, 2.0), (20, 1.5)):
    m, U, Lo = bands(V, n, k)
    print('\n最新', dates[-1], '波動率', V[-1], '中線', round(m[-1], 2), '上界', round(U[-1], 2), '下界', round(Lo[-1], 2), f'(n={n}, k={k})')
