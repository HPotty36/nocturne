"""Build index.html (Korean) and en/index.html (English) from src/template.html and src/photos.json.

Usage:  python scripts/build.py [--out DIR]      (Windows: py scripts\\build.py)

Next to the pages it writes manifest.webmanifest (for installing the site) and sw.js (src/sw.js with its
version, photo list and cover filled in). The Korean page carries src/redirect.js inline. Without --out all of
these go to the project root. With --out they go to DIR and photos/, icons/ (and admin/, if present) are copied
next to them, which is the folder GitHub Pages publishes.

Reads each photo's size and average colour from photos/full and photos/thumb,
so run scripts/prepare_photos.py first for any new photo.
"""
import argparse
import hashlib
import html
import json
import re
import shutil
import sys
import urllib.parse
from pathlib import Path

from PIL import Image

import photolib

ROOT = Path(__file__).resolve().parent.parent

COPIED = ("photos", "admin", "icons")  # folders published next to index.html with --out

# manifest.webmanifest: what a phone needs to install the site, the one app of this origin (the admin page has no
# manifest of its own: two apps on one origin clash on Android). Photos shared to it go to admin/share, where the
# site's worker keeps them for the admin page. The description is site.description with {total}.
MANIFEST = {
    "name": "Nocturne", "short_name": "Nocturne", "id": "/nocturne/", "start_url": "./", "scope": "./", "display": "standalone",
    "background_color": "#000000", "theme_color": "#000000", "lang": "ko", "description": "",
    "icons": [{"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
              {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
              {"src": "icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}],
    "share_target": {"action": "admin/share", "method": "POST", "enctype": "multipart/form-data",
                     "params": {"files": [{"name": "photos", "accept": ["image/jpeg", "image/png"]}]}},
}

# The three places in src/sw.js the build fills in; each must be there exactly once.
SW_VERSION, SW_PHOTOS, SW_COVER = "'__VERSION__'", "[/*__PHOTOS__*/]", "'' /*__COVER__*/"

# Screen text per language. `view` and `count` are templates: {title}, {n}.
STRINGS = {
    "ko": {"menu": "목록", "prev": "이전", "next": "다음", "close": "닫기", "admin": "관리", "nav": "전시실",
           "viewer": "사진 크게 보기", "switch": "English", "view": "{title} 크게 보기", "count": "{n}점"},
    "en": {"menu": "Index", "prev": "Previous", "next": "Next", "close": "Close", "admin": "Admin", "nav": "Rooms",
           "viewer": "Photo viewer", "switch": "한국어", "view": "View {title}", "count": "{n}"},
}

# Per page: where it is written, the prefix its asset and admin links need, the language toggle, and the
# hrefs of its <link rel="alternate"> lines (ko, en, x-default). The English page's toggle carries ?lang=ko, so
# the Korean page's first-visit rule (src/redirect.js) never sends someone who just chose Korean back to English.
PAGES = {
    "ko": {"file": "index.html", "base": "", "switch_href": "en/", "switch_lang": "en", "alternates": ("./", "en/", "./")},
    "en": {"file": "en/index.html", "base": "../", "switch_href": "../?lang=ko", "switch_lang": "ko", "alternates": ("../", "./", "../")},
}

COPY_PATHS = {"thumb": photolib.thumb_path, "full": photolib.full_path}

# Weight 300 sets only the site name and the room names, so the page asks Google Fonts for just their letters.
TITLE_FONT = "https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@300&text={text}&display=swap"


class BuildError(Exception):
    """The page cannot be built (e.g. a listed photo has no web copy)."""


def esc(s):
    return html.escape(str(s), quote=True)


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


def asset_url(root, kind, name):
    """Site-relative URL of a web copy, with the first 8 hex of the file's sha256 as ?v=.

    kind is "thumb" or "full". The hash changes the URL whenever the picture behind a name changes, so a
    browser or service worker never hands out the old one. A page adds its own base ("" or "../").
    """
    digest = hashlib.sha256(COPY_PATHS[kind](root, name).read_bytes()).hexdigest()[:8]
    return f"photos/{kind}/{name}.jpg?v={digest}"


def room_span(photos):
    """"2026.09 – 2026.10": the earliest and latest month of the photos' dates. One month gives just that month,
    no dated photo gives ""."""
    months = sorted({p["date"][:7] for p in photos if re.match(r"\d{4}\.\d{2}", p.get("date") or "")})
    if not months:
        return ""
    return months[0] if months[0] == months[-1] else f"{months[0]} – {months[-1]}"


def total_text(lang, n):
    """"사진 3점" / "3 photographs" ("1 photograph")."""
    if lang == "en":
        return f"{n} photograph" if n == 1 else f"{n} photographs"
    return f"사진 {n}점"


def pick(photo, field, lang):
    """(text, korean_fallback) of a photo's title or alt on the page of `lang`.

    The English page shows title_en / alt_en; without them it shows the Korean text, and says so (the caller
    marks that element lang="ko"), so it is read in the right voice and never comes out empty.
    """
    if lang == "en":
        english = photo.get(f"{field}_en")
        return (english, False) if english else (photo[field], True)
    return photo[field], False


def label_html(title, korean, date):
    """Wall label under a photo: the title, then ", date" (dimmed) if there is one."""
    shown = f'<span lang="ko">{esc(title)}</span>' if korean else esc(title)
    when = f'<span class="label-date">, {esc(date)}</span>' if date else ""
    return f'<figcaption class="label">{shown}{when}</figcaption>'


def print_html(p, f, lang, base, lazy):
    """One photo in its room. f is its shared facts: size, average colour and hashed URLs."""
    name, date = esc(p["file"]), p.get("date") or ""
    title, title_ko = pick(p, "title", lang)
    alt, alt_ko = pick(p, "alt", lang)
    view = STRINGS[lang]["view"].format(title=title)
    title_lang = ' data-title-lang="ko"' if title_ko else ""
    alt_lang = ' lang="ko"' if alt_ko else ""
    loading = ' loading="lazy"' if lazy else ""
    # The button's aria-label names it, which hides the photo's alt; aria-describedby brings the alt back as
    # its description. The cover (also hung in its room) points at the same image, so ids stay unique.
    alt_id = f"alt-{name}"
    return (
        f'<figure class="print" data-slug="{name}" data-w="{f["w"]}" data-h="{f["h"]}" '
        f'data-thumb="{esc(base + f["thumb"])}" data-full="{esc(base + f["full"])}" '
        f'data-title="{esc(title)}" data-date="{esc(date)}"{title_lang} style="--c:{f["col"]};--r:{f["w"]}/{f["h"]}">'
        f'<button class="print-btn" type="button" aria-label="{esc(view)}" aria-describedby="{alt_id}">'
        f'<img id="{alt_id}" src="{esc(base + f["thumb"])}" alt="{esc(alt)}"{alt_lang} '
        f'width="{f["tw"]}" height="{f["th"]}"{loading} decoding="async">'
        f'</button>{label_html(title, title_ko, date)}</figure>'
    )


def focus_style(focus):
    """"--fx:42%;--fy:50%" for a cover_focus [x, y] (0 to 1, from the top left; kept inside 0 to 100%), or "" when
    there is none or it is not two numbers: the stylesheet then keeps the middle."""
    if not (isinstance(focus, list) and len(focus) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and v == v for v in focus)):
        return ""
    x, y = (min(100.0, max(0.0, round(v * 100, 2))) for v in focus)
    return f"--fx:{x:g}%;--fy:{y:g}%;"


def cover_html(p, f, lang, base, focus=None):
    """The cover print: the full-size copy, labelled like any print (title, date; no camera). It fills its frame,
    cropped around `focus` (photos.json cover_focus)."""
    name, date = esc(p["file"]), p.get("date") or ""
    title, title_ko = pick(p, "title", lang)
    alt, alt_ko = pick(p, "alt", lang)
    view = STRINGS[lang]["view"].format(title=title)
    alt_lang = ' lang="ko"' if alt_ko else ""
    return (
        f'<figure class="cover"><button class="cover-btn" type="button" data-open="{name}" '
        f'style="--c:{f["col"]};--r:{f["w"]}/{f["h"]};{focus_style(focus)}" aria-label="{esc(view)}" aria-describedby="alt-{name}">'
        f'<img src="{esc(base + f["full"])}" alt="{esc(alt)}"{alt_lang} '
        f'width="{f["w"]}" height="{f["h"]}" fetchpriority="high"></button>{label_html(title, title_ko, date)}</figure>'
    )


def room_name(room, lang):
    """Room name on the page of `lang`: name_en on the English page, falling back to the Korean name."""
    return (room.get("name_en") or room["name"]) if lang == "en" else room["name"]


def title_font_href(data):
    """The weight-300 stylesheet for the site name and every room name (Korean and English), asking for only their
    letters: each once, in code point order, percent-encoded."""
    names = [data["site"]["title"]] + [room.get(key) or "" for room in data["rooms"] for key in ("name", "name_en")]
    text = "".join(sorted(set("".join(names))))
    return TITLE_FONT.format(text=urllib.parse.quote(text, safe=""))


def room_html(room, facts, lang, base, first):
    """One room: its head (the name, then count and span) and the hang of its prints. Only the first room loads eagerly."""
    count = STRINGS[lang]["count"].format(n=len(room["photos"]))
    span = room_span(room["photos"])
    span_html = f'<span class="room-span">{esc(span)}</span>' if span else ""
    prints = "".join(f"\n  {print_html(p, facts[p['file']], lang, base, not first)}" for p in room["photos"])
    return (f'<section class="room" id="{esc(room["id"])}"><header class="room-head"><h2>{esc(room_name(room, lang))}</h2>'
            f'<p class="room-meta"><span class="room-count">{esc(count)}</span>{span_html}</p></header>'
            f'<div class="hang">{prints}\n</div></section>')


def render_page(template, data, facts, lang, redirect):
    """The template filled for one language. Everything from photos.json is escaped on the way in.

    redirect is the text of src/redirect.js. Only the Korean page (the site root, which an installed app opens) carries
    it, inline: it sends a visitor to the English page when English was chosen before, or when nothing was chosen and
    the browser is not set to Korean.
    """
    site, rooms = data["site"], data["rooms"]
    page, strings = PAGES[lang], STRINGS[lang]
    base, total = page["base"], sum(len(r["photos"]) for r in rooms)
    nav = "".join(f'<li><a href="#{esc(r["id"])}">{esc(room_name(r, lang))}</a></li>' for r in rooms)
    cover = ""
    for room in rooms:
        for p in room["photos"]:
            if p["file"] == data.get("cover"):
                cover = cover_html(p, facts[p["file"]], lang, base, data.get("cover_focus"))
    description = site["description_en"] if lang == "en" and site.get("description_en") else site["description"]
    values = {
        "__LANG__": lang,
        "__REDIRECT__": f"<script>{redirect}</script>" if lang == "ko" else "",
        "__DESCRIPTION__": esc(description.format(total=total)),
        "__TITLE__": esc(site["title"]),
        "__TITLEFONT__": esc(title_font_href(data)),
        "__ALTERNATES__": "\n".join(f'<link rel="alternate" hreflang="{code}" href="{href}">'
                                    for code, href in zip(("ko", "en", "x-default"), page["alternates"])),
        "__NAVLABEL__": esc(strings["nav"]),
        "__MENU__": esc(strings["menu"]),
        "__NAV__": nav,
        "__SWITCHHREF__": page["switch_href"],
        "__SWITCHLANG__": page["switch_lang"],
        "__SWITCH__": esc(strings["switch"]),
        "__TOTALTEXT__": esc(total_text(lang, total)),
        "__COPYRIGHT__": esc(site["copyright"]),
        "__BASE__": base,
        "__ADMIN__": esc(strings["admin"]),
        "__COVER__": cover,
        "__ROOMS__": "\n".join(room_html(r, facts, lang, base, i == 0) for i, r in enumerate(rooms)),
        "__VIEWERLABEL__": esc(strings["viewer"]),
        "__PREV__": esc(strings["prev"]),
        "__NEXT__": esc(strings["next"]),
        "__CLOSE__": esc(strings["close"]),
    }
    # one pass, so text from photos.json can never be read as a placeholder
    return re.sub(r"__[A-Z]+__", lambda m: values[m.group(0)], template)


def manifest_text(description):
    """manifest.webmanifest: MANIFEST with the site description (its {total} already filled in)."""
    return json.dumps({**MANIFEST, "description": description}, ensure_ascii=False, indent=2) + "\n"


def icon_files(root):
    """[(name, bytes)] of every file under icons/, in name order (an empty list when there is no icons/ folder)."""
    folder = Path(root) / "icons"
    if not folder.is_dir():
        return []
    return [(p.relative_to(folder).as_posix(), p.read_bytes()) for p in sorted(folder.rglob("*"), key=lambda p: p.relative_to(folder).as_posix()) if p.is_file()]


def sw_text(template, pages, manifest, photos, cover, icons=()):
    """sw.js: the src/sw.js template with VERSION, PHOTOS and COVER filled in.

    VERSION is the first 12 hex of a sha256 over the Korean page, the English page, the manifest, the template and
    every icon (name and bytes, in name order), so the browser takes a new service worker whenever anything on the
    site changes (and only then): a new icon alone is enough. PHOTOS is the {thumb, full} URLs of every photo on
    show, in page order. COVER is the full-size URL of the cover ("" when none is hung): the cover page loads it
    before the worker controls the page, so the worker fetches it itself.
    """
    for mark in (SW_VERSION, SW_PHOTOS, SW_COVER):
        if template.count(mark) != 1:
            raise BuildError(f"src/sw.js 에 {mark} 가 꼭 한 번 있어야 합니다.")
    digest = hashlib.sha256()
    for text in (pages["ko"], pages["en"], manifest, template):
        digest.update(text.encode("utf-8"))
    for name, data in icons:
        digest.update(name.encode("utf-8") + b"\x00" + data)
    return (template.replace(SW_VERSION, f"'{digest.hexdigest()[:12]}'").replace(SW_PHOTOS, json.dumps(photos))
            .replace(SW_COVER, json.dumps(cover)))


def build(root, out=None):
    """Write index.html, en/index.html, manifest.webmanifest and sw.js to root, or to out with photos/, icons/ and
    admin/ copied.

    Returns {"total": photos on show, "rooms": number of rooms}.
    """
    root = Path(root)
    data = photolib.load_data(root)
    rooms = data["rooms"]
    total = sum(len(r["photos"]) for r in rooms)

    facts = {}  # per photo, the same on both pages; photo_info checks that both web copies exist before they are hashed
    for room in rooms:
        for p in room["photos"]:
            name = p["file"]
            w, h, tw, th, col = photo_info(root, name)
            facts[name] = {"w": w, "h": h, "tw": tw, "th": th, "col": col,
                           "thumb": asset_url(root, "thumb", name), "full": asset_url(root, "full", name)}

    template = (root / "src" / "template.html").read_text(encoding="utf-8")
    redirect = (root / "src" / "redirect.js").read_text(encoding="utf-8").strip()
    pages = {lang: render_page(template, data, facts, lang, redirect) for lang in PAGES}
    manifest = manifest_text(data["site"]["description"].format(total=total))
    photos = [{"thumb": facts[p["file"]]["thumb"], "full": facts[p["file"]]["full"]} for r in rooms for p in r["photos"]]
    cover = facts[data["cover"]]["full"] if data.get("cover") in facts else ""  # only a cover that is hung counts
    sw = sw_text((root / "src" / "sw.js").read_text(encoding="utf-8"), pages, manifest, photos, cover, icon_files(root))

    dest = root if out is None else Path(out)
    files = {**{page["file"]: pages[lang] for lang, page in PAGES.items()}, "manifest.webmanifest": manifest, "sw.js": sw}
    for name, text in files.items():
        path = dest / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    if out is not None:
        for folder in COPIED:
            if (root / folder).is_dir():
                shutil.copytree(root / folder, dest / folder, dirs_exist_ok=True)
    return {"total": total, "rooms": len(rooms)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="src/photos.json 으로 index.html 과 en/index.html 을 만듭니다.")
    parser.add_argument("--out", type=Path, help="두 페이지, manifest, sw.js 와 photos/, icons/, admin/ 을 이 폴더에 씁니다")
    args = parser.parse_args(argv)
    try:
        result = build(ROOT, args.out)
    except BuildError as e:
        print(e, file=sys.stderr)
        sys.exit(1)
    print(f"index.html, en/index.html, manifest.webmanifest, sw.js 생성 완료: 사진 {result['total']}점, 방 {result['rooms']}개")
    if args.out is not None:
        print(f"출력 폴더: {args.out}")


if __name__ == "__main__":
    main()
