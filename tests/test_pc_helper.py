import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import helpers  # puts scripts/ on sys.path
from helpers import FakeAI
import curator
import github_api
import pc_helper
import photolib
from github_api import GitHubError
from pc_helper import Helper
from test_worker import NOW, R, FakeRepo


class HelperTests(FakeRepo, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.clock = [NOW]
        self.h = Helper(self.w, status_fn=lambda: "ok", now_fn=lambda: self.clock[0])

    def make_target(self, file):
        """Give a photo text that the small model wrote, so it is due for an upgrade."""
        d = self.photos(); _, p = photolib.find_photo(d, file)
        p["ai"] = {"model": "gemma4:e4b-it-qat", "fields": ["title", "alt"]}
        self.fake.put_file(R, "src/photos.json", photolib.dumps_data(d).encode("utf-8"))

    def test_off_does_nothing(self):
        self.put_item("a")
        self.h.status_fn = lambda: "off"
        self.assertIsNone(self.h.tick())
        self.assertEqual(self.fake.requests, [])
    def test_queue_before_upgrade(self):
        self.put_item("a")
        self.assertEqual(self.h.tick(), "published:a")
        self.assertEqual(self.h.tick(), "upgraded:a2")
        self.assertIsNone(self.h.tick())
    def test_upgrade_every_thirty_minutes(self):
        self.assertEqual(self.h.tick(), "upgraded:a2")
        self.assertIsNone(self.h.tick())
        self.make_target("n1")  # due now, but the next look is not
        calls = self.w.drafter.calls
        self.clock[0] = NOW + timedelta(minutes=29)
        self.assertIsNone(self.h.tick())
        self.assertEqual(self.w.drafter.calls, calls)
        self.clock[0] = NOW + timedelta(minutes=31)
        self.assertEqual(self.h.tick(), "upgraded:n1")
        self.assertEqual(self.w.drafter.calls, calls + 1)
    def test_new_upload_goes_before_the_rest_of_an_upgrade_round(self):
        self.make_target("n1")
        self.assertEqual(self.h.tick(), "upgraded:a2")
        self.put_item("b")
        self.assertEqual(self.h.tick(), "published:b")
        self.assertEqual(self.h.tick(), "upgraded:n1")
        self.assertIsNone(self.h.tick())
    def test_failed_upgrade_is_tried_again_at_the_next_look(self):
        self.w.drafter = FakeAI(fail=True)
        self.assertEqual(self.h.tick(), "failed:a2")
        self.assertIsNone(self.h.tick())
        self.w.drafter = FakeAI()
        self.clock[0] = NOW + timedelta(minutes=10)
        self.assertIsNone(self.h.tick())  # skipped until the next look
        self.clock[0] = NOW + timedelta(minutes=30)
        self.assertEqual(self.h.tick(), "upgraded:a2")
    def test_failed_queue_item(self):
        # the PC hands an item its AI gave up on back to waiting, for GitHub, and leaves it alone meanwhile
        self.put_item("a"); self.w.drafter = FakeAI(fail=True)
        self.assertEqual(self.h.tick(), "released:a")
        self.assertEqual(self.h.tick(), "failed:a2")  # the next tick goes on to upgrades, not to a again


class Ticks:
    """A Helper stand-in whose tick returns (or raises) the given values in turn."""
    def __init__(self, *results):
        self.results = list(results)
    def tick(self):
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class RunTests(unittest.TestCase):
    def run_ticks(self, *results):
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == len(results):
                raise KeyboardInterrupt
        with self.assertLogs("nocturne", "INFO") as logs, self.assertRaises(KeyboardInterrupt):
            pc_helper.run(Ticks(*results), sleep=sleep)
        self.assertEqual(sleeps, [pc_helper.POLL_SECONDS] * len(results))
        return [record.getMessage() for record in logs.records]

    def test_one_line_per_result_and_error(self):
        lines = self.run_ticks("published:a", None, GitHubError("GitHub 502:\nbad gateway", 502), "upgraded:a2")
        self.assertEqual(lines[0::2], ["published:a", "upgraded:a2"])
        self.assertRegex(lines[1], r"^오류 GitHubError: GitHub 502: bad gateway \(test_pc_helper\.py:\d+\)$")
    def test_the_same_error_again_is_not_logged_again(self):
        drop = lambda: GitHubError("GitHub에 연결하지 못했어요", 0)
        lines = self.run_ticks(drop(), drop(), drop(), None, drop(), "published:a")
        self.assertEqual([line.split(" (")[0] for line in lines],
                         ["오류 GitHubError: GitHub에 연결하지 못했어요", "오류 GitHubError: GitHub에 연결하지 못했어요", "published:a"])


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        patcher = mock.patch.dict(os.environ, {"APPDATA": str(self.tmp)})
        patcher.start(); self.addCleanup(patcher.stop)

    def test_paths(self):
        self.assertEqual(pc_helper.config_path(), self.tmp / "nocturne" / "helper.json")
        self.assertEqual(pc_helper.log_path(), self.tmp / "nocturne" / "helper.log")
    def test_setup_saves_the_typed_token(self):
        with mock.patch("getpass.getpass", return_value=" github_pat_x \n") as ask:
            pc_helper.setup(pc_helper.config_path())
        ask.assert_called_once_with("GitHub 토큰: ")
        self.assertEqual(json.loads(pc_helper.config_path().read_text(encoding="utf-8")), {"token": "github_pat_x"})
    def test_setup_refuses_an_empty_token(self):
        with mock.patch("getpass.getpass", return_value="  "), self.assertRaises(SystemExit):
            pc_helper.setup(pc_helper.config_path())
        self.assertFalse(pc_helper.config_path().exists())
    def test_main_runs_a_pc_worker_with_the_saved_token(self):
        pc_helper.config_path().parent.mkdir()
        pc_helper.config_path().write_text('{"token": "github_pat_x"}', encoding="utf-8")
        seen, helper_author = [], []
        def run(helper):
            seen.append((helper.worker.gh.token, helper.worker.by, helper.worker.model, helper.worker.pc_model,
                         helper.worker.workdir.is_dir(), helper.status_fn))
            helper_author.append(helper.worker.gh.author)
            raise KeyboardInterrupt
        with mock.patch.dict(os.environ, {"NOCTURNE_MODEL": "gemma4:12b-it-qat"}), mock.patch.object(pc_helper, "run", run), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(pc_helper.main([]), 0)
        self.assertEqual(seen, [("github_pat_x", "pc", "gemma4:12b-it-qat", curator.DEFAULT_MODEL, True, curator.status)])
        self.assertEqual(helper_author[0], github_api.AUTHOR)  # its commits are by the noreply identity
        log = pc_helper.log_path().read_text(encoding="utf-8")
        self.assertEqual(len(log.splitlines()), 2)  # started, stopped
        self.assertNotIn("github_pat_x", log)
    def test_main_without_a_token(self):
        with contextlib.redirect_stdout(io.StringIO()) as printed:
            self.assertEqual(pc_helper.main([]), 1)
        self.assertIn("--setup", printed.getvalue())
        self.assertIn("--setup", pc_helper.log_path().read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
