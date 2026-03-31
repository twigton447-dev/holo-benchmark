// Holo Service Worker
// Caches the app shell so the UI loads instantly and works offline.
// API calls always go to the network — never cached.

const CACHE    = "holo-v1";
const SHELL    = [
  "/",
  "/static/manifest.json",
  "/static/icon-192.png",
  "/static/icon-512.png",
  "https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=Lora:ital,wght@0,400;0,600;0,700;1,400;1,600&display=swap",
];

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const url = new URL(e.request.url);

  // Never intercept API calls, auth, or non-GET requests
  if (e.request.method !== "GET") return;
  if (url.pathname.startsWith("/v1/")) return;
  if (url.hostname === "accounts.google.com") return;

  e.respondWith(
    caches.match(e.request).then(cached => {
      // Network-first for navigation (always get fresh HTML)
      if (e.request.mode === "navigate") {
        return fetch(e.request)
          .then(res => { caches.open(CACHE).then(c => c.put(e.request, res.clone())); return res; })
          .catch(() => cached || caches.match("/"));
      }
      // Cache-first for static assets
      return cached || fetch(e.request).then(res => {
        if (res.ok) caches.open(CACHE).then(c => c.put(e.request, res.clone()));
        return res;
      });
    })
  );
});
