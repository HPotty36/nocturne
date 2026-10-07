// The admin page shell: first-time setup or unlock, the tabs, the site build status, logout.
//
// The GitHub token is kept on this device only sealed with the lock passphrase (localStorage["nocturne.token"]).
// After unlock the plain token lives only inside `client` (a GitHub object, in a private field) until logout or
// until GitHub turns it down; it is never written anywhere else, never logged, never put in a URL.
// Views get ctx = {gh, config, refreshStatus, reloadExhibit}:
//   gh             the GitHub client's methods; a rejected token anywhere sends the page back to setup
//   config         resolveConfig(location): repo, siteWorkflow, api, pollMs
//   refreshStatus  refreshStatus(changed = false): read the site build status again; `changed` after a commit
//                  that changes the site, so the previous build does not pass for the new one
//   reloadExhibit  reloadExhibit({forget} = {}): read photos.json again and bring the "전시 중" tab up to date;
//                  `forget` names files whose redraft was thrown away in the queue

import { resolveConfig } from './config.js';
import { AuthError, GitHub, GitHubError } from './lib/github.js';
import { SiteStatus } from './lib/sitestatus.js';
import { seal, unseal, VaultError } from './lib/vault.js';
import { renderExhibit } from './views/exhibit.js';
import { addFiles, closeQueue, renderQueue } from './views/queue.js';

const STORE_KEY = 'nocturne.token';

const TOKEN_AGAIN = '토큰을 다시 넣어 주세요';
const NO_STORAGE = '이 브라우저에서는 저장할 수 없어요';
const VAULT_FAILED = '잠금을 풀 수 없어요. 다시 시도해 주세요';
const MISMATCH = '두 잠금 비밀번호가 같지 않아요';
const SITE_TEXT = { busy: '사이트 반영 중…', done: '반영됨', failed: '반영 실패' };

const config = resolveConfig(window.location);
const $ = (id) => document.getElementById(id);

function say(node, text, isError = false) {
  if (node.textContent !== text) node.textContent = text;
  node.classList.toggle('is-error', Boolean(text) && isError);
}

// --- the sealed token on this device ---------------------------------------------------------

/** The sealed token: an object, null when there is none (or it is damaged), undefined when storage cannot be used. */
function readSeal() {
  let text;
  try {
    text = window.localStorage.getItem(STORE_KEY);
  } catch {
    return undefined; // storage switched off, or a private window that refuses it
  }
  if (text === null) return null;
  try {
    const sealed = JSON.parse(text);
    return sealed !== null && typeof sealed === 'object' ? sealed : null;
  } catch {
    return null;
  }
}

function writeSeal(sealed) {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify(sealed));
    return true;
  } catch {
    return false;
  }
}

function forgetSeal() {
  try {
    window.localStorage.removeItem(STORE_KEY);
  } catch {
    // nothing could have been stored either
  }
}

// --- the GitHub client -----------------------------------------------------------------------

let client = null; // the GitHub client while unlocked; it alone holds the token

async function call(method, args) {
  const mine = client;
  if (!mine) throw new AuthError();
  try {
    return await mine[method](...args);
  } catch (error) {
    if (error instanceof AuthError && client === mine) tokenRejected();
    throw error;
  }
}

const METHODS = ['canPush', 'listDir', 'getFile', 'getJSON', 'putJSON', 'commitFiles', 'latestRun'];
const gh = Object.freeze(Object.fromEntries(METHODS.map((name) => [name, (...args) => call(name, args)])));

const ctx = Object.freeze({
  gh,
  config,
  refreshStatus: (changed = false) => refreshStatus(changed),
  reloadExhibit: (options) => renderExhibit($('panel-exhibit'), ctx, options),
});

/** A client for `token` if it may write to the repository; AuthError if not, GitHubError if GitHub cannot be reached. */
async function checkedClient(token) {
  const candidate = new GitHub({ token, api: config.api }); // AuthError for a token that cannot be one
  if (!(await candidate.canPush(config.repo))) throw new AuthError(403);
  return candidate;
}

// --- screens ---------------------------------------------------------------------------------

function show(screen) {
  $('boot').hidden = true;
  for (const name of ['setup', 'unlock', 'desk']) $(name).hidden = name !== screen;
  $('logout').hidden = screen !== 'desk';
  if (screen !== 'unlock') $('unlock-pass').value = ''; // a typed passphrase never waits on a hidden screen
}

function openSetup(message = '', isError = false) {
  for (const id of ['setup-token', 'setup-pass', 'setup-pass2']) $(id).value = '';
  show('setup');
  say($('setup-msg'), message, isError);
  $('setup-token').focus();
}

function openUnlock() {
  $('unlock-pass').value = '';
  show('unlock');
  say($('unlock-msg'), '');
  $('unlock-pass').focus();
}

function enter(github) {
  client = github;
  show('desk');
  const tab = tabs.find((t) => t.getAttribute('aria-selected') === 'true') ?? tabs[0];
  selectTab(tab, true);
  refreshStatus();
  renderQueue($('panel-new'), ctx);
  renderExhibit($('panel-exhibit'), ctx);
}

/**
 * Lock the page: forget the client (and with it the token), stop the status checks and the uploads (photos held
 * for them are let go), empty the tabs.
 */
function lock() {
  client = null;
  siteStatus.stop();
  closeQueue();
  $('panel-new').replaceChildren();
  $('panel-exhibit').replaceChildren();
}

/** GitHub turned the token down: back to setup. The sealed token stays until a new one is saved. */
function tokenRejected() {
  lock();
  openSetup(TOKEN_AGAIN, true);
}

/** While a form works, it ignores another submit; aria-disabled keeps the focus where it was. */
function busy(form, on) {
  form.setAttribute('aria-busy', String(on));
  form.querySelector('[type="submit"]').setAttribute('aria-disabled', String(on));
}

// first time (or a new token): check the token, seal it, keep only the sealed copy
$('setup-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.getAttribute('aria-busy') === 'true') return;
  const msg = $('setup-msg');
  const token = $('setup-token').value.trim();
  const pass = $('setup-pass').value;
  if (pass.normalize('NFC') !== $('setup-pass2').value.normalize('NFC')) {
    say(msg, MISMATCH, true);
    return;
  }
  if (readSeal() === undefined) {
    say(msg, NO_STORAGE, true);
    return;
  }
  busy(form, true);
  say(msg, '확인하는 중…');
  try {
    const sealed = await seal(token, pass); // VaultError: no token, passphrase too short
    const github = await checkedClient(token);
    if (!writeSeal(sealed)) {
      say(msg, NO_STORAGE, true);
      return;
    }
    for (const id of ['setup-token', 'setup-pass', 'setup-pass2']) $(id).value = ''; // the token leaves the page at once
    say(msg, '');
    enter(github);
  } catch (error) {
    if (error instanceof VaultError || error instanceof GitHubError) say(msg, error.message, true);
    else say(msg, VAULT_FAILED, true);
  } finally {
    busy(form, false);
  }
});

// later visits: the passphrase opens the sealed token
$('unlock-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  if (form.getAttribute('aria-busy') === 'true') return;
  const msg = $('unlock-msg');
  const sealed = readSeal();
  if (!sealed) {
    openSetup(sealed === undefined ? NO_STORAGE : '', sealed === undefined);
    return;
  }
  busy(form, true);
  say(msg, '여는 중…');
  try {
    let github;
    try {
      github = await checkedClient(await unseal(sealed, $('unlock-pass').value));
    } catch (error) {
      if (error instanceof AuthError) {
        tokenRejected();
        return;
      }
      if (error instanceof VaultError || error instanceof GitHubError) say(msg, error.message, true);
      else say(msg, VAULT_FAILED, true);
      $('unlock-pass').focus();
      $('unlock-pass').select();
      return;
    }
    $('unlock-pass').value = '';
    say(msg, '');
    enter(github);
  } finally {
    busy(form, false);
  }
});

// the token is a textarea (so no password manager offers to keep it): Enter sends the form instead of a line break
$('setup-token').addEventListener('keydown', (event) => {
  if (event.key !== 'Enter' || event.isComposing) return;
  event.preventDefault();
  $('setup-form').requestSubmit();
});

$('unlock-reset').addEventListener('click', () => openSetup());

$('logout').addEventListener('click', () => {
  forgetSeal();
  lock();
  openSetup('로그아웃했어요');
});

// --- tabs ------------------------------------------------------------------------------------

const tabs = [...document.querySelectorAll('[role="tab"]')];

function selectTab(tab, focus) {
  for (const t of tabs) {
    const on = t === tab;
    t.setAttribute('aria-selected', String(on));
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute('aria-controls')).hidden = !on;
  }
  if (focus) tab.focus();
}

/**
 * A tab the person switched to. "새 사진" then looks at the queue again: an upload from another device, or a redraft
 * asked for in "전시 중" that the AI gave up on, shows without waiting for a reload.
 */
function openTab(tab, focus) {
  const switched = tab.getAttribute('aria-selected') !== 'true';
  selectTab(tab, focus);
  if (switched && tab.id === 'tab-new' && client) renderQueue($('panel-new'), ctx);
}

tabs.forEach((tab, i) => {
  tab.addEventListener('click', () => openTab(tab, false));
  tab.addEventListener('keydown', (event) => {
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[event.key];
    if (to === undefined) return;
    event.preventDefault();
    openTab(tabs[(to + tabs.length) % tabs.length], true);
  });
});

// --- site build status -----------------------------------------------------------------------

function showStatus(state, url) {
  const box = $('site-status');
  box.hidden = state === null;
  if (state === null) return;
  box.dataset.state = state;
  say($('site-status-text'), SITE_TEXT[state]);
  const link = $('site-status-link');
  const linked = state === 'failed' && typeof url === 'string' && url.startsWith('https://');
  link.hidden = !linked;
  if (linked) link.href = url;
  else link.removeAttribute('href');
}

// when to read the newest site.yml run again and what to show: see lib/sitestatus.js
const siteStatus = new SiteStatus({ read: () => gh.latestRun(config.repo, config.siteWorkflow), show: showStatus });

/** Read the site build status again; `changed` right after a commit that changes the site. */
function refreshStatus(changed = false) {
  if (client) siteStatus.refresh(changed); // a rejected token is handled by gh; other read errors by SiteStatus
}

// --- start -----------------------------------------------------------------------------------

if (['localhost', '127.0.0.1'].includes(window.location.hostname)) {
  // For local browser checks only: hands File objects to the "새 사진" tab as if they had been picked there.
  // Resolves when they have all been sent (or failed).
  window.__nocturne = { addFiles: (files) => addFiles(files) };
}

const stored = readSeal();
if (stored === undefined) openSetup(NO_STORAGE, true);
else if (stored === null) openSetup();
else openUnlock();
