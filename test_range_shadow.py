from __future__ import annotations

import json
import os
import tempfile
import unittest

import range_shadow as rs


@unittest.skipIf(rs.pd is None, "pandas/ta kurulu değil")
class RangeDetectionTests(unittest.TestCase):
    def build_range_frame(self):
        rows = rs._synthetic_range_rows(190)
        frame = rs.add_indicators(rs.frame_from_ohlcv(rows))
        frame.loc[:, "adx"] = 18.0
        frame.loc[:, "ema20"] = frame["close"]
        frame.loc[:, "ema50"] = frame["close"] * 1.0005
        frame.loc[:, "atr"] = 0.25
        frame.loc[:, "rsi"] = 50.0
        frame.loc[:, "volume_ratio"] = 1.0
        return frame

    def test_detects_sideways_range(self):
        frame = self.build_range_frame()
        result = rs.detect_range(frame)
        self.assertTrue(result["is_range"], result)
        self.assertGreaterEqual(result["support_touches"], 2)
        self.assertGreaterEqual(result["resistance_touches"], 2)

    def test_rejects_strong_trend(self):
        rows = []
        base_ts = 1_700_000_000_000
        for index in range(190):
            opened = 100 + index * 0.22
            closed = opened + 0.18
            rows.append([base_ts + index * 300_000, opened, closed + 0.08, opened - 0.08, closed, 1000 + index])
        frame = rs.add_indicators(rs.frame_from_ohlcv(rows))
        frame.loc[:, "adx"] = 40.0
        self.assertFalse(rs.detect_range(frame)["is_range"])

    def test_long_candidate_targets_resistance_without_partial_tp(self):
        frame = self.build_range_frame()
        first = rs.detect_range(frame)
        support = float(first["support"])
        idx = frame.index[-2]
        frame.loc[idx, "open"] = support + 0.08
        frame.loc[idx, "low"] = support - 0.08
        frame.loc[idx, "high"] = support + 0.36
        frame.loc[idx, "close"] = support + 0.29
        frame.loc[idx, "rsi"] = 43.0
        frame.loc[idx, "volume_ratio"] = 1.0
        info = rs.detect_range(frame)
        guard = {"allowed": True, "adx_15m": 18.0, "ema_spread_15m_percent": 0.4}
        candidate = rs.evaluate_entry_candidate("ADAUSDT", frame, info, guard, 30_000_000)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["direction"], "LONG")
        self.assertEqual(candidate["target_zone"], "RESISTANCE")
        self.assertEqual(candidate["next_direction"], "SHORT")
        self.assertGreater(candidate["target"], candidate["entry"])
        self.assertNotIn("tp1", candidate)
        self.assertNotIn("tp2", candidate)

    def test_short_candidate_targets_support_without_partial_tp(self):
        frame = self.build_range_frame()
        first = rs.detect_range(frame)
        resistance = float(first["resistance"])
        idx = frame.index[-2]
        frame.loc[idx, "open"] = resistance - 0.08
        frame.loc[idx, "high"] = resistance + 0.08
        frame.loc[idx, "low"] = resistance - 0.36
        frame.loc[idx, "close"] = resistance - 0.29
        frame.loc[idx, "rsi"] = 58.0
        frame.loc[idx, "volume_ratio"] = 1.0
        info = rs.detect_range(frame)
        guard = {"allowed": True, "adx_15m": 18.0, "ema_spread_15m_percent": 0.4}
        candidate = rs.evaluate_entry_candidate("XRPUSDT", frame, info, guard, 40_000_000)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate["direction"], "SHORT")
        self.assertEqual(candidate["target_zone"], "SUPPORT")
        self.assertEqual(candidate["next_direction"], "LONG")
        self.assertLess(candidate["target"], candidate["entry"])


class PositionLifecycleTests(unittest.TestCase):
    def base_candidate(self, direction="LONG"):
        if direction == "LONG":
            entry, sl, target, zone, next_direction = 100.0, 99.0, 102.0, "RESISTANCE", "SHORT"
        else:
            entry, sl, target, zone, next_direction = 100.0, 101.0, 98.0, "SUPPORT", "LONG"
        return {
            "symbol": "TESTUSDT",
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "target": target,
            "target_zone": zone,
            "next_direction": next_direction,
            "risk_percent": 1.0,
            "signal_candle_ms": 1_700_000_000_000,
        }

    def test_stop_before_opposite_zone(self):
        position = rs.build_position(self.base_candidate("LONG"))
        candle = {"timestamp": position["entry_candle_ms"] + 300_000, "high": 100.4, "low": 98.9, "close": 99.1}
        result = rs.simulate_position_on_candles(position, [candle])
        self.assertEqual(result["outcome"], "SL")
        self.assertEqual(result["gross_r"], -1.0)

    def test_long_closes_fully_at_resistance_then_short_ready(self):
        position = rs.build_position(self.base_candidate("LONG"))
        candle = {"timestamp": position["entry_candle_ms"] + 300_000, "high": 102.1, "low": 100.1, "close": 102.0}
        result = rs.simulate_position_on_candles(position, [candle])
        self.assertEqual(result["outcome"], "RESISTANCE_EXIT")
        self.assertEqual(result["gross_r"], 2.0)
        self.assertTrue(result["reverse_ready"])
        self.assertEqual(result["next_direction"], "SHORT")

    def test_short_closes_fully_at_support_then_long_ready(self):
        position = rs.build_position(self.base_candidate("SHORT"))
        candle = {"timestamp": position["entry_candle_ms"] + 300_000, "high": 99.9, "low": 97.9, "close": 98.0}
        result = rs.simulate_position_on_candles(position, [candle])
        self.assertEqual(result["outcome"], "SUPPORT_EXIT")
        self.assertEqual(result["gross_r"], 2.0)
        self.assertTrue(result["reverse_ready"])
        self.assertEqual(result["next_direction"], "LONG")

    def test_same_candle_ambiguity_is_conservative(self):
        position = rs.build_position(self.base_candidate("LONG"))
        candle = {"timestamp": position["entry_candle_ms"] + 300_000, "high": 102.2, "low": 98.8, "close": 100.0}
        result = rs.simulate_position_on_candles(position, [candle])
        self.assertEqual(result["outcome"], "AMBIGUOUS_SL_TARGET_SAME_CANDLE")
        self.assertEqual(result["gross_r"], -1.0)


class LedgerTests(unittest.TestCase):
    def test_atomic_json_save(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "range_shadow.json")
            data = rs.empty_ledger()
            data["last_update"] = 123
            self.assertTrue(rs.save_json_atomically(path, data))
            with open(path, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            self.assertEqual(loaded["last_update"], 123)
            self.assertNotIn("tp1_be_count", loaded["summary"])


if __name__ == "__main__":
    unittest.main()
