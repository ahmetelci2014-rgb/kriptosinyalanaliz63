import unittest

import pandas as pd

import position_continuation_shadow as pcs


def make_frame(direction="LONG", strong=True, base=100.0):
    rows = []
    for i in range(12):
        if direction == "LONG":
            close = base + i * (0.4 if strong else -0.4)
            ema20 = close - (0.5 if strong else -0.5)
            ema50 = ema20 - (0.4 if strong else -0.4)
            slope = 0.25 if strong else -0.25
            low = close - 0.3
            high = close + 0.3
            rsi = 60 if strong else 38
        else:
            close = base - i * (0.4 if strong else -0.4)
            ema20 = close + (0.5 if strong else -0.5)
            ema50 = ema20 + (0.4 if strong else -0.4)
            slope = -0.25 if strong else 0.25
            low = close - 0.3
            high = close + 0.3
            rsi = 40 if strong else 62
        rows.append({
            "time": (1_780_000_000 + i * 900) * 1000,
            "open": close - 0.1,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000,
            "ema20": ema20,
            "ema50": ema50,
            "rsi": rsi,
            "adx": 30 if strong else 12,
            "atr": 0.8,
            "ema20_slope": slope,
            "volume_avg": 700,
            "volume_ratio": 1.6 if strong else 0.5,
        })
    return pd.DataFrame(rows)


class PositionContinuationShadowTests(unittest.TestCase):
    def test_strong_long_is_hold(self):
        f5 = make_frame("LONG", True, 100)
        f15 = make_frame("LONG", True, 100)
        f1 = make_frame("LONG", True, 100)
        result = pcs.evaluate_frames("LONG", 100.0, f5, f15, f1)
        self.assertEqual(result["status"], pcs.STATUS_STRONG)
        self.assertEqual(result["action_shadow"], pcs.ACTION_HOLD)
        self.assertGreaterEqual(result["score"], 80)
        self.assertGreater(result["remaining_move_shadow"]["high_percent"], 0)

    def test_opposed_long_flags_exit_risk(self):
        f5 = make_frame("LONG", False, 100)
        f15 = make_frame("LONG", False, 100)
        f1 = make_frame("LONG", False, 100)
        result = pcs.evaluate_frames("LONG", 100.0, f5, f15, f1)
        self.assertEqual(result["status"], pcs.STATUS_EXIT)
        self.assertEqual(result["action_shadow"], pcs.ACTION_EXIT_WATCH)
        self.assertLess(result["score"], 48)

    def test_strong_short_is_hold(self):
        f5 = make_frame("SHORT", True, 100)
        f15 = make_frame("SHORT", True, 100)
        f1 = make_frame("SHORT", True, 100)
        result = pcs.evaluate_frames("SHORT", 100.0, f5, f15, f1)
        self.assertEqual(result["status"], pcs.STATUS_STRONG)
        self.assertGreater(result["move_from_entry_percent"], 0)

    def test_directional_percent(self):
        self.assertAlmostEqual(pcs._directional_percent("LONG", 100, 105), 5.0)
        self.assertAlmostEqual(pcs._directional_percent("SHORT", 100, 95), 5.0)


if __name__ == "__main__":
    unittest.main()
