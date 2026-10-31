// 測試：Yahoo 60 分 K 能不能從 GitHub 抓、和 FinMind 一分 K 組出來的 60 分 K 是否一樣（只用 2 次 FinMind 查詢）
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function yh(sym, range){
  const u = 'https://query1.finance.yahoo.com/v8/finance/chart/' + sym + '?interval=60m&range=' + range;
  const r = await fetch(u, { headers:{ 'User-Agent':'Mozilla/5.0' } }); const j = await r.json().catch(() => null);
  if (!r.ok || !j || !j.chart || !j.chart.result) return { err:r.status + ' ' + JSON.stringify(j && j.chart && j.chart.error) };
  const x = j.chart.result[0], q = x.indicators.quote[0];
  return { n:x.timestamp.length, rows:x.timestamp.map((t, i) => [new Date((t + 8 * 3600) * 1000).toISOString().slice(0, 16), q.open[i], q.high[i], q.low[i], q.close[i], q.volume[i]]) };
}
(async () => {
  for (const s of ['2330.TW', '6488.TWO', '8121.TWO', '3006.TW']){ const t0 = Date.now(); const y = await yh(s, '1y'); console.log(s, y.err || ('bars ' + y.n), (Date.now() - t0) + 'ms', y.rows ? JSON.stringify(y.rows.slice(-7)) : ''); }
  // 和 FinMind 比對最後一個交易日
  const y = await yh('2330.TW', '5d'); const last = y.rows[y.rows.length - 1][0].slice(0, 10);
  const tok = process.env.FINMIND_TOKEN;
  const r = await fetch('https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockKBar&data_id=2330&start_date=' + last + '&token=' + tok); const j = await r.json();
  const mins = (j.data || []).map(d => [d.minute, d.open, d.high, d.low, d.close, d.volume]);
  const bars = {}; for (const [m, o, h, l, c] of mins){ const [hh, mm] = m.split(':').map(Number); const t = hh * 60 + mm; if (t < 540 || t > 810) continue; const k = Math.min(Math.floor((Math.min(t, 809) - 540) / 60), 4); const b = bars[k]; if (!b) bars[k] = [o, h, l, c]; else { b[1] = Math.max(b[1], h); b[2] = Math.min(b[2], l); b[3] = c; } }
  console.log('FinMind 60K', last, JSON.stringify(Object.values(bars)));
  console.log('Yahoo   60K', last, JSON.stringify(y.rows.filter(r => r[0].startsWith(last)).map(r => r.slice(1, 5).map(v => v && +v.toFixed(2)))));
  // 速度：50 檔
  const t0 = Date.now(); let ok = 0, bad = 0; const codes = ['1101','1102','1216','1301','1303','2002','2105','2207','2301','2303','2308','2317','2327','2357','2379','2382','2395','2408','2412','2454','2603','2609','2615','2880','2881','2882','2884','2885','2886','2891','2892','3008','3034','3037','3045','3231','3711','4904','5871','5880','6505','6669','2345','3017','2368','3443','6415','3661','2360','1590'];
  await Promise.all([0,1,2,3].map(async w => { for (let k = w; k < codes.length; k += 4){ const y = await yh(codes[k] + '.TW', '1y'); if (y.err) { bad++; console.log('ERR', codes[k], y.err); } else ok++; } }));
  console.log('50 檔', ok, 'ok', bad, 'bad', (Date.now() - t0) / 1000 + 's');
})();
