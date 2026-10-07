// The DOM-free part of the "새 사진" tab (QueueStore): naming, one commit per upload, retries after a failed upload,
// which queue items are shown and when item.json is read again, what a disappearance means, and the three actions.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { QueueStore } from '../../admin/views/queue.js';
import { GitHubError } from '../../admin/lib/github.js';
import { GalleryError, serialize } from '../../admin/lib/gallery.js';
import { ImageError } from '../../admin/lib/image.js';
import { newItem, toWaiting } from '../../admin/lib/queue.js';

const ROOT = new URL('../../', import.meta.url);
const site = () => JSON.parse(readFileSync(new URL('tests/fixtures/site.json', ROOT), 'utf8'));
const REPO = 'HPotty36/nocturne';
const DATA = 'src/photos.json';
const NOW = Date.parse('2026-10-07T09:00:00Z');
const tick = () => new Promise((done) => setTimeout(done, 0));

const bytesOf = async (value) => (value instanceof Blob ? new Uint8Array(await value.arrayBuffer()) : new TextEncoder().encode(value));
/** The git blob sha of some bytes, as GitHub reports it for a file. */
const gitSha = (bytes) => createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex');

/**
 * An in-memory GitHub with the calls the queue view uses. A file's sha is its real git blob sha (so the same bytes
 * have the same sha, as on GitHub); a folder's sha is made of its files' paths and shas, so it changes when anything
 * inside changes. A JSON read of a file that is not JSON fails as in github.js (GitHubError, status 200).
 * onBuild(fn) runs fn before the next commit's build (another device acting meanwhile); failCommit(error, {landed})
 * makes the next commit throw, after applying it when `landed` (the answer was lost); holdCommit() pauses it.
 */
function fakeGitHub(initial = {}) {
  const store = new Map(); // path -> text or Blob
  const text = (value) => (typeof value === 'string' ? value : `${JSON.stringify(value, null, 2)}\n`);
  const write = (path, value) => store.set(path, typeof value === 'string' || value instanceof Blob ? value : text(value));
  for (const [path, value] of Object.entries(initial)) write(path, value);
  const shaOf = async (path) => gitSha(await bytesOf(store.get(path)));
  const parsed = (path) => {
    if (!store.has(path)) return null;
    try {
      return JSON.parse(store.get(path));
    } catch {
      throw new GitHubError(`파일 내용을 읽을 수 없어요: ${path}`, 200);
    }
  };
  const calls = [];
  const commits = [];
  const beforeBuild = [];
  const failures = [];
  const holds = [];

  const children = async (dir) => {
    const prefix = `${dir}/`;
    const out = new Map();
    for (const path of [...store.keys()].sort()) {
      if (!path.startsWith(prefix)) continue;
      const [name, ...rest] = path.slice(prefix.length).split('/');
      const entry = out.get(name) ?? { name, path: prefix + name, parts: [], type: rest.length ? 'dir' : 'file' };
      entry.parts.push(`${path}:${await shaOf(path)}`);
      out.set(name, entry);
    }
    return Promise.all([...out.values()].map(async ({ parts, ...e }) => ({ ...e, sha: e.type === 'file' ? await shaOf(e.path) : `t(${parts.join(',')})` })));
  };

  const gh = {
    async listDir(repo, path) {
      calls.push(['listDir', path]);
      return children(path);
    },
    async getJSON(repo, path) {
      calls.push(['getJSON', path]);
      return store.has(path) ? { data: parsed(path), sha: await shaOf(path) } : null;
    },
    async getFile(repo, path) {
      calls.push(['getFile', path]);
      return store.has(path) ? { bytes: await bytesOf(store.get(path)), sha: await shaOf(path) } : null;
    },
    async commitFiles(repo, { message, build }) {
      calls.push(['commitFiles', message]);
      while (beforeBuild.length) beforeBuild.shift()();
      const built = await build(async (path) => (store.has(path) ? { data: parsed(path) } : null));
      if (holds.length) await holds.shift();
      const failure = failures.shift();
      if (failure && !failure.landed) throw failure.error;
      if (!built) return null;
      for (const [path, change] of Object.entries(built.files)) {
        if (change === null) {
          assert.ok(store.has(path), `deleting a missing file: ${path}`); // GitHub answers 422
          store.delete(path);
        } else if (typeof change === 'object' && !(change instanceof Blob) && Object.keys(change).length === 1 && 'sha' in change) {
          let source = null;
          for (const other of store.keys()) if ((await shaOf(other)) === change.sha) source = store.get(other);
          assert.ok(source, `reusing a blob that is not there: ${change.sha}`);
          store.set(path, source);
        } else {
          write(path, change);
        }
      }
      commits.push({ message, files: built.files });
      if (failure) throw failure.error; // it landed, but the answer was lost
      return `commit-${commits.length}`;
    },
  };
  return {
    gh,
    calls,
    commits,
    has: (path) => store.has(path),
    json: parsed,
    value: (path) => store.get(path),
    sha: (path) => shaOf(path),
    put: write,
    remove: (path) => store.delete(path),
    removeDir: (dir) => [...store.keys()].filter((p) => p.startsWith(`${dir}/`)).forEach((p) => store.delete(p)),
    onBuild: (fn) => beforeBuild.push(fn),
    failCommit: (error, { landed = false } = {}) => failures.push({ error, landed }),
    holdCommit() {
      let release;
      holds.push(new Promise((done) => { release = done; }));
      return release;
    },
    count: (method, path) => calls.filter(([m, p]) => m === method && (path === undefined || p === path)).length,
  };
}

/** queue/<name>/ as the page uploads it: full.jpg, thumb.jpg and item.json. */
function queued(name, extra = {}) {
  const item = { ...newItem({ name, kind: 'new', grayscale: false, now: NOW - 60_000 }), ...extra };
  return {
    [`queue/${name}/full.jpg`]: new Blob([`full of ${name}`]),
    [`queue/${name}/thumb.jpg`]: new Blob([`thumb of ${name}`]),
    [`queue/${name}/item.json`]: item,
  };
}

function redraftQueued(file, extra = {}) {
  const name = `redraft-${file}`;
  return { [`queue/${name}/item.json`]: { ...newItem({ name, kind: 'redraft', grayscale: false, now: NOW - 60_000, file }), ...extra } };
}

/** A processPhoto stand-in: records what it did; `hints`, `gray` and `bad` (ImageError) come from the fake file. */
function fakeProcess(events = []) {
  const process = async (file) => {
    events.push(`process ${file.name}`);
    await tick();
    if (file.bad) throw new ImageError('이 사진을 읽을 수 없어요. 사진 앱에서 JPEG로 골라 주세요');
    return { full: new Blob([`full ${file.name}`]), thumb: new Blob([`thumb ${file.name}`]), hints: file.hints ?? {}, grayscale: file.gray ?? false, width: 10, height: 10 };
  };
  process.events = events;
  return process;
}

function makeStore(fake, process = fakeProcess()) {
  const store = new QueueStore(fake.gh, REPO, { process, now: () => NOW });
  return store;
}

const cards = (store) => [...store.cards.values()];
const byName = (store, name) => cards(store).find((c) => c.name === name);
const messages = (fake) => fake.commits.map((c) => c.message);

// --- uploading ---------------------------------------------------------------------------------

test('each upload takes the first free name against the exhibition, the queue and its own batch', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('image') });
  const store = makeStore(fake);
  await store.load();
  const before = { photos: fake.count('getJSON', DATA), queue: fake.count('listDir', 'queue') };
  await store.addFiles([{ name: 'image.jpg' }, { name: 'IMG_A1.jpg' }, { name: 'image.jpg' }, { name: 'C:\\photos\\image.JPG' }]);
  assert.deepEqual(messages(fake), ['대기열: image-2 올림', '대기열: a1-2 올림', '대기열: image-3 올림', '대기열: image-4 올림']);
  assert.deepEqual(cards(store).map((c) => [c.name, c.stage]),
    [['image', 'queue'], ['image-2', 'queue'], ['a1-2', 'queue'], ['image-3', 'queue'], ['image-4', 'queue']]);
  // photos.json and the queue are read once for the whole batch, fresh, before the first name is chosen
  assert.equal(fake.count('getJSON', DATA) - before.photos, 1);
  assert.equal(fake.count('listDir', 'queue') - before.queue, 1);
});

test('a picked photo named like a redraft request is uploaded under another name', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  await store.load();
  await store.addFiles([{ name: 'redraft-a2.jpg' }]);
  assert.deepEqual(messages(fake), ['대기열: photo-redraft-a2 올림']);
  assert.equal(fake.json('queue/photo-redraft-a2/item.json').kind, 'new');
  assert.equal(fake.has('queue/redraft-a2/item.json'), false);
});

test('a later batch reads the names again and keeps clear of names this page still holds', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  await store.load();
  fake.failCommit(new GitHubError('GitHub 502: Injected failure (fail_next)', 502));
  await store.addFiles([{ name: 'x.jpg' }]); // fails: keeps "x" for 다시 올리기
  fake.put('queue/x-2/item.json', newItem({ name: 'x-2', kind: 'new', grayscale: false, now: NOW })); // another device
  await store.addFiles([{ name: 'x.jpg' }]);
  assert.deepEqual(messages(fake), ['대기열: x-3 올림']);
  assert.equal(byName(store, 'x').stage, 'upload-failed');
});

test('an upload is one commit of full.jpg, thumb.jpg and item.json; photos are processed and committed one at a time', async () => {
  const events = [];
  const fake = fakeGitHub({ [DATA]: site() });
  const original = fake.gh.commitFiles;
  fake.gh.commitFiles = async (repo, args) => {
    events.push(`commit ${args.message}`);
    const sha = await original(repo, args);
    events.push(`committed ${args.message}`);
    return sha;
  };
  const process = fakeProcess(events);
  const store = makeStore(fake, process);
  await store.load();
  const hints = { date: '2025.11.30', camera: 'TestCam · f/2 · 1/60s · ISO 100' };
  await store.addFiles([{ name: 'one.jpg', hints, gray: true }, { name: 'two.png' }]);

  assert.deepEqual(events, ['process one.jpg', 'commit 대기열: one 올림', 'committed 대기열: one 올림',
    'process two.png', 'commit 대기열: two 올림', 'committed 대기열: two 올림']);
  const [first] = fake.commits;
  assert.deepEqual(Object.keys(first.files), ['queue/one/full.jpg', 'queue/one/thumb.jpg', 'queue/one/item.json']);
  assert.ok(first.files['queue/one/full.jpg'] instanceof Blob);
  assert.equal(await first.files['queue/one/full.jpg'].text(), 'full one.jpg');
  assert.equal(await first.files['queue/one/thumb.jpg'].text(), 'thumb one.jpg');
  assert.deepEqual(first.files['queue/one/item.json'],
    newItem({ name: 'one', kind: 'new', grayscale: true, now: NOW, date: hints.date, camera: hints.camera }));
  assert.deepEqual(fake.json('queue/two/item.json'), newItem({ name: 'two', kind: 'new', grayscale: false, now: NOW }));

  const card = byName(store, 'one');
  assert.equal(card.stage, 'queue');
  assert.equal(card.copies, null, 'the processed copies are let go once committed');
  assert.equal(card.file, null);
  assert.equal(await card.thumb.text(), 'thumb one.jpg', 'the thumbnail stays for the card');
  assert.deepEqual(card.item, first.files['queue/one/item.json']);
});

test('cards appear at once while their photos wait their turn', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  let changes = 0;
  store.onChange = () => changes++;
  const done = store.addFiles([{ name: 'a.jpg' }, { name: 'b.jpg' }]);
  assert.deepEqual(cards(store).map((c) => [c.file.name, c.stage]), [['a.jpg', 'upload'], ['b.jpg', 'upload']]);
  assert.ok(changes >= 1);
  await done;
  assert.deepEqual(cards(store).map((c) => [c.name, c.stage]), [['a', 'queue'], ['b', 'queue']]);
});

test('a failed upload keeps its processed copies; 다시 올리기 commits them without processing again', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const process = fakeProcess();
  const store = makeStore(fake, process);
  fake.failCommit(new GitHubError('GitHub 502: Injected failure (fail_next)', 502));
  await store.addFiles([{ name: 'a.jpg' }, { name: 'b.jpg' }]);
  const a = byName(store, 'a');
  assert.equal(a.stage, 'upload-failed');
  assert.equal(a.error.message, 'GitHub 502: Injected failure (fail_next)');
  assert.equal(a.final, false);
  assert.ok(a.copies.full instanceof Blob && a.copies.thumb instanceof Blob);
  assert.equal(byName(store, 'b').stage, 'queue', 'the next photo of the batch still goes up');
  assert.equal(fake.has('queue/a/item.json'), false);

  await store.reupload(a.key);
  assert.deepEqual(process.events, ['process a.jpg', 'process b.jpg']);
  assert.deepEqual(messages(fake), ['대기열: b 올림', '대기열: a 올림']);
  assert.equal(a.stage, 'queue');
  assert.equal(a.error, null);
  assert.equal(a.copies, null);
  assert.equal(await fake.value('queue/a/full.jpg').text(), 'full a.jpg');
});

test('an unreadable photo shows its ImageError, takes no name and cannot be sent again', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const process = fakeProcess();
  const store = makeStore(fake, process);
  await store.addFiles([{ name: 'bad.heic', bad: true }, { name: 'bad.jpg' }]);
  const [bad, good] = cards(store);
  assert.equal(bad.stage, 'upload-failed');
  assert.ok(bad.error instanceof ImageError);
  assert.equal(bad.final, true);
  assert.equal(bad.name, null);
  assert.equal(bad.file, null, 'nothing left to send');
  assert.equal(bad.fileName, 'bad.heic', 'the card still says which photo it was');
  assert.equal(good.name, 'bad');
  await store.reupload(bad.key);
  assert.deepEqual(process.events, ['process bad.heic', 'process bad.jpg']);
  assert.deepEqual(messages(fake), ['대기열: bad 올림']);
  store.dropUpload(bad.key);
  assert.deepEqual(cards(store).map((c) => c.name), ['bad']);
});

test('a name another device took meanwhile is never overwritten: the upload moves to the next free name', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  const theirs = newItem({ name: 'image', kind: 'new', grayscale: true, now: NOW - 5000 });
  fake.onBuild(() => fake.put('queue/image/item.json', theirs));
  await store.addFiles([{ name: 'image.jpg' }]);
  assert.deepEqual(messages(fake), ['대기열: image-2 올림']);
  assert.deepEqual(fake.json('queue/image/item.json'), theirs);
  assert.equal(fake.json('queue/image-2/item.json').name, 'image-2');
  assert.deepEqual(cards(store).map((c) => [c.name, c.stage]), [['image-2', 'queue']]);
});

test('an upload that landed although its answer was lost is not committed twice', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  fake.failCommit(new GitHubError('인터넷 연결을 확인해 주세요', 0), { landed: true });
  await store.addFiles([{ name: 'a.jpg' }]);
  const [card] = cards(store);
  assert.equal(card.stage, 'upload-failed');
  await store.reupload(card.key);
  assert.equal(fake.commits.length, 1);
  assert.equal(card.stage, 'queue');
  assert.deepEqual(cards(store).map((c) => c.name), ['a']);
});

test('a look at the queue recognises an upload that landed although its answer was lost', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  fake.failCommit(new GitHubError('인터넷 연결을 확인해 주세요', 0), { landed: true });
  await store.addFiles([{ name: 'a.jpg' }]);
  await store.refresh();
  const [card] = cards(store);
  assert.equal(cards(store).length, 1, 'no second card for the same upload');
  assert.equal(card.stage, 'queue');
  assert.equal(card.copies, null);
  assert.equal(fake.commits.length, 1);
});

/** What the worker's publish does to queue/<name>: its blobs go on show, photos.json gains it, the folder goes. */
function publishAsWorker(fake, name) {
  fake.put(`photos/full/${name}.jpg`, fake.value(`queue/${name}/full.jpg`));
  fake.put(`photos/thumb/${name}.jpg`, fake.value(`queue/${name}/thumb.jpg`));
  const data = fake.json(DATA);
  data.rooms[1].photos.push({ file: name, title: '초안', alt: '초안 설명' });
  fake.put(DATA, serialize(data));
  fake.removeDir(`queue/${name}`);
}

test('다시 올리기 after a lost answer never publishes the photo twice: the AI had published it already', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  await store.load();
  fake.failCommit(new GitHubError('인터넷 연결을 확인해 주세요', 0), { landed: true }); // PATCH answer lost, then offline
  await store.addFiles([{ name: 'a.jpg' }]);
  const [card] = cards(store);
  assert.equal(card.stage, 'upload-failed');
  assert.equal(store.watching(), true, 'it may be in the queue: the page keeps looking');
  publishAsWorker(fake, 'a'); // before any look saw the folder
  await store.reupload(card.key);
  assert.deepEqual(messages(fake), ['대기열: a 올림'], 'no a-2');
  assert.equal(card.stage, 'published');
  assert.equal(card.copies, null);
  assert.equal(store.watching(), false);
});

test('다시 올리기 after a failed commit moves to the next name when a different photo is on show under its name', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  await store.load();
  fake.failCommit(new GitHubError('인터넷 연결을 확인해 주세요', 0)); // did not land, but the page cannot tell
  await store.addFiles([{ name: 'a.jpg' }]);
  const [card] = cards(store);
  fake.put('photos/full/a.jpg', new Blob(['another photo, published from the PC']));
  const data = fake.json(DATA);
  data.rooms[0].photos.push({ file: 'a', title: '다른 사진', alt: '다른 사진' });
  fake.put(DATA, serialize(data));
  await store.reupload(card.key);
  assert.deepEqual(messages(fake), ['대기열: a-2 올림']);
  assert.deepEqual([card.name, card.stage], ['a-2', 'queue']);
});

// --- the queue as shown ------------------------------------------------------------------------

test('item.json is read again only for folders whose sha changed', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x'), ...queued('y') });
  const store = makeStore(fake);
  await store.load();
  const reads = () => fake.calls.filter(([m, p]) => m === 'getJSON' && p.endsWith('/item.json')).map(([, p]) => p);
  assert.deepEqual(reads(), ['queue/x/item.json', 'queue/y/item.json']);
  await store.refresh();
  assert.equal(reads().length, 2, 'nothing changed, nothing read');
  fake.put('queue/y/item.json', { ...fake.json('queue/y/item.json'), status: 'pc', by: 'pc', claimed_at: '2026-10-07T09:00:05Z' });
  await store.refresh();
  assert.deepEqual(reads().slice(2), ['queue/y/item.json']);
  assert.equal(byName(store, 'y').item.status, 'pc');
  assert.equal(byName(store, 'x').item.status, 'waiting');
});

test('cards are shown oldest upload first, and items found later go after them', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('b', { uploaded_at: '2026-10-07T08:00:00Z' }), ...queued('a', { uploaded_at: '2026-10-07T08:30:00Z' }) });
  const store = makeStore(fake);
  await store.load();
  fake.put('queue/0/item.json', newItem({ name: '0', kind: 'new', grayscale: false, now: NOW - 3_600_000 }));
  const { added } = await store.refresh();
  assert.equal(added, true);
  assert.deepEqual(cards(store).map((c) => c.name), ['b', 'a', '0']);
});

test('a new item that leaves the queue is published when photos.json has it, else just removed', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x'), ...queued('y'), ...queued('z') });
  const store = makeStore(fake);
  await store.load();
  const photosReads = () => fake.count('getJSON', DATA);
  const before = photosReads();
  await store.refresh();
  assert.equal(photosReads(), before, 'photos.json is not read while nothing left');

  // the worker published x (one commit: photos.json gains it, the folder goes); y was thrown away elsewhere
  const data = fake.json(DATA);
  data.rooms[1].photos.push({ file: 'x', title: '초안', alt: '초안 설명' });
  fake.put(DATA, serialize(data));
  fake.removeDir('queue/x');
  fake.removeDir('queue/y');
  const { published, removed } = await store.refresh();
  assert.deepEqual(published.map((c) => c.name), ['x']);
  assert.deepEqual(removed.map((c) => c.name), ['y']);
  assert.equal(photosReads(), before + 1);
  const listing = fake.calls.findLastIndex(([m, p]) => m === 'listDir' && p === 'queue');
  const photos = fake.calls.findLastIndex(([m, p]) => m === 'getJSON' && p === DATA);
  assert.ok(photos > listing, 'photos.json is read after the listing that missed the item');
  assert.equal(byName(store, 'x').stage, 'published');
  assert.equal(byName(store, 'y'), undefined);
  assert.equal(store.watching(), true, 'z still waits');

  store.forget(byName(store, 'x').key);
  assert.deepEqual(cards(store).map((c) => c.name), ['z']);
  fake.removeDir('queue/z');
  await store.refresh();
  assert.equal(store.watching(), false);
});

test('only new items and failed redrafts are shown; a shown redraft stays until it leaves the queue', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('n'), ...redraftQueued('a1'), ...redraftQueued('a2', { status: 'failed', error: 'AI 오류' }) });
  const store = makeStore(fake);
  await store.load();
  assert.deepEqual(cards(store).map((c) => c.name), ['n', 'redraft-a2']);
  const card = byName(store, 'redraft-a2');
  await store.retry(card.key);
  assert.equal(card.item.status, 'waiting');
  await store.refresh();
  assert.deepEqual(cards(store).map((c) => c.name), ['n', 'redraft-a2'], 'still shown after its retry');

  const before = fake.count('getJSON', DATA);
  fake.removeDir('queue/redraft-a2');
  const { published, removed } = await store.refresh();
  assert.deepEqual([published.length, removed.map((c) => c.name)], [0, ['redraft-a2']]);
  assert.equal(fake.count('getJSON', DATA), before, 'a redraft is never "published" here');
});

test('the page keeps looking while queue/ holds anything, also a redraft it does not list (it may yet fail)', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  await store.load();
  assert.equal(store.watching(), false);
  fake.put('queue/redraft-a1/item.json', newItem({ name: 'redraft-a1', kind: 'redraft', grayscale: false, now: NOW, file: 'a1' }));
  await store.refresh();
  assert.equal(store.cards.size, 0);
  assert.equal(store.watching(), true);
  fake.put('queue/redraft-a1/item.json', { ...fake.json('queue/redraft-a1/item.json'), status: 'failed', error: 'AI 오류' });
  await store.refresh();
  assert.deepEqual(cards(store).map((c) => [c.name, c.item.status]), [['redraft-a1', 'failed']]);
  fake.remove('queue/redraft-a1/item.json');
  await store.refresh();
  assert.equal(store.watching(), false);
});

// --- actions -----------------------------------------------------------------------------------

const FAILED = { status: 'failed', by: 'pc', claimed_at: '2026-10-07T08:59:00Z', error: 'AI 오류' };

test('다시 시도 is one commit that sets the item at the base commit back to waiting', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', FAILED) });
  const store = makeStore(fake);
  await store.load();
  const card = byName(store, 'x');
  fake.put('queue/x/item.json', { ...fake.json('queue/x/item.json'), error: '다른 오류' }); // failed again elsewhere
  const atBase = fake.json('queue/x/item.json');
  assert.equal(await store.retry(card.key), true);
  assert.deepEqual(fake.commits, [{ message: '대기열: x 다시 시도', files: { 'queue/x/item.json': toWaiting(atBase) } }]);
  assert.deepEqual(fake.json('queue/x/item.json'), toWaiting(atBase));
  assert.deepEqual(card.item, toWaiting(atBase));
});

test('다시 시도 commits nothing when the item was retried elsewhere, replaced, or deleted (it never comes back)', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', FAILED), ...queued('y', FAILED), ...queued('z', FAILED) });
  const store = makeStore(fake);
  await store.load();

  fake.put('queue/x/item.json', toWaiting(fake.json('queue/x/item.json'))); // someone else retried it
  assert.equal(await store.retry(byName(store, 'x').key), false);
  assert.equal(byName(store, 'x').item.status, 'waiting', 'the card shows what it is now');

  fake.put('queue/y/item.json', { ...fake.json('queue/y/item.json'), uploaded_at: '2026-10-07T09:30:00Z' }); // another upload, same name
  assert.equal(await store.retry(byName(store, 'y').key), false);
  assert.equal(fake.json('queue/y/item.json').status, 'failed');

  fake.removeDir('queue/z'); // thrown away elsewhere
  assert.equal(await store.retry(byName(store, 'z').key), false);
  assert.equal(fake.has('queue/z/item.json'), false, 'a deleted item is not recreated');
  assert.equal(fake.commits.length, 0);
});

test('버리기 deletes every file of the folder in one commit, and is refused while an AI works on the item', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x'), ...queued('y') });
  const store = makeStore(fake);
  await store.load();
  await store.discard(byName(store, 'x').key);
  assert.deepEqual(fake.commits, [{ message: '대기열: x 버림',
    files: { 'queue/x/full.jpg': null, 'queue/x/item.json': null, 'queue/x/thumb.jpg': null } }]);
  assert.equal(byName(store, 'x'), undefined);

  fake.put('queue/y/item.json', { ...fake.json('queue/y/item.json'), status: 'pc', by: 'pc', claimed_at: '2026-10-07T09:00:05Z' });
  const y = byName(store, 'y');
  await assert.rejects(store.discard(y.key), (error) => error.message === '지금은 버릴 수 없어요. AI가 보는 중이에요');
  assert.equal(fake.commits.length, 1);
  assert.equal(y.item.status, 'pc');
  assert.ok(fake.has('queue/y/full.jpg'));
});

test('버리기 works for a claim gone stale (a worker that stopped), checked again at the base commit', async () => {
  const stale = { status: 'pc', by: 'pc', claimed_at: '2026-10-07T08:49:59Z' }; // 10 min 1 s before NOW
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', stale), ...queued('y', stale), ...queued('z', { ...stale, claimed_at: '2026-10-07T08:50:00Z' }) });
  const store = makeStore(fake);
  await store.load();
  assert.ok(await store.discard(byName(store, 'x').key));
  assert.deepEqual(fake.commits, [{ message: '대기열: x 버림',
    files: { 'queue/x/full.jpg': null, 'queue/x/item.json': null, 'queue/x/thumb.jpg': null } }]);

  // y: the worker claimed it afresh meanwhile; at the base commit it is not stale any more
  fake.put('queue/y/item.json', { ...fake.json('queue/y/item.json'), by: 'github', status: 'github', claimed_at: '2026-10-07T08:59:30Z' });
  await assert.rejects(store.discard(byName(store, 'y').key), (error) => error.message === '지금은 버릴 수 없어요. AI가 보는 중이에요');
  assert.equal(byName(store, 'y').item.status, 'github');

  // z: exactly 10 minutes is not stale yet
  assert.equal(await store.discard(byName(store, 'z').key), null);
  assert.equal(fake.commits.length, 1);
  assert.ok(fake.has('queue/y/full.jpg') && fake.has('queue/z/full.jpg'));
});

test('a failed redraft can be thrown away: only its item.json goes', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...redraftQueued('a2', { status: 'failed', error: 'AI 오류' }) });
  const store = makeStore(fake);
  await store.load();
  const card = await store.discard(byName(store, 'redraft-a2').key);
  assert.equal(card.item.file, 'a2');
  assert.deepEqual(fake.commits, [{ message: '대기열: redraft-a2 버림', files: { 'queue/redraft-a2/item.json': null } }]);
});

test('버리기 on a stale card never deletes another upload that took the same name', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', FAILED) });
  const store = makeStore(fake);
  await store.load();
  const stale = byName(store, 'x');
  // another device threw x away and uploaded a different photo that got the name x again
  fake.removeDir('queue/x');
  Object.entries(queued('x', { uploaded_at: '2026-10-07T09:30:00Z' })).forEach(([path, value]) => fake.put(path, value));
  assert.equal(await store.discard(stale.key), null);
  assert.equal(fake.commits.length, 0);
  assert.equal(fake.json('queue/x/item.json').uploaded_at, '2026-10-07T09:30:00Z');
});

test('a folder whose item.json is missing or not JSON is listed as such and can be thrown away', async () => {
  const fake = fakeGitHub({
    [DATA]: site(), ...queued('ok'),
    'queue/lone/full.jpg': new Blob(['full']),
    'queue/bad/item.json': '{ not json', 'queue/bad/thumb.jpg': new Blob(['thumb']),
  });
  const store = makeStore(fake);
  await store.load();
  assert.deepEqual(cards(store).map((c) => [c.name, c.broken]), [['ok', false], ['bad', true], ['lone', true]]);
  assert.equal(await store.thumbOf(byName(store, 'bad')), null);
  await store.discard(byName(store, 'bad').key);
  await store.discard(byName(store, 'lone').key);
  assert.deepEqual(fake.commits, [
    { message: '대기열: bad 버림', files: { 'queue/bad/item.json': null, 'queue/bad/thumb.jpg': null } },
    { message: '대기열: lone 버림', files: { 'queue/lone/full.jpg': null } },
  ]);
  assert.deepEqual(cards(store).map((c) => c.name), ['ok']);
});

test('a look replaces a card whose folder now holds another upload, or became unreadable', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', FAILED), ...queued('y') });
  const store = makeStore(fake);
  await store.load();
  const [oldX, oldY] = [byName(store, 'x'), byName(store, 'y')];
  fake.put('queue/x/item.json', { ...fake.json('queue/x/item.json'), uploaded_at: '2026-10-07T09:30:00Z', status: 'waiting' });
  fake.put('queue/y/item.json', '<html>');
  const { removed } = await store.refresh();
  assert.deepEqual(removed.map((c) => c.key).sort(), [oldX.key, oldY.key].sort());
  const [newX, newY] = [byName(store, 'x'), byName(store, 'y')];
  assert.notEqual(newX.key, oldX.key);
  assert.equal(newX.item.uploaded_at, '2026-10-07T09:30:00Z');
  assert.equal(newY.broken, true);
  fake.put('queue/y/item.json', newItem({ name: 'y', kind: 'new', grayscale: false, now: NOW }));
  await store.refresh();
  assert.equal(byName(store, 'y').broken, false, 'readable again: a normal card');
});

test('직접 입력해서 게시: one commit reusing the queue blobs, photos.json without ai, the queue folder gone', async () => {
  const failed = { status: 'failed', error: 'AI 오류', date: '2025.11.30', camera: 'TestCam · f/2 · 1/60s · ISO 100' };
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', failed) });
  const store = makeStore(fake);
  await store.load();
  const card = byName(store, 'x');
  const full = await fake.sha('queue/x/full.jpg');
  const thumb = await fake.sha('queue/x/thumb.jpg');
  const { sha, why } = await store.publish(card.key, { room: 'river', title: '  다리 위 \n', alt: ' 다리 위에서 본 강 ' });
  assert.ok(sha);
  assert.equal(why, null);

  const expected = site();
  expected.rooms[0].photos.push({ file: 'x', title: '다리 위', alt: '다리 위에서 본 강', date: failed.date, camera: failed.camera });
  assert.equal(fake.commits.length, 1);
  assert.equal(fake.commits[0].message, '사진 게시: 다리 위');
  assert.deepEqual(fake.commits[0].files, {
    'photos/full/x.jpg': { sha: full },
    'photos/thumb/x.jpg': { sha: thumb },
    [DATA]: serialize(expected),
    'queue/x/full.jpg': null,
    'queue/x/item.json': null,
    'queue/x/thumb.jpg': null,
  });
  assert.equal(fake.value(DATA), serialize(expected));
  assert.equal(await fake.value('photos/full/x.jpg').text(), 'full of x');
  assert.equal(card.stage, 'published');
  assert.deepEqual(store.data, expected);
});

test('직접 입력해서 게시 without a date or camera leaves them out', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', { status: 'failed', error: 'AI 오류' }) });
  const store = makeStore(fake);
  await store.load();
  await store.publish(byName(store, 'x').key, { room: 'night', title: '골목', alt: '가로등 아래 골목' });
  assert.deepEqual(fake.json(DATA).rooms[1].photos.at(-1), { file: 'x', title: '골목', alt: '가로등 아래 골목' });
});

test('직접 입력해서 게시 refuses empty text before anything is sent, and an unknown room before anything is written', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', { status: 'failed', error: 'AI 오류' }) });
  const store = makeStore(fake);
  await store.load();
  const card = byName(store, 'x');
  const calls = fake.calls.length;
  await assert.rejects(store.publish(card.key, { room: 'river', title: '   ', alt: '설명' }),
    (error) => error instanceof GalleryError && error.message === '제목과 설명을 채워 주세요');
  await assert.rejects(store.publish(card.key, { room: 'river', title: '제목', alt: '' }), GalleryError);
  assert.equal(fake.calls.length, calls);
  await assert.rejects(store.publish(card.key, { room: 'nowhere', title: '제목', alt: '설명' }),
    (error) => error instanceof GalleryError && error.message === '없는 방이에요: nowhere');
  assert.equal(fake.commits.length, 0);
  assert.equal(card.stage, 'queue');
});

test('직접 입력해서 게시 on a stale card publishes nothing: another upload took the name, or the item moved on', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', FAILED), ...queued('y', FAILED) });
  const store = makeStore(fake);
  await store.load();
  const fields = { room: 'river', title: '옛 제목', alt: '옛 설명' };
  // device B threw x away and uploaded a different photo that got the name x again
  fake.removeDir('queue/x');
  Object.entries(queued('x', { ...FAILED, uploaded_at: '2026-10-07T09:30:00Z' })).forEach(([path, value]) => fake.put(path, value));
  assert.deepEqual(await store.publish(byName(store, 'x').key, fields), { sha: null, why: 'changed' });
  // y was retried elsewhere: an AI is about to write it
  fake.put('queue/y/item.json', toWaiting(fake.json('queue/y/item.json')));
  assert.deepEqual(await store.publish(byName(store, 'y').key, fields), { sha: null, why: 'changed' });
  assert.equal(fake.commits.length, 0);
  assert.deepEqual(fake.json(DATA), site());
});

test('직접 입력해서 게시 after the AI published it first says so and commits nothing', async () => {
  const fake = fakeGitHub({ [DATA]: site(), ...queued('x', FAILED) });
  const store = makeStore(fake);
  await store.load();
  const card = byName(store, 'x');
  const data = fake.json(DATA);
  data.rooms[1].photos.push({ file: 'x', title: '초안', alt: '초안 설명' });
  fake.put(DATA, serialize(data));
  fake.removeDir('queue/x');
  assert.deepEqual(await store.publish(card.key, { room: 'river', title: '제목', alt: '설명' }), { sha: null, why: 'published' });
  assert.equal(card.stage, 'published');
  assert.equal(fake.commits.length, 0);
});

// --- order ---------------------------------------------------------------------------------------

test('a look asked for while an upload commits waits for that commit', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const store = makeStore(fake);
  await store.load();
  const release = fake.holdCommit();
  const upload = store.addFiles([{ name: 'a.jpg' }]);
  while (fake.count('commitFiles') === 0) await tick();
  const listings = fake.count('listDir', 'queue');
  const look = store.refresh();
  for (let i = 0; i < 5; i++) await tick();
  assert.equal(fake.count('listDir', 'queue'), listings, 'no listing while the commit is open');
  release();
  await upload;
  await look;
  assert.equal(fake.count('listDir', 'queue'), listings + 1);
  assert.equal(byName(store, 'a').stage, 'queue', 'the look after the commit keeps the new card');
});

test('close() stops a batch and lets go of every card', async () => {
  const fake = fakeGitHub({ [DATA]: site() });
  const process = fakeProcess();
  const store = makeStore(fake, process);
  const done = store.addFiles([{ name: 'a.jpg' }, { name: 'b.jpg' }]);
  while (process.events.length === 0) await tick();
  store.close(); // locked while the first photo is being processed
  await done;
  assert.equal(store.cards.size, 0);
  assert.deepEqual(process.events, ['process a.jpg']);
  assert.deepEqual(fake.calls, [], 'nothing is read or sent after the lock');
});
