const CACHE = 'mesada-leo-v23';
const ARQUIVOS = ['./', './index.html', './manifest.json', './icon-192.png', './icon-512.png', './apple-touch-icon.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ARQUIVOS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

// cache-first: funciona offline; rede só quando não tem no cache
self.addEventListener('fetch', e => {
  e.respondWith(
    caches.match(e.request).then(hit => hit || fetch(e.request).then(resp => {
      const u = e.request.url; if (e.request.method === 'GET' && (resp.ok || resp.type === 'opaque') && (new URL(u).origin === location.origin || u.includes('cdn.jsdelivr.net'))) {
        const cp = resp.clone();
        caches.open(CACHE).then(c => c.put(e.request, cp));
      }
      return resp;
    }).catch(() => caches.match('./index.html')))
  );
});
