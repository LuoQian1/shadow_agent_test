"""规则引擎单元测试：10 条规则的三态（pass / missing / risk）与分类（review / scheduling）。"""

from __future__ import annotations

import unittest

import helpers
from src.domain import rules as rules_engine

D = helpers.DATASET


def brief_of(brief_id):
    return D.brief(brief_id)


def evaluate(material_id, brief, entries=None, self_material=None):
    return rules_engine.evaluate(
        D.material(material_id),
        brief,
        entries if entries is not None else [],
        D.rules,
        D.blacklist,
        self_material or material_id,
    )


def ids(findings):
    return [item.rule_id for item in findings]


class TestRuleEngine(unittest.TestCase):
    def test_clean_material_triggers_nothing(self):
        self.assertEqual(ids(evaluate("M02", brief_of("B-M02-v1"))), [])

    def test_r01_missing_duration_is_missing_kind(self):
        findings = evaluate("M03", brief_of("B-M03-v1"))
        names = [f for f in findings if f.rule_id == "R01"]
        self.assertEqual(len(names), 1)
        self.assertEqual(names[0].kind, "missing")
        self.assertIn("duration_sec", names[0].evidence["missing_fields"])

    def test_r02_null_license_is_missing_not_risk(self):
        findings = [f for f in evaluate("M03", brief_of("B-M03-v1")) if f.rule_id == "R02"]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].kind, "missing")

    def test_r02_false_license_is_block_risk(self):
        material = dict(D.material("M02"))
        material["music_license"] = False
        findings = rules_engine.evaluate(
            material, brief_of("B-M02-v1"), [], D.rules, D.blacklist, "M02"
        )
        r02 = [f for f in findings if f.rule_id == "R02"]
        self.assertEqual(len(r02), 1)
        self.assertEqual(r02[0].kind, "risk")
        self.assertEqual(r02[0].severity, "block")

    def test_r03_portrait_false_blocks(self):
        findings = evaluate("M04", brief_of("B-M04-v1"))
        r03 = [f for f in findings if f.rule_id == "R03"]
        self.assertEqual(len(r03), 1)
        self.assertEqual(r03[0].severity, "block")

    def test_r03_skipped_when_no_person(self):
        self.assertNotIn("R03", ids(evaluate("M02", brief_of("B-M02-v1"))))

    def test_r04_blacklist_term_blocks(self):
        findings = evaluate("M04", brief_of("B-M04-v1"))
        r04 = [f for f in findings if f.rule_id == "R04"]
        self.assertEqual(len(r04), 1)
        self.assertIn("全网第一", r04[0].evidence["hits"])

    def test_r05_duration_over_limit_is_warn(self):
        findings = evaluate("M07", brief_of("B-M07-v1"))
        r05 = [f for f in findings if f.rule_id == "R05"]
        self.assertEqual(len(r05), 1)
        self.assertEqual(r05[0].severity, "warn")

    def test_r06_deadline_in_past_blocks(self):
        brief = dict(brief_of("B-M02-v1"))
        brief["deadline"] = "2025-05-01T00:00"
        findings = evaluate("M02", brief)
        self.assertIn("R06", ids(findings))

    def test_r10_pending_review_blocks(self):
        findings = evaluate("M08", brief_of("B-M08-v1"))
        self.assertIn("R10", ids(findings))

    def test_r07_occupied_slot_is_scheduling_category(self):
        findings = evaluate("M01", brief_of("B-M01-v1"), helpers.schedule_entries())
        r07 = [f for f in findings if f.rule_id == "R07"]
        self.assertEqual(len(r07), 1)
        self.assertEqual(r07[0].category, "scheduling")
        self.assertEqual(r07[0].evidence["occupant"], "M09")

    def test_r07_quota_exceeded(self):
        entries = [
            {
                "date": "2025-06-10",
                "time": "%02d:00" % (9 + index),
                "channel": "douyin",
                "duration_min": 30,
                "material_id": "M09",
                "status": "confirmed",
            }
            for index in range(6)
        ]
        brief = {
            "brief_id": "tmp",
            "material_id": "M02",
            "requested_date": "2025-06-10",
            "requested_time": "21:00",
            "channel": "douyin",
            "time_constraint": "soft",
        }
        findings = evaluate("M02", brief, helpers.planner().build_entries(entries, []))
        messages = " ".join(f.message for f in findings if f.rule_id == "R07")
        self.assertIn("配额已满", messages)

    def test_r08_duplicate_content_hash_needs_confirm(self):
        entries = helpers.planner().build_entries(
            [
                {
                    "date": "2025-06-11",
                    "time": "10:00",
                    "channel": "douyin",
                    "duration_min": 30,
                    "material_id": "M02",
                    "status": "confirmed",
                }
            ],
            [],
        )
        brief = {
            "brief_id": "tmp",
            "material_id": "M12",
            "requested_date": "2025-06-11",
            "requested_time": "15:00",
            "channel": "douyin",
            "time_constraint": "soft",
        }
        findings = evaluate("M12", brief, entries)
        r08 = [f for f in findings if f.rule_id == "R08"]
        self.assertEqual(len(r08), 1)
        self.assertEqual(r08[0].severity, "confirm")

    def test_r09_blackout_is_scheduling(self):
        brief = dict(brief_of("B-M02-v1"))
        brief["requested_time"] = "12:30"
        findings = evaluate("M02", brief)
        r09 = [f for f in findings if f.rule_id == "R09"]
        self.assertEqual(len(r09), 1)
        self.assertEqual(r09[0].category, "scheduling")

    def test_summary_prioritizes_missing_over_block(self):
        # M04 本来 blocked；把 portrait 改成 None 后应变成 missing_info 优先
        material = dict(D.material("M04"))
        material["portrait_consent"] = None
        material["title"] = "夏日防晒实测"
        findings = rules_engine.evaluate(
            material, brief_of("B-M04-v1"), [], D.rules, D.blacklist, "M04"
        )
        summary = rules_engine.summarize(findings)
        self.assertEqual(summary["expected_status"], "missing_info")

    def test_scheduling_findings_do_not_change_review_status(self):
        findings = evaluate("M01", brief_of("B-M01-v1"), helpers.schedule_entries())
        summary = rules_engine.summarize(findings)
        self.assertEqual(summary["expected_status"], "pass")
        self.assertEqual([f["rule_id"] for f in summary["scheduling"]], ["R07"])


if __name__ == "__main__":
    unittest.main()
