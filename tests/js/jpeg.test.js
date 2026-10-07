import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import { stripJpegMetadata, UNREADABLE } from '../../admin/lib/jpeg.js';
import { ImageError } from '../../admin/lib/image.js'; // the class jpeg.js throws is the one image.js exports
import { fixture } from './util.js';

// --- small JPEGs from crafted bytes: segments are real in shape, the picture data is made up -------------

const ascii = (text) => [...text].map((c) => c.charCodeAt(0));
/** One marker segment: FF, marker, 16-bit length (counts itself), payload. */
const seg = (marker, ...payload) => [0xff, marker, (payload.length + 2) >> 8, (payload.length + 2) & 0xff, ...payload];

const SOI = [0xff, 0xd8], EOI = [0xff, 0xd9];
const JFIF = seg(0xe0, ...ascii('JFIF\0'), 1, 1, 0, 0, 1, 0, 1, 0, 0);
const ICC = seg(0xe2, ...ascii('ICC_PROFILE\0'), 1, 1, 9, 8, 7, 6, 5);
const DQT = seg(0xdb, 0, ...Array.from({ length: 64 }, (_, i) => i + 1));
const SOF0 = seg(0xc0, 8, 0, 8, 0, 8, 1, 1, 0x11, 0);
const DHT = seg(0xc4, 0, 0, 1, ...new Array(14).fill(0), 7);
const DRI = seg(0xdd, 0, 4);
const SOS = seg(0xda, 1, 1, 0, 0, 63, 0);
// Scan data holds what scan data may hold: a stuffed 0xFF00, a restart marker, and a fill byte before the next marker.
const DATA = [0x12, 0xff, 0x00, 0x34, 0xff, 0xd0, 0x56, 0xff];

const EXIF = seg(0xe1, ...ascii('Exif\0\0'), ...ascii('TestCam'), 0x49, 0x49, 42, 0, 8, 0, 0, 0);
const XMP = seg(0xe1, ...ascii('http://ns.adobe.com/xap/1.0/\0'), ...ascii('<x:xmpmeta gps="37.5,127.0"/>'));
const IPTC = seg(0xed, ...ascii('Photoshop 3.0\0'), ...ascii('8BIM'), 4, 4, 0, 0, 0, 0);
const COMMENT = seg(0xfe, ...ascii('taken at home'));
const MPF = seg(0xe2, ...ascii('MPF\0'), 1, 2, 3, 4);
const JFXX = seg(0xe0, ...ascii('JFXX\0'), 0x10, 1, 2, 3);
const ADOBE = seg(0xee, ...ascii('Adobe'), 0, 100, 0, 0, 0, 0, 1);

const cat = (...parts) => new Uint8Array(parts.flat());
const strip = (bytes) => stripJpegMetadata(bytes);
const rejected = (error) => error instanceof ImageError && error.name === 'ImageError' && error.message === UNREADABLE;

test('drops Exif, XMP, IPTC, comments and other APPn; keeps JFIF, the colour profile and the picture byte for byte', () => {
  const input = cat(SOI, EXIF, JFIF, XMP, COMMENT, DQT, IPTC, SOF0, ICC, MPF, JFXX, ADOBE, DHT, DRI, SOS, DATA, EOI);
  const expected = cat(SOI, JFIF, DQT, SOF0, ICC, DHT, DRI, SOS, DATA, EOI);
  assert.deepEqual(strip(input), expected);
});

test('segments before the picture are kept in their original order, whatever they are called', () => {
  const input = cat(SOI, DQT, COMMENT, ICC, DRI, JFIF, SOF0, DHT, SOS, DATA, EOI);
  assert.deepEqual(strip(input), cat(SOI, DQT, ICC, DRI, JFIF, SOF0, DHT, SOS, DATA, EOI));
});

test('metadata between the scans of a progressive JPEG is dropped too, the scans are kept', () => {
  const scan2 = [0x77, 0xff, 0x00, 0x88, 0xff];
  const input = cat(SOI, JFIF, DQT, SOF0, DHT, SOS, DATA, EXIF, DHT, XMP, SOS, scan2, COMMENT, EOI);
  assert.deepEqual(strip(input), cat(SOI, JFIF, DQT, SOF0, DHT, SOS, DATA, DHT, SOS, scan2, EOI));
});

test('anything after the end of the image is dropped', () => {
  const input = cat(SOI, JFIF, DQT, SOF0, DHT, SOS, DATA, EOI, ascii('TRAILER gps 37.5,127.0'));
  assert.deepEqual(strip(input), cat(SOI, JFIF, DQT, SOF0, DHT, SOS, DATA, EOI));
});

test('the answer is a new array; the input is left alone; views and ArrayBuffers work', () => {
  const input = cat(SOI, EXIF, JFIF, DQT, SOF0, DHT, SOS, DATA, EOI);
  const before = Uint8Array.from(input);
  const out = strip(input);
  assert.ok(out instanceof Uint8Array);
  assert.notEqual(out.buffer, input.buffer);
  assert.deepEqual(input, before);

  const padded = new Uint8Array(input.length + 20);
  padded.set(input, 7); // a view that starts in the middle of a buffer
  assert.deepEqual(strip(padded.subarray(7, 7 + input.length)), out);
  assert.deepEqual(strip(input.buffer.slice(input.byteOffset, input.byteOffset + input.byteLength)), out);
});

test('an Exif JPEG from the fixtures comes out as the same picture without the APP1', () => {
  for (const name of ['exif_le.jpg', 'exif_be.jpg']) {
    const original = new Uint8Array(fixture(name));
    const app1Size = (original[4] << 8) | original[5];
    assert.deepEqual([original[2], original[3]], [0xff, 0xe1], name);
    const out = strip(original);
    assert.deepEqual(out, cat([...original.subarray(0, 2)], [...original.subarray(4 + app1Size)]), name);
    assert.equal(Buffer.from(out).includes('Exif'), false, name);
    assert.equal(Buffer.from(out).includes('TestCam'), false, name);
  }
});

/** `bytes` without the given header segments (markers listed); everything from the first SOS on is copied as it is. */
function withoutHeaderSegments(bytes, markers) {
  const parts = [bytes.subarray(0, 2)];
  let pos = 2;
  while (bytes[pos] === 0xff && bytes[pos + 1] !== 0xda) {
    const end = pos + 2 + ((bytes[pos + 2] << 8) | bytes[pos + 3]);
    if (!markers.includes(bytes[pos + 1])) parts.push(bytes.subarray(pos, end));
    pos = end;
  }
  parts.push(bytes.subarray(pos));
  const out = new Uint8Array(parts.reduce((sum, part) => sum + part.length, 0));
  let at = 0;
  for (const part of parts) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

const sameBytes = (a, b) => Buffer.from(a.buffer, a.byteOffset, a.length).equals(Buffer.from(b.buffer, b.byteOffset, b.length));

test('real web copies from photos/thumb: a JPEG with nothing to remove comes back unchanged (progressive scans, colour profiles)', () => {
  const folder = new URL('../../photos/thumb/', import.meta.url);
  const names = readdirSync(folder).filter((name) => name.endsWith('.jpg'));
  assert.ok(names.length > 0);
  for (const name of names) {
    const original = new Uint8Array(readFileSync(new URL(name, folder)));
    // Some of these files carry an APP11 (a JUMBF/C2PA provenance block); cut that and the other APPn that must go.
    const clean = withoutHeaderSegments(original, [0xe1, 0xeb, 0xed, 0xfe]);
    assert.ok(sameBytes(strip(clean), clean), `${name}: a JPEG without forbidden segments changed`);
    assert.ok(sameBytes(strip(original), clean), `${name}: stripping did not give the original minus APP1/APP11/APP13/COM`);
    assert.equal(Buffer.from(strip(original)).includes('c2pa'), false, name);
  }
});

test('input that is not a complete JPEG throws ImageError', () => {
  const whole = cat(SOI, JFIF, DQT, SOF0, DHT, SOS, DATA, EOI);
  const cases = {
    'empty': [],
    'one byte': [0xff],
    'only SOI': SOI,
    'text': ascii('hello, this is not a picture'),
    'PNG': [0x89, ...ascii('PNG\r\n'), 0x1a, 0x0a, 0, 0, 0, 13],
    'no SOI': [...JFIF, ...DQT, ...SOF0, ...DHT, ...SOS, ...DATA, ...EOI],
    'cut inside a segment header': [...SOI, 0xff, 0xe0, 0x00],
    'cut inside a segment': [...SOI, ...JFIF.slice(0, 9)],
    'cut inside the scan data': [...SOI, ...JFIF, ...DQT, ...SOF0, ...DHT, ...SOS, ...DATA.slice(0, 4)],
    'cut right after the scan header': [...SOI, ...JFIF, ...DQT, ...SOF0, ...DHT, ...SOS],
    'cut after a lone FF in the scan data': [...SOI, ...JFIF, ...SOS, 0x12, 0xff],
    'no picture data before the end': [...SOI, ...JFIF, ...DQT, ...EOI],
    'a segment shorter than its own length field': [...SOI, 0xff, 0xdb, 0x00, 0x01, 0x00],
    'a segment longer than the file': [...SOI, 0xff, 0xdb, 0x00, 0x50, 0x00, 0x01],
    'a restart marker between the segments': [...SOI, 0xff, 0xd0, ...DQT, ...SOS, ...DATA, ...EOI],
    'a second SOI': [...SOI, ...SOI, ...DQT, ...SOS, ...DATA, ...EOI],
    'a stuffed byte where a marker belongs': [...SOI, 0xff, 0x00, ...DQT, ...SOS, ...DATA, ...EOI],
    'a stray byte between segments': [...SOI, 0x00, ...DQT, ...SOS, ...DATA, ...EOI],
    'zeros': new Array(50).fill(0),
  };
  assert.doesNotThrow(() => strip(whole)); // the same pieces in the right shape are fine
  for (const [label, bytes] of Object.entries(cases)) {
    assert.throws(() => strip(Uint8Array.from(bytes)), rejected, label);
  }
});
