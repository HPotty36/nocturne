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
e.g. IMG_0306_Edited.jpeg -> 0306. The conversion itself lives in photolib.py.
"""
import json
import sys
from pathlib import Path

import photolib

ROOT = Path(__file__).resolve().parent.parent
EXTS = {".jpg", ".jpeg", ".png"}


def main(root=ROOT):
    root = Path(root)
    force = "--force" in sys.argv
    originals = root / "originals"
    if not originals.exists():
        originals.mkdir()
    for size in photolib.SIZES:
        (root / "photos" / size).mkdir(parents=True, exist_ok=True)

    data = json.loads((root / "src" / "photos.json").read_text(encoding="utf-8"))
    listed = {p["file"] for room in data["rooms"] for p in room["photos"]}

    files = sorted(p for p in originals.iterdir() if p.suffix.lower() in EXTS)
    if not files:
        print("originals/ 폴더에 사진(jpg, jpeg, png)을 넣고 다시 실행하세요.")
        return

    stubs = []
    for path in files:
        name = photolib.name_for(path.name)
        targets = (photolib.thumb_path(root, name), photolib.full_path(root, name))
        if not force and all(t.exists() for t in targets):
            print(f"건너뜀 (이미 있음): {name}")
        else:
            hints = photolib.make_web_copies(path, root, name)["hints"]
            print(f"변환 완료: {path.name} -> {name}")
            if name not in listed:
                stubs.append({"file": name, "title": "", "alt": "", **hints})

    if stubs:
        print("\nsrc/photos.json 에 아직 없는 사진입니다. 원하는 방의 photos 목록에 붙여 넣으세요:\n")
        for s in stubs:
            print("  " + json.dumps(s, ensure_ascii=False) + ",")


if __name__ == "__main__":
    main()
