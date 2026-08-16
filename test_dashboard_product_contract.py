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
import dashboard_mobile_recovery_app as recovery
import dashboard_mobile_safe_app as safe
import dashboard_mobileux_app as mobile
import dashboard_roleboundary_app as roleux
import dashboard_simplevoice_app as simplevoice
import dashboard_sitewideux_app as sitewide
import dashboard_touchguard_app as touch


class DashboardProductContractTests(unittest.TestCase):
    """Son panel katmanlarının gerçek şablonlar üzerinde birlikte kalmasını korur."""

    def current_home(self) -> str:
        return home.home_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf-contract"},
            "nonce-contract",
        )

    def test_real_home_keeps_cumulative_desktop_product_layers(self):
        body = self.current_home()
        body = sitewide.enhance_sitewide_ui(body, "nonce-contract", premium_access=True)
        body = flowux.enhance_flow_ui(body, "nonce-contract", premium_access=True)
        body = roleux.enhance_role_ui(body, "nonce-contract", is_admin=False)
        body = marketcoin.enhance_root_navigation(body, "nonce-contract", premium_access=True)
        body = simplevoice.enhance_simple_voice_ui(body, "nonce-contract")
        body = mobile.enhance_mobile_ui(body, "nonce-contract")
        body = touch.enhance_touch_guard(body, "nonce-contract")
        body = recovery.enhance_mobile_recovery(body, "nonce-contract")

        self.assertIn('id="homeSmartMetrics"', body)
        self.assertIn('id="v324SignalGuide"', body)
        self.assertIn('id="v326DataHealth"', body)
        self.assertIn('id="v328-sitewide-script"', body)
        self.assertIn('id="v329-flow-script"', body)
        self.assertIn('id="v331-role-script"', body)
        self.assertIn('id="v332-marketcoin-script"', body)
        self.assertIn('id="v333-simplevoice-script"', body)
        self.assertIn('id="v334-mobile-script"', body)
        self.assertIn('id="v335-touchguard-script"', body)
        self.assertIn('id="v336-mobile-recovery-script"', body)
        self.assertIn('id="page-signals"', body)
        self.assertIn('id="page-trades"', body)
        self.assertIn('id="page-results"', body)
        self.assertIn("Daha fazla bilgi", body)
        self.assertIn("Sesli bildirim kapalı", body)
        self.assertIn("v333Status", body)

    def test_real_market_template_keeps_free_access_and_symbol_deeplink(self):
        body = market.market_center_page("nonce-contract")
        body = marketcoin.enhance_market_page(body, "nonce-contract", premium_access=False)
        self.assertIn("/api/market/overview", body)
        self.assertIn("/api/market/candles", body)
        self.assertIn("new URLSearchParams(location.search).get('symbol')", body)
        self.assertIn("const PREMIUM=false", body)
        self.assertIn('id="v332-marketcoin-script"', body)
        self.assertIn("Hızlı grafiği göster", body)

    def test_real_coin_template_keeps_deep_review_contract(self):
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

    def test_mobile_safe_shell_is_javascript_free_and_keeps_core_routes(self):
        body = safe.mobile_safe_page(
            {"username": "member", "csrf": "csrf-contract"},
            {"open_trades": [{"symbol": "BTCUSDT", "direction": "LONG", "entry": 1, "tp1": 2, "sl": 0.5}], "recent_results": []},
            "home",
        )
        self.assertIn("Mobil güvenli görünüm", body)
        self.assertNotIn("<script", body.lower())
        self.assertIn('href="/mobile-safe?view=signals"', body)
        self.assertIn('href="/mobile-safe?view=trades"', body)
        self.assertIn('href="/mobile-safe?view=results"', body)
        self.assertIn('href="/market-center"', body)
        self.assertIn('href="/account"', body)

    def test_stable_entrypoint_points_to_current_safe_runtime(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_mobile_safe_app")
        self.assertEqual(app.VERSION, safe.VERSION)
        self.assertIs(app.make_handler, safe.make_v337_handler)

        dockerfile = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.assertIn("dashboard_mobile_recovery_app.py", dockerfile)
        self.assertIn("dashboard_mobile_safe_app.py", dockerfile)
        self.assertIn('CMD ["python", "dashboard_app.py"', dockerfile)
        self.assertIn("!dashboard_mobile_recovery_app.py", dockerignore)
        self.assertIn("!dashboard_mobile_safe_app.py", dockerignore)

    def test_latest_runtime_remains_presentation_only(self):
        source = inspect.getsource(safe)
        self.assertNotIn("def do_POST", source)
        self.assertIn("recovery.make_v336_handler", source)
        self.assertIn('"mobile_safe_javascript": False', source)
        self.assertIn('"desktop_runtime": "V3.36 preserved"', source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
