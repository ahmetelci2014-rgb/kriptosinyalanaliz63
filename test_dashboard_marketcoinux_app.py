from __future__ import annotations

import inspect
import unittest

import dashboard_coin_app as coin
import dashboard_market_app as market
import dashboard_marketcoinux_app as marketcoin


class MarketCoinUxTests(unittest.TestCase):
    def test_real_market_page_gets_task_hierarchy_and_symbol_deeplink(self):
        body = market.market_center_page("nonce-market")
        self.assertIn("loadOverview().then(()=>loadChart('BTCUSDT'));", body)
        enhanced = marketcoin.enhance_market_page(body, "nonce-market", premium_access=True)
        self.assertIn('id="v332-marketcoin-script"', enhanced)
        self.assertIn("URLSearchParams(location.search).get('symbol')", enhanced)
        self.assertNotIn("loadOverview().then(()=>loadChart('BTCUSDT'));", enhanced)
        self.assertIn("Piyasa Merkezi", enhanced)
        self.assertIn("Detaylı Coin Merkezi", enhanced)
        self.assertIn("v332-market-chart-collapsed", enhanced)
        self.assertIn("const PREMIUM=true", enhanced)

    def test_free_market_keeps_same_page_without_premium_entitlement(self):
        body = market.market_center_page("n")
        enhanced = marketcoin.enhance_market_page(body, "n", premium_access=False)
        self.assertIn("const PREMIUM=false", enhanced)
        self.assertIn("market_symbol_deeplink", inspect.getsource(marketcoin))
        self.assertIn("free_market_access", inspect.getsource(marketcoin))

    def test_real_coin_page_keeps_core_sections_and_adds_optional_analysis(self):
        body = coin.coin_center_page("nonce-coin", "SOLUSDT")
        enhanced = marketcoin.enhance_coin_page(body, "nonce-coin")
        self.assertIn('id="chart"', enhanced)
        self.assertIn("/api/coin-center/summary", enhanced)
        self.assertIn("/api/market/candles", enhanced)
        self.assertIn('id="v332-marketcoin-script"', enhanced)
        self.assertIn("v332MarketBack", enhanced)
        self.assertIn("Analiz ayrıntılarını göster", enhanced)
        self.assertIn("v332-secondary-analysis", enhanced)
        self.assertIn("Önce karar bilgisi", enhanced)

    def test_layer_is_idempotent(self):
        body = market.market_center_page("x")
        once = marketcoin.enhance_market_page(body, "x", premium_access=True)
        twice = marketcoin.enhance_market_page(once, "x", premium_access=True)
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="v332-marketcoin-script"'), 1)

    def test_root_navigation_reduces_duplicate_without_removing_route_contract(self):
        body = '<html><head><style></style></head><body><aside class="sidebar"><a class="nav-item" href="/market-center">Piyasa</a><a class="nav-item" href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a></aside></body></html>'
        enhanced = marketcoin.enhance_root_navigation(body, "root-nonce", premium_access=True)
        self.assertIn('.sidebar .nav-item[href^="/coin-center"]', enhanced)
        self.assertIn("/coin-center?symbol=", inspect.getsource(marketcoin))

    def test_source_contract_preserves_all_lower_layers_and_live_core(self):
        source = inspect.getsource(marketcoin)
        self.assertNotIn("def do_POST", source)
        self.assertIn("roleux.make_v331_handler", source)
        self.assertIn('"coin_center_premium_guard": "preserved"', source)
        self.assertIn('"role_boundary": "preserved"', source)
        self.assertIn('"account_ux": "preserved"', source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
