"""端到端场景测试：11 个用例逐个跑，断言 expect + 不变量。

和 CLI 的 `replay` 共用 check_case_expectations，因此「examples 里的产物」与
「测试通过的标准」是同一份口径。
"""

from __future__ import annotations

import json
import os
import unittest

import helpers
from src.runner import check_case_expectations, worst_exit_code


class TestScenarios(unittest.TestCase):
    """按目录顺序串行执行（共享同一个草稿库，等价于 CLI replay）。"""

    results = {}
    store = None

    @classmethod
    def setUpClass(cls):
        cls.store = helpers.temp_store()
        for name in helpers.case_names():
            case = helpers.load(name)
            cls.results[name] = (case, helpers.run(case, store=cls.store))

    def test_all_cases_present(self):
        self.assertEqual(len(helpers.case_names()), 11)

    def test_each_case_matches_expectations(self):
        for name in helpers.case_names():
            case, results = self.results[name]
            with self.subTest(case=name):
                failures = check_case_expectations(case, results, self.store)
                self.assertEqual(failures, [], "%s 断言失败：%s" % (name, failures))

    def test_repeat_case_creates_single_draft(self):
        case, results = self.results["E05_duplicate_submit"]
        first = (results[0]["output"]["materials"][0]["draft"])
        second = (results[1]["output"]["materials"][0]["draft"])
        self.assertEqual(first["save_status"], "saved")
        self.assertEqual(second["save_status"], "duplicate")
        self.assertEqual(first["draft_id"], second["draft_id"])
        active = self.store.active_drafts(material_id="M01", date="2025-06-03")
        self.assertEqual(len(active), 1)

    def test_no_output_claims_publish(self):
        for name, (case, results) in self.results.items():
            with self.subTest(case=name):
                for item in results:
                    blob = json.dumps(item["output"], ensure_ascii=False)
                    for word in ("已发布", "发布成功", "已上线"):
                        self.assertNotIn(word, blob)
                    self.assertFalse(item["output"]["materials"][0]["published"])
                    self.assertTrue(item["output"]["no_publish_capability"])

    def test_save_failure_case_reports_failure(self):
        case, results = self.results["E06_save_failure"]
        output = results[-1]["output"]
        self.assertEqual(output["status"], "failed")
        self.assertEqual(output["materials"][0]["draft"]["save_status"], "failed")
        self.assertEqual(worst_exit_code(results), 2)
        self.assertIn("EACCES", output["materials"][0]["draft"]["error"])

    def test_hard_constraint_case_never_saved(self):
        case, results = self.results["E07_hard_constraint"]
        output = results[-1]["output"]
        material = output["materials"][0]
        self.assertIsNone(material["suggested_slot"])
        self.assertEqual(material["draft"]["save_status"], "not_attempted")
        self.assertEqual(material["decision"], "conflict_blocked")
        self.assertEqual(self.store.active_drafts(material_id="M05"), [])

    def test_claim_violation_case_is_intercepted(self):
        case, results = self.results["E10_claim_violation"]
        output = results[-1]["output"]
        codes = [item["code"] for item in output["verifier"]["overrides"]]
        self.assertIn("FORBIDDEN_PUBLISH_CLAIM", codes)
        self.assertIn("SAVE_CLAIM_MISMATCH", codes)
        self.assertTrue(output["verifier"]["critical"])
        self.assertEqual(output["status"], "failed")

    def test_limit_case_is_incomplete(self):
        case, results = self.results["E11_step_limit"]
        output = results[-1]["output"]
        self.assertEqual(output["status"], "incomplete_limit_reached")
        self.assertEqual(worst_exit_code(results), 3)

    def test_missing_info_case_did_recheck_assets(self):
        case, results = self.results["E03_missing_info"]
        material = results[-1]["output"]["materials"][0]
        self.assertTrue(material["evidence"]["assets_fetched"])
        self.assertEqual(material["review_status"], "missing_info")

    def test_traces_exist_for_every_run(self):
        for name, (case, results) in self.results.items():
            with self.subTest(case=name):
                for item in results:
                    types = [record["type"] for record in item["run"]["trace"]]
                    self.assertIn("run_start", types)
                    self.assertIn("run_end", types)
                    self.assertGreaterEqual(len(types), 3)

    def test_artifacts_on_disk_are_consistent(self):
        """examples/ 里的产物必须与「当前代码 + 当前用例」跑出来的一致（防止文档过期）。"""
        for name in helpers.case_names():
            case_dir = os.path.join(helpers.CASES_DIR, name)
            output_path = os.path.join(case_dir, "actual_output.json")
            trace_path = os.path.join(case_dir, "trace.jsonl")
            self.assertTrue(os.path.isfile(output_path), "%s 缺少 actual_output.json" % name)
            self.assertTrue(os.path.isfile(trace_path), "%s 缺少 trace.jsonl" % name)
            with open(output_path, "r", encoding="utf-8") as handle:
                stored = json.load(handle)
            fresh = self.results[name][1][-1]["output"]
            self.assertEqual(
                stored["materials"][0]["decision"],
                fresh["materials"][0]["decision"],
                "%s 的 examples 产物已过期，请重跑 replay" % name,
            )
            self.assertEqual(stored["status"], fresh["status"])


if __name__ == "__main__":
    unittest.main()
