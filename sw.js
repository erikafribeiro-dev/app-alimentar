// Diário Alimentar — service worker
// Estratégia: REDE PRIMEIRO (sempre busca a versão nova do app); cache só como reserva offline.
// Também apaga qualquer cache antigo (inclusive de outros apps publicados neste mesmo domínio).
const CACHE = 'diario-alimentar-v2.2';

self.addEventListener('install', e => { self.skipWaiting(); });

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  const url = new URL(e.request.url);
  // Nunca interceptar chamadas ao Supabase ou a outros domínios
  if (url.origin !== location.origin) return;
  e.respondWith(
    fetch(e.request).then(resp => {
      if (resp.ok) { const cp = resp.clone(); caches.open(CACHE).then(c => c.put(e.request, cp)); }
      return resp;
    }).catch(() => caches.match(e.request).then(hit => hit || caches.match('./index.html')))
  );
});
