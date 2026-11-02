"""声明校验层测试：谎报保存/发布、审核状态放水、改动硬性要求都必须被打回。"""

from __future__ import annotations

import unittest

import helpers  # noqa: F401  (确保 src 在 sys.path)
from src.agent import verifier


def facts(review_status="pass", hard=False, requested="18:00", save=None, block=None, missing=None):
    return {
        "review": {
            "expected_status": review_status,
            "block": block or [],
            "missing": missing or [],
        },
        "brief": {
            "time_constraint": "hard" if hard else "soft",
            "requested_time": requested,
        },
        "last_save": save or {},
    }


def codes(overrides):
    return [item["code"] for item in overrides]


class TestForbiddenPublish(unittest.TestCase):
    def test_published_claim_is_removed_and_flagged(self):
        claims, overrides = verifier.verify({"published": True, "notes": "ok"}, facts())
        self.assertIn("FORBIDDEN_PUBLISH_CLAIM", codes(overrides))
        self.assertNotIn("published", claims)
        self.assertTrue(verifier.has_critical(overrides))

    def test_publish_wording_in_notes_is_rewritten(self):
        claims, overrides = verifier.verify({"notes": "已发布上线"}, facts())
        self.assertIn("PUBLISH_WORDING", codes(overrides))
        self.assertNotIn("已发布", claims["notes"])

    def test_all_publish_fields_checked(self):
        claims, overrides = verifier.verify(
            {"is_published": True, "publish_status": "done", "published_at": "now"}, facts()
        )
        self.assertEqual(codes(overrides).count("FORBIDDEN_PUBLISH_CLAIM"), 3)
        for field in ("is_published", "publish_status", "published_at"):
            self.assertNotIn(field, claims)


class TestSaveClaim(unittest.TestCase):
    def test_claims_saved_without_ledger_is_downgraded(self):
        claims, overrides = verifier.verify({"save_status": "saved"}, facts())
        self.assertIn("SAVE_CLAIM_MISMATCH", codes(overrides))
        self.assertEqual(claims["save_status"], "not_attempted")
        self.assertTrue(verifier.has_critical(overrides))

    def test_claims_duplicate_but_ledger_saved_is_downgraded(self):
        claims, overrides = verifier.verify(
            {"save_status": "duplicate"}, facts(save={"save_status": "saved"})
        )
        self.assertIn("SAVE_CLAIM_MISMATCH", codes(overrides))
        self.assertEqual(claims["save_status"], "saved")

    def test_matching_claim_is_accepted(self):
        claims, overrides = verifier.verify(
            {"save_status": "saved"}, facts(save={"save_status": "saved"})
        )
        self.assertEqual(overrides, [])
        self.assertEqual(claims["save_status"], "saved")

    def test_failed_save_claim_is_accepted(self):
        _, overrides = verifier.verify({"save_status": "failed"}, facts(save={"save_status": "failed"}))
        self.assertEqual(overrides, [])


class TestReviewStatus(unittest.TestCase):
    def test_cannot_be_more_permissive_than_facts(self):
        claims, overrides = verifier.verify({"review_status": "pass"}, facts(review_status="blocked"))
        self.assertIn("REVIEW_STATUS_ESCALATED", codes(overrides))
        self.assertEqual(claims["review_status"], "blocked")
        self.assertFalse(verifier.has_critical(overrides))

    def test_more_severe_claim_is_kept(self):
        claims, overrides = verifier.verify({"review_status": "blocked"}, facts(review_status="warn"))
        self.assertEqual(overrides, [])
        self.assertEqual(claims["review_status"], "blocked")

    def test_missing_info_cannot_be_downgraded_to_warn(self):
        claims, overrides = verifier.verify(
            {"review_status": "warn"}, facts(review_status="missing_info", missing=[{"rule_id": "R01"}])
        )
        self.assertEqual(claims["review_status"], "missing_info")


class TestHardConstraint(unittest.TestCase):
    def test_suggesting_other_time_is_blocked(self):
        claims, overrides = verifier.verify(
            {"suggested_slot": {"time": "19:00"}}, facts(hard=True, requested="18:00")
        )
        self.assertIn("HARD_CONSTRAINT_VIOLATION", codes(overrides))
        self.assertIsNone(claims["suggested_slot"])
        self.assertTrue(verifier.has_critical(overrides))

    def test_soft_requirement_allows_alternative_time(self):
        claims, overrides = verifier.verify(
            {"suggested_slot": {"time": "19:00"}}, facts(hard=False, requested="18:00")
        )
        self.assertEqual(overrides, [])
        self.assertEqual(claims["suggested_slot"]["time"], "19:00")

    def test_hard_requirement_same_time_is_fine(self):
        _, overrides = verifier.verify(
            {"suggested_slot": {"time": "18:00"}}, facts(hard=True, requested="18:00")
        )
        self.assertEqual(overrides, [])


class TestBlockedMaterial(unittest.TestCase):
    def test_saved_draft_on_blocked_material_is_flagged(self):
        _, overrides = verifier.verify(
            {"save_status": "saved"},
            facts(review_status="blocked", block=[{"rule_id": "R04"}], save={"save_status": "saved"}),
        )
        self.assertIn("SAVE_ON_BLOCKED_MATERIAL", codes(overrides))


class TestCleanPass(unittest.TestCase):
    def test_clean_claims_untouched(self):
        original = {
            "review_status": "pass",
            "suggested_slot": {"time": "19:00"},
            "save_status": "saved",
            "notes": "草稿已保存，待确认",
        }
        claims, overrides = verifier.verify(original, facts(save={"save_status": "saved"}))
        self.assertEqual(overrides, [])
        self.assertEqual(claims, original)


if __name__ == "__main__":
    unittest.main()
