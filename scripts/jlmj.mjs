// 九龍模金訣：直接借用「我的戰神看盤」裡的指標算法（同一份程式碼），對全市場日 K 計算九個訊號
// 用法：node scripts/jlmj.mjs            → data/price/jlmj.json（最新名單＋回測）
import fs from 'fs'; import path from 'path'; import vm from 'vm'; import { fileURLToPath } from 'url';
import * as acorn from 'acorn';
const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const PAGE = fs.readFileSync(path.join(ROOT, 'zhanshen-me.html'), 'utf8');

// ① 從網頁抽出函式與設定，放進一個沒有畫面的執行環境
function loadEngine(){
  const scripts = [...PAGE.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const src = scripts.find(s => s.includes('function calcBD(')); if (!src) throw new Error('找不到指標程式');
  const ast = acorn.parse(src, { ecmaVersion:'latest', sourceType:'script' });
  const noop = () => {}, el = new Proxy(function(){}, { get:(t, k) => k === Symbol.toPrimitive ? () => '' : el, apply:() => el, set:() => true });
  const ctx = { console, Math, Date, JSON, Float64Array, Float32Array, Int8Array, Uint8Array, Int32Array, Uint32Array, Array, Object, Number, String, Boolean, Map, Set, Symbol, Infinity, NaN, isNaN, isFinite, parseFloat, parseInt, Error, RegExp, Promise,
    window:{}, document:el, navigator:{ userAgent:'' }, location:{ search:'', hash:'', href:'' }, localStorage:{ getItem:() => null, setItem:noop, removeItem:noop }, addEventListener:noop, setTimeout:noop, clearTimeout:noop, requestAnimationFrame:noop, matchMedia:() => ({ matches:false, addEventListener:noop }), fetch:() => new Promise(noop) };
  ctx.window = ctx; vm.createContext(ctx);
  let ok = 0, bad = 0;
  for (const nd of ast.body){
    let code = src.slice(nd.start, nd.end);
    if (nd.type === 'FunctionDeclaration'){}
    else if (nd.type === 'VariableDeclaration') code = code.replace(/^(const|let)\s/, 'var ');
    else if (nd.type === 'ExpressionStatement' && nd.expression.type === 'AssignmentExpression' && /^SCHEMA\./.test(code)){}
    else continue;
    try { vm.runInContext(code, ctx); ok++; } catch (e){ bad++; }
  }
  return ctx;
}
const E = loadEngine();
const defaults = id => { const sc = E.SCHEMA && E.SCHEMA[id], p = {}; if (sc) for (const g of sc.groups) for (const it of g.items) p[it.k] = it.d; return p; };
const P = Object.fromEntries(['lx', 'mwr', 'bwt', 'bd', 'vo', 'so', 'b4'].map(k => [k, defaults(k)]));
P.bwt = Object.assign(P.bwt, { showDots:false, showAce:false, showB3:false, showDiv:false, mainSig:false });

// ② 讀日 K（全市場每日收盤檔）
const DAYS = path.join(ROOT, 'data', 'price', 'days');
const files = fs.readdirSync(DAYS).filter(f => /^\d{4}-\d{2}-\d{2}\.json$/.test(f)).sort();
const days = files.map(f => { try { return JSON.parse(fs.readFileSync(path.join(DAYS, f), 'utf8')); } catch (e){ return null; } }).filter(d => d && d.r);
const dates = days.map(d => d.d), LAST = dates[dates.length - 1];
const univ = (() => { try { return Object.fromEntries(JSON.parse(fs.readFileSync(path.join(ROOT, 'insider', 'universe.json'), 'utf8')).stocks.map(s => [s.code, s])); } catch (e){ return {}; } })();
const codes = [...new Set(days.slice(-3).flatMap(d => Object.keys(d.r)))].filter(c => /^[1-9]\d{3}$/.test(c)).sort();

// ③ 集保：每週大戶（400 張以上）比例、股東人數、千張大戶比例、總張數
function holders(c){
  const p = path.join(ROOT, 'data', 'holders', c + '.json'); if (!fs.existsSync(p)) return null;
  try { const w = JSON.parse(fs.readFileSync(p, 'utf8')).w; return Object.keys(w).sort().filter(k => Array.isArray(w[k]) && w[k].length === 32 && w[k][0] > 0).map(k => { const a = w[k], pc = a.slice(17).map(x => x / 1e4); return { d:k, ppl:a[0], lots:a[1] / 1000, b400:pc[11] + pc[12] + pc[13] + pc[14], b1k:pc[14] }; }); } catch (e){ return null; }
}
const addDays = (d, n) => { const t = new Date(d + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() + n); return t.toISOString().slice(0, 10); };

const WIN = 5;   // 技術訊號：最近 5 根內出現過就算
const NAMES = ['尋龍底', '月週共振', 'BotWT×3', '神奇波段', '量能情緒', '超級震盪', 'B4 超賣轉折', '籌碼收斂', '低檔燃料足'];
const isBot = t => /^底|^尋龍底|^龍/.test(t) || t.startsWith('底');
function signals(B, Hd){
  const n = B.n, D = Array.from({ length:9 }, () => new Int16Array(n).fill(-1));   // 每根：最近一次出現在幾根前（-1＝WIN 內沒有）
  const mark = (k, idx) => { for (const i of idx) for (let j = i; j < Math.min(n, i + WIN); j++) if (D[k][j] < 0 || D[k][j] > j - i) D[k][j] = j - i; };
  const sigIdx = (r, f) => !r || r.error || !r.sig ? [] : r.sig.filter(s => f(s.text)).map(s => s.i);
  let r;
  r = E.calcLX(B, P.lx); mark(0, sigIdx(r, isBot));
  r = E.calcMWR(B, P.mwr); const mI = sigIdx(r, t => t.startsWith('底')); if (r && r.R && r.LB) for (let i = 0; i < n; i++) if (r.R[i] < r.LB[i]) mI.push(i); mark(1, mI);
  { const v = E.BWT.wtCalc(Array.from(B.h), Array.from(B.l), Array.from(B.c), P.bwt); const idx = []; v.sig.forEach((s, i) => { if (s.bot || s.deep || s.aceBuy) idx.push(i); }); mark(2, idx); }
  r = E.calcBD(B, P.bd); mark(3, sigIdx(r, t => t.startsWith('底')));
  r = E.calcVO(B, P.vo); mark(4, sigIdx(r, t => t.startsWith('底')));
  r = E.calcSO(B, P.so); mark(5, sigIdx(r, t => t.startsWith('底')));
  r = E.calcB4(B, P.b4); if (r && r.dM){ const idx = []; for (let i = 2; i < n; i++){ let low = false; for (let j = Math.max(0, i - WIN + 1); j <= i; j++) if (r.dM[j] <= -80) low = true; if (low && r.dM[i] > r.dM[i - 1]) idx.push(i); } for (const i of idx) D[6][i] = 0; }
  // 籌碼與燃料（要有集保）
  const chip = new Array(n).fill(null);
  if (Hd && Hd.length){
    let w = -1;
    for (let i = 0; i < n; i++){
      while (w + 1 < Hd.length && addDays(Hd[w + 1].d, 3) <= B.d[i]) w++;   // 週資料公布後才用
      if (w < 4) continue;
      const h = Hd[w], h4 = Hd[w - 4], F = h.lots * (1 - h.b1k / 100);
      chip[i] = { b4:h.b400 - h4.b400, p4:(h.ppl / h4.ppl - 1) * 100, F };
      if (h.b400 > h4.b400 && h.ppl < h4.ppl) D[7][i] = 0;
      if (i >= 59 && F > 0){ let lo = i - 59; for (let j = i - 59; j <= i; j++) if (B.c[j] < B.c[lo]) lo = j; let cv = 0; for (let j = lo + 1; j <= i; j++) cv += B.v[j]; if (B.c[i] / B.c[lo] - 1 < 0.10 && cv / F < 0.5) D[8][i] = 0; }
    }
  }
  return { D, chip };
}

// ④ 逐檔計算＋回測（訊號後 20 個交易日）
const rows = [], bt = {}; const btAdd = (k, ret, mfe, mae, d) => { const b = bt[k] || (bt[k] = { n:0, s:0, w:0, up10:0, dn10:0, rs:[], h1:[0, 0], h2:[0, 0] }); b.n++; b.s += ret; if (ret > 0) b.w++; if (mfe >= .1) b.up10++; if (mae <= -.1) b.dn10++; if (b.rs.length < 20000) b.rs.push(ret); const h = d < '2026-05' ? b.h1 : b.h2; h[0]++; h[1] += ret; };
const base = { n:0, s:0 };
let done = 0;
for (const c of codes){
  const rr = []; for (const d of days){ const x = d.r[c]; if (x && x[3] && x[1] && x[2]) rr.push([d.d, x[0] || x[3], x[1], x[2], x[3], x[4] || 0]); }
  if (rr.length < 250 || rr[rr.length - 1][0] !== LAST) continue;
  const n = rr.length, F64 = Float64Array, B = { n, iv:'D', intra:false, d:rr.map(r => r[0]), o:F64.from(rr, r => r[1]), h:F64.from(rr, r => r[2]), l:F64.from(rr, r => r[3]), c:F64.from(rr, r => r[4]), v:F64.from(rr, r => r[5]), conf:new Uint8Array(n).fill(1) };
  B.vraw = B.v; B.t = F64.from(rr, r => Date.parse(r[0] + 'T00:00:00+08:00'));
  E.B = B; E.RAW = { id:c, rows:rr, sample:false }; E.R = {};
  let S; try { S = signals(B, holders(c)); } catch (e){ continue; }
  const v20 = B.v.slice(-20).reduce((a, b) => a + b, 0) / 20, C = B.c;
  // 回測：2024/8 以後，只算成交額 ≥ 3,000 萬
  for (let i = 60; i < n - 20; i++){
    const amt = B.v.slice(Math.max(0, i - 19), i + 1).reduce((a, b) => a + b, 0) / 20 * C[i] / 1e5; if (amt < 0.3) continue;
    const ret = C[i + 20] / C[i] - 1; let mx = -1, mn = 1; for (let j = i + 1; j <= i + 20; j++){ mx = Math.max(mx, B.h[j] / C[i] - 1); mn = Math.min(mn, B.l[j] / C[i] - 1); }
    const has = S.D.map(a => a[i] >= 0), tech = has.slice(0, 7).filter(Boolean).length, chipOk = S.chip[i] != null, all = has.filter(Boolean).length;
    base.n++; base.s += ret; btAdd('base', ret, mx, mn, B.d[i]);
    if (chipOk) btAdd('base9', ret, mx, mn, B.d[i]);
    if (tech >= 3) btAdd('t3', ret, mx, mn, B.d[i]); if (tech >= 4) btAdd('t4', ret, mx, mn, B.d[i]); if (tech >= 5) btAdd('t5', ret, mx, mn, B.d[i]);
    if (chipOk){ if (all >= 4) btAdd('a4', ret, mx, mn, B.d[i]); if (all >= 5) btAdd('a5', ret, mx, mn, B.d[i]); if (all >= 6) btAdd('a6', ret, mx, mn, B.d[i]); if (all >= 7) btAdd('a7', ret, mx, mn, B.d[i]);
      if (tech >= 3 && has[7]) btAdd('t3c', ret, mx, mn, B.d[i]); if (tech >= 4 && has[7]) btAdd('t4c', ret, mx, mn, B.d[i]); if (tech >= 5 && has[7]) btAdd('t5c', ret, mx, mn, B.d[i]); if (tech >= 4 && has[7] && has[8]) btAdd('t4cf', ret, mx, mn, B.d[i]); }
    for (let k = 0; k < 9; k++) if (has[k]) btAdd('k' + k, ret, mx, mn, B.d[i]);
  }
  const e = n - 1, ago = S.D.map(a => a[e]), cnt = ago.filter(x => x >= 0).length;
  if (cnt >= 3){
    const u = univ[c] || {}, ch = S.chip[e];
    rows.push([c, u.name || '', u.market || '', u.ind || '', C[e], Math.round((C[e] / C[e - 1] - 1) * 1e4) / 100, Math.round(v20), cnt, ago, ch ? Math.round(ch.b4 * 100) / 100 : null, ch ? Math.round(ch.p4 * 10) / 10 : null,
      Math.round((C[e] / Math.max(...B.h.slice(-60)) - 1) * 1000) / 10]);
  }
  if (++done % 300 === 0) console.log(done, c);
}
const sum = k => { const b = bt[k]; if (!b) return null; const rs = b.rs.slice().sort((x, y) => x - y); return [b.n, +(b.s / b.n * 100).toFixed(2), +(rs[Math.floor(rs.length / 2)] * 100).toFixed(2), Math.round(b.w / b.n * 100), Math.round(b.up10 / b.n * 100), Math.round(b.dn10 / b.n * 100), b.h1[0] ? +(b.h1[1] / b.h1[0] * 100).toFixed(2) : null, b.h2[0] ? +(b.h2[1] / b.h2[0] * 100).toFixed(2) : null]; };
const BT = {}; for (const k of Object.keys(bt)) BT[k] = sum(k);
rows.sort((a, b) => b[7] - a[7] || a[11] - b[11]);
const out = { updated:new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16), latest:LAST, from:dates[0], win:WIN, names:NAMES,
  cols:['code', 'name', 'market', 'ind', 'c', 'chg', 'v20', 'cnt', 'ago', 'b4', 'p4', 'ddHi'],
  btCols:['次數', '平均報酬', '中位數', '上漲比例', '曾漲 10% 以上', '曾跌 10% 以上', '2026/5 以前平均', '2026/5 以後平均'], bt:BT, rows };
fs.writeFileSync(path.join(ROOT, 'data', 'price', 'jlmj.json'), JSON.stringify(out));
console.log('jlmj', rows.length, 'stocks', JSON.stringify(BT));
