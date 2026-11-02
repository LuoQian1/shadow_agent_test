"""工具层护栏测试：能力边界、策略拒绝、故障注入、需求不可被替换。"""

from __future__ import annotations

import unittest

import helpers
from src.tools.registry import (
    FORBIDDEN_TOOL_NAMES,
    TOOL_NAMES,
    TOOL_SPECS,
    validate_args,
)


def box(material_id, brief_id, faults=None):
    return helpers.toolbox(
        {"case_id": "unit", "material_id": material_id, "brief_id": brief_id}, faults=faults
    )


class TestToolRegistry(unittest.TestCase):
    EXPECTED = {
        "read_material",
        "get_publish_brief",
        "check_rules",
        "query_slots",
        "fetch_material_assets",
        "list_drafts",
        "save_schedule_draft",
    }

    def test_registry_is_exactly_expected(self):
        self.assertEqual(set(TOOL_NAMES), self.EXPECTED)

    def test_no_publish_or_confirm_capability(self):
        """越权发布不是靠提示词禁止，而是靠「能力不存在」。"""
        self.assertEqual(set(TOOL_NAMES) & set(FORBIDDEN_TOOL_NAMES), set())
        # 模型如果硬要调用发布类工具，参数校验会直接判为未知工具
        self.assertEqual(validate_args("publish", {}), ["unknown tool: publish"])
        self.assertEqual(validate_args("confirm_schedule", {}), ["unknown tool: confirm_schedule"])

    def test_save_rejects_model_supplied_idempotency_key(self):
        """幂等键必须由代码派生，模型传 key 会被参数校验挡掉。"""
        errors = validate_args(
            "save_schedule_draft",
            {
                "material_id": "M01",
                "date": "2025-06-01",
                "time": "18:00",
                "channel": "douyin",
                "idempotency_key": "whatever",
            },
        )
        self.assertTrue(any("idempotency_key" in item for item in errors))

    def test_missing_required_arg(self):
        self.assertIn("missing required arg: material_id", validate_args("read_material", {}))

    def test_wrong_arg_type(self):
        errors = validate_args("query_slots", {"date": "x", "channel": "y", "duration_min": "30"})
        self.assertIn("arg duration_min must be integer", errors)


class TestPolicyDenial(unittest.TestCase):
    def test_hard_constraint_denies_other_time(self):
        toolbox, store = box("M05", "B-M05-v1")
        result = toolbox.save_schedule_draft("M05", "2025-06-07", "19:30", "douyin")
        self.assertEqual(result["save_status"], "denied")
        self.assertEqual(result["error"], "POLICY_HARD_CONSTRAINT")
        self.assertEqual(store.list_drafts(), [])

    def test_hard_constraint_requested_time_still_must_be_free(self):
        toolbox, store = box("M05", "B-M05-v1")
        result = toolbox.save_schedule_draft("M05", "2025-06-07", "18:00", "douyin")
        self.assertEqual(result["error"], "POLICY_SLOT_CONFLICT")
        self.assertEqual(store.list_drafts(), [])

    def test_block_rule_denies_save(self):
        toolbox, store = box("M04", "B-M04-v1")
        result = toolbox.save_schedule_draft("M04", "2025-06-04", "20:00", "douyin")
        self.assertEqual(result["save_status"], "denied")
        self.assertEqual(result["error"], "POLICY_BLOCK_RULE")
        self.assertIn("R03", result["blocked_by"])
        self.assertIn("R04", result["blocked_by"])
        self.assertEqual(store.list_drafts(), [])

    def test_missing_info_denies_save(self):
        toolbox, store = box("M03", "B-M03-v1")
        result = toolbox.save_schedule_draft("M03", "2025-06-04", "19:00", "douyin")
        self.assertEqual(result["error"], "POLICY_MISSING_INFO")
        self.assertEqual(store.list_drafts(), [])

    def test_occupied_slot_denies_save(self):
        toolbox, store = box("M01", "B-M01-v1")
        result = toolbox.save_schedule_draft("M01", "2025-06-01", "18:00", "douyin")
        self.assertEqual(result["error"], "POLICY_SLOT_CONFLICT")
        self.assertEqual(result["conflict"]["occupant"], "M09")
        self.assertEqual(store.list_drafts(), [])

    def test_free_slot_saves(self):
        toolbox, store = box("M02", "B-M02-v1")
        result = toolbox.save_schedule_draft("M02", "2025-06-01", "10:00", "douyin")
        self.assertEqual(result["save_status"], "saved")
        self.assertEqual(len(store.active_drafts(material_id="M02")), 1)

    def test_invalid_time_format_denied(self):
        toolbox, _ = box("M02", "B-M02-v1")
        result = toolbox.save_schedule_draft("M02", "2025-06-01", "25:00", "douyin")
        self.assertEqual(result["error"], "INVALID_TIME_FORMAT")

    def test_unknown_material_denied(self):
        toolbox, _ = box("M99", "B-M02-v1")
        result = toolbox.save_schedule_draft("M99", "2025-06-01", "10:00", "douyin")
        self.assertEqual(result["error"], "MATERIAL_NOT_FOUND")

    def test_session_requirement_cannot_be_replaced(self):
        """会话绑定的 brief 优先：模型无法通过参数拿到「可放宽」的需求版本。"""
        toolbox, _ = box("M05", "B-M05-v1")
        self.assertEqual(toolbox._brief_for("M05")["time_constraint"], "hard")

    def test_read_material_missing_fields(self):
        toolbox, _ = box("M03", "B-M03-v1")
        result = toolbox.read_material("M03")
        self.assertTrue(result["found"])
        self.assertIn("duration_sec", result["missing_fields"])

    def test_fetch_assets_reports_persistent_gaps(self):
        toolbox, _ = box("M03", "B-M03-v1")
        result = toolbox.fetch_material_assets("M03")
        self.assertEqual(result["upstream"], "mock-asset-service")
        self.assertIn("duration_sec", result["still_missing"])
        self.assertIn("music_license", result["still_missing"])

    def test_unknown_material_read(self):
        toolbox, _ = box("M99", "B-M02-v1")
        self.assertFalse(toolbox.read_material("M99")["found"])


class TestFaultInjection(unittest.TestCase):
    def test_save_fault_then_recover(self):
        toolbox, store = box("M02", "B-M02-v1", faults={"save_schedule_draft": {"fail_times": 1, "error": "EACCES"}})
        first = toolbox.save_schedule_draft("M02", "2025-06-01", "10:00", "douyin")
        second = toolbox.save_schedule_draft("M02", "2025-06-01", "10:00", "douyin")
        self.assertEqual(first["save_status"], "failed")
        self.assertEqual(second["save_status"], "saved")
        self.assertEqual(len(store.list_drafts()), 1)

    def test_slot_query_fault_then_recover(self):
        toolbox, _ = box("M02", "B-M02-v1", faults={"query_slots": {"fail_times": 1, "error": "ETIMEDOUT"}})
        first = toolbox.query_slots("2025-06-01", "douyin", near_time="10:00")
        second = toolbox.query_slots("2025-06-01", "douyin", near_time="10:00")
        self.assertFalse(first["ok"])
        self.assertTrue(second["ok"])


if __name__ == "__main__":
    unittest.main()
