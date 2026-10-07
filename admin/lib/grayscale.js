// Black-and-white check. Same rule as grayscale_from_chroma in scripts/photolib.py.

const MEAN_LIMIT = 4; // average chroma must stay below this
const EDGE_LIMIT = 16; // chroma at the 99th percentile must stay below this

/**
 * True when per-pixel chroma (max - min of R, G, B) says the picture is black and white.
 * A few coloured pixels (a sign, a lamp) are allowed: the mean must be low and the
 * 99th-percentile pixel must be nearly neutral.
 */
export function grayscaleFromChroma(values) {
  if (!values.length) return false;
  let sum = 0;
  for (const v of values) sum += v;
  if (sum / values.length >= MEAN_LIMIT) return false;
  const ordered = Array.from(values).sort((a, b) => a - b);
  return ordered[Math.floor(0.99 * (ordered.length - 1))] < EDGE_LIMIT;
}

/** Chroma of every pixel of canvas ImageData.data (RGBA bytes); alpha is ignored. */
export function chromaOfRGBA(data) {
  const chroma = [];
  for (let i = 0; i + 2 < data.length; i += 4) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    chroma.push(Math.max(r, g, b) - Math.min(r, g, b));
  }
  return chroma;
}
