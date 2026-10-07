// Where the admin page works: the repository, the site workflow, the GitHub API, and how often it looks again.
// DOM-free ES module.

export const CONFIG = Object.freeze({
  repo: 'HPotty36/nocturne',
  siteWorkflow: 'site.yml',
  api: 'https://api.github.com',
  pollMs: 15000,
});

/**
 * Author and committer of every commit the admin page makes. Without them GitHub uses the account's profile name and
 * email settings; this is the identity the repository's own commits use (scripts/github_api.py AUTHOR is the same).
 */
export const COMMIT_AUTHOR = Object.freeze({ name: 'HPotty36', email: '112685098+HPotty36@users.noreply.github.com' });

const LOCAL_PAGES = ['localhost', '127.0.0.1'];

/** `http://127.0.0.1:<port>` as an origin, or null for anything else (the page's CSP lets it reach only that). */
function localApi(text) {
  let url;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  const local = url.protocol === 'http:' && url.hostname === '127.0.0.1' && url.port !== '' && url.username === '' && url.password === '';
  return local ? url.origin : null;
}

/**
 * A copy of CONFIG for the page at `location` (window.location, or a URL). Only a page opened on this computer
 * (localhost / 127.0.0.1) may point the API at a local fake GitHub with `?api=http://127.0.0.1:<port>`; anywhere
 * else, and for any other address, `?api=` is ignored, so a link can never send the token somewhere else.
 */
export function resolveConfig(location) {
  const config = { ...CONFIG };
  const api = new URLSearchParams(location.search).get('api');
  if (LOCAL_PAGES.includes(location.hostname) && api) config.api = localApi(api) ?? config.api;
  return config;
}
