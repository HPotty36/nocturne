// The GitHub client against canned answers: a fake `fetch` that answers by method and path and records every call.
// The same client against a real HTTP server is in github.fake.test.js.
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { inspect } from 'node:util';
import { test, mock } from 'node:test';
import {
  GitHub, GitHubError, AuthError, ConflictError, CommitError, blobSha, dumpJSON,
  RETRY_DELAYS, TIMEOUT_MS, LARGE_TIMEOUT_MS, LARGE_BODY_BYTES,
} from '../../admin/lib/github.js';
import { COMMIT_AUTHOR } from '../../admin/config.js';

const TOKEN = 'ghp_secret-token-1234';
const ME = { name: 'HPotty36', email: '112685098+HPotty36@users.noreply.github.com' };
const R = 'o/r';
const P = `/repos/${R}`;
const REF = `${P}/git/ref/heads/main`;
const PATCH_REF = `${P}/git/refs/heads/main`;
const sha = (ch) => ch.repeat(40);
const [HEAD1, HEAD2, TREE1, TREE2, NEW_TREE, NEW1, NEW2] = ['1', '2', 'a', 'b', 'c', 'e', 'f'].map(sha);
const CONFLICT = '다른 곳에서 동시에 바뀌었어요. 새로고침 후 다시 해 주세요';
const AUTH = '토큰을 다시 넣어 주세요';
const OFFLINE = '인터넷 연결을 확인해 주세요';

const reply = ({ status = 200, json, body = '' }) => new Response(json !== undefined ? JSON.stringify(json) : body, { status });

/**
 * A `fetch` that answers by "METHOD /decoded/path" (query ignored, but recorded). A route is an answer {status?, json?,
 * body?}, a function (call) => answer, an Error (thrown, as a dropped connection is), {hang: true} (no answer until the
 * request's signal aborts), or {response} for a ready Response object; an array answers one request after the other and
 * then repeats its last element. No route: 404.
 */
function fakeFetch(routes) {
  const queues = new Map(Object.entries(routes).map(([key, route]) => [key, Array.isArray(route) ? [...route] : [route]]));
  const calls = [];
  async function fetch(url, init = {}) {
    const { pathname, search } = new URL(url);
    const call = {
      url, method: init.method ?? 'GET', path: decodeURIComponent(pathname), query: search, init,
      headers: init.headers ?? {}, body: init.body === undefined ? undefined : JSON.parse(init.body),
    };
    calls.push(call);
    const queue = queues.get(`${call.method} ${call.path}`);
    if (!queue) return reply({ status: 404, json: { message: `no route in test: ${call.method} ${call.path}` } });
    let answer = queue.length > 1 ? queue.shift() : queue[0];
    if (typeof answer === 'function') answer = await answer(call);
    if (answer instanceof Error) throw answer;
    if (answer.hang) return untilAborted(init.signal);
    return answer.response ?? reply(answer);
  }
  return { fetch, calls };
}

/** A promise that never settles by itself and rejects when the signal aborts, as a stalled connection does under a timeout. */
const untilAborted = (signal) => new Promise((_, reject) => {
  if (signal.aborted) reject(signal.reason);
  else signal.addEventListener('abort', () => reject(signal.reason), { once: true });
});

/** A client wired to fakeFetch(routes). Waiting between repeats is recorded in `waits`, not done. */
function client(routes, options = {}) {
  const { fetch, calls } = fakeFetch(routes);
  const waits = [];
  const gh = new GitHub({ token: TOKEN, api: 'http://github.test', fetch, sleep: async (ms) => { waits.push(ms); }, ...options });
  return { gh, calls, waits };
}

const steps = (calls) => calls.map((c) => `${c.method} ${c.path}`);
const dropped = () => new TypeError('fetch failed');
const fromBase64 = (text) => Buffer.from(text, 'base64');
const encodeUtf8 = (text) => new TextEncoder().encode(text);
/** Answers a blob POST with the sha git would give the posted bytes. */
const blobReply = async (call) => ({ status: 201, json: { sha: await blobSha(fromBase64(call.body.content)) } });

/** The answers of a repository at HEAD1 where a commit goes through; `extra` replaces or adds routes. */
function commitRoutes(extra = {}) {
  return {
    [`GET ${REF}`]: { json: { object: { sha: HEAD1 } } },
    [`GET ${P}/git/commits/${HEAD1}`]: { json: { sha: HEAD1, tree: { sha: TREE1 }, parents: [] } },
    [`POST ${P}/git/blobs`]: blobReply,
    [`POST ${P}/git/trees`]: { status: 201, json: { sha: NEW_TREE } },
    [`POST ${P}/git/commits`]: { status: 201, json: { sha: NEW1 } },
    [`PATCH ${PATCH_REF}`]: { json: { object: { sha: NEW1 } } },
    ...extra,
  };
}

const writeY = (counter) => async () => {
  counter.built = (counter.built ?? 0) + 1;
  return { files: { 'y.txt': 'y' } };
};

const isGitHubError = (status, message) => (e) => {
  assert.ok(e instanceof GitHubError, `not a GitHubError: ${e}`);
  assert.equal(e.status, status);
  if (message !== undefined) assert.equal(e.message, message);
  return true;
};

// --- the commit ------------------------------------------------------------------------------------

test('commitFiles makes one commit', async () => {
  const { gh, calls } = client(commitRoutes());
  const photos = '{\n  "rooms": [\n    {"id":"river","name":"강"}\n  ]\n}\n'; // pre-serialized, so written verbatim
  const jpeg = Uint8Array.from([0xff, 0xd8, 0x00, 0x01, 0xfe, 0xff]);
  const reused = sha('9');
  const commit = await gh.commitFiles(R, {
    message: '사진 게시: 강',
    build: async () => ({
      files: {
        'src/photos.json': photos,
        'photos/full/a.jpg': jpeg,
        'photos/thumb/a.jpg': { sha: reused },
        'queue/x/item.json': null,
      },
    }),
  });

  assert.equal(commit, NEW1);
  assert.deepEqual(steps(calls), [
    `GET ${REF}`, `GET ${P}/git/commits/${HEAD1}`,
    `POST ${P}/git/blobs`, `POST ${P}/git/blobs`, // only the two new contents; {sha} and null make no blob
    `POST ${P}/git/trees`, `POST ${P}/git/commits`, `PATCH ${PATCH_REF}`,
  ]);
  const [, , photosBlob, jpegBlob, tree, made, patch] = calls;
  assert.equal(photosBlob.body.encoding, 'base64');
  assert.equal(fromBase64(photosBlob.body.content).toString('utf8'), photos);
  assert.deepEqual([...fromBase64(jpegBlob.body.content)], [...jpeg]);
  const entry = (path, blob) => ({ path, mode: '100644', type: 'blob', sha: blob });
  assert.deepEqual(tree.body, {
    base_tree: TREE1,
    tree: [
      entry('src/photos.json', await blobSha(encodeUtf8(photos))),
      entry('photos/full/a.jpg', await blobSha(jpeg)),
      entry('photos/thumb/a.jpg', reused),
      entry('queue/x/item.json', null),
    ],
  });
  assert.deepEqual(made.body, { message: '사진 게시: 강', tree: NEW_TREE, parents: [HEAD1], author: ME, committer: ME });
  assert.deepEqual(patch.body, { sha: NEW1, force: false });
});

test('commits and contents writes name the noreply identity as author and committer, never the account profile', async () => {
  assert.deepEqual({ ...COMMIT_AUTHOR }, ME);
  assert.ok(Object.isFrozen(COMMIT_AUTHOR));
  const { gh, calls } = client(commitRoutes({ [`PUT ${P}/contents/a.json`]: { json: { content: { sha: sha('d') } } } }));
  await gh.commitFiles(R, { message: 'm', build: async () => ({ files: { 'y.txt': 'y' } }) });
  await gh.putJSON(R, 'a.json', { x: 1 }, { message: 'm' });
  const made = calls.find((c) => c.method === 'POST' && c.path === `${P}/git/commits`);
  const put = calls.find((c) => c.method === 'PUT');
  assert.deepEqual([made.body.author, made.body.committer, put.body.author, put.body.committer], [ME, ME, ME, ME]);
});

test('another identity can be given to the client', async () => {
  const other = { name: 'x', email: 'x@example.test' };
  const { gh, calls } = client({ [`PUT ${P}/contents/a.json`]: { json: { content: { sha: sha('d') } } } }, { author: other });
  await gh.putJSON(R, 'a.json', {}, { message: 'm' });
  assert.deepEqual([calls[0].body.author, calls[0].body.committer], [other, other]);
});

test('requests carry the token, the API version and no cache', async () => {
  const { gh, calls } = client(commitRoutes());
  await gh.commitFiles(R, { message: 'm', build: async () => ({ files: { 'a.txt': 'a' } }) });
  for (const call of calls) {
    assert.equal(call.headers.Authorization, `Bearer ${TOKEN}`, call.path);
    assert.equal(call.headers.Accept, 'application/vnd.github+json');
    assert.equal(call.headers['X-GitHub-Api-Version'], '2022-11-28');
    assert.equal(call.init.cache, 'no-store'); // GitHub lets a browser keep an answer for a minute; the ref must be fresh
    assert.ok(call.init.signal instanceof AbortSignal, call.path); // every try has its time limit
    assert.equal(call.headers['Content-Type'], call.method === 'GET' ? undefined : 'application/json');
  }
});

test('commitFiles re-reads after non-fast-forward', async () => {
  const { gh, calls } = client(commitRoutes({
    [`GET ${REF}`]: [{ json: { object: { sha: HEAD1 } } }, { json: { object: { sha: HEAD2 } } }],
    [`GET ${P}/git/commits/${HEAD2}`]: { json: { sha: HEAD2, tree: { sha: TREE2 }, parents: [{ sha: HEAD1 }] } },
    [`GET ${P}/contents/src/photos.json`]: (call) => ({ body: call.query === `?ref=${HEAD1}` ? '{"count": 1}' : '{"count": 2}' }),
    [`POST ${P}/git/commits`]: [{ status: 201, json: { sha: NEW1 } }, { status: 201, json: { sha: NEW2 } }],
    [`PATCH ${PATCH_REF}`]: [{ status: 422, json: { message: 'Update is not a fast forward' } }, { json: { object: { sha: NEW2 } } }],
  }));
  const seen = [];
  const commit = await gh.commitFiles(R, {
    message: 'm',
    build: async (readJSON) => {
      const current = await readJSON('src/photos.json');
      seen.push(current.data.count);
      return { files: { 'src/photos.json': dumpJSON({ count: current.data.count + 1 }) } };
    },
  });

  assert.equal(commit, NEW2);
  assert.deepEqual(seen, [1, 2]); // build ran twice, and the second read saw the other writer's change
  const attempt = (head, tree) => [
    `GET ${REF}`, `GET ${P}/git/commits/${head}`, `GET ${P}/contents/src/photos.json`,
    `POST ${P}/git/blobs`, `POST ${P}/git/trees`, `POST ${P}/git/commits`, `PATCH ${PATCH_REF}`,
  ];
  assert.deepEqual(steps(calls), [...attempt(HEAD1), ...attempt(HEAD2)]);
  assert.equal(calls[2].query, `?ref=${HEAD1}`); // readJSON reads at the commit of its own attempt
  assert.equal(calls[2].headers.Accept, 'application/vnd.github.raw');
  assert.equal(calls[9].query, `?ref=${HEAD2}`);
  assert.equal(calls[11].body.base_tree, TREE2);
  assert.deepEqual(calls[12].body.parents, [HEAD2]);
  assert.deepEqual(calls[13].body, { sha: NEW2, force: false });
  assert.equal(fromBase64(calls[10].body.content).toString('utf8'), dumpJSON({ count: 3 }));
});

test('readJSON gives {data} or null for a missing file', async () => {
  const { gh } = client(commitRoutes({ [`GET ${P}/contents/a.json`]: { body: '{"n": 1}' } }));
  const results = [];
  await gh.commitFiles(R, {
    message: 'm',
    build: async (readJSON) => {
      results.push(await readJSON('a.json'), await readJSON('nope.json'));
      return null;
    },
  });
  assert.deepEqual(results, [{ data: { n: 1 } }, null]);
});

test('failed commit leaves ref', async () => {
  const { gh, calls } = client(commitRoutes({ [`POST ${P}/git/trees`]: { status: 500, json: { message: 'boom' } } }));
  const counter = {};
  await assert.rejects(gh.commitFiles(R, { message: 'm', build: writeY(counter) }), (e) => {
    assert.ok(!(e instanceof CommitError));
    return isGitHubError(500, 'GitHub 500: boom')(e);
  });
  assert.equal(counter.built, 1); // an error is not a reason to build again
  assert.ok(!calls.some((c) => c.method === 'PATCH'));
});

test('three conflicts give CommitError', async () => {
  const { gh, calls } = client(commitRoutes({ [`PATCH ${PATCH_REF}`]: { status: 422, json: { message: 'Update is not a fast forward' } } }));
  const counter = {};
  await assert.rejects(gh.commitFiles(R, { message: 'm', build: writeY(counter) }), (e) => {
    assert.ok(e instanceof CommitError);
    return isGitHubError(422, CONFLICT)(e);
  });
  assert.equal(counter.built, 3);
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 3);
  assert.equal(calls.filter((c) => c.path === `${P}/git/commits/${HEAD1}`).length, 3); // no walk through history: no PATCH was resent
});

test('empty change makes no commit', async () => {
  for (const [label, built] of [['empty files', { files: {} }], ['nothing', undefined], ['null', null]]) {
    const { gh, calls } = client(commitRoutes());
    assert.equal(await gh.commitFiles(R, { message: 'm', build: async () => built }), null, label);
    assert.ok(!calls.some((c) => c.method !== 'GET'), label);
  }
});

test('commitFiles writes what it is given, and refuses what it cannot', async () => {
  const bytes = Uint8Array.from({ length: 256 }, (_, i) => i);
  const { gh, calls } = client(commitRoutes());
  await gh.commitFiles(R, {
    message: 'm',
    build: async () => ({
      files: {
        'data.json': { 이름: ['강', 1] }, // JSON data is written as dumpJSON
        'buffer.bin': bytes.buffer,
        'view.bin': new Uint8Array(bytes.buffer, 10, 20), // a view into a bigger buffer: only its own bytes
        'big.bin': Uint8Array.from({ length: 100_001 }, (_, i) => (i * 7) % 256), // more than one base64 chunk, not a multiple of 3
      },
    }),
  });
  const [data, buffer, view, big] = calls.filter((c) => c.path.endsWith('/git/blobs')).map((c) => fromBase64(c.body.content));
  assert.equal(data.toString('utf8'), '{\n  "이름": [\n    "강",\n    1\n  ]\n}\n');
  assert.deepEqual([...buffer], [...bytes]);
  assert.deepEqual([...view], [...bytes.subarray(10, 30)]);
  assert.equal(big.length, 100_001);
  assert.ok(big.every((b, i) => b === (i * 7) % 256));

  class Photo {}
  const refused = [
    ['number', 5], ['undefined', undefined], ['boolean', true], ['function', () => {}], ['symbol', Symbol('x')],
    ['Date', new Date(0)], ['Map', new Map([['a', 1]])], ['Set', new Set()], ['class instance', new Photo()], ['RegExp', /x/],
    ['{sha: undefined}', { sha: undefined }], ['{sha: ""}', { sha: '' }], ['{sha: 5}', { sha: 5 }],
  ];
  for (const [label, change] of refused) {
    const { gh: other, calls: otherCalls } = client(commitRoutes());
    // the good entry comes first: nothing of it may be sent either, because the bad one is found before the first blob
    const files = { 'good.txt': 'good', 'x.txt': change };
    await assert.rejects(other.commitFiles(R, { message: 'm', build: async () => ({ files }) }), TypeError, label);
    assert.deepEqual(steps(otherCalls), [`GET ${REF}`, `GET ${P}/git/commits/${HEAD1}`], label);
  }
  const { gh: wrong } = client(commitRoutes());
  await assert.rejects(wrong.commitFiles(R, { message: 'm', build: async () => ({ 'x.txt': 'x' }) }), TypeError); // the shape of Python's build
});

// --- a PATCH whose answer was lost ---------------------------------------------------------------------

const D = sha('d');
const NOT_FF = { status: 422, json: { message: 'Update is not a fast forward' } };
const patchTries = (...answers) => ({ [`PATCH ${PATCH_REF}`]: answers });

test('a lost PATCH that answers 422 on the resend is looked up in main', async () => {
  // Our commit NEW1 landed on the first try; then another commit D came on top, so the resend is not a fast-forward.
  const { gh, calls, waits } = client(commitRoutes({
    ...patchTries(dropped(), NOT_FF),
    [`GET ${REF}`]: [{ json: { object: { sha: HEAD1 } } }, { json: { object: { sha: D } } }],
    [`GET ${P}/git/commits/${D}`]: { json: { sha: D, tree: { sha: TREE2 }, parents: [{ sha: NEW1 }] } },
  }));
  const counter = {};
  assert.equal(await gh.commitFiles(R, { message: 'm', build: writeY(counter) }), NEW1);
  assert.equal(counter.built, 1); // not built, and not committed, again
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 2);
  assert.equal(calls.filter((c) => c.method === 'POST' && c.path.endsWith('/git/commits')).length, 1);
  assert.deepEqual(waits, [RETRY_DELAYS[0]]);
});

test('every PATCH try lost, and our commit is the head', async () => {
  const { gh, calls, waits } = client(commitRoutes({
    ...patchTries(dropped()),
    [`GET ${REF}`]: [{ json: { object: { sha: HEAD1 } } }, { json: { object: { sha: NEW1 } } }],
  }));
  const counter = {};
  assert.equal(await gh.commitFiles(R, { message: 'm', build: writeY(counter) }), NEW1);
  assert.equal(counter.built, 1);
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 4);
  assert.deepEqual(waits, RETRY_DELAYS);
  assert.ok(!calls.some((c) => c.path === `${P}/git/commits/${NEW1}`)); // found at the head, no history walk needed
});

test('every PATCH try lost, and our commit is a few commits down', async () => {
  const { gh } = client(commitRoutes({
    ...patchTries(dropped()),
    [`GET ${REF}`]: [{ json: { object: { sha: HEAD1 } } }, { json: { object: { sha: D } } }],
    [`GET ${P}/git/commits/${D}`]: { json: { sha: D, tree: { sha: TREE2 }, parents: [{ sha: HEAD2 }] } },
    [`GET ${P}/git/commits/${HEAD2}`]: { json: { sha: HEAD2, tree: { sha: TREE2 }, parents: [{ sha: NEW1 }] } },
  }));
  const counter = {};
  assert.equal(await gh.commitFiles(R, { message: 'm', build: writeY(counter) }), NEW1);
  assert.equal(counter.built, 1);
});

test('a lost PATCH that never landed starts over on the new main', async () => {
  // The resend says 422 because another commit (HEAD2) came first; ours is not in main, so everything is built again.
  const { gh, calls } = client(commitRoutes({
    ...patchTries(dropped(), NOT_FF, { json: { object: { sha: NEW2 } } }),
    [`GET ${REF}`]: [{ json: { object: { sha: HEAD1 } } }, { json: { object: { sha: HEAD2 } } }],
    [`GET ${P}/git/commits/${HEAD2}`]: { json: { sha: HEAD2, tree: { sha: TREE2 }, parents: [{ sha: HEAD1 }] } },
    [`POST ${P}/git/commits`]: [{ status: 201, json: { sha: NEW1 } }, { status: 201, json: { sha: NEW2 } }],
  }));
  const counter = {};
  assert.equal(await gh.commitFiles(R, { message: 'm', build: writeY(counter) }), NEW2);
  assert.equal(counter.built, 2);
  assert.deepEqual(calls.filter((c) => c.path.endsWith('/git/trees')).map((c) => c.body.base_tree), [TREE1, TREE2]);
  assert.deepEqual(calls.filter((c) => c.method === 'POST' && c.path.endsWith('/git/commits')).map((c) => c.body.parents), [[HEAD1], [HEAD2]]);
});

test('every PATCH try lost and none landed starts over', async () => {
  const { gh } = client(commitRoutes({
    ...patchTries(dropped(), dropped(), dropped(), dropped(), { json: { object: { sha: NEW2 } } }),
    [`POST ${P}/git/commits`]: [{ status: 201, json: { sha: NEW1 } }, { status: 201, json: { sha: NEW2 } }],
  }));
  const counter = {};
  assert.equal(await gh.commitFiles(R, { message: 'm', build: writeY(counter) }), NEW2);
  assert.equal(counter.built, 2);
});

test('the look-up goes at most 20 commits down', async () => {
  // Ours is 25 commits below the head: further than the look-up believes, so the commit starts over.
  const chain = Array.from({ length: 30 }, (_, i) => String(i).padStart(40, 'c'));
  const routes = commitRoutes({
    ...patchTries(dropped(), dropped(), dropped(), dropped(), { json: { object: { sha: NEW2 } } }),
    [`GET ${REF}`]: [{ json: { object: { sha: HEAD1 } } }, { json: { object: { sha: chain[0] } } }, { json: { object: { sha: HEAD1 } } }],
    [`POST ${P}/git/commits`]: [{ status: 201, json: { sha: NEW1 } }, { status: 201, json: { sha: NEW2 } }],
  });
  chain.forEach((c, i) => {
    routes[`GET ${P}/git/commits/${c}`] = { json: { sha: c, tree: { sha: TREE1 }, parents: [{ sha: i === 24 ? NEW1 : chain[i + 1] }] } };
  });
  const { gh, calls } = client(routes);
  const counter = {};
  assert.equal(await gh.commitFiles(R, { message: 'm', build: writeY(counter) }), NEW2);
  assert.equal(counter.built, 2);
  assert.equal(calls.filter((c) => chain.some((id) => c.path === `${P}/git/commits/${id}`)).length, 20);
});

// --- requests ----------------------------------------------------------------------------------------

test('a dropped connection is repeated with backoff', async () => {
  const { gh, calls, waits } = client({ [`GET ${P}`]: [dropped(), dropped(), dropped(), { json: { permissions: { push: true } } }] });
  assert.equal(await gh.canPush(R), true);
  assert.equal(calls.length, 4);
  assert.deepEqual(waits, [200, 500, 1000]);
  assert.deepEqual(RETRY_DELAYS, [200, 500, 1000]);
});

test('a response cut short is repeated too', async () => {
  const cut = { response: { status: 200, arrayBuffer: () => Promise.reject(new TypeError('terminated')) } };
  const { gh, calls } = client({ [`GET ${P}`]: [cut, { json: { permissions: { push: true } } }] });
  assert.equal(await gh.canPush(R), true);
  assert.equal(calls.length, 2);
});

test('giving up after the last repeat is a GitHubError with status 0', async () => {
  const { gh, calls, waits } = client({ [`GET ${P}`]: dropped() });
  await assert.rejects(gh.canPush(R), (e) => {
    assert.equal(e.cause.message, 'fetch failed');
    return isGitHubError(0, OFFLINE)(e);
  });
  assert.equal(calls.length, 4);
  assert.deepEqual(waits, [200, 500, 1000]);
});

test('HTTP errors are answers and are not repeated', async () => {
  const { gh, calls, waits } = client({ [`GET ${P}/contents/d`]: { status: 502, json: { message: 'bad gateway' } } });
  await assert.rejects(gh.listDir(R, 'd'), isGitHubError(502, 'GitHub 502: bad gateway'));
  assert.equal(calls.length, 1);
  assert.deepEqual(waits, []);
});

test('an error without a JSON body still gets a short message', async () => {
  const { gh } = client({ [`GET ${P}/contents/d`]: { status: 503, body: `  Service ${'x'.repeat(500)}  ` } });
  await assert.rejects(gh.listDir(R, 'd'), (e) => {
    assert.equal(e.status, 503);
    assert.ok(e.message.startsWith('GitHub 503: Service xxx'));
    assert.ok(e.message.length < 230);
    return true;
  });
});

test('a 200 that is not JSON is a GitHubError, not a SyntaxError', async () => {
  const { gh } = client({ [`GET ${P}`]: { body: '<html>wifi login</html>' } });
  await assert.rejects(gh.canPush(R), (e) => isGitHubError(200)(e) && e.cause instanceof SyntaxError);
});

test('fetch is called as a plain function', async () => {
  let receiver = 'never called';
  const fetch = function plain() {
    receiver = this; // a browser's window.fetch throws "Illegal invocation" for any other receiver
    return Promise.resolve(reply({ json: { permissions: { push: true } } }));
  };
  assert.equal(await new GitHub({ token: TOKEN, api: 'http://github.test', fetch }).canPush(R), true);
  assert.equal(receiver, undefined);
});

test('paths are encoded, the api base loses its trailing slash', async () => {
  const { gh, calls } = client({ [`GET ${P}/contents/photos/한글 1.jpg`]: { body: 'x' }, [`GET ${P}/contents/a.json`]: { body: '{}' } }, { api: 'http://github.test//' });
  await gh.getFile(R, 'photos/한글 1.jpg');
  await gh.getJSON(R, 'a.json', 'a/b');
  assert.equal(calls[0].url, 'http://github.test/repos/o/r/contents/photos/%ED%95%9C%EA%B8%80%201.jpg');
  assert.deepEqual(steps(calls), [`GET ${P}/contents/photos/한글 1.jpg`, `GET ${P}/contents/a.json`]);
  assert.equal(calls[1].query, '?ref=a%2Fb');
});

test('the token never shows in errors or when the client is printed', async () => {
  const failures = [
    client({ [`GET ${P}`]: { status: 401, json: { message: 'Bad credentials' } } }).gh.canPush(R),
    client({ [`GET ${P}`]: { status: 500, json: { message: 'boom' } } }).gh.canPush(R),
    client({ [`GET ${P}`]: dropped() }).gh.canPush(R),
  ];
  for (const failure of failures) {
    await assert.rejects(failure, (e) => {
      assert.ok(!`${e.message} ${e.stack} ${inspect(e)}`.includes(TOKEN));
      return true;
    });
  }
  const { gh } = client({});
  assert.ok(!`${inspect(gh, { showHidden: true })}${JSON.stringify(gh)}`.includes(TOKEN));
});

test('a token that is not plain printable ASCII is refused before anything is sent', () => {
  const bad = ['ghp_secret\nabc', 'ghp_secret abc', 'ghp_secret토큰', 'ghp_secret​abc', 'ghp_se\tcret', '', '   ', '\n', undefined, null, 5, {}];
  for (const token of bad) {
    let sent = 0;
    const fetch = async () => { sent++; return reply({ json: {} }); };
    assert.throws(() => new GitHub({ token, api: 'http://github.test', fetch }), (e) => {
      assert.ok(e instanceof AuthError, `${JSON.stringify(token)}`);
      assert.equal(e.name, 'AuthError');
      assert.equal(e.message, AUTH);
      assert.ok(e instanceof GitHubError);
      assert.equal(e.cause, undefined);
      const shown = `${e.message} ${e.stack} ${inspect(e, { showHidden: true, depth: 5 })}`;
      if (typeof token === 'string' && token.includes('secret')) assert.ok(!shown.includes('secret'), 'the token is in the error');
      return true;
    }, JSON.stringify(token));
    assert.equal(sent, 0);
  }
  assert.throws(() => new GitHub(), AuthError); // no options at all
});

test('a token is trimmed, and only then checked', async () => {
  const { fetch, calls } = fakeFetch({ [`GET ${P}`]: { json: { permissions: { push: true } } } });
  const gh = new GitHub({ token: '  ghp_abc-123_X.y~z\r\n', api: 'http://github.test', fetch });
  assert.equal(await gh.canPush(R), true);
  assert.equal(calls[0].headers.Authorization, 'Bearer ghp_abc-123_X.y~z');
  const everyPrintable = Array.from({ length: 0x7e - 0x21 + 1 }, (_, i) => String.fromCharCode(0x21 + i)).join('');
  assert.doesNotThrow(() => new GitHub({ token: everyPrintable, api: 'http://github.test', fetch }));
});

// --- reading and writing files ------------------------------------------------------------------------

test('auth errors', async () => {
  for (const status of [401, 403]) {
    const { gh } = client({ [`GET ${P}/contents/x`]: { status, json: { message: 'Bad credentials' } } });
    await assert.rejects(gh.getFile(R, 'x'), (e) => {
      assert.ok(e instanceof AuthError, `${status}`);
      assert.equal(e.name, 'AuthError');
      return isGitHubError(status, AUTH)(e);
    });
  }
  for (const status of [400, 404, 409, 429, 500]) {
    const { gh } = client({ [`PUT ${P}/contents/x.json`]: { status, json: { message: 'no' } } });
    await assert.rejects(gh.putJSON(R, 'x.json', {}, { sha: 'old', message: 'm' }), (e) => {
      assert.ok(!(e instanceof AuthError), `${status}`);
      return e instanceof GitHubError;
    });
  }
});

test('listDir 404 is empty', async () => {
  const { gh } = client({ [`GET ${P}/contents/queue`]: { status: 404, json: { message: 'Not Found' } } });
  assert.deepEqual(await gh.listDir(R, 'queue'), []);
});

test('listDir keeps only name, path, sha and type', async () => {
  const entry = { name: 'a.txt', path: 'd/a.txt', sha: 's1', type: 'file', size: 1, url: 'u', download_url: 'v' };
  const { gh } = client({ [`GET ${P}/contents/d`]: { json: [entry, { ...entry, name: 'sub', path: 'd/sub', type: 'dir' }] } });
  assert.deepEqual(await gh.listDir(R, 'd'), [
    { name: 'a.txt', path: 'd/a.txt', sha: 's1', type: 'file' },
    { name: 'sub', path: 'd/sub', sha: 's1', type: 'dir' },
  ]);
});

test('a file is not a folder', async () => {
  const { gh } = client({ [`GET ${P}/contents/a.txt`]: { json: { type: 'file', name: 'a.txt' } } });
  await assert.rejects(gh.listDir(R, 'a.txt'), isGitHubError(0));
});

test('canPush', async () => {
  assert.equal(await client({ [`GET ${P}`]: { json: { permissions: { push: true } } } }).gh.canPush(R), true);
  assert.equal(await client({ [`GET ${P}`]: { json: { permissions: { push: false, pull: true } } } }).gh.canPush(R), false);
  assert.equal(await client({ [`GET ${P}`]: { json: { full_name: R } } }).gh.canPush(R), false);
  assert.equal(await client({ [`GET ${P}`]: { status: 404, json: { message: 'Not Found' } } }).gh.canPush(R), false);
  await assert.rejects(client({ [`GET ${P}`]: { status: 401, json: {} } }).gh.canPush(R), AuthError);
});

test('blobSha is the git blob sha', async () => {
  assert.equal(await blobSha(new Uint8Array()), 'e69de29bb2d1d6434b8b29ae775ad8c2e48c5391'); // git hash-object of an empty file
  assert.equal(await blobSha(encodeUtf8('hello\n')), 'ce013625030ba8dba906f756967f9e9ca394464a'); // echo hello | git hash-object --stdin
  const bytes = Uint8Array.from({ length: 5000 }, (_, i) => (i * 31) % 256);
  assert.equal(await blobSha(bytes), createHash('sha1').update(`blob ${bytes.length}\0`).update(bytes).digest('hex'));
});

test('getFile gives the raw bytes and the blob sha, however big the file is', async () => {
  const big = Uint8Array.from({ length: 1_500_000 }, (_, i) => (i * 13) % 256); // above the 1 MB the JSON form of the contents API can carry
  const { gh, calls } = client({
    [`GET ${P}/contents/photos/big.jpg`]: { response: new Response(big, { status: 200 }) },
    [`GET ${P}/contents/nope.jpg`]: { status: 404, json: { message: 'Not Found' } },
  });
  const file = await gh.getFile(R, 'photos/big.jpg', HEAD1);
  assert.deepEqual(file.bytes, big);
  assert.equal(file.sha, createHash('sha1').update(`blob ${big.length}\0`).update(big).digest('hex'));
  assert.equal(calls[0].headers.Accept, 'application/vnd.github.raw');
  assert.equal(calls[0].query, `?ref=${HEAD1}`);
  assert.equal(await gh.getFile(R, 'nope.jpg'), null);
});

test('getJSON reads at a ref, missing is null', async () => {
  const text = '{\n  "이름": "강"\n}\n';
  const { gh, calls } = client({
    [`GET ${P}/contents/a.json`]: { body: text },
    [`GET ${P}/contents/nope.json`]: { status: 404, json: { message: 'Not Found' } },
  });
  const found = await gh.getJSON(R, 'a.json', 'main');
  assert.deepEqual(found.data, { 이름: '강' });
  assert.equal(found.sha, await blobSha(encodeUtf8(text)));
  assert.equal(calls[0].query, '?ref=main');
  assert.equal(await gh.getJSON(R, 'nope.json'), null);
});

test('putJSON writes JSON the way dumpJSON gives it, and returns the new sha', async () => {
  const { gh, calls } = client({ [`PUT ${P}/contents/queue/k/item.json`]: [{ json: { content: { sha: 'new1' } } }, { status: 201, json: { content: { sha: 'new2' } } }] });
  const data = { error: '꺼짐', n: [1], empty: {} };
  assert.equal(await gh.putJSON(R, 'queue/k/item.json', data, { sha: 'old', message: '대기열: k PC가 맡음' }), 'new1');
  assert.equal(await gh.putJSON(R, 'queue/k/item.json', {}, { message: 'm' }), 'new2');
  const [update, create] = calls;
  assert.equal(update.method, 'PUT');
  assert.equal(update.body.message, '대기열: k PC가 맡음');
  assert.equal(update.body.sha, 'old');
  assert.equal(fromBase64(update.body.content).toString('utf8'), `${JSON.stringify(data, null, 2)}\n`);
  assert.equal(fromBase64(update.body.content).toString('utf8'), '{\n  "error": "꺼짐",\n  "n": [\n    1\n  ],\n  "empty": {}\n}\n');
  assert.ok(!('sha' in create.body)); // a new file has no sha
});

test('putJSON conflict', async () => {
  for (const status of [409, 422]) {
    const { gh, calls } = client({ [`PUT ${P}/contents/x.json`]: { status, json: { message: 'x.json does not match' } } });
    await assert.rejects(gh.putJSON(R, 'x.json', {}, { sha: 'old', message: 'm' }), (e) => {
      assert.ok(e instanceof ConflictError, `${status}`);
      assert.equal(e.name, 'ConflictError');
      return isGitHubError(status, `GitHub ${status}: x.json does not match`)(e);
    });
    assert.deepEqual(steps(calls), [`PUT ${P}/contents/x.json`]); // without a resend a conflict is not second-guessed
  }
});

// A write can land and still lose its answer; the client sends it again and then hears about its own effect.

const ITEM = { status: 'pc' };
const ITEM_BYTES = encodeUtf8(dumpJSON(ITEM));
const itemPath = `${P}/contents/q/item.json`;

test('putJSON that landed before a lost answer is not a conflict', async () => {
  const ours = await blobSha(ITEM_BYTES);
  for (const status of [409, 422]) { // 409: the sha we held is stale now; 422: the file we meant to create exists now
    const { gh, calls } = client({
      [`PUT ${itemPath}`]: [dropped(), { status, json: { message: 'already' } }],
      [`GET ${itemPath}`]: { body: new TextDecoder().decode(ITEM_BYTES) },
    });
    assert.equal(await gh.putJSON(R, 'q/item.json', ITEM, { sha: status === 422 ? undefined : 'old', message: 'm' }), ours, `${status}`);
    assert.deepEqual(steps(calls), [`PUT ${itemPath}`, `PUT ${itemPath}`, `GET ${itemPath}`]);
  }
});

test('putJSON resent while the file holds something else is a conflict', async () => {
  for (const [label, get] of [
    ['another writer', { body: '{"status": "github"}' }],
    ['gone', { status: 404, json: { message: 'Not Found' } }],
    ['a folder', { json: [{ name: 'x' }] }],
  ]) {
    const { gh } = client({ [`PUT ${itemPath}`]: [dropped(), { status: 409, json: { message: 'no' } }], [`GET ${itemPath}`]: get });
    await assert.rejects(gh.putJSON(R, 'q/item.json', ITEM, { sha: 'old', message: 'm' }), (e) => e instanceof ConflictError && e.status === 409, label);
  }
});

// --- what commitFiles accepts: Blobs, JSON data, references -----------------------------------------------

test('commitFiles takes Blobs and Files as new blobs, byte for byte', async () => {
  const jpeg = Uint8Array.from({ length: 70_001 }, (_, i) => (i * 31 + 7) % 256); // every byte value, FF D8 among them
  const small = jpeg.subarray(0, 1000);
  const { gh, calls } = client(commitRoutes());
  await gh.commitFiles(R, {
    message: 'm',
    build: async () => ({
      files: {
        'queue/k/full.jpg': new Blob([jpeg], { type: 'image/jpeg' }),
        'queue/k/thumb.jpg': new File([small], 'thumb.jpg', { type: 'image/jpeg' }), // a File is a Blob
        'queue/k/empty.bin': new Blob([]),
      },
    }),
  });
  const posted = calls.filter((c) => c.path.endsWith('/git/blobs')).map((c) => fromBase64(c.body.content));
  assert.equal(posted.length, 3);
  assert.ok(posted[0].equals(jpeg));
  assert.ok(posted[1].equals(small));
  assert.equal(posted[2].length, 0);
  const tree = calls.find((c) => c.path.endsWith('/git/trees')).body.tree;
  assert.deepEqual(tree.map((e) => e.sha), [await blobSha(jpeg), await blobSha(small), await blobSha(new Uint8Array())]);
});

test('a Date or a Map is a TypeError that names the file, never "{}"', async () => {
  for (const [value, name] of [[new Date(0), 'Date'], [new Map(), 'Map']]) {
    const { gh, calls } = client(commitRoutes());
    await assert.rejects(gh.commitFiles(R, { message: 'm', build: async () => ({ files: { 'queue/k/item.json': value } }) }), (e) => {
      assert.ok(e instanceof TypeError);
      assert.ok(e.message.includes('queue/k/item.json') && e.message.includes(name), e.message);
      return true;
    });
    assert.ok(!calls.some((c) => c.method !== 'GET'));
  }
});

test('JSON is written for arrays and plain objects only, and only {sha} alone is a reference', async () => {
  const { gh, calls } = client(commitRoutes());
  const bare = Object.assign(Object.create(null), { n: 1 });
  await gh.commitFiles(R, {
    message: 'm',
    build: async () => ({
      files: {
        'with-other.json': { sha: sha('9'), note: '메모' }, // more than a sha: data
        'array.json': [1, 2],
        'bare.json': bare, // no prototype at all is still plain
        'ref.bin': { sha: sha('8') },
      },
    }),
  });
  const posted = calls.filter((c) => c.path.endsWith('/git/blobs')).map((c) => fromBase64(c.body.content).toString('utf8'));
  assert.deepEqual(posted, [dumpJSON({ sha: sha('9'), note: '메모' }), dumpJSON([1, 2]), dumpJSON({ n: 1 })]);
  const tree = calls.find((c) => c.path.endsWith('/git/trees')).body.tree;
  assert.equal(tree[0].sha, await blobSha(encodeUtf8(posted[0])));
  assert.equal(tree[3].sha, sha('8'));
});

// --- time limits ------------------------------------------------------------------------------------------

test('every try has a time limit: 30 s, or 120 s for a body above 512 KB', async () => {
  assert.deepEqual([TIMEOUT_MS, LARGE_TIMEOUT_MS, LARGE_BODY_BYTES], [30_000, 120_000, 512 * 1024]);
  const limits = mock.method(AbortSignal, 'timeout'); // records, and still makes the real signal
  try {
    const { gh } = client(commitRoutes({
      [`GET ${P}`]: { json: { permissions: { push: true } } },
      [`PUT ${P}/contents/a.json`]: { json: { content: { sha: 's' } } },
    }));
    await gh.canPush(R); // no body
    await gh.putJSON(R, 'a.json', { n: 1 }, { message: 'm' }); // small body
    // base64 makes these bodies about 400 KB and 533 KB
    await gh.commitFiles(R, { message: 'm', build: async () => ({ files: { 's.bin': new Uint8Array(300_000), 'l.bin': new Uint8Array(400_000) } }) });
    const [N, L] = [TIMEOUT_MS, LARGE_TIMEOUT_MS];
    // canPush, putJSON, GET ref, GET commit, blob s, blob l, tree, commit, PATCH
    assert.deepEqual(limits.mock.calls.map((c) => c.arguments[0]), [N, N, N, N, N, L, N, N, N]);
  } finally {
    limits.mock.restore();
  }
});

test('a try that gets no answer in time is repeated like a lost one', async () => {
  const { gh, calls, waits } = client({ [`GET ${P}`]: [{ hang: true }, { json: { permissions: { push: true } } }] }, { timeouts: { normal: 20 } });
  assert.equal(await gh.canPush(R), true);
  assert.equal(calls.length, 2);
  assert.deepEqual(waits, [RETRY_DELAYS[0]]);
  assert.equal(calls[0].init.signal.aborted, true); // the first try was cut off by its own limit
  assert.notEqual(calls[0].init.signal, calls[1].init.signal); // and the second has a fresh one
  assert.equal(calls[1].init.signal.aborted, false);
});

test('when every try times out the error is the same as for a dead connection', async () => {
  const { gh, calls, waits } = client({ [`GET ${P}`]: { hang: true } }, { timeouts: { normal: 10 } });
  await assert.rejects(gh.canPush(R), (e) => {
    assert.equal(e.cause.name, 'TimeoutError');
    return isGitHubError(0, OFFLINE)(e);
  });
  assert.equal(calls.length, 4);
  assert.deepEqual(waits, RETRY_DELAYS);
});

test('the time limit covers reading the body', async () => {
  const stalledBody = (call) => ({ response: { status: 200, arrayBuffer: () => untilAborted(call.init.signal) } });
  const { gh, calls } = client({ [`GET ${P}`]: [stalledBody, { json: { permissions: { push: true } } }] }, { timeouts: { normal: 20 } });
  assert.equal(await gh.canPush(R), true);
  assert.equal(calls.length, 2);
});

test('the large limit applies to a body above 512 KB, the normal one below', async () => {
  const timeToSecondTry = async (limits, bytes) => {
    const { gh, calls } = client(commitRoutes({ [`POST ${P}/git/blobs`]: [{ hang: true }, blobReply] }), { timeouts: limits });
    const started = performance.now();
    await gh.commitFiles(R, { message: 'm', build: async () => ({ files: { 'x.bin': new Uint8Array(bytes) } }) });
    assert.equal(calls.filter((c) => c.path.endsWith('/git/blobs')).length, 2); // the first timed out, the second went through
    return performance.now() - started;
  };
  const quick = await timeToSecondTry({ normal: 10, large: 400 }, 100_000); // ~133 KB: the normal limit
  const slow = await timeToSecondTry({ normal: 10, large: 400 }, 450_000); // ~600 KB: the large limit
  assert.ok(quick < 300, `${quick} ms`);
  assert.ok(slow >= 350, `${slow} ms`);
});

test('putJSON after a try that timed out checks what the file holds', async () => {
  const ours = await blobSha(ITEM_BYTES);
  const { gh, calls } = client({
    [`PUT ${itemPath}`]: [{ hang: true }, { status: 409, json: { message: 'already' } }],
    [`GET ${itemPath}`]: { body: new TextDecoder().decode(ITEM_BYTES) },
  }, { timeouts: { normal: 20 } });
  assert.equal(await gh.putJSON(R, 'q/item.json', ITEM, { sha: 'old', message: 'm' }), ours);
  assert.deepEqual(steps(calls), [`PUT ${itemPath}`, `PUT ${itemPath}`, `GET ${itemPath}`]);
});

test('a PATCH that timed out is looked up in main like a lost one', async () => {
  const { gh, calls } = client(commitRoutes({
    ...patchTries({ hang: true }, NOT_FF),
    [`GET ${REF}`]: [{ json: { object: { sha: HEAD1 } } }, { json: { object: { sha: D } } }],
    [`GET ${P}/git/commits/${D}`]: { json: { sha: D, tree: { sha: TREE2 }, parents: [{ sha: NEW1 }] } },
  }), { timeouts: { normal: 20 } });
  const counter = {};
  assert.equal(await gh.commitFiles(R, { message: 'm', build: writeY(counter) }), NEW1);
  assert.equal(counter.built, 1);
  assert.equal(calls.filter((c) => c.method === 'PATCH').length, 2);
});

test('a real connection that goes quiet is cut off, whether before the headers or half way through the body', async () => {
  for (const behavior of ['silence', 'half a body']) {
    let requests = 0;
    const server = createServer((request, response) => {
      requests++;
      if (behavior === 'half a body') {
        response.writeHead(200, { 'Content-Type': 'application/json', 'Content-Length': '1000' });
        response.write('{"permissions"');
      }
    });
    await new Promise((listening) => server.listen(0, '127.0.0.1', listening));
    try {
      const gh = new GitHub({ token: TOKEN, api: `http://127.0.0.1:${server.address().port}`, sleep: async () => {}, timeouts: { normal: 80 } });
      await assert.rejects(gh.canPush(R), isGitHubError(0, OFFLINE), behavior);
      assert.equal(requests, 4, behavior);
    } finally {
      server.closeAllConnections();
      await new Promise((closed) => server.close(closed));
    }
  }
});

// --- rate limits ------------------------------------------------------------------------------------------

const RATE = '요청이 너무 많아요. 잠시 후 다시 해 주세요';

test('rate limits are not bad tokens', async () => {
  const limited = [
    [403, 'API rate limit exceeded for user ID 1234.'], // primary
    [403, 'You have exceeded a secondary rate limit. Please wait a few minutes before you try again.'], // secondary
    [429, 'API rate limit exceeded for 203.0.113.9.'],
    [429, 'You have exceeded a secondary rate limit and have been temporarily blocked from content creation.'],
    [403, 'API Rate Limit exceeded'],
  ];
  for (const [status, message] of limited) {
    const { gh, calls, waits } = client({ [`GET ${P}`]: { status, json: { message, documentation_url: 'https://docs.github.com/rest' } } });
    await assert.rejects(gh.canPush(R), (e) => {
      assert.ok(!(e instanceof AuthError), message);
      return isGitHubError(status, RATE)(e);
    }, message);
    assert.equal(calls.length, 1); // an answer, so no repeat
    assert.deepEqual(waits, []);
  }
  // the same through the commit path
  const { gh } = client(commitRoutes({ [`PATCH ${PATCH_REF}`]: { status: 403, json: { message: 'You have exceeded a secondary rate limit.' } } }));
  await assert.rejects(gh.commitFiles(R, { message: 'm', build: writeY({}) }), (e) => !(e instanceof CommitError) && isGitHubError(403, RATE)(e));
});

test('other 401, 403 and 429 answers stay what they were', async () => {
  for (const [status, message, expectAuth] of [
    [403, 'Resource not accessible by personal access token', true],
    [403, 'Must have admin rights to Repository.', true],
    [401, 'Bad credentials', true],
    [401, 'API rate limit exceeded', true], // a 401 is a bad token whatever it says
    [429, 'Too Many Requests', false],
  ]) {
    const { gh } = client({ [`GET ${P}`]: { status, json: { message } } });
    await assert.rejects(gh.canPush(R), (e) => {
      assert.equal(e instanceof AuthError, expectAuth, `${status} ${message}`);
      return isGitHubError(status, expectAuth ? AUTH : `GitHub ${status}: ${message}`)(e);
    });
  }
});

// --- a file that is not JSON --------------------------------------------------------------------------------

test('a repo file that is not valid JSON is a GitHubError that names the file', async () => {
  const message = '파일 내용을 읽을 수 없어요: src/photos.json';
  const { gh } = client({ [`GET ${P}/contents/src/photos.json`]: { body: '{"rooms": [' } });
  await assert.rejects(gh.getJSON(R, 'src/photos.json'), (e) => {
    assert.ok(e.cause instanceof SyntaxError);
    return isGitHubError(200, message)(e);
  });

  // read inside commitFiles: the error comes out, and nothing is written
  const inCommit = client(commitRoutes({ [`GET ${P}/contents/src/photos.json`]: { body: 'not json' } }));
  await assert.rejects(inCommit.gh.commitFiles(R, {
    message: 'm',
    build: async (readJSON) => ({ files: { 'src/photos.json': dumpJSON(await readJSON('src/photos.json')) } }),
  }), isGitHubError(200, message));
  assert.ok(!inCommit.calls.some((c) => c.method !== 'GET'));
});
