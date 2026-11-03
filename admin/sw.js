/* Retired. The admin page used to have a worker of its own (scope admin/) to take photos from the share sheet. The
   site is now one installed app, and the site's worker (sw.js) takes the share. A device that still has the old
   worker gets this file when it next opens the admin page: it takes over at once and unregisters itself, so the
   admin page goes back to the site's worker (which leaves admin/ to the network). */

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.registration.unregister());
});
