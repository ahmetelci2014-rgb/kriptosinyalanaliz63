import types
import unittest

import crypto_universe_guard as guard


def market(symbol, inst_category="", group_id=""):
    info = {}
    if inst_category != "":
        info["instCategory"] = inst_category
    if group_id != "":
        info["groupId"] = group_id
    return {
        "symbol": symbol,
        "base": symbol.split("/")[0],
        "quote": "USDT",
        "settle": "USDT",
        "active": True,
        "swap": True,
        "info": info,
    }


class CryptoUniverseGuardTests(unittest.TestCase):
    def test_explicit_crypto_is_kept(self):
        self.assertTrue(guard.is_crypto_market(market("BTC/USDT:USDT", "1", "4")))

    def test_stock_commodity_forex_bond_are_rejected(self):
        for category in ("3", "4", "5", "6"):
            with self.subTest(category=category):
                self.assertFalse(
                    guard.is_crypto_market(market("X/USDT:USDT", category, "6"))
                )

    def test_rwa_group_fallback_is_rejected_when_category_missing(self):
        self.assertFalse(guard.is_crypto_market(market("MRVL/USDT:USDT", "", "6")))
        self.assertFalse(guard.is_crypto_market(market("RWA/USDT:USDT", "", "7")))

    def test_legacy_missing_metadata_fails_open(self):
        self.assertTrue(guard.is_crypto_market(market("ALT/USDT:USDT")))

    def test_filter_removes_mrvl_but_keeps_btc_and_legacy_crypto(self):
        markets = {
            "BTC": market("BTC/USDT:USDT", "1", "4"),
            "MRVL": market("MRVL/USDT:USDT", "3", "6"),
            "ALT": market("ALT/USDT:USDT"),
        }
        kept, excluded = guard.filter_crypto_markets(markets)
        self.assertEqual(set(kept), {"BTC", "ALT"})
        self.assertEqual([row["symbol"] for row in excluded], ["MRVL/USDT:USDT"])

    def test_install_guard_filters_before_existing_eligible_function(self):
        fake = types.SimpleNamespace()
        fake.eligible_markets = lambda markets: sorted(markets.keys())

        guard.install_crypto_only_guard(fake)
        result = fake.eligible_markets({
            "BTC": market("BTC/USDT:USDT", "1", "4"),
            "MRVL": market("MRVL/USDT:USDT", "3", "6"),
        })

        self.assertEqual(result, ["BTC"])
        self.assertTrue(fake._premium_crypto_only_guard_installed)

    def test_install_is_idempotent(self):
        fake = types.SimpleNamespace()
        fake.eligible_markets = lambda markets: sorted(markets.keys())
        guard.install_crypto_only_guard(fake)
        first = fake.eligible_markets
        guard.install_crypto_only_guard(fake)
        self.assertIs(first, fake.eligible_markets)


if __name__ == "__main__":
    unittest.main()
