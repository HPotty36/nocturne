// GitHub REST client for the admin page: contents API reads and writes, and one-commit multi-file writes.
// ES module for browsers and Node 24 (it needs only ../config.js, for the commit author); `fetch` can be injected.
// It mirrors scripts/github_api.py (same retry and conflict rules), so read that one first when something here looks odd.

import { COMMIT_AUTHOR } from '../config.js';

export const API = 'https://api.github.com';
export const RETRY_DELAYS = [200, 500, 1000]; // ms before each repeat of a request that got no answer
export const TIMEOUT_MS = 30_000; // one try, answer and body included, for a request with no body or a small one
export const LARGE_TIMEOUT_MS = 120_000; // the same for a request whose body is above LARGE_BODY_BYTES (an image upload)
export const LARGE_BODY_BYTES = 512 * 1024;

const API_VERSION = '2022-11-28';
const BRANCH = 'main';
const ATTEMPTS = 3; // a commit is tried again from the start this many times when main moved underneath it
const LOOKBACK = 20; // how many commits down from the head of main to look for our own commit after a lost answer
const JSON_ACCEPT = 'application/vnd.github+json';
const RAW_ACCEPT = 'application/vnd.github.raw';
const CHUNK = 0x3000; // bytes per btoa call; a multiple of 3, so the pieces join without padding

const AUTH_MESSAGE = '토큰을 다시 넣어 주세요';
const OFFLINE_MESSAGE = '인터넷 연결을 확인해 주세요';
const CONFLICT_MESSAGE = '다른 곳에서 동시에 바뀌었어요. 새로고침 후 다시 해 주세요';
const RATE_LIMIT_MESSAGE = '요청이 너무 많아요. 잠시 후 다시 해 주세요';
const TOKEN_FORMAT = /^[\x21-\x7e]+$/; // printable ASCII without spaces: all a GitHub token is, and all a header may hold

// None of these messages ever contains the token: it only goes into the Authorization header.

export class GitHubError extends Error {
  /** GitHub said no (status is the HTTP status), or could not be reached (status 0). */
  constructor(message, status = 0, options) {
    super(message, options);
    this.name = 'GitHubError';
    this.status = status;
  }
}

export class AuthError extends GitHubError {
  /** The token is wrong, expired or may not do this (401 or 403). */
  constructor(status = 401) {
    super(AUTH_MESSAGE, status);
    this.name = 'AuthError';
  }
}

export class ConflictError extends GitHubError {
  /** A contents write with a stale or missing sha (GitHub answers 409 or 422). */
  constructor(message, status = 409) {
    super(message, status);
    this.name = 'ConflictError';
  }
}

export class CommitError extends GitHubError {
  /** commitFiles gave up: main kept moving. */
  constructor(status = 422) {
    super(CONFLICT_MESSAGE, status);
    this.name = 'CommitError';
  }
}

// --- bytes ---------------------------------------------------------------------------------------

const encoder = new TextEncoder();
const decoder = new TextDecoder();

function toBase64(bytes) {
  let out = '';
  for (let i = 0; i < bytes.length; i += CHUNK) {
    out += globalThis.btoa(String.fromCharCode(...bytes.subarray(i, i + CHUNK)));
  }
  return out;
}

const toHex = (buffer) => Array.from(new Uint8Array(buffer), (b) => b.toString(16).padStart(2, '0')).join('');

/** The git blob sha of these bytes: sha1("blob <length>\0" + bytes), which is the `sha` GitHub reports for a file holding them. */
export async function blobSha(bytes) {
  const head = encoder.encode(`blob ${bytes.length}\0`);
  const all = new Uint8Array(head.length + bytes.length);
  all.set(head);
  all.set(bytes, head.length);
  return toHex(await globalThis.crypto.subtle.digest('SHA-1', all));
}

/** JSON text as the queue files are written: 2-space indent, Korean unescaped, final newline. */
export function dumpJSON(data) {
  return `${JSON.stringify(data, null, 2)}\n`;
}

function encodePath(path) {
  return path.split('/').map(encodeURIComponent).join('/');
}

function contentsPath(repo, path, ref) {
  return `/repos/${repo}/contents/${encodePath(path)}${ref ? `?ref=${encodeURIComponent(ref)}` : ''}`;
}

function messageOf(bytes) {
  const text = decoder.decode(bytes);
  try {
    const message = JSON.parse(text).message;
    if (typeof message === 'string') return message;
  } catch {
    // not JSON: fall through to the raw text
  }
  return text.trim().slice(0, 200);
}

const isPlainObject = (value) => typeof value === 'object' && value !== null && [Object.prototype, null].includes(Object.getPrototypeOf(value));

/** The bytes of a new blob for one entry of commitFiles' `files`. Only what is listed there is accepted: no Date, Map, class instance. */
async function bytesOf(path, change) {
  if (typeof change === 'string') return encoder.encode(change); // written verbatim
  if (change instanceof Uint8Array) return change;
  if (change instanceof ArrayBuffer) return new Uint8Array(change);
  if (ArrayBuffer.isView(change)) return new Uint8Array(change.buffer, change.byteOffset, change.byteLength);
  if (typeof Blob === 'function' && change instanceof Blob) return new Uint8Array(await change.arrayBuffer()); // File is a Blob
  if (Array.isArray(change) || isPlainObject(change)) return encoder.encode(dumpJSON(change)); // data -> JSON file
  const what = change === null || typeof change !== 'object' ? typeof change : (change.constructor?.name ?? 'object');
  throw new TypeError(`${path}: Uint8Array, Blob, string, JSON data (array or plain object), null, or {sha} expected, not ${what}`);
}

/** {path, bytes} for a new blob, or {path, sha} for a delete (sha null) or a blob already in the repository. Sends nothing. */
async function stage(path, change) {
  if (change === null) return { path, sha: null };
  if (isPlainObject(change) && Object.keys(change).length === 1 && 'sha' in change) { // a reference only when `sha` is its sole key
    if (typeof change.sha !== 'string' || change.sha === '') throw new TypeError(`${path}: {sha} needs the sha of a blob`);
    return { path, sha: change.sha };
  }
  return { path, bytes: await bytesOf(path, change) };
}

// --- the client ----------------------------------------------------------------------------------

export class GitHub {
  #token;
  #fetch;
  #sleep;
  #timeouts;
  #author;

  /**
   * `fetch`, `sleep` and `timeouts` ({normal, large} in ms) can be replaced (tests do). The token stays in a private
   * field, so it cannot leak through console.log or JSON.stringify of the client. A token that is not printable ASCII
   * (a pasted newline or space inside it, Hangul) throws AuthError here, before any fetch could choke on it in a
   * header and put the token into its own error. `author` ({name, email}) is the author and committer of every
   * commit this client makes.
   */
  constructor({ token, api = API, fetch = globalThis.fetch, sleep = (ms) => new Promise((done) => setTimeout(done, ms)), timeouts,
    author = COMMIT_AUTHOR } = {}) {
    const trimmed = typeof token === 'string' ? token.trim() : '';
    if (!TOKEN_FORMAT.test(trimmed)) throw new AuthError();
    this.#token = trimmed;
    this.api = api.replace(/\/+$/, '');
    this.#fetch = fetch;
    this.#sleep = sleep;
    this.#timeouts = { normal: TIMEOUT_MS, large: LARGE_TIMEOUT_MS, ...timeouts };
    this.#author = { name: author.name, email: author.email };
  }

  /** The author and committer fields of a request that makes a commit. */
  #signed() {
    return { author: { ...this.#author }, committer: { ...this.#author } };
  }

  // --- requests ---

  /**
   * {status, bytes, resent} of any HTTP answer. A request that got no answer (fetch rejects: the connection dropped
   * or was cut short, or the try ran out of time) is repeated up to RETRY_DELAYS.length more times, and `resent`
   * says an earlier try was lost: the server may have acted on it, so a repeated write can hear about its own effect.
   * Each try has its own time limit, which covers reading the body: TIMEOUT_MS, or LARGE_TIMEOUT_MS when the request
   * body is above LARGE_BODY_BYTES. An HTTP error status is an answer and is not repeated. A request that gets no
   * answer every time is a GitHubError with status 0.
   */
  async #request(method, path, body, accept = JSON_ACCEPT) {
    const headers = { Accept: accept, 'X-GitHub-Api-Version': API_VERSION, Authorization: `Bearer ${this.#token}` };
    // no-store: GitHub's answers may be cached for a minute, and a commit that re-reads main must see the newest one
    const init = { method, headers, cache: 'no-store' };
    let timeout = this.#timeouts.normal;
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
      if (encoder.encode(init.body).length > LARGE_BODY_BYTES) timeout = this.#timeouts.large;
    }
    const send = this.#fetch; // called bare, not as a method: a browser's fetch refuses any other `this`
    let resent = false;
    for (let tried = 0; ; tried++) {
      try {
        const response = await send(this.api + path, { ...init, signal: AbortSignal.timeout(timeout) });
        return { status: response.status, bytes: new Uint8Array(await response.arrayBuffer()), resent };
      } catch (cause) {
        if (tried >= RETRY_DELAYS.length) throw new GitHubError(OFFLINE_MESSAGE, 0, { cause });
      }
      resent = true;
      await this.#sleep(RETRY_DELAYS[tried]);
    }
  }

  static #check(status, bytes) {
    if (status >= 200 && status < 300) return;
    const message = messageOf(bytes);
    // GitHub answers a rate limit, primary or secondary, with 403 or 429: that is not a bad token
    if ((status === 403 || status === 429) && /rate limit/i.test(message)) throw new GitHubError(RATE_LIMIT_MESSAGE, status);
    if (status === 401 || status === 403) throw new AuthError(status);
    throw new GitHubError(`GitHub ${status}: ${message}`, status);
  }

  static #parse(status, bytes) {
    try {
      return JSON.parse(decoder.decode(bytes));
    } catch (cause) {
      throw new GitHubError(`GitHub ${status}: 응답을 읽을 수 없어요`, status, { cause });
    }
  }

  /** Parsed JSON of a successful answer; any other status throws. */
  async #json(method, path, body) {
    const { status, bytes } = await this.#request(method, path, body);
    GitHub.#check(status, bytes);
    return GitHub.#parse(status, bytes);
  }

  // --- files ---

  /** True if the token can write to `repo`. A repo the token cannot see is a 404 and counts as false. */
  async canPush(repo) {
    const { status, bytes } = await this.#request('GET', `/repos/${repo}`);
    if (status === 404) return false;
    GitHub.#check(status, bytes);
    return GitHub.#parse(status, bytes).permissions?.push === true;
  }

  /** [{name, path, sha, type}] of a folder (type "file" or "dir"); [] if it does not exist. */
  async listDir(repo, path) {
    const { status, bytes } = await this.#request('GET', contentsPath(repo, path));
    if (status === 404) return [];
    GitHub.#check(status, bytes);
    const entries = GitHub.#parse(status, bytes);
    if (!Array.isArray(entries)) throw new GitHubError(`${path}는 폴더가 아니에요`, 0);
    return entries.map(({ name, path: entryPath, sha, type }) => ({ name, path: entryPath, sha, type }));
  }

  /**
   * {bytes, sha} of a file, read at `ref` (a branch or commit sha) if given; null if it does not exist.
   *
   * One request, with the raw media type: that works for files of any size (the JSON form of the contents API gives
   * an empty `content` above 1 MB). The sha is computed here as the git blob sha of the bytes, which is exactly
   * what the contents API reports as `sha`, so no second request is needed and bytes and sha cannot disagree.
   */
  async getFile(repo, path, ref) {
    const { status, bytes } = await this.#request('GET', contentsPath(repo, path, ref), undefined, RAW_ACCEPT);
    if (status === 404) return null;
    GitHub.#check(status, bytes);
    return { bytes, sha: await blobSha(bytes) };
  }

  /** {data, sha} of a JSON file; null if it does not exist; a file that is not valid JSON is a GitHubError. */
  async getJSON(repo, path, ref) {
    const file = await this.getFile(repo, path, ref);
    if (!file) return null;
    try {
      return { data: JSON.parse(decoder.decode(file.bytes)), sha: file.sha };
    } catch (cause) {
      throw new GitHubError(`파일 내용을 읽을 수 없어요: ${path}`, 200, { cause });
    }
  }

  /**
   * Write a JSON file with its current sha (omit it for a new file). Returns the new sha; a stale sha is a ConflictError.
   *
   * If the connection dropped and the write was sent again, a 409 or 422 may be about our own first try, which
   * landed. Then the file holds exactly what we wrote, and that is success, not a conflict.
   */
  async putJSON(repo, path, data, { sha, message }) {
    const bytes = encoder.encode(dumpJSON(data));
    const body = { message, content: toBase64(bytes), ...this.#signed() };
    if (sha != null) body.sha = sha;
    const { status, bytes: answer, resent } = await this.#request('PUT', contentsPath(repo, path), body);
    if (status === 409 || status === 422) {
      if (resent) {
        const ours = await blobSha(bytes);
        if ((await this.getFile(repo, path))?.sha === ours) return ours;
      }
      throw new ConflictError(`GitHub ${status}: ${messageOf(answer)}`, status);
    }
    GitHub.#check(status, answer);
    return GitHub.#parse(status, answer).content.sha;
  }

  // --- one commit, many files ---

  /**
   * Commit several file changes at once on main; returns the new commit's sha, or null if there was nothing to change.
   *
   * `build(readJSON)` returns {files: {[path]: change}} (or nothing at all, for "nothing to do"). A change is
   * a string (written verbatim), bytes (Uint8Array, ArrayBuffer, a typed-array view) or a Blob/File (a new blob),
   * JSON data (an array or plain object, written as dumpJSON), null (delete the file), or {sha} (an object whose
   * only key is `sha`: reuse a blob that is already in the repository). Anything else (Date, Map, a class instance,
   * a number, undefined) is a TypeError, raised before anything is written.
   * `readJSON(path)` gives {data}, or null if the file is missing, as of the commit this attempt builds on.
   * If main moved before the ref update, everything starts over, build included, on top of the new main; after
   * ATTEMPTS tries that is a CommitError.
   */
  async commitFiles(repo, { message, build }) {
    const refPath = `/repos/${repo}/git/refs/heads/${BRANCH}`;
    for (let attempt = 0; attempt < ATTEMPTS; attempt++) {
      const head = (await this.#json('GET', `/repos/${repo}/git/ref/heads/${BRANCH}`)).object.sha;
      const baseTree = (await this.#json('GET', `/repos/${repo}/git/commits/${head}`)).tree.sha;
      const readJSON = async (path) => {
        const found = await this.getJSON(repo, path, head);
        return found ? { data: found.data } : null;
      };

      const built = await build(readJSON);
      if (!built) return null;
      if (typeof built.files !== 'object' || built.files === null) throw new TypeError('build must return {files: {...}}');
      const changes = Object.entries(built.files);
      if (changes.length === 0) return null;

      const staged = [];
      for (const [path, change] of changes) staged.push(await stage(path, change)); // all checked and read before the first blob is sent
      const tree = [];
      for (const entry of staged) tree.push(await this.#treeEntry(repo, entry));
      const treeSha = (await this.#json('POST', `/repos/${repo}/git/trees`, { base_tree: baseTree, tree })).sha;
      const commit = (await this.#json('POST', `/repos/${repo}/git/commits`, { message, tree: treeSha, parents: [head], ...this.#signed() })).sha;

      let patch;
      try {
        patch = await this.#request('PATCH', refPath, { sha: commit, force: false });
      } catch (error) {
        if (!(error instanceof GitHubError && error.status === 0)) throw error;
        patch = { status: 0, bytes: new Uint8Array(), resent: true }; // every try got no answer; the first may still have landed
      }
      const { status, bytes, resent } = patch;
      if (resent && (status === 0 || status === 422) && (await this.#inMain(repo, commit))) {
        return commit; // our own earlier try moved main; building again would publish twice
      }
      if (status === 0 || status === 422) continue; // not a fast-forward: someone else committed first
      GitHub.#check(status, bytes);
      return commit;
    }
    throw new CommitError();
  }

  /** Is `sha` the head of main, or one of its last LOOKBACK commits? */
  async #inMain(repo, sha) {
    const todo = [(await this.#json('GET', `/repos/${repo}/git/ref/heads/${BRANCH}`)).object.sha];
    const seen = new Set();
    while (todo.length > 0 && seen.size < LOOKBACK) {
      const current = todo.shift();
      if (current === sha) return true;
      if (!seen.has(current)) {
        seen.add(current);
        const commit = await this.#json('GET', `/repos/${repo}/git/commits/${current}`);
        todo.push(...commit.parents.map((parent) => parent.sha));
      }
    }
    return false;
  }

  /** The tree entry of a staged change; new bytes become a blob first. */
  async #treeEntry(repo, { path, bytes, sha }) {
    if (bytes) {
      const blob = { content: toBase64(bytes), encoding: 'base64' };
      sha = (await this.#json('POST', `/repos/${repo}/git/blobs`, blob)).sha;
    }
    return { path, mode: '100644', type: 'blob', sha };
  }

  // --- the site build ---

  /** {status, conclusion, url} of the newest run of a workflow file (e.g. "site.yml"); null if it has not run. */
  async latestRun(repo, workflowFile) {
    const path = `/repos/${repo}/actions/workflows/${encodeURIComponent(workflowFile)}/runs?per_page=1`;
    const { status, bytes } = await this.#request('GET', path);
    if (status === 404) return null;
    GitHub.#check(status, bytes);
    const run = GitHub.#parse(status, bytes).workflow_runs?.[0];
    return run ? { status: run.status, conclusion: run.conclusion ?? null, url: run.html_url } : null;
  }
}
