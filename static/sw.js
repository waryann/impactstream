const CACHE_NAME = 'impactstream-v8';
const ASSETS_TO_CACHE = [
  '/',
  '/static/images/logo.png',
  '/static/images/intro.webp',
  '/static/offline.html'
];

// Install Event
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      console.log('[Service Worker] Caching app shell assets');
      return cache.addAll(ASSETS_TO_CACHE).catch(err => {
        console.warn('Could not cache some assets on install:', err);
      });
    })
  );
  self.skipWaiting();
});

// Activate Event
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys => {
      return Promise.all(
        keys.map(key => {
          if (key !== CACHE_NAME) {
            console.log('[Service Worker] Removing old cache:', key);
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch Event (Network first, fall back to cache)
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  
  // Contourner le Service Worker pour les fichiers média
  const isMedia = 
    event.request.destination === 'audio' ||
    event.request.destination === 'video' ||
    event.request.headers.has('range') ||
    url.pathname.includes('/videos/') ||
    url.pathname.match(/\.(mp3|mp4|wav|m4a|webm|ogg|flac|aac|mov|avi|mkv)$/i);

  if (
    event.request.method !== 'GET' || 
    url.pathname.includes('/api/') || 
    url.pathname.includes('/video') || 
    isMedia
  ) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response && response.status === 200 && response.type === 'basic') {
          if (url.pathname.startsWith('/static/')) {
            const responseToCache = response.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(event.request, responseToCache);
            });
          }
        }
        return response;
      })
      .catch(() => {
        // En cas d'échec réseau, on cherche dans le cache
        return caches.match(event.request).then(cachedResponse => {
          if (cachedResponse) {
            return cachedResponse;
          }
          // Si on est hors ligne et qu'il s'agit d'une navigation vers une page (HTML), on affiche la page offline
          if (event.request.mode === 'navigate' || event.request.destination === 'document') {
            return caches.match('/static/offline.html');
          }
          return null;
        });
      })
  );
});
