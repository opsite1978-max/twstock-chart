#!/usr/bin/env node
/*
 神奇波段選股：全市場 60 分、4 小時、日、週、月 K，找「短波線（綠色＝在訊號線下面）碰到動態下界」的股票。
 指標程式直接從 zhanshen.html 取出來跑（scripts/zs_engine.js），和戰神看盤一樣。
 資料：日／週／月＝data/price/days＋data/price/long（證交所、櫃買中心、不用 FinMind）；
       60 分、4 小時＝Yahoo Finance 60 分 K（一檔一次查詢、不用 FinMind），13:30 收盤那筆併進 13:00 那根，和看盤頁的切法一樣。
   node scripts/bd_scan.js   → data/price/bd.json
*/
const fs = require('fs'), path = require('path');
const ROOT = path.join(__dirname, '..');
const DAYS = path.join(ROOT, 'data', 'price', 'days'), LONG = path.join(ROOT, 'data', 'price', 'long');
const OUT = path.join(ROOT, 'data', 'price', 'bd.json');
const LOOK = 5;           // 最近 5 根內碰過下界也記下來
const sleep = ms => new Promise(r => setTimeout(r, ms));
const monday = d => { const x = new Date(d + 'T00:00:00Z'); x.setUTCDate(x.getUTCDate() - ((x.getUTCDay() + 6) % 7)); return x.toISOString().slice(0, 10); };

async function yahoo(code, market){
  const sym = code + (market === '上櫃' ? '.TWO' : '.TW');
  for (let k = 0; k < 3; k++){
    try {
      const r = await fetch('https://query1.finance.yahoo.com/v8/finance/chart/' + sym + '?interval=60m&range=1y', { headers:{ 'User-Agent':'Mozilla/5.0' } });
      if (r.status === 429){ await sleep(5000 * (k + 1)); continue; }
      const j = await r.json(); const x = j && j.chart && j.chart.result && j.chart.result[0];
      if (!x || !x.timestamp) return null;
      const q = x.indicators.quote[0], out = [];
      x.timestamp.forEach((t, i) => {
        if (q.close[i] == null || q.high[i] == null) return;
        const s = new Date((t + 8 * 3600) * 1000).toISOString(), d = s.slice(0, 10), hm = s.slice(11, 16);
        const row = [d, hm, q.open[i] ?? q.close[i], q.high[i], q.low[i], q.close[i]];
        const last = out[out.length - 1];
        if (hm >= '13:30' && last && last[0] === d && last[1] === '13:00'){ last[3] = Math.max(last[3], row[3]); last[4] = Math.min(last[4], row[4]); last[5] = row[5]; return; }   // 收盤競價併進 13:00
        if (hm < '09:00' || hm > '13:00') return;
        out.push(row);
      });
      return out;
    } catch (e){ await sleep(2000 * (k + 1)); }
  }
  return null;
}
const r2 = x => Math.round(x * 10) / 10;
function scan(M, P, iv, d, o, h, l, c){
  const n = c.length; if (n < 80) return null; const F = Float64Array;
  const B = { n, iv, intra:iv === '60' || iv === '240', d, o:F.from(o), h:F.from(h), l:F.from(l), c:F.from(c), v:new F(n), vraw:new F(n), t:new F(n), conf:new Uint8Array(n).fill(1) };
  M.__setB(B); let r; try { r = M.calcBD(B, P); } catch (e){ return null; }
  const K = r.K, Sg = r.Sg, LB = r.LB, UB = r.UB;
  if (isNaN(LB[n - 1]) || isNaN(K[n - 1])) return null;
  let touch = null; for (let j = n - 1; j >= Math.max(0, n - LOOK); j--) if (K[j] <= LB[j] && K[j] < Sg[j]){ touch = n - 1 - j; break; }
  const bots = r.sig.filter(s => String(s.text).startsWith('底')).map(s => n - 1 - s.i).filter(a => a < LOOK);
  return [r2(K[n - 1]), r2(LB[n - 1]), r2(UB[n - 1]), K[n - 1] < Sg[n - 1] ? 1 : 0, touch, bots.length ? Math.min(...bots) : null, d[n - 1]];
}
(async () => {
  const t0 = Date.now();
  const E = require('./zs_engine.js');
  const M = E.load(path.join(ROOT, 'zhanshen.html'), ['calcBD', 'SCHEMA', 'defaults']); const P = M.defaults('bd');
  let info = {}; try { for (const s of JSON.parse(fs.readFileSync(path.join(ROOT, 'insider', 'universe.json'), 'utf8')).stocks) info[s.code] = s; } catch (e){}
  const files = fs.readdirSync(DAYS).filter(f => /^\d{4}-\d\d-\d\d\.json$/.test(f)).sort(), ser = {};
  for (const f of files){ const d = JSON.parse(fs.readFileSync(path.join(DAYS, f), 'utf8')); for (const [c, v] of Object.entries(d.r)) (ser[c] = ser[c] || []).push([d.d, v]); }
  const latest = files[files.length - 1].slice(0, 10);
  const codes = Object.keys(ser).filter(c => c.length === 4 && c[0] !== '0' && info[c] && ser[c][ser[c].length - 1][0] === latest).sort();
  // Yahoo 60 分 K（8 條同時抓）
  const Y = {}; let yok = 0, ybad = 0;
  if (process.env.NO_INTRA !== '1') await Promise.all([...Array(8).keys()].map(async w => { for (let k = w; k < codes.length; k += 8){ const y = await yahoo(codes[k], info[codes[k]].market); if (y && y.length) { Y[codes[k]] = y; yok++; } else ybad++; } }));
  let intraLatest = '';
  const rows = [];
  for (const code of codes){
    const rowsD = ser[code].filter(([, r]) => r && r[3] && r[1] && r[2]);
    let dd = rowsD.map(x => x[0]), o = rowsD.map(x => x[1][0] || x[1][3]), h = rowsD.map(x => x[1][1]), l = rowsD.map(x => x[1][2]), c = rowsD.map(x => x[1][3]);
    const y = Y[code], res = { h1:null, h4:null };
    if (y){
      const last = y[y.length - 1]; if (last[0] + ' ' + last[1] > intraLatest) intraLatest = last[0] + ' ' + last[1];
      res.h1 = scan(M, P, '60', y.map(r => r[0] + ' ' + r[1]), y.map(r => r[2]), y.map(r => r[3]), y.map(r => r[4]), y.map(r => r[5]));
      const g = [];   // 4 小時：09:00–13:00 一根、13:00–13:30 一根
      for (const r of y){ const k = r[0] + (r[1] < '13:00' ? ' 09:00' : ' 13:00'); const b = g[g.length - 1];
        if (b && b[0] === k){ b[2] = Math.max(b[2], r[3]); b[3] = Math.min(b[3], r[4]); b[4] = r[5]; } else g.push([k, r[2], r[3], r[4], r[5]]); }
      res.h4 = scan(M, P, '240', g.map(b => b[0]), g.map(b => b[1]), g.map(b => b[2]), g.map(b => b[3]), g.map(b => b[4]));
      // 盤中：今天的日 K 還沒進每日行情檔，就用 60 分 K 先組出今天這根
      const today = last[0];
      if (today > dd[dd.length - 1]){ const t = y.filter(r => r[0] === today);
        dd = dd.concat([today]); o = o.concat([t[0][2]]); h = h.concat([Math.max(...t.map(r => r[3]))]); l = l.concat([Math.min(...t.map(r => r[4]))]); c = c.concat([t[t.length - 1][5]]); }
    }
    res.d = scan(M, P, 'D', dd, o, h, l, c);
    let LP = null; const p = path.join(LONG, code + '.json'); if (fs.existsSync(p)){ try { LP = JSON.parse(fs.readFileSync(p, 'utf8')); } catch (e){} }
    for (const [t, keyf] of [['w', monday], ['m', x => x.slice(0, 7)]]){
      let K = [], O = [], Hh = [], Ll = [], Cc = [];
      if (LP && LP[t] && LP[t + 'h'] && LP[t + 'h'].length === LP[t].length){ K = LP[t + 'k'].slice(); O = LP[t + 'o'].slice(); Hh = LP[t + 'h'].slice(); Ll = LP[t + 'l'].slice(); Cc = LP[t].slice(); }
      for (let i = 0; i < dd.length; i++){ const k = keyf(dd[i]); const j = K.length - 1;
        if (j >= 0 && K[j] === k){ Hh[j] = Math.max(Hh[j], h[i]); Ll[j] = Math.min(Ll[j], l[i]); Cc[j] = c[i]; }
        else if (j < 0 || k > K[j]){ K.push(k); O.push(o[i]); Hh.push(h[i]); Ll.push(l[i]); Cc.push(c[i]); } }
      res[t] = scan(M, P, t.toUpperCase(), K, O, Hh, Ll, Cc);
    }
    if (!Object.values(res).some(x => x && (x[4] != null || x[5] != null))) continue;    // 五個週期都沒碰過下界、也沒出底 → 不列
    const n = c.length, V = rowsD.map(x => x[1][4] || 0);
    const u = info[code];
    rows.push([code, u.name || '', u.market || '', u.ind || '', c[n - 1], n > 1 ? Math.round((c[n - 1] / c[n - 2] - 1) * 1e4) / 100 : 0,
      Math.round(V.slice(-20).reduce((s, x) => s + x, 0) / Math.min(20, V.length)), res.h1, res.h4, res.d, res.w, res.m]);
  }
  const now = new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16) + '+08:00';
  fs.writeFileSync(OUT, JSON.stringify({ updated:now, latest, intraLatest, look:LOOK, yahoo:{ ok:yok, bad:ybad },
    cols:['code', 'name', 'market', 'ind', 'c', 'chg', 'v20', 'h1', 'h4', 'd', 'w', 'm'], tcols:['K', 'LB', 'UB', 'green', 'touch', 'bot', 'last'], rows }));
  const cnt = k => rows.filter(r => r[k] && r[k][4] === 0).length;
  console.log('bd', rows.length, 'stocks; 這根碰下界 60/240/D/W/M', cnt(7), cnt(8), cnt(9), cnt(10), cnt(11), 'yahoo', yok, ybad, Math.round((Date.now() - t0) / 1000) + 's');
  process.exit(0);
})();
