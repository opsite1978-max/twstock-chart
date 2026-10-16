// 讓看盤頁可以「安裝成 App」：外殼檔案離線可開，股價資料一律即時向 FinMind 取得
const CACHE = 'twchart-v62';
const SHELL = ['./', './index.html', './mobile.html', './holders.html', './insider.html', './txo.html', './strategy.html', './intraday.html', './futstock.html', './futscreen.html', './ctrl.html', './manifest.webmanifest', './manifest-m.webmanifest', './icon-192.png', './icon-512.png'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.hostname.includes('finmindtrade')) return;   // 資料不快取
  const cacheable = u.origin === location.origin || u.hostname.includes('jsdelivr') || u.hostname.includes('unpkg');
  e.respondWith(
    // 先連網取最新版（同網站的頁面略過瀏覽器快取，改版後立刻生效），斷線才用快取
    fetch(e.request, u.origin === location.origin ? { cache: 'no-cache' } : {}).then(r => {
      if (r.ok && cacheable){ const cp = r.clone(); caches.open(CACHE).then(c => c.put(e.request, cp)); }
      return r;
    }).catch(() => caches.match(e.request))
  );
});
