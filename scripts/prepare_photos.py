"""Turn original photos into web-ready copies with all metadata removed.

Usage:  python scripts/prepare_photos.py [--force]   (Windows: py scripts\\prepare_photos.py)

1. Put original files (jpg, jpeg, png) in originals/  (this folder is git-ignored).
2. Run this script. For each original it writes
     photos/thumb/<name>.jpg   long edge 1000px, for the grid
     photos/full/<name>.jpg    long edge 2400px, for the full-screen view
   EXIF (including GPS location) is dropped; only the colour profile is kept.
3. It prints a JSON entry for every photo that is not yet in src/photos.json.
   Paste those into the right room, fill in title and alt, then run build.py.

<name> is the file name in lower case without "IMG_" and "_Edited",
e.g. IMG_0306_Edited.jpeg -> 0306.
"""
import json
import sys
from pathlib import Path

from PIL import ExifTags, Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
ORIGINALS = ROOT / "originals"
OUT = {"thumb": (1000, 78), "full": (2400, 84)}
EXTS = {".jpg", ".jpeg", ".png"}


def name_for(path):
    stem = path.stem
    for token in ("_Edited", "_edited"):
        stem = stem.replace(token, "")
    if stem.upper().startswith("IMG_"):
        stem = stem[4:]
    return stem.lower()


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


def main():
    force = "--force" in sys.argv
    if not ORIGINALS.exists():
        ORIGINALS.mkdir()
    for d in OUT:
        (ROOT / "photos" / d).mkdir(parents=True, exist_ok=True)

    data = json.loads((ROOT / "src" / "photos.json").read_text(encoding="utf-8"))
    listed = {p["file"] for room in data["rooms"] for p in room["photos"]}

    files = sorted(p for p in ORIGINALS.iterdir() if p.suffix.lower() in EXTS)
    if not files:
        print("originals/ 폴더에 사진(jpg, jpeg, png)을 넣고 다시 실행하세요.")
        return

    stubs = []
    for path in files:
        name = name_for(path)
        targets = {d: ROOT / "photos" / d / f"{name}.jpg" for d in OUT}
        if not force and all(t.exists() for t in targets.values()):
            print(f"건너뜀 (이미 있음): {name}")
        else:
            im = Image.open(path)
            hints = exif_hints(im)
            icc = im.info.get("icc_profile")
            im = ImageOps.exif_transpose(im).convert("RGB")
            for d, (edge, quality) in OUT.items():
                copy = im.copy()
                copy.thumbnail((edge, edge), Image.LANCZOS)
                kwargs = {"quality": quality, "optimize": True, "progressive": True}
                if icc:
                    kwargs["icc_profile"] = icc
                copy.save(targets[d], "JPEG", **kwargs)
            print(f"변환 완료: {path.name} -> {name}")
            if name not in listed:
                stubs.append({"file": name, "title": "", "alt": "", **hints})

    if stubs:
        print("\nsrc/photos.json 에 아직 없는 사진입니다. 원하는 방의 photos 목록에 붙여 넣으세요:\n")
        for s in stubs:
            print("  " + json.dumps(s, ensure_ascii=False) + ",")


if __name__ == "__main__":
    main()
