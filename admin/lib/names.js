// Web file names for photos. Same rule as name_for in scripts/photolib.py.

/** pathlib's stem (Python 3.14): the name minus its last suffix; a leading run of dots is never a suffix. */
function stemOf(name) {
  const dot = name.lastIndexOf('.');
  if (dot !== -1 && name.slice(0, dot).replace(/^\.+/, '') !== '') return name.slice(0, dot);
  return name;
}

/** IMG_0306_Edited.jpeg -> 0306 */
export function nameFor(filename) {
  const base = String(filename).split(/[\\/]+/).filter(Boolean).pop() ?? '';
  let stem = stemOf(base).replaceAll('_Edited', '').replaceAll('_edited', '');
  if (stem.toUpperCase().startsWith('IMG_')) stem = stem.slice(4);
  stem = stem.toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^[_-]+|[_-]+$/g, '');
  return stem || 'photo';
}

const REDRAFT_PREFIX = 'redraft-'; // queue/redraft-<file>/ is a request to rewrite photo <file>'s text, never an upload
const RENAMED_PREFIX = 'photo-';

/**
 * base, or base-2, base-3, ... : the first one not in `taken`. A base that starts with "redraft-" first becomes
 * "photo-redraft-...", so an upload can never pass for a redraft request.
 */
export function uniqueName(base, taken) {
  if (base.startsWith(REDRAFT_PREFIX)) base = RENAMED_PREFIX + base;
  if (!taken.has(base)) return base;
  for (let n = 2; ; n++) {
    if (!taken.has(`${base}-${n}`)) return `${base}-${n}`;
  }
}
