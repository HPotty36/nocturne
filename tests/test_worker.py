import contextlib
import functools
import io
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import helpers  # puts scripts/ on sys.path
from helpers import SITE, FakeAI, make_jpeg
import photolib
import queue_items
import worker
from fake_github import FakeGitHub
from github_api import GitHub, GitHubError, dump_json
from worker import Worker

R = "HPotty36/nocturne"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def recording(seen):
    """A drafter that notes what it was given (reading the image file while it exists) before FakeAI answers."""
    def drafter(image, data, grayscale, *, model=None):
        seen.append({"dir": Path(image).parent, "image": Path(image).read_bytes(), "grayscale": grayscale, "model": model,
                     "rooms": [room["id"] for room in data["rooms"]]})
        return FakeAI()(image, data, grayscale, model=model)
    return drafter


class FakeRepo:
    """setUp shared with test_pc_helper: a fake GitHub holding SITE (photos.json and its thumbnails) and a PC worker
    that drafts with FakeAI. The fake is filled in-process, so setUp sends no HTTP request."""

    def setUp(self):
        self.fake = FakeGitHub(); self.fake.start(); self.addCleanup(self.fake.stop)
        self.tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, self.tmp, True)
        files = {"src/photos.json": photolib.dumps_data(SITE).encode("utf-8")}
        for room in SITE["rooms"]:
            for photo in room["photos"]:
                files[f"photos/thumb/{photo['file']}.jpg"] = self.jpeg((300, 200))
        self.fake.put_files(R, files)
        self.full_bytes, self.thumb_bytes = {}, {}
        self.work = self.tmp / "work"
        self.work.mkdir()
        self.w = Worker(GitHub("test-token", api=self.fake.url), by="pc", model="gemma4:12b-it-qat", workdir=self.work,
                        drafter=FakeAI(), now_fn=lambda: NOW)

    def jpeg(self, size, color=(200, 40, 40)):
        return make_jpeg(self.tmp / "made.jpg", size, color).read_bytes()

    def put_item(self, name, **kw):
        """queue/<name>/ as the admin page uploads it: full.jpg, thumb.jpg and item.json in one commit."""
        item = {**queue_items.new_item(name, kind="new", grayscale=False, now=NOW), **kw}
        shade = sum(name.encode()) % 200
        self.full_bytes[name] = self.jpeg((600, 400), (shade, 90, 40))
        self.thumb_bytes[name] = self.jpeg((300, 200), (shade, 90, 40))
        self.fake.put_files(R, {f"queue/{name}/full.jpg": self.full_bytes[name], f"queue/{name}/thumb.jpg": self.thumb_bytes[name],
                                f"queue/{name}/item.json": dump_json(item).encode("utf-8")})

    def photos(self): return json.loads(self.fake.read_file(R, "src/photos.json"))

    def item(self, name): return json.loads(self.fake.read_file(R, f"queue/{name}/item.json"))

    def messages(self, count):
        """The last `count` commit messages on main, newest first."""
        gh = GitHub("test-token", api=self.fake.url)
        sha, out = gh._json("GET", f"/repos/{R}/git/ref/heads/main")["object"]["sha"], []
        for _ in range(count):
            commit = gh._json("GET", f"/repos/{R}/git/commits/{sha}")
            out.append(commit["message"])
            sha = commit["parents"][0]["sha"]
        return out

    def queue_files(self, name): return sorted(k for k in self.fake.files(R) if k.startswith(f"queue/{name}/"))

    def as_github(self):
        """Make the worker the GitHub fallback (an AI failure there is recorded as failed, not handed on)."""
        self.w.by, self.w.model = "github", "gemma4:e4b-it-qat"

    def put_old_item(self, name, **kw):
        """An upload old enough for GitHub to take (WAIT_SECONDS have passed)."""
        self.put_item(name, uploaded_at=queue_items.iso(NOW - timedelta(seconds=61)), **kw)


class WorkerTests(FakeRepo, unittest.TestCase):
    def test_publish_one_commit(self):
        self.put_item("a", date="2025.11.30")
        before = self.fake.commits(R)
        self.assertEqual(self.w.process("a"), "published")
        self.assertEqual(self.fake.commits(R), before + 2)                 # 맡음 + 게시
        _, p = photolib.find_photo(self.photos(), "a")
        self.assertEqual((p["title"], p["date"], p["ai"]), ("초안", "2025.11.30", {"model": "gemma4:12b-it-qat", "fields": ["title", "alt"], "confidence": 0.95}))
        self.assertEqual(self.fake.read_file(R, "photos/full/a.jpg"), self.full_bytes["a"])
        self.assertEqual([k for k in self.fake.files(R) if k.startswith("queue/a")], [])
    def test_low_confidence_still_published(self):
        self.put_item("a"); self.w.drafter = FakeAI(confidence=0.61)
        self.assertEqual(self.w.process("a"), "published")
        self.assertEqual(photolib.find_photo(self.photos(), "a")[1]["ai"]["confidence"], 0.61)
    def test_failure_recorded(self):
        self.as_github(); self.put_old_item("a"); self.w.drafter = FakeAI(fail=True)
        self.assertEqual(self.w.process("a"), "failed")
        it = json.loads(self.fake.read_file(R, "queue/a/item.json"))
        self.assertEqual(it["status"], "failed"); self.assertIn("Ollama가 꺼져", it["error"])
    def test_claim_conflict_skips(self):
        self.put_item("a")
        real = self.w.gh.put_json
        def racing(repo, path, data, **kw):
            self.fake.put_file(R, path, json.dumps({**data, "status": "github", "by": "github"}).encode())
            return real(repo, path, data, **kw)
        self.w.gh.put_json = racing
        self.assertEqual(self.w.process("a"), "skipped")
        self.assertEqual(self.w.drafter.calls, 0)
    def test_github_waits_sixty_seconds(self):
        self.put_item("a", uploaded_at=queue_items.iso(NOW - timedelta(seconds=30)))
        self.put_item("b", uploaded_at=queue_items.iso(NOW - timedelta(seconds=90)))
        self.w.by = "github"
        self.assertEqual(self.w.takeable(), ["b"])
    def test_redraft(self):
        self.fake.put_file(R, "queue/redraft-n1/item.json", json.dumps(queue_items.new_item("redraft-n1", kind="redraft", grayscale=False, now=NOW, file="n1")).encode())
        self.assertEqual(self.w.process("redraft-n1"), "published")
        _, p = photolib.find_photo(self.photos(), "n1")
        self.assertEqual((p["title"], p["ai"]["fields"]), ("초안", ["title", "alt"]))
    def test_upgrade(self):
        self.assertEqual(self.w.upgrade_one(), ("upgraded", "a2"))
        _, p = photolib.find_photo(self.photos(), "a2")
        self.assertEqual((p["title"], p["ai"]["model"], p["ai"]["confidence"]), ("초안", "gemma4:12b-it-qat", 0.61))
        self.assertEqual(photolib.find_photo(self.photos(), "a2")[0]["id"], "river")
        self.assertIsNone(self.w.upgrade_one())
    def test_upgrade_respects_edit_made_meanwhile(self):
        real = self.w.drafter
        def drafter_then_human_edit(*a, **kw):
            d = self.photos(); _, p = photolib.find_photo(d, "a2")
            p["title"] = "내가 고침"; p["ai"]["fields"] = ["alt"]
            self.fake.put_file(R, "src/photos.json", photolib.dumps_data(d).encode())
            return real(*a, **kw)
        self.w.drafter = drafter_then_human_edit
        self.assertEqual(self.w.upgrade_one(), ("upgraded", "a2"))
        _, p = photolib.find_photo(self.photos(), "a2")
        self.assertEqual((p["title"], p["alt"]), ("내가 고침", "초안 설명"))
    def test_upgrade_failure_skips_until_cleared(self):
        self.w.drafter = FakeAI(fail=True)
        self.assertEqual(self.w.upgrade_one(), ("failed", "a2"))
        self.assertIsNone(self.w.upgrade_one())


class PublishTests(FakeRepo, unittest.TestCase):
    """What a published photo looks like, and what the worker does when something changed underneath it."""

    def test_published_entry_files_and_messages(self):
        self.put_item("a", date="2025.11.30", camera="TestCam · f/1.6", grayscale=True)
        seen = []
        self.w.drafter = recording(seen)
        self.assertEqual(self.w.process("a"), "published")
        self.assertEqual(seen, [{"dir": self.work, "image": self.thumb_bytes["a"], "grayscale": True, "model": "gemma4:12b-it-qat",
                                 "rooms": ["river", "night"]}])
        text = self.fake.read_file(R, "src/photos.json").decode("utf-8")
        self.assertEqual(text, photolib.dumps_data(self.photos()))  # canonical form
        night = self.photos()["rooms"][1]["photos"]
        self.assertEqual(json.dumps(night[-1], ensure_ascii=False, separators=(",", ":")),
                         '{"file":"a","title":"초안","alt":"초안 설명","date":"2025.11.30","camera":"TestCam · f/1.6",'
                         '"ai":{"model":"gemma4:12b-it-qat","fields":["title","alt"],"confidence":0.95}}')
        self.assertEqual(self.fake.read_file(R, "photos/thumb/a.jpg"), self.thumb_bytes["a"])
        self.assertEqual(self.messages(2), ["사진 게시: 초안", "대기열: a PC가 맡음"])
        self.assertEqual(list(self.work.iterdir()), [])  # the temporary image is gone
    def test_claim_and_publish_commits_are_by_the_noreply_identity(self):
        self.put_item("a")
        self.assertEqual(self.w.process("a"), "published")
        me = {"name": "HPotty36", "email": "112685098+HPotty36@users.noreply.github.com"}
        self.assertEqual([(c["message"], c["author"], c["committer"]) for c in self.fake.last_commits(R, 2)],
                         [("사진 게시: 초안", me, me), ("대기열: a PC가 맡음", me, me)])
    def test_without_date_camera_or_confidence(self):
        self.put_item("a"); self.w.drafter = FakeAI(confidence=None)
        self.assertEqual(self.w.process("a"), "published")
        self.assertEqual(photolib.find_photo(self.photos(), "a")[1],
                         {"file": "a", "title": "초안", "alt": "초안 설명", "ai": {"model": "gemma4:12b-it-qat", "fields": ["title", "alt"]}})
    def test_failure_message_and_files_kept(self):
        self.as_github(); self.put_old_item("a"); self.w.drafter = FakeAI(fail=True)
        self.assertEqual(self.w.process("a"), "failed")
        self.assertEqual(self.messages(2), ["대기열: a AI 실패", "대기열: a GitHub가 맡음"])
        self.assertEqual((self.item("a")["by"], self.item("a")["claimed_at"]), ("github", "2026-10-06T12:00:00Z"))
        self.assertEqual(self.queue_files("a"), ["queue/a/full.jpg", "queue/a/item.json", "queue/a/thumb.jpg"])
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertEqual(self.w.process("a"), "skipped")  # a failed item waits for a person
    def test_name_already_shown(self):
        self.put_item("a1")  # a1 is on show in the river room
        before, data = self.fake.commits(R), self.fake.read_file(R, "src/photos.json")
        self.assertEqual(self.w.process("a1"), "failed")
        self.assertEqual((self.item("a1")["status"], self.item("a1")["error"]), ("failed", "같은 이름의 사진이 이미 있어요"))
        self.assertEqual(self.fake.commits(R), before + 2)  # 맡음 + 실패, nothing published
        self.assertEqual(self.fake.read_file(R, "src/photos.json"), data)
        self.assertIsNone(self.fake.read_file(R, "photos/full/a1.jpg"))
        self.assertEqual(len(self.queue_files("a1")), 3)
    def test_github_claims_as_github(self):
        self.put_item("a", uploaded_at=queue_items.iso(NOW - timedelta(seconds=61)))
        self.w.by, self.w.model = "github", "gemma4:e4b-it-qat"
        self.assertEqual(self.w.process("a"), "published")
        self.assertEqual(self.messages(2), ["사진 게시: 초안", "대기열: a GitHub가 맡음"])
        self.assertEqual(photolib.find_photo(self.photos(), "a")[1]["ai"]["model"], "gemma4:e4b-it-qat")
    def test_github_does_not_take_a_fresh_upload(self):
        self.put_item("a", uploaded_at=queue_items.iso(NOW - timedelta(seconds=30)))
        self.w.by = "github"
        before = self.fake.commits(R)
        self.assertEqual(self.w.process("a"), "skipped")
        self.assertEqual((self.fake.commits(R), self.item("a")["status"]), (before, "waiting"))
    def test_gone_item_is_skipped(self):
        self.assertEqual(self.w.process("nope"), "skipped")
    def test_claim_taken_over_while_drafting_publishes_nothing(self):
        # Drafting took longer than STALE_SECONDS, so GitHub claimed the item again; the photo is its to publish now.
        self.put_item("a")
        def overtaken(*a, **kw):
            later = queue_items.claim(self.item("a"), "github", NOW + timedelta(minutes=11))
            self.fake.put_file(R, "queue/a/item.json", dump_json(later).encode("utf-8"))
            return FakeAI()(*a, **kw)
        self.w.drafter = overtaken
        data = self.fake.read_file(R, "src/photos.json")
        self.assertEqual(self.w.process("a"), "skipped")
        self.assertEqual(self.fake.read_file(R, "src/photos.json"), data)
        self.assertEqual((len(self.queue_files("a")), self.item("a")["by"]), (3, "github"))
    def test_item_published_by_the_other_worker_meanwhile(self):
        self.put_item("a")
        def published_meanwhile(*a, **kw):
            self.fake.put_files(R, {f"queue/a/{f}": None for f in ("full.jpg", "thumb.jpg", "item.json")})
            return FakeAI()(*a, **kw)
        self.w.drafter = published_meanwhile
        data = self.fake.read_file(R, "src/photos.json")
        self.assertEqual(self.w.process("a"), "skipped")
        self.assertEqual((self.fake.read_file(R, "src/photos.json"), self.fake.read_file(R, "photos/full/a.jpg")), (data, None))
    def test_redraft_keeps_room_and_confidence(self):
        self.fake.put_file(R, "queue/redraft-a2/item.json", dump_json(queue_items.new_item("redraft-a2", kind="redraft", grayscale=False, now=NOW, file="a2")).encode("utf-8"))
        self.fake.put_file(R, "photos/thumb/a2.jpg", self.jpeg((300, 200), (90, 90, 90)))
        seen = []
        self.w.drafter = recording(seen)
        self.assertEqual(self.w.process("redraft-a2"), "published")
        room, p = photolib.find_photo(self.photos(), "a2")
        self.assertEqual((room["id"], p["title"], p["alt"], p["ai"]), ("river", "초안", "초안 설명",
                                                                       {"model": "gemma4:12b-it-qat", "fields": ["title", "alt"], "confidence": 0.61}))
        self.assertTrue(seen[0]["grayscale"])  # judged from the thumbnail
        self.assertEqual(self.messages(2), ["AI 설명 다시 쓰기: 초안", "대기열: redraft-a2 PC가 맡음"])
        self.assertEqual((self.queue_files("redraft-a2"), list(self.work.iterdir())), ([], []))
    def test_redraft_of_a_photo_taken_down_before(self):
        self.fake.put_file(R, "queue/redraft-zz/item.json", dump_json(queue_items.new_item("redraft-zz", kind="redraft", grayscale=False, now=NOW, file="zz")).encode("utf-8"))
        data = self.fake.read_file(R, "src/photos.json")
        self.assertEqual(self.w.process("redraft-zz"), "published")
        self.assertEqual((self.w.drafter.calls, self.queue_files("redraft-zz")), (0, []))
        self.assertEqual(self.fake.read_file(R, "src/photos.json"), data)
        self.assertEqual(self.messages(1), ["AI 설명 다시 쓰기: zz"])
    def test_redraft_of_a_photo_taken_down_while_drafting(self):
        self.fake.put_file(R, "queue/redraft-n1/item.json", dump_json(queue_items.new_item("redraft-n1", kind="redraft", grayscale=False, now=NOW, file="n1")).encode("utf-8"))
        def taken_down(*a, **kw):
            d = self.photos(); d["rooms"][1]["photos"] = []
            self.fake.put_files(R, {"src/photos.json": photolib.dumps_data(d).encode("utf-8"), "photos/thumb/n1.jpg": None})
            return FakeAI()(*a, **kw)
        self.w.drafter = taken_down
        self.assertEqual(self.w.process("redraft-n1"), "published")
        self.assertEqual(self.queue_files("redraft-n1"), [])
        self.assertEqual(self.photos()["rooms"][1]["photos"], [])


class LostClaimAnswerTests(FakeRepo, unittest.TestCase):
    """A claim whose every try lost its connection (GitHubError, status 0) may still have landed (ruling R12)."""

    def losing_the_claim(self, *, landed):
        """put_json raises a dropped-connection error, after writing if `landed`."""
        real = self.w.gh.put_json
        def put_json(repo, path, data, **kw):
            if landed:
                real(repo, path, data, **kw)
            raise GitHubError("GitHub에 연결하지 못했어요: ConnectionResetError(10054)", 0)
        self.w.gh.put_json = put_json

    def test_claim_that_landed_goes_on(self):
        self.put_item("a"); self.losing_the_claim(landed=True)
        self.assertEqual(self.w.process("a"), "published")
        self.assertEqual(self.queue_files("a"), [])
    def test_claim_that_never_landed_is_skipped(self):
        self.put_item("a"); self.losing_the_claim(landed=False)
        self.assertEqual(self.w.process("a"), "skipped")
        self.assertEqual((self.w.drafter.calls, self.item("a")["status"]), (0, "waiting"))
    def test_claim_error_other_than_a_drop_is_raised(self):
        self.put_item("a")
        def refused(*a, **kw): raise GitHubError("GitHub 403: Resource not accessible", 403)
        self.w.gh.put_json = refused
        with self.assertRaises(GitHubError):
            self.w.process("a")


class FailureTests(FakeRepo, unittest.TestCase):
    """What is recorded as "failed" (a person has to look), what is not (GitHub trouble), and never over another's item."""

    def assert_failed(self, name, error):
        self.assertEqual(self.w.process(name), "failed")
        self.assertEqual((self.item(name)["status"], self.item(name)["error"]), ("failed", error))
        self.assertEqual(self.messages(1), [f"대기열: {name} AI 실패"])

    def test_no_failure_record_brings_back_an_item_published_meanwhile(self):
        self.as_github(); self.put_old_item("a")
        real, puts = self.w.gh.put_json, []
        def lenient_put_json(repo, path, data, **kw):
            # The fake refuses a contents PUT whose sha is stale (409); real GitHub might create a missing file instead.
            puts.append(path)
            if len(puts) == 1:
                return real(repo, path, data, **kw)  # the claim
            self.fake.put_file(R, path, dump_json(data).encode("utf-8"))
        self.w.gh.put_json = lenient_put_json
        def published_then_failed(*a, **kw):  # another worker took the stale claim over and published the photo
            self.fake.put_files(R, {f"queue/a/{f}": None for f in ("full.jpg", "thumb.jpg", "item.json")})
            return FakeAI(fail=True)(*a, **kw)
        self.w.drafter = published_then_failed
        with self.assertLogs("nocturne.worker", "WARNING") as logs:
            self.assertEqual(self.w.process("a"), "failed")
        self.assertIn("대기열: a ", logs.output[0])
        self.assertEqual((self.queue_files("a"), self.messages(1)), ([], ["fake commit"]))  # nothing after the other's
    def test_no_failure_record_over_a_claim_taken_over(self):
        self.as_github(); self.put_old_item("a")
        def taken_over_then_failed(*a, **kw):
            later = queue_items.claim(self.item("a"), "github", NOW + timedelta(minutes=11))
            self.fake.put_file(R, "queue/a/item.json", dump_json(later).encode("utf-8"))
            return FakeAI(fail=True)(*a, **kw)
        self.w.drafter = taken_over_then_failed
        with self.assertLogs("nocturne.worker", "WARNING"):
            self.assertEqual(self.w.process("a"), "failed")
        self.assertEqual((self.item("a")["status"], self.item("a")["error"]), ("github", None))
    def test_missing_queue_photo_files(self):
        self.put_item("a"); self.fake.delete_file(R, "queue/a/full.jpg")
        self.assert_failed("a", "대기열 사진 파일이 없어요")
        self.put_item("b"); self.fake.delete_file(R, "queue/b/thumb.jpg")
        self.assert_failed("b", "대기열 사진 파일이 없어요")
        self.assertEqual(self.w.drafter.calls, 0)
    def test_thumbnail_gone_when_fetched(self):
        self.put_item("a")
        real = self.w.gh.get_bytes
        def gone_first(repo, path):
            self.fake.delete_file(R, path)
            return real(repo, path)  # 404
        self.w.gh.get_bytes = gone_first
        self.assert_failed("a", "대기열 사진 파일이 없어요")
    def test_unreadable_picture(self):
        self.fake.put_file(R, "queue/redraft-n1/item.json", dump_json(queue_items.new_item("redraft-n1", kind="redraft", grayscale=False, now=NOW, file="n1")).encode("utf-8"))
        self.fake.put_file(R, "photos/thumb/n1.jpg", b"not a jpeg")
        data = self.fake.read_file(R, "src/photos.json")
        self.assert_failed("redraft-n1", "사진을 읽을 수 없어요")
        self.assertEqual((self.fake.read_file(R, "src/photos.json"), self.w.drafter.calls), (data, 0))
        self.assertEqual(list(self.work.iterdir()), [])
    def test_github_trouble_is_not_recorded(self):
        for name, err in (("a", GitHubError("GitHub 502: bad gateway", 502)), ("b", GitHubError("GitHub에 연결하지 못했어요", 0))):
            with self.subTest(err.status):
                self.put_item(name)
                def failing(repo, path, err=err): raise err
                self.w.gh.get_bytes = failing
                with self.assertRaises(GitHubError):
                    self.w.process(name)
                self.assertEqual((self.item(name)["status"], self.item(name)["error"]), ("pc", None))  # left to the stale rule
    def test_drafted_room_gone(self):
        self.put_item("a"); self.w.drafter = FakeAI(room="nowhere")
        self.assert_failed("a", "없는 방이에요: nowhere")
        self.assertEqual(len(self.queue_files("a")), 3)
    def test_a_crash_after_the_claim_is_recorded_not_left_to_loop(self):
        # e.g. a malformed Ollama reply: left claimed, the item would be claimed again every 10 minutes forever
        def malformed(*a, **kw): raise KeyError("response")
        def no_room(*a, **kw):
            draft = FakeAI()(*a, **kw); del draft["room"]  # the crash comes inside the publish commit's build
            return draft
        for name, drafter in (("a", malformed), ("b", no_room)):
            with self.subTest(name):
                self.put_item(name); self.w.drafter = drafter
                data = self.fake.read_file(R, "src/photos.json")
                with self.assertLogs("nocturne.worker", "ERROR") as logs:
                    self.assert_failed(name, "AI 처리 중 문제가 생겼어요")
                self.assertIsNotNone(logs.records[0].exc_info)  # the traceback is in the log
                self.assertEqual((len(self.queue_files(name)), self.fake.read_file(R, "src/photos.json")), (3, data))
                self.assertEqual(list(self.work.iterdir()), [])
                self.assertEqual(self.w.process(name), "skipped")  # a failed item waits for a person


class ReleaseTests(FakeRepo, unittest.TestCase):
    """When the PC's AI gives up, the item goes back to waiting so that GitHub (e4b) can take over (ruling L)."""

    def test_pc_ai_failure_hands_the_item_back_to_waiting(self):
        self.put_item("a", date="2025.11.30"); self.w.drafter = FakeAI(fail=True)
        before = self.item("a")
        self.assertEqual(self.w.process("a"), "released")
        self.assertEqual(self.item("a"), {**before, "error": "Ollama가 꺼져 있어요. Ollama를 켠 뒤 다시 시도하거나 직접 입력하세요."})
        # the message has "다시 시도" in it, so draft.yml starts on this push
        self.assertEqual(self.messages(2), ["대기열: a 다시 시도 (PC AI 실패)", "대기열: a PC가 맡음"])
        self.assertEqual((self.queue_files("a"), list(self.work.iterdir())), (["queue/a/full.jpg", "queue/a/item.json", "queue/a/thumb.jpg"], []))

    def test_the_pc_leaves_a_released_item_alone_for_thirty_minutes(self):
        clock = [NOW]
        self.w.now_fn = lambda: clock[0]
        self.put_item("a"); self.put_item("b", uploaded_at=queue_items.iso(NOW + timedelta(seconds=1)))
        self.w.drafter = FakeAI(fail=True)
        self.assertEqual(self.w.process("a"), "released")
        self.assertEqual(self.w.takeable(), ["b"])
        clock[0] = NOW + timedelta(minutes=29)
        self.assertEqual(self.w.takeable(), ["b"])
        clock[0] = NOW + timedelta(minutes=31)
        self.assertEqual(self.w.takeable(), ["a", "b"])
        self.assertEqual(self.w.released, {})  # forgotten once the time is up

    def test_github_takes_a_released_item_and_records_its_own_failure(self):
        self.put_old_item("a"); self.w.drafter = FakeAI(fail=True)
        self.assertEqual(self.w.process("a"), "released")
        github = Worker(GitHub("test-token", api=self.fake.url), by="github", model="gemma4:e4b-it-qat", workdir=self.work,
                        drafter=FakeAI(fail=True), now_fn=lambda: NOW)
        self.assertEqual(github.takeable(), ["a"])
        self.assertEqual(github.process("a"), "failed")
        self.assertEqual((self.item("a")["status"], self.item("a")["by"]), ("failed", "github"))
        self.assertEqual(github.released, {})

    def test_no_release_over_a_claim_taken_over(self):
        self.put_item("a")
        def taken_over_then_failed(*a, **kw):
            later = queue_items.claim(self.item("a"), "github", NOW + timedelta(minutes=11))
            self.fake.put_file(R, "queue/a/item.json", dump_json(later).encode("utf-8"))
            return FakeAI(fail=True)(*a, **kw)
        self.w.drafter = taken_over_then_failed
        with self.assertLogs("nocturne.worker", "WARNING"):
            self.assertEqual(self.w.process("a"), "skipped")
        self.assertEqual((self.item("a")["status"], self.item("a")["by"], self.messages(1)), ("github", "github", ["fake commit"]))

    def test_other_pc_failures_are_still_recorded(self):
        self.put_item("a"); self.fake.delete_file(R, "queue/a/full.jpg")
        self.assertEqual(self.w.process("a"), "failed")
        self.assertEqual(self.item("a")["status"], "failed")
        self.assertEqual(self.w.released, {})


class RedraftEditTests(FakeRepo, unittest.TestCase):
    """A redraft replaces only the texts a person has not changed since the request was read (ruling R16)."""

    def redraft(self, file, edit):
        self.fake.put_file(R, f"queue/redraft-{file}/item.json", dump_json(queue_items.new_item(f"redraft-{file}", kind="redraft", grayscale=False, now=NOW, file=file)).encode("utf-8"))
        def edited_meanwhile(*a, **kw):
            d = self.photos(); edit(photolib.find_photo(d, file)[1])
            self.fake.put_file(R, "src/photos.json", photolib.dumps_data(d).encode("utf-8"))
            return FakeAI()(*a, **kw)
        self.w.drafter = edited_meanwhile
        self.assertEqual(self.w.process(f"redraft-{file}"), "published")
        self.assertEqual(self.queue_files(f"redraft-{file}"), [])
        return photolib.find_photo(self.photos(), file)

    def test_a_title_changed_meanwhile_is_kept(self):
        # edited straight in photos.json, so ai.fields still lists the title: it must not stay marked as AI text
        room, p = self.redraft("a2", lambda p: p.update(title="내가 고침"))
        self.assertEqual((room["id"], p["title"], p["alt"], p["ai"]),
                         ("river", "내가 고침", "초안 설명", {"model": "gemma4:12b-it-qat", "fields": ["alt"], "confidence": 0.61}))
    def test_a_photo_without_ai_record(self):
        _, p = self.redraft("n1", lambda p: p.update(alt="내 설명"))
        self.assertEqual(p, {"file": "n1", "title": "초안", "alt": "내 설명", "date": "2025.11.30",
                             "ai": {"model": "gemma4:12b-it-qat", "fields": ["title"]}})
    def test_both_changed_meanwhile_only_removes_the_request(self):
        _, p = self.redraft("n1", lambda p: p.update(title="내 제목", alt="내 설명"))
        self.assertEqual(p, {"file": "n1", "title": "내 제목", "alt": "내 설명", "date": "2025.11.30"})


class TakeableTests(FakeRepo, unittest.TestCase):
    def paths(self):
        return {path.removeprefix(f"/repos/{R}/contents/") for _, path in self.fake.requests}

    def test_reads_item_json_only_when_its_folder_changed(self):
        clock = [NOW]
        self.w.by, self.w.now_fn = "github", lambda: clock[0]
        self.put_item("a", uploaded_at=queue_items.iso(NOW - timedelta(seconds=50)))
        self.put_item("b", uploaded_at=queue_items.iso(NOW - timedelta(seconds=70)))
        self.put_item("c", uploaded_at=queue_items.iso(NOW - timedelta(seconds=90)), status="failed")
        self.assertEqual(self.w.takeable(), ["b"])
        self.fake.requests.clear()
        clock[0] = NOW + timedelta(seconds=15)  # the time is judged again on every call
        self.assertEqual(self.w.takeable(), ["b", "a"])  # oldest upload first
        self.assertEqual(self.paths(), {"queue"})
        self.fake.put_file(R, "queue/c/item.json", dump_json({**self.item("c"), "status": "waiting"}).encode("utf-8"))  # 다시 시도
        self.fake.requests.clear()
        self.assertEqual(self.w.takeable(), ["c", "b", "a"])
        self.assertEqual(self.paths(), {"queue", "queue/c/item.json"})
        self.fake.put_files(R, {f"queue/b/{f}": None for f in ("full.jpg", "thumb.jpg", "item.json")})
        self.assertEqual(self.w.takeable(), ["c", "a"])
        self.assertEqual(set(self.w._items), {"a", "c"})  # a folder that went away is forgotten
    def test_empty_queue_and_stray_entries(self):
        self.assertEqual(self.w.takeable(), [])
        self.fake.put_files(R, {"queue/README.txt": b"x", "queue/odd/full.jpg": b"x"})  # a file, and a folder without item.json
        self.put_item("a")
        self.assertEqual(self.w.takeable(), ["a"])
    def test_stale_claims_are_taken(self):
        self.put_item("a", status="github", by="github", claimed_at=queue_items.iso(NOW - timedelta(minutes=11)))
        self.put_item("b", status="github", by="github", claimed_at=queue_items.iso(NOW - timedelta(minutes=5)))
        self.assertEqual(self.w.takeable(), ["a"])

    BROKEN = {"notjson": b"{ not json", "list": b"[1, 2]", "text": b'"waiting"', "binary": b"\xff\xfe\x00",
              "nofields": b'{"status": "waiting"}', "badtime": b'{"status": "pc", "claimed_at": "yesterday", "uploaded_at": "x"}'}

    def put_broken(self):
        self.fake.put_files(R, {f"queue/{name}/item.json": raw for name, raw in self.BROKEN.items()})

    def test_unreadable_items_are_skipped_and_logged_once(self):
        # shown in the admin page as "확인할 수 없는 항목"; one of them must not stop the others from being taken
        self.put_item("a"); self.put_broken()
        with self.assertLogs("nocturne.worker", "WARNING") as logs:
            self.assertEqual(self.w.takeable(), ["a"])
        self.assertEqual(sorted(name for name in self.BROKEN if any(f"queue/{name}/" in line for line in logs.output)), sorted(self.BROKEN))
        with self.assertNoLogs("nocturne.worker", "WARNING"):  # unchanged folders are neither read nor logged again
            self.assertEqual(self.w.takeable(), ["a"])
        self.w.by, self.w.now_fn = "github", lambda: NOW + timedelta(seconds=120)
        self.w._items.clear()
        with self.assertLogs("nocturne.worker", "WARNING"):
            self.assertEqual(self.w.takeable(), ["a"])

    def test_process_skips_an_unreadable_item(self):
        self.put_broken()
        before = self.fake.commits(R)
        for name in self.BROKEN:
            with self.subTest(name):
                self.assertEqual(self.w.process(name), "skipped")
        self.assertEqual((self.fake.commits(R), self.w.drafter.calls), (before, 0))


class UpgradeTests(FakeRepo, unittest.TestCase):
    def edit(self, change):
        d = self.photos(); change(d)
        self.fake.put_file(R, "src/photos.json", photolib.dumps_data(d).encode("utf-8"))

    def test_upgrade_uses_the_pc_model_and_the_thumbnail(self):
        self.fake.put_file(R, "photos/thumb/a2.jpg", self.jpeg((300, 200), (90, 90, 90)))
        self.w.model = "something-else"  # the model this worker publishes with; upgrades are always by pc_model
        seen = []
        self.w.drafter = recording(seen)
        self.assertEqual(self.w.upgrade_one(), ("upgraded", "a2"))
        self.assertEqual((seen[0]["model"], seen[0]["grayscale"], seen[0]["image"]), ("gemma4:12b-it-qat", True, self.fake.read_file(R, "photos/thumb/a2.jpg")))
        self.assertEqual(self.messages(1), ["AI 설명 개선: 초안"])
        self.assertEqual(list(self.work.iterdir()), [])
        self.assertIsNone(self.w.upgrade_one())
    def test_only_the_untouched_fields_and_never_the_room(self):
        self.edit(lambda d: d["rooms"][0]["photos"][1].update(title="사람 제목", ai={"model": "gemma4:e4b-it-qat", "fields": ["alt"], "confidence": 0.5}))
        self.w.drafter = FakeAI(room="night")
        self.assertEqual(self.w.upgrade_one(), ("upgraded", "a2"))
        room, p = photolib.find_photo(self.photos(), "a2")
        self.assertEqual((room["id"], p["title"], p["alt"], p["ai"]),
                         ("river", "사람 제목", "초안 설명", {"model": "gemma4:12b-it-qat", "fields": ["alt"], "confidence": 0.5}))
        self.assertEqual(self.messages(1), ["AI 설명 개선: 사람 제목"])
    def test_person_edited_everything_meanwhile(self):
        def by_a_person(d):
            p = d["rooms"][0]["photos"][1]
            p.update(title="사람 제목", alt="사람 설명"); del p["ai"]
        def edited(*a, **kw):
            self.edit(by_a_person)
            return FakeAI()(*a, **kw)
        self.w.drafter = edited
        before = self.fake.commits(R)
        self.assertEqual(self.w.upgrade_one(), ("changed", "a2"))
        self.assertEqual(self.fake.commits(R), before + 1)  # the person's edit, and nothing from the worker
        self.assertEqual(photolib.find_photo(self.photos(), "a2")[1], {"file": "a2", "title": "사람 제목", "alt": "사람 설명"})
    def test_taken_down_meanwhile(self):
        def taken_down(*a, **kw):
            self.edit(lambda d: d["rooms"][0]["photos"].pop())
            return FakeAI()(*a, **kw)
        self.w.drafter = taken_down
        self.assertEqual(self.w.upgrade_one(), ("changed", "a2"))
        self.assertEqual(self.photos()["rooms"][0]["photos"], [SITE["rooms"][0]["photos"][0]])
    def test_missing_or_unreadable_thumbnail_counts_as_failed(self):
        self.edit(lambda d: d["rooms"][1]["photos"][0].update(ai={"model": "gemma4:e4b-it-qat", "fields": ["title", "alt"]}))
        self.fake.delete_file(R, "photos/thumb/a2.jpg")
        self.fake.put_file(R, "photos/thumb/n1.jpg", b"not a jpeg")
        self.assertEqual(self.w.upgrade_one(), ("failed", "a2"))
        self.assertEqual(self.w.upgrade_one(), ("failed", "n1"))
        self.assertEqual((self.w.skip, self.w.drafter.calls), ({"a2", "n1"}, 0))
        self.assertIsNone(self.w.upgrade_one())
        self.assertEqual(list(self.work.iterdir()), [])
    def test_trouble_with_github_is_raised_not_skipped(self):
        self.fake.fail_next("GET", f"/repos/{R}/contents/photos/thumb/a2.jpg", 502, times=5)  # every try, drops included
        with self.assertRaises(GitHubError):
            self.w.upgrade_one()
        self.assertEqual(self.w.skip, set())
    def test_skip_is_per_photo(self):
        self.edit(lambda d: d["rooms"][1]["photos"][0].update(ai={"model": "gemma4:e4b-it-qat", "fields": ["title", "alt"]}))
        self.w.drafter = FakeAI(fail=True)
        self.assertEqual(self.w.upgrade_one(), ("failed", "a2"))
        self.w.drafter = FakeAI()
        self.assertEqual(self.w.upgrade_one(), ("upgraded", "n1"))
        self.w.skip.clear()
        self.assertEqual(self.w.upgrade_one(), ("upgraded", "a2"))


class MainTests(FakeRepo, unittest.TestCase):
    """worker.py --by github --check / --run as the draft workflow runs it."""

    def setUp(self):
        super().setUp()
        self.output = self.tmp / "github_output"
        self.output.write_text("earlier=1\n", encoding="utf-8")

    def run_main(self, *argv, drafter=None):
        env = {"GITHUB_TOKEN": "test-token", "GITHUB_OUTPUT": str(self.output), "NOCTURNE_MODEL": "gemma4:e4b-it-qat"}
        connect = lambda token: GitHub(token, api=self.fake.url)
        make = functools.partial(Worker, drafter=drafter or FakeAI(), now_fn=lambda: NOW + timedelta(seconds=120))
        with mock.patch.dict(os.environ, env), mock.patch.object(worker, "GitHub", connect), mock.patch.object(worker, "Worker", make), \
                contextlib.redirect_stdout(io.StringIO()) as printed:
            code = worker.main(list(argv))
        return code, printed.getvalue(), self.output.read_text(encoding="utf-8")

    def test_check_lists_the_names_and_changes_nothing(self):
        self.put_item("a"); self.put_item("b", uploaded_at=queue_items.iso(NOW - timedelta(seconds=10)))
        self.put_item("c", status="failed")
        before = self.fake.commits(R)
        self.assertEqual(self.run_main("--by", "github", "--check"), (0, "b,a\n", "earlier=1\nnames=b,a\n"))
        self.assertEqual(self.fake.commits(R), before)
    def test_check_with_nothing_to_do(self):
        self.assertEqual(self.run_main("--by", "github", "--check"), (0, "\n", "earlier=1\nnames=\n"))
    def test_check_lists_the_others_when_an_item_json_is_unreadable(self):
        self.put_item("a"); self.fake.put_file(R, "queue/bad/item.json", b"{ not json")
        with self.assertLogs("nocturne.worker", "WARNING"):
            self.assertEqual(self.run_main("--by", "github", "--check"), (0, "a\n", "earlier=1\nnames=a\n"))
    def test_run_publishes_with_the_model_named_in_the_environment(self):
        self.put_item("a")
        code, printed, output = self.run_main("--by", "github", "--run")
        self.assertEqual((code, output), (0, "earlier=1\npublished=1\n"))
        self.assertIn("published: a", printed)
        self.assertEqual(photolib.find_photo(self.photos(), "a")[1]["ai"]["model"], "gemma4:e4b-it-qat")
        self.assertEqual(self.messages(2), ["사진 게시: 초안", "대기열: a GitHub가 맡음"])
    def test_run_goes_on_after_an_item_fails_on_github(self):
        self.put_item("a"); self.put_item("b", uploaded_at=queue_items.iso(NOW - timedelta(seconds=10)))
        # every try fails, so a dropped error response cannot let b through on a repeat
        self.fake.fail_next("GET", f"/repos/{R}/contents/queue/b/thumb.jpg", 500, times=5)
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            code, printed, output = self.run_main("--by", "github", "--run")
        self.assertEqual((code, output), (1, "earlier=1\npublished=1\n"))
        self.assertTrue(errors.getvalue().startswith("error: b: GitHub 500"), errors.getvalue())
        self.assertEqual(self.queue_files("a"), [])
    def test_run_goes_on_after_a_crash_and_writes_the_count(self):
        self.put_item("a", uploaded_at=queue_items.iso(NOW - timedelta(seconds=10))); self.put_item("b")
        real = Worker.process
        def crashing(worker_self, name):
            if name == "a":  # a crash that escapes process (before any claim)
                raise RuntimeError("boom")
            return real(worker_self, name)
        with mock.patch.object(Worker, "process", crashing), contextlib.redirect_stderr(io.StringIO()) as errors:
            code, printed, output = self.run_main("--by", "github", "--run")
        self.assertEqual((code, output), (1, "earlier=1\npublished=1\n"))
        self.assertIn("error: a: RuntimeError: boom", errors.getvalue())
        self.assertIn("Traceback", errors.getvalue())
        self.assertIn("published: b", printed)
        self.assertEqual((self.queue_files("b"), self.item("a")["status"]), ([], "waiting"))
    def test_run_records_a_crash_after_the_claim_and_goes_on(self):
        self.put_item("a", uploaded_at=queue_items.iso(NOW - timedelta(seconds=10))); self.put_item("b")
        answers = [RuntimeError("boom"), FakeAI()]
        def drafter(*a, **kw):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer(*a, **kw)
        with self.assertLogs("nocturne.worker", "ERROR"):
            code, printed, output = self.run_main("--by", "github", "--run", drafter=drafter)
        self.assertEqual((code, output), (0, "earlier=1\npublished=1\n"))
        self.assertEqual((self.item("a")["status"], self.item("a")["error"]), ("failed", "AI 처리 중 문제가 생겼어요"))
        self.assertEqual(printed.splitlines(), ["failed: a", "published: b"])


if __name__ == "__main__":
    unittest.main()
