/* 自動更新（每天公布一次資料的頁面）：
   過了資料公布時間（08:30、15:30、17:30、21:30）後，回到這個分頁就自動重新整理；正在看的話，右下角出現「有新資料」按鈕，按了才更新，不會打斷操作 */
(function(){
  const CP = [510, 930, 1050, 1290], t0 = Date.now();
  function passed(){
    const d0 = Math.floor((t0 + 8 * 3600e3) / 864e5), d1 = Math.floor((Date.now() + 8 * 3600e3) / 864e5);
    for (let d = d0; d <= d1; d++) for (const m of CP){ const t = d * 864e5 + m * 60e3 - 8 * 3600e3; if (t > t0 && t <= Date.now()) return true; }
    return false;
  }
  let shown = false;
  function banner(){
    if (shown || !document.body) return; shown = true;
    const b = document.createElement('button');
    b.textContent = '🔄 有新資料，點這裡更新';
    b.style.cssText = 'position:fixed;right:14px;bottom:14px;z-index:9999;padding:9px 14px;border-radius:20px;border:0;background:#3b82f6;color:#fff;font-size:14px;box-shadow:0 2px 10px rgba(0,0,0,.35);cursor:pointer';
    b.onclick = () => location.reload();
    document.body.appendChild(b);
  }
  document.addEventListener('visibilitychange', () => { if (!document.hidden && passed()) location.reload(); });
  setInterval(() => { if (passed() && !document.hidden) banner(); }, 60e3);
})();
