import { test } from 'node:test';
import assert from 'node:assert/strict';
import { CONFIG, resolveConfig } from '../../admin/config.js';

const at = (href) => new URL(href); // hostname and search, as window.location has them
const withApi = (page, api) => resolveConfig(at(`${page}?api=${encodeURIComponent(api)}`)).api;

test('CONFIG values', () => {
  assert.deepEqual({ ...CONFIG }, { repo: 'HPotty36/nocturne', siteWorkflow: 'site.yml', api: 'https://api.github.com', pollMs: 15000 });
});

test('the published page always talks to api.github.com', () => {
  for (const href of ['https://hpotty36.github.io/nocturne/admin/', 'https://hpotty36.github.io/nocturne/admin/?api=http://127.0.0.1:8790',
    'http://example.test/admin/?api=http://127.0.0.1:8790']) {
    assert.equal(resolveConfig(at(href)).api, 'https://api.github.com', href);
  }
});

test('?api= is honoured on localhost and 127.0.0.1 for http://127.0.0.1:<port>, as an origin', () => {
  assert.equal(withApi('http://localhost:8000/admin/', 'http://127.0.0.1:8790'), 'http://127.0.0.1:8790');
  assert.equal(withApi('http://127.0.0.1:8000/admin/', 'http://127.0.0.1:8790'), 'http://127.0.0.1:8790');
  assert.equal(withApi('http://localhost:8000/admin/', 'http://127.0.0.1:8790/'), 'http://127.0.0.1:8790');
  assert.equal(withApi('http://localhost:8000/admin/', 'http://127.0.0.1:8790/some/path?x=1#y'), 'http://127.0.0.1:8790');
});

test('?api= pointing anywhere else is ignored, even on localhost (the CSP allows only http://127.0.0.1:*)', () => {
  for (const api of ['http://localhost:8790', 'http://user:pass@127.0.0.1:8790', 'http://user@127.0.0.1:8790',
    'https://127.0.0.1:8790', 'http://127.0.0.1', 'http://127.0.0.1.evil.example:8790', 'http://[::1]:8790',
    'https://evil.example', 'http://evil.example:8790', 'javascript:alert(1)', 'not a url', '']) {
    assert.equal(withApi('http://localhost:8000/admin/', api), 'https://api.github.com', api);
    assert.equal(withApi('http://127.0.0.1:8000/admin/', api), 'https://api.github.com', api);
  }
});

test('resolveConfig returns a copy', () => {
  const config = resolveConfig(at('http://localhost:8000/admin/?api=http://127.0.0.1:8790'));
  assert.notEqual(config, CONFIG);
  assert.equal(config.repo, CONFIG.repo);
  assert.equal(config.pollMs, CONFIG.pollMs);
  config.repo = 'someone/else';
  assert.equal(CONFIG.repo, 'HPotty36/nocturne');
});
