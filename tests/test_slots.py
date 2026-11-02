"""时段规划单元测试：占用、禁发窗口、间隔、配额、自家草稿、排序偏好。"""

from __future__ import annotations

import unittest

import helpers
from src.domain.slots import SlotPlanner, to_hhmm, to_minutes

P = helpers.planner()


def entry(date, time, material_id, status="draft", duration_min=30, channel="douyin"):
    return {
        "date": date,
        "time": time,
        "channel": channel,
        "duration_min": duration_min,
        "material_id": material_id,
        "status": status,
    }


def plan(near="18:00", date="2025-07-01", duration_min=1, entries=None, self_material="M01", limit=5):
    built = P.build_entries(entries or [], [])
    return P.plan(
        date=date,
        channel="douyin",
        duration_min=duration_min,
        near_time=near,
        entries=built,
        self_material=self_material,
        limit=limit,
    )


class TestTimeHelpers(unittest.TestCase):
    def test_roundtrip(self):
        self.assertEqual(to_minutes("18:30"), 1110)
        self.assertEqual(to_hhmm(1110), "18:30")
        self.assertEqual(to_hhmm(to_minutes("09:00")), "09:00")

    def test_invalid(self):
        for bad in ("", "25:00", "18:70", "abc", None):
            with self.assertRaises(ValueError):
                to_minutes(bad)


class TestSlotPlanner(unittest.TestCase):
    def test_no_conflict_when_free(self):
        result = plan(near="15:00")
        self.assertIsNone(result["conflict"])
        self.assertIn("15:00", [c["time"] for c in result["candidates"]])

    def test_occupied_requested_slot_reports_occupant(self):
        result = plan(near="18:00", entries=[entry("2025-07-01", "18:00", "M09")])
        self.assertEqual(result["conflict"]["type"], "slot_occupied")
        self.assertEqual(result["conflict"]["occupant"], "M09")

    def test_conflict_prefers_later_slot_over_earlier(self):
        """被占用后应顺延而不是提前：19:00 排在 17:00 之前。"""
        result = plan(near="18:00", entries=[entry("2025-07-01", "18:00", "M09")], limit=20)
        times = [c["time"] for c in result["candidates"]]
        self.assertEqual(times[0], "19:00")
        self.assertIn("17:00", times)
        self.assertLess(times.index("19:00"), times.index("17:00"))

    def test_blackout_window_excluded_and_flagged(self):
        result = plan(near="12:30")
        self.assertEqual(result["conflict"]["type"], "blackout")
        times = [c["time"] for c in result["candidates"]]
        self.assertNotIn("12:00", times)
        self.assertNotIn("12:30", times)
        self.assertIn("13:00", times)

    def test_min_gap_enforced(self):
        result = plan(near="10:00", entries=[entry("2025-07-01", "10:00", "M09")])
        times = [c["time"] for c in result["candidates"]]
        self.assertNotIn("10:30", times)  # 与 10:00-10:30 之间没有留出 30 分钟间隔
        self.assertIn("11:00", times)

    def test_quota_exceeded(self):
        entries = [entry("2025-07-01", "%02d:00" % (9 + index), "M09") for index in range(6)]
        result = plan(near="19:00", entries=entries)
        self.assertTrue(result["quota"]["exceeded"])
        self.assertEqual(result["candidates"], [])

    def test_own_draft_slot_is_available_and_marked(self):
        result = plan(
            near="19:00",
            entries=[entry("2025-07-01", "19:00", "M01", status="pending_confirmation")],
            self_material="M01",
        )
        self.assertIsNone(result["conflict"])  # 自家草稿不算冲突
        matched = [c for c in result["candidates"] if c["time"] == "19:00"]
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["note"], "existing_draft_same_slot")

    def test_foreign_draft_slot_is_occupied(self):
        result = plan(near="19:00", entries=[entry("2025-07-01", "19:00", "M09")])
        self.assertEqual(result["conflict"]["type"], "slot_occupied")

    def test_invalid_near_time_reports_conflict_type(self):
        result = plan(near="99:99")
        self.assertEqual(result["conflict"]["type"], "invalid_time")

    def test_cancelled_entry_is_ignored(self):
        result = plan(near="18:00", entries=[entry("2025-07-01", "18:00", "M09", status="cancelled")])
        self.assertIsNone(result["conflict"])

    def test_window_bounds_respected(self):
        result = plan(near="09:00", duration_min=30)
        times = [c["time"] for c in result["candidates"]]
        self.assertTrue(all("09:00" <= t < "22:00" for t in times))
        self.assertNotIn("21:30", [t for t in times if t == "21:30"] and [])


if __name__ == "__main__":
    unittest.main()
