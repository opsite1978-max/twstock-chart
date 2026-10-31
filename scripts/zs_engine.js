// 把戰神看盤 zhanshen.html 裡的指標函式原封不動拿出來在 Node 跑（選股和看盤用同一份程式碼，不會算得不一樣）
const fs = require('fs'), path = require('path');
let acorn; try { acorn = require('acorn'); } catch (e){ acorn = require(path.join(process.cwd(), 'node_modules', 'acorn')); }
function load(htmlPath, want){
  const html = fs.readFileSync(htmlPath, 'utf8');
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]);
  const src = scripts.reduce((a, b) => b.length > a.length ? b : a, '');
  const ast = acorn.parse(src, { ecmaVersion:'latest', sourceType:'script' });
  const decls = [], attach = [];          // {names, code, idx}
  ast.body.forEach((n, idx) => {
    let names = [];
    if (n.type === 'FunctionDeclaration') names = [n.id.name];
    else if (n.type === 'VariableDeclaration') for (const d of n.declarations){ if (d.id.type === 'Identifier') names.push(d.id.name); }
    else if (n.type === 'ClassDeclaration') names = [n.id.name];
    if (names.length) decls.push({ names, code:src.slice(n.start, n.end), idx });
    else if (n.type === 'ExpressionStatement' && n.expression.type === 'AssignmentExpression' && n.expression.left.type === 'MemberExpression' && n.expression.left.object.type === 'Identifier')
      attach.push({ owner:n.expression.left.object.name, code:src.slice(n.start, n.end), idx });
  });
  const byName = new Map(); for (const d of decls) for (const nm of d.names) byName.set(nm, d);
  const need = new Set(), q = [...want];
  const SKIP = new Set(['UI', 'LS', 'PAGE', 'RAW', 'B', 'R', 'view', 'CC', 'cv', 'ctx']);   // 介面狀態不需要
  while (q.length){
    const nm = q.pop(); const d = byName.get(nm); if (!d || need.has(d)) continue; need.add(d);
    for (const a of attach) if (d.names.includes(a.owner) && !need.has(a)){ need.add(a); for (const id of new Set(a.code.match(/[A-Za-z_$][\w$]*/g))) if (byName.has(id) && !SKIP.has(id) && !need.has(byName.get(id))) q.push(id); }
    const ids = new Set(d.code.match(/[A-Za-z_$][\w$]*/g));
    for (const id of ids) if (byName.has(id) && !SKIP.has(id) && !need.has(byName.get(id))) q.push(id);
  }
  const code = [...need].sort((a, b) => a.idx - b.idx).map(d => d.code).join('\n');
  const pre = `const F = Float64Array; const UI = {}; const CC = new Proxy({}, { get:() => "#888888" }); const PAGE = { key:"zs", name:"" }; const LS = { get:(k, d) => d, set(){} };
const R = {}, view = {}; let B = null, RAW = null; const ctx = new Proxy({}, { get:(t, k) => k === "measureText" ? () => ({ width:10 }) : () => {}, set:() => true }), cv = {};
const document = new Proxy({}, { get:() => () => null }), localStorage = { getItem:() => null, setItem(){} }, window = globalThis, location = { search:"", hash:"", href:"" };
`;
  const fn = new Function(pre + code.replace(/^const F = Float64Array;?/m, '') + '\nreturn {' + want.filter(w => byName.has(w)).join(',') + ', __setB:x => { B = x; }, __setRAW:x => { RAW = x; }, __CAPS:(typeof CAPS !== "undefined" ? CAPS : null) };');
  return fn();
}
module.exports = { load };
