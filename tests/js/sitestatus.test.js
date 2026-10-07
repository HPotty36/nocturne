import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SiteStatus, STATUS_MS, FIRST_RETRY_MS, NEW_RUN_MS } from '../../admin/lib/sitestatus.js';

const URL_A = 'https://github.com/HPotty36/nocturne/actions/runs/1';
const URL_B = 'https://github.com/HPotty36/nocturne/actions/runs/2';
const run = (url, status, conclusion = null) => ({ status, conclusion, url });
const done = (url) => run(url, 'completed', 'success');

/** A SiteStatus on a fake clock: reads are answered from a script, timers run when the clock is advanced. */
function harness() {
  let clock = 1_000_000;
  const timers = [];
  const shown = [];
  const answers = [];
  let reads = 0;
  const status = new SiteStatus({
    read: () => {
      reads++;
      const next = answers.shift();
      if (!next) throw new Error('unexpected read');
      return next();
    },
    show: (state, url) => shown.push([state, url]),
    now: () => clock,
    setTimer: (fn, ms) => {
      const timer = { fn, at: clock + ms };
      timers.push(timer);
      return timer;
    },
    clearTimer: (timer) => {
      const i = timers.indexOf(timer);
      if (i >= 0) timers.splice(i, 1);
    },
  });
  return {
    status,
    shown,
    reply: (answer) => answers.push(async () => answer),
    fail: () => answers.push(async () => { throw new Error('offline'); }),
    hold() {
      let resolve;
      const promise = new Promise((done_) => { resolve = done_; });
      answers.push(() => promise);
      return resolve;
    },
    last: () => shown.at(-1),
    waits: () => timers.map((t) => t.at - clock),
    reads: () => reads,
    async advance(ms) {
      clock += ms;
      for (const timer of timers.filter((t) => t.at <= clock)) {
        timers.splice(timers.indexOf(timer), 1);
        await timer.fn();
      }
    },
  };
}

test('a finished build shows 반영됨 and nothing is read again', async () => {
  const h = harness();
  h.reply(done(URL_A));
  await h.status.refresh();
  assert.deepEqual(h.last(), ['done', URL_A]);
  assert.deepEqual(h.waits(), []);
});

test('a build that has not finished is read again every 10 s until it completes', async () => {
  const h = harness();
  for (const state of ['queued', 'in_progress', 'waiting', 'pending', 'requested']) {
    h.reply(run(URL_A, state));
  }
  h.reply(done(URL_A));
  await h.status.refresh();
  assert.deepEqual(h.last(), ['busy', URL_A]);
  for (let i = 0; i < 4; i++) {
    assert.deepEqual(h.waits(), [STATUS_MS]);
    await h.advance(STATUS_MS);
    assert.deepEqual(h.last(), ['busy', URL_A]);
  }
  await h.advance(STATUS_MS);
  assert.deepEqual(h.last(), ['done', URL_A]);
  assert.deepEqual(h.waits(), []);
  assert.equal(STATUS_MS, 10_000);
});

test('a failed build shows 반영 실패 with its link; no run at all hides the line', async () => {
  const h = harness();
  h.reply(run(URL_A, 'completed', 'failure'));
  await h.status.refresh();
  assert.deepEqual(h.last(), ['failed', URL_A]);
  h.reply(null);
  await h.status.refresh();
  assert.deepEqual(h.last(), [null, null]);
});

test('after a change, the build seen before it does not pass for the new one', async () => {
  const h = harness();
  h.reply(done(URL_A));
  await h.status.refresh();
  h.reply(done(URL_A)); // GitHub has not started the new run yet
  const reading = h.status.refresh(true);
  assert.deepEqual(h.last(), ['busy', null]); // shown at once, before the read answers
  await reading;
  assert.deepEqual(h.last(), ['busy', URL_A]);
  h.reply(run(URL_B, 'in_progress'));
  await h.advance(STATUS_MS);
  assert.deepEqual(h.last(), ['busy', URL_B]);
  h.reply(done(URL_B));
  await h.advance(STATUS_MS);
  assert.deepEqual(h.last(), ['done', URL_B]);
  assert.deepEqual(h.waits(), []);
});

test('after a change with no run seen yet, the first run seen counts as the old build', async () => {
  const h = harness();
  h.fail(); // the first read at unlock failed
  await h.status.refresh();
  h.reply(done(URL_A));
  await h.status.refresh(true);
  assert.deepEqual(h.last(), ['busy', URL_A]);
  h.reply(done(URL_A));
  await h.advance(STATUS_MS);
  assert.deepEqual(h.last(), ['busy', URL_A]);
  h.reply(done(URL_B));
  await h.advance(STATUS_MS);
  assert.deepEqual(h.last(), ['done', URL_B]);
});

test('a read from before the change that answers late still tells which build is the old one', async () => {
  const h = harness();
  const first = h.hold(); // the read at unlock is slow
  const unlock = h.status.refresh();
  const second = h.hold();
  const change = h.status.refresh(true);
  first(done(URL_A)); // answers after the change: not shown, but it was the build before the change
  await unlock;
  assert.deepEqual(h.last(), ['busy', null]);
  second(done(URL_B)); // the new build already finished
  await change;
  assert.deepEqual(h.last(), ['done', URL_B]);
});

test('waiting for the new run ends after 2 minutes', async () => {
  const h = harness();
  h.reply(done(URL_A));
  await h.status.refresh();
  h.reply(done(URL_A));
  await h.status.refresh(true);
  let waited = 0;
  while (waited < NEW_RUN_MS) {
    assert.deepEqual(h.last(), ['busy', URL_A]);
    h.reply(done(URL_A));
    await h.advance(STATUS_MS);
    waited += STATUS_MS;
  }
  assert.deepEqual(h.last(), ['done', URL_A]);
  assert.deepEqual(h.waits(), []);
  assert.equal(NEW_RUN_MS, 120_000);
});

test('the first read is retried once when it fails, then left alone', async () => {
  const h = harness();
  h.fail();
  await h.status.refresh();
  assert.deepEqual(h.shown, []);
  assert.deepEqual(h.waits(), [FIRST_RETRY_MS]);
  h.fail();
  await h.advance(FIRST_RETRY_MS);
  assert.deepEqual(h.waits(), []);
  assert.equal(h.reads(), 2);
  assert.deepEqual(h.shown, []);
});

test('the retried first read can still show the status', async () => {
  const h = harness();
  h.fail();
  await h.status.refresh();
  h.reply(done(URL_A));
  await h.advance(FIRST_RETRY_MS);
  assert.deepEqual(h.last(), ['done', URL_A]);
});

test('a failed read while a build runs keeps what is shown and looks again', async () => {
  const h = harness();
  h.reply(run(URL_A, 'in_progress'));
  await h.status.refresh();
  h.fail();
  await h.advance(STATUS_MS);
  assert.deepEqual(h.last(), ['busy', URL_A]);
  assert.deepEqual(h.waits(), [STATUS_MS]);
});

test('an older read that answers after a newer one is not shown', async () => {
  const h = harness();
  h.reply(done(URL_A));
  await h.status.refresh();
  const slow = h.hold();
  const older = h.status.refresh();
  h.reply(done(URL_B));
  await h.status.refresh();
  slow(run(URL_A, 'in_progress'));
  await older;
  assert.deepEqual(h.last(), ['done', URL_B]);
  assert.deepEqual(h.waits(), []);
});

test('stop() hides the line, cancels the timer and ignores reads still on the way', async () => {
  const h = harness();
  h.reply(run(URL_A, 'in_progress'));
  await h.status.refresh();
  const slow = h.hold();
  const late = h.status.refresh();
  h.status.stop();
  assert.deepEqual(h.last(), [null, null]);
  assert.deepEqual(h.waits(), []);
  slow(done(URL_A));
  await late;
  assert.deepEqual(h.last(), [null, null]);
  // a new start knows nothing (not even what the late read said): a change treats the first run seen as the old build
  h.reply(done(URL_B));
  await h.status.refresh(true);
  assert.deepEqual(h.last(), ['busy', URL_B]);
});
