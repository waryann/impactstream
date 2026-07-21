const CACHE_NAME = 'impactstream-v10';
const MEDIA_CACHE = 'impactstream-media';
const BIBLE_CACHE = 'impactstream-bible';

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
          if (key !== CACHE_NAME && key !== MEDIA_CACHE && key !== BIBLE_CACHE) {
            console.log('[Service Worker] Removing old cache:', key);
            return caches.delete(key);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Helper for Range requests (Audio/Video offline support for Safari)
async function handleRangeRequest(request) {
  const cache = await caches.open(MEDIA_CACHE);
  const cachedResponse = await cache.match(request.url);
  
  if (!cachedResponse) {
    return fetch(request);
  }

  const rangeHeader = request.headers.get('range');
  if (!rangeHeader) {
    return cachedResponse;
  }

  const arrayBuffer = await cachedResponse.arrayBuffer();
  const match = rangeHeader.match(/bytes=(\d+)-(.*)/);
  if (!match) return cachedResponse;

  const start = Number(match[1]);
  const end = match[2] ? Number(match[2]) : arrayBuffer.byteLength - 1;
  const slicedBuffer = arrayBuffer.slice(start, end + 1);

  return new Response(slicedBuffer, {
    status: 206,
    statusText: 'Partial Content',
    headers: {
      'Content-Type': cachedResponse.headers.get('Content-Type') || 'audio/mpeg',
      'Content-Range': `bytes ${start}-${end}/${arrayBuffer.byteLength}`,
      'Content-Length': slicedBuffer.byteLength,
      'Accept-Ranges': 'bytes'
    }
  });
}

// Fetch Event
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // 0. Bypass SW for offline downloads (bug Safari cache no-store)
  if (url.searchParams.has('offline_download')) {
    return;
  }

  // 1. Gérer les fichiers médias (Audio) via le Cache de Médias
  const isMedia = 
    event.request.destination === 'audio' ||
    event.request.headers.has('range') ||
    url.pathname.match(/\.(mp3|mp4|wav|m4a|webm|ogg|flac|aac)$/i);

  if (isMedia) {
    event.respondWith(handleRangeRequest(event.request));
    return;
  }

  // 2. Gérer les requêtes API Bible
  if (url.hostname === 'api.getbible.net') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          // Clone and cache the response if successful
          if (response && response.status === 200) {
            const responseToCache = response.clone();
            caches.open(BIBLE_CACHE).then(cache => {
              cache.put(event.request, responseToCache);
            });
          }
          return response;
        })
        .catch(async () => {
          // Hors-ligne : chercher dans le cache bible
          const cache = await caches.open(BIBLE_CACHE);
          return cache.match(event.request);
        })
    );
    return;
  }

  // 3. Ignorer certaines requêtes backend (LiveKit, etc) mais autoriser /api/medias
  if (
    event.request.method !== 'GET' || 
    (url.pathname.includes('/api/') && url.hostname !== 'api.getbible.net' && !url.pathname.includes('/api/medias')) || 
    url.pathname.includes('/video')
  ) {
    return;
  }

  // 4. Stratégie classique (Network First, Cache Fallback)
  event.respondWith(
    fetch(event.request)
      .then(response => {
        if (response && response.status === 200 && response.type === 'basic') {
          if (url.pathname.startsWith('/static/') || url.pathname.includes('/api/medias')) {
            const responseToCache = response.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(event.request, responseToCache);
            });
          }
        }
        return response;
      })
      .catch(() => {
        return caches.match(event.request).then(cachedResponse => {
          if (cachedResponse) {
            return cachedResponse;
          }
          if (event.request.mode === 'navigate' || event.request.destination === 'document') {
            return caches.match('/static/offline.html');
          }
          return null;
        });
      })
  );
});
