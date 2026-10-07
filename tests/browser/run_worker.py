"""Run the queue worker once against the local fake GitHub, for the browser check of the admin page.

Usage:  py tests\\browser\\run_worker.py http://127.0.0.1:8790 --by pc       (or --by github)

Takes every queue item this worker may take now and processes it, as the PC helper (--by pc, model
gemma4:12b-it-qat) or GitHub Actions (--by github, gemma4:e4b-it-qat) would, but with the tests' FakeAI instead
of Ollama: each photo is published with the title "초안" and the description "초안 설명" in the room "night".
For --by github the clock runs 120 seconds ahead, so an upload counts as older than the 60 seconds GitHub waits.
Prints one line per item ("published: <name>", "failed: <name>", "skipped: <name>").
Only a local address is accepted: the token is the fake server's "test-token".
"""
import argparse
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # tests/, for helpers (which adds scripts/)

from helpers import FakeAI  # noqa: E402
from github_api import GitHub  # noqa: E402
from worker import Worker  # noqa: E402

MODELS = {"pc": "gemma4:12b-it-qat", "github": "gemma4:e4b-it-qat"}
LOCAL = ("http://127.0.0.1:", "http://localhost:")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("url", help="the fake GitHub, e.g. http://127.0.0.1:8790")
    parser.add_argument("--by", choices=tuple(MODELS), required=True)
    args = parser.parse_args(argv)
    if not args.url.startswith(LOCAL):
        parser.error("only a local fake GitHub (http://127.0.0.1:<port>) can be used")
    ahead = timedelta(seconds=120 if args.by == "github" else 0)
    with tempfile.TemporaryDirectory(prefix="nocturne-worker-") as workdir:
        worker = Worker(GitHub("test-token", api=args.url), by=args.by, model=MODELS[args.by], workdir=workdir,
                        drafter=FakeAI(), now_fn=lambda: datetime.now(timezone.utc) + ahead)
        names = worker.takeable()
        if not names:
            print("nothing to take")
        for name in names:
            print(f"{worker.process(name)}: {name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
