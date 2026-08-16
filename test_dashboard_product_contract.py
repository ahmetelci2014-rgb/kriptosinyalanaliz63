from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import dashboard_app as app
import dashboard_coin_app as coin
import dashboard_flowux_app as flowux
import dashboard_home_app as home
import dashboard_market_app as market
import dashboard_marketcoinux_app as marketcoin
import dashboard_mobile_market_app as mobilemarket
import dashboard_roleboundary_app as roleux
import dashboard_runtimefix_app as runtimefix
import dashboard_sitewideux_app as sitewide


class DashboardProductContractTests(unittest.TestCase):
    """V3.32 ürün görünümünü, V3.32.1 masaüstü ve V3.32.4 mobil katmanını birlikte korur."""

    def current_home_v332(self, *, premium_access: bool = True) -> str:
        body = home.home_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf-contract"},
            "nonce-contract",
        )
        body = sitewide.enhance_sitewide_ui(body, "nonce-contract", premium_access=premium_access)
        body = flowux.enhance_flow_ui(body, "nonce-contract", premium_access=premium_access)
        body = roleux.enhance_role_ui(body, "nonce-contract", is_admin=False)
        body = marketcoin.enhance_root_navigation(body, "nonce-contract", premium_access=premium_access)
        return body

    def test_real_home_keeps_v332_product_layers_and_runtime_repair(self):
        body = self.current_home_v332(premium_access=True)
        body = runtimefix.enhance_runtime_repair(body, "nonce-contract")
        self.assertIn('id="homeSmartMetrics"', body)
        self.assertIn('id="v324SignalGuide"', body)
        self.assertIn('id="v326DataHealth"', body)
        self.assertIn('id="v328-sitewide-script"', body)
        self.assertIn('id="v329-flow-script"', body)
        self.assertIn('id="v331-role-script"', body)
        self.assertIn('id="v332-marketcoin-script"', body)
        self.assertIn('id="v3321-runtime-repair-script"', body)
        self.assertIn('id="page-signals"', body)
        self.assertIn('id="page-trades"', body)
        self.assertIn('id="page-results"', body)
        self.assertNotIn('id="v333-simplevoice-script"', body)
        self.assertNotIn('id="v334-mobile-script"', body)
        self.assertNotIn('id="v335-touchguard-script"', body)
        self.assertNotIn('id="v336-mobile-recovery-script"', body)

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
        body = coin.coin_center_page("nonce-contract", "SOLUSDT")
        body = marketcoin.enhance_coin_page(body, "nonce-contract")
        self.assertIn("SOLUSDT", body)
        self.assertIn('id="chart"', body)
        self.assertIn("/api/market/candles", body)
        self.assertIn("/api/market/analysis-score", body)
        self.assertIn("/api/coin-center/summary", body)
        self.assertIn('id="v332-marketcoin-script"', body)
        self.assertIn("Analiz ayrıntılarını göster", body)
        self.assertIn("Önce karar bilgisi", body)

    def test_stable_entrypoint_is_v3324_mobile_wrapper_over_v3321_repair(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_mobile_market_app")
        self.assertEqual(app.VERSION, mobilemarket.VERSION)
        self.assertIs(app.make_handler, mobilemarket.make_v3324_handler)
        mobile_source = inspect.getsource(mobilemarket)
        self.assertIn("current.make_v3321_handler", mobile_source)
        dockerfile = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.assertIn("dashboard_app.py", dockerfile)
        self.assertIn("dashboard_marketcoinux_app.py", dockerfile)
        self.assertIn("dashboard_runtimefix_app.py", dockerfile)
        self.assertIn("dashboard_mobile_market_app.py", dockerfile)
        self.assertIn('CMD ["python", "dashboard_app.py"', dockerfile)
        self.assertIn("!dashboard_app.py", dockerignore)
        self.assertIn("!dashboard_marketcoinux_app.py", dockerignore)
        self.assertIn("!dashboard_runtimefix_app.py", dockerignore)
        self.assertIn("!dashboard_mobile_market_app.py", dockerignore)

    def test_v332_role_boundaries_remain_under_repair_and_mobile_wrapper(self):
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
        self.assertIn('"signal_engine":"unchanged"', repair_source)
        self.assertIn('"telegram":"unchanged"', repair_source)
        self.assertIn('"trade_management":"unchanged"', repair_source)
        self.assertIn('"ledger_write":"unchanged"', repair_source)
        self.assertIn('"signal_engine": "unchanged"', mobile_source)
        self.assertIn('"telegram": "unchanged"', mobile_source)
        self.assertIn('"trade_management": "unchanged"', mobile_source)
        self.assertIn('"ledger_write": "unchanged"', mobile_source)


if __name__ == "__main__":
    unittest.main()
