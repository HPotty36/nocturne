"""scripts/cover.py: the Draft step that has Claude pick the cover (and where to crop it) when the photos on show change."""
import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

import helpers  # puts scripts/ on sys.path
from helpers import SITE, make_jpeg
import cover
import curator
import photolib
from fake_github import FakeGitHub
from github_api import GitHub

R = "HPotty36/nocturne"


def with_pick(data, **changes):
    """A copy of `data` with its top-level keys changed; a value of None removes the key."""
    data = json.loads(json.dumps(data))
    for key, value in changes.items():
        if value is None: data.pop(key, None)
        else: data[key] = value
    return data


class FakePicker:
    """Stands in for curator.claude_pick_cover: records what it was shown and answers `answer` (or raises `fail`)."""

    def __init__(self, answer=None, fail=None, during=None):
        self.answer, self.fail, self.during, self.calls = answer, fail, during, []

    def __call__(self, candidates, current, *, focus_only=False, model=None):
        self.calls.append({"candidates": candidates, "current": current, "focus_only": focus_only, "model": model})
        if self.during:
            self.during()
        if self.fail:
            raise curator.CuratorError(self.fail)
        return dict(self.answer)


class PlanTests(unittest.TestCase):
    def test_plan(self):
        fresh = photolib.fingerprint(SITE)
        self.assertIsNone(cover.plan(with_pick(SITE, rooms=[])))
        self.assertEqual(cover.plan(with_pick(SITE, cover_pick=None)), "pick")  # before this feature, or handed back
        self.assertEqual(cover.plan(with_pick(SITE, cover_pick={"by": "ai", "model": "m", "seen": "00000000"})), "pick")
        self.assertIsNone(cover.plan(with_pick(SITE, cover_pick={"by": "ai", "model": "m", "seen": fresh})))
        self.assertEqual(cover.plan(with_pick(SITE, cover_pick={"by": "person"}, cover_focus=None)), "focus")
        self.assertIsNone(cover.plan(with_pick(SITE, cover_pick={"by": "person"})))
        self.assertEqual(cover.plan(with_pick(SITE, cover_pick="odd")), "pick")

    def test_plan_picks_when_the_cover_is_gone(self):
        self.assertEqual(cover.plan(with_pick(SITE, cover="gone")), "pick")
        self.assertEqual(cover.plan(with_pick(SITE, cover=None)), "pick")
        self.assertEqual(cover.plan(with_pick(SITE, cover="gone", cover_pick={"by": "person"})), "pick")  # a pin on nothing is void

    def test_candidates_cap_keeps_the_cover(self):
        photos = [{"file": f"p{i:03}", "title": "t", "alt": "a"} for i in range(120)]
        data = {"site": {}, "cover": "p110", "rooms": [{"id": "r", "name": "방", "photos": photos}]}
        got = [p["file"] for _, p in cover.candidates(data)]
        self.assertEqual(len(got), 100)
        self.assertIn("p110", got)
        self.assertEqual(got, sorted(got))  # page order kept
        self.assertEqual(got[:99], [f"p{i:03}" for i in range(99)])
        self.assertEqual([p["file"] for _, p in cover.candidates(SITE)], ["a1", "a2", "n1"])

    def test_small_copy(self):
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, True)
        raw = make_jpeg(tmp / "big.jpg", (1000, 750), camera="TestCam", gps=True).read_bytes()
        small = cover.small_copy(raw)
        with Image.open(io.BytesIO(small)) as im:
            self.assertEqual((im.format, im.size), ("JPEG", (512, 384)))
            self.assertEqual(len(im.getexif()), 0)
            self.assertNotIn("icc_profile", im.info)
        self.assertNotIn(b"TestCam", small)


class RepoCase(unittest.TestCase):
    """A fake GitHub holding SITE (with no cover_pick or cover_focus yet) and a 1000x750 thumbnail per photo."""

    def setUp(self):
        self.fake = FakeGitHub(); self.fake.start(); self.addCleanup(self.fake.stop)
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        self.put(with_pick(SITE, cover_pick=None, cover_focus=None))
        thumbs = {f"photos/thumb/{p['file']}.jpg": make_jpeg(self.tmp / "t.jpg", (1000, 750)).read_bytes()
                  for room in SITE["rooms"] for p in room["photos"]}
        self.fake.put_files(R, thumbs)
        self.gh = GitHub("test-token", api=self.fake.url)

    def put(self, data):
        self.fake.put_files(R, {"src/photos.json": photolib.dumps_data(data).encode("utf-8")})

    def photos(self):
        return json.loads(self.fake.read_file(R, "src/photos.json"))

    def message(self):
        sha = self.gh._json("GET", f"/repos/{R}/git/ref/heads/main")["object"]["sha"]
        return self.gh._json("GET", f"/repos/{R}/git/commits/{sha}")["message"]


class RunTests(RepoCase):
    def test_run_picks_and_commits(self):
        picker = FakePicker({"file": "n1", "focus": [0.2, 0.7], "model": "claude-opus-5-5"})
        before = self.fake.commits(R)
        got = cover.run(self.gh, picker, model="claude-opus-5-5")
        self.assertEqual(got, {"result": "picked", "changed": True, "file": "n1"})
        self.assertEqual(self.fake.commits(R), before + 1)
        self.assertEqual(self.message(), "대표 사진: 공중전화 (AI)")
        data = self.photos()
        self.assertEqual((data["cover"], data["cover_focus"], data["cover_pick"]),
                         ("n1", [0.2, 0.7], {"by": "ai", "model": "claude-opus-5-5", "seen": photolib.fingerprint(SITE)}))
        (call,) = picker.calls
        self.assertEqual((call["current"], call["focus_only"], call["model"]), ("a1", False, "claude-opus-5-5"))
        self.assertEqual([(c["file"], c["title"], c["room"]) for c in call["candidates"]],
                         [("a1", "스카이라인", "강"), ("a2", "다리 아래", "강"), ("n1", "공중전화", "밤")])
        with Image.open(io.BytesIO(call["candidates"][0]["image"])) as im:
            self.assertEqual(im.size, (512, 384))
        self.assertEqual(cover.run(self.gh, picker), {"result": None, "changed": False, "file": None})  # nothing left to do
        self.assertEqual(len(picker.calls), 1)

    def test_run_same_cover_same_focus_is_not_a_change(self):
        self.put(with_pick(SITE, cover_pick={"by": "ai", "model": "m", "seen": "00000000"}))
        got = cover.run(self.gh, FakePicker({"file": "a1", "focus": [0.42, 0.5], "model": "claude-opus-5-5"}))
        self.assertEqual(got, {"result": "picked", "changed": False, "file": "a1"})
        self.assertEqual(self.photos()["cover_pick"]["seen"], photolib.fingerprint(SITE))  # recorded, so it is not asked again

    def test_run_focus_only(self):
        self.put(with_pick(SITE, cover="a2", cover_pick={"by": "person"}, cover_focus=None))
        picker = FakePicker({"file": "a2", "focus": [0.1, 0], "model": "claude-opus-5-5"})
        got = cover.run(self.gh, picker)
        self.assertEqual(got, {"result": "focused", "changed": True, "file": "a2"})
        (call,) = picker.calls
        self.assertEqual(([c["file"] for c in call["candidates"]], call["focus_only"]), (["a2"], True))
        data = self.photos()
        self.assertEqual((data["cover"], data["cover_focus"], data["cover_pick"]), ("a2", [0.1, 0], {"by": "person"}))
        self.assertEqual(self.message(), "대표 사진 중심: 다리 아래 (AI)")

    def test_a_person_who_pinned_meanwhile_wins(self):
        pinned = with_pick(SITE, cover="a2", cover_pick={"by": "person"}, cover_focus=None)
        picker = FakePicker({"file": "n1", "focus": [0.2, 0.7], "model": "m"}, during=lambda: self.put(pinned))
        got = cover.run(self.gh, picker)
        self.assertEqual(got, {"result": "skipped", "changed": False, "file": "n1"})
        self.assertEqual(self.photos(), pinned)

    def test_run_skips_a_photo_taken_down_meanwhile(self):
        gone = with_pick(SITE, cover_pick=None, cover_focus=None)
        gone["rooms"][1]["photos"] = []
        picker = FakePicker({"file": "n1", "focus": [0.2, 0.7], "model": "m"}, during=lambda: self.put(gone))
        self.assertEqual(cover.run(self.gh, picker)["result"], "skipped")
        self.assertEqual(self.photos(), gone)


class MainTests(RepoCase):
    def main(self, key="sk-test", picker=None):
        out = self.tmp / "output.txt"
        env = {"GITHUB_TOKEN": "test-token", "ANTHROPIC_API_KEY": key, "GITHUB_OUTPUT": str(out)}
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, env), mock.patch.object(cover, "GitHub", lambda token: GitHub(token, api=self.fake.url)), \
                mock.patch.object(curator, "claude_pick_cover", picker or FakePicker({"file": "n1", "focus": [0.2, 0.7], "model": "m"})), \
                contextlib.redirect_stdout(stdout):
            code = cover.main([])
        return code, stdout.getvalue(), out.read_text(encoding="utf-8") if out.exists() else ""

    def test_main_picks(self):
        code, stdout, output = self.main()
        self.assertEqual((code, output), (0, "changed=1\n"))
        self.assertIn("picked: n1", stdout)

    def test_main_without_key(self):
        before = self.fake.commits(R)
        code, stdout, output = self.main(key="")
        self.assertEqual((code, output), (0, "changed=0\n"))
        self.assertIn("Claude API 키가 없어서 대표 사진을 고르지 않아요", stdout)
        self.assertEqual(self.fake.commits(R), before)

    def test_main_warns_on_claude_failure(self):
        code, stdout, output = self.main(picker=FakePicker(fail="Claude API 요청이 너무 많아요"))
        self.assertEqual((code, output), (0, "changed=0\n"))
        self.assertIn("::warning title=대표 사진::Claude API 요청이 너무 많아요", stdout)
        self.assertNotIn("cover_pick", self.fake.read_file(R, "src/photos.json").decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
