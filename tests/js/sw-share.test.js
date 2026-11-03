import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

// The site's worker (src/sw.js) takes the photos shared to the installed app (the manifest's share_target, a POST to
// admin/share): run the way a service worker runs it, in node:vm with stand-ins for the worker scope and the
// caches. Request, Response, FormData and File are Node's own. admin/sw.js only retires the admin page's old worker.
const source = readFileSync(new URL('../../src/sw.js', import.meta.url), 'utf8');
const S = 'https://hpotty36.github.io/nocturne/';
const SHARE = `${S}admin/share`;

/** A worker with the cache `nocturne-share` in memory (or one whose caches.open always rejects). */
function boot({ broken = false } = {}) {
  const listeners = {};
  const entries = new Map(); // key -> Response
  const opened = [];
  const caches = {
    async open(name) {
      opened.push(name);
      if (broken) throw new Error('no space left');
      return { put: async (key, response) => { entries.set(String(key?.url ?? key), response); } };
    },
  };
  const ctx = vm.createContext({
    URL, Request, Response, caches, setTimeout, clearTimeout, ServiceWorkerGlobalScope: class {},
    fetch: async () => { throw new Error('no network in this test'); },
    self: { registration: { scope: S }, addEventListener: (type, fn) => { listeners[type] = fn; },
            skipWaiting: async () => {}, clients: { claim: async () => {} } },
  });
  vm.runInContext(source, ctx);

  /** Fires a fetch event; resolves to the response given to respondWith, or null when it was not called. */
  async function fetchEvent(request) {
    let answer = null;
    listeners.fetch({ request, respondWith: (p) => { answer = p; }, waitUntil: () => {} });
    return answer === null ? null : answer;
  }
  return { entries, opened, fetchEvent };
}

const file = (name, type, bytes = 'x') => new File([bytes], name, { type });

function sharePost(files, url = SHARE) {
  const form = new FormData();
  for (const f of files) form.append('photos', f);
  return new Request(url, { method: 'POST', body: form });
}

test('a share POST keeps the JPEG and PNG files and answers 303 to the admin page', async () => {
  const w = boot();
  const answer = await w.fetchEvent(sharePost([
    file('a.jpg', 'image/jpeg', 'jpeg bytes'), file('b.png', 'image/png', 'png bytes'),
    file('c.heic', 'image/heic', 'heic bytes'), file('empty.jpg', 'image/jpeg', ''),
  ]));
  assert.notEqual(answer, null, 'respondWith was called');
  const response = await answer;
  assert.equal(response.status, 303);
  assert.equal(response.headers.get('Location'), `${S}admin/`);

  const keys = [...w.entries.keys()];
  assert.equal(keys.length, 2);
  for (const key of keys) assert.match(key, /^https:\/\/hpotty36\.github\.io\/nocturne\/admin\/share\/\d+-\d$/);
  const kept = [...w.entries.values()];
  assert.deepEqual(kept.map((r) => decodeURIComponent(r.headers.get('X-Name'))), ['a.jpg', 'b.png']);
  assert.deepEqual(kept.map((r) => r.headers.get('Content-Type')), ['image/jpeg', 'image/png']);
  assert.deepEqual(await Promise.all(kept.map((r) => r.text())), ['jpeg bytes', 'png bytes']);
  assert.deepEqual(w.opened, ['nocturne-share']);
});

test('the file name is kept whole, whatever it holds', async () => {
  const w = boot();
  await (await w.fetchEvent(sharePost([file('한 강 100%.jpg', 'image/jpeg')])));
  const [kept] = [...w.entries.values()];
  assert.equal(kept.headers.get('X-Name'), encodeURIComponent('한 강 100%.jpg'));  // header-safe: ASCII only
  assert.equal(decodeURIComponent(kept.headers.get('X-Name')), '한 강 100%.jpg');
});

test('other requests are left alone: no respondWith', async () => {
  const w = boot();
  for (const request of [
    new Request(`${S}admin/`),                                    // the admin page itself
    new Request(SHARE),                                           // a GET of the share address
    new Request(`${S}admin/other`, { method: 'POST', body: 'x' }),  // a POST somewhere else
    new Request(`${S}share`, { method: 'POST', body: 'x' }),        // not under admin/
    new Request('https://api.github.com/repos/x/y', { method: 'POST', body: '{}' }),
  ]) {
    assert.equal(await w.fetchEvent(request), null, `${request.method} ${request.url}`);
  }
  assert.equal(w.entries.size, 0);
  assert.deepEqual(w.opened, []);  // no cache was even opened
});

test('a cache that cannot be written still gets the 303', async () => {
  const w = boot({ broken: true });
  const response = await (await w.fetchEvent(sharePost([file('a.jpg', 'image/jpeg')])));
  assert.equal(response.status, 303);
  assert.equal(response.headers.get('Location'), `${S}admin/`);
  assert.equal(w.entries.size, 0);
});

test('a body that is not a form still gets the 303', async () => {
  const w = boot();
  const request = new Request(SHARE, { method: 'POST', body: 'not a form', headers: { 'Content-Type': 'text/plain' } });
  const response = await (await w.fetchEvent(request));
  assert.equal(response.status, 303);
  assert.equal(w.entries.size, 0);
});

test('a share with nothing usable keeps nothing', async () => {
  const w = boot();
  const response = await (await w.fetchEvent(sharePost([file('c.heic', 'image/heic'), file('d.gif', 'image/gif')])));
  assert.equal(response.status, 303);
  assert.equal(w.entries.size, 0);
});

test("admin/sw.js retires the admin page's old worker: it takes over and unregisters itself", async () => {
  const listeners = {};
  const calls = { skipped: 0, unregistered: 0 };
  const ctx = vm.createContext({
    self: {
      registration: { unregister: async () => { calls.unregistered += 1; return true; } },
      addEventListener: (type, fn) => { listeners[type] = fn; },
      skipWaiting: async () => { calls.skipped += 1; },
    },
  });
  vm.runInContext(readFileSync(new URL('../../admin/sw.js', import.meta.url), 'utf8'), ctx);
  assert.deepEqual(Object.keys(listeners).sort(), ['activate', 'install']);  // no fetch: it answers nothing
  listeners.install({});
  let done;
  listeners.activate({ waitUntil: (p) => { done = p; } });
  await done;
  assert.deepEqual(calls, { skipped: 1, unregistered: 1 });
});
