import re
import shutil
import tempfile
import unittest
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

    def test_roman(self):
        self.assertEqual([build.roman(n) for n in (1, 2, 3, 4, 9, 14)], ["I", "II", "III", "IV", "IX", "XIV"])

    def test_page(self):
        html = self.html()
        for s in ("<title>Nocturne</title>", '<a class="mark" href="#top">Nocturne</a>', '<span class="room-num">II</span>',
                  '<span class="label-meta">2025.11.30</span>', '<figure class="cover">'):
            self.assertIn(s, html)
        self.assertEqual(html.count('<figure class="print"'), 3)
        self.assertIsNone(re.search(r"__[A-Z]+__", html))

    def test_screen_readers_hear_the_alt_and_clean_names(self):
        html = self.html()
        # the button's aria-label names it; the photo's alt text describes it
        self.assertIn('<button class="print-btn" type="button" aria-label="공중전화 크게 보기" aria-describedby="alt-n1">'
                      '<img id="alt-n1" src="photos/thumb/n1.jpg" alt="빨간 공중전화 부스"', html)
        for name in ("a1", "a2", "n1"):
            self.assertIn(f'<img id="alt-{name}" src="photos/thumb/{name}.jpg"', html)
        cover = re.search(r'<button class="cover-btn"[^>]*>', html).group(0)
        self.assertIn('aria-label="스카이라인 크게 보기"', cover)
        self.assertIn('aria-describedby="alt-a1"', cover)  # the cover is also in its room: it points at that image
        ids = re.findall(r'\bid="([^"]+)"', html)
        self.assertEqual(len(ids), len(set(ids)), "ids must be unique")
        # "I 강", not "I강": the numeral is decoration; "강 River", not "강River", and River is read as English
        self.assertIn('<a href="#river"><span class="num" aria-hidden="true">I</span>강</a>', html)
        self.assertIn('<h2>강 <span class="room-en" lang="en">River</span></h2>', html)

    def test_escapes_text(self):
        data = photolib.load_data(self.root); data["rooms"][0]["photos"][1]["title"] = "<b>&"
        photolib.save_data(self.root, data)
        self.assertIn("&lt;b&gt;&amp;", self.html())

    def test_missing_photo_file(self):
        photolib.thumb_path(self.root, "n1").unlink()
        with self.assertRaisesRegex(build.BuildError, "prepare_photos"):
            build.build(self.root)

    def test_out_dir(self):
        (self.root / "admin").mkdir(); (self.root / "admin" / "index.html").write_text("x")
        (self.root / "queue" / "q").mkdir(parents=True); (self.root / "queue" / "q" / "item.json").write_text("{}")
        out = Path(tempfile.mkdtemp()) / "_site"
        self.addCleanup(shutil.rmtree, out.parent, ignore_errors=True)
        build.build(self.root, out)
        for p in ("index.html", "photos/thumb/a1.jpg", "photos/full/n1.jpg", "admin/index.html"):
            self.assertTrue((out / p).exists(), p)
        self.assertFalse((self.root / "index.html").exists())
        for p in ("src", "queue"):
            self.assertFalse((out / p).exists(), p)


class TokenTests(unittest.TestCase):
    def test_contrast(self):
        dark, light_media, light_attr = helpers.theme_tokens(helpers.ROOT / "src" / "template.html")
        self.assertEqual(light_media, light_attr)
        for t in (dark, light_media):
            for fg, bg, need in (("--ink", "--ground", 7), ("--ink-dim", "--ground", 4.5), ("--lamp", "--ground", 3)):
                self.assertGreaterEqual(helpers.contrast(t[fg], t[bg]), need, f"{fg} on {bg}")


if __name__ == "__main__":
    unittest.main()
