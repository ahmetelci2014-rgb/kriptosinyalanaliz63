import unittest

import day_trading_candidate_tracker as tracker


class CandidateTrackerTests(unittest.TestCase):
    def _candidate(self):
        return {
            "candidate_id": "TEST_LONG_1",
            "direction": "LONG",
            "entry": 100.0,
            "initial_stop": 99.0,
            "current_stop": 99.0,
            "risk_abs": 1.0,
            "tp1": 101.2,
            "tp2": 102.0,
            "tp3": 103.0,
            "stage": 0,
            "remaining": 1.0,
            "realized_r": 0.0,
            "stop_r": -1.0,
            "status": "OPEN",
            "highest_target": "NONE",
            "events": [],
        }

    def test_full_stop_is_minus_one_r_before_cost(self):
        candidate = self._candidate()
        closed = tracker._apply_candidate_bar(
            candidate,
            {"ts": 1_000_000, "high": 100.5, "low": 98.9},
        )
        self.assertTrue(closed)
        self.assertEqual(candidate["final_result"], "SL")
        self.assertAlmostEqual(candidate["gross_r"], -1.0, places=4)
        self.assertAlmostEqual(candidate["net_r"], -1.15, places=4)

    def test_tp1_then_protected_stop_is_positive(self):
        candidate = self._candidate()
        closed = tracker._apply_candidate_bar(
            candidate,
            {"ts": 1_000_000, "high": 101.3, "low": 99.5},
        )
        self.assertFalse(closed)
        self.assertEqual(candidate["stage"], 1)
        self.assertEqual(candidate["highest_target"], "TP1")
        self.assertAlmostEqual(candidate["current_stop"], 100.1, places=4)

        closed = tracker._apply_candidate_bar(
            candidate,
            {"ts": 1_300_000, "high": 101.5, "low": 100.0},
        )
        self.assertTrue(closed)
        self.assertEqual(candidate["final_result"], "PROTECTED_AFTER_TP1")
        self.assertAlmostEqual(candidate["gross_r"], 0.485, places=4)
        self.assertAlmostEqual(candidate["net_r"], 0.335, places=4)

    def test_full_tp3_path_matches_live_r_model(self):
        candidate = self._candidate()
        self.assertFalse(
            tracker._apply_candidate_bar(
                candidate,
                {"ts": 1_000_000, "high": 101.3, "low": 99.5},
            )
        )
        self.assertFalse(
            tracker._apply_candidate_bar(
                candidate,
                {"ts": 1_300_000, "high": 102.1, "low": 100.5},
            )
        )
        self.assertTrue(
            tracker._apply_candidate_bar(
                candidate,
                {"ts": 1_600_000, "high": 103.1, "low": 101.5},
            )
        )
        self.assertEqual(candidate["final_result"], "TP3")
        self.assertEqual(candidate["highest_target"], "TP3")
        self.assertAlmostEqual(candidate["gross_r"], 2.02, places=4)
        self.assertAlmostEqual(candidate["net_r"], 1.87, places=4)

    def test_summary_separates_unsent_candidates(self):
        ledger = {
            "candidates": {
                "a": {
                    "status": "CLOSED",
                    "telegram_sent": False,
                    "highest_target": "TP1",
                    "final_result": "PROTECTED_AFTER_TP1",
                    "net_r": 0.335,
                    "trigger_passed": False,
                },
                "b": {
                    "status": "CLOSED",
                    "telegram_sent": True,
                    "highest_target": "TP3",
                    "final_result": "TP3",
                    "net_r": 1.87,
                    "trigger_passed": True,
                },
            }
        }
        summary = tracker._build_summary(ledger)
        self.assertEqual(summary["total_candidates"], 2)
        self.assertEqual(summary["unsent_candidates"], 1)
        self.assertEqual(summary["telegram_linked_candidates"], 1)
        self.assertEqual(summary["unsent_tp1_or_better_candidates"], 1)
        self.assertAlmostEqual(summary["unsent_total_net_r"], 0.335, places=4)


if __name__ == "__main__":
    unittest.main()
