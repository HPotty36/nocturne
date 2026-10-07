// Harness for admin/lib/image.js: runs processPhoto on every file named in ?inputs=a.jpg,b.jpg
// (paths relative to this page), saves the results through serve.py and signals with window.done.
// With &fallback=1, createImageBitmap rejects as an engine that does not know imageOrientation 'from-image' does
// (TypeError), so image.js has to decode through an <img>.
//   window.results  [{name, hints, grayscale, width, height, full: base64, thumb: base64}]
//   window.refused  how many createImageBitmap calls the fallback stub refused (0 without &fallback=1)
//   window.done     true when finished, whether it worked or not
//   window.error    message of what went wrong (unset on success)
import { processPhoto } from '../../admin/lib/image.js';

const RESULTS_URL = '/tests/browser/out/results.json';
const status = document.getElementById('status');

function say(text) {
  status.textContent = text;
}

/** Base64 of a Blob. */
function base64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

/** File name without folders and extension: out/rot_gps.jpg -> rot_gps */
function nameOf(path) {
  return path.split('/').pop().replace(/\.[^.]*$/, '');
}

async function run() {
  if (!['localhost', '127.0.0.1'].includes(location.hostname)) {
    throw new Error(`this harness only runs on localhost, not on ${location.hostname}`);
  }
  const params = new URLSearchParams(location.search);
  const inputs = (params.get('inputs') || '').split(',').filter(Boolean);
  if (!inputs.length) throw new Error('no ?inputs=a.jpg,b.jpg given');
  window.refused = 0;
  if (params.get('fallback') === '1') {
    globalThis.createImageBitmap = () => {
      window.refused += 1;
      return Promise.reject(new TypeError("The provided value 'from-image' is not a valid enum value of type ImageOrientation."));
    };
  }

  const results = [];
  for (const path of inputs) {
    say(`processing ${path} (${results.length + 1}/${inputs.length})`);
    const response = await fetch(path);
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    const photo = await processPhoto(await response.blob());
    results.push({
      name: nameOf(path),
      hints: photo.hints,
      grayscale: photo.grayscale,
      width: photo.width,
      height: photo.height,
      full: await base64(photo.full),
      thumb: await base64(photo.thumb),
    });
  }
  window.results = results;

  say('saving results.json');
  const saved = await fetch(RESULTS_URL, { method: 'PUT', body: JSON.stringify(results) });
  if (!saved.ok) throw new Error(`saving results.json: HTTP ${saved.status}`);
  say(`done: ${results.map((r) => `${r.name} ${r.width}x${r.height} gray=${r.grayscale}`).join(', ')}; createImageBitmap refused ${window.refused}x`);
}

run()
  .catch((error) => {
    window.error = `${error.name}: ${error.message}`;
    say(`failed: ${window.error}`);
  })
  .finally(() => {
    window.done = true;
  });
