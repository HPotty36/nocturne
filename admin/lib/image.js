// Turns a picked photo into the two web copies, in the browser, before anything leaves the device.
// Canvas encoding writes no EXIF, and stripJpegMetadata removes whatever else an encoder adds (WebKit's may),
// so GPS and every other metadata segment are gone from both copies.
// The only part that needs a browser; tests/browser/ checks it in a real one.

import { readHints } from './exif.js';
import { chromaOfRGBA, grayscaleFromChroma } from './grayscale.js';
import { ImageError, UNREADABLE, stripJpegMetadata } from './jpeg.js';

export { ImageError }; // defined in jpeg.js, which this module builds on and which has to stay free of the DOM

/** name -> [long edge in px, JPEG quality]. Same sizes as SIZES in scripts/photolib.py. */
export const SIZES = { full: [2400, 0.84], thumb: [1000, 0.78] };

const GRAY_SAMPLE_EDGE = 256; // long edge of the downsized copy that is measured, as in photolib.py
const EXIF_HEAD_BYTES = 262144; // the EXIF block sits in the first bytes of a JPEG

/**
 * Web copies of a photo file:
 * {full, thumb (JPEG Blobs, no metadata), hints (date/camera from the original's EXIF), grayscale, width, height (of full)}.
 */
export async function processPhoto(file) {
  const hints = await readOriginalHints(file);
  const picture = await decode(file); // turned upright, so its size is as shown

  const [fullEdge, fullQuality] = SIZES.full;
  const [thumbEdge, thumbQuality] = SIZES.thumb;
  let fullCanvas;
  try {
    fullCanvas = shrink(picture.source, fullEdge);
  } finally {
    picture.release(); // the source pixels are not needed again; free them before the encodes, which are the memory peak on a phone
  }
  const full = await encode(fullCanvas, fullQuality);
  const width = fullCanvas.width, height = fullCanvas.height;

  const thumbCanvas = shrink(fullCanvas, thumbEdge); // from the 2400px copy: less to draw, same look
  release(fullCanvas);
  const thumb = await encode(thumbCanvas, thumbQuality);

  const sample = shrink(thumbCanvas, GRAY_SAMPLE_EDGE, { willReadFrequently: true });
  release(thumbCanvas);
  const pixels = context(sample).getImageData(0, 0, sample.width, sample.height).data;
  const grayscale = grayscaleFromChroma(chromaOfRGBA(pixels));
  release(sample);

  return { full, thumb, hints, grayscale, width, height };
}

/**
 * The photo decoded and turned upright by its EXIF orientation: {source (something drawImage takes), release()}.
 * createImageBitmap with imageOrientation 'from-image' first. An engine that does not know that option rejects it with
 * a TypeError (an older WebKit may; then every iPhone upload would fail as unreadable), and an engine without
 * createImageBitmap has nothing to call: both read it through an <img> instead, which every current engine shows (and
 * draws) upright. ImageError when the photo cannot be read either way (HEIC outside Safari, a damaged file).
 */
async function decode(file) {
  if (typeof globalThis.createImageBitmap === 'function') {
    try {
      const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' });
      return { source: bitmap, release: () => bitmap.close() };
    } catch (error) {
      if (!(error instanceof TypeError)) throw new ImageError(UNREADABLE, { cause: error }); // the engine could not read it
    }
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await loaded(url);
    if (!img.naturalWidth || !img.naturalHeight) throw new Error('no picture in the file');
    return { source: img, release: () => URL.revokeObjectURL(url) };
  } catch (error) {
    URL.revokeObjectURL(url);
    throw new ImageError(UNREADABLE, { cause: error });
  }
}

/**
 * An <img> of `url` once it has loaded (drawImage decodes it then). Not img.decode(): Chromium keeps that promise
 * pending while the page is hidden (another app or tab in front), and the load event comes either way.
 */
function loaded(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('the browser could not read the picture'));
    img.src = url;
  });
}

/** Pixel size of a draw source as it is shown: an <img> by its natural (upright) size, a bitmap or canvas by its own. */
function sizeOf(source) {
  return 'naturalWidth' in source ? [source.naturalWidth, source.naturalHeight] : [source.width, source.height];
}

/** Date and camera from the original's EXIF; {} when there is none or it cannot be read. Never throws. */
async function readOriginalHints(file) {
  try {
    return readHints(await file.slice(0, EXIF_HEAD_BYTES).arrayBuffer());
  } catch {
    return {};
  }
}

/**
 * A new canvas with `source` (an ImageBitmap, an <img> or a canvas) drawn at a long edge of `edge`; never larger than
 * the source. Halves step by step while the source is more than twice the target, then draws once at the exact size:
 * one big jump would skip pixels, halving keeps them averaged.
 * `contextOptions` go to the 2D context of the returned canvas, which is where they take effect (the first getContext call wins).
 */
function shrink(source, edge, contextOptions) {
  const [sourceWidth, sourceHeight] = sizeOf(source);
  const long = Math.max(sourceWidth, sourceHeight);
  const scale = Math.min(1, edge / long);
  const width = Math.max(1, Math.round(sourceWidth * scale));
  const height = Math.max(1, Math.round(sourceHeight * scale));

  let current = source;
  let w = sourceWidth, h = sourceHeight;
  while (Math.max(w, h) > edge * 2) {
    w = Math.max(1, Math.round(w / 2));
    h = Math.max(1, Math.round(h / 2));
    const half = draw(current, w, h);
    if (current !== source) release(current);
    current = half;
  }
  const result = draw(current, width, height, contextOptions);
  if (current !== source) release(current);
  return result;
}

function draw(source, width, height, contextOptions) {
  const canvas = makeCanvas(width, height);
  const ctx = context(canvas, contextOptions);
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(source, 0, 0, width, height);
  return canvas;
}

function makeCanvas(width, height) {
  if (typeof OffscreenCanvas !== 'undefined') return new OffscreenCanvas(width, height);
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  return canvas;
}

function context(canvas, options) {
  const ctx = canvas.getContext('2d', options);
  if (!ctx) throw new ImageError(UNREADABLE);
  return ctx;
}

/** JPEG Blob of a canvas, with every metadata segment the encoder wrote taken out. */
async function encode(canvas, quality) {
  const blob = canvas.convertToBlob
    ? await canvas.convertToBlob({ type: 'image/jpeg', quality })
    : await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', quality));
  if (!blob) throw new ImageError(UNREADABLE); // the browser could not encode it (too large for its memory, say)
  // Not trusting the encoder: throws ImageError for anything that is not a complete JPEG (a browser may answer PNG).
  const clean = stripJpegMetadata(new Uint8Array(await blob.arrayBuffer()));
  return new Blob([clean], { type: 'image/jpeg' });
}

/** Give a canvas's memory back now; phones cap the total canvas memory and a 2400px canvas is 23 MB. */
function release(canvas) {
  canvas.width = 0;
  canvas.height = 0;
}
