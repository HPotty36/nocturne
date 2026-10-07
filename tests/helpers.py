"""Shared helpers for the test suite: paths, shared cases, JPEG builders, and theme tokens."""
import json
import re
import shutil
import struct
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

# Importing this module is enough for tests to `import photolib`.
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))


# Same content as tests/fixtures/site.json; the JavaScript tests read that file.
SITE = {
    "site": {"title": "Nocturne", "lede": "사진 {total}점", "description": "사진 {total}점", "copyright": "© 2026 Nocturne"},
    "cover": "a1",
    "rooms": [
        {"id": "river", "name": "강", "name_en": "River", "note": "물가에서 바라본 도시",
         "photos": [{"file": "a1", "title": "스카이라인", "alt": "강 건너 빌딩"},
                    {"file": "a2", "title": "다리 아래", "alt": "다리 상판",
                     "ai": {"model": "gemma4:e4b-it-qat", "fields": ["title", "alt"], "confidence": 0.61}}]},
        {"id": "night", "name": "밤", "name_en": "Night", "note": "가로등 아래",
         "photos": [{"file": "n1", "title": "공중전화", "alt": "빨간 공중전화 부스", "date": "2025.11.30"}]},
    ],
}


def load_cases():
    """cases.json is shared with the JavaScript tests."""
    return json.loads((ROOT / "tests" / "cases.json").read_text(encoding="utf-8"))


def expand_gray_case(case):
    """Chroma (max - min) of every pixel of a grayscale case: spots first, then base."""
    chroma = lambda rgb: max(rgb) - min(rgb)
    values = []
    for spot in case["spots"]:
        values += [chroma(spot["rgb"])] * spot["n"]
    values += [chroma(case["base"])] * (case["count"] - len(values))
    return values


def make_jpeg(path, size=(1200, 800), color=(200, 40, 40), *, camera=None, gps=False, orientation=None):
    """Write a solid-colour JPEG, optionally with camera model, GPS and orientation EXIF."""
    exif = Image.Exif()
    if camera:
        exif[0x0110] = camera
    if orientation:
        exif[0x0112] = orientation
    if gps:  # zero coordinates, never a real place
        place = exif.get_ifd(0x8825)
        place[1], place[2] = "N", (0.0, 0.0, 0.0)
        place[3], place[4] = "E", (0.0, 0.0, 0.0)
    kwargs = {"exif": exif} if (camera or gps or orientation) else {}
    Image.new("RGB", size, color).save(path, "JPEG", quality=90, **kwargs)
    return Path(path)


# TIFF field types used below: (type id, bytes per item)
_ASCII, _SHORT, _LONG, _RATIONAL = (2, 1), (3, 2), (4, 4), (5, 8)


def _field(tag, kind, value, bo):
    """One IFD entry as (tag, type id, count, raw bytes of its value)."""
    if kind is _ASCII:
        raw = value.encode("ascii") + b"\x00"
        return tag, kind[0], len(raw), raw
    if kind is _SHORT:
        return tag, kind[0], 1, struct.pack(bo + "H", value)
    if kind is _LONG:
        return tag, kind[0], 1, struct.pack(bo + "I", value)
    raw = b"".join(struct.pack(bo + "II", n, d) for n, d in value)
    return tag, kind[0], len(value), raw


def _ifd_size(fields):
    out_of_line = sum(len(raw) + len(raw) % 2 for _, _, _, raw in fields if len(raw) > 4)
    return 2 + 12 * len(fields) + 4 + out_of_line


def _ifd_bytes(fields, start, bo):
    """Serialise an IFD that begins `start` bytes after the TIFF header."""
    entries, tail = b"", b""
    tail_at = start + 2 + 12 * len(fields) + 4
    for tag, kind, count, raw in fields:
        if len(raw) <= 4:
            entries += struct.pack(bo + "HHI", tag, kind, count) + raw.ljust(4, b"\x00")
        else:
            entries += struct.pack(bo + "HHII", tag, kind, count, tail_at + len(tail))
            tail += raw + b"\x00" * (len(raw) % 2)
    return struct.pack(bo + "H", len(fields)) + entries + struct.pack(bo + "I", 0) + tail


def make_exif_jpeg(path, byteorder):
    """64x48 grey JPEG with a hand-built APP1 right after SOI; byteorder is "<" or ">".

    Holds a camera model, capture time, f/1.6, 1/121s, ISO 100 and a zero GPS latitude,
    so both TIFF byte orders can be checked in Python and in JavaScript.
    """
    bo = byteorder
    when = "2025:11:30 23:13:46"
    ifd0 = [
        _field(0x0110, _ASCII, "TestCam", bo),
        _field(0x0132, _ASCII, when, bo),
        _field(0x8769, _LONG, 0, bo),  # Exif IFD pointer, patched below
        _field(0x8825, _LONG, 0, bo),  # GPS IFD pointer, patched below
    ]
    sub = [
        _field(0x829A, _RATIONAL, [(1, 121)], bo),
        _field(0x829D, _RATIONAL, [(16, 10)], bo),
        _field(0x8827, _SHORT, 100, bo),
        _field(0x9003, _ASCII, when, bo),
    ]
    gps = [
        _field(0x0001, _ASCII, "N", bo),
        _field(0x0002, _RATIONAL, [(0, 1), (0, 1), (0, 1)], bo),
    ]
    sub_at = 8 + _ifd_size(ifd0)
    gps_at = sub_at + _ifd_size(sub)
    ifd0[2] = _field(0x8769, _LONG, sub_at, bo)
    ifd0[3] = _field(0x8825, _LONG, gps_at, bo)
    tiff = (b"II" if bo == "<" else b"MM") + struct.pack(bo + "HI", 42, 8)
    tiff += _ifd_bytes(ifd0, 8, bo) + _ifd_bytes(sub, sub_at, bo) + _ifd_bytes(gps, gps_at, bo)
    payload = b"Exif\x00\x00" + tiff
    app1 = b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload

    plain = Path(path).with_suffix(".plain.tmp")
    Image.new("L", (64, 48), 128).save(plain, "JPEG", quality=90)
    data = plain.read_bytes()
    plain.unlink()
    Path(path).write_bytes(data[:2] + app1 + data[2:])
    return Path(path)


def make_site(tmp):
    """Small site tree under tmp: the real template, SITE as photos.json, and a JPEG pair per photo."""
    import photolib

    tmp = Path(tmp)
    (tmp / "src").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "src" / "template.html", tmp / "src" / "template.html")
    (tmp / "src" / "photos.json").write_text(photolib.dumps_data(SITE), encoding="utf-8", newline="\n")
    for room in SITE["rooms"]:
        for photo in room["photos"]:
            sizes = ((photolib.thumb_path(tmp, photo["file"]), (300, 200)), (photolib.full_path(tmp, photo["file"]), (600, 400)))
            for path, size in sizes:
                path.parent.mkdir(parents=True, exist_ok=True)
                make_jpeg(path, size)
    return tmp


class FakeAI:
    """Stands in for curator.draft in tests: same call signature, no Ollama, counts its calls."""

    def __init__(self, room="night", confidence=0.95, fail=False):
        self.room, self.confidence, self.fail, self.calls = room, confidence, fail, 0

    def __call__(self, image, data, grayscale, *, model=None):
        import curator

        self.calls += 1
        if self.fail:
            raise curator.CuratorError("Ollama가 꺼져 있어요. Ollama를 켠 뒤 다시 시도하거나 직접 입력하세요.")
        return {"room": self.room, "title": "초안", "alt": "초안 설명",
                "confidence": self.confidence, "model": model or "fake"}


# --- colour tokens in a stylesheet ---------------------------------------------------------

def _declarations(block):
    """Custom properties (--name: value) declared directly in a CSS block body."""
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+?)\s*;", block))


def _block_body(css, selector):
    """Body of the first `selector { ... }` block (selector text matched exactly), braces balanced."""
    m = re.search(r"(?:^|[}\s])" + re.escape(selector) + r"\s*\{", css)
    if not m:
        raise AssertionError(f"no {selector!r} block")
    depth = 1
    for i in range(m.end(), len(css)):
        depth += {"{": 1, "}": -1}.get(css[i], 0)
        if depth == 0:
            return css[m.end():i]
    raise AssertionError(f"unclosed {selector!r} block")


def theme_tokens(path):
    """Colour tokens of a page or stylesheet as (dark, light by media query, light by data-theme).

    dark: the first `:root { }` block; light by media query: the rule inside
    `@media (prefers-color-scheme: light)`; light by data-theme: `:root[data-theme="light"] { }`.
    """
    css = re.sub(r"/\*.*?\*/", "", Path(path).read_text(encoding="utf-8"), flags=re.S)
    media = _block_body(css, "@media (prefers-color-scheme: light)")
    return (_declarations(_block_body(css, ":root")),
            _declarations(media[media.index("{") + 1:media.rindex("}")]),
            _declarations(_block_body(css, ':root[data-theme="light"]')))


def contrast(a_hex, b_hex):
    """WCAG 2 contrast ratio of two #rrggbb colours."""
    def luminance(h):
        h = h.lstrip("#")
        c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        c = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted((luminance(a_hex), luminance(b_hex)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)
