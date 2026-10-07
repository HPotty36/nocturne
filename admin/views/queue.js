// The "새 사진" tab: photos picked (or dropped) here are made safe in the browser and sent, one commit each, to
// queue/<name>/ (full.jpg, thumb.jpg, item.json); the PC helper or GitHub Actions then drafts and publishes them.
// Below the picker, every item in queue/ with its status, and what a person can do about it: 다시 올리기 (an upload
// that did not go through), 버리기, and for an item the AI gave up on, 다시 시도 or 직접 입력해서 게시.
//
// QueueStore (DOM-free, tested in Node) holds the cards. Every read of the queue and every commit run one after
// another, so a look that began before a commit can never land after it. Photos are processed and sent one at a
// time (a phone has little memory), and the processed copies are kept only until their commit has landed.
// The view keeps one card per upload or queue item; text from item.json and photos.json only ever reaches the page
// through textContent and value.

import { AuthError, blobSha, GitHubError } from '../lib/github.js';
import { addPhoto, allNames, GalleryError, roman, serialize } from '../lib/gallery.js';
import { ImageError, processPhoto } from '../lib/image.js';
import { nameFor, uniqueName } from '../lib/names.js';
import { canDiscard, hint, label, newItem, toWaiting } from '../lib/queue.js';

const DATA = 'src/photos.json';
const QUEUE = 'queue';
const itemPath = (name) => `${QUEUE}/${name}/item.json`;
const RENAMES = 10; // names tried for one upload when other devices keep taking them first
// What tells one upload from another under the same name: none of these change while it waits in the queue.
const SAME_UPLOAD = ['name', 'kind', 'uploaded_at', 'file'];

const NO_DATA = 'src/photos.json을 찾을 수 없어요';
const FILL = '제목과 설명을 채워 주세요';
const FILES_MISSING = '대기열 사진 파일이 없어요';
const NAME_TAKEN = '같은 이름의 사진이 이미 있어요';
const BUSY_NOW = '지금은 버릴 수 없어요. AI가 보는 중이에요';

/** Something the queue cannot do right now; the message is shown to the person. */
export class QueueError extends Error {
  constructor(message) {
    super(message);
    this.name = 'QueueError';
  }
}

/** The name an upload was about to use is taken at the commit it builds on (another device, or a photo on show). */
class NameTaken extends QueueError {
  constructor() {
    super(NAME_TAKEN);
  }
}

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
/** Are a and b item.json of the same upload (possibly in another status)? */
const sameUpload = (a, b) => isObject(a) && isObject(b) && SAME_UPLOAD.every((key) => a[key] === b[key]);

/** Is a queue item listed here? New uploads always; a redraft only when the AI gave up on it (else 전시 중 shows it). */
const listed = (item) => item.kind === 'new' || (item.kind === 'redraft' && item.status === 'failed');

// --- the queue as this page knows it (no DOM) --------------------------------------------------

/**
 * A card is {key, stage, name, ...}:
 *   stage      "upload" (being processed or sent), "upload-failed", "queue" (in queue/), "published" (gone from
 *              queue/ into photos.json; shown as 게시됨 until forget(key))
 *   name       the queue folder name; null for an upload not yet named
 *   file       the picked File while it may still have to be processed; fileName: its name, kept for the card
 *   copies     {full, thumb}: processed Blobs, kept only until their commit lands (for 다시 올리기)
 *   facts      {date, camera, grayscale} found while processing
 *   pending    the item.json an upload writes (kept, so a retry writes the same one)
 *   error      why an upload failed; final: an error sending again cannot fix (ImageError)
 *   mayHaveLanded  a commit of this upload failed in a way that does not tell whether it landed (a lost answer)
 *   item       item.json as last read or written (stage "queue")
 *   broken     a queue folder whose item.json is missing or unreadable (stage "queue", item null)
 *   thumb      the thumbnail Blob this page made, kept for the card after the commit (the large copy is not);
 *              other cards' thumbnails are read from queue/<name>/thumb.jpg
 */
export class QueueStore {
  cards = new Map(); // key -> card, in the order they were first shown
  data = null; // src/photos.json as last read or committed
  onChange = () => {}; // called whenever an upload moves on
  #folders = new Map(); // queue folder name -> {sha, item}: item.json is read again only when the sha changes
  #line = Promise.resolve();
  #uploads = Promise.resolve();
  #keys = 0;
  #closed = false;

  constructor(gh, repo, { process = processPhoto, now = () => Date.now() } = {}) {
    this.gh = gh;
    this.repo = repo;
    this.process = process;
    this.now = now;
  }

  /** Reads and commits run one after another: a read that began before a commit can never land after it. */
  #serial(task) {
    const run = this.#line.then(task);
    this.#line = run.catch(() => {});
    return run;
  }

  #changed() {
    try {
      this.onChange();
    } catch (error) {
      console.error(error); // a drawing problem must not count as a failed upload
    }
  }

  #add(fields) {
    const card = { key: `c${++this.#keys}`, stage: 'queue', name: null, file: null, fileName: null, copies: null, facts: null,
      pending: null, error: null, final: false, mayHaveLanded: false, item: null, broken: false, thumb: null, ...fields };
    this.cards.set(card.key, card);
    return card;
  }

  /** A card shown for queue folder `name`, if any. */
  #shown(name) {
    for (const card of this.cards.values()) if (card.name === name && (card.stage === 'queue' || card.stage === 'published')) return card;
    return null;
  }

  /** The upload card that means to use `name`, if any. */
  #uploadNamed(name) {
    for (const card of this.cards.values()) if (card.name === name && card.stage.startsWith('upload')) return card;
    return null;
  }

  /** An upload is in queue/ now: it becomes a queue card and lets go of the File and the processed copies. */
  #landed(card, name, item) {
    Object.assign(card, { stage: 'queue', name, item, file: null, copies: null, facts: null, pending: null, error: null, final: false,
      mayHaveLanded: false });
  }

  /**
   * While anything is in queue/ (a card, or a redraft the AI is still to write, which may yet fail and then be
   * listed), or an upload may have landed although its answer was lost, the page looks again every so often.
   */
  watching() {
    if (this.#folders.size > 0) return true;
    for (const card of this.cards.values()) {
      if (card.stage === 'queue' || (card.stage === 'upload-failed' && card.mayHaveLanded)) return true;
    }
    return false;
  }

  // --- looking at the queue ---

  /** First look: the queue and photos.json (for names, rooms and redraft titles). */
  load() {
    return this.#serial(() => this.#look(true));
  }

  /**
   * Look at the queue again. Resolves to {published, removed, added}: cards whose item left the queue and is now
   * in photos.json (stage "published"), cards whose item left otherwise (taken off the list), and whether new items
   * were found. photos.json is read only when a new item left, and only after the listing that missed it.
   */
  refresh() {
    return this.#serial(() => this.#look(false));
  }

  async #look(withData) {
    const listing = await this.gh.listDir(this.repo, QUEUE);
    const dirs = new Map(listing.filter((e) => e.type === 'dir').map((e) => [e.name, e.sha]));
    for (const name of [...this.#folders.keys()]) if (!dirs.has(name)) this.#folders.delete(name);
    for (const [name, sha] of dirs) {
      if (this.#folders.get(name)?.sha !== sha) this.#folders.set(name, { sha, item: await this.#readItem(name) });
    }

    const gone = [];
    const removed = [];
    for (const card of [...this.cards.values()]) {
      if (card.stage !== 'queue') continue;
      const folder = this.#folders.get(card.name);
      if (!folder) {
        gone.push(card);
      } else if (card.broken ? folder.item !== null : !sameUpload(folder.item, card.item)) {
        this.cards.delete(card.key); // the folder holds another upload now (or became unreadable, or readable): another card
        removed.push(card);
      } else if (!card.broken) {
        card.item = folder.item;
      }
    }

    const found = [];
    for (const [name, folder] of this.#folders) {
      if (this.#shown(name)) continue;
      const mine = folder.item && this.#uploadNamed(name);
      if (mine && sameUpload(folder.item, mine.pending)) this.#landed(mine, name, folder.item); // its answer was lost, not the upload
      else if (!folder.item || listed(folder.item)) found.push([name, folder]); // a folder that cannot be read is listed too
    }
    const order = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
    const when = (folder) => [!folder.item, String(folder.item?.uploaded_at)]; // readable ones first, oldest upload first
    found.sort(([a, x], [b, y]) => order(when(x)[0], when(y)[0]) || order(when(x)[1], when(y)[1]) || order(a, b));
    for (const [name, folder] of found) this.#add(folder.item ? { name, item: folder.item } : { name, broken: true });

    let names = null;
    if (withData || gone.some((card) => card.item?.kind === 'new')) {
      const photos = await this.gh.getJSON(this.repo, DATA);
      if (!photos) throw new GalleryError(NO_DATA);
      this.data = photos.data;
      names = allNames(photos.data);
    }
    const published = [];
    for (const card of gone) {
      if (card.item?.kind === 'new' && names.has(card.name)) {
        card.stage = 'published';
        published.push(card);
      } else {
        this.cards.delete(card.key);
        removed.push(card);
      }
    }
    return { published, removed, added: found.length > 0 };
  }

  /** A queue folder's item.json; null when it is missing, not JSON, or not an object (the folder cannot be read). */
  async #readItem(name) {
    let found;
    try {
      found = await this.gh.getJSON(this.repo, itemPath(name));
    } catch (error) {
      if (error instanceof GitHubError && error.status === 200) return null; // not JSON
      throw error; // offline for a moment: this look fails, the next one reads it again
    }
    return found && isObject(found.data) ? found.data : null;
  }

  /** A thumbnail for a card: the one this page made, or queue/<name>/thumb.jpg; null if there is none. */
  async thumbOf(card) {
    if (card.thumb) return card.thumb;
    if (card.stage !== 'queue' || card.item?.kind !== 'new') return null;
    const file = await this.gh.getFile(this.repo, `${QUEUE}/${card.name}/thumb.jpg`);
    return file ? new Blob([file.bytes], { type: 'image/jpeg' }) : null;
  }

  // --- uploading ---

  /** Cards for these files show at once; the photos are then processed and sent one by one. Resolves when all are done. */
  addFiles(files) {
    const cards = [...files].map((file) => this.#add({ stage: 'upload', file, fileName: String(file.name ?? '') }));
    this.#changed();
    return this.#send(cards);
  }

  /** 다시 올리기: send a failed upload again (processing it again only if that is where it failed). */
  reupload(key) {
    const card = this.cards.get(key);
    if (!card || card.stage !== 'upload-failed' || card.final) return Promise.resolve();
    Object.assign(card, { stage: 'upload', error: null });
    this.#changed();
    return this.#send([card]);
  }

  /** Let go of a failed upload (and its copies); nothing was sent, so nothing is committed. */
  dropUpload(key) {
    if (this.cards.get(key)?.stage !== 'upload-failed') return;
    this.cards.delete(key);
    this.#changed();
  }

  #send(cards) {
    const run = this.#uploads.then(() => this.#batch(cards)); // #batch never rejects
    this.#uploads = run;
    return run;
  }

  /** One batch, one photo at a time. photos.json and the queue are read once, when the first photo needs a name. */
  async #batch(cards) {
    let taken = null;
    for (const card of cards) {
      if (this.cards.get(card.key) !== card || card.stage !== 'upload') continue; // let go of, or the page was locked
      try {
        if (!card.copies) {
          const photo = await this.process(card.file);
          if (this.#closed) return; // locked meanwhile: nothing more is sent
          card.copies = { full: photo.full, thumb: photo.thumb };
          card.thumb = photo.thumb;
          card.facts = { date: photo.hints?.date ?? null, camera: photo.hints?.camera ?? null, grayscale: photo.grayscale };
          this.#changed();
        }
        if (!card.name) {
          taken ??= await this.#serial(() => this.#takenNames());
          card.name = uniqueName(nameFor(card.file.name), taken);
          taken.add(card.name);
          card.pending = newItem({ name: card.name, kind: 'new', now: this.now(), ...card.facts });
        }
        await this.#serial(() => this.#commitUpload(card));
      } catch (error) {
        if (this.#closed) return;
        if (card.stage === 'upload') {
          const final = error instanceof ImageError; // sending it again would fail the same way
          Object.assign(card, { stage: 'upload-failed', error, final }, final ? { file: null } : {});
        }
      }
      this.#changed();
    }
  }

  /** Names a new upload must not take: photos on show, folders in queue/, and names this page holds. */
  async #takenNames() {
    const [photos, listing] = await Promise.all([this.gh.getJSON(this.repo, DATA), this.gh.listDir(this.repo, QUEUE)]);
    if (!photos) throw new GalleryError(NO_DATA);
    this.data = photos.data;
    const taken = allNames(photos.data);
    for (const entry of listing) taken.add(entry.name);
    for (const card of this.cards.values()) if (card.name) taken.add(card.name);
    return taken;
  }

  /**
   * The upload's commit. It never overwrites: if its folder is taken at the commit it builds on (another device
   * chose the same name meanwhile) or a photo on show has the name, it moves to the next free name and tries again.
   * An earlier try whose answer was lost may have landed: if the folder holds this very upload, it is done; if the
   * worker has published it meanwhile (the photo on show under the name is this upload's full copy, blob for blob),
   * the card is "published". Only a name taken by another photo makes it move on.
   */
  async #commitUpload(card) {
    if (this.#closed || card.stage !== 'upload') return; // a look found that an earlier try had landed
    const collisions = new Set();
    for (let renames = 0; ; renames++) {
      const { name, pending: item, copies } = card;
      const dir = `${QUEUE}/${name}`;
      let onShow = false;
      try {
        await this.gh.commitFiles(this.repo, {
          message: `대기열: ${name} 올림`,
          build: async (read) => {
            let there;
            try {
              there = await read(`${dir}/item.json`);
            } catch (error) {
              if (error instanceof GitHubError && error.status === 200) throw new NameTaken(); // a folder nobody can read
              throw error;
            }
            if (there) {
              if (sameUpload(there.data, item)) return null;
              throw new NameTaken();
            }
            const photos = await read(DATA);
            if (photos && allNames(photos.data).has(name)) {
              if (card.mayHaveLanded && (await this.#isOnShow(name, copies.full))) {
                onShow = true;
                return null;
              }
              throw new NameTaken();
            }
            return { files: { [`${dir}/full.jpg`]: copies.full, [`${dir}/thumb.jpg`]: copies.thumb, [`${dir}/item.json`]: item } };
          },
        });
        if (onShow) Object.assign(card, { stage: 'published', file: null, copies: null, pending: null, error: null, mayHaveLanded: false });
        else this.#landed(card, name, item);
        return;
      } catch (error) {
        if (!(error instanceof NameTaken)) {
          card.mayHaveLanded = true; // a lost answer looks like any other failure: it may be in the queue (or on show) now
          throw error;
        }
        if (renames + 1 >= RENAMES) throw error;
        collisions.add(name);
        const taken = new Set([...collisions, ...this.#folders.keys(), ...(this.data ? allNames(this.data) : [])]);
        for (const other of this.cards.values()) if (other !== card && other.name) taken.add(other.name);
        card.name = uniqueName(nameFor(card.file.name), taken);
        card.pending = { ...item, name: card.name };
        this.#changed();
      }
    }
  }

  /**
   * Is photos/full/<name>.jpg exactly this full copy? The worker publishes by reusing the queue blob, so the same
   * git blob sha means this upload is what was published.
   */
  async #isOnShow(name, full) {
    const ours = await blobSha(new Uint8Array(await full.arrayBuffer()));
    const entry = (await this.gh.listDir(this.repo, 'photos/full')).find((e) => e.type === 'file' && e.name === `${name}.jpg`);
    return entry?.sha === ours;
  }

  // --- what a person can do about a queue item ---

  /**
   * 다시 시도: one commit (`대기열: <name> 다시 시도`, which also starts GitHub Actions) that sets item.json back to
   * "waiting", built on item.json as it is at that commit: only if it is still this upload and still failed.
   * Otherwise (retried elsewhere, thrown away, another upload under the name) nothing is written, so a deleted item
   * never comes back; the card shows what it is now (or the next look tells what became of it).
   * Resolves true when this page set it waiting.
   */
  retry(key) {
    return this.#serial(async () => {
      const card = this.cards.get(key);
      if (card?.stage !== 'queue' || card.item?.status !== 'failed') return false;
      const path = itemPath(card.name);
      let now = null;
      let waiting = null;
      const sha = await this.gh.commitFiles(this.repo, {
        message: `대기열: ${card.name} 다시 시도`,
        build: async (read) => {
          now = (await read(path))?.data ?? null;
          if (!sameUpload(now, card.item) || now.status !== 'failed') return null;
          waiting = toWaiting(now);
          return { files: { [path]: waiting } };
        },
      });
      if (sha) card.item = waiting;
      else if (sameUpload(now, card.item)) card.item = now;
      return Boolean(sha);
    });
  }

  /**
   * 버리기: every file of the item's folder deleted in one commit, built only if at that commit the folder still
   * holds this upload and nobody is working on it (waiting, failed, or a claim gone stale; else QueueError). A folder
   * that cannot be read is deleted while it still cannot be read. Resolves to the card, taken off the list; or null
   * when the folder holds something else now (gone, published, another upload under the name): nothing is deleted
   * and the next look shows what is there.
   */
  discard(key) {
    return this.#serial(async () => {
      const card = this.cards.get(key);
      if (card?.stage !== 'queue' || !(card.broken || canDiscard(card.item, this.now()))) return null;
      const dir = `${QUEUE}/${card.name}`;
      const files = (await this.gh.listDir(this.repo, dir)).filter((e) => e.type === 'file');
      if (files.length === 0) return null;
      let busy = null;
      const sha = await this.gh.commitFiles(this.repo, {
        message: `대기열: ${card.name} 버림`,
        build: async (read) => {
          let found;
          try {
            found = (await read(`${dir}/item.json`))?.data ?? null;
          } catch (error) {
            if (!(card.broken && error instanceof GitHubError && error.status === 200)) throw error;
            found = null; // still not JSON
          }
          if (card.broken ? isObject(found) : !sameUpload(found, card.item)) return null;
          if (!card.broken && !canDiscard(found, this.now())) {
            busy = found;
            return null;
          }
          return { files: Object.fromEntries(files.map((e) => [e.path, null])) };
        },
      });
      if (busy) {
        card.item = busy;
        throw new QueueError(BUSY_NOW);
      }
      if (!sha) return null;
      this.cards.delete(card.key);
      this.#folders.delete(card.name);
      return card;
    });
  }

  /**
   * 직접 입력해서 게시 (a new item the AI gave up on): one commit puts the two queue photos on show (their blobs reused),
   * adds the photo at the end of `room` in photos.json (no `ai`: a person wrote it), and deletes the queue folder;
   * built only if at that commit item.json is still this upload and still failed.
   * Title and description are trimmed; empty is a GalleryError before anything is sent.
   * Resolves to {sha, why}: the commit sha (the card is then "published") and why null; or sha null and why
   * "published" (the AI published it first; the card is "published") or "changed" (retried elsewhere, thrown away,
   * or another upload took the name: nothing was written, the next look shows what is there).
   */
  async publish(key, { room, title, alt }) {
    const texts = { title: String(title ?? '').trim(), alt: String(alt ?? '').trim() };
    if (!texts.title || !texts.alt) throw new GalleryError(FILL);
    return this.#serial(async () => {
      const card = this.cards.get(key);
      if (card?.stage !== 'queue' || card.item?.kind !== 'new') return { sha: null, why: 'changed' };
      const dir = `${QUEUE}/${card.name}`;
      const blobs = Object.fromEntries((await this.gh.listDir(this.repo, dir)).filter((e) => e.type === 'file').map((e) => [e.name, e.sha]));
      const entry = { file: card.name, ...texts };
      for (const key of ['date', 'camera']) if (card.item[key]) entry[key] = card.item[key];
      let data = null;
      let why = null;
      const sha = await this.gh.commitFiles(this.repo, {
        message: `사진 게시: ${texts.title}`,
        build: async (read) => {
          const now = (await read(`${dir}/item.json`))?.data ?? null;
          const found = await read(DATA);
          if (!found) throw new GalleryError(NO_DATA);
          if (!sameUpload(now, card.item) || now.status !== 'failed') {
            why = !now && allNames(found.data).has(card.name) ? 'published' : 'changed';
            return null;
          }
          if (!blobs['full.jpg'] || !blobs['thumb.jpg']) throw new GalleryError(FILES_MISSING);
          addPhoto(found.data, room, { ...entry }); // GalleryError: unknown room, name already on show
          data = found.data;
          return {
            files: {
              [`photos/full/${card.name}.jpg`]: { sha: blobs['full.jpg'] },
              [`photos/thumb/${card.name}.jpg`]: { sha: blobs['thumb.jpg'] },
              [DATA]: serialize(found.data),
              ...Object.fromEntries(Object.keys(blobs).map((file) => [`${dir}/${file}`, null])),
            },
          };
        },
      });
      if (sha) this.data = data;
      if (sha || why === 'published') {
        card.stage = 'published';
        this.#folders.delete(card.name);
      }
      return { sha, why };
    });
  }

  /** Take a card off the list (a "published" one, once it has been shown long enough). */
  forget(key) {
    this.cards.delete(key);
  }

  /** The page is locked: stop the uploads still waiting and let go of every photo held in memory. */
  close() {
    this.#closed = true;
    this.cards.clear();
    this.#folders.clear();
  }
}

// --- the view ----------------------------------------------------------------------------------

const UPLOADING = '올리는 중…';
const UPLOAD_FAILED = '올리지 못했어요';
const PUBLISHED = '게시됨';
const PUBLISHED_MS = 5000; // how long a published card stays before it leaves the list
const LOADING = '불러오는 중…';
const EMPTY = '대기열이 비어 있어요';
const GRAYSCALE = '흑백 감지';
const NO_LOCATION = '위치 정보 삭제됨';
const REDRAFT = 'AI로 다시 쓰기';
const UNREADABLE_ITEM = '확인할 수 없는 항목';
const LEFT = '대기열에서 빠졌어요';
const AI_FIRST = 'AI가 먼저 게시했어요. 전시 중에서 고칠 수 있어요';
const DISCARD_NOTE = '버려도 올린 사진은 공개 저장소의 기록에 남아요';
const UNKNOWN_PROBLEM = '문제가 생겼어요. 새로고침 후 다시 해 주세요';

function el(tag, className, ...children) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  node.append(...children); // strings become text nodes
  return node;
}

function button(text, className) {
  const node = el('button', className, text);
  node.type = 'button';
  return node;
}

function say(node, text, isError = false) {
  if (node.textContent !== text) node.textContent = text; // the same text again would be read out again
  node.classList.toggle('is-error', Boolean(text) && isError);
}

function live(node) {
  node.setAttribute('role', 'status');
  node.setAttribute('aria-live', 'polite');
  return node;
}

/** Put `node` at `index` among parent's children, moving it only when it is not already there. */
function place(parent, node, index) {
  const at = parent.children[index] ?? null;
  if (at !== node) parent.insertBefore(node, at);
}

/** The message to show for a failed action; null for a rejected token (the page is already back at setup). */
function problem(error) {
  if (error instanceof AuthError) return null;
  if (error instanceof GitHubError || error instanceof GalleryError || error instanceof ImageError || error instanceof QueueError) return error.message;
  return UNKNOWN_PROBLEM;
}

function titleOf(data, file) {
  for (const room of data?.rooms ?? []) {
    const photo = room.photos.find((p) => p.file === file);
    if (photo) return photo.title;
  }
  return file;
}

const hasFiles = (event) => [...(event.dataTransfer?.types ?? [])].includes('Files');

let view = null; // the tab on the page while unlocked

/**
 * Show (or bring up to date) the "새 사진" tab in `root`. ctx = {gh, config, refreshStatus, reloadExhibit}.
 * Resolves once the queue has been read (errors are shown in the view, not thrown).
 */
export function renderQueue(root, ctx) {
  if (!view || view.root !== root || !view.list.isConnected) {
    closeQueue();
    view = build(root);
  }
  view.ctx = ctx;
  view.store.gh = ctx.gh;
  view.store.repo = ctx.config.repo;
  return load(view);
}

/** The page is locked: stop looking, stop uploading, and let go of the photos and thumbnails held in memory. */
export function closeQueue() {
  if (!view) return;
  const v = view;
  view = null;
  clearTimeout(v.timer);
  for (const timer of v.later) clearTimeout(timer);
  v.store.close();
  for (const node of v.nodes.values()) if (node.url) URL.revokeObjectURL(node.url);
  v.nodes.clear();
}

/** Hand files to the tab as if they had been picked there (window.__nocturne.addFiles, for local browser checks). */
export function addFiles(files) {
  if (!view) throw new Error('새 사진 탭이 열려 있지 않아요');
  return view.store.addFiles(files);
}

let dropGuard = false;

function build(root) {
  const input = el('input');
  input.type = 'file';
  input.accept = 'image/jpeg,image/png';
  input.multiple = true;
  input.hidden = true;
  input.tabIndex = -1;
  const pick = button('사진 고르기', 'btn btn-main');
  const zone = el('div', 'q-drop', pick, el('p', 'q-drop-text', '여기에 끌어다 놓아도 돼요. 위치 정보는 올리기 전에 이 기기에서 지워요.'), input);

  const heading = el('h2', 'q-title', '대기열');
  const status = live(el('p', 'msg'));
  status.tabIndex = -1; // focus lands here when the card that had it leaves the list
  const retry = button('다시 불러오기', 'btn');
  retry.hidden = true;
  const empty = el('p', 'q-empty', EMPTY);
  empty.hidden = true;
  const list = el('ul', 'q-cards');
  list.setAttribute('aria-labelledby', 'q-title');
  heading.id = 'q-title';
  root.replaceChildren(zone, el('div', 'q-top', heading, status, retry), empty, list);

  const v = { root, ctx: null, store: new QueueStore(null, null), list, status, retry, empty, loaded: false,
    nodes: new Map(), fading: new Set(), later: new Set(), timer: 0, polling: false, ids: 0 };
  v.store.onChange = () => draw(v);

  const take = (files) => {
    if (files.length && v === view) v.store.addFiles(files);
  };
  pick.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    const files = [...input.files];
    input.value = ''; // the same photo can be picked again
    take(files);
  });
  zone.addEventListener('dragover', (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
    zone.classList.add('is-over');
  });
  zone.addEventListener('dragleave', (event) => {
    if (!zone.contains(event.relatedTarget)) zone.classList.remove('is-over');
  });
  zone.addEventListener('drop', (event) => {
    if (!hasFiles(event)) return;
    event.preventDefault();
    zone.classList.remove('is-over');
    take([...event.dataTransfer.files]);
  });
  if (!dropGuard) {
    // a photo dropped next to the box would make the browser open it and leave the page (and the uploads)
    dropGuard = true;
    window.addEventListener('dragover', (event) => {
      if (!hasFiles(event) || event.defaultPrevented) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = 'none';
    });
    window.addEventListener('drop', (event) => {
      if (hasFiles(event)) event.preventDefault();
    });
  }
  retry.addEventListener('click', () => load(v));
  return v;
}

/** Read the queue and photos.json, then draw; keeps looking while anything is in the queue. */
async function load(v) {
  if (!v.loaded) say(v.status, LOADING);
  try {
    const result = await v.store.load();
    if (v !== view) return;
    v.loaded = true;
    v.retry.hidden = true;
    say(v.status, '');
    noticed(v, result);
  } catch (error) {
    if (v !== view) return;
    const text = problem(error);
    if (text === null) return;
    say(v.status, text, true);
    v.retry.hidden = false;
  }
  draw(v);
}

/** One look at the queue; a failure shows here and the next look tries again. Looks never overlap. */
async function poll(v) {
  v.timer = 0;
  if (v !== view) return;
  v.polling = true;
  try {
    const result = await v.store.refresh();
    if (v !== view) return;
    if (v.status.classList.contains('is-error')) say(v.status, '');
    v.retry.hidden = true;
    noticed(v, result);
  } catch (error) {
    if (v !== view) return;
    const text = problem(error);
    if (text !== null) say(v.status, text, true);
  } finally {
    v.polling = false;
  }
  draw(v); // schedules the next look
}

function ensurePoll(v) {
  if (v !== view || v.timer || v.polling || !v.store.watching()) return;
  v.timer = setTimeout(() => poll(v), v.ctx.config.pollMs);
}

/** Look now (after an action whose item had already left the queue), unless a look is on its way. */
function pollNow(v) {
  if (v.polling) return;
  clearTimeout(v.timer);
  poll(v);
}

/** A worker published something: the exhibit and the site build status are out of date. */
function noticed(v, { published }) {
  if (published.length === 0) return;
  v.ctx.reloadExhibit();
  v.ctx.refreshStatus(true);
}

/** Draw the cards as the store has them, in their order; a card that left the list takes its thumbnail URL along. */
function draw(v) {
  if (v !== view) return;
  let i = 0;
  for (const card of v.store.cards.values()) {
    const node = v.nodes.get(card.key) ?? makeCard(v, card);
    fillCard(v, node, card);
    place(v.list, node.li, i++);
    if (card.stage === 'published' && !v.fading.has(card.key)) fade(v, card);
  }
  for (const [key, node] of v.nodes) {
    if (!v.store.cards.has(key)) dropNode(v, key, node);
  }
  v.empty.hidden = !v.loaded || v.store.cards.size > 0;
  ensurePoll(v);
}

function dropNode(v, key, node, message = '') {
  const hadFocus = node.li.contains(document.activeElement);
  node.li.remove();
  if (node.url) URL.revokeObjectURL(node.url);
  v.nodes.delete(key);
  v.fading.delete(key);
  if (hadFocus) {
    say(v.status, message || `${LEFT}: ${node.title.textContent}`); // never an older message
    v.status.focus(); // the focus would otherwise fall back to the top of the page
  }
}

/** A published card shows 게시됨 for a few seconds, then leaves the list. */
function fade(v, card) {
  v.fading.add(card.key);
  const timer = setTimeout(() => {
    v.later.delete(timer);
    if (v !== view) return;
    const node = v.nodes.get(card.key);
    v.store.forget(card.key);
    if (node) dropNode(v, card.key, node, node.leaving || `${PUBLISHED}: ${node.title.textContent}`);
    draw(v);
  }, PUBLISHED_MS);
  v.later.add(timer);
}

function makeCard(v, card) {
  const id = `q-${++v.ids}`;
  const img = el('img');
  img.alt = ''; // the card's name says which photo it is
  img.decoding = 'async';
  const figure = el('figure', 'q-thumb is-missing', img);
  img.addEventListener('load', () => figure.classList.remove('is-missing'));
  img.addEventListener('error', () => figure.classList.add('is-missing'));

  const title = el('p', 'q-name');
  title.id = `${id}-name`;
  const tag = el('span', 'tag', REDRAFT);
  const state = el('p', 'q-state');
  const detail = el('p', 'q-detail');
  const status = live(el('div', 'q-status', state, detail));
  status.tabIndex = -1;
  const info = el('ul', 'q-info');
  info.setAttribute('aria-label', '사진 정보');

  const again = button('다시 올리기', 'btn btn-main');
  const retry = button('다시 시도', 'btn btn-main');
  const manual = button('직접 입력해서 게시', 'btn');
  const discard = button('버리기', 'btn btn-delete');
  const note = el('p', 'q-note', DISCARD_NOTE); // a queue folder was committed to the public repository: deleting it is not forgetting it
  note.id = `${id}-note`;
  const actions = el('div', 'q-actions', again, retry, manual, discard, note);
  const msg = live(el('p', 'msg'));
  msg.tabIndex = -1;

  const form = buildForm(id);
  manual.setAttribute('aria-expanded', 'false');
  manual.setAttribute('aria-controls', form.node.id);

  const body = el('div', 'q-body', el('div', 'q-head', title, tag), status, info);
  const box = el('div', 'q-card', figure, body, actions, msg, form.node);
  box.setAttribute('role', 'group');
  box.setAttribute('aria-labelledby', title.id);
  const node = { key: card.key, li: el('li', '', box), box, figure, img, title, tag, state, detail, status, info, infoKey: '',
    again, retry, manual, discard, note, actions, msg, form, url: '', thumb: 'none', busy: false, leaving: '' };
  v.nodes.set(card.key, node);

  const on = (b, action) => b.addEventListener('click', () => {
    if (b.getAttribute('aria-disabled') !== 'true') action(v, node);
  });
  on(again, reuploadCard);
  on(retry, retryCard);
  on(discard, discardCard);
  on(manual, (v2, n) => openForm(v2, n, n.form.node.hidden));
  form.cancel.addEventListener('click', () => {
    openForm(v, node, false);
    node.manual.focus();
  });
  form.node.addEventListener('submit', (event) => {
    event.preventDefault();
    if (form.submit.getAttribute('aria-disabled') !== 'true') publishCard(v, node);
  });
  return node;
}

function buildForm(id) {
  const field = (text, control, key, wide = false) => {
    control.id = `${id}-${key}`;
    const tag = el('label', '', text);
    tag.htmlFor = control.id;
    return el('div', wide ? 'field field-wide' : 'field', tag, control);
  };
  const room = el('select');
  const title = el('input');
  title.type = 'text';
  title.autocomplete = 'off';
  const alt = el('textarea');
  alt.rows = 2;
  const submit = el('button', 'btn btn-main', '게시');
  submit.type = 'submit';
  const cancel = button('취소', 'btn btn-quiet');
  const node = el('form', 'q-manual',
    el('div', 'row-fields', field('방', room, 'room'), field('제목', title, 'title'), field('설명', alt, 'alt', true)),
    el('div', 'q-actions', submit, cancel));
  node.id = `${id}-form`;
  node.noValidate = true;
  node.hidden = true;
  node.setAttribute('aria-label', '직접 입력해서 게시');
  return { node, room, title, alt, submit, cancel };
}

/** Bring a card's text, thumbnail, information line and buttons up to date with the store. */
function fillCard(v, node, card) {
  const redraft = card.item?.kind === 'redraft';
  node.title.textContent = redraft ? titleOf(v.store.data, card.item.file) : (card.name ?? card.fileName ?? '');
  node.tag.hidden = !redraft;
  showThumb(v, node, card);

  let state;
  let tone;
  let detail = '';
  if (card.stage === 'upload') {
    [state, tone] = [UPLOADING, 'busy'];
  } else if (card.stage === 'upload-failed') {
    [state, tone] = [UPLOAD_FAILED, 'error'];
    detail = problem(card.error) ?? '';
  } else if (card.stage === 'published') {
    [state, tone] = [PUBLISHED, 'done'];
  } else if (card.broken) {
    [state, tone] = [UNREADABLE_ITEM, 'error']; // item.json missing or unreadable: it can only be thrown away
  } else {
    ({ text: state, tone } = label(card.item));
    if (card.item.status === 'failed') detail = typeof card.item.error === 'string' ? card.item.error : '';
    else detail = hint(card.item, v.store.now()); // waiting long, or a claim gone stale (버리기 then shows)
  }
  node.box.dataset.tone = tone;
  say(node.state, state);
  say(node.detail, detail);
  node.detail.hidden = !detail;

  // date, camera, 흑백 감지, 위치 정보 삭제됨: once the photo has been processed (a redraft is a photo already on show)
  const facts = redraft ? null : (card.item ?? card.pending ?? card.facts);
  const lines = facts ? [facts.date, facts.camera, facts.grayscale ? GRAYSCALE : null, NO_LOCATION].filter((t) => typeof t === 'string' && t) : [];
  const infoKey = JSON.stringify(lines);
  if (node.infoKey !== infoKey) {
    node.info.replaceChildren(...lines.map((text) => el('li', '', text)));
    node.infoKey = infoKey;
  }
  node.info.hidden = lines.length === 0;

  const failed = card.stage === 'queue' && card.item?.status === 'failed';
  node.again.hidden = !(card.stage === 'upload-failed' && !card.final);
  node.retry.hidden = !failed;
  node.manual.hidden = !(failed && !redraft);
  node.discard.hidden = !(card.stage === 'upload-failed' || (card.stage === 'queue' && (card.broken || canDiscard(card.item, v.store.now()))));
  node.note.hidden = node.discard.hidden || card.stage !== 'queue'; // an upload that failed was never committed
  if (node.note.hidden) node.discard.removeAttribute('aria-describedby');
  else node.discard.setAttribute('aria-describedby', node.note.id);
  node.actions.hidden = [node.again, node.retry, node.manual, node.discard].every((b) => b.hidden);
  if (node.manual.hidden && !node.form.node.hidden) openForm(v, node, false);
  updateButtons(node);
}

/** The card's thumbnail: one this page made, the queue's thumb.jpg (read once), or the site's for a redraft. */
function showThumb(v, node, card) {
  if (node.thumb === 'shown' || node.thumb === 'asking') return;
  if (card.item?.kind === 'redraft') {
    node.img.src = `../photos/thumb/${encodeURIComponent(card.item.file)}.jpg`;
    node.thumb = 'shown';
    return;
  }
  if (card.thumb) {
    setUrl(node, URL.createObjectURL(card.thumb));
    node.thumb = 'shown';
    return;
  }
  if (card.stage !== 'queue') return;
  node.thumb = 'asking';
  v.store.thumbOf(card).then((blob) => {
    if (v !== view || v.nodes.get(card.key) !== node) return;
    if (blob) setUrl(node, URL.createObjectURL(blob));
    node.thumb = 'shown'; // a missing thumb.jpg stays an empty box
  }, () => {
    if (node.thumb === 'asking') node.thumb = 'none'; // offline for a moment: the next draw asks again
  });
}

function setUrl(node, url) {
  if (node.url) URL.revokeObjectURL(node.url);
  node.url = url;
  node.img.src = url;
}

function updateButtons(node) {
  for (const b of [node.again, node.retry, node.manual, node.discard, node.form.submit]) b.setAttribute('aria-disabled', String(node.busy));
}

/** Show or hide the form of 직접 입력해서 게시; opening it fills the rooms from photos.json and puts the focus on 제목. */
function openForm(v, node, open) {
  const { form } = node;
  if (open) {
    const rooms = v.store.data?.rooms ?? [];
    if (rooms.length === 0) {
      say(node.msg, NO_DATA, true);
      return;
    }
    const keep = form.room.value;
    form.room.replaceChildren(...rooms.map((room, i) => {
      const option = el('option', '', `${roman(i + 1)} ${room.name}`);
      option.value = room.id;
      return option;
    }));
    if (rooms.some((room) => room.id === keep)) form.room.value = keep;
  }
  form.node.hidden = !open;
  node.manual.setAttribute('aria-expanded', String(open));
  if (open) form.title.focus();
}

// --- actions -----------------------------------------------------------------------------------

/**
 * Run one action for a card: its buttons are off and `busyText` shows meanwhile. Resolves to {ok: true, value} or
 * {ok: false} once the problem is shown on the card.
 */
async function act(v, node, busyText, task) {
  node.busy = true;
  updateButtons(node);
  say(node.msg, busyText);
  try {
    const value = await task();
    node.busy = false;
    say(node.msg, '');
    return { ok: true, value };
  } catch (error) {
    node.busy = false;
    const text = problem(error);
    if (text !== null) say(node.msg, text, true);
    return { ok: false };
  } finally {
    if (v === view) draw(v);
    keepFocus(v, node);
  }
}

/** A button that was just pressed may be gone now (the state moved on): the focus goes to what the card says now. */
function keepFocus(v, node) {
  const active = document.activeElement;
  if (!node.li.isConnected || !(active === document.body || (node.box.contains(active) && active.closest('[hidden]')))) return;
  (node.msg.textContent ? node.msg : node.status).focus();
}

async function reuploadCard(v, node) {
  const card = v.store.cards.get(node.key);
  const sent = v.store.reupload(node.key); // the card follows through the store's changes
  node.status.focus(); // 다시 올리기 is gone now; the status line reads 올리는 중… and what follows
  await sent;
  if (v === view && card?.stage === 'published') { // an earlier try had landed and the AI has published it since
    v.ctx.reloadExhibit();
    v.ctx.refreshStatus();
  }
}

async function retryCard(v, node) {
  const done = await act(v, node, '다시 시도하는 중…', () => v.store.retry(node.key));
  if (done.ok && !done.value) pollNow(v); // nothing to retry any more: the look shows what it is now
}

async function discardCard(v, node) {
  const card = v.store.cards.get(node.key);
  if (!card) return;
  const name = node.title.textContent;
  if (card.stage === 'upload-failed') {
    v.store.dropUpload(node.key); // nothing was sent: nothing to commit
    say(v.status, `버렸어요: ${name}`);
    v.status.focus();
    return;
  }
  const done = await act(v, node, '버리는 중…', () => v.store.discard(node.key));
  if (!done.ok) return;
  if (!done.value) {
    pollNow(v); // the folder holds something else now (published, gone, another upload): the look shows it
    return;
  }
  say(v.status, `버렸어요: ${name}`);
  v.status.focus();
  const file = done.value.item?.kind === 'redraft' ? done.value.item.file : null;
  v.ctx.reloadExhibit(file ? { forget: [file] } : undefined); // a redraft thrown away was not written by the AI
}

async function publishCard(v, node) {
  const { form } = node;
  const fields = { room: form.room.value, title: form.title.value, alt: form.alt.value };
  const done = await act(v, node, '게시하는 중…', () => v.store.publish(node.key, fields));
  if (!done.ok) return;
  const { sha, why } = done.value;
  if (why === 'published') { // the AI got there first: nothing was written; its text can be changed in 전시 중
    openForm(v, node, false);
    node.leaving = AI_FIRST;
    say(node.msg, AI_FIRST);
    draw(v);
    node.msg.focus();
    v.ctx.reloadExhibit();
    v.ctx.refreshStatus();
    return;
  }
  if (!sha) {
    pollNow(v); // retried elsewhere, thrown away, or another upload under the name: the look shows what is there
    return;
  }
  form.title.value = '';
  form.alt.value = '';
  openForm(v, node, false);
  draw(v);
  node.status.focus(); // reads 게시됨
  v.ctx.refreshStatus(true);
  v.ctx.reloadExhibit();
}
