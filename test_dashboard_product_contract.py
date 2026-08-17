from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import dashboard_accountflow_runtime_app as account_runtime
import dashboard_app as app
import dashboard_coin_app as coin
import dashboard_flowux_app as flowux
import dashboard_home_app as home
import dashboard_market_app as market
import dashboard_marketcoinux_app as marketcoin
import dashboard_mobile_market_app as mobilemarket
import dashboard_roleboundary_app as roleux
import dashboard_runtimefix_app as runtimefix
import dashboard_share_runtime_app as share_runtime
import dashboard_sitewideux_app as sitewide


class DashboardProductContractTests(unittest.TestCase):
    """V3.32 ürün görünümünü, masaüstü onarımını, mobil pariteyi ve yeni paylaşım üst katmanını birlikte korur."""

    def current_home_v332(self, *, premium_access: bool = True) -> str:
        body = home.home_dashboard_page({"username": "member", "role": "MEMBER", "csrf": "csrf-contract"}, "nonce-contract")
        body = sitewide.enhance_sitewide_ui(body, "nonce-contract", premium_access=premium_access)
        body = flowux.enhance_flow_ui(body, "nonce-contract", premium_access=premium_access)
        body = roleux.enhance_role_ui(body, "nonce-contract", is_admin=False)
        body = marketcoin.enhance_root_navigation(body, "nonce-contract", premium_access=premium_access)
        return body

    def test_real_home_keeps_v332_product_layers_and_runtime_repair(self):
        body = runtimefix.enhance_runtime_repair(self.current_home_v332(premium_access=True), "nonce-contract")
        for marker in ('id="homeSmartMetrics"', 'id="v324SignalGuide"', 'id="v326DataHealth"', 'id="v328-sitewide-script"', 'id="v329-flow-script"', 'id="v331-role-script"', 'id="v332-marketcoin-script"', 'id="v3321-runtime-repair-script"', 'id="page-signals"', 'id="page-trades"', 'id="page-results"'):
            self.assertIn(marker, body)
        for removed in ('id="v333-simplevoice-script"', 'id="v334-mobile-script"', 'id="v335-touchguard-script"', 'id="v336-mobile-recovery-script"'):
            self.assertNotIn(removed, body)

    def test_market_keeps_free_and_premium_presentation_distinct(self):
        base = market.market_center_page("nonce-contract")
        free_body = marketcoin.enhance_market_page(base, "nonce-contract", premium_access=False)
        premium_body = marketcoin.enhance_market_page(base, "nonce-contract", premium_access=True)
        self.assertIn("/api/market/overview", free_body)
        self.assertIn("/api/market/candles", free_body)
        self.assertIn("new URLSearchParams(location.search).get('symbol')", free_body)
        self.assertIn("const PREMIUM=false", free_body)
        self.assertIn("const PREMIUM=true", premium_body)
        self.assertNotEqual(free_body, premium_body)
        self.assertIn('id="v332-marketcoin-script"', free_body)
        self.assertIn("Hızlı grafiği göster", free_body)

    def test_real_coin_template_keeps_premium_deep_review_contract(self):
        body = marketcoin.enhance_coin_page(coin.coin_center_page("nonce-contract", "SOLUSDT"), "nonce-contract")
        for marker in ("SOLUSDT", 'id="chart"', "/api/market/candles", "/api/market/analysis-score", "/api/coin-center/summary", 'id="v332-marketcoin-script"', "Analiz ayrıntılarını göster", "Önce karar bilgisi"):
            self.assertIn(marker, body)

    def test_stable_entrypoint_is_share_over_account_flow_over_runtimefix(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_share_runtime_app")
        self.assertEqual(app.VERSION, share_runtime.VERSION)
        self.assertIs(app.make_handler, share_runtime.make_v3321_handler)
        self.assertIn("V3_32_9_SHARE_CARDS", share_runtime.VERSION)
        self.assertIn("V3_32_8_WATCHLIST_SYNC", account_runtime.VERSION)
        self.assertIn("V3_32_6_SURFACE_PARITY", runtimefix.VERSION)
        repair_source = inspect.getsource(runtimefix)
        account_source = inspect.getsource(account_runtime)
        share_source = inspect.getsource(share_runtime)
        self.assertIn("base.make_v3321_handler", share_source)
        self.assertIn("runtimefix.make_v3321_handler", account_source)
        self.assertIn('path == "/account/password"', account_source)
        self.assertIn('path == "/payment/notify"', account_source)
        self.assertIn('"watchlist_sync": "managed_account_cross_device"', account_source)
        self.assertIn('path in {"/share/trade", "/share/card.svg"}', share_source)
        self.assertIn('_serve_mobile_market', repair_source)
        self.assertIn('_serve_mobile_coin', repair_source)
        self.assertIn('"mobile_chart": "svg_no_javascript"', repair_source)
        dockerfile = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        for name in ("dashboard_app.py", "dashboard_marketcoinux_app.py", "dashboard_runtimefix_app.py", "dashboard_accountflow_runtime_app.py", "dashboard_mobile_market_app.py", "dashboard_watchsync_app.py", "dashboard_share_runtime_app.py"):
            self.assertIn(name, dockerfile)
            self.assertIn("!" + name, dockerignore)
        self.assertIn('CMD ["python", "dashboard_app.py"', dockerfile)

    def test_v332_role_boundaries_remain_under_repair_and_mobile_helpers(self):
        source = inspect.getsource(marketcoin)
        repair_source = inspect.getsource(runtimefix)
        mobile_source = inspect.getsource(mobilemarket)
        self.assertIn("roleux.make_v331_handler", source)
        self.assertIn("premium_access = bool(self._is_premium(session))", source)
        self.assertIn('path == "/coin-center" and premium_access', source)
        self.assertIn("session and self._is_premium(session)", repair_source)
        self.assertNotIn("def do_POST", repair_source)
        self.assertNotIn("def do_POST", mobile_source)
        self.assertIn('"free_runtime":"separate_preserved"', repair_source)
        self.assertIn('"signal_engine": "unchanged"', repair_source)
        self.assertIn('"telegram": "unchanged"', repair_source)
        self.assertIn('"trade_management": "unchanged"', repair_source)
        self.assertIn('"ledger_write": "unchanged"', repair_source)
        self.assertIn("render_market_page", mobile_source)
        self.assertIn("render_coin_page", mobile_source)


if __name__ == "__main__":
    unittest.main()
