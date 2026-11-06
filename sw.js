/* ============================================================
   PARTNERTYPE Service Worker
   Network-first: every request goes to the network, and a copy of plain
   static files is kept so the app shell still opens offline.
   ============================================================ */

// Bump this name whenever the caching rules change: activation deletes every
// cache with a different name, so entries stored under older rules are dropped.
const CACHE_NAME  = 'partnertype-v3';
const STATIC_URLS = [
  '/',
  '/index.html',
  '/css/main.css',
  '/css/dashboard.css',
  '/css/components.css',
  '/css/notifications.css',
  '/css/comments.css',
  '/js/api.js',
  '/locales.js',
  '/js/app.js',
  '/js/charts.js',
  '/js/notifications.js',
  '/js/comments.js',
];

// Install — pre-cache static files
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_URLS))
      .then(() => self.skipWaiting())
  );
});

// Activate — clean old caches
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Whether a request/response pair may be written to Cache Storage. Only plain
// same-origin static files qualify. Anything with a query string is refused:
// links such as set-password.html?token=..., index.html?oauth=... and
// consent.html?req=... carry one-time secrets, and Cache.put ignores the
// server's Cache-Control, so the worker itself has to keep them out.
function isCacheable(request, response) {
  if (request.method !== 'GET') return false;
  if (!response || !response.ok || response.type !== 'basic') return false;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return false;
  if (url.search || url.hash) return false;
  if (url.pathname.startsWith('/api/')) return false;
  if (url.pathname.startsWith('/oauth/') || url.pathname.startsWith('/.well-known/')) return false;
  if (url.pathname === '/mcp' || url.pathname === '/health') return false;
  const cacheControl = response.headers.get('Cache-Control') || '';
  if (/no-store|private/i.test(cacheControl)) return false;
  return true;
}

// Fetch — network first; fall back to the cached copy only when offline.
self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request).then(response => {
      if (isCacheable(event.request, response)) {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
      }
      return response;
    }).catch(() => {
      // A page opened from a link with a query string is stored without it,
      // so an offline navigation looks the page up by its path alone.
      const options = event.request.mode === 'navigate' ? { ignoreSearch: true } : undefined;
      return caches.match(event.request, options);
    })
  );
});
