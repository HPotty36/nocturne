import hashlib
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

    def test_cover_keys_follow_site_on_one_line(self):
        data = {"site": {"title": "N"}, "cover_pick": {"by": "person"}, "rooms": [], "cover_focus": [0.42, 0.5], "cover": "a1"}
        self.assertEqual(photolib.dumps_data(data), "\n".join([
            "{", '  "site": {', '    "title": "N"', "  },", '  "cover": "a1",', '  "cover_focus": [0.42,0.5],',
            '  "cover_pick": {"by":"person"},', '  "rooms": []', "}", ""]))
        self.assertEqual(photolib.COVER_KEYS, ("cover", "cover_focus", "cover_pick"))

    def test_cover_keys_without_site_come_first(self):
        text = photolib.dumps_data({"rooms": [], "cover_focus": [0, 1]})
        self.assertTrue(text.startswith('{\n  "cover_focus": [0,1],\n  "rooms": []'), text)

    def test_fingerprint(self):
        self.assertEqual(photolib.fingerprint(helpers.SITE), hashlib.sha256("a1\na2\nn1".encode()).hexdigest()[:8])
        photolib.add_photo(self.d, "night", {"file": "n0", "title": "t", "alt": "a"})
        self.assertNotEqual(photolib.fingerprint(self.d), photolib.fingerprint(helpers.SITE))
        self.assertEqual(len(photolib.fingerprint({"rooms": []})), 8)

    def test_real_data_shape(self):
        data = photolib.load_data(helpers.ROOT)
        self.assertNotIn("lede", data["site"])
        self.assertIn("{total}", data["site"]["description_en"])
        for room in data["rooms"]:
            self.assertTrue({"id", "name", "name_en", "note", "photos"} <= set(room), room["id"])

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
        self.assertEqual((room["id"], p["ai"]),
                         ("night", {"model": "m", "fields": ["title", "alt", "title_en", "alt_en"], "confidence": 0.9}))
        self.assertNotIn("confidence", photolib.ai_entry("m", None))
        for bad in (("moon", "x"), ("night", "a1")):
            with self.assertRaises(photolib.PhotoError):
                photolib.add_photo(self.d, bad[0], {"file": bad[1], "title": "t", "alt": "a"})
        with self.assertRaises(photolib.PhotoError):
            photolib.find_photo(self.d, "zzz")

    def test_upgrade_rules(self):
        e4b = ("gemma4:e4b-it-qat",)
        _, a2 = photolib.find_photo(self.d, "a2"); _, a1 = photolib.find_photo(self.d, "a1")
        self.assertTrue(photolib.needs_upgrade(a2, e4b))  # e4b wrote both texts
        self.assertFalse(photolib.needs_upgrade(a1, e4b))  # no ai record
        for better in ("claude-opus-5-5", "gemma4:12b-it-qat"):  # only the models listed are rewritten
            self.assertFalse(photolib.needs_upgrade({"ai": {"model": better, "fields": ["title", "alt"]}}, e4b))
        self.assertFalse(photolib.needs_upgrade({"ai": {"model": "gemma4:e4b-it-qat", "fields": []}}, e4b))
        a2["ai"]["fields"] = ["alt"]
        photolib.apply_ai_text(a2, {"title": "새 제목", "alt": "새 설명"}, "gemma4:12b-it-qat", ["alt"])
        self.assertEqual((a2["title"], a2["alt"]), ("다리 아래", "새 설명"))
        self.assertEqual(a2["ai"], {"model": "gemma4:12b-it-qat", "fields": ["alt"], "confidence": 0.61})
        self.assertFalse(photolib.needs_upgrade(a2, e4b))

    def test_ai_fields_include_english_in_order(self):
        self.assertEqual(photolib.AI_FIELDS, ("title", "alt", "title_en", "alt_en"))
        self.assertEqual(photolib.ai_entry("m", 0.9)["fields"], ["title", "alt", "title_en", "alt_en"])
        self.assertEqual(photolib.ai_entry("m", None), {"model": "m", "fields": ["title", "alt", "title_en", "alt_en"]})

    def test_apply_ai_text_lists_fields_in_ai_fields_order(self):
        _, n1 = photolib.find_photo(self.d, "n1")
        texts = {"title": "새 제목", "alt": "새 설명", "title_en": "New title", "alt_en": "A new description"}
        photolib.apply_ai_text(n1, texts, "m", ["alt_en", "title"])  # the order asked does not matter
        self.assertEqual(n1["ai"], {"model": "m", "fields": ["title", "alt_en"]})
        self.assertEqual((n1["title"], n1["alt"], n1["title_en"], n1["alt_en"]),
                         ("새 제목", "빨간 공중전화 부스", "Payphone", "A new description"))  # only the fields asked change

    def test_apply_ai_text_adds_to_the_fields_already_marked(self):
        _, a2 = photolib.find_photo(self.d, "a2")
        a2["ai"]["fields"] = ["alt"]
        photolib.apply_ai_text(a2, {"title_en": "Under the bridge"}, "m", ["title_en"])
        self.assertEqual((a2["title_en"], a2["ai"]["fields"], a2["ai"]["confidence"]), ("Under the bridge", ["alt", "title_en"], 0.61))


if __name__ == "__main__":
    unittest.main()
