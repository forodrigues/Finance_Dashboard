{% load static %}// Service worker: makes the app installable and keeps static files (CSS, JS, fonts, icons) on the phone.
// Pages with data always come from the network, so every device sees the same, current data.
// The cache name changes whenever our own static files change, which drops the old copies.
const CACHE = 'finance-static-{% static "finance/app.css" %}{% static "finance/charts.js" %}{% static "finance/auth.js" %}';

// Only immutable URLs are cached: CDN files pinned to a version, and our files with a content hash in the name
// (production). Unhashed files (local development) always come from the network.
function isCacheable(url) {
  if (url.hostname === 'cdn.jsdelivr.net') return true;
  return url.origin === self.location.origin && /^\/static\/.+\.[0-9a-f]{12}\.\w+$/.test(url.pathname);
}

self.addEventListener('install', () => self.skipWaiting());

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET' || !isCacheable(new URL(request.url))) return;
  event.respondWith(
    caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match(request);
      if (cached) return cached;
      const response = await fetch(request);
      if (response.ok) cache.put(request, response.clone());
      return response;
    })
  );
});
