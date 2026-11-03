// The admin page shell: "GitHub로 로그인", the tabs, the site build status, logout.
//
// Logging in (every decision is in lib/auth.js): the button sends the person to GitHub, GitHub sends them back here
// with a code, and the Worker at config.exchange trades the code for a token. The token lives only inside `client` (a
// GitHub object, in a private field) until logout, until it runs out, or until GitHub turns it down; it is never
// written to storage, never logged, never put in a URL. Storage holds only the login on its way to GitHub and back
// (sessionStorage) and two flags: this browser has logged in before (localStorage), and this tab already went to
// GitHub on its own once (sessionStorage), so a failed login shows the button instead of going round again.
// Photos shared to the app from a phone's share sheet wait in the cache nocturne-share (the site's worker puts them there,
// lib/shared.js reads them). The login screen says how many wait; after the login the "새 사진" tab opens and offers
// them (올리기 / 버리기). Nothing is uploaded or discarded without the person saying so.
// Views get ctx = {gh, config, refreshStatus, reloadExhibit}:
//   gh             the GitHub client's methods; a rejected token anywhere sends the page back to the login screen
//   config         resolveConfig(location): repo, siteWorkflow, api, pollMs, and where the login goes
//   refreshStatus  refreshStatus(changed = false): read the site build status again; `changed` after a commit
//                  that changes the site, so the previous build does not pass for the new one
//   reloadExhibit  reloadExhibit({forget} = {}): read photos.json again and bring the "전시 중" tab up to date;
//                  `forget` names files whose redraft was thrown away in the queue

import { resolveConfig } from './config.js';
import {
  AUTO_KEY, MESSAGES, OLD_TOKEN_KEY, SIGNED_IN_KEY, LoginError, authorizeUrl, bootAction, challengeFor, checkReturn,
  exchange, forget, loginFailureMessage, needsLogin, newLogin, readReturn, recall, redirectUri, rejectedMessage, remember,
  saveLogin, takeLogin,
} from './lib/auth.js';
import { AuthError, GitHub, GitHubError } from './lib/github.js';
import { installedApp } from './lib/installed.js';
import { cacheStorage, pendingShares } from './lib/shared.js';
import { SiteStatus } from './lib/sitestatus.js';
import { renderExhibit } from './views/exhibit.js';
import { addFiles, closeQueue, renderQueue } from './views/queue.js';

const SITE_TEXT = { busy: '사이트 반영 중…', done: '반영됨', failed: '반영 실패' };
const sharedWaiting = (n) => `공유한 사진 ${n}장이 기다리고 있어요. 로그인하면 올릴 수 있어요`;

const config = resolveConfig(window.location);
const $ = (id) => document.getElementById(id);

function say(node, text, isError = false) {
  if (node.textContent !== text) node.textContent = text;
  node.classList.toggle('is-error', Boolean(text) && isError);
}

/** window.localStorage or window.sessionStorage; null where merely reading it throws (site data blocked). */
function storage(name) {
  try {
    return window[name];
  } catch {
    return null;
  }
}

// lib/auth.js treats null like a storage that refuses everything
const local = storage('localStorage');
const session = storage('sessionStorage');

// --- the GitHub client -----------------------------------------------------------------------

let client = null; // the GitHub client while logged in; it alone holds the token
let expiresAt = 0; // when its token ends (ms)

async function call(method, args) {
  const mine = client;
  if (!mine) throw new AuthError();
  try {
    return await mine[method](...args);
  } catch (error) {
    if (error instanceof AuthError && client === mine) tokenRejected(error.status);
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

// --- screens ---------------------------------------------------------------------------------

function show(screen) {
  $('boot').hidden = true;
  for (const name of ['login', 'desk']) $(name).hidden = name !== screen;
  $('logout').hidden = screen !== 'desk';
}

/** The login screen saying `message`, its button ready and focused. */
function openLogin(message = '', isError = false) {
  show('login');
  say($('login-msg'), message, isError);
  const button = $('login-start');
  button.removeAttribute('aria-disabled');
  button.focus();
  noteShared();
}

/** Under the login note: how many shared photos wait (nothing when none, or when the cache cannot be read). */
async function noteShared() {
  const note = $('login-shared');
  const found = await pendingShares(cacheStorage());
  note.textContent = found.length > 0 ? sharedWaiting(found.length) : '';
  note.hidden = found.length === 0;
}

function enter(github) {
  client = github;
  show('desk');
  const tab = tabs.find((t) => t.getAttribute('aria-selected') === 'true') ?? tabs[0];
  selectTab(tab, true);
  refreshStatus();
  renderQueue($('panel-new'), ctx);
  renderExhibit($('panel-exhibit'), ctx);
  openSharedTab(github);
}

/** Shared photos wait: show the "새 사진" tab, where they are offered (the focus follows only if it was on a tab). */
async function openSharedTab(github) {
  if ((await pendingShares(cacheStorage())).length === 0 || client !== github) return;
  selectTab(tabs[0], tabs.includes(document.activeElement));
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

/** GitHub turned the token down while the page worked: back to the login screen, saying why (see rejectedMessage). */
function tokenRejected(status) {
  lock();
  openLogin(rejectedMessage(status), true);
}

// --- logging in ------------------------------------------------------------------------------

/** Our own errors say what went wrong; anything else (a bug, a missing browser feature) also goes to the console. */
function report(error) {
  if (!(error instanceof LoginError || error instanceof GitHubError)) console.error(error); // never holds the token
}

/**
 * Off to GitHub. The button stays aria-disabled (clicks ignored, focus kept) until the page is left, or until it
 * shows again without having left; the login in progress waits in sessionStorage for the way back. GitHub takes this
 * page's place in the history (location.replace), so Back after logging in does not land on the page before the
 * login (which would show the button, or log in again).
 */
async function startLogin() {
  const button = $('login-start');
  if (button.getAttribute('aria-disabled') === 'true') return;
  button.setAttribute('aria-disabled', 'true');
  const login = newLogin(Date.now());
  if (!saveLogin(session, login)) {
    openLogin(MESSAGES.noStorage, true);
    return;
  }
  try {
    const challenge = await challengeFor(login.verifier);
    window.location.replace(authorizeUrl(config, { state: login.state, challenge, redirect: redirectUri(window.location, config) }));
  } catch (error) {
    report(error);
    openLogin(MESSAGES.failed, true); // no WebCrypto here: the button must not stay dead
  }
}

/**
 * Back from GitHub with `got` (readReturn): take the code out of the address, check that it belongs to the login this
 * tab started, trade it for a token and make sure the account may write to the repository. A failure leaves
 * sessionStorage[AUTO_KEY] in place, so a reload shows the button instead of going to GitHub again.
 */
async function finishLogin(got) {
  const redirect = redirectUri(window.location, config);
  window.history.replaceState(null, '', redirect);
  const checked = checkReturn(takeLogin(session), got, Date.now());
  if (checked.message) {
    openLogin(checked.message, true);
    return;
  }
  say($('boot'), MESSAGES.starting);
  let answer;
  let github;
  try {
    answer = await exchange(config, { code: checked.code, verifier: checked.verifier, redirect });
    github = new GitHub({ token: answer.token, api: config.api });
    if (!(await github.canPush(config.repo))) {
      openLogin(MESSAGES.notAllowed, true); // the token goes with `github`
      return;
    }
    if (!(await github.installedOn(config.repo))) {
      openLogin(MESSAGES.notInstalled, true);
      return;
    }
  } catch (error) {
    report(error);
    openLogin(loginFailureMessage(error), true);
    return;
  }
  // outside the try: a view that throws must not leave a live client under a "failed" message
  remember(local, SIGNED_IN_KEY, '1');
  forget(session, AUTO_KEY);
  expiresAt = answer.expiresAt;
  enter(github);
}

$('login-start').addEventListener('click', () => startLogin());

// Back in this tab after a while. Logged in: a token that has ended (or soon will) is let go before anything is
// typed. Not logged in: a login button left disabled works again, for when the page never left (the GitHub app on a
// phone took the login link, say); nothing is said.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible') return;
  if (!client) {
    $('login-start').removeAttribute('aria-disabled');
    return;
  }
  if (!needsLogin(expiresAt, Date.now())) return;
  lock();
  openLogin(MESSAGES.expired, true);
});

// A page brought back with the browser's back or forward button may come back exactly as it was left (the button
// disabled, or "로그인하는 중…"). Offer the button again; never go to GitHub on its own from here.
window.addEventListener('pageshow', (event) => {
  if (event.persisted && !client) openLogin();
});

$('logout').addEventListener('click', () => {
  forget(local, SIGNED_IN_KEY);
  lock();
  openLogin(MESSAGES.loggedOut);
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

forget(local, OLD_TOKEN_KEY); // the sealed token of the old paste-a-token login

// "Nocturne" opens the site in a new tab, so this page stays open and logged in. In an installed app a new tab would
// leave the app (an iPad opens Safari), so there it goes to the site in place; "관리" there logs in again by itself.
if (installedApp(window)) document.querySelector('.brand .mark')?.removeAttribute('target');

const got = readReturn(window.location);
const action = bootAction({
  returned: got !== null,
  signedIn: recall(local, SIGNED_IN_KEY) === '1',
  autoTried: recall(session, AUTO_KEY) === '1',
});
if (action === 'return') {
  finishLogin(got);
} else if (action === 'auto') {
  remember(session, AUTO_KEY, '1'); // once per tab: after a failed login a reload shows the button
  say($('boot'), MESSAGES.starting);
  startLogin();
} else {
  openLogin();
}
