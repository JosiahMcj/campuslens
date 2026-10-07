/* CampusLens service worker.
 *
 * Caches only the application shell, from an ALLOW-LIST: navigation HTML
 * responses, the manifest, the icons, and the hashed build assets under
 * /assets/. Everything else — every API response in particular — goes to
 * the network untouched and is NEVER stored. The production server serves
 * the API unprefixed (/events, /auth/me), so a path-prefix exclusion is not
 * enough: a cached /auth/me kept serving a signed-out executive's user
 * object and CSRF token. Offline means exactly this: the shell opens, and
 * the API is required for every number on it.
 */
const CACHE = 'cabinet-shell-v4'
const SHELL = [
  '/',
  '/index.html',
  '/manifest.webmanifest',
  '/favicon.svg',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/icons/icon-maskable-192.png',
  '/icons/icon-maskable-512.png',
  '/icons/apple-touch-icon.png',
]

// The allow-list: the shell entries plus the hashed build assets and the
// icons. Anything else — API paths, dev-server internals (/@vite,
// /node_modules, /src), unknown routes — is answered by the network only.
function cacheableStaticPath(pathname) {
  return (
    SHELL.includes(pathname) ||
    pathname.startsWith('/assets/') ||
    pathname.startsWith('/icons/')
  )
}

// Only a good HTML document may become the cached shell: an error page or
// a non-HTML response cached as '/' would replace the app shell for every
// later offline navigation.
function cacheableShellResponse(response) {
  if (!response.ok) return false
  const type = response.headers.get('content-type') || ''
  return type.includes('text/html')
}

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))),
      )
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url)
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) return

  // Navigations: network first, cached shell as the offline fallback.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          if (cacheableShellResponse(response)) {
            const copy = response.clone()
            caches.open(CACHE).then((cache) => cache.put('/', copy))
          }
          return response
        })
        .catch(() =>
          caches.match('/').then((cached) => cached ?? Response.error()),
        ),
    )
    return
  }

  // Static shell assets only: cache first, network fill. Everything else
  // returns without respondWith — the browser takes it to the network and
  // the worker never sees the response again, so it can never be stored.
  if (!cacheableStaticPath(url.pathname)) return
  event.respondWith(
    caches.match(event.request).then(
      (cached) =>
        cached ??
        fetch(event.request).then((response) => {
          if (response.ok) {
            const copy = response.clone()
            caches.open(CACHE).then((cache) => cache.put(event.request, copy))
          }
          return response
        }),
    ),
  )
})
