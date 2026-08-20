import os
import tempfile
import unittest

import premium_all_coins as pac


class FakeProfit:
    @staticmethod
    def cost_viability(signal):
        return {"ok": True}


class PremiumAllCoinsTests(unittest.TestCase):
    def test_adaptive_fetch_keeps_young_history(self):
        calls = []

        def original(exchange, symbol, timeframe, limit, min_len=20):
            calls.append((timeframe, min_len))
            return "ok"

        wrapped = pac.make_adaptive_fetcher(original)
        self.assertEqual(wrapped(None, "TESTUSDT", "4h", 240, min_len=120), "ok")
        self.assertEqual(calls[-1], ("4h", 12))
        wrapped(None, "TESTUSDT", "1m", 180, min_len=5)
        self.assertEqual(calls[-1], ("1m", 5))

    def test_mature_a_plus_direct_gate(self):
        signal = {
            "source": "15M_ENTRY",
            "signal_class": "TRADE",
            "direction": "LONG",
            "score": 100,
            "quality": "A+ ANA",
            "volume_ratio": 2.1,
            "adx_15m": 31,
            "adx_1h": 42,
            "adx_4h": 28,
            "zone_distance_percent": 0.12,
            "rsi_15m": 59,
            "entry": 100,
            "sl": 99,
            "tp1": 100.55,
        }
        allowed = pac.strong_direct_allowed(
            signal,
            100,
            lambda s, p: (True, "OK"),
            FakeProfit,
        )
        self.assertTrue(allowed)

    def test_mature_direct_rejects_weak_volume(self):
        signal = {
            "source": "15M_ENTRY",
            "signal_class": "TRADE",
            "direction": "LONG",
            "score": 100,
            "quality": "A+ ANA",
            "volume_ratio": 1.1,
            "adx_15m": 31,
            "adx_1h": 42,
            "adx_4h": 28,
            "zone_distance_percent": 0.12,
            "rsi_15m": 59,
            "entry": 100,
            "sl": 99,
            "tp1": 100.55,
        }
        self.assertFalse(
            pac.strong_direct_allowed(
                signal,
                100,
                lambda s, p: (True, "OK"),
                FakeProfit,
            )
        )

    def test_young_direct_uses_higher_score_threshold(self):
        base = {
            "source": "YOUNG_COIN_ENTRY",
            "signal_class": "TRADE",
            "direction": "LONG",
            "entry": 1.0,
            "sl": 0.99,
            "tp1": 1.0055,
        }
        good = dict(base, score=96)
        bad = dict(base, score=95)
        validator = lambda s, p: (True, "OK")
        self.assertTrue(pac.strong_direct_allowed(good, 1.0, validator, FakeProfit))
        self.assertFalse(pac.strong_direct_allowed(bad, 1.0, validator, FakeProfit))

    def test_all_market_universe_merges_rotating_outside(self):
        class Exchange:
            def load_markets(self):
                return {}
            def fetch_tickers(self):
                return {}

        old_build = pac.market_scan.build_universe
        old_select = pac.market_scan.select_deep_scan
        old_state = pac.STATE_FILE
        try:
            pac.market_scan.build_universe = lambda **kwargs: {
                "eligible": [{"symbol": "AUSDT"}, {"symbol": "BUSDT"}, {"symbol": "CUSDT"}],
                "live_reference_symbols": ["AUSDT", "BUSDT"],
                "outside": [{"symbol": "CUSDT"}],
            }
            pac.market_scan.select_deep_scan = lambda outside, cursor, max_per_run, hot_count: (outside, 1)
            with tempfile.TemporaryDirectory() as tmp:
                pac.STATE_FILE = os.path.join(tmp, "state.json")
                result = pac.build_scan_universe(Exchange(), [], 500000, 300)
            self.assertEqual(result, ["AUSDT", "BUSDT", "CUSDT"])
        finally:
            pac.market_scan.build_universe = old_build
            pac.market_scan.select_deep_scan = old_select
            pac.STATE_FILE = old_state


if __name__ == "__main__":
    unittest.main()
