import types
import unittest

import crypto_universe_guard as guard


def market(symbol, inst_category="", group_id="", *, state="live", active=True):
    info = {}
    if inst_category != "":
        info["instCategory"] = inst_category
    if group_id != "":
        info["groupId"] = group_id
    if state != "":
        info["state"] = state
    return {
        "symbol": symbol,
        "base": symbol.split("/")[0],
        "quote": "USDT",
        "settle": "USDT",
        "active": active,
        "swap": True,
        "info": info,
    }


class CryptoUniverseGuardTests(unittest.TestCase):
    def setUp(self):
        guard.VERIFIED_LIVE_FUTURES_SYMBOLS.clear()

    def test_explicit_crypto_is_kept(self):
        self.assertTrue(guard.is_crypto_market(market("BTC/USDT:USDT", "1", "4")))

    def test_crypto_group_is_positive_evidence_when_category_missing(self):
        self.assertTrue(guard.is_crypto_market(market("ALT/USDT:USDT", "", "4")))
        self.assertTrue(guard.is_crypto_market(market("ALT/USDT:USDT", "", "5")))

    def test_stock_commodity_forex_bond_are_rejected(self):
        for category in ("3", "4", "5", "6"):
            with self.subTest(category=category):
                self.assertFalse(
                    guard.is_crypto_market(market("X/USDT:USDT", category, "6"))
                )

    def test_rwa_group_fallback_is_rejected_when_category_missing(self):
        self.assertFalse(guard.is_crypto_market(market("MRVL/USDT:USDT", "", "6")))
        self.assertFalse(guard.is_crypto_market(market("RWA/USDT:USDT", "", "7")))

    def test_missing_crypto_metadata_fails_closed(self):
        row = market("ALT/USDT:USDT", state="live")
        self.assertFalse(guard.is_crypto_market(row))
        self.assertEqual(
            guard.market_exclusion_reason(row),
            "CRYPTO_SWAP_METADATA_UNVERIFIED",
        )

    def test_missing_live_state_fails_closed(self):
        row = market("ALT/USDT:USDT", "1", "4", state="")
        self.assertEqual(
            guard.market_exclusion_reason(row),
            "OKX_STATE_NOT_CONFIRMED",
        )

    def test_ethw_is_user_untradable_futures_symbol(self):
        self.assertFalse(guard.is_user_tradable_futures_symbol("ETHWUSDT"))
        self.assertFalse(guard.is_user_tradable_futures_symbol("ETHW/USDT:USDT"))
        self.assertTrue(guard.is_user_tradable_futures_symbol("BTCUSDT"))

    def test_non_live_swap_is_rejected(self):
        row = market("ALT/USDT:USDT", "1", "4", state="suspend")
        self.assertEqual(guard.market_exclusion_reason(row), "OKX_STATE_SUSPEND")

    def test_inactive_or_unknown_active_swap_is_rejected(self):
        row = market("ALT/USDT:USDT", "1", "4", active=False)
        self.assertEqual(
            guard.market_exclusion_reason(row),
            "ACTIVE_SWAP_NOT_CONFIRMED",
        )
        row["active"] = None
        self.assertEqual(
            guard.market_exclusion_reason(row),
            "ACTIVE_SWAP_NOT_CONFIRMED",
        )

    def test_filter_only_keeps_verified_live_crypto_futures(self):
        markets = {
            "BTC": market("BTC/USDT:USDT", "1", "4"),
            "GROUP_ONLY": market("DOGE/USDT:USDT", "", "5"),
            "MRVL": market("MRVL/USDT:USDT", "3", "6"),
            "ETHW": market("ETHW/USDT:USDT", "1", "4"),
            "AMBIG": market("ALT/USDT:USDT", "", ""),
        }
        kept, excluded = guard.filter_crypto_markets(markets)
        self.assertEqual(set(kept), {"BTC", "GROUP_ONLY"})
        self.assertEqual(
            guard.VERIFIED_LIVE_FUTURES_SYMBOLS,
            {"BTCUSDT", "DOGEUSDT"},
        )
        reasons = {row["bot_symbol"]: row["reason"] for row in excluded}
        self.assertEqual(reasons["MRVLUSDT"], "NON_CRYPTO_CATEGORY")
        self.assertEqual(reasons["ETHWUSDT"], "USER_INTERFACE_UNTRADABLE")
        self.assertEqual(reasons["ALTUSDT"], "CRYPTO_SWAP_METADATA_UNVERIFIED")
        self.assertTrue(guard.is_verified_live_futures_symbol("BTCUSDT"))
        self.assertFalse(guard.is_verified_live_futures_symbol("ETHWUSDT"))
        self.assertFalse(guard.is_verified_live_futures_symbol("ALTUSDT"))

    def test_install_guard_filters_before_existing_eligible_function(self):
        fake = types.SimpleNamespace()
        fake.eligible_markets = lambda markets: sorted(markets.keys())

        guard.install_crypto_only_guard(fake)
        result = fake.eligible_markets({
            "BTC": market("BTC/USDT:USDT", "1", "4"),
            "MRVL": market("MRVL/USDT:USDT", "3", "6"),
            "ETHW": market("ETHW/USDT:USDT", "1", "4"),
            "AMBIG": market("ALT/USDT:USDT", "", ""),
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
