"""决策循环测试：上限、重复调用、非法动作、未知工具、降级、trace 结构。"""

from __future__ import annotations

import unittest

import helpers
from src.agent.loop import LIMIT_STATUS, AgentRunner
from src.models.adversarial import AdversarialLoopModel
from src.models.base import ModelError
from src.models.offline_policy import OfflinePolicyModel


def make_runner(model, case=None, store=None, faults=None, fallback=None, limits=None):
    case = case or {"case_id": "unit_loop", "material_id": "M02", "brief_id": "B-M02-v1"}
    box, store = helpers.toolbox(case, store=store, faults=faults)
    task = {
        "case_id": case["case_id"],
        "material_id": case["material_id"],
        "brief_id": case["brief_id"],
        "run_index": 1,
    }
    runner = AgentRunner(
        model, box, helpers.SETTINGS, task, limits=limits, fallback_model=fallback
    )
    return runner, store


def records(result, record_type):
    return [item for item in result["trace"] if item.get("type") == record_type]


class TestLimits(unittest.TestCase):
    def test_tool_call_limit_returns_incomplete(self):
        runner, _ = make_runner(
            AdversarialLoopModel(), limits={"max_steps": 20, "max_tool_calls": 3, "max_repeat": 5}
        )
        result = runner.run()
        self.assertEqual(result["status"], LIMIT_STATUS)
        self.assertEqual(result["usage"]["tool_calls_used"], 3)
        self.assertIn("工具调用", result["limit_reason"])
        self.assertTrue(records(result, "limit_reached"))

    def test_step_limit_returns_incomplete(self):
        model = AdversarialLoopModel()
        runner, _ = make_runner(model, limits={"max_steps": 2, "max_tool_calls": 10, "max_repeat": 10})
        result = runner.run()
        self.assertEqual(result["status"], LIMIT_STATUS)
        self.assertEqual(result["usage"]["steps_used"], 2)

    def test_repeat_limit_rejects_identical_calls(self):
        same = helpers.tool_call("read_material", {"material_id": "M02"})
        model = helpers.ScriptedModel([same, same, same, helpers.final_call({"review_status": "pass"})])
        runner, _ = make_runner(model, limits={"max_steps": 10, "max_tool_calls": 10, "max_repeat": 2})
        result = runner.run()
        rejected = records(result, "tool_rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "REPEAT_LIMIT")
        self.assertEqual(result["usage"]["tool_calls_used"], 2)


class TestActionValidation(unittest.TestCase):
    def test_publish_tool_is_unavailable(self):
        model = helpers.ScriptedModel(
            [
                helpers.tool_call("publish", {"material_id": "M02"}),
                helpers.final_call({"review_status": "pass"}),
            ]
        )
        runner, store = make_runner(model)
        result = runner.run()
        rejected = records(result, "tool_rejected")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["reason"], "TOOL_NOT_AVAILABLE")
        self.assertEqual(store.list_drafts(), [])
        self.assertEqual(result["status"], "completed")

    def test_invalid_action_becomes_failed(self):
        model = helpers.ScriptedModel(["这不是 JSON", "也不是", "还不是", "永远不是"])
        runner, _ = make_runner(model, limits={"max_steps": 10, "max_tool_calls": 10, "max_repeat": 2})
        result = runner.run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(len(records(result, "invalid_action")), 3)

    def test_bad_args_do_not_execute_tool(self):
        model = helpers.ScriptedModel(
            [
                helpers.tool_call("query_slots", {"date": "2025-06-01"}),
                helpers.final_call({"review_status": "pass"}),
            ]
        )
        runner, _ = make_runner(model)
        result = runner.run()
        rejected = records(result, "tool_rejected")
        self.assertEqual(rejected[0]["reason"], "INVALID_ARGS")
        self.assertEqual(result["usage"]["tool_calls_used"], 0)


class TestDegrade(unittest.TestCase):
    def test_model_error_degrades_to_offline_policy(self):
        class Broken(helpers.ScriptedModel):
            name = "broken-real"

            def __init__(self):
                super().__init__([ModelError("HTTP 401 invalid api key")])

        runner, _ = make_runner(Broken(), fallback=OfflinePolicyModel())
        result = runner.run()
        self.assertTrue(result["model_info"]["degraded"])
        self.assertEqual(result["model_info"]["final"], "offline-policy")
        self.assertEqual(result["status"], "completed")
        self.assertTrue(records(result, "model_degraded"))

    def test_model_error_without_fallback_fails(self):
        class Broken(helpers.ScriptedModel):
            name = "broken-real"

            def __init__(self):
                super().__init__([ModelError("boom")])

        runner, _ = make_runner(Broken())
        result = runner.run()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["model_error"], "boom")


class TestTraceInvariants(unittest.TestCase):
    def setUp(self):
        runner, _ = make_runner(OfflinePolicyModel())
        self.result = runner.run()
        self.trace = self.result["trace"]

    def test_first_record_is_run_start_and_last_is_run_end(self):
        self.assertEqual(self.trace[0]["type"], "run_start")
        self.assertEqual(self.trace[-1]["type"], "run_end")

    def test_every_tool_result_follows_a_tool_action(self):
        pending = None
        for record in self.trace:
            if record["type"] == "model_action" and record["action"]["type"] == "tool":
                pending = record["action"]["name"]
            elif record["type"] == "tool_result":
                self.assertEqual(pending, record["tool"])
                pending = None
        self.assertIsNone(pending)

    def test_steps_match_actions(self):
        actions = records(self.result, "model_action")
        self.assertEqual(len(actions), self.result["usage"]["steps_used"])

    def test_each_action_records_decision_basis(self):
        for record in records(self.result, "model_action"):
            self.assertTrue(record["rationale"])

    def test_run_end_carries_budget_and_status(self):
        end = self.trace[-1]
        self.assertIn("steps_used", end)
        self.assertIn("tool_calls_used", end)
        self.assertEqual(end["status"], self.result["status"])


if __name__ == "__main__":
    unittest.main()
