"""Curator: a local vision model (Ollama) drafts the room, title and alt text of a photo.

Standard library only. Callers pass `post`/`get` in tests; by default they talk to Ollama over HTTP.
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
TIMEOUT = 300  # seconds for one generate call
CONFIDENT = 0.8  # room confidence at or above this is trusted without a second look
ATTEMPTS = 3  # this PC's Ollama sometimes drops a request right after generating; a retry works
TITLE_MAX = 40  # characters; the prompt asks for 2 to 8, this only stops a rambling reply
ALT_MAX = 200  # characters, before ", 흑백 사진" is added

OFF_MESSAGE = "Ollama가 꺼져 있어요. Ollama를 켠 뒤 다시 시도하거나 직접 입력하세요."

PROMPT_HEAD = "당신은 사진 전시 사이트의 큐레이터입니다. 이 사진을 아래 방 중 하나에 걸고, 한국어 제목과 대체 텍스트(alt)를 쓰세요."
PROMPT_RULES = """규칙:
- 제목은 2~8글자의 짧은 명사구.
- alt는 화면 낭독기용으로, 무엇이 찍혔는지 한 문장. "입니다"나 마침표로 끝내지 마세요.
- 방 설명 문구를 그대로 옮기지 말고, 이 사진에 보이는 것을 쓰세요.
- 도시, 건물, 경기장, 다리 같은 장소의 이름을 쓰지 마세요. 사진만 보고 장소를 짐작하지 마세요.
- 흑백인지 컬러인지는 쓰지 마세요.

JSON으로만 답하세요: {"room": "<방 id>", "title": "...", "alt": "..."}"""


class CuratorError(Exception):
    """The draft could not be made. retry=False means asking again right away cannot help."""

    def __init__(self, message, *, retry=True):
        super().__init__(message)
        self.retry = retry


def model_name():
    return os.environ.get("NOCTURNE_MODEL", DEFAULT_MODEL)


# --- prompt and reply ------------------------------------------------------------------------

def build_prompt(data):
    rooms = data["rooms"]
    examples = [f"- [{room['name']}] {p['title']} / {p['alt']}" for room in rooms for p in room["photos"][:2]]
    parts = [PROMPT_HEAD, "방:\n" + "\n".join(f"- {r['id']} ({r['name']}): {r['note']}" for r in rooms)]
    if examples:
        parts.append("기존 사진의 제목과 alt 예시 (말투를 맞추세요):\n" + "\n".join(examples))
    parts.append(PROMPT_RULES)
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


def parse_reply(text, room_ids):
    """{"room", "title", "alt", "known"} from the model's JSON; an unknown room falls back to the first one.
    The title is cut to TITLE_MAX characters and the alt to ALT_MAX (see clamp)."""
    found = re.search(r"\{.*\}", text or "", re.S)
    try:
        reply = json.loads(found.group(0)) if found else None
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        raise ValueError(f"JSON이 아니에요: {(text or '')[:80]!r}")
    fields = {}
    for key in ("title", "alt"):
        value = reply.get(key)
        fields[key] = value.strip() if isinstance(value, str) else ""
        if not fields[key]:
            raise ValueError(f"{key}가 비어 있어요")
    room = reply.get("room")
    room = room.strip() if isinstance(room, str) else ""
    known = room in room_ids
    return {"room": room if known else room_ids[0], "title": clamp(fields["title"], TITLE_MAX),
            "alt": clamp(fields["alt"], ALT_MAX), "known": known}


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
    """Ask the model for {"room", "title", "alt", "confidence", "model"}; confidence is None without logprobs."""
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
                    "confidence": confidence, "model": model}
    raise CuratorError(f"AI가 답을 주지 못했어요: {cause}")
