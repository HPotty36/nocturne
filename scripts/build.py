"""Build index.html from src/template.html and src/photos.json.

Usage:  python scripts/build.py      (Windows: py scripts\\build.py)

Reads each photo's size and average colour from photos/full and photos/thumb,
so run scripts/prepare_photos.py first for any new photo.
"""
import html
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
PHOTOS = ROOT / "photos"

COMMON_RATIOS = [(4, 3), (3, 4), (3, 2), (2, 3), (16, 9), (1, 1), (4, 5), (5, 4)]


def esc(s):
    return html.escape(str(s), quote=True)


def ratio_label(w, h):
    r = w / h
    for a, b in COMMON_RATIOS:
        if abs(r - a / b) / (a / b) < 0.02:
            return f"{a}:{b}"
    return f"{r:.2f}:1" if r >= 1 else f"1:{1 / r:.2f}"


def avg_color(path):
    """Placeholder colour shown while a photo loads."""
    r, g, b = Image.open(path).convert("RGB").resize((1, 1), Image.BOX).getpixel((0, 0))
    return f"#{r:02x}{g:02x}{b:02x}"


def photo_info(name):
    full = PHOTOS / "full" / f"{name}.jpg"
    thumb = PHOTOS / "thumb" / f"{name}.jpg"
    missing = [str(p.relative_to(ROOT)) for p in (full, thumb) if not p.exists()]
    if missing:
        sys.exit(f"사진 파일이 없습니다: {', '.join(missing)}\n"
                 f"originals/ 에 원본을 넣고 scripts/prepare_photos.py 를 먼저 실행하세요.")
    w, h = Image.open(full).size
    tw, th = Image.open(thumb).size
    return w, h, tw, th, avg_color(thumb)


def main():
    data = json.loads((SRC / "photos.json").read_text(encoding="utf-8"))
    site, rooms = data["site"], data["rooms"]
    total = sum(len(r["photos"]) for r in rooms)

    nav, rooms_html, cover_html = [], [], ""
    for room_i, room in enumerate(rooms):
        nav.append(f'        <a href="#{room["id"]}"><span>{esc(room["name"])}</span>'
                   f'<span class="n">{len(room["photos"])}</span></a>')
        prints = []
        for p in room["photos"]:
            name = p["file"]
            w, h, tw, th, col = photo_info(name)
            ratio = ratio_label(w, h)
            meta = " · ".join(x for x in [room["name"], p.get("date"), p.get("camera"), ratio] if x)
            lazy = "" if room_i == 0 else ' loading="lazy"'
            prints.append(
                f'      <button class="print" type="button" style="--c:{col};--r:{w}/{h}" '
                f'data-slug="{esc(name)}" data-w="{w}" data-h="{h}" '
                f'data-thumb="photos/thumb/{esc(name)}.jpg" data-full="photos/full/{esc(name)}.jpg" '
                f'data-title="{esc(p["title"])}" data-meta="{esc(meta)}" aria-label="{esc(p["title"])} 크게 보기">'
                f'<img src="photos/thumb/{esc(name)}.jpg" alt="{esc(p["alt"])}" width="{tw}" height="{th}"{lazy} decoding="async">'
                f'<span class="print-cap" aria-hidden="true">{esc(p["title"])}</span></button>'
            )
            if name == data.get("cover"):
                cover_meta = " · ".join(x for x in [p.get("date"), p.get("camera"), ratio] if x)
                cover_html = f'''  <figure class="cover">
    <button class="cover-btn" type="button" data-open="{esc(name)}" style="--c:{col}" aria-label="{esc(p["title"])} 크게 보기">
      <img src="photos/full/{esc(name)}.jpg" alt="{esc(p["alt"])}" width="{w}" height="{h}" fetchpriority="high">
    </button>
    <figcaption>
      <span class="label-title">{esc(p["title"])}</span>
      <span class="label-meta">{esc(cover_meta)}</span>
    </figcaption>
  </figure>'''
        rooms_html.append(f'''    <section class="room" id="{room["id"]}" aria-labelledby="{room["id"]}-h">
      <header class="room-head">
        <h2 id="{room["id"]}-h">{esc(room["name"])}<span class="room-en">{esc(room["name_en"])}</span></h2>
        <p class="room-count">작품 {len(room["photos"])}점</p>
        <p class="room-note">{esc(room["note"])}</p>
      </header>
      <div class="hang">
{chr(10).join(prints)}
      </div>
    </section>''')

    page = (SRC / "template.html").read_text(encoding="utf-8")
    for key, value in {
        "__TITLE__": esc(site["title"]),
        "__KICKER__": esc(site["kicker"]),
        "__LEDE__": esc(site["lede"].format(total=total)),
        "__DESCRIPTION__": esc(site["description"].format(total=total)),
        "__COPYRIGHT__": esc(site["copyright"]),
        "__NAV__": "\n".join(nav),
        "__COVER__": cover_html,
        "__ROOMS__": "\n".join(rooms_html),
        "__TOTAL__": str(total),
    }.items():
        page = page.replace(key, value)

    (ROOT / "index.html").write_text(page, encoding="utf-8")
    print(f"index.html 생성 완료: 사진 {total}점, 방 {len(rooms)}개")


if __name__ == "__main__":
    main()
