// Queue item rules for the admin page: the item.json shape, the status text shown to a person, retry and discard.
// DOM-free ES module, no I/O; time is passed in as milliseconds. newItem mirrors new_item in scripts/queue_items.py
// (same keys in the same order, same timestamp format).

/** "YYYY-MM-DDTHH:MM:SSZ" in UTC; milliseconds are cut off, like Python's strftime. */
export function iso(ms) {
  return new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
}

/** item.json of a fresh upload; kind is "new" or "redraft" (then `file` is the photo to rewrite). */
export function newItem({ name, kind, grayscale, now, date = null, camera = null, file = null }) {
  return {
    name, kind, file, uploaded_at: iso(now), date, camera, grayscale,
    status: 'waiting', claimed_at: null, by: null, error: null,
  };
}

const LABELS = {
  waiting: { text: 'AI 대기', tone: 'busy' },
  pc: { text: 'PC AI가 보는 중…', tone: 'busy' },
  github: { text: 'GitHub AI가 보는 중… (몇 분)', tone: 'busy' },
  failed: { text: 'AI 실패', tone: 'error' },
};
const UNKNOWN = { text: '알 수 없음', tone: 'error' };

/** What to show for an item's status: {text, tone} with tone "busy" or "error". */
export function label(item) {
  return { ...(Object.hasOwn(LABELS, item.status) ? LABELS[item.status] : UNKNOWN) };
}

/** The item as a retry leaves it: waiting again, no claim, no error; uploaded_at stays. */
export function toWaiting(item) {
  return { ...item, status: 'waiting', error: null, claimed_at: null, by: null };
}

/** A claim older than this many seconds is taken to be dead (the worker stopped); as STALE_SECONDS in scripts/queue_items.py. */
export const STALE_SECONDS = 600;

/**
 * Claimed by pc or github more than STALE_SECONDS before `now` (ms)? As is_stale in scripts/queue_items.py, a claim
 * without a time counts as stale; so does one whose time cannot be read (no worker will take that item again).
 */
export function isStale(item, now) {
  if (item.status !== 'pc' && item.status !== 'github') return false;
  return !item.claimed_at || !(now - Date.parse(item.claimed_at) <= STALE_SECONDS * 1000);
}

/**
 * Only an item nobody is working on may be thrown away: waiting or failed, and, when `now` (ms) is given, one whose
 * claim has gone stale (a worker that crashed or was switched off would otherwise leave it there for good).
 */
export function canDiscard(item, now = null) {
  return item.status === 'waiting' || item.status === 'failed' || (now !== null && isStale(item, now));
}

export const SLOW_SECONDS = 15 * 60; // an upload still waiting after this long gets SLOW_HINT
export const SLOW_HINT = 'AI가 오래 걸리고 있어요';

/** SLOW_HINT for an upload waiting more than SLOW_SECONDS, or one whose claim has gone stale; '' otherwise. */
export function hint(item, now) {
  if (item.status === 'waiting') return now - Date.parse(item.uploaded_at) > SLOW_SECONDS * 1000 ? SLOW_HINT : '';
  return isStale(item, now) ? SLOW_HINT : '';
}
