"""草稿存储单元测试：幂等、并发、原子写、supersede、真实写入失败。"""

from __future__ import annotations

import os
import threading
import unittest

import helpers
from src.store.draft_store import DraftStore

BRIEF = {"brief_id": "B-test", "brief_version": 1, "requested_time": "18:00"}


def save(store, material_id="M01", date="2025-07-01", time="19:00", pending=None):
    return store.save(
        material_id=material_id,
        date=date,
        time=time,
        channel="douyin",
        duration_min=1,
        brief=BRIEF,
        pending_confirmations=pending or [],
    )


class TestDraftId(unittest.TestCase):
    def test_deterministic_and_slot_sensitive(self):
        first = DraftStore.compute_draft_id("M01", "2025-07-01", "19:00", "douyin")
        second = DraftStore.compute_draft_id("M01", "2025-07-01", "19:00", "douyin")
        other = DraftStore.compute_draft_id("M01", "2025-07-01", "19:30", "douyin")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertTrue(first.startswith("D-"))
        self.assertEqual(len(first), 14)


class TestIdempotency(unittest.TestCase):
    def setUp(self):
        self.store = helpers.temp_store()

    def test_same_request_twice_creates_one_draft(self):
        first = save(self.store)
        second = save(self.store)
        self.assertEqual(first["save_status"], "saved")
        self.assertEqual(second["save_status"], "duplicate")
        self.assertEqual(first["draft_id"], second["draft_id"])
        self.assertEqual(len(self.store.list_drafts(material_id="M01")), 1)

    def test_many_repeats_still_one(self):
        ids = {save(self.store)["draft_id"] for _ in range(10)}
        self.assertEqual(len(ids), 1)
        self.assertEqual(len(os.listdir(self.store.root)), 1)

    def test_different_slot_creates_second_draft(self):
        save(self.store, time="19:00")
        save(self.store, time="19:30")
        self.assertEqual(len(self.store.list_drafts(material_id="M01")), 2)

    def test_different_material_same_slot_is_separate(self):
        save(self.store, material_id="M01")
        save(self.store, material_id="M02")
        self.assertEqual(len(self.store.list_drafts()), 2)

    def test_concurrent_saves_produce_exactly_one(self):
        """8 个线程同时写同一素材同一时段，只允许成功一次（O_EXCL 原子性）。"""
        results = []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            results.append(save(self.store)["save_status"])

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(results), 8)
        self.assertEqual(results.count("saved"), 1)
        self.assertEqual(results.count("duplicate"), 7)
        self.assertEqual(len(self.store.list_drafts()), 1)


class TestSupersede(unittest.TestCase):
    def setUp(self):
        self.store = helpers.temp_store()

    def test_new_slot_supersedes_old_active_draft(self):
        old = save(self.store, time="20:30", pending=[{"id": "C-SLOT", "reason": "旧方案"}])
        new = save(self.store, time="18:00")

        self.assertEqual(old["save_status"], "saved")
        self.assertEqual(new["superseded"], [old["draft_id"]])

        old_record = self.store.get(old["draft_id"])
        self.assertEqual(old_record["status"], "superseded")
        self.assertEqual(old_record["superseded_by"], new["draft_id"])

        active = self.store.active_drafts(material_id="M01", date="2025-07-01")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["draft_id"], new["draft_id"])

    def test_supersede_does_not_cross_dates(self):
        old = save(self.store, date="2025-07-01", time="19:00")
        save(self.store, date="2025-07-02", time="19:00")
        self.assertEqual(self.store.get(old["draft_id"])["status"], "draft")

    def test_superseded_draft_not_counted_as_occupied(self):
        old = save(self.store, time="20:30")
        save(self.store, time="18:00")
        self.assertEqual(self.store.get(old["draft_id"])["status"], "superseded")


class TestFailurePaths(unittest.TestCase):
    def setUp(self):
        self.store = helpers.temp_store()

    def test_real_write_error_is_reported_as_failed(self):
        """用「同名目录」占位，让 os.open(O_EXCL) 抛真正的 OSError。"""
        draft_id = DraftStore.compute_draft_id("M01", "2025-07-01", "19:00", "douyin")
        os.makedirs(self.store.path_for(draft_id))
        result = save(self.store)
        self.assertEqual(result["save_status"], "failed")
        self.assertIn("error", result)
        self.assertEqual(self.store.active_drafts(material_id="M01"), [])

    def test_reset_clears_store(self):
        save(self.store)
        self.assertEqual(self.store.reset(), 1)
        self.assertEqual(self.store.list_drafts(), [])

    def test_summary_shape(self):
        save(self.store)
        save(self.store, time="19:30", material_id="M02")
        summary = self.store.summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(len(summary["active"]), 2)


class TestPendingStatus(unittest.TestCase):
    def setUp(self):
        self.store = helpers.temp_store()

    def test_pending_confirmation_status(self):
        result = save(self.store, pending=[{"id": "C-SLOT", "reason": "时段不一致", "needs": "owner_approval"}])
        self.assertEqual(result["record"]["status"], "pending_confirmation")

    def test_draft_status_without_pending(self):
        result = save(self.store)
        self.assertEqual(result["record"]["status"], "draft")


if __name__ == "__main__":
    unittest.main()
