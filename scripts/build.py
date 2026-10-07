"""Build index.html from src/template.html and src/photos.json.

Usage:  python scripts/build.py [--out DIR]      (Windows: py scripts\\build.py)

Without --out the page is written to index.html at the project root. With --out the page
goes to DIR/index.html and photos/ (and admin/, if present) are copied next to it, which
is the folder GitHub Pages publishes.

Reads each photo's size and average colour from photos/full and photos/thumb,
so run scripts/prepare_photos.py first for any new photo.
"""
import argparse
import html
import re
import shutil
import sys
from pathlib import Path

from PIL import Image

import photolib

ROOT = Path(__file__).resolve().parent.parent

COMMON_RATIOS = [(4, 3), (3, 4), (3, 2), (2, 3), (16, 9), (1, 1), (4, 5), (5, 4)]
ROMAN = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
         (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
COPIED = ("photos", "admin")  # folders published next to index.html with --out


class BuildError(Exception):
    """The page cannot be built (e.g. a listed photo has no web copy)."""


def esc(s):
    return html.escape(str(s), quote=True)


def roman(n):
    """Room number as a roman numeral: 1 -> I, 14 -> XIV."""
    out = ""
    for value, letters in ROMAN:
        while n >= value:
            out += letters
            n -= value
    return out


def ratio_label(w, h):
    r = w / h
    for a, b in COMMON_RATIOS:
        if abs(r - a / b) / (a / b) < 0.02:
            return f"{a}:{b}"
    return f"{r:.2f}:1" if r >= 1 else f"1:{1 / r:.2f}"


def photo_info(root, name):
    """(full width, full height, thumb width, thumb height, average colour) of a web copy."""
    full, thumb = photolib.full_path(root, name), photolib.thumb_path(root, name)
    missing = [p.relative_to(root).as_posix() for p in (full, thumb) if not p.exists()]
    if missing:
        raise BuildError(f"사진 파일이 없습니다: {', '.join(missing)}\n"
                         f"originals/ 에 원본을 넣고 scripts/prepare_photos.py 를 먼저 실행하세요.")
    with Image.open(full) as im:
        w, h = im.size
    with Image.open(thumb) as im:
        tw, th = im.size
        r, g, b = im.convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
    return w, h, tw, th, f"#{r:02x}{g:02x}{b:02x}"


def label(title, meta):
    """Wall label under a photo: title on the left, meta (if any) on the right."""
    meta_html = f'<span class="label-meta">{esc(meta)}</span>' if meta else ""
    return f'<figcaption class="label"><span class="label-title">{esc(title)}</span>{meta_html}</figcaption>'


def build(root, out=None):
    """Write the page to root/index.html, or to out/index.html with photos/ and admin/ copied.

    Returns {"total": photos on show, "rooms": number of rooms}.
    """
    root = Path(root)
    data = photolib.load_data(root)
    site, rooms = data["site"], data["rooms"]
    total = sum(len(r["photos"]) for r in rooms)

    nav, rooms_html, cover_html = [], [], ""
    for room_i, room in enumerate(rooms):
        num, rid = roman(room_i + 1), esc(room["id"])
        # the numeral is decoration for a screen reader, which would read "I강" as one word
        nav.append(f'      <a href="#{rid}"><span class="num" aria-hidden="true">{num}</span>{esc(room["name"])}</a>')
        prints = []
        for p in room["photos"]:
            name = p["file"]
            w, h, tw, th, col = photo_info(root, name)
            meta = " · ".join(x for x in [room["name"], p.get("date"), p.get("camera"), ratio_label(w, h)] if x)
            lazy = "" if room_i == 0 else ' loading="lazy"'
            # The button's aria-label names it, which hides the photo's alt; aria-describedby brings the alt back as
            # its description. The cover (also hung in its room) points at the same image, so ids stay unique.
            alt_id = f"alt-{esc(name)}"
            prints.append(
                f'      <figure class="print" data-slug="{esc(name)}" data-w="{w}" data-h="{h}" '
                f'data-thumb="photos/thumb/{esc(name)}.jpg" data-full="photos/full/{esc(name)}.jpg" '
                f'data-title="{esc(p["title"])}" data-meta="{esc(meta)}" style="--c:{col};--r:{w}/{h}">'
                f'<button class="print-btn" type="button" aria-label="{esc(p["title"])} 크게 보기" aria-describedby="{alt_id}">'
                f'<img id="{alt_id}" src="photos/thumb/{esc(name)}.jpg" alt="{esc(p["alt"])}" width="{tw}" height="{th}"{lazy} decoding="async">'
                f'</button>{label(p["title"], p.get("date"))}</figure>'
            )
            if name == data.get("cover"):
                cover_meta = " · ".join(x for x in [p.get("date"), p.get("camera")] if x)
                cover_html = f'''  <figure class="cover">
    <button class="cover-btn" type="button" data-open="{esc(name)}" style="--c:{col};--r:{w}/{h}" aria-label="{esc(p["title"])} 크게 보기" aria-describedby="{alt_id}">
      <img src="photos/full/{esc(name)}.jpg" alt="{esc(p["alt"])}" width="{w}" height="{h}" fetchpriority="high">
    </button>
    {label(p["title"], cover_meta)}
  </figure>'''
        rooms_html.append(f'''  <section class="room" id="{rid}">
    <header class="room-head">
      <span class="room-num">{num}</span>
      <h2>{esc(room["name"])} <span class="room-en" lang="en">{esc(room["name_en"])}</span></h2>
      <p class="room-count">{len(room["photos"])}점</p>
      <p class="room-note">{esc(room["note"])}</p>
    </header>
    <div class="hang">
{chr(10).join(prints)}
    </div>
  </section>''')

    values = {
        "__TITLE__": esc(site["title"]),
        "__DESCRIPTION__": esc(site["description"].format(total=total)),
        "__LEDE__": esc(site["lede"].format(total=total)),
        "__COPYRIGHT__": esc(site["copyright"]),
        "__NAV__": "\n".join(nav),
        "__COVER__": cover_html,
        "__ROOMS__": "\n".join(rooms_html),
        "__TOTAL__": str(total),
    }
    template = (root / "src" / "template.html").read_text(encoding="utf-8")
    # one pass, so text from photos.json can never be read as a placeholder
    page = re.sub(r"__[A-Z]+__", lambda m: values[m.group(0)], template)

    dest = root if out is None else Path(out)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "index.html").write_text(page, encoding="utf-8", newline="\n")
    if out is not None:
        for folder in COPIED:
            if (root / folder).is_dir():
                shutil.copytree(root / folder, dest / folder, dirs_exist_ok=True)
    return {"total": total, "rooms": len(rooms)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="src/photos.json 으로 index.html 을 만듭니다.")
    parser.add_argument("--out", type=Path, help="index.html 과 photos/, admin/ 을 이 폴더에 씁니다")
    args = parser.parse_args(argv)
    try:
        result = build(ROOT, args.out)
    except BuildError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    print(f"index.html 생성 완료: 사진 {result['total']}점, 방 {result['rooms']}개")
    if args.out is not None:
        print(f"출력 폴더: {args.out}")


if __name__ == "__main__":
    main()
