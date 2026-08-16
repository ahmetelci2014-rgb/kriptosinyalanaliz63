import time
import unittest

import dashboard_improvement_app as improvement


class ImprovementDecisionTests(unittest.TestCase):
    def test_negative_shadow_model_is_rejected(self):
        row = improvement.classify_shadow_model(
            "TP1_DELAY_BE_UNTIL_TP2",
            {
                "sample": 52,
                "evidence_gate": "ENOUGH_SAMPLE",
                "average_incremental_r": -0.01,
                "net_incremental_r": -0.5,
                "negative_rate": 55.0,
                "positive_rate": 40.0,
            },
        )
        self.assertEqual(row["status"], "REJECT")
        self.assertFalse(row["automatic_apply"])

    def test_strong_runner_becomes_promotion_candidate(self):
        row = improvement.classify_shadow_model(
            "TP3_RUNNER_TRAIL_0_5R",
            {
                "sample": 35,
                "evidence_gate": "ENOUGH_SAMPLE",
                "average_incremental_r": 0.12,
                "net_incremental_r": 4.2,
                "negative_rate": 0.0,
                "positive_rate": 22.0,
            },
        )
        self.assertEqual(row["status"], "PROMOTION_CANDIDATE")

    def test_live_review_requires_stricter_gate(self):
        row = improvement.classify_shadow_model(
            "TP3_RUNNER_TRAIL_0_5R",
            {
                "sample": 60,
                "evidence_gate": "ENOUGH_SAMPLE",
                "average_incremental_r": 0.10,
                "net_incremental_r": 6.1,
                "negative_rate": 4.0,
                "positive_rate": 30.0,
            },
        )
        self.assertEqual(row["status"], "LIVE_REVIEW_READY")

    def test_same_family_keeps_only_best_candidate(self):
        rows = improvement.build_shadow_candidates(
            {
                "models": {
                    "TP3_RUNNER_TRAIL_0_5R": {
                        "sample": 40,
                        "evidence_gate": "ENOUGH_SAMPLE",
                        "average_incremental_r": 0.12,
                        "net_incremental_r": 4.2,
                        "negative_rate": 0.0,
                    },
                    "TP3_RUNNER_TRAIL_1_0R": {
                        "sample": 40,
                        "evidence_gate": "ENOUGH_SAMPLE",
                        "average_incremental_r": 0.10,
                        "net_incremental_r": 3.7,
                        "negative_rate": 0.0,
                    },
                }
            }
        )
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["TP3_RUNNER_TRAIL_0_5R"]["status"], "PROMOTION_CANDIDATE")
        self.assertEqual(by_id["TP3_RUNNER_TRAIL_1_0R"]["status"], "COMPARE_BACKUP")

    def test_stop_return_rate_opens_shadow_test_not_live_change(self):
        row = improvement.build_stop_experiment(
            {
                "stop_diagnosis": {
                    "sl_total": 40,
                    "resolved_follow": 30,
                    "return_rate": 46.7,
                }
            }
        )
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "NEW_SHADOW_TEST")
        self.assertFalse(row["automatic_apply"])

    def test_stale_decision_report_blocks_live_review(self):
        now = int(time.time())
        performance_payload = {
            "window_intelligence": {"systems": []},
            "stop_diagnosis": {},
        }
        decision_report = {
            "generated_at": now - 48 * 3600,
            "executive": {"overall": "OK", "top_actions": []},
        }
        shadow_report = {
            "generated_at": now,
            "models": {
                "TP3_RUNNER_TRAIL_0_5R": {
                    "sample": 60,
                    "evidence_gate": "ENOUGH_SAMPLE",
                    "average_incremental_r": 0.10,
                    "net_incremental_r": 6.5,
                    "negative_rate": 0.0,
                }
            },
        }
        payload = improvement.build_improvement_payload(
            performance_payload,
            decision_report,
            shadow_report,
            now=now,
        )
        self.assertFalse(payload["summary"]["live_review_allowed"])
        self.assertFalse(payload["auto_apply"])
        candidate = payload["candidates"][0]
        self.assertNotEqual(candidate["status"], "LIVE_REVIEW_READY")

    def test_render_page_states_auto_apply_is_off(self):
        now = int(time.time())
        payload = improvement.build_improvement_payload(
            {"window_intelligence": {"systems": []}, "stop_diagnosis": {}},
            {"generated_at": now, "executive": {"top_actions": []}},
            {"generated_at": now, "models": {}},
            now=now,
        )
        page = improvement.render_improvement_page(payload)
        self.assertIn("OTOMATİK CANLI DEĞİŞİKLİK KAPALI", page)
        self.assertIn("Terfi hattı", page)
        self.assertNotIn("strategy.py", page)


if __name__ == "__main__":
    unittest.main()
