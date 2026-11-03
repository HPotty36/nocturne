/* Nocturne's service worker: keeps the exhibition on the device, so an installed site opens without a network.

   This file is a template. scripts/build.py writes sw.js next to index.html with the three constants below filled
   in: VERSION (a hash of both pages, the manifest, the icons and this file, so any change to the site gives the
   browser a new worker), PHOTOS (the hashed thumb and full URLs of every photo on show, relative to the scope) and COVER
   (the full-size URL of the cover, or empty). Unfilled, the file is still valid JavaScript: the node tests
   (tests/js/sw.test.js) run it in node:vm and call the three decisions below, and it only listens for events
   when it runs as a service worker.

   Caches: nocturne-page (both pages, the manifest, the icons), nocturne-thumbs (every thumbnail on show),
   nocturne-full (the cover, then full-size copies once viewed), nocturne-fonts (the Google Fonts stylesheet and
   font files), nocturne-share (photos shared to the installed app from a phone's share sheet, see receive; they wait
   there, one entry each, until the admin page uploads or drops them: admin/lib/shared.js; never cleaned here).
   Apart from that share (a POST to admin/share), admin/ is never answered here: the admin page, its GitHub login
   return and its API calls go to the network exactly as if there were no service worker. */

const VERSION = '__VERSION__';
const PHOTOS = [/*__PHOTOS__*/];
const COVER = '' /*__COVER__*/;

const PAGE = 'nocturne-page';
const THUMBS = 'nocturne-thumbs';
const FULL = 'nocturne-full';
const FONTS = 'nocturne-fonts';
const SHARE = 'nocturne-share';  // photos shared to the app, waiting for the admin page
const ACCEPT = ['image/jpeg', 'image/png'];  // what the manifest's share_target asks for, and what the photo picker takes
const TIMEOUT = 4000;  // ms a page waits for the network before the copy on the device is shown
const SHELL = ['manifest.webmanifest', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/icon-maskable-512.png',
               'icons/apple-touch-icon.png'];

/* What to do with a request: 'pass' (not answered here), 'page' (one of the two pages), 'shell' (manifest, icons),
   'thumb', 'full', 'font-css' or 'font-file'. url and scope are absolute; mode is request.mode. */
function route(url, scope, mode) {
  const u = new URL(url);
  if (u.origin === 'https://fonts.googleapis.com') return 'font-css';
  if (u.origin === 'https://fonts.gstatic.com') return 'font-file';
  const base = new URL(scope);
  if (u.origin !== base.origin || !u.pathname.startsWith(base.pathname)) return 'pass';
  const path = u.pathname.slice(base.pathname.length);
  if (path === 'admin' || path.startsWith('admin/')) return 'pass';  // checked first: the admin page is never touched
  if (mode === 'navigate' && ['', 'index.html', 'en/', 'en/index.html'].includes(path)) return 'page';
  if (path === 'manifest.webmanifest' || path.startsWith('icons/')) return 'shell';
  if (path.startsWith('photos/thumb/')) return 'thumb';
  if (path.startsWith('photos/full/')) return 'full';
  return 'pass';
}

/* Cached photo URLs (absolute) that no photo on show uses any more: taken down, or replaced under the same name
   (its ?v= hash changed). Anything outside photos/ is left alone. */
function stale(cachedUrls, photos, scope) {
  const shown = new Set();
  for (const p of photos) {
    shown.add(new URL(p.thumb, scope).href);
    shown.add(new URL(p.full, scope).href);
  }
  const under = new URL('photos/', scope).href;
  return cachedUrls.filter((url) => url.startsWith(under) && !shown.has(url));
}

/* Only a whole, readable answer is kept: never an error page (404 or 500 during a deploy), an opaque answer or an
   unfollowed (opaque) redirect. */
function storable(response) {
  return response.ok === true && (response.type === 'basic' || response.type === 'cors');
}

if (typeof ServiceWorkerGlobalScope !== 'undefined') {
  const scope = self.registration.scope;
  const at = (path) => new URL(path, scope).href;
  const shown = new Set(PHOTOS.flatMap((p) => [at(p.thumb), at(p.full)]));
  const ours = [PAGE, THUMBS, FULL, FONTS, SHARE];
  const quiet = () => {};

  /* Fetches the request and, when keep(response), stores a copy under key while the event lives. The copy is
     taken before the response is handed on, so whoever reads the original never races the cache. No network gives
     Response.error(): the page sees the same network error, and no promise is left rejected. */
  const fetchAndKeep = (event, name, key, keep) => {
    const network = fetch(event.request);
    event.waitUntil(network.then((res) => {
      if (!keep(res)) return undefined;
      const copy = res.clone();
      return caches.open(name).then((cache) => cache.put(key, copy));
    }).catch(quiet));
    return network.catch(() => Response.error());
  };

  // undefined when there is no copy, or the cache cannot be read (then the network answers as usual)
  const cached = (name, key) => caches.open(name).then((cache) => cache.match(key, { ignoreVary: true })).catch(quiet);

  /* A share from the phone's share sheet (the manifest's share_target: a POST to admin/share). The usable photos are
     kept for the admin page, one entry each: key admin/share/<ms>-<number>, the photo as the body, Content-Type and
     X-Name (the file name through encodeURIComponent) as headers. Then on to the admin page with a 303, so the page
     is read with a GET, never with the POST again. A cache that cannot be written (no space) or a form that cannot
     be read only means fewer photos there: the person is never left on an error page. */
  const receive = async (request) => {
    try {
      const form = await request.formData();
      const photos = form.getAll('photos').filter((f) => typeof f !== 'string' && f.size > 0 && ACCEPT.includes(f.type));
      if (photos.length > 0) {
        const cache = await caches.open(SHARE);
        const now = Date.now();
        for (let i = 0; i < photos.length; i++) {
          await cache.put(at(`admin/share/${now}-${i}`), new Response(photos[i], {
            headers: { 'Content-Type': photos[i].type, 'X-Name': encodeURIComponent(photos[i].name) },
          }));
        }
      }
    } catch (e) {
      // nothing kept: the redirect below is the same
    }
    return Response.redirect(at('admin/'), 303);
  };

  self.addEventListener('install', (event) => {
    event.waitUntil((async () => {
      // the pages and the shell are taken fresh each time (revalidated, not straight from the HTTP cache)
      const pages = await caches.open(PAGE);
      await pages.addAll(['./', 'en/', ...SHELL].map((path) => new Request(at(path), { cache: 'no-cache' })));
      // thumbnails, and the cover (a full-size copy that the first page loads before this worker controls it): only
      // the ones not here yet, each on its own; one that cannot be had waits for the page to ask
      const [thumbs, full] = await Promise.all([caches.open(THUMBS), caches.open(FULL)]);
      const keep = async (cache, url) => {
        try {
          if (await cache.match(url)) return;
          const res = await fetch(url);
          if (storable(res)) await cache.put(url, res);
        } catch (e) {
          // offline or gone: not a reason to keep the old worker
        }
      };
      await Promise.all([...PHOTOS.map((p) => keep(thumbs, at(p.thumb))), ...(COVER ? [keep(full, at(COVER))] : [])]);
      await self.skipWaiting();
    })());
  });

  self.addEventListener('activate', (event) => {
    event.waitUntil((async () => {
      // other sites of this origin keep their caches: only this site's old ones go
      for (const name of await caches.keys()) {
        if (name.startsWith('nocturne-') && !ours.includes(name)) await caches.delete(name);
      }
      for (const name of [THUMBS, FULL]) {
        const cache = await caches.open(name);
        const urls = (await cache.keys()).map((request) => request.url);
        await Promise.all(stale(urls, PHOTOS, scope).map((url) => cache.delete(url)));
      }
      await self.clients.claim();
    })());
  });

  /* A page: the network if it answers well (ok) within TIMEOUT; otherwise (no network, too slow, 404 or 500) the
     copy on the device; with no copy, whatever the network gives, however long it takes. Every good answer
     refreshes the copy, even a late one; an error page never covers the exhibition. A redirect (a navigation
     is fetched with redirect: 'manual', so it comes back as an opaque redirect) is no failure: it is handed on as
     it is, never kept, and the browser follows it, so a site that moved never traps a device in its old copy. */
  const page = async (event) => {
    const path = new URL(event.request.url).pathname.slice(new URL(scope).pathname.length);
    const key = at(path.startsWith('en/') ? 'en/' : './');
    const network = fetchAndKeep(event, PAGE, key, storable);
    let timer;
    const early = await Promise.race([network, new Promise((resolve) => { timer = setTimeout(resolve, TIMEOUT, null); })]);
    clearTimeout(timer);
    if (early && (early.ok || early.type === 'opaqueredirect')) return early;
    return (await cached(PAGE, key)) || network;
  };

  /* The copy on the device, else the network (kept when keep(response)). Offline with no copy: a network error,
     so a viewer stays on the thumbnail. */
  const cacheFirst = async (event, name, keep) => {
    const hit = await cached(name, event.request.url);
    return hit || fetchAndKeep(event, name, event.request.url, keep);
  };

  const photo = (res, url) => storable(res) && shown.has(url);

  self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method === 'POST' && new URL(request.url).pathname === new URL(at('admin/share')).pathname) {
      event.respondWith(receive(request));
      return;
    }
    if (request.method !== 'GET') return;
    const kind = route(request.url, scope, request.mode);
    if (kind === 'pass') return;  // no respondWith: the browser fetches it as usual
    if (kind === 'page') {
      event.respondWith(page(event));
    } else if (kind === 'shell') {
      event.respondWith(cacheFirst(event, PAGE, () => false));  // refreshed by each new worker's install
    } else if (kind === 'thumb') {
      event.respondWith(cacheFirst(event, THUMBS, (res) => photo(res, request.url)));
    } else if (kind === 'full') {
      event.respondWith(cacheFirst(event, FULL, (res) => photo(res, request.url)));
    } else if (kind === 'font-css') {
      // the stylesheet on the device at once; a fresh one is fetched and kept for next time
      const network = fetchAndKeep(event, FONTS, request.url, storable);
      event.respondWith(cached(FONTS, request.url).then((hit) => hit || network));
    } else if (kind === 'font-file') {
      event.respondWith(cacheFirst(event, FONTS, storable));
    }
  });
}
