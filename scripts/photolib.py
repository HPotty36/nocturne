"""Shared photo helpers: file names, EXIF hints, grayscale check, metadata-free web copies.

Used by prepare_photos.py and by the admin tools. Dependencies: standard library + Pillow.
"""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from PIL import ExifTags, Image, ImageOps

# name -> (long edge in px, JPEG quality)
SIZES = {"thumb": (1000, 78), "full": (2400, 84)}

AI_FIELDS = ("title", "alt", "title_en", "alt_en")  # the photo texts an AI may write, in the order ai.fields lists them

GRAY_MEAN_LIMIT = 4  # average chroma must stay below this
GRAY_EDGE_LIMIT = 16  # chroma at the 99th percentile must stay below this
GRAY_SAMPLE_EDGE = 256  # long edge of the downsized copy that is measured


class PhotoError(ValueError):
    """A photo cannot be used (unreadable, wrong type, bad name)."""


def name_for(filename):
    """Web name for an original file name: IMG_0306_Edited.jpeg -> 0306. "edited" as a separate word, in any case,
    goes with its separator and brackets: "a (EDITED)" -> a."""
    stem = Path(filename).stem
    # re.ASCII, like JS /i: dotless i, dotted I, long s and the Kelvin sign do not count as i, s, k
    stem = re.sub(r"[ _.-]*\(?(?<![A-Za-z0-9])edited(?![A-Za-z0-9])\)?", "", stem, flags=re.IGNORECASE | re.ASCII)
    if stem.upper().startswith("IMG_"):
        stem = stem[4:]
    stem = re.sub(r"[^a-z0-9_-]+", "_", stem.lower()).strip("_-")
    return stem or "photo"


def exif_hints(im):
    """Date and camera line from EXIF, read before the metadata is dropped."""
    ex = im.getexif()
    sub = ex.get_ifd(0x8769)
    tag = lambda d, name: next((v for k, v in d.items() if ExifTags.TAGS.get(k) == name), None)
    hints = {}
    when = tag(sub, "DateTimeOriginal") or tag(ex, "DateTime")
    if when:
        hints["date"] = str(when)[:10].replace(":", ".")
    model = tag(ex, "Model")
    if model:
        parts = [str(model).strip()]
        f = tag(sub, "FNumber")
        t = tag(sub, "ExposureTime")
        iso = tag(sub, "ISOSpeedRatings")
        if f:
            parts.append(f"f/{float(f):g}")
        if t:
            t = float(t)
            parts.append(f"1/{round(1 / t)}s" if 0 < t < 1 else f"{t:g}s")
        if iso:
            parts.append(f"ISO {iso}")
        hints["camera"] = " · ".join(parts)
    return hints


def grayscale_from_chroma(values):
    """True when per-pixel chroma (max - min of R, G, B) says the picture is black and white.

    A few coloured pixels (a sign, a lamp) are allowed: the mean must be low and the
    99th-percentile pixel must be nearly neutral.
    """
    if not values:
        return False
    if sum(values) / len(values) >= GRAY_MEAN_LIMIT:
        return False
    ordered = sorted(values)
    return ordered[int(0.99 * (len(ordered) - 1))] < GRAY_EDGE_LIMIT


def is_grayscale(im):
    """Whether an image is black and white, judged on a 256px copy."""
    small = im.convert("RGB")
    small.thumbnail((GRAY_SAMPLE_EDGE, GRAY_SAMPLE_EDGE))
    raw = small.tobytes()
    chroma = [max(px) - min(px) for px in zip(raw[0::3], raw[1::3], raw[2::3])]
    return grayscale_from_chroma(chroma)


def thumb_path(root, name):
    return Path(root) / "photos" / "thumb" / f"{name}.jpg"


def full_path(root, name):
    return Path(root) / "photos" / "full" / f"{name}.jpg"


def make_web_copies(src, root, name):
    """Write photos/thumb and photos/full copies of src with all metadata dropped.

    EXIF (GPS, camera model), XMP, IPTC and comments are not written; only the colour profile is kept.
    Returns {"hints": EXIF date/camera read before dropping, "grayscale": bool}.
    """
    paths = {"thumb": thumb_path(root, name), "full": full_path(root, name)}
    with Image.open(src) as original:
        hints = exif_hints(original)
        icc = original.info.get("icc_profile")
        im = ImageOps.exif_transpose(original).convert("RGB")
    for size, (edge, quality) in SIZES.items():
        copy = im.copy()
        copy.thumbnail((edge, edge), Image.LANCZOS)
        # convert() and copy() carry the source's info along, and Pillow writes info["comment"] back as a COM
        # segment (an editor's "owner: ..." note); only what is passed below may reach the file.
        copy.info.clear()
        kwargs = {"quality": quality, "optimize": True, "progressive": True}
        if icc:
            kwargs["icc_profile"] = icc
        paths[size].parent.mkdir(parents=True, exist_ok=True)
        copy.save(paths[size], "JPEG", **kwargs)
    return {"hints": hints, "grayscale": is_grayscale(im)}


# --- src/photos.json: one canonical text format -----------------------------------------
# gallery.js writes the same text byte for byte, so a save from either side makes no diff.

_PHOTO_PATH = ("rooms", "*", "photos", "*")  # these objects go on one line each
COVER_KEYS = ("cover", "cover_focus", "cover_pick")  # the cover, where to crop it, who picked it: right after "site"
_ONE_LINE = {_PHOTO_PATH, ("cover_focus",), ("cover_pick",)}


def _top_order(data):
    """data with the cover keys it has moved right after "site" (first if there is no "site"); the other keys keep
    their order. Anything but a dict is returned as it is."""
    if not isinstance(data, dict):
        return data
    cover = [(k, data[k]) for k in COVER_KEYS if k in data]
    rest = [(k, v) for k, v in data.items() if k not in COVER_KEYS]
    at = next((i + 1 for i, (k, _) in enumerate(rest) if k == "site"), 0)
    return dict(rest[:at] + cover + rest[at:])


def _dump(value, depth, path):
    if path in _ONE_LINE:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    pad = "  " * (depth + 1)
    if isinstance(value, dict) and value:
        lines = [f"{pad}{json.dumps(k, ensure_ascii=False)}: {_dump(v, depth + 1, path + (k,))}" for k, v in value.items()]
        return "{\n" + ",\n".join(lines) + "\n" + "  " * depth + "}"
    if isinstance(value, list) and value:
        lines = [f"{pad}{_dump(v, depth + 1, path + ('*',))}" for v in value]
        return "[\n" + ",\n".join(lines) + "\n" + "  " * depth + "]"
    return json.dumps(value, ensure_ascii=False)


def dumps_data(data):
    """photos.json text: 2-space indent, one line per photo (and for cover_focus, cover_pick), Korean left unescaped,
    final newline. The cover keys sit right after "site"."""
    return _dump(_top_order(data), 0, ()) + "\n"


def _data_path(root):
    return Path(root) / "src" / "photos.json"


def load_data(root):
    return json.loads(_data_path(root).read_text(encoding="utf-8"))


def save_data(root, data):
    """Write photos.json through a temporary file in the same folder, so a crash never leaves half a file."""
    path = _data_path(root)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="photos.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(dumps_data(data))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def listed(data):
    """File names of every photo that is registered in a room."""
    return {p["file"] for room in data["rooms"] for p in room["photos"]}


def fingerprint(data):
    """8 hex naming the set of photos on show: sha256 of their file names, sorted, one per line. The cover step
    (scripts/cover.py) keeps the one it picked for, so it picks again only when photos come or go."""
    return hashlib.sha256("\n".join(sorted(listed(data))).encode("utf-8")).hexdigest()[:8]


def find_photo(data, name):
    """(room, photo) for a registered file name."""
    for room in data["rooms"]:
        for photo in room["photos"]:
            if photo["file"] == name:
                return room, photo
    raise PhotoError(f"없는 사진이에요: {name}")


def add_photo(data, room_id, entry):
    """Append entry to the end of a room."""
    room = next((r for r in data["rooms"] if r["id"] == room_id), None)
    if room is None:
        raise PhotoError(f"없는 방이에요: {room_id}")
    if entry["file"] in listed(data):
        raise PhotoError(f"이미 있는 사진 이름이에요: {entry['file']}")
    room["photos"].append(entry)


# --- the per-photo "ai" record: which texts are still untouched AI output, and who wrote them ---

def ai_entry(model, confidence):
    entry = {"model": model, "fields": list(AI_FIELDS)}
    if confidence is not None:
        entry["confidence"] = confidence
    return entry


def needs_upgrade(photo, from_models):
    """True while some text is still AI-written and the model that wrote it is one of `from_models` (a tuple of names,
    curator.UPGRADE_FROM): text by any other model (Claude, the PC's own) and text a person wrote are left alone."""
    ai = photo.get("ai")
    return bool(ai and ai.get("fields")) and ai.get("model") in from_models


def apply_ai_text(photo, texts, model, fields):
    """Replace only `fields` with texts[field] and record model as their author.

    ai.fields becomes the old fields plus `fields`, in AI_FIELDS order; other ai keys stay.
    """
    for field in fields:
        photo[field] = texts[field]
    ai = dict(photo.get("ai") or {})
    marked = set(ai.get("fields", ())) | set(fields)
    ai["model"] = model
    ai["fields"] = [f for f in AI_FIELDS if f in marked]
    photo["ai"] = ai
