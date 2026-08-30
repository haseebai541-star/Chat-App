// Basic service worker - PWA installable hone ke liye zaroori hai
self.addEventListener('install', (e) => {
  self.skipWaiting();
});

self.addEventListener('activate', (e) => {
  self.clients.claim();
});

self.addEventListener('fetch', (e) => {
  // Simple pass-through - koi offline caching abhi nahi
  e.respondWith(fetch(e.request));
});
