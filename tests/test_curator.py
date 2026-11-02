import base64
import builtins
import http.client
import io
import json
import math
import os
import shutil
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
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

    def test_prompt_asks_for_english_too(self):
        p = curator.build_prompt(helpers.SITE)
        self.assertEqual(curator.PROMPT_HEAD, "당신은 사진 전시 사이트의 큐레이터입니다. 이 사진을 아래 방 중 하나에 걸고, 한국어 제목과 대체 텍스트(alt), "
                                              "그리고 같은 뜻의 영어 제목(title_en)과 영어 대체 텍스트(alt_en)를 쓰세요.")
        self.assertTrue(p.startswith(curator.PROMPT_HEAD))
        rule = "- 영어 제목은 2~6단어의 짧은 명사구, 영어 alt는 무엇이 찍혔는지 한 문장이고 마침표로 끝내지 마세요. 영어에도 위 규칙(장소 이름, 흑백)을 똑같이 지키세요."
        self.assertTrue(curator.PROMPT_RULES.endswith("\n" + rule))  # the last rule line
        self.assertIn(rule, p)
        self.assertEqual(curator.PROMPT_REPLY,
                         'JSON으로만 답하세요: {"room": "<방 id>", "title": "...", "alt": "...", "title_en": "...", "alt_en": "..."}')
        for s in ("title_en", "alt_en", "[밤] 공중전화 / 빨간 공중전화 부스 | Payphone / A red payphone booth"):
            self.assertIn(s, p)
        self.assertIn("[강] 스카이라인 / 강 건너 빌딩\n", p)  # a photo without English has no " | " part
        self.assertNotIn("스카이라인 / 강 건너 빌딩 |", p)

    def test_example_heading_says_the_part_after_the_bar_is_english(self):
        p = curator.build_prompt(helpers.SITE)
        self.assertIn('기존 사진의 제목과 alt 예시 (말투를 맞추세요, " | " 뒤는 영어):\n- [강] 스카이라인 / 강 건너 빌딩\n', p)
        site = {"rooms": [{"id": "r", "name": "방", "note": "n", "photos": [{"file": "p0", "title": "t0", "alt": "a0"}]}]}
        plain = curator.build_prompt(site)  # no example has English, so there is no " | " to explain
        self.assertIn("기존 사진의 제목과 alt 예시 (말투를 맞추세요):\n- [방] t0 / a0", plain)

    def test_english_example_needs_both_texts(self):
        site = {"rooms": [{"id": "r", "name": "방", "note": "n", "photos": [
            {"file": "p0", "title": "t0", "alt": "a0", "title_en": "T0"},
            {"file": "p1", "title": "t1", "alt": "a1", "alt_en": "A1"}]}]}
        p = curator.build_prompt(site)
        self.assertIn("- [방] t0 / a0\n", p)
        self.assertIn("- [방] t1 / a1\n", p)
        self.assertNotIn(" | ", p)

    def test_examples_are_first_two_per_room(self):
        site = {"rooms": [{"id": "r", "name": "방", "note": "n", "photos": [
            {"file": f"p{i}", "title": f"t{i}", "alt": f"a{i}"} for i in range(3)]}]}
        p = curator.build_prompt(site)
        self.assertIn("- [방] t0 / a0", p)
        self.assertIn("- [방] t1 / a1", p)
        self.assertNotIn("t2", p)


def reply_json(room="night", title="t", alt="a", title_en="T", alt_en="A", **extra):
    """A model's JSON reply with the four texts (and whatever else `extra` adds)."""
    return json.dumps({"room": room, "title": title, "alt": alt, "title_en": title_en, "alt_en": alt_en, **extra},
                      ensure_ascii=False)


class ParseTests(unittest.TestCase):
    ids = ["river", "night"]

    def test_ok_fenced_unknown_bad(self):
        self.assertEqual(curator.parse_reply(reply_json(), self.ids),
                         {"room": "night", "title": "t", "alt": "a", "title_en": "T", "alt_en": "A", "known": True})
        self.assertEqual(curator.parse_reply("```json\n" + reply_json(room="river") + "\n```", self.ids)["room"], "river")
        moon = curator.parse_reply(reply_json(room="moon"), self.ids)
        self.assertEqual((moon["room"], moon["known"]), ("river", False))
        for bad in ("not json", '{"room":"river","title":"t"}', reply_json(title=""),
                    '{"room":"river","title":"t","alt":"a"}'):  # the English texts are required
            with self.assertRaises(ValueError):
                curator.parse_reply(bad, self.ids)

    def test_all_four_texts_are_required_and_not_blank(self):
        # the error names the missing text in Korean, with the particle that fits
        said = {"title": "제목이", "alt": "설명이", "title_en": "영어 제목이", "alt_en": "영어 설명이"}
        for key, name in said.items():
            missing = json.loads(reply_json())
            del missing[key]
            for label, reply in (("missing", json.dumps(missing)), ("empty", reply_json(**{key: ""})),
                                 ("blank", reply_json(**{key: "   "})), ("null", reply_json(**{key: None})),
                                 ("number", reply_json(**{key: 5}))):
                with self.subTest(key=key, case=label), self.assertRaises(ValueError) as cm:
                    curator.parse_reply(reply, self.ids)
                self.assertEqual(str(cm.exception), f"{name} 비어 있어요")

    def test_english_texts_are_stripped(self):
        got = curator.parse_reply(reply_json(title_en="  Payphone ", alt_en="\nA red booth \n"), self.ids)
        self.assertEqual((got["title_en"], got["alt_en"]), ("Payphone", "A red booth"))


class ClampTests(unittest.TestCase):
    """A rambling model must not put a paragraph into the title or the alt (both go public without a person's look)."""
    ids = ["river"]

    def parse(self, title, alt, title_en="T", alt_en="A"):
        return curator.parse_reply(reply_json("river", title, alt, title_en, alt_en), self.ids)

    def assert_cut_at_a_word(self, got, original, limit):
        self.assertLessEqual(len(got), limit)
        self.assertTrue(original.startswith(got), got)
        self.assertEqual(original[len(got)], " ")  # it ends where a word ended
        self.assertGreater(len(got), limit // 2)

    def test_limits(self):
        self.assertEqual((curator.TITLE_MAX, curator.ALT_MAX), (40, 200))
        self.assertEqual((curator.TITLE_EN_MAX, curator.ALT_EN_MAX), (60, 250))

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

    def test_english_texts_are_cut_at_a_word(self):
        title = " ".join(["rain"] * 14) + "x"  # 70 characters
        alt = (" ".join(["a wet street under tall buildings at night"] * 7))[:300]
        self.assertEqual((len(title), len(alt)), (70, 300))
        got = self.parse("가", "나", title, alt)
        self.assert_cut_at_a_word(got["title_en"], title, 60)
        self.assert_cut_at_a_word(got["alt_en"], alt, 250)
        self.assertEqual((got["title"], got["alt"]), ("가", "나"))

    def test_short_english_texts_are_kept(self):
        title, alt = "t" * 60, "a" * 250
        got = self.parse("가", "나", title, alt)
        self.assertEqual((got["title_en"], got["alt_en"]), (title, alt))

    def test_one_long_word_is_cut_hard(self):
        got = self.parse("가" * 50, "나" * 250)
        self.assertEqual((got["title"], got["alt"]), ("가" * 40, "나" * 200))
        got = self.parse("가", "나", "t" * 70, "a" * 300)
        self.assertEqual((got["title_en"], got["alt_en"]), ("t" * 60, "a" * 250))

    def test_no_dangling_comma_or_space(self):
        got = self.parse("가" * 30 + ", " + "나" * 20, "a")
        self.assertEqual(got["title"], "가" * 30)

    def test_the_grayscale_ending_is_added_after_the_cut(self):
        tmp = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        img = helpers.make_jpeg(tmp / "t.jpg")
        alt = " ".join(["강 건너 높은 빌딩들이 늘어선 밤의 도시 풍경"] * 10)
        alt_en = (" ".join(["a wet street under tall buildings at night"] * 7))[:300]
        reply = {"response": reply_json("river", "강", alt, "River", alt_en)}
        got = curator.draft(img, helpers.SITE, True, post=lambda p, t: reply)
        self.assertTrue(got["alt"].endswith(", 흑백 사진"), got["alt"])
        self.assert_cut_at_a_word(got["alt"].removesuffix(", 흑백 사진"), alt, 200)
        self.assertTrue(got["alt_en"].endswith(", black-and-white photo"), got["alt_en"])  # added after the cut, too
        self.assert_cut_at_a_word(got["alt_en"].removesuffix(", black-and-white photo"), alt_en, 250)


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

    def test_finish_english(self):
        self.assertEqual(curator.finish_alt_en("A red booth.", True), "A red booth, black-and-white photo")
        self.assertEqual(curator.finish_alt_en("A red booth.", False), "A red booth")
        self.assertEqual(curator.finish_alt_en("A red booth, black-and-white photo", True), "A red booth, black-and-white photo")
        self.assertEqual(curator.finish_alt_en("A red booth, black-and-white photo.", True), "A red booth, black-and-white photo")
        self.assertEqual(curator.finish_alt_en("  A  red\n booth ..", True), "A red booth, black-and-white photo")
        self.assertNotIn("black-and-white", curator.finish_alt_en("A red booth.", False))


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
                          post=lambda p, t: seen.append(p) or self.reply(
                              reply_json("night", "공중전화", "부스입니다.", "Payphone", "A red booth.")))
        p = seen[0]
        self.assertEqual((p["model"], p["format"], p["think"], p["stream"], p["logprobs"], p["top_logprobs"]), ("m1", "json", False, False, True, 5))
        self.assertIn("title_en", p["prompt"])
        self.assertEqual(r, {"room": "night", "title": "공중전화", "alt": "부스, 흑백 사진", "title_en": "Payphone",
                             "alt_en": "A red booth, black-and-white photo", "confidence": 0.95, "model": "m1"})

    def test_colour_photo_has_no_black_and_white_ending(self):
        r = curator.draft(self.img, helpers.SITE, False,
                          post=lambda p, t: self.reply(reply_json("night", "공중전화", "부스입니다.", "Payphone", "A red booth.")))
        self.assertEqual((r["alt"], r["title_en"], r["alt_en"]), ("부스", "Payphone", "A red booth"))

    def test_a_reply_without_english_is_asked_again(self):
        replies = iter([self.reply('{"room": "night", "title": "t", "alt": "a"}'),
                        self.reply(reply_json(title_en="")), self.reply(reply_json())])
        calls = []
        r = curator.draft(self.img, helpers.SITE, False, post=lambda p, t: calls.append(1) or next(replies))
        self.assertEqual((len(calls), r["title_en"], r["alt_en"]), (3, "T", "A"))
        with self.assertRaises(curator.CuratorError) as cm:
            curator.draft(self.img, helpers.SITE, False, post=lambda p, t: self.reply('{"room": "night", "title": "t", "alt": "a"}'))
        self.assertIn("영어 제목이 비어 있어요", str(cm.exception))

    def test_three_attempts(self):
        calls = []

        def flaky(p, t):
            calls.append(1)
            if len(calls) < 3:
                raise curator.CuratorError("끊김")
            return self.reply(reply_json())
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
        r = curator.draft(self.img, helpers.SITE, False, post=lambda p, t: self.reply(reply_json("moon")))
        self.assertEqual((r["room"], r["confidence"]), ("river", 0.0))
        self.assertIs(type(r["confidence"]), int)

    def test_confidence_is_int_when_whole(self):
        sure = {"response": reply_json(),
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
        good = json.dumps({"response": reply_json("night", "공중전화", "부스", "Payphone", "A booth")})
        calls = []

        def flaky_urlopen(request, timeout):
            calls.append(1)
            if len(calls) < 3:
                raise http.client.IncompleteRead(b"par", 483)
            return io.BytesIO(good.encode("utf-8"))
        with mock.patch.object(curator.urllib.request, "urlopen", flaky_urlopen):
            r = curator.draft(img, helpers.SITE, False, model="m")
        self.assertEqual((r["room"], r["title"], r["title_en"], len(calls)), ("night", "공중전화", "Payphone", 3))


class PromptReplyTests(unittest.TestCase):
    def test_reply_line_is_swappable(self):
        ollama = curator.build_prompt(helpers.SITE)
        claude = curator.build_prompt(helpers.SITE, curator.CLAUDE_REPLY)
        self.assertTrue(ollama.endswith(curator.PROMPT_REPLY))
        self.assertTrue(claude.endswith(curator.CLAUDE_REPLY))
        self.assertEqual(ollama.removesuffix(curator.PROMPT_REPLY), claude.removesuffix(curator.CLAUDE_REPLY))
        self.assertIn("장소의 이름을 쓰지 마세요", claude)
        self.assertIn("confidence", claude)
        self.assertNotIn("JSON으로만 답하세요", claude)


class APIStatusError(Exception):
    """Same shape as anthropic.APIStatusError: an int status_code, the error body (a dict when it was JSON) and the
    request id; `attrs` adds attributes such as `type`. The conversion is duck-typed, so no SDK is needed."""

    def __init__(self, status_code, *, body=None, request_id=None, **attrs):
        super().__init__(f"HTTP {status_code}: secret details from the API")
        self.status_code, self.body, self.request_id = status_code, body, request_id
        for key, value in attrs.items():
            setattr(self, key, value)


def error_body(kind, message="secret details from the API"):
    """An Anthropic API error body as the SDK parses it."""
    return {"type": "error", "error": {"type": kind, "message": message}, "request_id": "req_inbody"}


class APIConnectionError(Exception):
    """Same name as anthropic.APIConnectionError: no status_code."""


class APITimeoutError(APIConnectionError):
    """Same as anthropic.APITimeoutError, a subclass of APIConnectionError."""


class FakeClient:
    """Stands in for anthropic.Anthropic: beta.messages.create records its keyword arguments."""

    def __init__(self, response=None, error=None):
        self.calls = []
        self.response, self.error = response, error
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


def claude_response(reply=None, *, text=None, stop_reason="end_turn", model="claude-opus-5-5", blocks=None):
    """What client.beta.messages.create returns: a text block holding the JSON reply."""
    if text is None:
        text = json.dumps(reply, ensure_ascii=False)
    content = blocks if blocks is not None else [SimpleNamespace(type="text", text=text)]
    return SimpleNamespace(stop_reason=stop_reason, model=model, content=content)


GOOD_REPLY = {"room": "night", "title": "공중전화", "alt": "빨간 공중전화 부스", "title_en": "Payphone",
              "alt_en": "A red payphone booth", "confidence": 0.9}


class ClaudeDraftTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        self.img = helpers.make_jpeg(tmp / "t.jpg")
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("NOCTURNE_CLAUDE_MODEL", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    def draft(self, response=None, error=None, grayscale=False, **kwargs):
        client = FakeClient(response if response is not None else claude_response(GOOD_REPLY), error)
        return curator.claude_draft(self.img, helpers.SITE, grayscale, client=client, **kwargs), client

    def failure(self, **kwargs):
        with self.assertRaises(curator.CuratorError) as cm:
            self.draft(**kwargs)
        return str(cm.exception)

    def test_constants(self):
        self.assertEqual(curator.CLAUDE_MODEL, "claude-opus-5-5")
        self.assertEqual(curator.UPGRADE_FROM, ("gemma4:e4b-it-qat",))
        self.assertEqual(curator.claude_model_name(), "claude-opus-5-5")

    def test_request(self):
        got, client = self.draft()
        self.assertEqual(len(client.calls), 1)
        kw = client.calls[0]
        self.assertEqual(set(kw), {"model", "max_tokens", "betas", "fallbacks", "output_config", "messages"})
        self.assertEqual(set(kw["output_config"]), {"effort", "format"})
        self.assertEqual((kw["model"], kw["max_tokens"]), ("claude-opus-5-5", 16000))
        self.assertEqual(kw["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(kw["fallbacks"], "default")
        self.assertEqual(kw["output_config"]["effort"], "low")
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        schema = kw["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["room"]["enum"], [room["id"] for room in helpers.SITE["rooms"]])
        self.assertEqual(set(schema["properties"]), {"room", "title", "alt", "title_en", "alt_en", "confidence"})
        self.assertEqual({key: schema["properties"][key]["type"] for key in schema["properties"]},
                         {"room": "string", "title": "string", "alt": "string", "title_en": "string", "alt_en": "string",
                          "confidence": "number"})
        self.assertEqual(schema["required"], ["room", "title", "alt", "title_en", "alt_en", "confidence"])
        self.assertIs(schema["additionalProperties"], False)
        self.assertNotIn("thinking", kw)  # Opus 5.5 always thinks and rejects the parameter
        self.assertNotIn("temperature", kw)
        (message,) = kw["messages"]
        self.assertEqual(message["role"], "user")
        image, text = message["content"]
        self.assertEqual(image, {"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(self.img.read_bytes()).decode("ascii")}})
        self.assertEqual(text["type"], "text")
        self.assertIn("장소의 이름을 쓰지 마세요", text["text"])
        self.assertIn("confidence", text["text"])
        self.assertIn("title_en", text["text"])
        self.assertIn("alt_en", text["text"])
        self.assertIn("영어 제목은 2~6단어의 짧은 명사구", text["text"])
        self.assertNotIn("JSON으로만 답하세요", text["text"])
        self.assertEqual(got["model"], "claude-opus-5-5")

    def test_schema(self):
        schema = curator.claude_schema(["river"])
        self.assertEqual(schema["required"], ["room", "title", "alt", "title_en", "alt_en", "confidence"])
        self.assertEqual(schema["properties"]["title_en"], {"type": "string"})
        self.assertEqual(schema["properties"]["alt_en"], {"type": "string"})
        self.assertEqual(schema["properties"]["room"], {"type": "string", "enum": ["river"]})
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(set(schema["properties"]), set(schema["required"]))  # every property is required

    def test_model_from_environment(self):
        os.environ["NOCTURNE_CLAUDE_MODEL"] = "claude-sonnet-5-5"
        self.assertEqual(curator.claude_model_name(), "claude-sonnet-5-5")
        _, client = self.draft(claude_response(GOOD_REPLY, model="claude-sonnet-5-5"))
        self.assertEqual(client.calls[0]["model"], "claude-sonnet-5-5")
        os.environ["NOCTURNE_CLAUDE_MODEL"] = ""  # a workflow variable that is not set arrives empty
        self.assertEqual(curator.claude_model_name(), "claude-opus-5-5")

    def test_model_argument_wins(self):
        _, client = self.draft(model="claude-haiku-5")
        self.assertEqual(client.calls[0]["model"], "claude-haiku-5")

    def test_reply_is_tidied(self):
        title = "  공중전화 부스가 있는 비 내린 밤거리의 아주 긴 제목이 계속 이어집니다 그리고 더 이어집니다 "
        title_en = "  A red payphone booth on a rainy night street with a very long title that goes on and on  "
        reply = {"room": "night", "title": title, "alt": "빨간 공중전화 부스입니다.", "title_en": title_en,
                 "alt_en": "  A red payphone booth.  ", "confidence": 0.65}
        got, _ = self.draft(claude_response(reply), grayscale=True)
        self.assertGreater(len(title.strip()), 40)
        self.assertEqual(got["title"], curator.clamp(title.strip(), 40))
        self.assertLessEqual(len(got["title"]), 40)
        self.assertGreater(len(title_en.strip()), 60)
        self.assertEqual(got["title_en"], curator.clamp(title_en.strip(), 60))
        self.assertLessEqual(len(got["title_en"]), 60)
        self.assertEqual(got, {"room": "night", "title": got["title"], "alt": "빨간 공중전화 부스, 흑백 사진",
                               "title_en": got["title_en"], "alt_en": "A red payphone booth, black-and-white photo",
                               "confidence": 0.65, "model": "claude-opus-5-5"})

    def test_colour_photo_has_no_black_and_white_ending(self):
        got, _ = self.draft(claude_response(dict(GOOD_REPLY, alt_en="A red payphone booth.")))
        self.assertEqual((got["alt"], got["title_en"], got["alt_en"]), ("빨간 공중전화 부스", "Payphone", "A red payphone booth"))

    def test_the_grayscale_ending_is_not_added_twice(self):
        got, _ = self.draft(claude_response(dict(GOOD_REPLY, alt_en="A red booth, black-and-white photo")), grayscale=True)
        self.assertEqual(got["alt_en"], "A red booth, black-and-white photo")

    def test_fallback_model_is_recorded(self):
        got, _ = self.draft(claude_response(GOOD_REPLY, model="claude-opus-4-8"))
        self.assertEqual(got["model"], "claude-opus-4-8")

    def test_thinking_block_before_the_text_is_skipped(self):
        blocks = [SimpleNamespace(type="thinking", thinking="…"),
                  SimpleNamespace(type="text", text=json.dumps(GOOD_REPLY, ensure_ascii=False))]
        got, _ = self.draft(claude_response(blocks=blocks))
        self.assertEqual(got["room"], "night")

    def test_confidence_edges(self):
        for value, want in ((1.7, 1.0), (-0.2, 0.0), ("0.9", None), (True, None), (None, None), (0.9, 0.9),
                            (float("nan"), None), (float("inf"), 1.0)):
            with self.subTest(value=value):
                got, _ = self.draft(claude_response(dict(GOOD_REPLY, confidence=value)))
                self.assertEqual(got["confidence"], want)
                self.assertEqual(got["confidence"] is None, want is None)

    def test_missing_confidence_is_none(self):
        got, _ = self.draft(claude_response({k: v for k, v in GOOD_REPLY.items() if k != "confidence"}))
        self.assertIsNone(got["confidence"])

    def test_whole_confidence_is_int(self):
        # photos.json is written by Python and by JavaScript; 1.0 would print differently, so 1 is an int.
        for value in (1, 1.0, 1.7, 0, -0.2):
            with self.subTest(value=value):
                got, _ = self.draft(claude_response(dict(GOOD_REPLY, confidence=value)))
                self.assertIs(type(got["confidence"]), int)

    def test_failures(self):
        cases = [
            (dict(response=claude_response(GOOD_REPLY, stop_reason="refusal")), "Claude가 이 사진의 초안을 거절했어요"),
            (dict(response=claude_response(blocks=[], stop_reason="refusal")), "Claude가 이 사진의 초안을 거절했어요"),
            (dict(response=claude_response(GOOD_REPLY, stop_reason="max_tokens")), "Claude의 답이 끊겼어요"),
            (dict(error=APIStatusError(401)), "Claude API 키가 맞지 않아요"),
            (dict(error=APIStatusError(402)), "Claude API 권한이나 크레딧을 확인해 주세요"),
            (dict(error=APIStatusError(403)), "Claude API 권한이나 크레딧을 확인해 주세요"),
            (dict(error=APIStatusError(429)), "Claude API 요청이 너무 많아요"),
            (dict(error=APIStatusError(500)), "Claude API 오류 500"),
            (dict(error=APIStatusError(529)), "Claude API 오류 529"),
            (dict(error=APIConnectionError()), "Claude API에 연결하지 못했어요"),
            (dict(error=APITimeoutError()), "Claude API에 연결하지 못했어요"),
        ]
        for kwargs, message in cases:
            with self.subTest(message=message, **{k: type(v).__name__ for k, v in kwargs.items()}):
                self.assertEqual(self.failure(**kwargs), message)

    def test_status_error_detail(self):
        # the error type (only a known Anthropic name) and the request id help to look the failure up; the API's own
        # message never goes into the note
        cases = [
            (APIStatusError(500), "Claude API 오류 500"),
            (APIStatusError(500, body=error_body("api_error"), request_id="req_011CAbc9"), "Claude API 오류 500 (api_error, req_011CAbc9)"),
            (APIStatusError(400, body=error_body("invalid_request_error")), "Claude API 오류 400 (invalid_request_error)"),
            (APIStatusError(404, request_id="req_abc123"), "Claude API 오류 404 (req_abc123)"),
            (APIStatusError(529, type="overloaded_error"), "Claude API 오류 529 (overloaded_error)"),  # no body: the attribute
            (APIStatusError(413, body="<html>too large</html>", type="request_too_large"), "Claude API 오류 413 (request_too_large)"),
            (APIStatusError(400, body={"error": "flat"}, request_id="req_1"), "Claude API 오류 400 (req_1)"),
            (APIStatusError(400, body={"type": "error"}), "Claude API 오류 400"),
            (APIStatusError(400, body=error_body("Your credit balance is too low")), "Claude API 오류 400"),  # not a known type
            (APIStatusError(400, body=error_body("billing_error"), request_id="not-a-request-id"), "Claude API 오류 400 (billing_error)"),
            (APIStatusError(500, body=error_body(["api_error"])), "Claude API 오류 500"),
        ]
        for kind in ("invalid_request_error", "authentication_error", "permission_error", "not_found_error",
                     "request_too_large", "rate_limit_error", "api_error", "overloaded_error", "billing_error"):
            cases.append((APIStatusError(418, body=error_body(kind)), f"Claude API 오류 418 ({kind})"))
        for bad in ("req_", "req_abc def", "req_abc\n", "REQ_abc", "req_abc-1", "xreq_abc", 12345, b"req_abc"):
            cases.append((APIStatusError(500, request_id=bad), "Claude API 오류 500"))
        for error, message in cases:
            with self.subTest(status=error.status_code, body=error.body, request_id=error.request_id):
                got = self.failure(error=error)
                self.assertEqual(got, message)
                self.assertNotIn("secret", got)
        # the fixed messages stay as they are
        self.assertEqual(self.failure(error=APIStatusError(401, body=error_body("authentication_error"), request_id="req_1")),
                         "Claude API 키가 맞지 않아요")

    def test_every_failure_message_is_known_as_a_claude_failure(self):
        # the worker's Claude pass leaves alone an item whose error is one of these: an earlier pass handed it off
        errors = [APIStatusError(status) for status in (400, 401, 402, 403, 404, 429, 500)]
        errors += [APIStatusError(529, body=error_body("overloaded_error"), request_id="req_1"), APIConnectionError(), APITimeoutError()]
        replies = [claude_response(GOOD_REPLY, stop_reason="refusal"), claude_response(GOOD_REPLY, stop_reason="max_tokens"),
                   claude_response(text="not json"), claude_response(dict(GOOD_REPLY, room="nowhere")), claude_response(blocks=[])]
        messages = [self.failure(error=error) for error in errors] + [self.failure(response=reply) for reply in replies]
        for message in messages + [curator.CLAUDE_NO_KEY, curator.CLAUDE_NO_PACKAGE]:
            self.assertTrue(curator.is_claude_failure(message), message)
        for other in (curator.OFF_MESSAGE, "AI가 답을 주지 못했어요: JSON이 아니에요", "Ollama 오류 500: boom",
                      "Ollama 연결 오류: timed out", "같은 이름의 사진이 이미 있어요", "", "Claude", None, 500):
            self.assertFalse(curator.is_claude_failure(other), other)

    def test_unreadable_replies(self):
        unreadable = [
            claude_response(text="not json"),
            claude_response(dict(GOOD_REPLY, room="nowhere")),
            claude_response(dict(GOOD_REPLY, title="")),
            claude_response(dict(GOOD_REPLY, title="   ")),
            claude_response(dict(GOOD_REPLY, alt="")),
            claude_response(dict(GOOD_REPLY, title_en="")),
            claude_response(dict(GOOD_REPLY, alt_en="   ")),
            claude_response({k: v for k, v in GOOD_REPLY.items() if k != "title_en"}),
            claude_response({k: v for k, v in GOOD_REPLY.items() if k != "alt_en"}),
            claude_response(blocks=[]),
            claude_response(blocks=[SimpleNamespace(type="thinking", thinking="…")]),
        ]
        for n, response in enumerate(unreadable):
            with self.subTest(n=n):
                self.assertTrue(self.failure(response=response).startswith("Claude의 답을 읽을 수 없어요 ("))

    def test_the_reply_text_is_not_put_in_the_error(self):
        self.assertNotIn("not json", self.failure(response=claude_response(text="not json")))

    def test_other_errors_are_not_converted(self):
        with self.assertRaises(TypeError):
            self.draft(error=TypeError("bad argument"))

    def test_no_key_does_not_import_the_sdk(self):
        real_import, imported = builtins.__import__, []

        def spy(name, *args, **kwargs):
            if name.split(".")[0] == "anthropic":
                imported.append(name)
            return real_import(name, *args, **kwargs)
        with mock.patch.object(builtins, "__import__", spy), self.assertRaises(curator.CuratorError) as cm:
            curator.claude_draft(self.img, helpers.SITE, False)
        self.assertEqual(str(cm.exception), "Claude API 키가 없어요")
        self.assertFalse(cm.exception.retry)
        self.assertEqual(imported, [])
        os.environ["ANTHROPIC_API_KEY"] = ""  # an empty secret is no key
        with self.assertRaises(curator.CuratorError) as cm:
            curator.claude_draft(self.img, helpers.SITE, False)
        self.assertEqual(str(cm.exception), "Claude API 키가 없어요")

    def test_no_package(self):
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        with mock.patch.dict(sys.modules, {"anthropic": None}), self.assertRaises(curator.CuratorError) as cm:
            curator.claude_draft(self.img, helpers.SITE, False)
        self.assertEqual(str(cm.exception), "anthropic 패키지가 없어요")
        self.assertFalse(cm.exception.retry)

    def test_production_client(self):
        """Without a client it builds anthropic.Anthropic(timeout=120, max_retries=2); the key stays in the environment."""
        os.environ["ANTHROPIC_API_KEY"] = "sk-test"
        built = []
        fake_module = SimpleNamespace(Anthropic=lambda **kw: built.append(kw) or FakeClient(claude_response(GOOD_REPLY)))
        with mock.patch.dict(sys.modules, {"anthropic": fake_module}):
            got = curator.claude_draft(self.img, helpers.SITE, False)
        self.assertEqual(built, [{"timeout": 120, "max_retries": 2}])
        self.assertEqual(got["room"], "night")


class ClaudePickCoverTests(unittest.TestCase):
    """curator.claude_pick_cover: Claude looks at the photos on show and names the cover and where to crop it."""

    CANDIDATES = [{"file": "a1", "title": "스카이라인", "room": "강", "image": b"jpeg-a1"},
                  {"file": "n1", "title": "공중전화", "room": "밤", "image": b"jpeg-n1"}]

    def setUp(self):
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("NOCTURNE_CLAUDE_MODEL", None)

    def pick(self, reply=None, *, response=None, error=None, candidates=None, current="a1", **kwargs):
        if response is None:
            response = claude_response(reply if reply is not None else {"file": "n1", "focus_x": 0.3, "focus_y": 0.6})
        client = FakeClient(response, error)
        got = curator.claude_pick_cover(candidates or self.CANDIDATES, current, client=client, **kwargs)
        return got, client

    def failure(self, **kwargs):
        with self.assertRaises(curator.CuratorError) as cm:
            self.pick(**kwargs)
        return str(cm.exception)

    def test_pick_cover_request(self):
        _, client = self.pick()
        self.assertEqual(len(client.calls), 1)
        kw = client.calls[0]
        self.assertEqual((kw["model"], kw["max_tokens"], kw["betas"], kw["fallbacks"]),
                         ("claude-opus-5-5", 16000, ["server-side-fallback-2026-07-01"], "default"))
        self.assertEqual(kw["output_config"]["effort"], "low")
        schema = kw["output_config"]["format"]["schema"]
        self.assertEqual(schema["properties"]["file"], {"type": "string", "enum": ["a1", "n1"]})
        self.assertEqual(schema["required"], ["file", "focus_x", "focus_y"])
        self.assertIs(schema["additionalProperties"], False)
        content = kw["messages"][0]["content"]
        self.assertEqual([block["type"] for block in content], ["text", "text", "image", "text", "image", "text"])
        self.assertEqual(content[1]["text"], "사진 a1: 스카이라인 (방: 강)")
        self.assertEqual(content[2]["source"], {"type": "base64", "media_type": "image/jpeg",
                                                "data": base64.b64encode(b"jpeg-a1").decode("ascii")})
        self.assertEqual(content[3]["text"], "사진 n1: 공중전화 (방: 밤)")
        last = content[-1]["text"]
        for s in ("대표 사진", "4:5", "focus_x", "지금 대표 사진은 a1입니다"):
            self.assertIn(s, last)
        self.assertNotIn("JSON으로만 답하세요", last)  # the schema says how to answer

    def test_pick_cover_without_a_current_cover(self):
        _, client = self.pick(current=None)
        self.assertNotIn("지금 대표 사진은", client.calls[0]["messages"][0]["content"][-1]["text"])

    def test_pick_cover_reply(self):
        got, _ = self.pick({"file": "n1", "focus_x": 0.333, "focus_y": 1.0})
        self.assertEqual(got, {"file": "n1", "focus": [0.33, 1], "model": "claude-opus-5-5"})
        self.assertIsInstance(got["focus"][1], int)  # whole values are written as 0 or 1, the same in Python and JS
        got, _ = self.pick(response=claude_response({"file": "a1", "focus_x": 0.5, "focus_y": 0.25}, model="claude-sonnet-5-5"))
        self.assertEqual(got, {"file": "a1", "focus": [0.5, 0.25], "model": "claude-sonnet-5-5"})  # the model that answered

    def test_focus_is_clamped(self):
        for x, want in ((-0.2, 0), (1.7, 1), (float("nan"), 0.5), ("left", 0.5), (True, 0.5), (0.005, 0.01)):
            got, _ = self.pick({"file": "n1", "focus_x": x, "focus_y": 0.5})
            self.assertEqual(got["focus"][0], want, x)

    def test_focus_only_request(self):
        got, client = self.pick({"file": "a1", "focus_x": 0.1, "focus_y": 0.9}, candidates=self.CANDIDATES[:1], focus_only=True)
        content = client.calls[0]["messages"][0]["content"]
        self.assertEqual([block["type"] for block in content], ["text", "text", "image", "text"])
        self.assertNotIn("지금 대표 사진은", content[-1]["text"])
        self.assertIn("focus_x", content[-1]["text"])
        self.assertEqual(client.calls[0]["output_config"]["format"]["schema"]["properties"]["file"]["enum"], ["a1"])
        self.assertEqual(got["focus"], [0.1, 0.9])

    def test_pick_cover_failures(self):
        cases = [
            (dict(response=claude_response(stop_reason="refusal", text="")), "Claude가 대표 사진 고르기를 거절했어요"),
            (dict(response=claude_response(stop_reason="max_tokens", text="")), "Claude의 답이 끊겼어요"),
            (dict(error=APIStatusError(401)), "Claude API 키가 맞지 않아요"),
            (dict(error=APIConnectionError("down")), "Claude API에 연결하지 못했어요"),
        ]
        for kwargs, message in cases:
            self.assertEqual(self.failure(**kwargs), message)
        self.assertEqual(curator.CLAUDE_COVER_REFUSED, "Claude가 대표 사진 고르기를 거절했어요")
        for reply in ("not json", json.dumps({"file": "zz", "focus_x": 0.5, "focus_y": 0.5}), json.dumps(["a1"])):
            self.assertTrue(self.failure(response=claude_response(text=reply)).startswith("Claude의 답을 읽을 수 없어요 ("), reply)
        message = self.failure(response=claude_response(blocks=[SimpleNamespace(type="thinking", thinking="…")]))
        self.assertTrue(message.startswith("Claude의 답을 읽을 수 없어요 ("))
        self.assertTrue(curator.is_claude_failure(curator.CLAUDE_COVER_REFUSED))


class FakeAITests(unittest.TestCase):
    def test_fake(self):
        ai = helpers.FakeAI(room="river", confidence=0.5)
        self.assertEqual(ai(Path("x.jpg"), helpers.SITE, False, model="mm"),
                         {"room": "river", "title": "초안", "alt": "초안 설명", "title_en": "Draft",
                          "alt_en": "A draft description", "confidence": 0.5, "model": "mm"})
        self.assertEqual(ai(Path("x.jpg"), helpers.SITE, False)["model"], "fake")
        self.assertEqual(ai.calls, 2)
        with self.assertRaises(curator.CuratorError):
            helpers.FakeAI(fail=True)(Path("x.jpg"), helpers.SITE, False)


if __name__ == "__main__":
    unittest.main()
