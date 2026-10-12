// 股期選股：用網頁本身的分析（futstock.html）逐檔算出進場燈號，存成 data/futscreen/latest.json
// 在 GitHub Actions 裡執行：先用 python3 -m http.server 在本機提供網頁，再用 Playwright 打開
const { chromium } = require('playwright');
const fs = require('fs'), path = require('path');
const tok = process.env.FINMIND_TOKEN || '';
const scrub = s => tok ? String(s).split(tok).join('***') : String(s);
const sleep = ms => new Promise(r => setTimeout(r, ms));
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport:{ width:1280, height:900 }, serviceWorkers:'block' });
  const pg = await ctx.newPage();
  await pg.addInitScript(t => { try { localStorage.setItem('twchart.settings', JSON.stringify({ token:t })); } catch(e){} }, tok);
  await pg.goto('http://localhost:8080/futstock.html', { waitUntil:'domcontentloaded' });
  await pg.waitForFunction(() => window.__fsList);
  const list = await pg.evaluate(() => window.__fsList());
  console.log('股期標的', list.length);
  const rows = [], t0 = Date.now();
  for (const [k, it] of list.entries()){
    let r = null;
    for (let tryN = 0; tryN < 4; tryN++){
      r = await Promise.race([pg.evaluate(id => window.__fsAnalyze(id), it.id), sleep(90000).then(() => ({ err:'逾時' }))]).catch(e => ({ err:String(e).slice(0, 200) }));
      if (r && r.err && /次數已用完/.test(r.err)){ console.log('查詢次數用完，等 6 分鐘'); await sleep(360000); continue; }
      if (r && r.err === '逾時'){ await pg.reload({ waitUntil:'domcontentloaded' }); await pg.waitForFunction(() => window.__fsList); continue; }
      break;
    }
    rows.push(r && !r.err ? r : { id:it.id, name:it.name, fut:it.fut, err:scrub(r && r.err) });
    if (k % 20 === 0) console.log(k + 1, '/', list.length, it.id, r && (r.err ? scrub(r.err) : r.sig + ' ' + r.score), Math.round((Date.now() - t0) / 1000) + 's');
  }
  const tw = new Date(Date.now() + 8 * 3600e3).toISOString().replace('T', ' ').slice(0, 16);
  const out = path.join(__dirname, '..', 'data', 'futscreen', 'latest.json');
  fs.writeFileSync(out, JSON.stringify({ updated:tw, n:rows.length, rows }));
  console.log('完成', rows.length, '可進場', rows.filter(r => r.sig === 'go').length, '觀望', rows.filter(r => r.sig === 'wait').length, '避開', rows.filter(r => r.sig === 'avoid').length, '失敗', rows.filter(r => r.err).length);
  await b.close();
})();
