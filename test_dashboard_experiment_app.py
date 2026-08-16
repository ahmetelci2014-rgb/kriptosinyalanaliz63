import unittest

import dashboard_experiment_app as experiment


class ExperimentReadinessTests(unittest.TestCase):
    def _base_payload(self):
        return {
            "summary": {"live_review_allowed": True},
            "decision_engine": {"actions": [{"component": "PREMIUM", "decision_code": "KORU"}]},
            "candidates": [],
        }

    def test_core_guard_requires_premium_keep_and_freshness(self):
        guard = experiment.build_core_guard(self._base_payload())
        self.assertTrue(guard["overall_pass"])
        self.assertFalse(guard["automatic_apply"])

    def test_premium_protection_guard_blocks_promotion(self):
        payload = self._base_payload()
        payload["candidates"] = [{"id": "PREMIUM_PROTECTION_GUARD", "status": "WATCH_PROTECT"}]
        guard = experiment.build_core_guard(payload)
        self.assertFalse(guard["overall_pass"])
        self.assertFalse(guard["premium_trend_pass"])

    def test_runner_at_35_samples_shows_missing_evidence(self):
        payload = self._base_payload()
        payload["candidates"] = [{
            "id": "TP3_RUNNER_TRAIL_0_5R", "family": "TP3_RUNNER", "label": "Runner",
            "source": "POST_RESULT_SHADOW_V3", "status": "PROMOTION_CANDIDATE", "sample": 35,
            "net_incremental_r": 4.29, "average_incremental_r": 0.1226, "negative_rate": 0.0,
        }]
        registry = experiment.build_experiment_registry(payload)
        row = registry["experiments"][0]
        self.assertEqual(row["readiness"]["stage"], "SECOND_VALIDATION")
        self.assertEqual(row["readiness"]["sample_gap"], 15)
        self.assertAlmostEqual(row["readiness"]["net_r_gap"], 0.71, places=2)
        self.assertEqual(registry["summary"]["promotion_packets"], 0)

    def test_ready_runner_creates_packet_but_never_auto_applies(self):
        payload = self._base_payload()
        payload["candidates"] = [{
            "id": "TP3_RUNNER_TRAIL_0_5R", "family": "TP3_RUNNER", "label": "Runner",
            "source": "POST_RESULT_SHADOW_V3", "status": "LIVE_REVIEW_READY", "sample": 60,
            "net_incremental_r": 6.2, "average_incremental_r": 0.10, "negative_rate": 5.0,
        }]
        registry = experiment.build_experiment_registry(payload)
        self.assertEqual(registry["experiments"][0]["readiness"]["stage"], "REVIEW_READY")
        self.assertEqual(len(registry["promotion_packets"]), 1)
        self.assertFalse(registry["promotion_packets"][0]["automatic_apply"])
        self.assertFalse(registry["promotion_packets"][0]["automatic_rollback"])

    def test_stale_guard_prevents_packet(self):
        payload = self._base_payload()
        payload["summary"]["live_review_allowed"] = False
        payload["candidates"] = [{
            "id": "TP3_RUNNER_TRAIL_0_5R", "family": "TP3_RUNNER", "label": "Runner",
            "source": "POST_RESULT_SHADOW_V3", "status": "LIVE_REVIEW_READY", "sample": 60,
            "net_incremental_r": 6.2, "average_incremental_r": 0.10, "negative_rate": 5.0,
        }]
        registry = experiment.build_experiment_registry(payload)
        self.assertEqual(registry["experiments"][0]["readiness"]["stage"], "SECOND_VALIDATION")
        self.assertEqual(registry["promotion_packets"], [])

    def test_stop_candidate_stays_shadow_only(self):
        payload = self._base_payload()
        payload["candidates"] = [{
            "id": "STOP_ENTRY_TIMING_REVIEW", "label": "Stop teşhisi", "source": "PERFORMANCE_INTELLIGENCE",
            "status": "NEW_SHADOW_TEST", "sample": 30, "return_rate": 46.0,
        }]
        registry = experiment.build_experiment_registry(payload)
        row = registry["experiments"][0]
        self.assertEqual(row["readiness"]["stage"], "DESIGN_SHADOW")
        self.assertFalse(row["readiness"]["ready_for_live_review"])

    def test_page_explains_score_and_actions_cost(self):
        page = experiment.render_experiment_page(experiment.build_experiment_registry(self._base_payload()))
        self.assertIn("kazanç olasılığı değildir", page)
        self.assertIn("ekstra periyodik Actions maliyeti", page)
        self.assertIn("Otomatik canlı değişiklik kapalıdır", page)


if __name__ == "__main__":
    unittest.main()
