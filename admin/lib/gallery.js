// Exhibition rules for the admin page: photos.json text format, add / edit / delete / cover, and the AI-text badges.
// DOM-free ES module. It mirrors scripts/photolib.py (dumps_data, find_photo, add_photo), so read that one first when
// something here looks odd. The functions change the object they are given; callers pass a structuredClone.

export const SMALL_MODEL = 'gemma4:e4b-it-qat'; // the model whose AI texts still wait to be polished by the PC model
export const CONFIDENT = 0.8; // room confidence below this is flagged "확인 필요"
export const COVER_BADGE = '대표 사진'; // not from badges(): the view puts it first on the cover photo
export const REVIEW_BADGE = '확인 필요';

const REQUIRED = ['title', 'alt']; // the Korean texts: a photo always has them, a person cannot empty them
const OPTIONAL = ['title_en', 'alt_en']; // the English texts: emptying one removes its key (the English page then shows the Korean)
const TEXTS = [...REQUIRED, ...OPTIONAL]; // the photo texts an AI may write (ai.fields lists these)
const EDITABLE = ['room', ...TEXTS];
// where photos.json keeps a new English text: right after the last of these the photo has
const AFTER = { title_en: ['alt'], alt_en: ['title_en', 'alt'] };

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
export const COVER_KEYS = ['cover', 'cover_focus', 'cover_pick']; // the cover, where to crop it, who picked it: right after "site"
const ONE_LINE = [PHOTO_PATH, ['cover_focus'], ['cover_pick']];
const isOneLine = (path) => ONE_LINE.some((one) => path.length === one.length && path.every((part, i) => part === one[i]));
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);

/** data with the cover keys it has moved right after "site" (first if there is no "site"); the other keys keep their order. Anything but an object is returned as it is. */
function topOrder(data) {
  if (!isObject(data)) return data;
  const cover = COVER_KEYS.filter((key) => key in data).map((key) => [key, data[key]]);
  const rest = Object.entries(data).filter(([key]) => !COVER_KEYS.includes(key));
  const at = rest.findIndex(([key]) => key === 'site') + 1; // 0 when there is no "site"
  return Object.fromEntries([...rest.slice(0, at), ...cover, ...rest.slice(at)]);
}

function dump(value, depth, path) {
  if (isOneLine(path)) return JSON.stringify(value);
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

/**
 * photos.json text. Numbers print like Python's only for whole values and short decimals (confidence is rounded to 3
 * places, a cover focus to 2). The cover keys sit right after "site".
 */
export function serialize(data) {
  return `${dump(topOrder(data), 0, [])}\n`;
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

/**
 * Make a photo the cover, picked by a person: the AI leaves it (cover_pick says so) and only sets where to crop it on
 * its next run, so the old cover's focus goes.
 */
export function setCover(data, name) {
  findPhoto(data, name);
  data.cover = name;
  data.cover_pick = { by: 'person' };
  delete data.cover_focus;
}

/** Hand the cover back to the AI: on its next run it picks again. The cover and its focus stay until then. */
export function releaseCover(data) {
  delete data.cover_pick;
}

/**
 * Who picked the cover, as the badge after 대표 사진 puts it. 'AI가 못 고름': the AI's last try failed in a way it is not
 * asked again for the same photos (cover_pick.tried), until they change or the cover is handed back.
 */
export function coverBadge(data) {
  const pick = isObject(data.cover_pick) ? data.cover_pick : {};
  if (pick.by === 'person') return '직접 고름';
  if (pick.tried) return 'AI가 못 고름';
  if (pick.by === 'ai') return 'AI가 고름';
  return 'AI가 고를 차례';
}

/** Whether AI에게 맡기기 applies: the cover is pinned by a person, or the AI's last try failed. */
export function coverCanRelease(data) {
  const pick = isObject(data.cover_pick) ? data.cover_pick : {};
  return pick.by === 'person' || Boolean(pick.tried);
}

/** Set a text. A key the photo already has changes in place; a new English key goes where photos.json keeps it. */
function putText(photo, key, value) {
  const anchor = key in photo ? undefined : AFTER[key]?.find((k) => k in photo);
  if (anchor === undefined) {
    photo[key] = value;
    return;
  }
  const keys = Object.keys(photo);
  const later = keys.slice(keys.indexOf(anchor) + 1).map((k) => [k, photo[k]]);
  for (const [k] of later) delete photo[k];
  photo[key] = value;
  for (const [k, v] of later) photo[k] = v; // the same object, with the keys after the new one put back in order
}

/**
 * Save what a person changed on one photo: changes may hold `room`, `title`, `alt`, `title_en` and `alt_en`.
 * A new room takes the photo at its end. Title and description cannot be emptied; an English text given as ''
 * is removed (nothing happens if the photo has none), and a new one lands after `alt` like photos.json writes it.
 * Saving counts as "a person looked at it": the AI confidence goes, every text whose value really changed
 * (or went) leaves ai.fields, and an empty ai.fields removes `ai`.
 * Everything is checked before anything is changed, so a refused save leaves `data` as it was.
 */
export function saveEdits(data, name, changes) {
  const { room: from, photo } = findPhoto(data, name);
  const given = Object.entries(changes).filter(([, value]) => value !== undefined);
  for (const [key] of given) {
    if (!EDITABLE.includes(key)) throw new GalleryError(`고칠 수 없는 항목이에요: ${key}`);
  }
  const edits = Object.fromEntries(given);
  for (const key of REQUIRED) {
    if (key in edits && (typeof edits[key] !== 'string' || !edits[key].trim())) throw new GalleryError('제목과 설명을 채워 주세요');
  }
  for (const key of OPTIONAL) {
    if (key in edits && typeof edits[key] !== 'string') throw new GalleryError(`올바르지 않은 값이에요: ${key}`);
  }
  const to = 'room' in edits && edits.room !== from.id ? findRoom(data, edits.room) : null;

  const removed = OPTIONAL.filter((key) => key in edits && !edits[key].trim() && key in photo);
  const written = TEXTS.filter((key) => key in edits && (REQUIRED.includes(key) || edits[key].trim()) && edits[key] !== photo[key]);
  const changed = TEXTS.filter((key) => removed.includes(key) || written.includes(key));
  for (const key of removed) delete photo[key];
  for (const key of written) putText(photo, key, edits[key]);
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

/**
 * Notes shown beside a photo, in this order: "확인 필요" (low room confidence), "AI 한국어" and "AI 영어" (an AI text of
 * that language is still there), "다듬기 대기" (the small model's texts, which the PC model will polish).
 */
export function badges(photo) {
  const out = [];
  const ai = photo.ai;
  if (!ai) return out;
  const fields = ai.fields ?? [];
  if (typeof ai.confidence === 'number' && ai.confidence < CONFIDENT) out.push(REVIEW_BADGE);
  if (fields.some((key) => REQUIRED.includes(key))) out.push('AI 한국어');
  if (fields.some((key) => OPTIONAL.includes(key))) out.push('AI 영어');
  if (fields.length && ai.model === SMALL_MODEL) out.push('다듬기 대기');
  return out;
}
