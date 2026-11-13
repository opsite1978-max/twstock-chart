// 離線快取：第一次開啟後，沒有網路也能使用。更新內容時請把版本號加 1。
const CACHE = "dm-adjust-v1";
const FILES = ["./", "index.html", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png"];
const FONT_HOSTS = ["fonts.googleapis.com", "fonts.gstatic.com"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k.startsWith("dm-adjust-") && k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
// 先用網路取得最新版，沒網路時改用快取
self.addEventListener("fetch", e => {
  const u = new URL(e.request.url);
  if (e.request.method !== "GET" || (u.origin !== location.origin && !FONT_HOSTS.includes(u.hostname))) return;
  e.respondWith(
    fetch(e.request).then(r => { const copy = r.clone(); caches.open(CACHE).then(c => c.put(e.request, copy)); return r; })
      .catch(() => caches.match(e.request).then(r => r || (u.origin === location.origin ? caches.match("index.html") : Response.error())))
  );
});
