// The DOM-free part of the "전시 중" tab: which fields 저장 sends, and that reads and commits cannot overtake each other.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { editsFor, ExhibitStore } from '../../admin/views/exhibit.js';
import { GalleryError, serialize } from '../../admin/lib/gallery.js';

const ROOT = new URL('../../', import.meta.url);
const site = () => JSON.parse(readFileSync(new URL('tests/fixtures/site.json', ROOT), 'utf8'));
const REPO = 'HPotty36/nocturne';
const DATA = 'src/photos.json';
const tick = () => new Promise((done) => setTimeout(done, 0));

/**
 * An in-memory GitHub with the four calls the exhibit uses. A JSON file is kept as text, like on GitHub.
 * hold(kind) makes the next getJSON ('read') or the next commit ('commit') wait until released; a held read has
 * already taken its snapshot, so it answers with what the file held when the read began.
 */
function fakeGitHub(files) {
  const store = new Map(Object.entries(files).map(([path, value]) => [path, typeof value === 'string' ? value : `${JSON.stringify(value)}\n`]));
  const calls = [];
  const commits = [];
  const holds = { read: [], commit: [] };
  const gate = (kind) => (holds[kind].length ? holds[kind].shift() : Promise.resolve());
  const parsed = (path) => (store.has(path) ? JSON.parse(store.get(path)) : null);
  const gh = {
    async getJSON(repo, path) {
      calls.push(['getJSON', path]);
      const snapshot = parsed(path);
      await gate('read');
      return snapshot === null ? null : { data: snapshot, sha: 'x' };
    },
    async listDir(repo, path) {
      calls.push(['listDir', path]);
      const names = new Set([...store.keys()].filter((p) => p.startsWith(`${path}/`)).map((p) => p.slice(path.length + 1).split('/')[0]));
      return [...names].map((name) => ({ name, path: `${path}/${name}`, sha: 'x', type: 'dir' }));
    },
    async commitFiles(repo, { message, build }) {
      calls.push(['commitFiles', message]);
      const built = await build(async (path) => (store.has(path) ? { data: parsed(path) } : null));
      await gate('commit');
      if (!built) return null;
      for (const [path, change] of Object.entries(built.files)) {
        if (change === null) {
          assert.ok(store.has(path), `deleting a missing file: ${path}`); // GitHub answers 422
          store.delete(path);
        } else {
          store.set(path, typeof change === 'string' ? change : `${JSON.stringify(change, null, 2)}\n`);
        }
      }
      commits.push({ message, files: built.files });
      return `commit-${commits.length}`;
    },
  };
  return {
    gh,
    calls,
    commits,
    text: (path) => store.get(path),
    data: () => parsed(DATA),
    json: parsed,
    has: (path) => store.has(path),
    /** Change photos.json as another device would. */
    edit(change) {
      const data = parsed(DATA);
      change(data);
      store.set(DATA, serialize(data));
    },
    put: (path, value) => store.set(path, `${JSON.stringify(value)}\n`),
    remove: (path) => store.delete(path),
    hold(kind) {
      let release;
      holds[kind].push(new Promise((done) => { release = done; }));
      return release;
    },
  };
}

const photo = (data, file) => data.rooms.flatMap((r) => r.photos.map((p) => ({ ...p, room: r.id }))).find((p) => p.file === file);
const shownOf = (data, file) => {
  const p = photo(data, file);
  return { room: p.room, title: p.title, alt: p.alt };
};

async function opened(files = { [DATA]: site() }) {
  const fake = fakeGitHub(files);
  const store = new ExhibitStore(fake.gh, REPO);
  await store.load();
  return { fake, store };
}

// --- editsFor: only what the person changed ---------------------------------------------------

test('editsFor sends only the fields that differ from what the row showed', () => {
  const shown = { room: 'river', title: '다리 아래', alt: '다리 상판' };
  assert.deepEqual(editsFor(shown, { ...shown }), {});
  assert.deepEqual(editsFor(shown, { ...shown, title: '  새 제목 ' }), { title: '새 제목' });
  assert.deepEqual(editsFor(shown, { ...shown, room: 'night' }), { room: 'night' });
  assert.deepEqual(editsFor(shown, { ...shown, alt: '다리 상판   ' }), {}, 'spaces around the text are not a change');
  assert.deepEqual(editsFor(shown, { room: 'night', title: 'a', alt: 'b' }), { room: 'night', title: 'a', alt: 'b' });
  assert.deepEqual(editsFor(shown, { ...shown, title: '   ' }), { title: '' }, 'an emptied field is a change (refused later)');
});

test('store.edits checks the changes before anything is sent', async () => {
  const { fake, store } = await opened();
  const shown = shownOf(store.data, 'a2');
  assert.throws(() => store.edits('a2', shown, { ...shown, title: '  ' }), (e) => e instanceof GalleryError && e.message === '제목과 설명을 채워 주세요');
  assert.throws(() => store.edits('a2', shown, { ...shown, room: 'nowhere' }), GalleryError);
  assert.deepEqual(store.edits('a2', shown, { ...shown, alt: ' 새 설명 ' }), { alt: '새 설명' });
  assert.equal(fake.commits.length, 0);
});

// --- 저장 never overwrites what the person did not touch --------------------------------------

test('a stale tab that changes only the room keeps a title changed on another device', async () => {
  const { fake, store } = await opened();
  const shown = shownOf(store.data, 'n1'); // what the PC tab shows
  fake.edit((data) => { data.rooms[1].photos[0].title = '폰에서 고친 제목'; }); // the phone saved meanwhile
  const changes = store.edits('n1', shown, { ...shown, room: 'river' });
  assert.deepEqual(changes, { room: 'river' });
  await store.save('n1', changes);
  const now = photo(fake.data(), 'n1');
  assert.equal(now.room, 'river');
  assert.equal(now.title, '폰에서 고친 제목');
  assert.equal(now.alt, '빨간 공중전화 부스');
  assert.equal(fake.commits.length, 1);
  assert.equal(fake.commits[0].message, '사진 수정: 공중전화'); // the title as this page showed it
  assert.deepEqual(Object.keys(fake.commits[0].files), [DATA]);
  assert.equal(fake.text(DATA), serialize(fake.data()), 'canonical photos.json');
  assert.deepEqual(store.data, fake.data(), 'the page now knows the committed state');
});

test('a redraft that landed while the row was open is kept when only the description is saved', async () => {
  const { fake, store } = await opened();
  const shown = shownOf(store.data, 'a2');
  fake.edit((data) => { // the PC model rewrote both texts
    Object.assign(data.rooms[0].photos[1], { title: 'AI 제목', alt: 'AI 설명', ai: { model: 'gemma4:12b-it-qat', fields: ['title', 'alt'] } });
  });
  await store.save('a2', store.edits('a2', shown, { ...shown, alt: '사람이 쓴 설명' }));
  const now = photo(fake.data(), 'a2');
  assert.equal(now.title, 'AI 제목');
  assert.equal(now.alt, '사람이 쓴 설명');
  assert.deepEqual(now.ai, { model: 'gemma4:12b-it-qat', fields: ['title'] });
  assert.equal(fake.commits[0].message, '사진 수정: 다리 아래'); // the title as this page showed it
});

test('저장 with nothing changed still marks the AI text as seen (confidence goes), and commits nothing when there is nothing to change', async () => {
  const { fake, store } = await opened();
  const shown = shownOf(store.data, 'a2');
  const changes = store.edits('a2', shown, { ...shown });
  assert.deepEqual(changes, {});
  assert.equal(await store.save('a2', changes), 'commit-1');
  const now = photo(fake.data(), 'a2');
  assert.deepEqual(now.ai, { model: 'gemma4:e4b-it-qat', fields: ['title', 'alt'] });
  assert.equal(now.title, '다리 아래');
  assert.equal(fake.commits[0].message, '사진 수정: 다리 아래');
  assert.equal(await store.save('a2', {}), null);
  assert.equal(await store.save('a1', {}), null);
  assert.equal(fake.commits.length, 1);
});

// --- reads and commits in one line -------------------------------------------------------------

test('a reload asked for while a commit is on its way reads only after it, so it cannot bring back older data', async () => {
  const { fake, store } = await opened();
  const reads = () => fake.calls.filter(([kind]) => kind === 'getJSON').length;
  const before = reads();
  const release = fake.hold('commit');
  const saving = store.save('a1', { title: '새 제목' });
  const reloading = store.load(); // e.g. a poll that saw a redraft finish
  await tick();
  assert.equal(reads(), before, 'no read starts while the commit is open');
  release();
  await saving;
  await reloading;
  assert.equal(reads(), before + 1);
  assert.equal(photo(store.data, 'a1').title, '새 제목');
});

test('a reload whose read began before a commit lands first and the commit then builds on the newest state', async () => {
  const { fake, store } = await opened();
  const release = fake.hold('read');
  const reloading = store.load(); // its snapshot is taken now, before the save
  const saving = store.save('a1', { title: '새 제목' });
  await tick();
  assert.equal(fake.calls.filter(([kind]) => kind === 'commitFiles').length, 0, 'the commit waits for the read');
  release();
  await reloading;
  await saving;
  assert.equal(photo(store.data, 'a1').title, '새 제목', 'the older read did not overwrite the commit');
  assert.equal(photo(fake.data(), 'a1').title, '새 제목');
});

test('one failed step does not block the line', async () => {
  const { fake, store } = await opened();
  await assert.rejects(store.save('nope', { title: 'x' }), GalleryError);
  await store.save('a1', { title: '그다음' });
  assert.equal(photo(fake.data(), 'a1').title, '그다음');
});

// --- cover, delete, redraft --------------------------------------------------------------------

test('대표로 지정 is one commit with the photo title', async () => {
  const { fake, store } = await opened();
  assert.equal(await store.cover('n1'), 'commit-1');
  assert.equal(fake.data().cover, 'n1');
  assert.equal(fake.commits[0].message, '대표 사진 변경: 공중전화');
  assert.equal(store.data.cover, 'n1');
});

test('삭제 takes out the photo, both site copies and a redraft still waiting for it, in one commit', async () => {
  const files = {
    [DATA]: site(), 'photos/full/a2.jpg': 'jpeg', 'photos/thumb/a2.jpg': 'jpeg',
    'queue/redraft-a2/item.json': { name: 'redraft-a2', kind: 'redraft', file: 'a2', status: 'waiting' },
  };
  const { fake, store } = await opened(files);
  assert.ok(store.pending.has('a2'));
  await store.remove('a2');
  assert.equal(fake.commits.length, 1);
  assert.equal(fake.commits[0].message, '사진 삭제: 다리 아래');
  assert.deepEqual(fake.commits[0].files['photos/full/a2.jpg'], null);
  assert.deepEqual(fake.commits[0].files['photos/thumb/a2.jpg'], null);
  assert.deepEqual(fake.commits[0].files['queue/redraft-a2/item.json'], null);
  assert.equal(photo(fake.data(), 'a2'), undefined);
  assert.ok(!fake.has('queue/redraft-a2/item.json'));
  assert.ok(!store.pending.has('a2'), 'nothing left to poll for');
});

test('삭제 without a waiting redraft touches no queue file; the cover cannot be deleted', async () => {
  const files = { [DATA]: site(), 'photos/full/n1.jpg': 'jpeg', 'photos/thumb/n1.jpg': 'jpeg' };
  const { fake, store } = await opened(files);
  await store.remove('n1');
  assert.deepEqual(Object.keys(fake.commits[0].files).sort(), ['photos/full/n1.jpg', 'photos/thumb/n1.jpg', DATA]);
  await assert.rejects(store.remove('a1'), (e) => e instanceof GalleryError && e.message === '대표 사진은 다른 사진을 대표로 지정한 뒤 지울 수 있어요');
});

test('AI로 다시 쓰기 queues one redraft item, and only once', async () => {
  const { fake, store } = await opened();
  assert.equal(await store.redraft('n1'), 'commit-1');
  assert.equal(fake.commits[0].message, '대기열: redraft-n1 올림');
  const item = fake.json('queue/redraft-n1/item.json');
  assert.deepEqual(Object.keys(item), ['name', 'kind', 'file', 'uploaded_at', 'date', 'camera', 'grayscale', 'status', 'claimed_at', 'by', 'error']);
  assert.deepEqual({ ...item, uploaded_at: null }, {
    name: 'redraft-n1', kind: 'redraft', file: 'n1', uploaded_at: null, date: null, camera: null, grayscale: false,
    status: 'waiting', claimed_at: null, by: null, error: null,
  });
  assert.ok(store.pending.has('n1'));
  assert.equal(await store.redraft('n1'), null, 'already waiting: nothing committed');
  assert.equal(fake.commits.length, 1);
});

test('the queue check tells when a redraft is gone, and the reload names it', async () => {
  const { fake, store } = await opened();
  await store.redraft('n1');
  assert.deepEqual(await store.checkQueue(), { done: false, added: false });
  fake.put('queue/redraft-a2/item.json', { kind: 'redraft' }); // asked from another device
  assert.deepEqual(await store.checkQueue(), { done: false, added: true });
  assert.ok(store.pending.has('a2'));
  fake.remove('queue/redraft-n1/item.json'); // the worker published it
  fake.edit((data) => { data.rooms[1].photos[0].title = '초안'; });
  assert.deepEqual(await store.checkQueue(), { done: true, added: false });
  assert.deepEqual(await store.load(), ['n1']);
  assert.equal(photo(store.data, 'n1').title, '초안');
  assert.deepEqual([...store.pending], ['a2']);
});

test('a redraft thrown away in the queue is forgotten: no longer waited for, never reported as written', async () => {
  const { fake, store } = await opened();
  await store.redraft('n1');
  fake.remove('queue/redraft-n1/item.json'); // 버리기 in the "새 사진" tab
  store.forget('n1');
  assert.deepEqual(await store.checkQueue(), { done: false, added: false });
  assert.deepEqual(await store.load(), []);
  assert.equal(store.pending.size, 0);
});
