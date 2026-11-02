// Whether the admin page runs as an installed app (the site's or its own), where a new tab would leave the app.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { installedApp } from '../../admin/lib/installed.js';

const win = ({ standaloneQuery = false, iosStandalone } = {}) => ({
  matchMedia: (query) => ({ matches: query === '(display-mode: standalone)' && standaloneQuery }),
  navigator: iosStandalone === undefined ? {} : { standalone: iosStandalone },
});

test('an installed app (display-mode: standalone) counts', () => {
  assert.equal(installedApp(win({ standaloneQuery: true })), true);
});

test('a home-screen app on iPhone or iPad counts, by navigator.standalone', () => {
  assert.equal(installedApp(win({ iosStandalone: true })), true);
});

test('a browser tab does not', () => {
  assert.equal(installedApp(win()), false);
  assert.equal(installedApp(win({ iosStandalone: false })), false);
});

test('a browser without matchMedia is a tab', () => {
  assert.equal(installedApp({ navigator: {} }), false);
});
