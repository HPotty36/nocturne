// The site build status line of the admin page, as a small state machine: when to read the newest run of the
// site workflow again and what to show. DOM-free; the clock, the timers, the read and the display are passed in.
//
// GitHub's run list says nothing about which commit a run builds, and a push takes a few seconds to start one.
// So after a commit that changes the site, the newest run seen before it is "the old build": until a run with
// another URL shows up (at most NEW_RUN_MS), the line says the site is being updated instead of "반영됨".

export const STATUS_MS = 10_000; // read again this often while a build runs (or a change waits for its build)
export const FIRST_RETRY_MS = 3_000; // a failed first read is tried once more after this long
export const NEW_RUN_MS = 120_000; // after a change, the old build counts as "not started yet" for at most this long

const FIRST = Symbol('the first run seen'); // a change came before any run was seen: the first one seen is the old build

export class SiteStatus {
  #read;
  #show;
  #now;
  #setTimer;
  #clearTimer;
  #timer = null;
  #seq = 0; // number of the newest read; an older one's answer is not shown
  #base = 0; // reads numbered up to this one were started before the last stop()
  #seen = false; // a read has answered since the start
  #lastUrl = null; // url of the newest run seen
  #waitFor; // after a change: url of the build from before it (or FIRST)
  #until = 0; // ... and until when that build still counts as "the new one has not started"
  #retried = false;
  #state = null;

  /**
   * read(): {status, conclusion, url} of the newest run, or null (GitHub.latestRun). show(state, url): state is
   * 'busy' ("사이트 반영 중…"), 'done' ("반영됨"), 'failed' ("반영 실패", url links to the run) or null (hide).
   */
  constructor({ read, show, now = Date.now, setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (timer) => clearTimeout(timer) }) {
    this.#read = read;
    this.#show = show;
    this.#now = now;
    this.#setTimer = setTimer;
    this.#clearTimer = clearTimer;
  }

  /** Read and show the status; `changed` right after a commit that changes the site. */
  async refresh(changed = false) {
    this.#cancel();
    const seq = ++this.#seq;
    if (changed) {
      this.#waitFor = this.#seen ? this.#lastUrl : FIRST;
      this.#until = this.#now() + NEW_RUN_MS;
      this.#display('busy');
    }
    let run;
    try {
      run = await this.#read();
    } catch {
      if (seq !== this.#seq) return;
      if (!this.#seen && !this.#retried) {
        this.#retried = true;
        this.#later(FIRST_RETRY_MS);
      } else if (this.#state === 'busy') {
        this.#later(STATUS_MS); // keep what is shown and look again
      }
      return;
    }
    const url = run?.url ?? null;
    if (seq !== this.#seq) {
      // too late to be shown; but if nothing was known yet, it still tells which build came before the change
      if (!this.#seen && seq > this.#base) this.#learn(url);
      return;
    }
    this.#learn(url);
    const old = this.#now() < this.#until && url === this.#waitFor; // the change has no run of its own yet
    if (!old) {
      this.#until = 0;
      this.#waitFor = undefined;
    }
    if (!run && !old) {
      this.#display(null); // the workflow has never run
      return;
    }
    // GitHub reports queued, in_progress, waiting, pending or requested until a run is completed
    const running = old || run.status !== 'completed';
    this.#display(running ? 'busy' : run.conclusion === 'success' ? 'done' : 'failed', url);
    if (running) this.#later(STATUS_MS);
  }

  /** Forget everything (the page was locked): hide the line, stop the timer, ignore reads still on their way. */
  stop() {
    this.#cancel();
    this.#base = ++this.#seq;
    this.#seen = false;
    this.#lastUrl = null;
    this.#waitFor = undefined;
    this.#until = 0;
    this.#retried = false;
    this.#display(null);
  }

  #learn(url) {
    this.#seen = true;
    this.#lastUrl = url;
    if (this.#waitFor === FIRST) this.#waitFor = url;
  }

  #display(state, url = null) {
    this.#state = state;
    this.#show(state, url);
  }

  #later(ms) {
    this.#timer = this.#setTimer(() => this.refresh(), ms);
  }

  #cancel() {
    if (this.#timer !== null) this.#clearTimer(this.#timer);
    this.#timer = null;
  }
}
