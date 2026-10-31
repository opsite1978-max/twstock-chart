#!/usr/bin/env node
/*
 尋底選股：全市場日／週／月 K，每個週期用各自回測最穩的底部指標，數最近幾根內有幾個同時出底部訊號。
 指標程式碼直接從 zhanshen.html 取出來跑（scripts/zs_engine.js），和看盤畫面一模一樣。
 資料：data/price/days（每日行情）＋ data/price/long（2000 年起的週／月開高低收量），不用 FinMind 額度。
   node scripts/bottom_scan.js   → data/price/bottom.json
*/
const fs = require('fs'), path = require('path'), os = require('os'), cp = require('child_process');
const ROOT = path.join(__dirname, '..');
const DAYS = path.join(ROOT, 'data', 'price', 'days'), LONG = path.join(ROOT, 'data', 'price', 'long');
const OUT = path.join(ROOT, 'data', 'price', 'bottom.json');
// 每個週期各自回測挑指標（日 K 有效的到週 K 幾乎沒用，所以分開挑）
// 日 K：2025/1～2026/9 全市場、抱 20 天，四個半年至少三個贏過隨便挑一天
// 週 K：2010～2026 全市場、抱 8 週，四個時期都贏過隨便挑一週
// 月 K：2010～2026 全市場、抱 6 個月，四個時期都贏過隨便挑一個月（樣本較少）
const A = {
  d:{ sy:['底★'], cd:['起漲★'], zx:['入市', '見底', '底', '機會'], hm:['黑馬★'], cci:['底'], lx:['真龍★★', '尋龍底★'], ls:['機會'], km:['底'] },
  w:{ zx:['入市'], bbot:['進入大底區'] },
  m:{ valley:['谷底'], wl:['底★'], zx:['入市'] }
};
// 參考（列出來、不計分）：只在部分時期有效
const Bg = {
  d:{ so:['底★★', '底★'], wl:['底★'], ym:['底'], vo:['底★'], camo:['底★'], bbot:['進入大底區'], xg:['反轉訊號'] },
  w:{ md:['底背離'], lbb:['BUY'], camo:['底★'], zx:['機會'], ym:['底'] },
  m:{ hm:['黑馬★'], lx:['真龍★★', '尋龍底★'], zx:['見底'], fz:['轉折底'] }
};
const LOOK = { d:10, w:8, m:6 };
const KEYS = [...new Set(['d', 'w', 'm'].flatMap(t => [...Object.keys(A[t]), ...Object.keys(Bg[t])]))], FN = Object.fromEntries(KEYS.map(k => [k, 'calc' + k.toUpperCase()]));

function loadDays(){
  const files = fs.readdirSync(DAYS).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).sort();
  const dates = [], ser = {};
  for (const f of files){ const d = JSON.parse(fs.readFileSync(path.join(DAYS, f), 'utf8')); dates.push(d.d); for (const [c, v] of Object.entries(d.r)) (ser[c] = ser[c] || []).push([d.d, v]); }
  return { dates, ser };
}
const monday = d => { const x = new Date(d + 'T00:00:00Z'); x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7)); return x.toISOString().slice(0, 10); };
function mkB(iv, d, o, h, l, c, v){ const n = c.length, F = Float64Array;
  return { n, iv, intra:false, d, o:F.from(o), h:F.from(h), l:F.from(l), c:F.from(c), v:F.from(v), vraw:F.from(v), t:new F(n), conf:new Uint8Array(n).fill(1) }; }
function aggregate(rows, keyf, LP, pre){
  let K = [], o = [], h = [], l = [], c = [], v = [];
  if (LP && LP[pre] && LP[pre].length){
    const N = LP[pre].length, ok = Array.isArray(LP[pre + 'h']) && LP[pre + 'h'].length === N, okv = Array.isArray(LP[pre + 'v']) && LP[pre + 'v'].length === N;
    K = LP[pre + 'k'].slice(); c = LP[pre].slice();
    o = ok ? LP[pre + 'o'].slice() : c.slice(); h = ok ? LP[pre + 'h'].slice() : c.slice(); l = ok ? LP[pre + 'l'].slice() : c.slice(); v = okv ? LP[pre + 'v'].slice() : new Array(N).fill(0);
  }
  for (const [dd, r] of rows){
    const k = keyf(dd);
    if (K.length && K[K.length - 1] === k){ const j = K.length - 1; h[j] = Math.max(h[j], r[1]); l[j] = Math.min(l[j], r[2]); c[j] = r[3]; v[j] += r[4] || 0; }
    else if (K.length && k < K[K.length - 1]) continue;
    else { K.push(k); o.push(r[0] || r[3]); h.push(r[1]); l.push(r[2]); c.push(r[3]); v.push(r[4] || 0); }
  }
  return { K, o, h, l, c, v };
}
function scan(M, B, t){
  M.__setB(B); const n = B.n, a = [], b = [], look = LOOK[t], GA = A[t], GB = Bg[t];
  for (const k of new Set([...Object.keys(GA), ...Object.keys(GB)])){
    const want = [...(GA[k] || []), ...(GB[k] || [])]; let r;
    try { r = M[FN[k]](B, M.defaults(k)); } catch (e){ continue; }
    if (!r || r.error) continue;
    let best = null;
    let bestB = null;
    for (const s of r.sig || []){ const x = String(s.text).split('（')[0]; const ago = n - 1 - s.i; if (ago >= look) continue;
      if (GA[k] && GA[k].includes(x)){ if (!best || ago < best[2]) best = [k, x, ago]; }
      else if (GB[k] && GB[k].includes(x)){ if (!bestB || ago < bestB[2]) bestB = [k, x, ago]; } }
    if (best) a.push(best); if (bestB) b.push(bestB);
  }
  return [a.length, a, b];
}
function worker(shard, NS){
  const E = require('./zs_engine.js');
  const M = E.load(path.join(ROOT, 'zhanshen.html'), [...Object.values(FN), 'SCHEMA', 'defaults']);
  const { dates, ser } = loadDays(), last = dates[dates.length - 1], out = [];
  const codes = Object.keys(ser).sort();
  codes.forEach((code, ci) => {
    if (ci % NS !== shard) return;
    const rows = ser[code].filter(([, r]) => r && r[3] && r[1] && r[2]);
    if (rows.length < 60 || rows[rows.length - 1][0] !== last) return;
    let LP = null, lf = 0; const p = path.join(LONG, code + '.json');
    if (fs.existsSync(p)){ try { LP = JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e){} lf = !LP ? 0 : !LP.w ? 3 : LP.wv ? 3 : LP.wh ? 2 : 1; }
    const res = {};
    if (rows.length >= 120) res.d = scan(M, mkB('D', rows.map(x => x[0]), rows.map(x => x[1][0] || x[1][3]), rows.map(x => x[1][1]), rows.map(x => x[1][2]), rows.map(x => x[1][3]), rows.map(x => x[1][4] || 0)), 'd');
    for (const [t, keyf] of [['w', monday], ['m', d => d.slice(0, 7)]]){
      if (lf !== 3) { res[t] = null; continue; }        // 長期資料還沒補完（缺高低點或成交量）→ 先不算，避免和看盤不一樣
      const g = aggregate(rows, keyf, LP, t);
      res[t] = g.c.length >= 60 ? scan(M, mkB(t.toUpperCase(), g.K, g.o, g.h, g.l, g.c, g.v), t) : null;
    }
    const C = rows.map(x => x[1][3]), V = rows.map(x => x[1][4] || 0), n = C.length;
    out.push([code, C[n - 1], n > 1 ? Math.round((C[n - 1] / C[n - 2] - 1) * 1e4) / 100 : 0, Math.round(V.slice(-20).reduce((s, x) => s + x, 0) / Math.min(20, n)), lf, res.d || null, res.w || null, res.m || null]);
  });
  fs.writeFileSync(path.join(os.tmpdir(), 'bottom_' + shard + '.json'), JSON.stringify(out));
  process.exit(0);
}
function main(){
  const NS = Math.max(1, Math.min(8, os.cpus().length)), t0 = Date.now();
  const kids = []; for (let s = 0; s < NS; s++) kids.push(new Promise((ok, bad) => cp.fork(__filename, ['worker', s, NS]).on('exit', c => c ? bad(new Error('worker ' + s + ' exit ' + c)) : ok())));
  Promise.all(kids).then(() => {
    const E = require('./zs_engine.js');
    const M = E.load(path.join(ROOT, 'zhanshen.html'), ['SCHEMA']);
    let info = {}; try { for (const s of JSON.parse(fs.readFileSync(path.join(ROOT, 'insider', 'universe.json'), 'utf8')).stocks) info[s.code] = s; } catch (e){}
    let rows = []; for (let s = 0; s < NS; s++) rows = rows.concat(JSON.parse(fs.readFileSync(path.join(os.tmpdir(), 'bottom_' + s + '.json'), 'utf8')));
    rows = rows.map(r => { const u = info[r[0]] || {}; return [r[0], u.name || '', u.market || '', u.ind || '', ...r.slice(1)]; }).filter(r => [r[8], r[9], r[10]].some(t => t && (t[1].length || t[2].length))).sort((x, y) => x[0].localeCompare(y[0]));
    const files = fs.readdirSync(DAYS).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).sort();
    let st = {}; try { st = JSON.parse(fs.readFileSync(path.join(LONG, '_status.json'), 'utf8')); } catch (e){}
    const names = Object.fromEntries(KEYS.map(k => [k, (M.SCHEMA[k] && M.SCHEMA[k].tab) || k]));
    const now = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16) + '+08:00';
    const out = { updated:now, latest:files[files.length - 1].slice(0, 10), look:LOOK, A, B:Bg, names, long:st,
      cols:['code', 'name', 'market', 'ind', 'c', 'chg', 'v20', 'lf', 'd', 'w', 'm'], rows };
    fs.writeFileSync(OUT, JSON.stringify(out));
    const REC = { d:3, w:1, m:2 }, cnt = t => rows.filter(r => r[{ d:8, w:9, m:10 }[t]] && r[{ d:8, w:9, m:10 }[t]][0] >= REC[t]).length;
    console.log('bottom', rows.length, 'stocks; 達建議門檻 d/w/m', cnt('d'), cnt('w'), cnt('m'), 'in', Math.round((Date.now() - t0) / 1000) + 's');
    process.exit(0);
  }).catch(e => { console.error(e); process.exit(1); });
}
if (process.argv[2] === 'worker') worker(+process.argv[3], +process.argv[4]); else main();
