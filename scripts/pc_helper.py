"""PC helper: while Ollama runs on this PC, publish queue uploads with the 12B model and upgrade small-model text.

    py scripts\\pc_helper.py --setup   type the GitHub token (repository nocturne, Contents read and write);
                                      it is saved to %APPDATA%\\nocturne\\helper.json
    py scripts\\pc_helper.py           runs until stopped; pyw runs it without a console window

Every POLL_SECONDS one tick: nothing while Ollama is off; else the oldest queue item the PC may take; else, once
every UPGRADE_EVERY seconds, a round of upgrades, one photo per tick. Each result and each error is one line in
%APPDATA%\\nocturne\\helper.log.
"""
import argparse
import contextlib
import getpass
import json
import logging
import os
import sys
import tempfile
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import curator
from github_api import GitHub
from worker import Worker

POLL_SECONDS = 20
UPGRADE_EVERY = 1800

log = logging.getLogger("nocturne.helper")


def _home():
    return Path(os.environ["APPDATA"]) / "nocturne"


def config_path():
    return _home() / "helper.json"


def log_path():
    return _home() / "helper.log"


def setup(path):
    """Ask for the GitHub token in the terminal (it is not shown) and save it to `path`."""
    token = getpass.getpass("GitHub 토큰: ").strip()
    if not token:
        raise SystemExit("토큰이 비어 있어서 저장하지 않았어요.")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"token": token}), encoding="utf-8")


class Helper:
    def __init__(self, worker, *, status_fn=curator.status, now_fn=lambda: datetime.now(timezone.utc)):
        self.worker, self.status_fn, self.now_fn = worker, status_fn, now_fn
        self.next_look = None   # when to look for photos to upgrade again; None: at the first tick with an empty queue
        self.upgrading = False  # a round of upgrades is under way

    def tick(self):
        """One step: "<result>:<name or file>" when something was done, else None."""
        if self.status_fn() != "ok":
            return None
        names = self.worker.takeable()
        if names:
            return f"{self.worker.process(names[0])}:{names[0]}"
        now = self.now_fn()
        if self.next_look is None or now >= self.next_look:
            self.worker.skip.clear()
            self.upgrading = True
            self.next_look = now + timedelta(seconds=UPGRADE_EVERY)
        if not self.upgrading:
            return None
        done = self.worker.upgrade_one()
        if done is None:
            self.upgrading = False
            return None
        return f"{done[0]}:{done[1]}"


def _one_line(err):
    """An exception as one log line: its type, its message, and the file and line it was raised at."""
    text = f"{type(err).__name__}: {' '.join(str(err).split())}"
    frames = traceback.extract_tb(err.__traceback__)
    return f"{text} ({Path(frames[-1].filename).name}:{frames[-1].lineno})" if frames else text


def run(helper, *, sleep=time.sleep):
    """Tick every POLL_SECONDS until interrupted, logging what each tick did.

    An error does not stop the helper (GitHub or Ollama may be back by the next tick); the same error again in a
    row is not logged again.
    """
    last_error = None
    while True:
        try:
            result = helper.tick()
        except Exception as err:
            line = _one_line(err)
            if line != last_error:
                log.error("오류 %s", line)
            last_error = line
        else:
            last_error = None
            if result:
                log.info("%s", result)
        sleep(POLL_SECONDS)


@contextlib.contextmanager
def _logging_to(path):
    """Log the nocturne.* loggers (helper and worker) to `path`, one line per record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    logger = logging.getLogger("nocturne")
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        yield
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
        handler.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Nocturne PC 도우미: 대기열의 사진을 이 PC의 모델로 게시하고, "
                                                 "작은 모델이 쓴 설명을 다듬어요.")
    parser.add_argument("--setup", action="store_true", help="GitHub 토큰을 입력해 저장")
    args = parser.parse_args(argv)
    if args.setup:
        setup(config_path())
        print(f"저장했어요: {config_path()}")
        return 0
    with _logging_to(log_path()):
        try:
            token = json.loads(config_path().read_text(encoding="utf-8"))["token"]
        except (OSError, ValueError, KeyError, TypeError):
            message = "토큰이 없어요. 먼저 py scripts\\pc_helper.py --setup 으로 저장하세요."
            log.error("%s", message)
            print(message)  # under pyw there is no console and this prints nothing; the log has it
            return 1
        model = curator.model_name()
        print(f"Nocturne PC 도우미를 시작해요 (모델 {model}). 기록: {log_path()}")
        log.info("시작 (모델 %s)", model)
        with tempfile.TemporaryDirectory(prefix="nocturne-", ignore_cleanup_errors=True) as tmp:
            helper = Helper(Worker(GitHub(token), by="pc", model=model, workdir=tmp))
            try:
                run(helper)
            except KeyboardInterrupt:
                log.info("멈춤")
    return 0


if __name__ == "__main__":
    sys.exit(main())
