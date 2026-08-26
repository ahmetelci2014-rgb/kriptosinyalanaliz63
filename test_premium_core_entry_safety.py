import unittest
from unittest.mock import patch

import pandas as pd

import premium_core_entry_safety as safety


def make_frame(late: bool = True) -> pd.DataFrame:
    rows = []
    price = 100.0
    # Stable history so EMA20/ATR are well formed.
    for i in range(30):
        o = price
        c = 100.0 + ((i % 3) - 1) * 0.10
        rows.append([i, o, max(o, c) + 0.25, min(o, c) - 0.25, c, 1000.0])
        price = c

    if late:
        closes = [99.0, 97.5, 96.0, 95.0, 97.0, 99.5, 102.0, 105.0, 108.0, 111.0, 114.0]
    else:
        closes = [99.0, 97.5, 96.0, 95.0, 97.0, 99.5, 101.0]

    for j, c in enumerate(closes, start=30):
        o = price
        rows.append([j, o, max(o, c) + 0.35, min(o, c) - 0.35, c, 1800.0])
        price = c

    # Forming candle (ignored by guard calculations).
    rows.append([len(rows), price, price + 0.2, price - 0.2, price, 500.0])
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


class PreSignalExtensionTests(unittest.TestCase):
    def test_late_long_is_blocked(self):
        df = make_frame(late=True)
        ctx = safety.pre_signal_extension_context("LONG", df, 114.0, 67.0)
        self.assertEqual(ctx.get("mode"), "BLOCK")
        self.assertGreaterEqual(ctx.get("extension_atr", 0), 2.2)
        self.assertGreaterEqual(ctx.get("bars_since_launch", 0), 3)

    def test_fresh_long_is_not_blocked(self):
        df = make_frame(late=False)
        ctx = safety.pre_signal_extension_context("LONG", df, 101.0, 58.0)
        self.assertNotEqual(ctx.get("mode"), "BLOCK")

    def test_analyzer_drops_late_15m_trade(self):
        df = make_frame(late=True)

        def original(*args, **kwargs):
            return {
                "symbol": "TESTUSDT",
                "direction": "LONG",
                "source": "15M_ENTRY",
                "signal_class": "TRADE",
                "entry": 114.0,
                "rsi_15m": 67.0,
            }

        wrapped = safety.make_no_chase_analyzer(original)
        self.assertIsNone(wrapped("TESTUSDT", df, None, None, 114.0))

    def test_non_core_source_is_untouched(self):
        signal = {
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "source": "SHADOW_ONLY",
            "signal_class": "TRADE",
            "entry": 114.0,
        }

        def original(*args, **kwargs):
            return dict(signal)

        wrapped = safety.make_no_chase_analyzer(original)
        self.assertEqual(wrapped("TESTUSDT", make_frame(True), None, None, 114.0), signal)


class CryptoOnlyUniverseTests(unittest.TestCase):
    class FakeExchange:
        def load_markets(self):
            return {
                "BTC": {"symbol": "BTC/USDT:USDT"},
                "NG": {"symbol": "NG/USDT:USDT"},
                "RKLB": {"symbol": "RKLB/USDT:USDT"},
            }

    def test_non_crypto_symbols_are_removed_after_scan(self):
        def original(exchange, priority_coins, min_quote_volume, max_scan_coins):
            # Original scanner would otherwise pass all three through.
            self.assertEqual(set(exchange.load_markets()), {"BTC"})
            return ["BTCUSDT", "NGUSDT", "RKLBUSDT"]

        wrapped = safety.make_crypto_only_universe(original)
        with (
            patch.object(safety.universe_guard, "refresh_account_tradable_futures_from_env", return_value=None),
            patch.object(
                safety.universe_guard,
                "filter_crypto_markets",
                return_value=({"BTC": {"symbol": "BTC/USDT:USDT"}}, [{"symbol": "NGUSDT"}, {"symbol": "RKLBUSDT"}]),
            ),
            patch.object(
                safety.universe_guard,
                "is_verified_live_futures_symbol",
                side_effect=lambda symbol: symbol == "BTCUSDT",
            ),
        ):
            result = wrapped(self.FakeExchange(), [], 0.0, 300)

        self.assertEqual(result, ["BTCUSDT"])


if __name__ == "__main__":
    unittest.main()
