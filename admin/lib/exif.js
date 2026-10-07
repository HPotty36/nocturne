// A tiny EXIF reader for the admin page: capture date and camera line, nothing else.
// Same output as exif_hints in scripts/photolib.py. The GPS IFD (tag 0x8825) is never followed.
// Every offset is checked against the Exif segment, so a damaged file gives {} (or fewer fields), never an error.

const MAX_ENTRIES = 1000; // a real IFD has a few dozen; more means garbage

const ASCII = 2, SHORT = 3, LONG = 4, RATIONAL = 5, SRATIONAL = 10;
const TYPE_SIZE = { [ASCII]: 1, [SHORT]: 2, [LONG]: 4, [RATIONAL]: 8, [SRATIONAL]: 8 }; // bytes per item; other types are skipped

const MODEL = 0x0110, DATE_TIME = 0x0132, EXIF_IFD = 0x8769;
const DATE_ORIGINAL = 0x9003, F_NUMBER = 0x829d, EXPOSURE_TIME = 0x829a, ISO = 0x8827;

/** {date?: "YYYY.MM.DD", camera?: "Model · f/1.6 · 1/121s · ISO 100"} from a JPEG; {} when there is no usable EXIF. */
export function readHints(buffer) {
  try {
    return hintsOf(buffer);
  } catch {
    return {};
  }
}

function hintsOf(buffer) {
  const dv = ArrayBuffer.isView(buffer) ? new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength) : new DataView(buffer);
  const tiff = findTiff(dv);
  if (!tiff) return {};
  const ifd0 = readIfd(tiff, offsetAt(tiff, tiff.base + 4));
  const exifEntry = ifd0.get(EXIF_IFD);
  const sub = exifEntry ? readIfd(tiff, number(tiff, exifEntry)) : new Map();

  const hints = {};
  const when = text(tiff, sub.get(DATE_ORIGINAL)) || text(tiff, ifd0.get(DATE_TIME));
  if (when) hints.date = when.slice(0, 10).replaceAll(':', '.');

  const model = text(tiff, ifd0.get(MODEL));
  if (model) {
    const parts = [model.trim()];
    const f = number(tiff, sub.get(F_NUMBER));
    const t = number(tiff, sub.get(EXPOSURE_TIME));
    const iso = number(tiff, sub.get(ISO));
    if (f) parts.push(`f/${formatG(f)}`);
    if (t) parts.push(t > 0 && t < 1 ? `1/${roundHalfEven(1 / t)}s` : `${formatG(t)}s`);
    if (iso) parts.push(`ISO ${iso}`);
    hints.camera = parts.join(' · ');
  }
  return hints;
}

/** Position of the TIFF header inside the first Exif APP1 segment, with the segment's end and byte order. */
function findTiff(dv) {
  const length = dv.byteLength;
  if (length < 4 || dv.getUint16(0) !== 0xffd8) return null;
  let pos = 2;
  while (pos + 4 <= length) {
    if (dv.getUint8(pos) !== 0xff) { pos += 1; continue; } // junk between segments: skip it, as Pillow does
    const marker = dv.getUint8(pos + 1);
    if (marker === 0xff) { pos += 1; continue; } // fill byte
    if (marker === 0xd9 || marker === 0xda) return null; // end of image / start of scan: no headers after this
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd8)) { pos += 2; continue; } // markers without a length
    const size = dv.getUint16(pos + 2);
    if (size < 2) return null;
    const end = Math.min(pos + 2 + size, length);
    if (marker === 0xe1 && hasExifHeader(dv, pos + 4, end)) return tiffAt(dv, pos + 10, end);
    pos += 2 + size;
  }
  return null;
}

function hasExifHeader(dv, at, end) {
  if (at + 6 > end) return false;
  return [0x45, 0x78, 0x69, 0x66, 0, 0].every((byte, i) => dv.getUint8(at + i) === byte); // "Exif\0\0"
}

function tiffAt(dv, base, end) {
  if (base + 8 > end) return null;
  const order = dv.getUint16(base); // 0x4949 "II" little-endian, 0x4d4d "MM" big-endian
  if (order !== 0x4949 && order !== 0x4d4d) return null;
  const little = order === 0x4949;
  if (dv.getUint16(base + 2, little) !== 42) return null;
  return { dv, base, end, little };
}

/** The 32-bit value at an absolute position inside the segment, or null. */
function offsetAt(t, at) {
  return at + 4 <= t.end ? t.dv.getUint32(at, t.little) : null;
}

/** IFD at `offset` from the TIFF header: Map of tag -> {type, count, at}, where `at` is where the value bytes start. */
function readIfd(t, offset) {
  const entries = new Map();
  if (!Number.isInteger(offset) || offset < 0) return entries;
  const start = t.base + offset;
  if (start + 2 > t.end) return entries;
  const n = t.dv.getUint16(start, t.little);
  if (n > MAX_ENTRIES) return entries;
  for (let i = 0; i < n; i++) {
    const p = start + 2 + 12 * i;
    if (p + 12 > t.end) break;
    const type = t.dv.getUint16(p + 2, t.little);
    const count = t.dv.getUint32(p + 4, t.little);
    const size = (TYPE_SIZE[type] ?? 0) * count;
    if (size === 0) continue;
    const at = size <= 4 ? p + 8 : t.base + t.dv.getUint32(p + 8, t.little); // small values sit inside the entry
    if (at + size > t.end) continue;
    entries.set(t.dv.getUint16(p, t.little), { type, count, at });
  }
  return entries;
}

/** An ASCII value up to its first NUL (Pillow drops one trailing NUL; cameras sometimes pad with several), or null. */
function text(t, entry) {
  if (!entry || entry.type !== ASCII) return null;
  let out = '';
  for (let i = 0; i < entry.count; i++) {
    const code = t.dv.getUint8(entry.at + i);
    if (code === 0) break;
    out += String.fromCharCode(code); // Latin-1, as Pillow decodes it
  }
  return out;
}

/** The first number of a SHORT, LONG or RATIONAL value, or null (also for a zero denominator). */
function number(t, entry) {
  if (!entry) return null;
  const { dv, little } = t;
  switch (entry.type) {
    case SHORT: return dv.getUint16(entry.at, little);
    case LONG: return dv.getUint32(entry.at, little);
    case RATIONAL:
    case SRATIONAL: {
      const signed = entry.type === SRATIONAL;
      const top = signed ? dv.getInt32(entry.at, little) : dv.getUint32(entry.at, little);
      const bottom = signed ? dv.getInt32(entry.at + 4, little) : dv.getUint32(entry.at + 4, little);
      return bottom === 0 ? null : top / bottom;
    }
    default: return null;
  }
}

/** Python's round(): halves go to the even neighbour (Math.round sends them up). */
function roundHalfEven(x) {
  const floor = Math.floor(x);
  const diff = x - floor;
  if (diff !== 0.5) return diff < 0.5 ? floor : floor + 1;
  return floor % 2 === 0 ? floor : floor + 1;
}

/**
 * Python's f"{x:g}": six significant digits, no trailing zeros, exponent form below 1e-4 and from 1e6.
 * Rounds the exact decimal value of the double, halves to even, so 15.15625 gives 15.1562 as in Python
 * (toFixed and toExponential send exact halves up).
 */
function formatG(x) {
  if (x === 0) return '0';
  // toFixed(100) writes a double's exact decimal value; the values here (n / d with d < 2^32) fit in 100 digits.
  const [whole, fraction = ''] = Math.abs(x).toFixed(100).split('.');
  const all = whole + fraction;
  const first = all.search(/[1-9]/);
  if (first === -1) return '0';
  let exp = whole.length - 1 - first;
  const sig = all.slice(first);
  let head = sig.slice(0, 6).padEnd(6, '0');
  const rest = sig.slice(6);
  const half = rest.startsWith('5') && /^0*$/.test(rest.slice(1));
  if (rest[0] > '5' || (rest[0] === '5' && !half) || (half && Number(head[5]) % 2 === 1)) {
    head = String(Number(head) + 1);
    if (head.length > 6) { head = head.slice(0, 6); exp += 1; } // 999999 rounded up to 1000000
  }
  const sign = x < 0 ? '-' : '';
  if (exp < -4 || exp >= 6) {
    const mantissa = `${head[0]}.${head.slice(1)}`.replace(/\.?0+$/, '');
    return `${sign}${mantissa}e${exp < 0 ? '-' : '+'}${String(Math.abs(exp)).padStart(2, '0')}`;
  }
  const digits = exp >= 0 ? head : '0'.repeat(-exp - 1) + head;
  const point = exp >= 0 ? exp + 1 : 0;
  const fractionPart = digits.slice(point).replace(/0+$/, '');
  return `${sign}${exp >= 0 ? digits.slice(0, point) : '0'}${fractionPart ? `.${fractionPart}` : ''}`;
}
