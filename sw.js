// 讓看盤頁可以「安裝成 App」：外殼檔案離線可開，股價資料一律即時向 FinMind 取得
const CACHE = 'twchart-v242';
const SHELL = ['./', './index.html', './mobile.html', './holders.html', './insider.html', './txo.html', './strategy.html', './futstock.html', './futscreen.html', './ctrl.html', './force.html', './flowchart.html', './force-lab.html', './optsim.html', './optkelly.html', './iv.html', './overview.html', './optlive.html', './optk.html', './optgo.html', './optmap.html', './clear.html', './bbview.html', './margin.html', './zhanshen.html', './zhanshen-me.html', './pro/', './manifest-pro.webmanifest', './manifest-me.webmanifest', './manifest-zs.webmanifest', './icon-180.png', './calc.html', './vwscreen.html', './bbscreen.html', './lxscreen.html', './xdscreen.html', './qzscreen.html', './bdscreen.html', './jgscreen.html', './fxpos.html', './autoreload.js', './manifest.webmanifest', './manifest-m.webmanifest', './icon-192.png', './icon-512.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
// 只清自己的舊快取：同一個 github.io 底下的 zhanshen 網站快取不能被這裡刪掉
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k.startsWith('twchart-') && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.hostname.includes('finmindtrade') || u.hostname.includes('cnyes') || u.hostname.includes('githubusercontent')) return;   // 資料不快取
  const cacheable = u.origin === location.origin || u.hostname.includes('jsdelivr') || u.hostname.includes('unpkg');
  e.respondWith(
    // 先連網取最新版（同網站的頁面略過瀏覽器快取，改版後立刻生效），斷線才用快取
    fetch(e.request, u.origin === location.origin ? { cache: 'no-cache' } : {}).then(r => {
      if (r.ok && cacheable){ const cp = r.clone(); caches.open(CACHE).then(c => c.put(e.request, cp)); }
      return r;
    }).catch(() => caches.match(e.request))
  );
});
