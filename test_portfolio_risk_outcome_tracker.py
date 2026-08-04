import json
import os
import tempfile
import unittest

from portfolio_risk_outcome_tracker import (
    analyze_record_from_candles,
    analyze_window,
    directional_return_percent,
    first_threshold_event,
    make_record_key,
    normalize_symbol,
    summarize_records,
)


class PortfolioRiskOutcomeTests(unittest.TestCase):
    def test_normalize_symbol(self):
        self.assertEqual(normalize_symbol("BTC/USDT:USDT"), "BTCUSDT")
        self.assertEqual(normalize_symbol("btc-usdt"), "BTCUSDT")

    def test_directional_return(self):
        self.assertEqual(directional_return_percent("LONG", 100, 101), 1.0)
        self.assertEqual(directional_return_percent("SHORT", 100, 99), 1.0)

    def test_long_favorable_first(self):
        candles = [
            [300000, 100, 100.6, 99.9, 100.4, 1],
            [600000, 100.4, 100.8, 100.1, 100.7, 1],
        ]
        event = first_threshold_event(candles, "LONG", 100, 0.5)
        self.assertEqual(event["event"], "FAVORABLE_FIRST")
        analysis = analyze_window(candles, "LONG", 100)
        self.assertEqual(analysis["directional_return_percent"], 0.7)
        self.assertEqual(analysis["max_favorable_percent"], 0.8)

    def test_short_adverse_first(self):
        candles = [
            [300000, 100, 100.7, 99.8, 100.5, 1],
            [600000, 100.5, 100.6, 99.0, 99.2, 1],
        ]
        event = first_threshold_event(candles, "SHORT", 100, 0.5)
        self.assertEqual(event["event"], "ADVERSE_FIRST")

    def test_ambiguous_same_candle(self):
        candles = [[300000, 100, 100.7, 99.3, 100, 1]]
        event = first_threshold_event(candles, "LONG", 100, 0.5)
        self.assertEqual(event["event"], "AMBIGUOUS_SAME_CANDLE")

    def test_record_checkpoints(self):
        # recorded_at=600 sn; referans olarak 5M'de 0 ms mumu kapanmış kabul edilir.
        record = {
            "identity": "MAIN|BTCUSDT|LONG|ALLOW|0|0",
            "recorded_at": 600,
            "decision": "ALLOW",
            "would_block": False,
            "symbol": "BTCUSDT",
            "direction": "LONG",
        }
        candles = [[0, 100, 100.2, 99.8, 100, 1]]
        candles.append([300000, 100, 100.2, 99.8, 100, 1])
        for index in range(2, 15):
            ts = index * 300000
            candles.append([ts, 100, 101, 99.9, 100.5, 1])
        result = analyze_record_from_candles(record, candles, current_ts=4300)
        self.assertEqual(result["reference_price"], 100)
        self.assertIn("60", result["checkpoints"])
        self.assertEqual(result["data_status"], "TRACKING")


    def test_record_key_keeps_repeated_decisions_separate(self):
        first = {"identity": "MAIN|BTCUSDT|LONG|ALLOW|1|1", "recorded_at": 100}
        second = {"identity": "MAIN|BTCUSDT|LONG|ALLOW|1|1", "recorded_at": 200}
        self.assertNotEqual(make_record_key(first), make_record_key(second))

    def test_partial_decision_candle_is_excluded(self):
        record = {
            "identity": "MAIN|BTCUSDT|LONG|ALLOW|0|0",
            "recorded_at": 620,
            "decision": "ALLOW",
            "would_block": False,
            "symbol": "BTCUSDT",
            "direction": "LONG",
        }
        candles = [
            [600000, 100, 110, 90, 100, 1],  # kararın verildiği kısmi mum: dışlanmalı
            [900000, 100, 100.4, 99.8, 100.2, 1],
        ]
        result = analyze_record_from_candles(record, candles, current_ts=1210)
        self.assertEqual(result["reference_candle_ms"], 900000)
        self.assertEqual(result["reference_price"], 100)
        self.assertEqual(result["latest_analysis"]["max_favorable_percent"], 0.4)
        self.assertEqual(result["latest_analysis"]["max_adverse_percent"], 0.2)

    def test_summary_separates_block_allow(self):
        records = [
            {
                "decision": "BLOCK",
                "block_code": "DIRECTION_RISK_LIMIT",
                "data_status": "TRACKING",
                "completed": False,
                "checkpoints": {"60": {
                    "directional_return_percent": -0.4,
                    "max_favorable_percent": 0.2,
                    "max_adverse_percent": 0.8,
                }},
                "latest_analysis": {
                    "first_0_5_percent": {"event": "ADVERSE_FIRST"},
                    "first_1_0_percent": {"event": "NONE"},
                },
            },
            {
                "decision": "ALLOW",
                "block_code": None,
                "data_status": "TRACKING",
                "completed": False,
                "checkpoints": {"60": {
                    "directional_return_percent": 0.6,
                    "max_favorable_percent": 0.9,
                    "max_adverse_percent": 0.1,
                }},
                "latest_analysis": {
                    "first_0_5_percent": {"event": "FAVORABLE_FIRST"},
                    "first_1_0_percent": {"event": "NONE"},
                },
            },
        ]
        summary = summarize_records(records)
        self.assertEqual(summary["by_decision"]["BLOCK"]["records"], 1)
        self.assertEqual(
            summary["by_decision"]["ALLOW"]["avg_return_60m_percent"], 0.6
        )


if __name__ == "__main__":
    unittest.main()
