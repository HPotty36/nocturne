// Photos shared to the app from the phone's share sheet (the site's worker, src/sw.js, keeps them in the cache
// nocturne-share).
// DOM-free: the page passes `caches` in, so Node can test it. Reading never removes anything: a photo leaves the
// cache only once its upload is in queue/ or the person discards it (dropShares), so a share that waits through a failed or
// cancelled login is offered again after the next one.

export const SHARE_CACHE = 'nocturne-share';

/** This browser's CacheStorage, or null when it has none or merely reading `caches` throws (site data blocked). */
export function cacheStorage() {
  try {
    return globalThis.caches ?? null;
  } catch {
    return null;
  }
}

const RECEIVED = /(?:^|\/)share\/(\d+)-\d+$/; // share/<ms the worker received it>-<number in that share>

/**
 * The shared photos still waiting, oldest key first (keys as strings, so "10" sorts before "2"; the worker's keys
 * are full addresses): [{key, file}]. Each file is a File with the name the photo had when it was shared
 * (photo.jpg when the worker did not record one), its type, and the time it was received as lastModified.
 * An empty list when there is no cache storage or anything fails while reading it.
 */
export async function pendingShares(cachesLike) {
  if (!cachesLike) return [];
  try {
    const cache = await cachesLike.open(SHARE_CACHE);
    const keys = (await cache.keys()).map((request) => request.url).sort();
    const found = [];
    for (const key of keys) {
      const response = await cache.match(key);
      if (!response) continue; // dropped meanwhile
      const blob = await response.blob();
      const name = decodeName(response.headers.get('X-Name'));
      const received = RECEIVED.exec(key);
      const options = { type: response.headers.get('Content-Type') ?? blob.type };
      if (received) options.lastModified = Number(received[1]);
      found.push({ key, file: new File([blob], name, options) });
    }
    return found;
  } catch {
    return [];
  }
}

/** Remove these keys (as pendingShares gave them) from the cache. Errors are ignored: a photo that stays is offered again. */
export async function dropShares(cachesLike, keys) {
  if (!cachesLike || keys.length === 0) return;
  try {
    const cache = await cachesLike.open(SHARE_CACHE);
    for (const key of keys) {
      try {
        await cache.delete(key);
      } catch {
        // the next one is still dropped
      }
    }
  } catch {
    // no cache, nothing to drop
  }
}

function decodeName(header) {
  if (!header) return 'photo.jpg';
  try {
    return decodeURIComponent(header) || 'photo.jpg';
  } catch {
    return 'photo.jpg'; // not something the worker wrote
  }
}
