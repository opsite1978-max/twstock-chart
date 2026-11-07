// 選擇權勝率・凱利・主力佈局：每天收盤後在 GitHub 上跑一次，結果存到 data/txo/kelly.json
// 計算方式與 optkelly.html 完全相同：直接載入網頁裡的程式來算（只把畫圖的部分換成空的），兩邊不會算得不一樣
// FinMind 憑證從環境變數 FINMIND_TOKEN 讀取（GitHub 加密密碼），不會印出或存檔
'use strict';
const fs = require('fs'), path = require('path'), vm = require('vm');
const ROOT = path.join(__dirname, '..');
const TOKEN = (process.env.FINMIND_TOKEN || '').trim();

const html = fs.readFileSync(path.join(ROOT, 'optkelly.html'), 'utf8');
const code = html.split('<script>\n\'use strict\';')[1].split('</script>')[0].replace(/\nstart\(\);\s*$/, '\n');

// 假的網頁元件：記下每個區塊最後寫進去的內容
const boxes = {};
const el = sel => boxes[sel] || (boxes[sel] = new Proxy({ innerHTML:'', textContent:'', value:'', style:{}, dataset:{}, hidden:false,
  querySelector:() => null, querySelectorAll:() => [], appendChild(){}, setAttribute(){}, addEventListener(){}, clientWidth:400 }, { set(t, k, v){ t[k] = v; return true; } }));
const ctx = {
  console, fetch, URLSearchParams, Response, setTimeout, clearTimeout, Math, Date, JSON, Map, Set, Promise,
  Float64Array, Array, Number, String, Object, RegExp, Error, isNaN, Infinity,
  localStorage:{ getItem:() => null, setItem(){} },
  getComputedStyle:() => ({ getPropertyValue:() => '' }),
  requestAnimationFrame:() => 0,
  window:{ addEventListener(){} },
  document:{ documentElement:{ dataset:{} }, querySelector:el, querySelectorAll:() => [], createElement:() => el('_tmp'), createElementNS:() => el('_tmp'), head:{ appendChild(){} } },
};
ctx.globalThis = ctx;
vm.createContext(ctx);

const strip = h => String(h || '').replace(/<br\s*\/?>/g, '\n').replace(/<\/(li|div|tr)>/g, '\n').replace(/<\/t[dh]>/g, '\t').replace(/<[^>]+>/g, '')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&amp;/g, '&').replace(/[ \t]+\n/g, '\n').replace(/\n{2,}/g, '\n').trim();

const runner = `
;(function(){
  renderChips = () => {}; renderStk = () => {}; renderBook = () => {};
  globalThis.__RUN = async function(token, hist, chips){
    SET.token = token;
    HISTD = hist; prepHist(HISTD); CHIPS = chips;
    TD = await tradingDates();
    let date = null, err = null;
    for (let i = TD.length - 1; i >= Math.max(0, TD.length - 4); i--){
      try { await loadDate(TD[i]); date = TD[i]; break; } catch(e){ err = e; if (!/還沒有/.test(e.message)) throw e; }
    }
    if (!date) throw err || new Error('找不到資料');
    await new Promise(r => setTimeout(r, 50));
    await loadInst(date); renderMain();
    return date;
  };
  globalThis.__RECALC = function(model){ MODEL = model; compute(); CUR = 'ALL'; renderAll(); renderMain(); };
  globalThis.__STATE = () => ({ date:DATA.date, fut:DATA.fut, exps:DATA.exps.map(e => ({ code:e.code, exp:e.exp, n:e.n, C:Math.round(e.C), atmIv:+(e.atmIv * 100).toFixed(1), oi:e.oiC + e.oiP, maxPain:maxPain(e), walls:walls(e) })), rets:RETS.length, rv20:RV20 });
  globalThis.__GROUPS = function(){
    const out = {};
    for (const key of ['ALL', ...DATA.exps.slice(0, 4).map(e => e.code)]){
      CUR = key; const L = listOf(), o = {};
      for (const [sd, role, nm] of [['c','buy','買權買方'],['c','sell','買權賣方'],['p','buy','賣權買方'],['p','sell','賣權賣方']]){
        const g = group(L, sd, role, x => x.oi), n = DATA.hasPrev ? group(L, sd, role, x => x.prev == null ? 0 : x.oi - x.prev) : null;
        const r = g => g && { lots:g.lots, p:+g.p.toFixed(4), b:g.b == null ? null : +g.b.toFixed(3), kelly:+g.k.toFixed(4), W:Math.round(g.W), L:Math.round(g.L), ev:Math.round(g.ev) };
        o[nm] = { all:r(g), today:r(n) };
      }
      out[key] = o;
    }
    CUR = 'ALL';
    return out;
  };
})();`;

(async () => {
  if (!TOKEN) console.log('沒有 FINMIND_TOKEN，改用免費額度試試');
  vm.runInContext(code + runner, ctx, { filename:'optkelly.js' });
  const hist = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/txo/history.json'), 'utf8')).days || [];
  let chips = [];
  try { chips = JSON.parse(fs.readFileSync(path.join(ROOT, 'data/txo/chips.json'), 'utf8')).days || []; } catch(e){}
  const date = await ctx.__RUN(TOKEN, hist, chips);
  const res = { updated:new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 16).replace('T', ' '), date, state:ctx.__STATE(), models:{} };
  for (const m of ['hist', 'rv', 'iv']){
    ctx.__RECALC(m);
    res.models[m] = { groups:ctx.__GROUPS(), flow:strip(el('#flow').innerHTML), main:strip(el('#mainBox').innerHTML), who:strip(el('#whoBox').innerHTML),
      book:strip(el('#bookK').innerHTML), bookNote:strip(el('#bookNote').innerHTML) };
  }
  res.inst = strip(el('#instTbl').innerHTML); res.big = strip(el('#bigTbl').innerHTML);
  fs.mkdirSync(path.join(ROOT, 'data/txo'), { recursive:true });
  fs.writeFileSync(path.join(ROOT, 'data/txo/kelly.json'), JSON.stringify(res, null, 1));
  console.log('ok', date, res.models.hist.main.slice(0, 200));
})().catch(e => { console.error('失敗：' + String(e && e.message || e).replace(TOKEN || '###', '***')); process.exit(1); });
