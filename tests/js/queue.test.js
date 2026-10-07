import { test } from 'node:test';
import assert from 'node:assert/strict';
import { iso, newItem, label, toWaiting, canDiscard, STALE_SECONDS, hint, SLOW_SECONDS, SLOW_HINT } from '../../admin/lib/queue.js';

const NOW = Date.parse('2026-10-06T12:00:00Z');

test('newItem matches Python shape', () => {
  assert.deepEqual(Object.keys(newItem({name: 'a', kind: 'new', grayscale: false, now: NOW})),
    ['name', 'kind', 'file', 'uploaded_at', 'date', 'camera', 'grayscale', 'status', 'claimed_at', 'by', 'error']);
  assert.equal(newItem({name: 'a', kind: 'new', grayscale: false, now: NOW}).uploaded_at, '2026-10-06T12:00:00Z');
});

test('newItem values', () => {
  assert.deepEqual(newItem({name: '1234', kind: 'new', grayscale: true, now: NOW, date: '2025.11.30', camera: 'iPhone 12 mini · f/1.6 · 1/121s · ISO 100'}), {
    name: '1234', kind: 'new', file: null, uploaded_at: '2026-10-06T12:00:00Z', date: '2025.11.30',
    camera: 'iPhone 12 mini · f/1.6 · 1/121s · ISO 100', grayscale: true, status: 'waiting', claimed_at: null, by: null, error: null,
  });
  const redraft = newItem({name: 'redraft-a1', kind: 'redraft', grayscale: false, now: NOW, file: 'a1'});
  assert.equal(redraft.file, 'a1');
  assert.equal(redraft.date, null);
  assert.equal(redraft.camera, null);
});

test('iso drops milliseconds and is UTC', () => {
  assert.equal(iso(Date.parse('2026-10-06T12:00:00Z')), '2026-10-06T12:00:00Z');
  assert.equal(iso(Date.parse('2026-10-06T12:00:00.999Z')), '2026-10-06T12:00:00Z');
  assert.equal(iso(Date.parse('2026-01-02T03:04:05+09:00')), '2026-01-01T18:04:05Z');
});

test('labels', () => {
  assert.equal(label({status: 'waiting'}).text, 'AI 대기');
  assert.equal(label({status: 'github'}).text, 'GitHub AI가 보는 중… (몇 분)');
  assert.deepEqual(label({status: 'failed'}), {text: 'AI 실패', tone: 'error'});
});

test('every status has a label; an unknown one is an error', () => {
  assert.deepEqual(label({status: 'waiting'}), {text: 'AI 대기', tone: 'busy'});
  assert.deepEqual(label({status: 'pc'}), {text: 'PC AI가 보는 중…', tone: 'busy'});
  assert.deepEqual(label({status: 'github'}), {text: 'GitHub AI가 보는 중… (몇 분)', tone: 'busy'});
  assert.deepEqual(label({status: 'done'}), {text: '알 수 없음', tone: 'error'});
  assert.deepEqual(label({}), {text: '알 수 없음', tone: 'error'});
});

test('retry and discard', () => {
  const f = {...newItem({name: 'a', kind: 'new', grayscale: false, now: NOW}), status: 'failed', error: 'x', by: 'pc', claimed_at: 'y'};
  assert.deepEqual([toWaiting(f).status, toWaiting(f).error, toWaiting(f).by], ['waiting', null, null]);
  assert.equal(canDiscard(f), true); assert.equal(canDiscard({status: 'pc'}), false);
});

test('toWaiting resets the claim, keeps the rest and does not touch its argument', () => {
  const f = {...newItem({name: 'a', kind: 'new', grayscale: true, now: NOW, date: '2025.11.30'}), status: 'failed', error: 'x', by: 'github', claimed_at: '2026-10-06T12:01:00Z'};
  const copy = structuredClone(f);
  const w = toWaiting(f);
  assert.deepEqual(w, {...copy, status: 'waiting', error: null, claimed_at: null, by: null});
  assert.equal(w.uploaded_at, '2026-10-06T12:00:00Z');
  assert.deepEqual(Object.keys(w), Object.keys(copy));
  assert.deepEqual(f, copy);
});

test('canDiscard only for waiting and failed', () => {
  for (const [status, want] of [['waiting', true], ['failed', true], ['pc', false], ['github', false], ['done', false]]) {
    assert.equal(canDiscard({status}), want, status);
  }
});

test('canDiscard: given now, a claim older than STALE_SECONDS (a worker that stopped) may be thrown away too', () => {
  assert.equal(STALE_SECONDS, 600); // as STALE_SECONDS in scripts/queue_items.py
  const claimed = (status, at) => ({...newItem({name: 'a', kind: 'new', grayscale: false, now: NOW - 3_600_000}), status, by: status, claimed_at: at});
  for (const status of ['pc', 'github']) {
    assert.equal(canDiscard(claimed(status, iso(NOW - 600_000)), NOW), false, `${status}: exactly 10 minutes is not stale yet`);
    assert.equal(canDiscard(claimed(status, iso(NOW - 601_000)), NOW), true, `${status}: over 10 minutes`);
    assert.equal(canDiscard(claimed(status, null), NOW), true, `${status}: a claim without a time counts as stale, as in Python`);
    assert.equal(canDiscard(claimed(status, 'yesterday'), NOW), true, `${status}: an unreadable claim time cannot keep it forever`);
    assert.equal(canDiscard(claimed(status, iso(NOW - 3_600_000))), false, `${status}: without now, only waiting and failed`);
  }
  assert.equal(canDiscard({status: 'done', claimed_at: null}, NOW), false);
  assert.equal(canDiscard({status: 'waiting'}, NOW), true);
});

test('hint: an upload waiting over 15 minutes, or a claim gone stale, says the AI is taking long', () => {
  assert.equal(SLOW_SECONDS, 900);
  assert.equal(SLOW_HINT, 'AI가 오래 걸리고 있어요');
  const waiting = (ago) => newItem({name: 'a', kind: 'new', grayscale: false, now: NOW - ago});
  assert.equal(hint(waiting(900_000), NOW), '', 'exactly 15 minutes is not long yet');
  assert.equal(hint(waiting(901_000), NOW), SLOW_HINT);
  assert.equal(hint(waiting(60_000), NOW), '');
  const claimed = (at) => ({...waiting(3_600_000), status: 'pc', by: 'pc', claimed_at: iso(at)});
  assert.equal(hint(claimed(NOW - 300_000), NOW), '', 'an AI at work, not stale: no hint however old the upload');
  assert.equal(hint(claimed(NOW - 601_000), NOW), SLOW_HINT, 'a claim gone stale (버리기 shows for it)');
  assert.equal(hint({...waiting(3_600_000), status: 'failed', error: 'x'}, NOW), '', 'a failure shows its own error');
  assert.equal(hint({...waiting(0), uploaded_at: 'garbage'}, NOW), '');
});
