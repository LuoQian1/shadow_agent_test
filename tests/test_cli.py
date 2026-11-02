"""CLI 测试：退出码、产物落盘、inspect / reset-store / replay 子命令。"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest

import helpers
from src.cli import main


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agent-cli-")
        self.store = os.path.join(self.tmp, "store")
        self.out = os.path.join(self.tmp, "out")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def run_case(self, name, extra=None):
        argv = [
            "run",
            "--case",
            helpers.case_path(name),
            "--model",
            "offline",
            "--store",
            self.store,
            "--out",
            self.out,
            "--reset-store",
        ]
        return self.cli(argv + list(extra or []))

    def test_normal_run_exits_zero_and_writes_artifacts(self):
        code, out = self.run_case("E01_normal")
        self.assertEqual(code, 0, out)
        for name in ("actual_output.json", "report.md", "trace.jsonl"):
            self.assertTrue(os.path.isfile(os.path.join(self.out, name)), name)

    def test_save_failure_exits_two(self):
        code, out = self.run_case("E06_save_failure")
        self.assertEqual(code, 2, out)

    def test_limit_exits_three(self):
        code, out = self.run_case("E11_step_limit")
        self.assertEqual(code, 3, out)

    def test_missing_case_file_is_usage_error(self):
        code, _ = self.cli(["run", "--case", os.path.join(self.tmp, "nope.json")])
        self.assertEqual(code, 1)

    def test_no_subcommand_prints_help(self):
        code, out = self.cli([])
        self.assertEqual(code, 1)
        self.assertIn("素材审核与发布排期", out)

    def test_json_flag_prints_valid_output(self):
        code, out = self.run_case("E02_slot_conflict", extra=["--json"])
        self.assertEqual(code, 0)
        start = out.find("{")
        payload = json.loads(out[start:])
        self.assertEqual(payload["materials"][0]["suggested_slot"]["time"], "19:00")

    def test_inspect_reads_trace(self):
        self.run_case("E02_slot_conflict")
        code, out = self.cli(
            ["inspect", "--trace", os.path.join(self.out, "trace.jsonl")]
        )
        self.assertEqual(code, 0)
        self.assertIn("TOOL query_slots", out)
        self.assertIn("结论：status=completed", out)

    def test_reset_store_command(self):
        self.run_case("E01_normal")
        code, out = self.cli(["reset-store", "--store", self.store])
        self.assertEqual(code, 0)
        self.assertIn("草稿库已清空", out)

    def test_replay_on_copied_cases(self):
        cases_dir = os.path.join(self.tmp, "cases")
        for name in ("E01_normal", "E05_duplicate_submit"):
            target = os.path.join(cases_dir, name)
            os.makedirs(target)
            shutil.copy(helpers.case_path(name), os.path.join(target, "input.json"))
        code, out = self.cli(
            ["replay", "--cases", cases_dir, "--model", "offline", "--store", self.store]
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("PASS"), 2)
        self.assertIn("全部用例符合预期", out)

    def test_replay_detects_broken_expectation(self):
        """把期望改错，replay 必须报 FAIL 并以 4 退出（说明断言真的在生效）。"""
        cases_dir = os.path.join(self.tmp, "cases")
        target = os.path.join(cases_dir, "E01_normal")
        os.makedirs(target)
        with open(helpers.case_path("E01_normal"), "r", encoding="utf-8") as handle:
            case = json.load(handle)
        case["expect"]["suggested_time"] = "09:00"
        with open(os.path.join(target, "input.json"), "w", encoding="utf-8") as handle:
            json.dump(case, handle, ensure_ascii=False)
        code, out = self.cli(
            ["replay", "--cases", cases_dir, "--model", "offline", "--store", self.store]
        )
        self.assertEqual(code, 4, out)
        self.assertIn("FAIL", out)


if __name__ == "__main__":
    unittest.main()
