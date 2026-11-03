import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SHARE_CACHE, cacheStorage, dropShares, pendingShares } from '../../admin/lib/shared.js';

// The photos the site's worker (src/sw.js) keeps in the cache `nocturne-share`, as the page reads them back.
const BASE = 'https://x.test/nocturne/admin/';

/** An in-memory `caches` with one cache; `entries` is {key: {type, name, rawName, body}} (neither name: no X-Name; rawName is sent as it is). */
function fakeCaches(entries, { throwOn = null } = {}) {
  const store = new Map(Object.entries(entries).map(([key, e]) => {
    const headers = { 'Content-Type': e.type };
    if (e.name !== undefined) headers['X-Name'] = encodeURIComponent(e.name);
    if (e.rawName !== undefined) headers['X-Name'] = e.rawName;
    return [BASE + key, new Response(e.body ?? 'bytes', { headers })];
  }));
  const calls = [];
  const fail = (what) => { if (throwOn === what) throw new Error(`${what} failed`); };
  return {
    calls,
    keys: () => [...store.keys()],
    async open(name) {
      calls.push(['open', name]);
      fail('open');
      return {
        keys: async () => { fail('keys'); return [...store.keys()].map((url) => ({ url })); },
        match: async (key) => { fail('match'); return store.get(String(key?.url ?? key))?.clone(); },
        delete: async (key) => { calls.push(['delete', String(key?.url ?? key)]); fail('delete'); return store.delete(String(key?.url ?? key)); },
      };
    },
  };
}

test('pendingShares: the cache name', () => {
  assert.equal(SHARE_CACHE, 'nocturne-share');
});

test('pendingShares reads the kept photos back as Files, in string order of the keys', async () => {
  const caches = fakeCaches({
    'share/2-0': { type: 'image/jpeg', name: '한.jpg', body: 'two' },
    'share/10-1': { type: 'image/png', name: 'ten.png', body: 'ten' },
  });
  const found = await pendingShares(caches);
  assert.deepEqual(found.map((s) => s.key), [`${BASE}share/10-1`, `${BASE}share/2-0`]);  // "1" sorts before "2"
  const [ten, two] = found.map((s) => s.file);
  assert.ok(ten instanceof File);
  assert.equal(ten.name, 'ten.png');
  assert.equal(ten.type, 'image/png');
  assert.equal(ten.lastModified, 10);  // the receive time in the key
  assert.equal(two.name, '한.jpg');  // %ED%95%9C.jpg
  assert.equal(two.type, 'image/jpeg');
  assert.equal(two.lastModified, 2);
  assert.equal(await two.text(), 'two');
  assert.equal(await ten.text(), 'ten');
  assert.deepEqual(caches.calls.filter(([what]) => what === 'open'), [['open', 'nocturne-share']]);
});

test('pendingShares: a missing name becomes photo.jpg', async () => {
  const found = await pendingShares(fakeCaches({ 'share/5-0': { type: 'image/jpeg' } }));
  assert.equal(found.length, 1);
  assert.equal(found[0].file.name, 'photo.jpg');
  assert.equal(found[0].file.lastModified, 5);
});

test('pendingShares: an X-Name that is not valid percent-encoding becomes photo.jpg too', async () => {
  const found = await pendingShares(fakeCaches({ 'share/5-0': { type: 'image/jpeg', rawName: '%E0%A4%A' } }));
  assert.equal(found.length, 1);
  assert.equal(found[0].file.name, 'photo.jpg');
  assert.equal(found[0].file.type, 'image/jpeg');
});

test('pendingShares: nothing kept is an empty list', async () => {
  assert.deepEqual(await pendingShares(fakeCaches({})), []);
});

test('pendingShares: no caches, or a cache that cannot be read, is an empty list', async () => {
  assert.deepEqual(await pendingShares(undefined), []);
  assert.deepEqual(await pendingShares(null), []);
  const entries = { 'share/2-0': { type: 'image/jpeg', name: 'a.jpg' } };
  for (const what of ['open', 'keys', 'match']) {
    assert.deepEqual(await pendingShares(fakeCaches(entries, { throwOn: what })), [], what);
  }
});

test('dropShares removes only the given keys', async () => {
  const caches = fakeCaches({
    'share/1-0': { type: 'image/jpeg', name: 'a.jpg' },
    'share/2-0': { type: 'image/jpeg', name: 'b.jpg' },
    'share/3-0': { type: 'image/png', name: 'c.png' },
  });
  await dropShares(caches, [`${BASE}share/1-0`, `${BASE}share/3-0`]);
  assert.deepEqual(caches.keys(), [`${BASE}share/2-0`]);
  assert.deepEqual((await pendingShares(caches)).map((s) => s.file.name), ['b.jpg']);
});

test('dropShares ignores errors and a missing cache', async () => {
  const entries = { 'share/1-0': { type: 'image/jpeg', name: 'a.jpg' } };
  await dropShares(undefined, ['x']);
  await dropShares(fakeCaches(entries, { throwOn: 'open' }), [`${BASE}share/1-0`]);
  await dropShares(fakeCaches(entries, { throwOn: 'delete' }), [`${BASE}share/1-0`]);
  await dropShares(fakeCaches(entries), []);
});

test('a share that waits through a cancelled login is still there afterwards, and nothing reads it away', async () => {
  const caches = fakeCaches({ 'share/7-0': { type: 'image/jpeg', name: 'a.jpg' } });
  assert.equal((await pendingShares(caches)).length, 1);
  assert.equal((await pendingShares(caches)).length, 1);  // reading never removes
  assert.deepEqual(caches.calls.filter(([what]) => what === 'delete'), []);
});

/** Runs fn with globalThis.caches set to `descriptor` (or removed), putting back whatever was there. */
async function withCaches(descriptor, fn) {
  const had = Object.getOwnPropertyDescriptor(globalThis, 'caches');
  try {
    delete globalThis.caches;
    if (descriptor) Object.defineProperty(globalThis, 'caches', { configurable: true, ...descriptor });
    await fn();
  } finally {
    delete globalThis.caches;
    if (had) Object.defineProperty(globalThis, 'caches', had);
  }
}

test('cacheStorage: the browser\'s caches, or null when there are none or reading them throws', async () => {
  await withCaches(null, () => assert.equal(cacheStorage(), null));  // Node has none
  const fake = { open: async () => ({}) };
  await withCaches({ value: fake }, () => assert.equal(cacheStorage(), fake));
  // site data blocked: merely reading `caches` throws (as localStorage does)
  await withCaches({ get() { throw new DOMException('blocked', 'SecurityError'); } }, () => assert.equal(cacheStorage(), null));
});

test('with a blocked cache storage, the page reads no shares and dropping them ends quietly', async () => {
  await withCaches({ get() { throw new Error('blocked'); } }, async () => {
    assert.deepEqual(await pendingShares(cacheStorage()), []);
    await dropShares(cacheStorage(), ['x']);
  });
});
