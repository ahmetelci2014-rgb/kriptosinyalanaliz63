import json
import tempfile
import unittest
from pathlib import Path

from momentum_shadow import (
    build_summary,
    evaluate_feature_snapshot,
    load_json,
    save_json_atomically,
)


class MomentumShadowTests(unittest.TestCase):
    def test_strong_profile_passes(self):
        result = evaluate_feature_snapshot({
            "direction": "LONG",
            "adx_4h": 30,
            "adx_1h": 22,
            "volume_ratio_15m": 1.4,
            "entry_distance_percent": 0.10,
            "slope_5m_ok": True,
            "slope_15m_ok": True,
            "macd_5m_ok": True,
            "macd_15m_ok": True,
            "candle_direction_ok": True,
            "rejection_ok": True,
            "recent_retest": True,
            "market_guard_allowed": True,
        })
        self.assertEqual(result["decision"], "PASS")
        self.assertFalse(result["would_block"])

    def test_multiple_critical_failures_would_block(self):
        result = evaluate_feature_snapshot({
            "direction": "SHORT",
            "adx_4h": 13,
            "adx_1h": 11,
            "volume_ratio_15m": 0.55,
            "entry_distance_percent": 0.50,
            "slope_5m_ok": False,
            "slope_15m_ok": False,
            "macd_5m_ok": False,
            "macd_15m_ok": False,
            "candle_direction_ok": False,
            "rejection_ok": False,
            "recent_retest": False,
            "market_guard_allowed": False,
        })
        self.assertEqual(result["decision"], "WOULD_BLOCK")
        self.assertTrue(result["would_block"])
        self.assertGreaterEqual(len(result["critical_reasons"]), 2)

    def test_single_borderline_issue_does_not_block(self):
        result = evaluate_feature_snapshot({
            "direction": "LONG",
            "adx_4h": 16,
            "adx_1h": 21,
            "volume_ratio_15m": 1.2,
            "entry_distance_percent": 0.14,
            "slope_5m_ok": True,
            "slope_15m_ok": True,
            "macd_5m_ok": True,
            "macd_15m_ok": True,
            "candle_direction_ok": True,
            "rejection_ok": True,
            "recent_retest": True,
            "market_guard_allowed": True,
        })
        self.assertFalse(result["would_block"])
        self.assertIn(result["decision"], {"PASS", "CAUTION"})

    def test_atomic_json_roundtrip(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "ledger.json"
            payload = {"records": {"x": {"decision": "PASS"}}}
            self.assertTrue(save_json_atomically(str(path), payload))
            self.assertEqual(load_json(str(path)), payload)

    def test_summary_detects_blocked_winner(self):
        records = {
            "a": {
                "evaluation": {"decision": "WOULD_BLOCK", "would_block": True},
                "outcome": {
                    "resolved": True,
                    "r_result": 0.5,
                    "tp1_hit": True,
                },
            },
            "b": {
                "evaluation": {"decision": "PASS", "would_block": False},
                "outcome": {
                    "resolved": True,
                    "r_result": -1.0,
                    "tp1_hit": False,
                },
            },
        }
        summary = build_summary(records)
        self.assertEqual(summary["blocked_winners"], 1)
        self.assertEqual(summary["passed_losers"], 1)


if __name__ == "__main__":
    unittest.main()
