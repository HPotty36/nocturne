import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { readHints } from '../../admin/lib/exif.js';
import { ascii, exifJpeg, fixture, rational, short } from './util.js';

const want = JSON.parse(readFileSync(new URL('../fixtures/exif_expected.json', import.meta.url), 'utf8'));

const MODEL = ascii(0x0110, 'TestCam');
const DATE_TIME = 0x0132, MODEL_TAG = 0x0110, DATE_ORIGINAL = 0x9003, F_NUMBER = 0x829d, EXPOSURE = 0x829a, ISO = 0x8827;

/** Camera line of a file whose only IFD0 field is the model and whose Exif IFD is `sub`. */
const cameraOf = (...sub) => readHints(exifJpeg({ ifd0: [MODEL], sub })).camera;
const exposureOf = (n, d) => cameraOf(rational(EXPOSURE, [n, d])).replace('TestCam · ', '');
const fnumberOf = (n, d) => cameraOf(rational(F_NUMBER, [n, d])).replace('TestCam · ', '');

const onlyDateAndCamera = (hints) => assert.deepEqual(Object.keys(hints).filter((k) => k !== 'date' && k !== 'camera'), []);

test('little-endian', () => assert.deepEqual(readHints(fixture('exif_le.jpg')), want));
test('big-endian', () => assert.deepEqual(readHints(fixture('exif_be.jpg')), want));
test('no exif or garbage', () => { assert.deepEqual(readHints(new Uint8Array([0xff, 0xd8, 0xff, 0xd9]).buffer), {}); assert.deepEqual(readHints(new ArrayBuffer(3)), {}); });
test('never returns gps', () => assert.equal(JSON.stringify(readHints(fixture('exif_be.jpg'))).includes('GPS'), false));

test('builder output reads like the fixtures', () => {
  for (const little of [true, false]) {
    const jpeg = exifJpeg({
      little,
      ifd0: [MODEL, ascii(DATE_TIME, '2025:11:30 23:13:46')],
      sub: [rational(EXPOSURE, [1, 121]), rational(F_NUMBER, [16, 10]), short(ISO, 100), ascii(DATE_ORIGINAL, '2025:11:30 23:13:46')],
    });
    assert.deepEqual(readHints(jpeg), want);
  }
});

test('returns only date and camera, and never follows the GPS pointer', () => {
  for (const name of ['exif_le.jpg', 'exif_be.jpg']) {
    const hints = readHints(fixture(name));
    assert.deepEqual(Object.keys(hints).sort(), ['camera', 'date']);
  }
  // The GPS IFD here holds tags that would be read if it were mistaken for the Exif IFD.
  const trap = [ascii(DATE_ORIGINAL, '1999:01:01 00:00:00'), rational(F_NUMBER, [1, 1]), short(ISO, 6400)];
  for (const little of [true, false]) {
    assert.deepEqual(readHints(exifJpeg({ little, ifd0: [MODEL], gps: trap })), { camera: 'TestCam' });
  }
});

test('date: DateTimeOriginal first, then DateTime, only the first 10 characters', () => {
  const both = exifJpeg({ ifd0: [ascii(DATE_TIME, '2020:02:02 02:02:02')], sub: [ascii(DATE_ORIGINAL, '2024:05:06 07:08:09')] });
  assert.deepEqual(readHints(both), { date: '2024.05.06' });
  assert.deepEqual(readHints(exifJpeg({ ifd0: [ascii(DATE_TIME, '2020:02:02 02:02:02')] })), { date: '2020.02.02' });
  const emptyOriginal = exifJpeg({ ifd0: [ascii(DATE_TIME, '2020:02:02 02:02:02')], sub: [ascii(DATE_ORIGINAL, '')] });
  assert.deepEqual(readHints(emptyOriginal), { date: '2020.02.02' });
});

test('camera needs a model; the other parts are optional', () => {
  assert.deepEqual(readHints(exifJpeg({ sub: [rational(F_NUMBER, [2, 1]), short(ISO, 100)] })), {});
  assert.deepEqual(readHints(exifJpeg({ ifd0: [MODEL] })), { camera: 'TestCam' });
  assert.deepEqual(readHints(exifJpeg({ ifd0: [ascii(MODEL_TAG, '  TestCam  ')] })), { camera: 'TestCam' });
  assert.equal(cameraOf(rational(F_NUMBER, [28, 10]), rational(EXPOSURE, [1, 50]), short(ISO, 400)), 'TestCam · f/2.8 · 1/50s · ISO 400');
  assert.equal(cameraOf(short(ISO, 800)), 'TestCam · ISO 800');
  assert.equal(cameraOf(rational(F_NUMBER, [0, 1]), rational(EXPOSURE, [0, 1]), short(ISO, 0)), 'TestCam');
});

test('1/Ns uses round-half-to-even like Python round(), not Math.round', () => {
  assert.equal(exposureOf(2, 5), '1/2s'); // 1/t = 2.5 -> 2
  assert.equal(exposureOf(2, 7), '1/4s'); // 1/t = 3.5 -> 4
  assert.equal(exposureOf(2, 9), '1/4s'); // 1/t = 4.5 -> 4
  assert.equal(exposureOf(2, 11), '1/6s'); // 1/t = 5.5 -> 6
  assert.equal(exposureOf(2, 3), '1/2s'); // 1/t = 1.5 -> 2
  assert.equal(exposureOf(2, 1000), '1/500s');
});

test('short exposures', () => {
  assert.equal(exposureOf(1, 125), '1/125s');
  assert.equal(exposureOf(1, 3), '1/3s');
  assert.equal(exposureOf(3, 10), '1/3s'); // 3.33
  assert.equal(exposureOf(999, 1000), '1/1s'); // just under a second still reads 1/Ns
});

test('long exposures print like Python {t:g}', () => {
  assert.equal(exposureOf(1, 1), '1s');
  assert.equal(exposureOf(30, 1), '30s');
  assert.equal(exposureOf(5, 2), '2.5s');
  assert.equal(exposureOf(13, 10), '1.3s');
  assert.equal(exposureOf(10, 3), '3.33333s'); // six significant digits
  assert.equal(exposureOf(1000000, 1), '1e+06s');
});

test('f-number prints like Python {f:g}', () => {
  assert.equal(fnumberOf(16, 10), 'f/1.6');
  assert.equal(fnumberOf(2, 1), 'f/2');
  assert.equal(fnumberOf(28, 10), 'f/2.8');
  assert.equal(fnumberOf(100, 1), 'f/100');
  assert.equal(fnumberOf(1, 3), 'f/0.333333');
  assert.equal(fnumberOf(1, 100000), 'f/1e-05');
  assert.equal(fnumberOf(1234567, 1), 'f/1.23457e+06');
  assert.equal(fnumberOf(999999, 1), 'f/999999');
  assert.equal(fnumberOf(9999995, 10), 'f/1e+06'); // rounds up into the exponent form
});

test('{:g} rounds exact halves to even, like Python, and everything else by its exact value', () => {
  assert.equal(fnumberOf(485, 32), 'f/15.1562'); // 15.15625: tie, 2 is even
  assert.equal(fnumberOf(487, 32), 'f/15.2188'); // 15.21875: tie, 7 is odd
  assert.equal(fnumberOf(1999997, 2), 'f/999998'); // 999998.5
  assert.equal(fnumberOf(1999999, 2), 'f/1e+06'); // 999999.5
  assert.equal(fnumberOf(246913, 2), 'f/123456'); // 123456.5
  assert.equal(fnumberOf(246915, 2), 'f/123458'); // 123457.5
  assert.equal(fnumberOf(1000125, 1000), 'f/1000.12'); // 1000.125
  assert.equal(fnumberOf(1000375, 1000), 'f/1000.38'); // 1000.375
  assert.equal(fnumberOf(9999995, 1000000), 'f/10'); // 9.999995 is stored just above the tie
  assert.equal(fnumberOf(5, 64), 'f/0.078125');
  assert.equal(fnumberOf(3, 1024), 'f/0.00292969');
  assert.equal(fnumberOf(1, 10000), 'f/0.0001');
  assert.equal(fnumberOf(1, 1000000), 'f/1e-06');
});

test('zero denominators are skipped, not printed', () => {
  assert.equal(cameraOf(rational(F_NUMBER, [1, 0]), rational(EXPOSURE, [1, 0])), 'TestCam');
});

test('finds the Exif APP1 behind other segments', () => {
  const exif = new Uint8Array(exifJpeg({ ifd0: [MODEL] }));
  const segment = (marker, bytes) => [0xff, marker, (bytes.length + 2) >> 8, (bytes.length + 2) & 0xff, ...bytes];
  const xmp = segment(0xe1, [...'http://ns.adobe.com/xap/1.0/\0<x/>'].map((c) => c.charCodeAt(0)));
  const jfif = segment(0xe0, [0x4a, 0x46, 0x49, 0x46, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0]);
  const padded = new Uint8Array([0xff, 0xd8, 0xff, ...jfif, ...xmp, ...exif.slice(2)]);
  assert.deepEqual(readHints(padded.buffer), { camera: 'TestCam' });
});

test('skips junk bytes between segments like Pillow does', () => {
  const exif = new Uint8Array(exifJpeg({ ifd0: [MODEL], sub: [short(ISO, 100)] }));
  const jfif = [0xff, 0xe0, 0, 16, 0x4a, 0x46, 0x49, 0x46, 0, 1, 1, 0, 0, 1, 0, 1, 0, 0];
  for (const junk of [[0], [0, 0, 0], [0x12, 0x34]]) {
    const betweenSegments = new Uint8Array([0xff, 0xd8, ...jfif, ...junk, ...exif.slice(2)]);
    assert.deepEqual(readHints(betweenSegments.buffer), { camera: 'TestCam · ISO 100' }, `after JFIF: ${junk}`);
    const afterSoi = new Uint8Array([0xff, 0xd8, ...junk, ...jfif, ...exif.slice(2)]);
    assert.deepEqual(readHints(afterSoi.buffer), { camera: 'TestCam · ISO 100' }, `after SOI: ${junk}`);
  }
  // all junk to the end of the file: the scan stops at the buffer end
  assert.deepEqual(readHints(new Uint8Array([0xff, 0xd8, ...new Array(5000).fill(0)]).buffer), {});
});

test('accepts a typed array view as well as an ArrayBuffer', () => {
  const bytes = new Uint8Array(fixture('exif_le.jpg'));
  const padded = new Uint8Array(bytes.length + 7);
  padded.set(bytes, 7);
  assert.deepEqual(readHints(padded.subarray(7)), want);
});

test('a huge IFD entry count is refused', () => {
  for (const [name, little] of [['exif_le.jpg', true], ['exif_be.jpg', false]]) {
    const view = new DataView(fixture(name));
    view.setUint16(20, 0xffff, little); // IFD0 entry count: SOI 2 + APP1 header 4 + "Exif\0\0" 6 + TIFF header 8
    assert.deepEqual(readHints(view.buffer), {}, name);
  }
});

// Fixture layout (both byte orders): TIFF header at byte 12, IFD0 at 20 with its entries from 22, 12 bytes each:
// Model (offset field at 30), DateTime, Exif IFD pointer (value field at 54), GPS IFD pointer. APP1 ends at byte 246.
test('a value that points outside the Exif segment is ignored', () => {
  for (const [name, little] of [['exif_le.jpg', true], ['exif_be.jpg', false]]) {
    const view = new DataView(fixture(name));
    view.setUint32(30, 0xf0, little); // Model now starts at byte 252, in the JFIF segment that follows APP1
    assert.deepEqual(readHints(view.buffer), { date: '2025.11.30' }, name);
  }
});

test('a bad Exif IFD pointer keeps what IFD0 already gave', () => {
  for (const [name, little] of [['exif_le.jpg', true], ['exif_be.jpg', false]]) {
    const view = new DataView(fixture(name));
    view.setUint32(54, 0x7fffffff, little);
    assert.deepEqual(readHints(view.buffer), { date: '2025.11.30', camera: 'TestCam' }, name);
  }
});

test('text stops at the first NUL, so a model padded with NULs stays clean', () => {
  assert.deepEqual(readHints(exifJpeg({ ifd0: [ascii(MODEL_TAG, 'TestCam\0\0\0\0')] })), { camera: 'TestCam' });
});

test('truncated or damaged files never throw and never return anything but date and camera', () => {
  for (const name of ['exif_le.jpg', 'exif_be.jpg']) {
    const whole = fixture(name);
    for (let n = 0; n <= whole.byteLength; n++) onlyDateAndCamera(readHints(whole.slice(0, n)));
    let seed = 12345;
    const next = (limit) => { seed = (Math.imul(seed, 1103515245) + 12345) >>> 0; return seed % limit; };
    for (let round = 0; round < 3000; round++) {
      const bytes = new Uint8Array(whole.slice(0));
      for (let k = next(4) + 1; k > 0; k--) bytes[next(260)] = next(256);
      const hints = readHints(bytes.buffer);
      onlyDateAndCamera(hints);
      for (const value of Object.values(hints)) assert.equal(typeof value, 'string');
    }
  }
});

test('not a buffer at all', () => {
  for (const junk of [undefined, null, 'text', 42, {}]) assert.deepEqual(readHints(junk), {});
});
