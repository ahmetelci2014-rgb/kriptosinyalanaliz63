import unittest

import pandas as pd

import multi_day_trend_shadow as md


def make_frame(direction="SHORT", strong=True, base=100.0, step=0.35):
    rows = []
    for i in range(90):
        if direction == "SHORT":
            close = base - i * step if strong else base + i * step
            ema20 = close + (0.8 if strong else -0.8)
            ema50 = ema20 + (0.6 if strong else -0.6)
            slope = -0.25 if strong else 0.25
            rsi = 40 if strong else 66
        else:
            close = base + i * step if strong else base - i * step
            ema20 = close - (0.8 if strong else -0.8)
            ema50 = ema20 - (0.6 if strong else -0.6)
            slope = 0.25 if strong else -0.25
            rsi = 60 if strong else 36
        rows.append({
            "time": (1_780_000_000 + i * 14_400) * 1000,
            "open": close + (0.05 if direction == "SHORT" else -0.05),
            "high": close + 0.30,
            "low": close - 0.30,
            "close": close,
            "volume": 1000.0,
            "ema20": ema20,
            "ema50": ema50,
            "rsi": rsi,
            "adx": 32.0 if strong else 11.0,
            "atr": 1.0,
            "ema20_slope": slope,
        })
    return pd.DataFrame(rows)


class MultiDayTrendShadowTests(unittest.TestCase):
    def test_strong_short_is_multi_day_continue(self):
        f1 = make_frame("SHORT", True, 120.0, 0.08)
        f4 = make_frame("SHORT", True, 120.0, 0.30)
        now = int(f4.iloc[-1]["time"] / 1000) + 3600
        result = md.evaluate_frames(
            "SHORT",
            f1,
            f4,
            float(f1.iloc[-1]["close"]),
            now_ts=now,
        )
        self.assertIn(result["status"], {md.STATUS_START, md.STATUS_STRONG})
        self.assertGreaterEqual(result["score"], 78)
        self.assertGreater(result["trend_origin"]["move_so_far_percent"], 0)

    def test_opposed_short_flags_end_risk(self):
        f1 = make_frame("SHORT", False, 100.0, 0.08)
        f4 = make_frame("SHORT", False, 100.0, 0.30)
        now = int(f4.iloc[-1]["time"] / 1000) + 3600
        result = md.evaluate_frames(
            "SHORT",
            f1,
            f4,
            float(f1.iloc[-1]["close"]),
            now_ts=now,
        )
        self.assertEqual(result["status"], md.STATUS_EXIT)
        self.assertLess(result["score"], 50)

    def test_directional_percent(self):
        self.assertAlmostEqual(md._directional_percent("LONG", 100.0, 120.0), 20.0)
        self.assertAlmostEqual(md._directional_percent("SHORT", 100.0, 80.0), 20.0)


if __name__ == "__main__":
    unittest.main()
