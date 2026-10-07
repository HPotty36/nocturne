import json
import re
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageCms

import helpers  # puts scripts/ on sys.path
import photolib

_SCAN_END = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")  # the next real marker after compressed scan data


def jpeg_segments(data):
    """[(marker, payload)] of every segment of a JPEG from SOI to EOI, also those between progressive scans."""
    assert data[:2] == b"\xff\xd8", "no SOI"
    found, pos = [(0xD8, b"")], 2
    while True:
        assert data[pos] == 0xFF, f"no marker at byte {pos}"
        marker = data[pos + 1]
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker == 0xD9:
            found.append((marker, b""))
            assert pos + 2 == len(data), "bytes after EOI"
            return found
        size = int.from_bytes(data[pos + 2:pos + 4], "big")
        found.append((marker, data[pos + 4:pos + 2 + size]))
        pos += 2 + size
        if marker == 0xDA:
            pos = _SCAN_END.search(data, pos).start()


def iptc_app13():
    """An APP13 Photoshop/IPTC segment holding a caption, as photo apps write it."""
    record = b"\x1c\x02\x78\x00\x0aSeoul home"  # IPTC 2:120 caption
    block = b"8BIM\x04\x04\x00\x00" + struct.pack(">I", len(record)) + record + b"\x00" * (len(record) % 2)
    payload = b"Photoshop 3.0\x00" + block
    return b"\xff\xed" + struct.pack(">H", len(payload) + 2) + payload


class NameTests(unittest.TestCase):
    def test_cases(self):
        for given, want in helpers.load_cases()["names"]:
            self.assertEqual(photolib.name_for(given), want, given)


class GrayTests(unittest.TestCase):
    def test_cases(self):
        for case in helpers.load_cases()["grayscale"]:
            self.assertEqual(photolib.grayscale_from_chroma(helpers.expand_gray_case(case)), case["expect"], case)

    def test_images(self):
        self.assertTrue(photolib.is_grayscale(Image.linear_gradient("L").convert("RGB")))
        self.assertFalse(photolib.is_grayscale(Image.new("RGB", (300, 200), (200, 40, 40))))


def size_of(path):
    with Image.open(path) as im:
        return im.size


class ExifTests(unittest.TestCase):
    def test_exif_fixtures(self):
        want = json.loads((helpers.FIXTURES / "exif_expected.json").read_text(encoding="utf-8"))
        for f in ("exif_le.jpg", "exif_be.jpg"):
            with Image.open(helpers.FIXTURES / f) as im:
                self.assertEqual(photolib.exif_hints(im), want, f)


class CopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_copies_have_no_metadata(self):
        out = photolib.make_web_copies(helpers.make_jpeg(self.tmp / "in.jpg", camera="TestCam", gps=True), self.tmp, "in")
        self.assertEqual(out["hints"]["camera"], "TestCam")
        for p in (photolib.thumb_path(self.tmp, "in"), photolib.full_path(self.tmp, "in")):
            data = p.read_bytes()
            self.assertNotIn(b"TestCam", data)
            self.assertNotIn(b"Exif\x00\x00", data)

    def test_copies_keep_only_jfif_icc_and_image_segments(self):
        # A source with every kind of metadata a phone or an editor leaves: Exif with GPS, XMP, IPTC and a comment.
        src = self.tmp / "tagged.jpg"
        exif = Image.Exif()
        exif[0x0110] = "TestCam"
        place = exif.get_ifd(0x8825)
        place[1], place[2] = "N", (0.0, 0.0, 0.0)
        icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
        xmp = b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>'
        Image.new("RGB", (1200, 800), (200, 40, 40)).save(src, "JPEG", quality=90, exif=exif, xmp=xmp, icc_profile=icc,
                                                           comment=b"owner: test, Seoul home")
        raw = src.read_bytes()
        src.write_bytes(raw[:2] + iptc_app13() + raw[2:])
        source_markers = {m for m, _ in jpeg_segments(src.read_bytes())}
        self.assertLessEqual({0xE1, 0xED, 0xFE}, source_markers)  # the source really carries APP1, APP13 and COM

        photolib.make_web_copies(src, self.tmp, "tagged")
        image_segments = {0xDB, 0xC4, 0xDD, 0xDA, 0xD9} | set(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
        for p in (photolib.thumb_path(self.tmp, "tagged"), photolib.full_path(self.tmp, "tagged")):
            with self.subTest(p.parent.name):
                found = jpeg_segments(p.read_bytes())
                self.assertEqual(found[0][0], 0xD8)
                self.assertEqual([m for m, _ in found if 0xE0 <= m <= 0xEF or m == 0xFE], [0xE0, 0xE2])
                self.assertTrue(found[1] == (0xE0, found[1][1]) and found[1][1].startswith(b"JFIF\x00"))
                self.assertTrue(next(pl for m, pl in found if m == 0xE2).startswith(b"ICC_PROFILE\x00"))
                self.assertEqual({m for m, _ in found[1:]} - {0xE0, 0xE2}, {m for m, _ in found[1:]} & image_segments)
                self.assertNotIn(b"Seoul home", p.read_bytes())

    def test_copies_without_a_colour_profile_have_no_app2(self):
        src = self.tmp / "plain.jpg"
        Image.new("RGB", (300, 200), (10, 20, 30)).save(src, "JPEG", comment=b"owner: test")
        photolib.make_web_copies(src, self.tmp, "plain")
        found = jpeg_segments(photolib.full_path(self.tmp, "plain").read_bytes())
        self.assertEqual([m for m, _ in found if 0xE0 <= m <= 0xEF or m == 0xFE], [0xE0])

    def test_sizes_and_orientation(self):
        photolib.make_web_copies(helpers.make_jpeg(self.tmp / "big.jpg", size=(3000, 2000)), self.tmp, "big")
        self.assertEqual(size_of(photolib.thumb_path(self.tmp, "big")), (1000, 667))
        self.assertEqual(size_of(photolib.full_path(self.tmp, "big")), (2400, 1600))
        photolib.make_web_copies(helpers.make_jpeg(self.tmp / "rot.jpg", orientation=6), self.tmp, "rot")
        w, h = size_of(photolib.thumb_path(self.tmp, "rot"))
        self.assertLess(w, h)


if __name__ == "__main__":
    unittest.main()
