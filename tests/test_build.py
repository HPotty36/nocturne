import hashlib
import json
import re
import shutil
import tempfile
import unittest
import urllib.parse
from pathlib import Path

import helpers  # puts scripts/ on sys.path
import build
import photolib


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.root = helpers.make_site(Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def html(self):
        build.build(self.root)
        return (self.root / "index.html").read_text(encoding="utf-8")

    def en(self):
        build.build(self.root)
        return (self.root / "en" / "index.html").read_text(encoding="utf-8")

    def test_page(self):
        html = self.html()
        for s in ('<html lang="ko">', "<title>Nocturne</title>", '<a class="mark" href="#top">Nocturne</a>',
                  '<li><a href="#river">강</a></li>', '<h2>강</h2><p class="room-meta"><span class="room-count"><span class="room-n">2</span>점</span></p>',
                  '<h2>밤</h2><p class="room-meta"><span class="room-count"><span class="room-n">1</span>점</span>', '<summary>목록</summary>', '<nav aria-label="전시실">',
                  '<a class="lang" href="en/" lang="en" hreflang="en">English</a>', '사진 3점<br>© 2026 Nocturne<br>'):
            self.assertIn(s, html)
        for gone in ("room-num", "room-en", "room-note", "lede", "River", "Night", "label-meta", "data-meta", "lb-btn"):
            self.assertNotIn(gone, html)
        self.assertEqual(html.count('<figure class="print"'), 3)
        self.assertEqual(html.count('<li><a href="#night">밤</a></li>'), 2)  # phone menu and side list
        self.assertIsNone(re.search(r"__[A-Z]+__", html))

    def test_english_page(self):
        html = self.en()
        for s in ('<html lang="en">', '<li><a href="#river">River</a></li>', '<h2>Night</h2><p class="room-meta"><span class="room-count"><span class="room-n">1</span></span>',
                  '<summary>Index</summary>', '<nav aria-label="Rooms">', 'aria-label="Photo viewer"', '>Previous<', '>Next<', '>Close<',
                  '<a class="lang" href="../?lang=ko" lang="ko" hreflang="ko">한국어</a>', '3 photographs<br>',
                  '<a class="foot-admin" href="../admin/">Admin</a>', '<meta name="description" content="3 photographs">'):
            self.assertIn(s, html)
        self.assertIn('aria-label="View Payphone"', html)
        self.assertIn('<figcaption class="label">Payphone<span class="label-date">, 2025.11.30</span></figcaption>', html)
        self.assertRegex(html, r'<img id="alt-n1" src="\.\./photos/thumb/n1\.jpg\?v=[0-9a-f]{8}" alt="A red payphone booth"')
        self.assertIsNone(re.search(r'(src|data-thumb|data-full)="photos/', html))  # every asset path starts with ../
        self.assertIsNone(re.search(r"__[A-Z]+__", html))

    def test_english_falls_back_to_korean(self):
        html = self.en()
        self.assertRegex(html, r'data-slug="a2"[^>]*data-title="다리 아래"[^>]*data-title-lang="ko"')
        self.assertIn('<figcaption class="label"><span lang="ko">다리 아래</span></figcaption>', html)
        self.assertRegex(html, r'<img id="alt-a2"[^>]*alt="다리 상판"[^>]*lang="ko"')
        self.assertNotIn('data-title-lang', re.search(r'<figure class="print" data-slug="n1".*?</figure>', html, re.S).group(0))

    def test_alternates_and_redirect(self):
        ko, en = self.html(), self.en()
        for page, links in ((ko, ("./", "en/", "./")), (en, ("../", "./", "../"))):
            for lang, href in zip(("ko", "en", "x-default"), links):
                self.assertIn(f'<link rel="alternate" hreflang="{lang}" href="{href}">', page)
        redirect = (helpers.ROOT / "src" / "redirect.js").read_text(encoding="utf-8").strip()
        self.assertIn(f"<script>{redirect}</script>", ko)  # src/redirect.js itself, inlined
        self.assertLess(ko.index(redirect), ko.index("<title>"))
        self.assertNotIn("location.replace", en)
        self.assertNotIn("getItem('nocturne.lang')", en)

    def test_total_text(self):
        self.assertEqual([build.total_text("ko", 3), build.total_text("en", 3), build.total_text("en", 1)],
                         ["사진 3점", "3 photographs", "1 photograph"])

    def test_room_span(self):
        span = build.room_span
        self.assertEqual(span([]), "")
        self.assertEqual(span([{"date": "2025.11.30"}]), "2025.11")
        self.assertEqual(span([{"date": "2026.09.01"}, {"date": "2026.09.30"}]), "2026.09")
        self.assertEqual(span([{"date": "2026.09.03"}, {}, {"date": "2025.11.30"}]), "2025.11 – 2026.09")
        self.assertEqual(span([{"date": "알 수 없음"}, {}]), "")
        html = self.html()
        self.assertIn('<span class="room-count"><span class="room-n">1</span>점</span><span class="room-span">2025.11</span></p></header>', html)
        self.assertIn('<span class="room-count"><span class="room-n">2</span>점</span></p></header>', html)  # river: no dates, no span

    def test_labels_and_data(self):
        html = self.html()
        self.assertIn('<figcaption class="label">공중전화<span class="label-date">, 2025.11.30</span></figcaption>', html)
        self.assertIn('<figcaption class="label">다리 아래</figcaption>', html)
        self.assertRegex(html, r'data-slug="n1"[^>]*data-title="공중전화" data-date="2025.11.30"')
        self.assertRegex(html, r'data-slug="a2"[^>]*data-title="다리 아래" data-date=""')

    def test_screen_readers_hear_the_alt_and_clean_names(self):
        html = self.html()
        thumb = build.asset_url(self.root, "thumb", "n1")
        # the button's aria-label names it; the photo's alt text describes it
        self.assertIn('<button class="print-btn" type="button" aria-label="공중전화 크게 보기" aria-describedby="alt-n1">'
                      f'<img id="alt-n1" src="{thumb}" alt="빨간 공중전화 부스"', html)
        for name in ("a1", "a2", "n1"):
            self.assertIn(f'<img id="alt-{name}" src="{build.asset_url(self.root, "thumb", name)}"', html)
        cover = re.search(r'<button class="cover-btn"[^>]*>', html).group(0)
        self.assertIn('aria-label="스카이라인 크게 보기"', cover)
        self.assertIn('aria-describedby="alt-a1"', cover)  # the cover is also in its room: it points at that image
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), "ids must be unique")

    def test_cover(self):
        cover = re.search(r'<figure class="cover">.*?</figure>', self.html(), re.S).group(0)
        self.assertIn('data-open="a1"', cover)
        self.assertIn('<figcaption class="label">스카이라인</figcaption>', cover)

    def test_cover_focus(self):
        style = lambda: re.search(r'<button class="cover-btn"[^>]*style="([^"]*)"', self.html()).group(1)
        self.assertIn("--fx:42%;--fy:50%", style())  # SITE's [0.42, 0.5]
        for value, want in (([1.5, -1], "--fx:100%;--fy:0%"), ("x", None), ([0.5], None), (None, None)):
            data = photolib.load_data(self.root)
            if value is None: data.pop("cover_focus", None)
            else: data["cover_focus"] = value
            photolib.save_data(self.root, data)
            if want: self.assertIn(want, style())
            else: self.assertNotIn("--fx", style())

    def test_title_font_link(self):
        for page in (self.html(), self.en()):
            found = re.search(r'<link rel="stylesheet" href="https://fonts\.googleapis\.com/css2\?family=Noto\+Serif\+KR:wght@300'
                              r'&amp;text=([^"&]+)&amp;display=swap" crossorigin>', page)
            self.assertIsNotNone(found)
            self.assertEqual(urllib.parse.unquote(found.group(1)), "".join(sorted(set("Nocturne강River밤Night"))))

    def test_title_font_link_encodes_odd_names(self):
        data = photolib.load_data(self.root)
        data["rooms"][0]["name"] = 'A&B "#1"'
        href = build.title_font_href(data)
        text = href.split("&text=", 1)[1].rsplit("&display=swap", 1)[0]
        for raw in "&# \"":
            self.assertNotIn(raw, text)
        self.assertEqual(urllib.parse.unquote(text), "".join(sorted(set('NocturneA&B "#1"River밤Night'))))
        photolib.save_data(self.root, data)
        self.assertNotIn('"#1"', re.search(r'<link rel="stylesheet" href="[^"]*wght@300[^"]*"', self.html()).group(0))

    def test_cover_missing(self):
        for value in ("gone", None):
            data = photolib.load_data(self.root)
            if value is None: data.pop("cover", None)
            else: data["cover"] = value
            photolib.save_data(self.root, data)
            self.assertNotIn('<figure class="cover">', self.html())

    def test_empty_room(self):
        data = photolib.load_data(self.root)
        data["rooms"].append({"id": "new", "name": "새 방", "name_en": "New", "note": "n", "photos": []})
        photolib.save_data(self.root, data)
        html = self.html()
        self.assertIn('<h2>새 방</h2><p class="room-meta"><span class="room-count"><span class="room-n">0</span>점</span></p></header><div class="hang">', html)
        self.assertIn('<li><a href="#new">새 방</a></li>', html)

    def test_versioned_urls(self):
        url = build.asset_url(self.root, "thumb", "n1")
        digest = hashlib.sha256(photolib.thumb_path(self.root, "n1").read_bytes()).hexdigest()[:8]
        self.assertEqual(url, f"photos/thumb/n1.jpg?v={digest}")
        html = self.html()
        self.assertIn(f'data-thumb="{url}"', html)
        self.assertIn(f'<img id="alt-n1" src="{url}"', html)
        self.assertIn(f'data-full="{build.asset_url(self.root, "full", "n1")}"', html)
        helpers.make_jpeg(photolib.thumb_path(self.root, "n1"), (300, 200), (10, 200, 10))
        self.assertNotEqual(build.asset_url(self.root, "thumb", "n1"), url)

    def test_footer_links_to_the_admin_page(self):
        self.assertEqual(self.html().count('<a class="foot-admin" href="admin/">관리</a>'), 2)

    def css(self):
        return re.search(r"<style>(.*?)</style>", self.html(), re.S).group(1)

    def test_side_bullet_is_not_read_aloud(self):
        # the plain declaration first (browsers that do not know the alt-text form keep it), then the silent one
        self.assertIn(r'content: "\2022"; content: "\2022" / "";', self.css())

    def test_coarse_pointer_keeps_viewer_buttons_big(self):
        # from 960px the buttons are 24px tall; a finger on a big screen still gets 40px
        self.assertRegex(self.css(), r"@media \(pointer: coarse\) \{\s*\.lb-link \{[^}]*min-height: 40px;")

    def test_vertical_titles_css(self):
        css = self.css()
        self.assertRegex(css, r"\.room-head \{[^}]*position: sticky;")
        self.assertRegex(css, r"\.room-head \{[^}]*writing-mode: vertical-rl;")
        self.assertRegex(css, r"(?s)@media \(min-width: 960px\) \{.*?\.side \.mark \{[^}]*writing-mode: vertical-rl;")
        for s in ("font-size: max(56px, min(7.6vw, 124px, (100svh - 22rem) / 4.6));", "font-size: clamp(40px, 4vw, 60px);",
                  "aspect-ratio: 4 / 5;", "font-weight: 300;"):
            self.assertIn(s, css)
        self.assertRegex(css, r"\.cover-btn img \{[^}]*object-fit: cover;[^}]*object-position: var\(--fx, 50%\) var\(--fy, 50%\);")

    def test_count_stands_upright(self):
        # `text-combine-upright: digits 2` is taken by no browser: the number has its own span instead
        css = self.css()
        self.assertNotIn("digits 2", css)
        self.assertRegex(css, r"\.room-n \{ text-combine-upright: all; \}")
        self.assertRegex(css, r"\.room-n\.is-long \{ text-combine-upright: none; text-orientation: upright; \}")
        self.assertEqual(build.count_html("ko", 7), '<span class="room-n">7</span>점')
        self.assertEqual(build.count_html("ko", 99), '<span class="room-n">99</span>점')
        self.assertEqual(build.count_html("ko", 100), '<span class="room-n is-long">100</span>점')
        self.assertEqual(build.count_html("en", 12), '<span class="room-n">12</span>')

    def test_phone_cover_never_taller_than_the_first_screen(self):
        # 4:5 of the full width would be several screens tall on a phone held sideways or a narrow desktop window
        self.assertRegex(self.css(), r"\.cover-btn \{[^}]*aspect-ratio: 4 / 5;[^}]*max-height: calc\(100svh - var\(--bar\) - "
                                     r"env\(safe-area-inset-top, 0px\) - var\(--lead\) - 28px\);")
        self.assertRegex(self.css(), r"@media \(min-width: 960px\) \{[^@]*\.cover-btn \{[^}]*max-height: none;")

    def test_phone_rows_lower_target(self):
        # the name column takes room from a phone's hang: a lower target keeps two prints a row
        self.assertIn("return w < 400 ? 150 : 180;", self.html())

    def test_side_column_scrolls_when_the_window_is_short(self):
        side = re.search(r"\.side \{[^}]*overflow-y: auto;[^}]*\}", self.css())
        self.assertIsNotNone(side)
        self.assertIn("scrollbar-width: thin;", side.group(0))  # a thin bar, and only when it overflows

    def test_phone_footer_admin_link_is_underlined(self):
        self.assertRegex(self.css(), r"\.foot \.foot-admin \{[^}]*text-decoration: underline;")

    def test_viewer_ignores_a_late_original(self):
        self.assertIn("if (!lb.hidden && idx === want) lbImg.src = full.src;", self.html())  # nothing is set once it is shut

    def test_phone_menu_script(self):
        html = self.html()
        self.assertIn("menu.addEventListener('focusout'", html)  # focus leaving the menu folds it
        self.assertIn("e.stopPropagation()", html)  # the tap that folds it does not also press what is under it
        self.assertIn("}, true);", html)  # ... caught before the target sees the click

    def test_escapes_text(self):
        data = photolib.load_data(self.root); data["rooms"][0]["photos"][1]["title"] = "<b>&"
        photolib.save_data(self.root, data)
        self.assertIn("&lt;b&gt;&amp;", self.html())

    def test_missing_photo_file(self):
        photolib.thumb_path(self.root, "n1").unlink()
        with self.assertRaisesRegex(build.BuildError, "prepare_photos"):
            build.build(self.root)

    def test_out_dir(self):
        (self.root / "admin" / "icons").mkdir(parents=True); (self.root / "admin" / "index.html").write_text("x")
        for p in ("manifest.webmanifest", "sw.js", "icons/icon-512.png"):
            (self.root / "admin" / p).write_text("x")
        (self.root / "queue" / "q").mkdir(parents=True); (self.root / "queue" / "q" / "item.json").write_text("{}")
        out = Path(tempfile.mkdtemp()) / "_site"
        self.addCleanup(shutil.rmtree, out.parent, ignore_errors=True)
        build.build(self.root, out)
        for p in ("index.html", "en/index.html", "photos/thumb/a1.jpg", "photos/full/n1.jpg", "admin/index.html",
                  "admin/manifest.webmanifest", "admin/sw.js", "admin/icons/icon-512.png",
                  "icons/icon-512.png", "manifest.webmanifest", "sw.js"):
            self.assertTrue((out / p).exists(), p)
        for p in ("index.html", "sw.js", "manifest.webmanifest"):
            self.assertFalse((self.root / p).exists(), p)
        for p in ("src", "queue"):
            self.assertFalse((out / p).exists(), p)


class PwaTests(unittest.TestCase):
    def setUp(self):
        self.root = helpers.make_site(Path(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.out = Path(tempfile.mkdtemp()) / "_site"
        self.addCleanup(shutil.rmtree, self.out.parent, ignore_errors=True)

    def built(self, name):
        build.build(self.root, self.out)
        return (self.out / name).read_text(encoding="utf-8")

    def version(self):
        return re.search(r"const VERSION = '([0-9a-f]{12})';", self.built("sw.js")).group(1)

    def cover(self):
        return json.loads(re.search(r'const COVER = (".*?");', self.built("sw.js")).group(1))

    def test_manifest(self):
        manifest = json.loads(self.built("manifest.webmanifest"))
        self.assertEqual(manifest, {
            "name": "Nocturne", "short_name": "Nocturne", "id": "/nocturne/", "start_url": "./", "scope": "./",
            "display": "standalone", "background_color": "#000000", "theme_color": "#000000", "lang": "ko",
            "description": "사진 3점",
            "icons": [{"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                      {"src": "icons/icon-maskable-512.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]})
        for icon in manifest["icons"]:
            self.assertTrue((self.out / icon["src"]).is_file(), icon["src"])

    def test_sw(self):
        text = self.built("sw.js")
        for placeholder in ("__VERSION__", "__PHOTOS__", "__COVER__"):
            self.assertNotIn(placeholder, text)
        photos = json.loads(re.search(r"const PHOTOS = (\[.*?\]);", text, re.S).group(1))
        self.assertEqual(photos, [{"thumb": build.asset_url(self.root, "thumb", f), "full": build.asset_url(self.root, "full", f)}
                                  for f in ("a1", "a2", "n1")])
        first = self.version()
        self.assertEqual(self.version(), first)  # the same site gives the same version
        data = photolib.load_data(self.root); data["rooms"][1]["photos"][0]["title"] = "다른 제목"
        photolib.save_data(self.root, data)
        second = self.version()
        self.assertNotEqual(second, first)
        data["rooms"][1]["photos"][0]["alt_en"] = "Another payphone"  # only the English page changes
        photolib.save_data(self.root, data)
        self.assertNotEqual(self.version(), second)

    def test_sw_version_covers_icons(self):
        first = self.version()
        icon = self.root / "icons" / "icon-192.png"
        data = bytearray(icon.read_bytes())
        data[-1] ^= 0x01  # one byte of one icon, nothing else on the site
        icon.write_bytes(bytes(data))
        changed = self.version()
        self.assertNotEqual(changed, first)  # an icon-only change still gives installed devices a new worker
        (self.root / "icons" / "extra.png").write_bytes(b"x")
        self.assertNotEqual(self.version(), changed)  # and so does an icon added

    def test_sw_cover(self):
        self.assertEqual(self.cover(), build.asset_url(self.root, "full", "a1"))  # the full-size print, not the thumbnail
        for value in (None, "gone"):  # no cover, and a cover that is not hung
            data = photolib.load_data(self.root)
            if value is None: data.pop("cover", None)
            else: data["cover"] = value
            photolib.save_data(self.root, data)
            self.assertEqual(self.cover(), "")

    def test_sw_template_marks(self):
        sw = self.root / "src" / "sw.js"
        text = sw.read_text(encoding="utf-8")
        for broken in (text.replace("'__VERSION__'", "'x'"), text + "\nconst MORE = [/*__PHOTOS__*/];\n",
                       text.replace("'' /*__COVER__*/", "''"), text + "\nconst MORE = '' /*__COVER__*/;\n"):
            sw.write_text(broken, encoding="utf-8")
            with self.assertRaisesRegex(build.BuildError, "src/sw.js"):
                build.build(self.root)

    def test_shell_files_are_published(self):
        # install fetches every path of SHELL: one missing from the published folder would fail every install
        shell = re.search(r"const SHELL = \[(.*?)\];", self.built("sw.js"), re.S).group(1)
        paths = re.findall(r"'([^']+)'", shell)
        self.assertIn("manifest.webmanifest", paths)
        self.assertGreater(len(paths), 1)
        for path in paths:
            self.assertTrue((self.out / path).is_file(), path)

    def test_root_build_writes_pwa_files(self):
        build.build(self.root)
        for name in ("sw.js", "manifest.webmanifest"):
            self.assertTrue((self.root / name).is_file(), name)

    def test_head(self):
        build.build(self.root)
        ko = (self.root / "index.html").read_text(encoding="utf-8")
        en = (self.root / "en" / "index.html").read_text(encoding="utf-8")
        for page, base in ((ko, ""), (en, "../")):
            for s in (f'<link rel="manifest" href="{base}manifest.webmanifest">', f'<link rel="icon" href="{base}icons/icon-192.png">',
                      f'<link rel="apple-touch-icon" href="{base}icons/apple-touch-icon.png">',
                      f"navigator.serviceWorker.register('{base}sw.js')"):
                self.assertTrue(s in page, s)  # (assertIn would print the whole page)

    def test_gitignore(self):
        lines = (helpers.ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        for s in ("/sw.js", "/manifest.webmanifest"):
            self.assertIn(s, lines)


class TokenTests(unittest.TestCase):
    TEMPLATE = helpers.ROOT / "src" / "template.html"

    def test_tokens(self):
        t = helpers.root_tokens(self.TEMPLATE)
        want = {"--ground": "#000", "--wall": "#111", "--ink": "#ebe9e4", "--ink-dim": "#8f8c86", "--rule": "#262624"}
        self.assertEqual({k: t[k] for k in want}, want)
        self.assertGreaterEqual(helpers.contrast("#ebe9e4", "#000000"), 7)
        self.assertGreaterEqual(helpers.contrast("#8f8c86", "#000000"), 4.5)

    def test_one_dark_theme_one_font(self):
        text = self.TEMPLATE.read_text(encoding="utf-8")
        for gone in ("prefers-color-scheme", 'data-theme', "--lamp", "--lb-", "Cormorant", "IBM Plex", "italic",
                     "letter-spacing", "text-transform", "lb-btn", "<svg", "사진을 누르면"):
            self.assertNotIn(gone, text)
        self.assertIn('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400&display=swap" crossorigin>', text)
        self.assertIn('<link rel="stylesheet" href="__TITLEFONT__" crossorigin>', text)  # weight 300, only the title letters
        self.assertIn('<meta name="theme-color" content="#000">', text)
        self.assertIn("localStorage.setItem('nocturne.lang'", text)  # the toggle remembers the choice


if __name__ == "__main__":
    unittest.main()
