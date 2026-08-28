import unittest

import crypto_universe_guard as guard
import market_first_crypto_purity as purity


def derivative(symbol, category="1", group_id="4", *, swap=False, contract=True):
    return {
        "symbol": symbol,
        "quote": "USDT",
        "settle": "USDT",
        "active": True,
        "swap": swap,
        "contract": contract,
        "future": contract and not swap,
        "expiry": None,
        "info": {
            "instCategory": category,
            "groupId": group_id,
            "state": "live",
        },
    }


class FakeExchange:
    def __init__(self, markets):
        self._markets = markets

    def load_markets(self):
        return self._markets


class MarketFirstCryptoPurityTests(unittest.TestCase):
    def setUp(self):
        guard.clear_account_tradable_futures()

    def tearDown(self):
        guard.clear_account_tradable_futures()

    def test_contract_only_stock_and_commodity_are_rejected(self):
        stock = derivative("AAPL/USDT:USDT", category="3", group_id="6")
        gold = derivative("XAU/USDT:USDT", category="4", group_id="6")
        self.assertTrue(purity.crypto_derivative_exclusion_reason(stock).startswith("NON_CRYPTO"))
        self.assertTrue(purity.crypto_derivative_exclusion_reason(gold).startswith("NON_CRYPTO"))

    def test_contract_only_crypto_is_kept_with_positive_metadata(self):
        btc = derivative("BTC/USDT:USDT", category="1", group_id="4")
        self.assertEqual(purity.crypto_derivative_exclusion_reason(btc), "")

    def test_missing_category_can_use_crypto_group_fallback(self):
        doge = derivative("DOGE/USDT:USDT", category="", group_id="5")
        self.assertEqual(purity.crypto_derivative_exclusion_reason(doge), "")

    def test_ambiguous_derivative_fails_closed(self):
        unknown = derivative("MYSTERY/USDT:USDT", category="", group_id="")
        self.assertEqual(
            purity.crypto_derivative_exclusion_reason(unknown),
            "CRYPTO_METADATA_UNVERIFIED",
        )

    def test_market_first_filter_removes_tradfi_contract_rows(self):
        markets = {
            "BTC": derivative("BTC/USDT:USDT", category="1", group_id="4"),
            "DOGE": derivative("DOGE/USDT:USDT", category="", group_id="5"),
            "AAPL": derivative("AAPL/USDT:USDT", category="3", group_id="6"),
            "DELL": derivative("DELL/USDT:USDT", category="3", group_id="6"),
            "XAU": derivative("XAU/USDT:USDT", category="4", group_id="6"),
        }
        rows = [
            {"symbol": symbol, "price": 1.0}
            for symbol in ("BTCUSDT", "DOGEUSDT", "AAPLUSDT", "DELLUSDT", "XAUUSDT")
        ]
        universe = {row["symbol"]: dict(row) for row in rows}

        kept_rows, kept_universe, summary = purity.filter_market_first_universe(
            FakeExchange(markets),
            rows,
            universe,
        )

        self.assertEqual(
            {row["symbol"] for row in kept_rows},
            {"BTCUSDT", "DOGEUSDT"},
        )
        self.assertEqual(set(kept_universe), {"BTCUSDT", "DOGEUSDT"})
        self.assertEqual(summary["excluded"], 3)


if __name__ == "__main__":
    unittest.main()
