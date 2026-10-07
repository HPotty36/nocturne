// Exhibition rules for the admin page: photos.json text format, add / edit / delete / cover, and the AI-text badges.
// DOM-free ES module. It mirrors scripts/photolib.py (dumps_data, find_photo, add_photo), so read that one first when
// something here looks odd. The functions change the object they are given; callers pass a structuredClone.

export const SMALL_MODEL = 'gemma4:e4b-it-qat'; // the model whose AI texts still wait to be polished by the PC model
export const CONFIDENT = 0.8; // room confidence below this is flagged "확인 필요"

const EDITABLE = ['room', 'title', 'alt'];
const TEXTS = ['title', 'alt']; // the photo texts an AI may write (ai.fields lists these)

export class GalleryError extends Error {
  /** A change the exhibition cannot make (unknown room or photo, duplicate name, ...). The message is shown to the user. */
  constructor(message, options) {
    super(message, options);
    this.name = 'GalleryError';
  }
}

// --- src/photos.json: one canonical text format, the same text Python's dumps_data writes -----------------------
// 2-space indent, every rooms[*].photos[*] element on one line, Korean left unescaped, final newline.

const PHOTO_PATH = ['rooms', '*', 'photos', '*'];
const isPhotoPath = (path) => path.length === PHOTO_PATH.length && path.every((part, i) => part === PHOTO_PATH[i]);
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

function dump(value, depth, path) {
  if (isPhotoPath(path)) return JSON.stringify(value);
  const pad = '  '.repeat(depth + 1);
  const close = '  '.repeat(depth);
  if (isObject(value)) {
    const entries = Object.entries(value).filter(([, v]) => v !== undefined); // JSON.stringify drops these, so do we
    if (entries.length) {
      const lines = entries.map(([k, v]) => `${pad}${JSON.stringify(k)}: ${dump(v, depth + 1, [...path, k])}`);
      return `{\n${lines.join(',\n')}\n${close}}`;
    }
  }
  if (Array.isArray(value) && value.length) {
    const lines = value.map((v) => `${pad}${dump(v, depth + 1, [...path, '*'])}`);
    return `[\n${lines.join(',\n')}\n${close}]`;
  }
  return JSON.stringify(value); // scalars, [] and {}
}

/** photos.json text. Numbers print like Python's only for whole values and short decimals (confidence is rounded to 3 places). */
export function serialize(data) {
  return `${dump(data, 0, [])}\n`;
}

// --- room numbers ----------------------------------------------------------------------------------------------

const ROMAN = [[1000, 'M'], [900, 'CM'], [500, 'D'], [400, 'CD'], [100, 'C'], [90, 'XC'],
  [50, 'L'], [40, 'XL'], [10, 'X'], [9, 'IX'], [5, 'V'], [4, 'IV'], [1, 'I']];

/** Room number as on the site: 1 -> I, 14 -> XIV (same table as scripts/build.py). */
export function roman(n) {
  let out = '';
  for (const [value, letters] of ROMAN) {
    while (n >= value) {
      out += letters;
      n -= value;
    }
  }
  return out;
}

// --- finding things --------------------------------------------------------------------------------------------

/** File names of every photo registered in a room. */
export function allNames(data) {
  return new Set(data.rooms.flatMap((room) => room.photos.map((photo) => photo.file)));
}

function findRoom(data, roomId) {
  const room = data.rooms.find((r) => r.id === roomId);
  if (!room) throw new GalleryError(`없는 방이에요: ${roomId}`);
  return room;
}

function findPhoto(data, name) {
  for (const room of data.rooms) {
    const photo = room.photos.find((p) => p.file === name);
    if (photo) return { room, photo };
  }
  throw new GalleryError(`없는 사진이에요: ${name}`);
}

// --- changes ---------------------------------------------------------------------------------------------------

/** Append a photo to the end of a room (a direct post: it carries no `ai`). The room is checked before the name. */
export function addPhoto(data, roomId, entry) {
  const room = findRoom(data, roomId);
  if (allNames(data).has(entry.file)) throw new GalleryError(`이미 있는 사진 이름이에요: ${entry.file}`);
  room.photos.push(entry);
}

/** Take a photo out of its room and return its entry. The cover photo cannot be deleted. */
export function deletePhoto(data, name) {
  const { room, photo } = findPhoto(data, name);
  if (data.cover === name) throw new GalleryError('대표 사진은 다른 사진을 대표로 지정한 뒤 지울 수 있어요');
  room.photos.splice(room.photos.indexOf(photo), 1);
  return photo;
}

export function setCover(data, name) {
  findPhoto(data, name);
  data.cover = name;
}

/**
 * Save what a person changed on one photo: changes may hold `room`, `title` and `alt`.
 * A new room takes the photo at its end. Saving counts as "a person looked at it": the AI confidence goes,
 * every text whose value really changed leaves ai.fields, and an empty ai.fields removes `ai`.
 * Everything is checked before anything is changed, so a refused save leaves `data` as it was.
 */
export function saveEdits(data, name, changes) {
  const { room: from, photo } = findPhoto(data, name);
  const given = Object.entries(changes).filter(([, value]) => value !== undefined);
  for (const [key] of given) {
    if (!EDITABLE.includes(key)) throw new GalleryError(`고칠 수 없는 항목이에요: ${key}`);
  }
  const edits = Object.fromEntries(given);
  for (const key of TEXTS) {
    if (key in edits && (typeof edits[key] !== 'string' || !edits[key].trim())) throw new GalleryError('제목과 설명을 채워 주세요');
  }
  const to = 'room' in edits && edits.room !== from.id ? findRoom(data, edits.room) : null;

  const changed = TEXTS.filter((key) => key in edits && edits[key] !== photo[key]);
  for (const key of changed) photo[key] = edits[key];
  if (to) {
    from.photos.splice(from.photos.indexOf(photo), 1);
    to.photos.push(photo);
  }
  if (photo.ai) {
    const fields = (photo.ai.fields ?? []).filter((key) => !changed.includes(key));
    if (fields.length) {
      photo.ai.fields = fields;
      delete photo.ai.confidence;
    } else {
      delete photo.ai;
    }
  }
  return photo;
}

// --- badges ----------------------------------------------------------------------------------------------------

/** Notes shown beside a photo: "확인 필요" (low confidence), then who wrote the AI text. */
export function badges(photo) {
  const out = [];
  const ai = photo.ai;
  if (!ai) return out;
  if (typeof ai.confidence === 'number' && ai.confidence < CONFIDENT) out.push('확인 필요');
  if (ai.fields?.length) out.push(ai.model === SMALL_MODEL ? 'AI 설명 · 다듬기 대기' : 'AI 설명');
  return out;
}
