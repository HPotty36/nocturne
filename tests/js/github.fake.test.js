// The GitHub client over real HTTP against tests/fake_github.py, which this file starts (py tests/fake_github.py --port 0)
// and stops. The canned-answer tests are in github.test.js.
import { test, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { GitHub, GitHubError, AuthError, ConflictError, CommitError, dumpJSON } from '../../admin/lib/github.js';

const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const TOKEN = 'test-token';
const NO_WAIT = async () => {}; // for tests that make many repeats on purpose

let server;
let base;

/** Starts the fake and resolves with its URL (the first line it prints). */
function startFake() {
  return new Promise((resolve, reject) => {
    const child = spawn('py', ['tests/fake_github.py', '--port', '0'], { cwd: ROOT, stdio: ['ignore', 'pipe', 'inherit'] });
    let seen = '';
    const fail = (error) => {
      clearTimeout(timer);
      stopFake(child);
      reject(error);
    };
    const timer = setTimeout(() => fail(new Error('fake_github.py printed no URL within 20 s')), 20_000);
    child.once('error', fail);
    child.once('exit', (code) => fail(new Error(`fake_github.py exited with ${code} before it printed its URL`)));
    child.stdout.on('data', (chunk) => {
      seen += chunk;
      const line = seen.split(/\r?\n/)[0];
      if (!seen.includes('\n')) return;
      clearTimeout(timer);
      child.removeAllListeners('exit');
      child.removeListener('error', fail);
      resolve({ child, url: line.trim() });
    });
  });
}

/** Kills the fake (the py launcher takes its python child down with it, so no server is left running). */
function stopFake(child) {
  if (child.exitCode === null) child.kill();
  child.stdout?.destroy();
}

before(async () => {
  const started = await startFake();
  server = started.child;
  base = started.url;
});

after(() => {
  if (server) stopFake(server);
});

// --- plumbing for the tests themselves: plain HTTP to the fake, repeated when this PC resets a loopback connection ---

async function http(method, path, { body, auth = true } = {}) {
  for (let tried = 0; ; tried++) {
    try {
      const response = await fetch(base + path, {
        method,
        headers: { ...(auth ? { Authorization: `Bearer ${TOKEN}` } : {}), 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      return { status: response.status, json: await response.json() };
    } catch (error) {
      if (tried >= 5) throw error;
      await new Promise((done) => setTimeout(done, 50));
    }
  }
}

let repoCount = 0;
/** A repository of its own, so tests cannot disturb each other; `files` ({path: string | Uint8Array}) is its first commit(s). */
async function repoWith(files) {
  const repo = `o/repo${++repoCount}`;
  for (const [path, content] of Object.entries(files)) {
    const bytes = typeof content === 'string' ? Buffer.from(content) : content;
    const { status } = await http('PUT', '/_fake/file', { auth: false, body: { repo, path, content_base64: Buffer.from(bytes).toString('base64') } });
    assert.equal(status, 200);
  }
  return repo;
}

const otherCommit = (repo, path, content) => http('PUT', '/_fake/file', { auth: false, body: { repo, path, content_base64: Buffer.from(content).toString('base64') } });
const head = async (repo) => (await http('GET', `/repos/${repo}/git/ref/heads/main`)).json.object.sha;

/** Main's commits from the head down, along first parents: [{sha, message, parents}]. */
async function history(repo) {
  const commits = [];
  for (let sha = await head(repo); sha; ) {
    const { json } = await http('GET', `/repos/${repo}/git/commits/${sha}`);
    commits.push({ sha, message: json.message, parents: json.parents.map((p) => p.sha) });
    sha = json.parents[0]?.sha;
  }
  return commits;
}

/** The bytes of a file on main as the fake holds it (read with plain HTTP, not through the client), or null. */
async function onMain(repo, path) {
  const { status, json } = await http('GET', `/repos/${repo}/contents/${path.split('/').map(encodeURIComponent).join('/')}`);
  return status === 404 ? null : Buffer.from(json.content, 'base64');
}

const client = (options) => new GitHub({ token: TOKEN, api: base, ...options });

// --- tests -----------------------------------------------------------------------------------------------

test('canPush is true for a repository the token can write to', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  assert.equal(await client().canPush(repo), true);
  assert.equal(await client().canPush('o/does-not-exist'), false);
  await assert.rejects(new GitHub({ token: 'wrong', api: base }).canPush(repo), (e) => e instanceof AuthError && e.message === '토큰을 다시 넣어 주세요' && e.status === 401);
});

test('commitFiles adds a file, copies a blob by its sha and deletes a file, in one commit', async () => {
  const everyByte = Uint8Array.from({ length: 256 }, (_, i) => i);
  const repo = await repoWith({ 'b.txt': 'bee', 'orig/photo.bin': everyByte });
  const earlier = await history(repo);
  const posted = [];
  const gh = client({
    fetch: (url, init) => {
      if (init?.method === 'POST' && url.endsWith('/git/blobs')) posted.push(JSON.parse(init.body).content);
      return fetch(url, init);
    },
  });
  const known = (await gh.listDir(repo, 'orig')).find((entry) => entry.name === 'photo.bin').sha;

  const commit = await gh.commitFiles(repo, {
    message: '사진 게시: 강',
    build: async () => ({ files: { 'a.txt': 'ay\n', 'photos/한글 1.bin': { sha: known }, 'b.txt': null } }),
  });

  const later = await history(repo);
  assert.equal(commit, later[0].sha);
  assert.deepEqual(later.slice(1), earlier); // exactly one new commit on top of what was there
  assert.equal(later[0].message, '사진 게시: 강');
  assert.deepEqual([...new Set(posted)], [Buffer.from('ay\n').toString('base64')]); // the copy made no blob of its own
  assert.equal((await onMain(repo, 'a.txt')).toString(), 'ay\n');
  assert.equal(await onMain(repo, 'b.txt'), null);
  assert.deepEqual([...await onMain(repo, 'photos/한글 1.bin')], [...everyByte]);
  assert.deepEqual([...(await gh.getFile(repo, 'orig/photo.bin')).bytes], [...everyByte]);
  const copy = (await gh.listDir(repo, 'photos')).find((entry) => entry.type === 'file');
  assert.deepEqual([copy.name, copy.sha], ['한글 1.bin', known]);
});

test('the commits land with the noreply identity as author and committer', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const gh = client();
  await gh.commitFiles(repo, { message: 'one commit', build: async () => ({ files: { 'b.txt': 'b' } }) });
  const viaCommits = (await http('GET', `/repos/${repo}/git/commits/${await head(repo)}`)).json;
  await gh.putJSON(repo, 'c.json', { c: 1 }, { message: 'contents write' });
  const viaContents = (await http('GET', `/repos/${repo}/git/commits/${await head(repo)}`)).json;
  const me = { name: 'HPotty36', email: '112685098+HPotty36@users.noreply.github.com' };
  for (const commit of [viaCommits, viaContents]) {
    assert.deepEqual([commit.author.name, commit.author.email], [me.name, me.email], commit.message);
    assert.deepEqual([commit.committer.name, commit.committer.email], [me.name, me.email], commit.message);
  }
});

test('commitFiles with nothing to change makes no commit', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const headBefore = await head(repo);
  assert.equal(await client().commitFiles(repo, { message: 'm', build: async () => ({ files: {} }) }), null);
  assert.equal(await head(repo), headBefore);
});

test('getJSON and putJSON round-trip the sha', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const gh = client();
  assert.equal(await gh.getJSON(repo, 'queue/k/item.json'), null);

  const first = await gh.putJSON(repo, 'queue/k/item.json', { status: 'pending', 메모: '밤' }, { message: '대기열: k' });
  const read = await gh.getJSON(repo, 'queue/k/item.json');
  assert.deepEqual(read, { data: { status: 'pending', 메모: '밤' }, sha: first });
  assert.equal((await gh.listDir(repo, 'queue/k')).find((e) => e.name === 'item.json').sha, first); // the sha GitHub itself reports
  assert.equal((await onMain(repo, 'queue/k/item.json')).toString(), dumpJSON(read.data));

  const second = await gh.putJSON(repo, 'queue/k/item.json', { status: 'pc' }, { sha: first, message: '대기열: k PC가 맡음' });
  assert.notEqual(second, first);
  assert.deepEqual(await gh.getJSON(repo, 'queue/k/item.json'), { data: { status: 'pc' }, sha: second });

  await assert.rejects(gh.putJSON(repo, 'queue/k/item.json', { status: 'github' }, { sha: first, message: 'm' }), (e) => e instanceof ConflictError && e.status === 409);
  await assert.rejects(gh.putJSON(repo, 'queue/k/item.json', { status: 'github' }, { message: 'm' }), (e) => e instanceof ConflictError && e.status === 422);
  assert.deepEqual((await gh.getJSON(repo, 'queue/k/item.json')).data, { status: 'pc' });
});

test('commitFiles reads again when the other side committed first', async () => {
  const repo = await repoWith({ 'src/photos.json': dumpJSON({ n: 1 }) });
  const gh = client();
  const seen = [];
  const commit = await gh.commitFiles(repo, {
    message: '사진 게시',
    build: async (readJSON) => {
      const { data } = await readJSON('src/photos.json');
      seen.push(data.n);
      if (seen.length === 1) { // someone else commits while this attempt is being built
        await otherCommit(repo, 'src/photos.json', dumpJSON({ n: 5 }));
        await otherCommit(repo, 'other.txt', 'o');
      }
      return { files: { 'src/photos.json': dumpJSON({ n: data.n + 1 }) } };
    },
  });
  assert.deepEqual(seen, [1, 5]); // built twice, and the second time on top of the other commits
  assert.equal(commit, await head(repo));
  assert.deepEqual(JSON.parse(await onMain(repo, 'src/photos.json')), { n: 6 });
  assert.equal((await onMain(repo, 'other.txt')).toString(), 'o');
});

test('commitFiles gives up with the message when main keeps moving', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  let built = 0;
  await assert.rejects(
    client().commitFiles(repo, {
      message: 'm',
      build: async () => {
        built++;
        await otherCommit(repo, 'x.txt', String(built)); // someone commits during every attempt
        return { files: { 'y.txt': 'y' } };
      },
    }),
    (e) => e instanceof CommitError && e.status === 422 && e.message === '다른 곳에서 동시에 바뀌었어요. 새로고침 후 다시 해 주세요',
  );
  assert.equal(built, 3);
  assert.equal(await onMain(repo, 'y.txt'), null);
});

test('a file above 1 MB goes in and comes out intact', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const big = Uint8Array.from({ length: 1_500_001 }, (_, i) => (i * 29 + (i >> 8)) % 256);
  const gh = client();
  await gh.commitFiles(repo, { message: 'big', build: async () => ({ files: { 'photos/full/big.jpg': big } }) });
  const file = await gh.getFile(repo, 'photos/full/big.jpg');
  assert.ok(Buffer.from(file.bytes).equals(big));
  assert.equal((await gh.listDir(repo, 'photos/full'))[0].sha, file.sha);
});

test('Blobs, as the photo processing gives them, go in and come out byte for byte', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const full = Uint8Array.from({ length: 300_003 }, (_, i) => (i * 17 + (i >> 9)) % 256); // all byte values, FF D8 included
  const thumb = full.subarray(7, 5007);
  const gh = client();
  await gh.commitFiles(repo, {
    message: '대기열: k',
    build: async () => ({
      files: {
        'queue/k/full.jpg': new Blob([full], { type: 'image/jpeg' }),
        'queue/k/thumb.jpg': new Blob([thumb], { type: 'image/jpeg' }),
        'queue/k/item.json': { status: 'pending' },
      },
    }),
  });
  assert.ok((await onMain(repo, 'queue/k/full.jpg')).equals(full));
  assert.ok((await onMain(repo, 'queue/k/thumb.jpg')).equals(thumb));
  assert.ok(Buffer.from((await gh.getFile(repo, 'queue/k/full.jpg')).bytes).equals(full));
  assert.deepEqual((await gh.getJSON(repo, 'queue/k/item.json')).data, { status: 'pending' });
  const listed = await gh.listDir(repo, 'queue/k');
  assert.deepEqual(listed.map((e) => e.name), ['full.jpg', 'item.json', 'thumb.jpg']);
});

test('latestRun reads the newest run of a workflow', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const gh = client();
  assert.equal(await gh.latestRun(repo, 'site.yml'), null);
  const run = { id: 7, status: 'in_progress', conclusion: null, html_url: 'https://example.test/run/7' };
  assert.equal((await http('PUT', '/_fake/run', { auth: false, body: { repo, workflow: 'site.yml', run } })).status, 200);
  assert.deepEqual(await gh.latestRun(repo, 'site.yml'), { status: 'in_progress', conclusion: null, url: 'https://example.test/run/7' });
  assert.equal(await gh.latestRun(repo, 'draft.yml'), null);
});

test('errors from the fake: a missing repository, an injected failure', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const gh = client();
  assert.deepEqual(await gh.listDir('o/does-not-exist', ''), []);
  assert.equal(await gh.getFile(repo, 'nope.txt'), null);
  await http('POST', '/_fake/fail_next', { auth: false, body: { method: 'POST', path_prefix: `/repos/${repo}/git/trees`, status: 500 } });
  await assert.rejects(
    gh.commitFiles(repo, { message: 'm', build: async () => ({ files: { 'x.txt': 'x' } }) }),
    (e) => e instanceof GitHubError && e.status === 500 && /^GitHub 500: /.test(e.message),
  );
  assert.equal(await onMain(repo, 'x.txt'), null); // the failed commit left main alone
});

// --- a PATCH that lands and loses its answer: this PC resets loopback connections, phones drop too -----------------

/**
 * A fetch where the first `count` PATCH requests get no answer (the connection "resets", as fetch rejects with a TypeError).
 * With `landing`, the first one reaches the fake before that; `between` runs once after it, before the client sends again.
 */
function losingPatchAnswers(count, { landing = false, between } = {}) {
  let patches = 0;
  return async (url, init) => {
    if (init?.method !== 'PATCH') return fetch(url, init);
    patches++;
    if (patches > count) return fetch(url, init);
    if (patches === 1) {
      if (landing) {
        for (let tried = 0; ; tried++) { // the request that must land is not itself allowed to fall to a real reset
          try {
            await (await fetch(url, init)).arrayBuffer();
            break;
          } catch (error) {
            if (tried >= 5) throw error;
          }
        }
      }
      if (between) await between();
    }
    throw new TypeError('fetch failed');
  };
}

/** Publishes y.txt with `message` through a client whose PATCH answers get lost as `fetch` says. */
async function publishThroughLosses(fetchImpl, repo, message) {
  const gh = client({ fetch: fetchImpl, sleep: NO_WAIT });
  let built = 0;
  const commit = await gh.commitFiles(repo, { message, build: async () => { built++; return { files: { 'y.txt': 'y' } }; } });
  const commits = await history(repo);
  return { commit, built, commits, index: commits.findIndex((c) => c.sha === commit), mine: commits.filter((c) => c.message === message).length };
}

test('a PATCH that landed and lost its answer is not built again', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  // The resend gets 422 because another commit came first, but ours is in main already.
  const result = await publishThroughLosses(losingPatchAnswers(1, { landing: true, between: () => otherCommit(repo, 'other.txt', 'o') }), repo, 'lost-a');
  assert.deepEqual([result.built, result.mine], [1, 1]);
  assert.ok(result.index > 0); // not the head: the other commit is above it
  assert.equal((await onMain(repo, 'y.txt')).toString(), 'y');
  assert.equal((await onMain(repo, 'other.txt')).toString(), 'o');
});

test('every PATCH try lost but the first landed', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const result = await publishThroughLosses(losingPatchAnswers(4, { landing: true }), repo, 'lost-b');
  assert.deepEqual([result.built, result.mine, result.index], [1, 1, 0]);
});

test('every PATCH try lost and another commit landed after ours', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const result = await publishThroughLosses(losingPatchAnswers(4, { landing: true, between: () => otherCommit(repo, 'other.txt', 'o') }), repo, 'lost-c');
  assert.deepEqual([result.built, result.mine], [1, 1]);
  assert.ok(result.index > 0);
});

test('our commit is found a few commits down', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const three = async () => { for (const name of ['o1', 'o2', 'o3']) await otherCommit(repo, name, 'o'); };
  const result = await publishThroughLosses(losingPatchAnswers(1, { landing: true, between: three }), repo, 'lost-d');
  assert.deepEqual([result.built, result.mine], [1, 1]);
  assert.ok(result.index >= 3);
});

test('a PATCH that never landed starts over on the new main', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const result = await publishThroughLosses(losingPatchAnswers(1, { between: () => otherCommit(repo, 'other.txt', 'o') }), repo, 'lost-e');
  assert.deepEqual([result.built, result.mine, result.index], [2, 1, 0]);
  assert.equal((await onMain(repo, 'other.txt')).toString(), 'o');
  assert.equal((await onMain(repo, 'y.txt')).toString(), 'y');
});

test('every PATCH try lost and none landed starts over', async () => {
  const repo = await repoWith({ 'a.txt': 'a' });
  const result = await publishThroughLosses(losingPatchAnswers(4), repo, 'lost-f');
  assert.deepEqual([result.built, result.mine, result.index], [2, 1, 0]);
});
