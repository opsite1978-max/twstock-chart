#!/usr/bin/env python3
"""
產生 TradingView 版「台指隱含波動率反看・找底」→ data/pine/iv_txo.pine（不連 FinMind）

TradingView 讀不到本站資料，所以把本站算好的資料直接寫進程式碼裡：
  日 K：data/txo/iv.json（每天收盤後自算的台指選擇權隱含波動率，30 天恆定與最近合約價平）
  分 K：data/force/m5/*.json 裡的台指 VIX（期交所，只有日盤 08:45–13:45）
        最近 40 個交易日用 5 分鐘，更早的用 15 分鐘（程式碼不能太大）
計算方式和看盤頁的隱含波動率副圖完全相同（只用有數值的 K 棒算上下界，「底」＝衝出上界後回到上界以內）。
"""
import json, os, glob, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'data', 'pine')
TW = datetime.timezone(datetime.timedelta(hours=8))
FINE_DAYS = 40
CHUNK = 3500


def yymmdd(d):
    return d[2:4] + d[5:7] + d[8:10]


def chunks(records):
    out, cur = [], ''
    for r in records:
        if cur and len(cur) + len(r) + 1 > CHUNK:
            out.append(cur); cur = ''
        cur += r + ';'
    if cur:
        out.append(cur)
    return out or ['']


def main():
    iv = json.load(open(os.path.join(ROOT, 'data', 'txo', 'iv.json'), encoding='utf-8'))
    drec = []
    for x in iv.get('days', []):
        a, b = x.get('iv30'), x.get('near')
        if not a and not b:
            continue
        drec.append('%s=%s=%s' % (yymmdd(x['d']), int(round(a * 100)) if a else '', int(round(b * 100)) if b else ''))
    day_last = iv['days'][-1]['d'] if iv.get('days') else ''
    # 分 K：每個交易日日盤 61 根 5 分鐘（08:45 起）
    days = {}
    for f in sorted(glob.glob(os.path.join(ROOT, 'data', 'force', 'm5', '*.json'))):
        j = json.load(open(f, encoding='utf-8'))
        it, iv_i = j['cols'].index('t'), j['cols'].index('vix')
        for s in j['sessions']:
            if s['s'] != 'D':
                continue
            slots = [None] * 61
            for b in s['bars']:
                v = b[iv_i]
                if v is None or v <= 0:
                    continue
                t = datetime.datetime.fromtimestamp(b[it], datetime.timezone.utc)
                k = (t.hour * 60 + t.minute - 525) // 5
                if 0 <= k < 61:
                    slots[k] = v
            if any(v is not None for v in slots):
                days[s['td']] = slots
    order = sorted(days)
    mrec = []
    for n, d in enumerate(order):
        slots = days[d]
        if n >= len(order) - FINE_DAYS:
            step, vals = 5, slots
        else:
            step, vals = 15, []
            for blk in range(0, 61, 3):
                seg = [v for v in slots[blk:blk + 3] if v is not None]
                vals.append(seg[-1] if seg else None)
        while vals and vals[-1] is None:
            vals = vals[:-1]
        mrec.append('%s|%d|%s' % (yymmdd(d), step, ','.join('' if v is None else str(int(round(v * 100))) for v in vals)))
    min_last = order[-1] if order else ''
    dch, mch = chunks(drec), chunks(mrec)
    upd = datetime.datetime.now(TW).strftime('%Y-%m-%d %H:%M')
    tpl = open(os.path.join(ROOT, 'scripts', 'pine_iv_template.pine'), encoding='utf-8').read()
    data = '\n'.join('string DAY%d = "%s"' % (i, c) for i, c in enumerate(dch)) + '\n' + \
           '\n'.join('string MIN%d = "%s"' % (i, c) for i, c in enumerate(mch)) + '\n' + \
           'array<string> DAYS = array.from(%s)\n' % ', '.join('DAY%d' % i for i in range(len(dch))) + \
           'array<string> MINS = array.from(%s)\n' % ', '.join('MIN%d' % i for i in range(len(mch)))
    code = tpl.replace('{{UPDATED}}', upd).replace('{{DAYLAST}}', day_last).replace('{{MINLAST}}', min_last).replace('{{DATA}}', data)
    os.makedirs(OUT, exist_ok=True)
    open(os.path.join(OUT, 'iv_txo.pine'), 'w', encoding='utf-8').write(code)
    json.dump({'updated': upd, 'dayLast': day_last, 'minLast': min_last, 'days': len(drec), 'minDays': len(order),
               'chars': len(code)}, open(os.path.join(OUT, 'iv_txo.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    print('pine written', len(code), 'chars', len(drec), 'daily', len(order), 'intraday days')


if __name__ == '__main__':
    main()
