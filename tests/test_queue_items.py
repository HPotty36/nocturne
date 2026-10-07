import unittest
from datetime import datetime, timedelta, timezone

import helpers  # puts scripts/ on sys.path
import queue_items

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
def ago(s): return queue_items.iso(NOW - timedelta(seconds=s))
class QueueTests(unittest.TestCase):
    def item(self, **kw):
        return {**queue_items.new_item("a", kind="new", grayscale=False, now=NOW), **kw}
    def test_new_item(self):
        it = queue_items.new_item("redraft-a1", kind="redraft", grayscale=False, now=NOW, file="a1")
        self.assertEqual((it["status"], it["uploaded_at"], it["file"], it["by"]), ("waiting", "2026-10-06T12:00:00Z", "a1", None))
    def test_pc(self):
        self.assertTrue(queue_items.can_take(self.item(), NOW, "pc"))
        self.assertTrue(queue_items.can_take(self.item(status="github", claimed_at=ago(601)), NOW, "pc"))
        for st in ("pc", "github"):
            self.assertFalse(queue_items.can_take(self.item(status=st, claimed_at=ago(30)), NOW, "pc"))
        self.assertFalse(queue_items.can_take(self.item(status="failed"), NOW, "pc"))
    def test_github_waits(self):
        self.assertFalse(queue_items.can_take(self.item(uploaded_at=ago(30)), NOW, "github"))
        self.assertTrue(queue_items.can_take(self.item(uploaded_at=ago(61)), NOW, "github"))
        self.assertTrue(queue_items.can_take(self.item(status="pc", claimed_at=ago(601)), NOW, "github"))
    def test_claim_fail(self):
        c = queue_items.claim(self.item(error="x"), "github", NOW)
        self.assertEqual((c["status"], c["by"], c["claimed_at"], c["error"]), ("github", "github", "2026-10-06T12:00:00Z", None))
        self.assertEqual(queue_items.fail(c, "꺼짐")["status"], "failed")


class EdgeTests(unittest.TestCase):
    def item(self, **kw):
        return {**queue_items.new_item("a", kind="new", grayscale=False, now=NOW), **kw}
    def test_boundaries(self):
        self.assertFalse(queue_items.is_stale(self.item(status="pc", claimed_at=ago(600)), NOW))
        self.assertTrue(queue_items.is_stale(self.item(status="pc", claimed_at=ago(601)), NOW))
        self.assertFalse(queue_items.can_take(self.item(uploaded_at=ago(59)), NOW, "github"))
        self.assertTrue(queue_items.can_take(self.item(uploaded_at=ago(60)), NOW, "github"))
    def test_failed_is_never_taken_however_old(self):
        old = self.item(status="failed", uploaded_at=ago(99999), claimed_at=ago(99999))
        self.assertFalse(queue_items.is_stale(old, NOW))
        for by in ("pc", "github"):
            self.assertFalse(queue_items.can_take(old, NOW, by))
    def test_a_claim_without_a_time_can_be_taken_again(self):
        self.assertTrue(queue_items.can_take(self.item(status="pc", claimed_at=None), NOW, "github"))
    def test_unknown_worker(self):
        with self.assertRaises(ValueError):
            queue_items.can_take(self.item(), NOW, "cloud")
    def test_iso_is_utc_and_round_trips(self):
        self.assertEqual(queue_items.parse_iso(queue_items.iso(NOW)), NOW)
        seoul = datetime(2026, 10, 6, 21, 0, tzinfo=timezone(timedelta(hours=9)))
        self.assertEqual(queue_items.iso(seoul), "2026-10-06T12:00:00Z")
    def test_release_hands_the_item_back_with_a_note(self):
        claimed = queue_items.claim(self.item(uploaded_at=ago(90)), "pc", NOW)
        released = queue_items.release(claimed, "Ollama가 꺼져 있어요")
        self.assertEqual(released, {**claimed, "status": "waiting", "claimed_at": None, "by": None, "error": "Ollama가 꺼져 있어요"})
        self.assertEqual(list(released), list(claimed))  # same keys in the same order
        self.assertEqual(claimed["status"], "pc")  # a new item; the claim is untouched
        self.assertTrue(queue_items.can_take(released, NOW, "github"))
    def test_claim_and_fail_return_new_items(self):
        item = self.item()
        claimed = queue_items.claim(item, "pc", NOW)
        queue_items.fail(claimed, "x")
        self.assertEqual((item["status"], item["by"], item["error"]), ("waiting", None, None))
        self.assertEqual((claimed["status"], claimed["error"]), ("pc", None))
