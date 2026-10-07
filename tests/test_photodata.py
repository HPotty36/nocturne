import json
import shutil
import tempfile
import unittest
from pathlib import Path

import helpers  # puts scripts/ on sys.path
import photolib


class DataTests(unittest.TestCase):
    def setUp(self):
        self.d = json.loads(json.dumps(helpers.SITE))

    def test_site_fixture_matches_helpers(self):
        self.assertEqual(json.loads((helpers.FIXTURES / "site.json").read_text(encoding="utf-8")), helpers.SITE)

    def test_canonical_text(self):
        self.assertEqual(photolib.dumps_data(helpers.SITE), (helpers.FIXTURES / "site.canonical.json").read_text(encoding="utf-8"))

    def test_save_load_roundtrip(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        root = helpers.make_site(tmp)
        data = photolib.load_data(root); photolib.save_data(root, data)
        self.assertEqual(photolib.load_data(root), data)
        self.assertEqual(photolib.listed(data), {"a1", "a2", "n1"})

    def test_add_and_find(self):
        photolib.add_photo(self.d, "night", {"file": "n2", "title": "t", "alt": "a", "ai": photolib.ai_entry("m", 0.9)})
        room, p = photolib.find_photo(self.d, "n2")
        self.assertEqual((room["id"], p["ai"]), ("night", {"model": "m", "fields": ["title", "alt"], "confidence": 0.9}))
        self.assertNotIn("confidence", photolib.ai_entry("m", None))
        for bad in (("moon", "x"), ("night", "a1")):
            with self.assertRaises(photolib.PhotoError):
                photolib.add_photo(self.d, bad[0], {"file": bad[1], "title": "t", "alt": "a"})
        with self.assertRaises(photolib.PhotoError):
            photolib.find_photo(self.d, "zzz")

    def test_upgrade_rules(self):
        _, a2 = photolib.find_photo(self.d, "a2"); _, a1 = photolib.find_photo(self.d, "a1")
        self.assertTrue(photolib.needs_upgrade(a2, "gemma4:12b-it-qat"))
        self.assertFalse(photolib.needs_upgrade(a1, "gemma4:12b-it-qat"))
        self.assertFalse(photolib.needs_upgrade({"ai": {"model": "gemma4:e4b-it-qat", "fields": []}}, "gemma4:12b-it-qat"))
        a2["ai"]["fields"] = ["alt"]
        photolib.apply_ai_text(a2, {"title": "새 제목", "alt": "새 설명"}, "gemma4:12b-it-qat", ["alt"])
        self.assertEqual((a2["title"], a2["alt"]), ("다리 아래", "새 설명"))
        self.assertEqual(a2["ai"], {"model": "gemma4:12b-it-qat", "fields": ["alt"], "confidence": 0.61})
        self.assertFalse(photolib.needs_upgrade(a2, "gemma4:12b-it-qat"))


if __name__ == "__main__":
    unittest.main()
