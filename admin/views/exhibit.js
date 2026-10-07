// The "전시 중" tab: every exhibited photo by room, with its badges, room, title and description, and four actions:
// 저장, 대표로 지정, 삭제 (after a confirm dialog) and AI로 다시 쓰기 (a redraft item in the queue).
//
// ExhibitStore (DOM-free, tested in Node) holds photos.json as the page knows it. Each change is one commit whose
// build re-reads src/photos.json at the commit it builds on and applies the change with gallery.js, and 저장 sends
// only the fields the person changed, so a change made elsewhere in the meantime is never overwritten. Reads and
// commits run one after another, so an older read can never land after a newer commit.
// The view keeps rows by file name across redraws: a row the person is typing in is neither rebuilt nor moved
// without need, and a field they changed but did not save keeps its text. Photo and AI text only ever reaches the
// page through textContent and value.

import { AuthError, GitHubError } from '../lib/github.js';
import { badges, deletePhoto, GalleryError, roman, saveEdits, serialize, setCover } from '../lib/gallery.js';
import { newItem } from '../lib/queue.js';

const DATA = 'src/photos.json';
const REDRAFT = 'redraft-'; // queue/redraft-<file>/item.json asks the AI to write a photo's title and description again
const redraftPath = (file) => `queue/${REDRAFT}${file}/item.json`;
const thumbSrc = (file) => `../photos/thumb/${encodeURIComponent(file)}.jpg`;
const TEXTS = ['title', 'alt'];

const LOADING = '불러오는 중…';
const REDRAFTING = 'AI가 다시 쓰는 중…';
const REDRAFTED = 'AI가 새로 썼어요';
const DELETE_TEXT = '이 사진을 전시에서 뺄까요? 사이트용 사본이 지워집니다. 이미 게시한 사진은 GitHub 기록에 남습니다.';
const COVER_NOTE = '대표 사진은 다른 사진을 대표로 지정한 뒤 지울 수 있어요';
const NO_DATA = 'src/photos.json을 찾을 수 없어요';
const UNKNOWN_PROBLEM = '문제가 생겼어요. 새로고침 후 다시 해 주세요';

// --- the exhibition as this page knows it (no DOM) ---------------------------------------------

/** Files that have a redraft item waiting in a listing of queue/. */
function redraftsIn(listing) {
  return new Set(listing.filter((e) => e.type === 'dir' && e.name.startsWith(REDRAFT)).map((e) => e.name.slice(REDRAFT.length)));
}

function titleOf(data, file) {
  for (const room of data?.rooms ?? []) {
    const photo = room.photos.find((p) => p.file === file);
    if (photo) return photo.title;
  }
  return file;
}

/**
 * What 저장 sends for a row: only the fields the person changed, i.e. whose value differs from what the row last
 * showed (`shown`: {room, title, alt}); title and description are compared and sent trimmed. A field left alone is
 * not sent, so it can never overwrite a newer value saved elsewhere (another device, the AI).
 */
export function editsFor(shown, typed) {
  const edits = {};
  if (typed.room !== shown.room) edits.room = typed.room;
  for (const key of TEXTS) {
    const value = typed[key].trim();
    if (value !== shown[key]) edits[key] = value;
  }
  return edits;
}

export class ExhibitStore {
  data = null; // src/photos.json as last read or committed
  pending = new Set(); // files with a redraft waiting in queue/
  #line = Promise.resolve();

  constructor(gh, repo) {
    this.gh = gh;
    this.repo = repo;
  }

  /** Reads and commits run one after another: a read that began before a commit can never land after it. */
  #serial(task) {
    const run = this.#line.then(task);
    this.#line = run.catch(() => {});
    return run;
  }

  /** Read photos.json and the queue. Resolves to the files whose redraft is gone since the last look (the AI wrote them). */
  load() {
    return this.#serial(async () => {
      const [file, queue] = await Promise.all([this.gh.getJSON(this.repo, DATA), this.gh.listDir(this.repo, 'queue')]);
      if (!file) throw new GalleryError(NO_DATA);
      const now = redraftsIn(queue);
      const finished = [...this.pending].filter((name) => !now.has(name));
      this.data = file.data;
      this.pending = now;
      return finished;
    });
  }

  /** A redraft was thrown away (the AI did not write it): stop waiting for it, and never report it as written. */
  forget(file) {
    this.pending.delete(file);
  }

  /** A look at the queue only. done: a waiting redraft is gone (then load() again); added: one more is waiting now. */
  async checkQueue() {
    const now = redraftsIn(await this.gh.listDir(this.repo, 'queue'));
    if ([...this.pending].some((name) => !now.has(name))) return { done: true, added: false };
    const added = [...now].filter((name) => !this.pending.has(name));
    for (const name of added) this.pending.add(name);
    return { done: false, added: added.length > 0 };
  }

  /** The changes 저장 would send (editsFor), checked against the exhibition as known; GalleryError if refused. */
  edits(file, shown, typed) {
    const changes = editsFor(shown, typed);
    saveEdits(structuredClone(this.data), file, changes);
    return changes;
  }

  /** Save changes from edits() in one commit; {} still counts as "a person looked at it" (the AI confidence goes). */
  save(file, changes) {
    return this.#serial(() => this.#commitPhotos(`사진 수정: ${changes.title ?? titleOf(this.data, file)}`, (data) => saveEdits(data, file, changes)));
  }

  cover(file) {
    return this.#serial(() => this.#commitPhotos(`대표 사진 변경: ${titleOf(this.data, file)}`, (data) => setCover(data, file)));
  }

  /** Take a photo out: its entry, its two site copies and a redraft still waiting for it go in one commit. */
  remove(file) {
    return this.#serial(async () => {
      const sha = await this.#commitPhotos(`사진 삭제: ${titleOf(this.data, file)}`, (data) => deletePhoto(data, file), async (read) => ({
        [`photos/full/${file}.jpg`]: null,
        [`photos/thumb/${file}.jpg`]: null,
        ...((await read(redraftPath(file))) ? { [redraftPath(file)]: null } : {}),
      }));
      this.pending.delete(file);
      return sha;
    });
  }

  /** Ask the AI to write a photo's texts again, unless that is already asked. Resolves to the commit sha or null. */
  redraft(file) {
    const name = REDRAFT + file;
    const path = redraftPath(file);
    return this.#serial(async () => {
      const sha = await this.gh.commitFiles(this.repo, {
        message: `대기열: ${name} 올림`,
        build: async (read) => ((await read(path)) ? null : { files: { [path]: newItem({ name, kind: 'redraft', grayscale: false, now: Date.now(), file }) } }),
      });
      this.pending.add(file);
      return sha;
    });
  }

  /**
   * One commit of photos.json as `change(data)` leaves it, plus `others(read)` (more files, e.g. deletes), built on the
   * newest main. Resolves to the sha, or null when nothing changed; afterwards `data` is photos.json as committed.
   */
  async #commitPhotos(message, change, others = async () => ({})) {
    let result = null;
    const sha = await this.gh.commitFiles(this.repo, {
      message,
      build: async (read) => {
        const found = await read(DATA);
        if (!found) throw new GalleryError(NO_DATA);
        const before = serialize(found.data);
        change(found.data);
        result = found.data;
        const files = await others(read);
        const text = serialize(found.data);
        if (text === before && Object.keys(files).length === 0) return null; // nothing to commit
        return { files: { [DATA]: text, ...files } };
      },
    });
    this.data = result;
    return sha;
  }
}

// --- the view ----------------------------------------------------------------------------------

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

/** The message to show for a failed action; null for a rejected token (the page is already back at setup). */
function problem(error) {
  if (error instanceof AuthError) return null;
  if (error instanceof GalleryError || error instanceof GitHubError) return error.message;
  return UNKNOWN_PROBLEM;
}

let view = null; // the exhibit on the page; renderExhibit on the same root keeps its rows

/**
 * Show (or bring up to date) the exhibit in `root`. ctx = {gh, config, refreshStatus, reloadExhibit}.
 * `forget`: files whose redraft was thrown away in the queue (their rows stop waiting, without "AI가 새로 썼어요").
 * Resolves once photos.json has been read (errors are shown in the view, not thrown).
 */
export function renderExhibit(root, ctx, { forget = [] } = {}) {
  if (!view || view.root !== root || !view.list.isConnected) {
    if (view) clearTimeout(view.timer);
    view = build(root);
  }
  view.ctx = ctx;
  view.store.gh = ctx.gh;
  view.store.repo = ctx.config.repo;
  for (const file of forget) view.store.forget(file);
  return load(view);
}

function build(root) {
  const total = el('p', 'ex-total');
  const status = el('p', 'msg');
  status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite');
  status.tabIndex = -1; // focus lands here after a delete
  const retry = button('다시 불러오기', 'btn');
  retry.hidden = true;
  const list = el('div', 'ex-rooms');
  const dialog = buildDialog();
  root.replaceChildren(el('div', 'ex-top', total, status, retry), list, dialog.node);
  const v = { root, ctx: null, store: new ExhibitStore(null, null), total, status, retry, list, dialog, rows: new Map(), rooms: new Map(), timer: 0 };
  retry.addEventListener('click', () => load(v));
  return v;
}

/** Read photos.json and the queue, then redraw; keeps polling while a redraft is waiting. */
async function load(v) {
  if (!v.store.data) say(v.status, LOADING);
  try {
    const finished = await v.store.load();
    if (v !== view) return;
    v.retry.hidden = true;
    if (v.status.textContent === LOADING || v.status.classList.contains('is-error')) say(v.status, '');
    draw(v);
    for (const name of finished) {
      const row = v.rows.get(name);
      if (row) say(row.msg, REDRAFTED);
    }
  } catch (error) {
    if (v !== view) return;
    const text = problem(error);
    if (text === null) return;
    say(v.status, text, true);
    v.retry.hidden = false;
  }
  schedulePoll(v);
}

function schedulePoll(v) {
  clearTimeout(v.timer);
  if (v.store.pending.size === 0 || v !== view) return;
  v.timer = setTimeout(() => poll(v), v.ctx.config.pollMs);
}

/** While redrafts wait: look at the queue; once one is gone, the AI has written it, so read photos.json again. */
async function poll(v) {
  if (v !== view || !v.list.isConnected) return; // locked or replaced meanwhile
  try {
    const { done, added } = await v.store.checkQueue();
    if (v !== view) return;
    if (done) {
      await load(v); // schedules the next poll itself
      return;
    }
    if (added) draw(v);
  } catch {
    // offline for a moment: look again next time
  }
  schedulePoll(v);
}

/** Put `node` at `index` among parent's children, moving it only when it is not already there. */
function place(parent, node, index) {
  const at = parent.children[index] ?? null;
  if (at !== node) parent.insertBefore(node, at);
}

/** Draw the exhibition as the store knows it now (always the newest state, whichever action asked). */
function draw(v) {
  const { data } = v.store;
  const active = document.activeElement;
  const roomOptions = data.rooms.map((room, i) => [room.id, `${roman(i + 1)} ${room.name}`]);
  const optionsKey = JSON.stringify(roomOptions);
  const seenRooms = new Set();
  const seenRows = new Set();
  let count = 0;

  data.rooms.forEach((room, i) => {
    const section = v.rooms.get(room.id) ?? makeRoom(v, room.id);
    section.num.textContent = roman(i + 1);
    section.name.textContent = room.name;
    section.en.textContent = room.name_en ?? '';
    section.count.textContent = `${room.photos.length}점`;
    place(v.list, section.node, i);
    seenRooms.add(room.id);
    room.photos.forEach((photo, j) => {
      const row = v.rows.get(photo.file) ?? makeRow(v, photo.file);
      fillRow(v, row, photo, room.id, roomOptions, optionsKey);
      place(section.list, row.node, j);
      seenRows.add(photo.file);
      count++;
    });
  });
  for (const [file, row] of v.rows) {
    if (!seenRows.has(file)) {
      row.node.remove();
      v.rows.delete(file);
    }
  }
  for (const [id, section] of v.rooms) {
    if (!seenRooms.has(id)) {
      section.node.remove();
      v.rooms.delete(id);
    }
  }
  v.total.textContent = `사진 ${count}점`;

  // a row that moved to another room lost focus on the way: give it back (or to the row's message if it is hidden now)
  if (active instanceof HTMLElement && active !== document.activeElement && v.list.contains(active)) {
    active.focus();
    if (document.activeElement !== active) active.closest('.row')?.querySelector('.msg')?.focus();
  }
}

function makeRoom(v, id) {
  const num = el('span', 'room-num');
  num.setAttribute('aria-hidden', 'true');
  const name = document.createTextNode('');
  const en = el('span', 'room-en');
  en.lang = 'en';
  const heading = el('h2', '', name, ' ', en);
  heading.id = `ex-room-${id}`;
  const count = el('span', 'room-count');
  const list = el('ul', 'rows');
  const node = el('section', 'room', el('div', 'room-head', num, heading, count), list);
  node.setAttribute('aria-labelledby', heading.id);
  const section = { id, node, num, name, en, count, list };
  v.rooms.set(id, section);
  return section;
}

function makeRow(v, file) {
  const img = el('img');
  img.loading = 'lazy';
  img.decoding = 'async';
  img.src = thumbSrc(file);
  const figure = el('figure', 'row-thumb', img);
  img.addEventListener('error', () => figure.classList.add('is-missing')); // not on the site yet (published a moment ago)

  const field = (label, control, id, wide = false) => {
    control.id = id;
    const tag = el('label', '', label);
    tag.htmlFor = id;
    return el('div', wide ? 'field field-wide' : 'field', tag, control);
  };
  const room = el('select');
  const title = el('input');
  title.type = 'text';
  title.autocomplete = 'off';
  const alt = el('textarea');
  alt.rows = 2;
  const fields = el('div', 'row-fields',
    field('방', room, `ex-room-of-${file}`), field('제목', title, `ex-title-${file}`), field('설명', alt, `ex-alt-${file}`, true));

  const save = button('저장', 'btn btn-main');
  const redraft = button('AI로 다시 쓰기', 'btn');
  const cover = button('대표로 지정', 'btn');
  const remove = button('삭제', 'btn btn-delete');
  const coverNote = el('p', 'cover-note', COVER_NOTE);
  const msg = el('p', 'msg');
  msg.setAttribute('role', 'status');
  msg.setAttribute('aria-live', 'polite');
  msg.tabIndex = -1;
  const tags = el('ul', 'tags');
  tags.setAttribute('aria-label', '표시');

  const group = el('div', 'row', figure, el('div', 'row-body', tags, fields, el('div', 'row-actions', save, redraft, cover, remove, coverNote), msg));
  group.setAttribute('role', 'group');
  const row = { file, node: el('li', '', group), group, img, tags, room, title, alt, save, redraft, cover, remove, coverNote, msg,
    optionsKey: '', shown: null, photo: null, busy: false };
  v.rows.set(file, row);

  const on = (b, action) => b.addEventListener('click', () => {
    if (b.getAttribute('aria-disabled') !== 'true') action(v, row);
  });
  on(save, saveRow);
  on(cover, coverRow);
  on(remove, deleteRow);
  on(redraft, redraftRow);
  return row;
}

/**
 * Turn the row's buttons off while it is busy; 저장 and AI로 다시 쓰기 also while a redraft waits (the AI is about to
 * write the texts). aria-disabled rather than `disabled`, so the button that was pressed keeps the keyboard focus.
 */
function updateButtons(v, row) {
  const off = (b, yes) => b.setAttribute('aria-disabled', String(yes));
  const redrafting = v.store.pending.has(row.file);
  off(row.save, row.busy || redrafting);
  off(row.redraft, row.busy || redrafting);
  off(row.cover, row.busy);
  off(row.remove, row.busy);
}

/**
 * Bring a row up to date. row.shown is what the row showed after the last draw: a field still holding that text
 * takes the new value; a field the person changed (and did not save) keeps its text.
 */
function fillRow(v, row, photo, roomId, roomOptions, optionsKey) {
  const isCover = v.store.data.cover === photo.file;
  row.photo = photo;
  row.group.setAttribute('aria-label', photo.title);
  if (row.img.alt !== photo.alt) row.img.alt = photo.alt;

  const tags = badges(photo);
  if (isCover) tags.unshift('대표 사진');
  row.tags.replaceChildren(...tags.map((text) => el('li', text === '대표 사진' ? 'tag tag-cover' : 'tag', text)));
  row.tags.hidden = tags.length === 0;

  const before = row.shown;
  if (row.optionsKey !== optionsKey) {
    const keep = row.room.value;
    row.room.replaceChildren(...roomOptions.map(([id, text]) => {
      const option = el('option', '', text);
      option.value = id;
      return option;
    }));
    row.room.value = before && keep !== before.room ? keep : roomId;
    row.optionsKey = optionsKey;
  } else if (!before || row.room.value === before.room) {
    row.room.value = roomId;
  }
  if (!before || row.title.value === before.title) row.title.value = photo.title;
  if (!before || row.alt.value === before.alt) row.alt.value = photo.alt;
  row.shown = { room: roomId, title: photo.title, alt: photo.alt };

  row.cover.hidden = isCover;
  row.remove.hidden = isCover;
  row.coverNote.hidden = !isCover;
  updateButtons(v, row);
  const redrafting = v.store.pending.has(photo.file);
  if (redrafting && !row.busy) say(row.msg, REDRAFTING);
  else if (!redrafting && row.msg.textContent === REDRAFTING) say(row.msg, '');
}

function setBusy(v, row, busy, text = '') {
  row.busy = busy;
  updateButtons(v, row);
  if (busy) say(row.msg, text);
}

// --- actions -----------------------------------------------------------------------------------

/**
 * Run one change for a row: its buttons are off and `busyText` shows meanwhile. Resolves to {ok: true, value}
 * with what `task` returned, or {ok: false} once the problem is shown on the row.
 */
async function act(v, row, busyText, task) {
  setBusy(v, row, true, busyText);
  try {
    const value = await task();
    setBusy(v, row, false);
    return { ok: true, value };
  } catch (error) {
    setBusy(v, row, false);
    const text = problem(error);
    if (text !== null) say(row.msg, text, true);
    return { ok: false };
  }
}

async function saveRow(v, row) {
  const typed = { room: row.room.value, title: row.title.value, alt: row.alt.value };
  let changes;
  try {
    changes = v.store.edits(row.file, row.shown, typed); // only what the person changed; checked before anything is sent
  } catch (error) {
    say(row.msg, problem(error) ?? UNKNOWN_PROBLEM, true);
    return;
  }
  const done = await act(v, row, '저장하는 중…', async () => {
    const sha = await v.store.save(row.file, changes);
    // a sent field that still holds what was sent takes the saved (trimmed) text; newer typing stays
    row.shown = { ...row.shown, ...Object.fromEntries(Object.keys(changes).map((key) => [key, typed[key]])) };
    draw(v);
    return sha;
  });
  if (!done.ok) return;
  say(row.msg, done.value ? '저장했어요' : '바뀐 것이 없어요');
  if (done.value) v.ctx.refreshStatus(true);
}

async function coverRow(v, row) {
  const done = await act(v, row, '바꾸는 중…', async () => {
    const sha = await v.store.cover(row.file);
    draw(v);
    return sha;
  });
  if (!done.ok) return;
  say(row.msg, '대표 사진으로 지정했어요');
  if (done.value) v.ctx.refreshStatus(true);
}

async function deleteRow(v, row) {
  const title = row.photo.title;
  if (!(await confirmDelete(v, title))) {
    row.remove.focus();
    return;
  }
  const done = await act(v, row, '지우는 중…', async () => {
    const sha = await v.store.remove(row.file);
    draw(v);
    return sha;
  });
  if (!done.ok) {
    if (row.node.isConnected) row.remove.focus();
    return;
  }
  say(v.status, `지웠어요: ${title}`);
  v.status.focus();
  v.ctx.refreshStatus(true);
}

async function redraftRow(v, row) {
  // already waiting (asked from this or another device): nothing is committed, the row just shows it
  const done = await act(v, row, '대기열에 올리는 중…', () => v.store.redraft(row.file));
  if (!done.ok) return;
  draw(v); // shows "AI가 다시 쓰는 중…" and turns 저장 and AI로 다시 쓰기 off
  schedulePoll(v);
}

// --- the delete dialog -------------------------------------------------------------------------

function buildDialog() {
  const node = el('dialog', 'confirm');
  const title = el('h2', 'confirm-title', '사진 삭제');
  title.id = 'ex-delete-title';
  const photo = el('p', 'confirm-photo');
  photo.id = 'ex-delete-photo';
  const text = el('p', 'confirm-text', DELETE_TEXT);
  text.id = 'ex-delete-text';
  node.setAttribute('aria-labelledby', title.id);
  node.setAttribute('aria-describedby', `${photo.id} ${text.id}`);
  const cancel = button('취소', 'btn btn-quiet');
  const ok = button('삭제', 'btn btn-main');
  node.append(title, photo, text, el('div', 'confirm-actions', cancel, ok));
  cancel.addEventListener('click', () => node.close('cancel'));
  ok.addEventListener('click', () => node.close('delete'));
  node.addEventListener('click', (event) => { // a click on the backdrop, outside the box, is a cancel
    if (event.target !== node) return;
    const box = node.getBoundingClientRect();
    const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
    if (!inside) node.close('cancel');
  });
  return { node, photo, cancel };
}

/** Ask before deleting; resolves true only for the 삭제 button (Esc, 취소 and the backdrop say no). */
function confirmDelete(v, title) {
  const { node, photo, cancel } = v.dialog;
  photo.textContent = title;
  node.returnValue = '';
  return new Promise((resolve) => {
    node.addEventListener('close', () => resolve(node.returnValue === 'delete'), { once: true });
    node.showModal();
    cancel.focus(); // the safe choice first
  });
}
