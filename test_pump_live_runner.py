import unittest

import pump_live_runner as runner


class FakeRadar:
    @staticmethod
    def safe_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def normalize_bot_symbol(symbol):
        value = str(symbol or "").upper().replace("/", "")
        return value if value.endswith("USDT") else value + "USDT"


UNI_LONG_EVENT = {
    "recorded_at": 1787138280,
    "symbol": "UNIUSDT",
    "direction": "LONG",
    "shadow_ready": False,
    "move15_percent": 0.9368,
    "move30_percent": 1.1815,
    "price": 3.338,
    "ema20": 3.3065579277166606,
    "ema50": 3.308150152904189,
    "ema20_slope_percent": 0.2378,
    "ema20_distance_percent": 0.9509,
    "green_5m_count": 4,
    "red_5m_count": 0,
    "resume_confirmed": True,
    "rsi5": 72.0832,
    "vol1": 0.3911,
    "vol5": 3.4256,
}


class TrendContinuationTests(unittest.TestCase):
    def setUp(self):
        self.radar = FakeRadar()

    def test_uni_profile_is_strong_internal_trend(self):
        self.assertTrue(
            runner.strong_internal_trend_confirmation(
                self.radar,
                dict(UNI_LONG_EVENT),
            )
        )

    def test_uni_profile_builds_real_trend_entry(self):
        signal = runner.build_trend_entry_signal(
            self.radar,
            dict(UNI_LONG_EVENT),
            3.338,
        )
        self.assertIsNotNone(signal)
        self.assertEqual(signal["direction"], "LONG")
        self.assertEqual(signal["source"], "TREND_CONTINUATION")
        self.assertGreaterEqual(signal["score"], runner.TREND_ENTRY_MIN_SCORE)
        self.assertGreaterEqual(
            signal["risk_percent"],
            runner.TREND_ENTRY_MIN_RISK_PERCENT,
        )
        self.assertLessEqual(
            signal["risk_percent"],
            runner.TREND_ENTRY_MAX_RISK_PERCENT,
        )
        self.assertLess(signal["sl"], signal["entry"])
        self.assertGreater(signal["tp1"], signal["entry"])
        self.assertGreater(signal["tp2"], signal["tp1"])
        self.assertGreater(signal["tp3"], signal["tp2"])

    def test_chasing_is_rejected(self):
        signal = runner.build_trend_entry_signal(
            self.radar,
            dict(UNI_LONG_EVENT),
            3.36,
        )
        self.assertIsNone(signal)

    def test_overheated_rsi_is_rejected(self):
        event = dict(UNI_LONG_EVENT)
        event["rsi5"] = 79.0
        self.assertFalse(
            runner.strong_internal_trend_confirmation(
                self.radar,
                event,
            )
        )
        self.assertIsNone(
            runner.build_trend_entry_signal(
                self.radar,
                event,
                event["price"],
            )
        )


if __name__ == "__main__":
    unittest.main()
