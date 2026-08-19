import json
import os
import tempfile
import unittest

import market_impulse_guard as impulse


class FakeExchange:
    def __init__(self, price, volume=2_000_000):
        self.price = price
        self.volume = volume

    def load_markets(self):
        return {
            "FIL/USDT:USDT": {
                "active": True,
                "swap": True,
                "quote": "USDT",
                "settle": "USDT",
                "symbol": "FIL/USDT:USDT",
                "base": "FIL",
            },
            "USDC/USDT:USDT": {
                "active": True,
                "swap": True,
                "quote": "USDT",
                "settle": "USDT",
                "symbol": "USDC/USDT:USDT",
                "base": "USDC",
            },
        }

    def fetch_tickers(self, symbols):
        return {
            "FIL/USDT:USDT": {"last": self.price, "quoteVolume": self.volume}
        }


class MarketImpulseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_live_long_impulse_is_prioritised(self):
        ex = FakeExchange(0.6300)
        impulse.update_market_impulse_state(ex, path=self.path, now=1_000_000)
        ex.price = 0.6380  # +1.27% in five minutes
        state = impulse.update_market_impulse_state(ex, path=self.path, now=1_000_300)
        self.assertEqual(impulse.priority_symbols(state)[0], "FILUSDT")
        item = state["impulses"][0]
        self.assertEqual(item["direction"], "LONG")
        self.assertTrue(item["strong"])

    def test_priority_can_override_normal_volume_slice(self):
        ex = FakeExchange(0.6300, volume=400_000)
        impulse.update_market_impulse_state(ex, path=self.path, now=2_000_000)
        ex.price = 0.6370
        state = impulse.update_market_impulse_state(ex, path=self.path, now=2_000_300)
        scan = impulse.scan_universe_from_state(
            state, normal_min_quote_volume=1_000_000, normal_max_scan_coins=300
        )
        self.assertIn("FILUSDT", scan)

    def test_opposing_strong_impulse_blocks_reaction(self):
        state = impulse.empty_state()
        state["impulses"] = [{
            "symbol": "FILUSDT",
            "direction": "LONG",
            "detected_at": 3_000_000,
            "strong": True,
            "move5_percent": 1.4,
            "move15_percent": 2.0,
            "move30_percent": 2.5,
        }]
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        found = impulse.recent_opposing_strong_impulse(
            "FILUSDT", "SHORT", path=self.path, now=3_000_300
        )
        self.assertIsNotNone(found)
        same = impulse.recent_opposing_strong_impulse(
            "FILUSDT", "LONG", path=self.path, now=3_000_300
        )
        self.assertIsNone(same)

    def test_stale_impulse_does_not_block(self):
        state = impulse.empty_state()
        state["impulses"] = [{
            "symbol": "FILUSDT",
            "direction": "LONG",
            "detected_at": 4_000_000,
            "strong": True,
        }]
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
        found = impulse.recent_opposing_strong_impulse(
            "FILUSDT", "SHORT", path=self.path, now=4_002_000
        )
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
