"""Curator: an AI drafts the room, title and alt text of a photo, in Korean and in English.

Two drafters with the same call shape and result: `draft` asks a local vision model (Ollama), `claude_draft` asks
the Claude API. Standard library only, except that `claude_draft` imports the `anthropic` SDK (requirements-ai.txt)
when it is not given a client, so the site build and the tests need no SDK. Callers pass `post`/`get`/`client` in
tests; by default they talk to Ollama over HTTP and to the Claude API through the SDK.
"""
import base64
import http.client
import json
import math
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "gemma4:12b-it-qat"
UPGRADE_FROM = ("gemma4:e4b-it-qat",)  # drafts by these models are rewritten later by a better one
CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_BETA = "server-side-fallback-2026-07-01"  # with fallbacks="default": another model answers if one refuses
CLAUDE_MAX_TOKENS = 16000  # room for the thinking Opus 5.5 always does; the reply itself is tiny
CLAUDE_TIMEOUT = 120  # seconds for one request
CLAUDE_RETRIES = 2  # the SDK retries 429, 5xx and dropped connections itself; a failure after that is the caller's
TIMEOUT = 300  # seconds for one generate call
CONFIDENT = 0.8  # room confidence at or above this is trusted without a second look
ATTEMPTS = 3  # this PC's Ollama sometimes drops a request right after generating; a retry works
TITLE_MAX = 40  # characters; the prompt asks for 2 to 8, this only stops a rambling reply
ALT_MAX = 200  # characters, before ", 흑백 사진" is added
TITLE_EN_MAX = 60  # characters; the prompt asks for 2 to 6 words
ALT_EN_MAX = 250  # characters, before ", black-and-white photo" is added

OFF_MESSAGE = "Ollama가 꺼져 있어요. Ollama를 켠 뒤 다시 시도하거나 직접 입력하세요."
CLAUDE_NO_KEY = "Claude API 키가 없어요"
CLAUDE_NO_PACKAGE = "anthropic 패키지가 없어요"
CLAUDE_BAD_KEY = "Claude API 키가 맞지 않아요"
CLAUDE_NO_ACCESS = "Claude API 권한이나 크레딧을 확인해 주세요"
CLAUDE_BUSY = "Claude API 요청이 너무 많아요"
CLAUDE_OFFLINE = "Claude API에 연결하지 못했어요"
CLAUDE_REFUSED = "Claude가 이 사진의 초안을 거절했어요"
CLAUDE_COVER_REFUSED = "Claude가 대표 사진 고르기를 거절했어요"
CLAUDE_CUT_OFF = "Claude의 답이 끊겼어요"
CLAUDE_STATUS = "Claude API 오류 "  # + the status, and maybe "(<type>, <request id>)"
CLAUDE_UNREADABLE = "Claude의 답을 읽을 수 없어요 ("  # + the reason and ")"
_CLAUDE_FAILURES = frozenset({CLAUDE_NO_KEY, CLAUDE_NO_PACKAGE, CLAUDE_BAD_KEY, CLAUDE_NO_ACCESS, CLAUDE_BUSY,
                              CLAUDE_OFFLINE, CLAUDE_REFUSED, CLAUDE_COVER_REFUSED, CLAUDE_CUT_OFF})
# Anthropic's error type names: only one of these is put in a note, never whatever else an error body says
CLAUDE_ERROR_TYPES = frozenset({"invalid_request_error", "authentication_error", "permission_error", "not_found_error",
                                "request_too_large", "rate_limit_error", "api_error", "overloaded_error", "billing_error"})
_REQUEST_ID = re.compile(r"req_[A-Za-z0-9]+")

PROMPT_HEAD = ("당신은 사진 전시 사이트의 큐레이터입니다. 이 사진을 아래 방 중 하나에 걸고, 한국어 제목과 대체 텍스트(alt), "
               "그리고 같은 뜻의 영어 제목(title_en)과 영어 대체 텍스트(alt_en)를 쓰세요.")
PROMPT_RULES = """규칙:
- 제목은 2~8글자의 짧은 명사구.
- alt는 화면 낭독기용으로, 무엇이 찍혔는지 한 문장. "입니다"나 마침표로 끝내지 마세요.
- 방 설명 문구를 그대로 옮기지 말고, 이 사진에 보이는 것을 쓰세요.
- 도시, 건물, 경기장, 다리 같은 장소의 이름을 쓰지 마세요. 사진만 보고 장소를 짐작하지 마세요.
- 흑백인지 컬러인지는 쓰지 마세요.
- 영어 제목은 2~6단어의 짧은 명사구, 영어 alt는 무엇이 찍혔는지 한 문장이고 마침표로 끝내지 마세요. 영어에도 위 규칙(장소 이름, 흑백)을 똑같이 지키세요."""
PROMPT_REPLY = 'JSON으로만 답하세요: {"room": "<방 id>", "title": "...", "alt": "...", "title_en": "...", "alt_en": "..."}'
CLAUDE_REPLY = "confidence에는 고른 방이 맞다고 얼마나 확신하는지 0에서 1 사이의 숫자를 쓰세요."

# Picking the cover (scripts/cover.py): every photo on show goes before COVER_PICK; to set only where a cover a person
# picked is cropped, that one photo goes before COVER_FOCUS.
COVER_HEAD = "당신은 사진 전시 사이트 Nocturne의 큐레이터입니다. 아래는 지금 전시 중인 사진들이고, 각 사진 앞에 이름과 제목, 방이 있습니다."
COVER_FOCUS_HEAD = "당신은 사진 전시 사이트 Nocturne의 큐레이터입니다. 아래 사진을 사이트 첫 화면 맨 위에 대표 사진으로 크게 겁니다."
COVER_FRAMES = ("대표 사진은 넓은 화면에서는 가로가 조금 긴 틀(약 7:5), 폰에서는 세로 4:5 틀에 맞춰 잘립니다.")
COVER_WHERE = ("focus_x, focus_y에는 잘릴 때 꼭 남길 곳의 위치를 0에서 1 사이로 쓰세요. "
               "사진 왼쪽 위가 (0, 0), 오른쪽 아래가 (1, 1)입니다.")
COVER_PICK = f"""사이트 첫 화면 맨 위에 크게 걸 대표 사진 한 장을 고르세요.
- 혼자서 이 전시 전체를 대표할 만큼 인상이 강한 사진을 고르세요.
- {COVER_FRAMES} 두 틀 모두로 잘려도 주제가 살아 있는 사진을 고르세요.
- {COVER_WHERE}
- file에는 고른 사진의 이름을 쓰세요."""
COVER_KEEP = "지금 대표 사진은 {file}입니다. 다른 사진이 뚜렷이 낫지 않으면 지금 사진을 그대로 고르세요."
COVER_FOCUS = f"""{COVER_FRAMES} 잘려도 주제가 살아 있게 하세요.
- {COVER_WHERE}
- file에는 이 사진의 이름을 쓰세요."""


class CuratorError(Exception):
    """The draft could not be made. retry=False means asking again right away cannot help."""

    def __init__(self, message, *, retry=True):
        super().__init__(message)
        self.retry = retry


def model_name():
    return os.environ.get("NOCTURNE_MODEL", DEFAULT_MODEL)


def claude_model_name():
    return os.environ.get("NOCTURNE_CLAUDE_MODEL") or CLAUDE_MODEL  # empty counts as unset: a workflow passes "" for no value


# --- prompt and reply ------------------------------------------------------------------------

def _has_english(photo):
    return bool(photo.get("title_en") and photo.get("alt_en"))


def _example(room, photo):
    """One example line: "- [방] 제목 / alt", and " | English title / English alt" after it when the photo has both."""
    line = f"- [{room['name']}] {photo['title']} / {photo['alt']}"
    if _has_english(photo):
        line += f" | {photo['title_en']} / {photo['alt_en']}"
    return line


def build_prompt(data, reply=PROMPT_REPLY):
    """The instructions for a model; `reply` is the last line, which says how to answer (Ollama and Claude differ)."""
    rooms = data["rooms"]
    shown = [(room, p) for room in rooms for p in room["photos"][:2]]
    parts = [PROMPT_HEAD, "방:\n" + "\n".join(f"- {r['id']} ({r['name']}): {r['note']}" for r in rooms)]
    if shown:
        note = ', " | " 뒤는 영어' if any(_has_english(p) for _, p in shown) else ""  # say what " | " means only when it shows
        parts.append(f"기존 사진의 제목과 alt 예시 (말투를 맞추세요{note}):\n" + "\n".join(_example(r, p) for r, p in shown))
    parts += [PROMPT_RULES, reply]
    return "\n\n".join(parts)


def clamp(text, limit):
    """`text` cut to at most `limit` characters: after the last whole word that fits, unless that leaves less than
    half (one very long word is cut where the limit falls). No trailing space or comma is left."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if not text[limit].isspace():
        space = max(cut.rfind(" "), cut.rfind("\n"))
        if space > limit // 2:
            cut = cut[:space]
    return cut.rstrip(" \n,")


def _load_reply(text):
    """The JSON object in the model's reply (it may sit inside a code fence), or ValueError."""
    found = re.search(r"\{.*\}", text or "", re.S)
    try:
        reply = json.loads(found.group(0)) if found else None
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        raise ValueError(f"JSON이 아니에요: {(text or '')[:80]!r}")
    return reply


# The four texts of a reply, as named in an error ("<name> 비어 있어요"): each with the particle that fits it.
TEXT_NAMES = {"title": "제목이", "alt": "설명이", "title_en": "영어 제목이", "alt_en": "영어 설명이"}


def parse_reply(text, room_ids):
    """{"room", "title", "alt", "title_en", "alt_en", "known"} from the model's JSON; an unknown room falls back to the
    first one. All four texts must be there and not blank. They are cut to TITLE_MAX, ALT_MAX, TITLE_EN_MAX and
    ALT_EN_MAX characters (see clamp)."""
    reply = _load_reply(text)
    fields = {}
    for key, name in TEXT_NAMES.items():
        value = reply.get(key)
        fields[key] = value.strip() if isinstance(value, str) else ""
        if not fields[key]:
            raise ValueError(f"{name} 비어 있어요")
    room = reply.get("room")
    room = room.strip() if isinstance(room, str) else ""
    known = room in room_ids
    return {"room": room if known else room_ids[0], "title": clamp(fields["title"], TITLE_MAX),
            "alt": clamp(fields["alt"], ALT_MAX), "title_en": clamp(fields["title_en"], TITLE_EN_MAX),
            "alt_en": clamp(fields["alt_en"], ALT_EN_MAX), "known": known}


def _tidy(value):
    """3 decimals, and a whole number becomes an int: Python and JavaScript print 1.0 differently, 1 the same."""
    value = round(value, 3)
    return int(value) if value == int(value) else value


def room_confidence(logprobs, room):
    """Probability mass of the tokens that spell `room` right after the "room" key, or None if unknown."""
    if not logprobs:
        return None
    text = ""
    for lp in logprobs:
        if re.search(r'"room"\s*:\s*"?\s*$', text):
            if lp["token"].strip().lstrip('"').strip():
                tops = lp.get("top_logprobs") or [lp]
                p = sum(math.exp(t["logprob"]) for t in tops
                        if (s := t["token"].strip().lstrip('"').strip()) and room.startswith(s))
                return _tidy(min(1.0, p))
        text += lp["token"]
    return None


def finish_alt(alt, grayscale):
    """Plain noun phrase: no closing period or 입니다, and ', 흑백 사진' appended for black-and-white photos."""
    alt = " ".join(alt.split()).rstrip(".")
    if alt.endswith("입니다"):
        alt = alt[:-len("입니다")]
    alt = alt.rstrip(" .")
    if grayscale and not alt.endswith("흑백 사진"):
        alt += ", 흑백 사진"
    return alt


def finish_alt_en(alt_en, grayscale):
    """English alt without a closing period, and ', black-and-white photo' appended for black-and-white photos."""
    alt_en = " ".join(alt_en.split()).rstrip(" .")
    if grayscale and not alt_en.endswith("black-and-white photo"):
        alt_en += ", black-and-white photo"
    return alt_en


# --- talking to Ollama -------------------------------------------------------------------------

def _refused(err):
    reason = getattr(err, "reason", err)
    return isinstance(reason, ConnectionRefusedError) or getattr(reason, "winerror", None) == 10061


def _summary(body):
    """The error text of an Ollama error body ({"error": "..."}), or the start of the body."""
    try:
        return str(json.loads(body)["error"])[:300]
    except (ValueError, KeyError, TypeError):
        return body.strip()[:300]


def _post(payload, timeout):
    request = urllib.request.Request(OLLAMA_URL + "/api/generate", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:  # before URLError: it is a subclass
        try:
            with err:  # close the error response, or Python warns about the unclosed file
                body = err.read().decode("utf-8", "replace")
        except (OSError, http.client.HTTPException):  # the connection died while the error body was coming
            body = ""
        if err.code == 404 and "not found" in body.lower():
            model = payload["model"]
            raise CuratorError(f"모델 {model}이 없어요. ollama pull {model} 후 다시 시도하세요.", retry=False) from None
        raise CuratorError(f"Ollama 오류 {err.code}: {_summary(body)}") from None
    except (OSError, ValueError, http.client.HTTPException) as err:  # URLError, timeouts, resets, short or bad body
        if _refused(err):
            raise CuratorError(OFF_MESSAGE, retry=False) from None
        raise CuratorError(f"Ollama 연결 오류: {getattr(err, 'reason', err)}") from None


def _get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def status(*, get=None, model=None):
    """"ok" when Ollama answers and has the model, "no-model" when it lacks it, "off" when it is not running."""
    model = model or model_name()
    try:
        data = (get or _get)(OLLAMA_URL + "/api/tags", 2)
    except (OSError, ValueError, http.client.HTTPException):
        return "off"
    names = {m.get("name") for m in data.get("models", [])}
    return "ok" if names & {model, model if ":" in model else model + ":latest"} else "no-model"


def draft(image, data, grayscale, *, post=None, model=None):
    """Ask the model for {"room", "title", "alt", "title_en", "alt_en", "confidence", "model"}; confidence is None
    without logprobs."""
    model = model or model_name()
    post = post or _post
    room_ids = [room["id"] for room in data["rooms"]]
    payload = {
        "model": model,
        "prompt": build_prompt(data),
        "images": [base64.b64encode(Path(image).read_bytes()).decode("ascii")],
        "format": "json",
        "think": False,
        "stream": False,
        "logprobs": True,
        "top_logprobs": 5,
        "options": {"temperature": 0.2},
    }
    cause = None
    for _ in range(ATTEMPTS):
        try:
            reply = post(payload, TIMEOUT)
            parsed = parse_reply(reply.get("response", ""), room_ids)
        except CuratorError as err:
            if not err.retry:
                raise
            cause = err
        except ValueError as err:
            cause = err
        else:
            confidence = room_confidence(reply.get("logprobs"), parsed["room"]) if parsed["known"] else 0
            return {"room": parsed["room"], "title": parsed["title"], "alt": finish_alt(parsed["alt"], grayscale),
                    "title_en": parsed["title_en"], "alt_en": finish_alt_en(parsed["alt_en"], grayscale),
                    "confidence": confidence, "model": model}
    raise CuratorError(f"AI가 답을 주지 못했어요: {cause}")


# --- talking to Claude -------------------------------------------------------------------------

def claude_schema(room_ids):
    """The JSON Schema the reply must follow: the room is one of the ids, and nothing else is added."""
    return {"type": "object",
            "properties": {"room": {"type": "string", "enum": list(room_ids)}, "title": {"type": "string"},
                           "alt": {"type": "string"}, "title_en": {"type": "string"}, "alt_en": {"type": "string"},
                           "confidence": {"type": "number"}},
            "required": ["room", "title", "alt", "title_en", "alt_en", "confidence"],
            "additionalProperties": False}


def claude_confidence(value):
    """The model's own confidence cut into 0 to 1; None when it is not a number. A whole number is an int (see _tidy)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:  # not a number, or NaN
        return None
    return _tidy(min(1.0, max(0.0, value)))


def _claude_client():
    """anthropic.Anthropic, which reads ANTHROPIC_API_KEY itself. Without a key the SDK is not even imported."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise CuratorError(CLAUDE_NO_KEY, retry=False)
    try:
        import anthropic
    except ImportError:
        raise CuratorError(CLAUDE_NO_PACKAGE, retry=False) from None
    return anthropic.Anthropic(timeout=CLAUDE_TIMEOUT, max_retries=CLAUDE_RETRIES)


def _status_detail(err):
    """"(<type>, <request id>)" for an API status error, either part left out when the error does not carry it (or
    "" for neither): the error type only if it is one of CLAUDE_ERROR_TYPES, read from the error body when it is a
    dict ({"error": {"type": ...}}) and else from err.type; the request id only if it looks like one (req_...)."""
    body = getattr(err, "body", None)
    kind = body.get("error") if isinstance(body, dict) else None
    kind = kind.get("type") if isinstance(kind, dict) else getattr(err, "type", None)
    request_id = getattr(err, "request_id", None)
    parts = [kind if isinstance(kind, str) and kind in CLAUDE_ERROR_TYPES else None,
             request_id if isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id) else None]
    parts = [part for part in parts if part]
    return f" ({', '.join(parts)})" if parts else ""


def _claude_failure(err):
    """The CuratorError for an SDK exception, or None if `err` is not one. It is matched by shape (an int status_code,
    or a class named like the SDK's connection errors) so tests need no SDK. The SDK's own text is not passed on; an
    other status error names only its error type and request id (see _status_detail)."""
    status = getattr(err, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        if status == 401:
            return CuratorError(CLAUDE_BAD_KEY, retry=False)
        if status in (402, 403):
            return CuratorError(CLAUDE_NO_ACCESS, retry=False)
        if status == 429:
            return CuratorError(CLAUDE_BUSY)
        return CuratorError(f"{CLAUDE_STATUS}{status}{_status_detail(err)}")
    if any(cls.__name__ in ("APIConnectionError", "APITimeoutError") for cls in type(err).__mro__):
        return CuratorError(CLAUDE_OFFLINE)
    return None


def _unreadable(reason):
    return CuratorError(f"{CLAUDE_UNREADABLE}{reason})")


def is_claude_failure(message):
    """Is `message` one that claude_draft fails with (so the note a Claude handoff leaves in item.json)?"""
    return isinstance(message, str) and (message in _CLAUDE_FAILURES or message.startswith((CLAUDE_STATUS, CLAUDE_UNREADABLE)))


def _claude_reply(client, model, schema, content, refused):
    """Ask Claude once (the SDK retries transient failures itself) and return (the reply's text, the model that answered,
    which may be a fallback). `refused` is the message for a refusal. Every failure is a CuratorError with a short Korean
    message, never the SDK's text or the reply."""
    try:
        response = client.beta.messages.create(
            model=model,
            max_tokens=CLAUDE_MAX_TOKENS,
            betas=[CLAUDE_BETA],
            fallbacks="default",
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": content}],
        )
    except Exception as err:
        failure = _claude_failure(err)
        if failure is None:
            raise
        raise failure from None
    if response.stop_reason == "refusal":
        raise CuratorError(refused, retry=False)
    if response.stop_reason == "max_tokens":
        raise CuratorError(CLAUDE_CUT_OFF)
    text = next((block.text for block in response.content if block.type == "text"), None)  # thinking blocks may come first
    if text is None:
        raise _unreadable("답에 글이 없어요")
    return text, getattr(response, "model", None) or model


def claude_draft(image, data, grayscale, *, model=None, client=None):
    """Like `draft`, but the Claude API answers: {"room", "title", "alt", "title_en", "alt_en", "confidence", "model"}.
    One request (the SDK retries transient failures itself). Every failure is a CuratorError with a short Korean
    message, never the SDK's text or the reply. `model` in the result is the model that really answered, which may be
    a fallback."""
    model = model or claude_model_name()
    if client is None:
        client = _claude_client()
    room_ids = [room["id"] for room in data["rooms"]]
    content = [_image_block(Path(image).read_bytes()), {"type": "text", "text": build_prompt(data, CLAUDE_REPLY)}]
    text, answered = _claude_reply(client, model, claude_schema(room_ids), content, CLAUDE_REFUSED)
    try:
        parsed = parse_reply(text, room_ids)
        reply = _load_reply(text)
    except ValueError as err:  # parse_reply quotes the start of a reply that is not JSON: keep only the part before ":"
        raise _unreadable(str(err).partition(":")[0]) from None
    if not parsed["known"]:
        raise _unreadable("모르는 방이에요")
    return {"room": parsed["room"], "title": parsed["title"], "alt": finish_alt(parsed["alt"], grayscale),
            "title_en": parsed["title_en"], "alt_en": finish_alt_en(parsed["alt_en"], grayscale),
            "confidence": claude_confidence(reply.get("confidence")), "model": answered}


def _image_block(jpeg):
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(jpeg).decode("ascii")}}


def cover_schema(files):
    """The JSON Schema of a cover reply: one of `files`, and where to keep it when it is cropped."""
    return {"type": "object",
            "properties": {"file": {"type": "string", "enum": list(files)}, "focus_x": {"type": "number"},
                           "focus_y": {"type": "number"}},
            "required": ["file", "focus_x", "focus_y"],
            "additionalProperties": False}


def cover_focus(value):
    """One coordinate of a cover focus: inside 0 to 1, two decimals, 0 and 1 as ints (they print alike in Python and
    JavaScript); 0.5 (the middle) when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return 0.5
    value = round(min(1.0, max(0.0, value)), 2)
    return int(value) if value == int(value) else value


def claude_pick_cover(candidates, current, *, focus_only=False, model=None, client=None):
    """Claude names the cover among `candidates` and where to crop it: {"file", "focus": [x, y], "model"}.

    candidates: [{"file", "title", "room", "image": JPEG bytes}] in page order, each shown after a line naming it.
    current: the cover now (it is told to keep it unless another photo is clearly better), or None. focus_only: the
    one candidate is a cover a person picked; only where to crop it is asked. Failures are CuratorErrors, as in
    claude_draft; a refusal is CLAUDE_COVER_REFUSED. `model` in the result is the model that really answered."""
    model = model or claude_model_name()
    if client is None:
        client = _claude_client()
    files = [c["file"] for c in candidates]
    content = [{"type": "text", "text": COVER_FOCUS_HEAD if focus_only else COVER_HEAD}]
    for c in candidates:
        content += [{"type": "text", "text": f"사진 {c['file']}: {c['title']} (방: {c['room']})"}, _image_block(c["image"])]
    ask = COVER_FOCUS if focus_only else COVER_PICK
    if not focus_only and current in files:
        ask += "\n" + COVER_KEEP.format(file=current)
    content.append({"type": "text", "text": ask})
    text, answered = _claude_reply(client, model, cover_schema(files), content, CLAUDE_COVER_REFUSED)
    try:
        reply = _load_reply(text)
    except ValueError as err:
        raise _unreadable(str(err).partition(":")[0]) from None
    if reply.get("file") not in files:
        raise _unreadable("모르는 사진이에요")
    return {"file": reply["file"], "focus": [cover_focus(reply.get("focus_x")), cover_focus(reply.get("focus_y"))],
            "model": answered}
