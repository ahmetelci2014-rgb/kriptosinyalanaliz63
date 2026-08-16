from __future__ import annotations

import unittest

import dashboard_learning_app as learning


def early(outcome: str, close_r: float, mfe: float, mae: float, event: str):
    return {
        "status": "ok",
        "outcome": outcome,
        "window_close_r": close_r,
        "mfe_r": mfe,
        "mae_r": mae,
        "first_event": event,
    }


def result(system: str, direction: str, outcome: str, r_result: float | None = None):
    return {
        "system": system,
        "direction": direction,
        "outcome": outcome,
        "r_result": r_result,
    }


class LearningCohortTests(unittest.TestCase):
    def test_tp_and_stop_are_compared_separately(self):
        payload = {"samples": [
            early("TP3", 0.8, 1.5, -0.2, "TP1_FIRST"),
            early("TP2", 0.4, 1.1, -0.4, "TP1_FIRST"),
            early("SL", -0.7, 0.3, -1.0, "SL_FIRST"),
            early("SL", -0.3, 0.6, -0.9, "SL_FIRST"),
        ]}
        out = learning.build_early_cohorts(payload)
        self.assertEqual(out["tp"]["sample"], 2)
        self.assertEqual(out["sl"]["sample"], 2)
        self.assertAlmostEqual(out["tp"]["average_close_r"], 0.6)
        self.assertAlmostEqual(out["sl"]["average_close_r"], -0.5)
        self.assertAlmostEqual(out["comparison"]["close_r_gap"], 1.1)
        self.assertEqual(out["tp"]["tp1_first_rate"], 100.0)
        self.assertEqual(out["sl"]["sl_first_rate"], 100.0)


class DirectionLearningTests(unittest.TestCase):
    def test_weaker_direction_requires_gap_and_minimum_samples(self):
        rows = []
        rows += [result("PREMIUM", "LONG", "SL", -1.0) for _ in range(3)]
        rows += [result("PREMIUM", "SHORT", "TP2", 1.0) for _ in range(3)]
        out = learning.build_direction_learning({"recent_results": rows})
        premium = out[0]
        self.assertEqual(premium["weaker_direction"], "LONG")
        self.assertEqual(premium["sl_rate_gap"], 100.0)

    def test_small_direction_sample_is_not_flagged(self):
        rows = [
            result("PREMIUM", "LONG", "SL", -1.0),
            result("PREMIUM", "SHORT", "TP2", 1.0),
        ]
        out = learning.build_direction_learning({"recent_results": rows})
        self.assertIsNone(out[0]["weaker_direction"])


class LearningActionTests(unittest.TestCase):
    def test_stop_timing_and_stop_protection_can_coexist(self):
        cohorts = {
            "tp": {"sample": 3, "average_mae_r": -0.8, "average_close_r": 0.5, "average_mfe_r": 1.2},
            "sl": {"sample": 3, "average_mae_r": -1.0, "average_close_r": -0.5, "average_mfe_r": 0.4},
        }
        actions = learning.build_learning_actions(
            cohorts,
            [],
            {"profiles": []},
            {"resolved_follow": 0, "return_rate": None},
            None,
            None,
        )
        titles = {row["title"] for row in actions}
        self.assertIn("STOP işlemlerinde erken ters hareketi araştır", titles)
        self.assertIn("Stopu körlemesine daraltma", titles)

    def test_low_sample_requests_more_data(self):
        actions = learning.build_learning_actions(
            {"tp": {"sample": 1}, "sl": {"sample": 2}},
            [], {"profiles": []}, {"resolved_follow": 0}, None, None,
        )
        self.assertTrue(any(row["type"] == "COLLECT" for row in actions))


class LearningPageTests(unittest.TestCase):
    def test_page_is_admin_decision_support_and_read_only(self):
        body = learning.page("nonce320")
        self.assertIn("Sistem Öğrenme Merkezi", body)
        self.assertIn("V3.20 · ADMIN · KARAR DESTEK", body)
        self.assertIn("/api/learning-center", body)
        self.assertIn('nonce="nonce320"', body)
        self.assertNotIn("method:'POST'", body)
        self.assertNotIn('method="post"', body.lower())

    def test_navigation_injection_is_idempotent(self):
        body = '<a class="btn" href="/early-performance">15 dk Analizi</a>'
        once = learning.enhance_admin_navigation(body, "/system-quality")
        twice = learning.enhance_admin_navigation(once, "/system-quality")
        self.assertEqual(once, twice)
        self.assertEqual(once.count('href="/learning-center"'), 1)


class LearningSourceBoundaryTests(unittest.TestCase):
    def test_version_and_safety_boundary(self):
        self.assertIn("V3_20", learning.VERSION)
        source = open(learning.__file__, encoding="utf-8").read()
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)
        self.assertIn('"automatic_filter": False', source)
        self.assertIn("admin_only", source)


if __name__ == "__main__":
    unittest.main()
