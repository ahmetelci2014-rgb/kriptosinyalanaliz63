import unittest

import dashboard_performance_app as performance


class PerformanceIntelligenceTests(unittest.TestCase):
    def setUp(self):
        self.now = 2_000_000_000

    def _result(self, *, days_ago=1, system="PREMIUM", outcome="TP3", r=1.0):
        return {
            "system": system,
            "outcome": outcome,
            "r_result": r,
            "closed_at": self.now - days_ago * performance.DAY,
        }

    def test_summarize_rows_counts_tp_sl_and_r(self):
        rows = [
            self._result(outcome="TP3", r=1.6),
            self._result(outcome="SL", r=-1.0),
            self._result(outcome="TP1_SONRASI_BE", r=0.5),
        ]
        summary = performance._summarize_rows(rows)
        self.assertEqual(summary["sample"], 3)
        self.assertEqual(summary["tp"], 1)
        self.assertEqual(summary["sl"], 1)
        self.assertEqual(summary["be"], 1)
        self.assertAlmostEqual(summary["net_r"], 1.1)

    def test_manual_14_day_period_separates_previous_window(self):
        data = {
            "summary": {"closed_total": 4},
            "recent_results": [
                self._result(days_ago=2, r=1.2),
                self._result(days_ago=5, outcome="SL", r=-1.0),
                self._result(days_ago=16, r=1.0),
                self._result(days_ago=20, outcome="SL", r=-1.0),
            ],
        }
        period = performance._manual_period(data, "PREMIUM", 14, self.now)
        self.assertEqual(period["current"]["sample"], 2)
        self.assertEqual(period["previous"]["sample"], 2)
        self.assertAlmostEqual(period["current"]["net_r"], 0.2)
        self.assertAlmostEqual(period["previous"]["net_r"], 0.0)
        self.assertTrue(period["coverage_complete"])

    def test_trend_detects_improving_and_weakening(self):
        improving = performance._trend(
            {"current": {"exact_r_sample": 5, "net_r": 2.0}, "net_r_delta": 1.2},
            {"current": {"exact_r_sample": 10, "net_r": 4.0}, "net_r_delta": 0.8},
        )
        weakening = performance._trend(
            {"current": {"exact_r_sample": 5, "net_r": -2.0}, "net_r_delta": -1.2},
            {"current": {"exact_r_sample": 10, "net_r": -4.0}, "net_r_delta": -0.8},
        )
        self.assertEqual(improving["code"], "IMPROVING")
        self.assertEqual(weakening["code"], "WEAKENING")

    def test_tp_continuation_reads_events_and_flags(self):
        ledger = {
            "trades": {
                "a": {
                    "closed_at": self.now - performance.DAY,
                    "final_result": "TP3",
                    "events": [{"event": "TP1"}, {"event": "TP2"}, {"event": "TP3"}],
                },
                "b": {
                    "closed_at": self.now - 2 * performance.DAY,
                    "final_result": "TP1_SONRASI_BE",
                    "tp1_hit": True,
                },
                "c": {
                    "closed_at": self.now - 3 * performance.DAY,
                    "final_result": "TP2_SONRASI_BE",
                    "tp1_hit": True,
                    "tp2_hit": True,
                },
            }
        }
        result = performance.analyze_tp_continuation(ledger, now=self.now)
        self.assertEqual(result["tp1_sample"], 3)
        self.assertEqual(result["tp2_after_tp1"], 2)
        self.assertEqual(result["tp3_after_tp1"], 1)
        self.assertEqual(result["be_after_tp1"], 2)

    def test_stop_diagnosis_uses_root_cause_and_follow_status(self):
        ledger = {
            "trades": {
                "a": {
                    "closed_at": self.now - performance.DAY,
                    "final_result": "SL",
                    "stop_root_cause": {"label": "Fitil/dar stop", "provisional": False},
                    "post_stop_follow": {"status": "RETURNED_TO_TARGET", "returned_level": "TP2"},
                },
                "b": {
                    "closed_at": self.now - 2 * performance.DAY,
                    "final_result": "SL",
                    "stop_root_cause": {"label": "Muhtemel yanlış yön", "provisional": False},
                    "post_stop_follow": {"status": "NO_TP1_RETURN"},
                },
                "c": {
                    "closed_at": self.now - 3 * performance.DAY,
                    "final_result": "SL",
                    "diagnosis": {"primary": "Takip sürüyor", "provisional": True},
                },
            }
        }
        result = performance.analyze_stop_diagnosis(ledger, now=self.now)
        self.assertEqual(result["sl_total"], 3)
        self.assertEqual(result["returned_to_target"], 1)
        self.assertEqual(result["no_tp1_return"], 1)
        self.assertEqual(result["tracking"], 1)
        self.assertEqual(result["provisional"], 1)
        self.assertEqual(result["return_rate"], 50.0)

    def test_window_intelligence_uses_existing_7_and_30_day_comparisons(self):
        comparison_rows = []
        for system in performance.SYSTEM_ORDER:
            comparison_rows.append({
                "system": system,
                "label": performance.SYSTEM_LABELS[system],
                "current": {"sample": 10, "tp": 6, "sl": 3, "tp_rate": 60.0, "sl_rate": 30.0, "exact_r_sample": 8, "net_r": 2.0},
                "previous": {"sample": 10, "tp": 5, "sl": 4, "tp_rate": 50.0, "sl_rate": 40.0, "exact_r_sample": 8, "net_r": 1.0},
                "sample_delta": 0,
                "net_r_delta": 1.0,
            })
        data = {
            "period_comparisons": {
                "7D": {"rows": comparison_rows},
                "30D": {"rows": comparison_rows},
            },
            "summary": {"closed_total": 2},
            "recent_results": [
                self._result(days_ago=2, r=1.0),
                self._result(days_ago=4, r=1.0),
            ],
        }
        result = performance.build_window_intelligence(data, now=self.now)
        self.assertEqual(len(result["systems"]), len(performance.SYSTEM_ORDER))
        self.assertEqual(result["overall"]["trend"]["code"], "IMPROVING")
        self.assertEqual(result["overall"]["periods"]["14D"]["current"]["sample"], 2)


if __name__ == "__main__":
    unittest.main()
