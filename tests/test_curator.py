import http.client
import io
import json
import math
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import helpers  # puts scripts/ on sys.path
import curator


def tok(text, tops=None):
    """One logprob entry as Ollama returns it: the token, its logprob, and the top alternatives."""
    lp = {"token": text, "logprob": 0.0}
    lp["top_logprobs"] = [{"token": t, "logprob": math.log(p)} for t, p in (tops or [])]
    return lp


class PromptTests(unittest.TestCase):
    def test_prompt(self):
        p = curator.build_prompt(helpers.SITE)
        for s in ("river (강): 물가에서 바라본 도시", "[밤] 공중전화 / 빨간 공중전화 부스", "장소의 이름을 쓰지 마세요", '{"room": "<방 id>"'):
            self.assertIn(s, p)

    def test_examples_are_first_two_per_room(self):
        site = {"rooms": [{"id": "r", "name": "방", "note": "n", "photos": [
            {"file": f"p{i}", "title": f"t{i}", "alt": f"a{i}"} for i in range(3)]}]}
        p = curator.build_prompt(site)
        self.assertIn("- [방] t0 / a0", p)
        self.assertIn("- [방] t1 / a1", p)
        self.assertNotIn("t2", p)


class ParseTests(unittest.TestCase):
    ids = ["river", "night"]

    def test_ok_fenced_unknown_bad(self):
        self.assertEqual(curator.parse_reply('{"room":"night","title":"t","alt":"a"}', self.ids)["known"], True)
        self.assertEqual(curator.parse_reply('```json\n{"room":"river","title":"t","alt":"a"}\n```', self.ids)["room"], "river")
        self.assertEqual(curator.parse_reply('{"room":"moon","title":"t","alt":"a"}', self.ids)["room"], "river")
        for bad in ("not json", '{"room":"river","title":"t"}', '{"room":"river","title":"","alt":"a"}'):
            with self.assertRaises(ValueError):
                curator.parse_reply(bad, self.ids)


class ClampTests(unittest.TestCase):
    """A rambling model must not put a paragraph into the title or the alt (both go public without a person's look)."""
    ids = ["river"]

    def parse(self, title, alt):
        return curator.parse_reply(json.dumps({"room": "river", "title": title, "alt": alt}, ensure_ascii=False), self.ids)

    def assert_cut_at_a_word(self, got, original, limit):
        self.assertLessEqual(len(got), limit)
        self.assertTrue(original.startswith(got), got)
        self.assertEqual(original[len(got)], " ")  # it ends where a word ended
        self.assertGreater(len(got), limit // 2)

    def test_limits(self):
        self.assertEqual((curator.TITLE_MAX, curator.ALT_MAX), (40, 200))

    def test_short_texts_are_kept(self):
        title, alt = "가" * 40, "나" * 200
        got = self.parse(title, alt)
        self.assertEqual((got["title"], got["alt"]), (title, alt))

    def test_long_texts_are_cut_at_a_word(self):
        title = "비 내린 밤 골목 끝에서 혼자 빛나는 오래된 공중전화 부스와 젖은 보도블록"  # 41 characters
        alt = " ".join(["강 건너 높은 빌딩들이 늘어선 밤의 도시 풍경"] * 10)
        got = self.parse(title, alt)
        self.assertGreater(len(title), 40)
        self.assert_cut_at_a_word(got["title"], title, 40)
        self.assert_cut_at_a_word(got["alt"], alt, 200)

    def test_one_long_word_is_cut_hard(self):
        got = self.parse("가" * 50, "나" * 250)
        self.assertEqual((got["title"], got["alt"]), ("가" * 40, "나" * 200))

    def test_no_dangling_comma_or_space(self):
        got = self.parse("가" * 30 + ", " + "나" * 20, "a")
        self.assertEqual(got["title"], "가" * 30)

    def test_the_grayscale_ending_is_added_after_the_cut(self):
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        img = helpers.make_jpeg(tmp / "t.jpg")
        alt = " ".join(["강 건너 높은 빌딩들이 늘어선 밤의 도시 풍경"] * 10)
        reply = {"response": json.dumps({"room": "river", "title": "강", "alt": alt}, ensure_ascii=False)}
        got = curator.draft(img, helpers.SITE, True, post=lambda p, t: reply)["alt"]
        self.assertTrue(got.endswith(", 흑백 사진"), got)
        self.assert_cut_at_a_word(got.removesuffix(", 흑백 사진"), alt, 200)


class ConfidenceTests(unittest.TestCase):
    def test_tokens(self):
        lp = [tok('{"'), tok("room"), tok('":'), tok(' "'), tok("st", [("st", 0.9), ("night", 0.1)]), tok("adium")]
        self.assertEqual(curator.room_confidence(lp, "stadium"), 0.9)
        self.assertEqual(curator.room_confidence([tok('{"room":'), tok(' "night', [(' "night', 0.7), (' "river', 0.3)])], "night"), 0.7)
        self.assertIsNone(curator.room_confidence(None, "night"))
        self.assertIsNone(curator.room_confidence([tok('{"title": "x"}')], "night"))

    def test_whole_number_is_int(self):
        # photos.json is written by Python and by JavaScript; 1.0 would print differently, so 1 is an int.
        got = curator.room_confidence([tok('{"room": "'), tok("night", [("night", 1.0)])], "night")
        self.assertEqual(got, 1)
        self.assertIs(type(got), int)


class AltTests(unittest.TestCase):
    def test_finish(self):
        self.assertEqual(curator.finish_alt("밤거리의 공중전화 부스입니다.", False), "밤거리의 공중전화 부스")
        self.assertEqual(curator.finish_alt("나무 사이로 보이는 강.", True), "나무 사이로 보이는 강, 흑백 사진")
        self.assertEqual(curator.finish_alt("강, 흑백 사진", True), "강, 흑백 사진")


class DraftTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.img = helpers.make_jpeg(tmp / "t.jpg")

    def reply(self, text):
        return {"response": text, "logprobs": [tok('{"room": "'), tok("night", [("night", 0.95), ("river", 0.05)])]}

    def test_payload_and_result(self):
        seen = []
        r = curator.draft(self.img, helpers.SITE, True, model="m1",
                          post=lambda p, t: seen.append(p) or self.reply('{"room": "night", "title": "공중전화", "alt": "부스입니다."}'))
        p = seen[0]
        self.assertEqual((p["model"], p["format"], p["think"], p["stream"], p["logprobs"], p["top_logprobs"]), ("m1", "json", False, False, True, 5))
        self.assertEqual(r, {"room": "night", "title": "공중전화", "alt": "부스, 흑백 사진", "confidence": 0.95, "model": "m1"})

    def test_three_attempts(self):
        calls = []

        def flaky(p, t):
            calls.append(1)
            if len(calls) < 3:
                raise curator.CuratorError("끊김")
            return self.reply('{"room": "night", "title": "t", "alt": "a"}')
        self.assertEqual(curator.draft(self.img, helpers.SITE, False, post=flaky)["room"], "night")
        calls.clear()
        with self.assertRaises(curator.CuratorError):
            curator.draft(self.img, helpers.SITE, False, post=lambda p, t: {"response": "nope"})

    def test_no_retry_when_off(self):
        calls = []

        def off(p, t):
            calls.append(1)
            raise curator.CuratorError("Ollama가 꺼져 있어요.", retry=False)
        with self.assertRaises(curator.CuratorError):
            curator.draft(self.img, helpers.SITE, False, post=off)
        self.assertEqual(len(calls), 1)

    def test_unknown_room_zero(self):
        r = curator.draft(self.img, helpers.SITE, False, post=lambda p, t: self.reply('{"room": "moon", "title": "t", "alt": "a"}'))
        self.assertEqual((r["room"], r["confidence"]), ("river", 0.0))
        self.assertIs(type(r["confidence"]), int)

    def test_confidence_is_int_when_whole(self):
        sure = {"response": '{"room": "night", "title": "t", "alt": "a"}',
                "logprobs": [tok('{"room": "'), tok("night", [("night", 1.0)])]}
        r = curator.draft(self.img, helpers.SITE, False, post=lambda p, t: sure)
        self.assertEqual(r["confidence"], 1)
        self.assertIs(type(r["confidence"]), int)


class StatusTests(unittest.TestCase):
    def test_status(self):
        self.assertEqual(curator.status(model="m", get=lambda u, t: {"models": [{"name": "m"}]}), "ok")
        self.assertEqual(curator.status(model="m", get=lambda u, t: {"models": []}), "no-model")

        def down(u, t):
            raise OSError("refused")
        self.assertEqual(curator.status(get=down), "off")

    def test_latest_tag(self):
        self.assertEqual(curator.status(model="m", get=lambda u, t: {"models": [{"name": "m:latest"}]}), "ok")
        self.assertEqual(curator.status(model="m:q", get=lambda u, t: {"models": [{"name": "m:q:latest"}]}), "no-model")

    def test_dropped_body_is_off(self):
        def dropped(u, t):
            raise http.client.IncompleteRead(b"par", 483)
        self.assertEqual(curator.status(get=dropped), "off")


class TransportTests(unittest.TestCase):
    """How the default post turns urllib outcomes into CuratorError (urlopen is replaced; no network).

    A real loopback server is not used: on this PC about one in six local connections is reset by Windows.
    """

    def post_with(self, outcome):
        def fake_urlopen(request, timeout):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        with mock.patch.object(curator.urllib.request, "urlopen", fake_urlopen):
            return curator._post({"model": "m"}, 5)

    def http_error(self, code, body):
        return urllib.error.HTTPError("http://x/api/generate", code, "err", {}, io.BytesIO(body.encode("utf-8")))

    def test_refused_means_off_without_retry(self):
        class WinRefused(OSError):  # what an OSError looks like on Windows, whatever platform the test runs on
            winerror = 10061
        for refused in (urllib.error.URLError(ConnectionRefusedError("refused")),
                        urllib.error.URLError(WinRefused("refused"))):
            with self.subTest(refused=refused), self.assertRaises(curator.CuratorError) as cm:
                self.post_with(refused)
            self.assertEqual(str(cm.exception), "Ollama가 꺼져 있어요. Ollama를 켠 뒤 다시 시도하거나 직접 입력하세요.")
            self.assertFalse(cm.exception.retry)

    def test_missing_model_stops_retries(self):
        with self.assertRaises(curator.CuratorError) as cm:
            self.post_with(self.http_error(404, '{"error":"model \'m\' not found"}'))
        self.assertEqual(str(cm.exception), "모델 m이 없어요. ollama pull m 후 다시 시도하세요.")
        self.assertFalse(cm.exception.retry)

    def test_other_failures_are_retried(self):
        failures = (self.http_error(500, '{"error":"wsarecv: An existing connection was forcibly closed"}'),
                    self.http_error(404, "page missing"),
                    urllib.error.URLError(OSError("unreachable")),
                    ConnectionResetError("reset while reading"),
                    TimeoutError("timed out"))
        for failure in failures:
            with self.subTest(failure=failure), self.assertRaises(curator.CuratorError) as cm:
                self.post_with(failure)
            self.assertTrue(cm.exception.retry)
        with self.assertRaises(curator.CuratorError) as cm:  # the server's own words reach the message
            self.post_with(self.http_error(500, '{"error":"wsarecv: An existing connection was forcibly closed"}'))
        self.assertIn("forcibly closed", str(cm.exception))

    def test_ok_reply(self):
        self.assertEqual(self.post_with(io.BytesIO('{"response": "{}"}'.encode("utf-8"))), {"response": "{}"})

    def test_dropped_body_is_retryable(self):
        """The connection closes after the headers: http.client raises IncompleteRead, which is not an OSError."""
        class DroppedBody(io.BytesIO):
            def read(self, *args):
                raise http.client.IncompleteRead(b"par", 483)

        for label, outcome in (("urlopen raises", http.client.IncompleteRead(b"par", 483)),
                               ("reply body drops", DroppedBody()),
                               ("error body drops", urllib.error.HTTPError("http://x/api/generate", 500, "err", {}, DroppedBody()))):
            with self.subTest(label), self.assertRaises(curator.CuratorError) as cm:
                self.post_with(outcome)
            self.assertTrue(cm.exception.retry)

    def test_draft_retries_dropped_body(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        img = helpers.make_jpeg(tmp / "t.jpg")
        good = json.dumps({"response": '{"room": "night", "title": "공중전화", "alt": "부스"}'})
        calls = []

        def flaky_urlopen(request, timeout):
            calls.append(1)
            if len(calls) < 3:
                raise http.client.IncompleteRead(b"par", 483)
            return io.BytesIO(good.encode("utf-8"))
        with mock.patch.object(curator.urllib.request, "urlopen", flaky_urlopen):
            r = curator.draft(img, helpers.SITE, False, model="m")
        self.assertEqual((r["room"], r["title"], len(calls)), ("night", "공중전화", 3))


class FakeAITests(unittest.TestCase):
    def test_fake(self):
        ai = helpers.FakeAI(room="river", confidence=0.5)
        self.assertEqual(ai(Path("x.jpg"), helpers.SITE, False, model="mm"),
                         {"room": "river", "title": "초안", "alt": "초안 설명", "confidence": 0.5, "model": "mm"})
        self.assertEqual(ai(Path("x.jpg"), helpers.SITE, False)["model"], "fake")
        self.assertEqual(ai.calls, 2)
        with self.assertRaises(curator.CuratorError):
            helpers.FakeAI(fail=True)(Path("x.jpg"), helpers.SITE, False)


if __name__ == "__main__":
    unittest.main()
