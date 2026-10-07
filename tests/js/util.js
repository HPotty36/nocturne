// Helpers shared by the JavaScript tests: the shared cases, fixtures, and a tiny EXIF JPEG builder.
import { readFileSync } from 'node:fs';

const ROOT = new URL('../../', import.meta.url); // repo root, whatever the working directory

export function loadCases() {
  return JSON.parse(readFileSync(new URL('tests/cases.json', ROOT), 'utf8'));
}

/** Chroma (max - min) of every pixel of a grayscale case: spots first, then base up to count. */
export function expandGrayCase(c) {
  const chroma = (rgb) => Math.max(...rgb) - Math.min(...rgb);
  const values = [];
  for (const spot of c.spots) for (let i = 0; i < spot.n; i++) values.push(chroma(spot.rgb));
  while (values.length < c.count) values.push(chroma(c.base));
  return values;
}

/** A file from tests/fixtures as a standalone ArrayBuffer. */
export function fixture(name) {
  const b = readFileSync(new URL(`tests/fixtures/${name}`, ROOT));
  return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
}

// --- EXIF JPEG builder: small APP1/TIFF files for cases the binary fixtures do not cover -----
// Mirrors make_exif_jpeg in tests/helpers.py, but any IFD0 / Exif IFD / GPS IFD content can be given.

const ASCII = 2, SHORT = 3, LONG = 4, RATIONAL = 5;

export const ascii = (tag, text) => ({ tag, type: ASCII, text });
export const short = (tag, n) => ({ tag, type: SHORT, numbers: [n] });
export const long = (tag, n) => ({ tag, type: LONG, numbers: [n] });
/** pairs: [[numerator, denominator], ...] */
export const rational = (tag, ...pairs) => ({ tag, type: RATIONAL, numbers: pairs.flat() });

function valueBytes(field, little) {
  if (field.type === ASCII) return [...field.text].map((c) => c.charCodeAt(0)).concat(0);
  const width = field.type === SHORT ? 2 : 4;
  const out = new DataView(new ArrayBuffer(width * field.numbers.length));
  field.numbers.forEach((n, i) => (width === 2 ? out.setUint16(i * 2, n, little) : out.setUint32(i * 4, n, little)));
  return [...new Uint8Array(out.buffer)];
}

const countOf = (field) => (field.type === ASCII ? field.text.length + 1 : field.numbers.length / (field.type === RATIONAL ? 2 : 1));

function ifdSize(fields, little) {
  const outOfLine = fields.reduce((sum, f) => {
    const n = valueBytes(f, little).length;
    return sum + (n > 4 ? n + (n % 2) : 0);
  }, 0);
  return 2 + 12 * fields.length + 4 + outOfLine;
}

/** One IFD that starts `start` bytes after the TIFF header. */
function ifdBytes(fields, start, little) {
  const head = new DataView(new ArrayBuffer(2));
  head.setUint16(0, fields.length, little);
  const bytes = [...new Uint8Array(head.buffer)];
  let tail = [];
  const tailAt = start + 2 + 12 * fields.length + 4;
  for (const f of fields) {
    const raw = valueBytes(f, little);
    const entry = new DataView(new ArrayBuffer(12));
    entry.setUint16(0, f.tag, little);
    entry.setUint16(2, f.type, little);
    entry.setUint32(4, countOf(f), little);
    if (raw.length <= 4) {
      raw.forEach((b, i) => entry.setUint8(8 + i, b));
    } else {
      entry.setUint32(8, tailAt + tail.length, little);
      tail = tail.concat(raw, raw.length % 2 ? [0] : []);
    }
    bytes.push(...new Uint8Array(entry.buffer));
  }
  bytes.push(0, 0, 0, 0); // no next IFD
  return bytes.concat(tail);
}

/**
 * A JPEG that is only SOI, one APP1 (Exif) and EOI, returned as an ArrayBuffer.
 * ifd0, sub, gps are arrays of ascii()/short()/long()/rational() fields; the 0x8769 / 0x8825
 * pointers are added automatically when sub / gps are given.
 */
export function exifJpeg({ little = true, ifd0 = [], sub = null, gps = null } = {}) {
  const pointer = (tag, at) => long(tag, at);
  const withPointers = (subAt, gpsAt) => [...ifd0, ...(sub ? [pointer(0x8769, subAt)] : []), ...(gps ? [pointer(0x8825, gpsAt)] : [])];
  const subAt = 8 + ifdSize(withPointers(0, 0), little);
  const gpsAt = subAt + (sub ? ifdSize(sub, little) : 0);
  const header = new DataView(new ArrayBuffer(8));
  header.setUint8(0, little ? 0x49 : 0x4d);
  header.setUint8(1, little ? 0x49 : 0x4d);
  header.setUint16(2, 42, little);
  header.setUint32(4, 8, little);
  const tiff = [...new Uint8Array(header.buffer)]
    .concat(ifdBytes(withPointers(subAt, gpsAt), 8, little))
    .concat(sub ? ifdBytes(sub, subAt, little) : [])
    .concat(gps ? ifdBytes(gps, gpsAt, little) : []);
  const payload = [0x45, 0x78, 0x69, 0x66, 0, 0, ...tiff]; // "Exif\0\0" + TIFF
  const size = payload.length + 2;
  return new Uint8Array([0xff, 0xd8, 0xff, 0xe1, size >> 8, size & 0xff, ...payload, 0xff, 0xd9]).buffer;
}
