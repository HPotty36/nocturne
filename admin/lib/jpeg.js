// Last line of defence for the privacy boundary: whatever a browser's canvas encoder wrote, only the
// segments needed to show the picture leave the device. No DOM here, so Node tests can run it.
// (Chrome writes just JFIF and a colour profile; WebKit's encoder may add segments of its own.)

export const UNREADABLE = '이 사진을 읽을 수 없어요. 사진 앱에서 JPEG로 골라 주세요';

export class ImageError extends Error {
  /** The photo cannot be used (not decodable here, e.g. HEIC outside Safari, or damaged). The message is shown to the user. */
  constructor(message, options) {
    super(message, options);
    this.name = 'ImageError';
  }
}

const SOI = 0xd8, EOI = 0xd9, SOS = 0xda;
const APP0 = 0xe0, APP2 = 0xe2, APP15 = 0xef, COM = 0xfe;
const JFIF = [0x4a, 0x46, 0x49, 0x46, 0]; // "JFIF\0"
const ICC = [0x49, 0x43, 0x43, 0x5f, 0x50, 0x52, 0x4f, 0x46, 0x49, 0x4c, 0x45, 0]; // "ICC_PROFILE\0"

/**
 * A copy of a JPEG without any metadata: SOI, every segment that is not an APPn or a comment, and the scan data,
 * byte for byte, plus APP0 "JFIF" and APP2 "ICC_PROFILE" (the colour profile). Exif, XMP, IPTC, comments, every other
 * APPn and anything after the final EOI are dropped. Throws ImageError when the bytes are not a complete JPEG;
 * the input is never handed back unchecked. `input` is a Uint8Array (or any view / ArrayBuffer).
 */
export function stripJpegMetadata(input) {
  const bytes = ArrayBuffer.isView(input) ? new Uint8Array(input.buffer, input.byteOffset, input.byteLength) : new Uint8Array(input);
  const end = bytes.length;
  const bad = () => new ImageError(UNREADABLE);
  if (end < 4 || bytes[0] !== 0xff || bytes[1] !== SOI) throw bad();

  const kept = [bytes.subarray(0, 2)];
  let pos = 2;
  let scans = 0;
  for (;;) {
    while (pos + 1 < end && bytes[pos] === 0xff && bytes[pos + 1] === 0xff) pos += 1; // fill bytes may come before a marker
    if (pos + 2 > end || bytes[pos] !== 0xff) throw bad();
    const marker = bytes[pos + 1];
    if (marker === EOI) {
      if (!scans) throw bad(); // no picture data at all
      kept.push(bytes.subarray(pos, pos + 2));
      break; // whatever follows the end of the image is not part of it
    }
    if (marker === 0x00 || marker === 0x01 || (marker >= 0xd0 && marker <= SOI)) throw bad(); // TEM, RSTn, SOI: not valid between segments
    if (pos + 4 > end) throw bad();
    const size = (bytes[pos + 2] << 8) | bytes[pos + 3]; // counts its own two bytes
    if (size < 2 || pos + 2 + size > end) throw bad();
    const segment = bytes.subarray(pos, pos + 2 + size);
    if (keeps(marker, segment)) kept.push(segment);
    pos += 2 + size;

    if (marker === SOS) {
      scans += 1;
      const dataStart = pos;
      pos = endOfScanData(bytes, pos); // the compressed data runs until the next real marker
      if (pos < 0) throw bad(); // cut off: no marker follows
      kept.push(bytes.subarray(dataStart, pos));
    }
  }

  const out = new Uint8Array(kept.reduce((sum, part) => sum + part.length, 0));
  let at = 0;
  for (const part of kept) {
    out.set(part, at);
    at += part.length;
  }
  return out;
}

/** Whether a length-carrying segment belongs in the output. */
function keeps(marker, segment) {
  if (marker === COM) return false;
  if (marker < APP0 || marker > APP15) return true; // DQT, SOF, DHT, DRI, SOS, ...: the picture itself
  if (marker === APP0) return startsWith(segment, JFIF);
  if (marker === APP2) return startsWith(segment, ICC);
  return false; // APP1 (Exif, XMP), APP13 (IPTC), ...
}

/** Does the payload (after marker and length) of a segment begin with these bytes? */
function startsWith(segment, prefix) {
  return segment.length >= 4 + prefix.length && prefix.every((byte, i) => segment[4 + i] === byte);
}

/** Position of the first marker after compressed scan data starting at `from` (stuffed 0xFF00, RSTn and fill bytes are data), or -1. */
function endOfScanData(bytes, from) {
  let pos = from;
  while (pos < bytes.length) {
    if (bytes[pos] !== 0xff) {
      pos += 1;
      continue;
    }
    const next = bytes[pos + 1];
    if (next === undefined) return -1;
    if (next === 0x00 || (next >= 0xd0 && next <= 0xd7)) pos += 2; // stuffed byte, restart marker
    else if (next === 0xff) pos += 1; // fill
    else return pos;
  }
  return -1;
}
