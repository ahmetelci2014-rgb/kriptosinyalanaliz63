import unittest

import post_result_shadow_v3 as v3


class PostResultShadowV3Tests(unittest.TestCase):
    def test_soft_be_uses_stop_first_on_ambiguous_candle(self):
        candles = [{"time": 1, "high": 102.0, "low": 99.0, "close": 101.0}]
        result = v3.simulate_fixed_stop_target(
            "MODEL", candles, "LONG", 100.0, 2.0, -0.25, 1.0,
        )
        self.assertEqual(result["exit_reason"], "SHADOW_STOP")
        self.assertEqual(result["incremental_r"], -0.25)

    def test_delayed_be_moves_stop_only_after_tp2(self):
        candles = [
            {"time": 1, "high": 101.1, "low": 100.1, "close": 100.8},
            {"time": 2, "high": 103.1, "low": 100.2, "close": 103.0},
        ]
        result = v3.simulate_delayed_be(
            candles, "LONG", 100.0, 2.0, tp2_r=0.5, tp3_r=1.5,
        )
        self.assertEqual(result["exit_reason"], "SHADOW_TP3")
        self.assertEqual(result["incremental_r"], 1.5)

    def test_runner_never_gives_back_below_tp3_reference(self):
        candles = [
            {"time": 1, "high": 102.0, "low": 100.2, "close": 101.8},
            {"time": 2, "high": 102.2, "low": 100.8, "close": 101.0},
        ]
        result = v3.simulate_runner(candles, "LONG", 100.0, 2.0, 0.5)
        self.assertGreaterEqual(result["incremental_r"], 0.0)

    def test_report_never_auto_applies(self):
        ledger = {"trades": {"x": {"post_result_shadow_v3": {
            "version": v3.VERSION,
            "models": {"M": {"incremental_r": 1.0}},
        }}}}
        report = v3.build_report(ledger, generated_at=1)
        self.assertFalse(report["decision"]["automatic_rule_change"])
        self.assertEqual(report["models"]["M"]["evidence_gate"], "OBSERVE_ONLY")


if __name__ == "__main__":
    unittest.main()
