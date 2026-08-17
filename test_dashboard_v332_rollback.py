from __future__ import annotations

import inspect
import unittest

import dashboard_accountflow_runtime_app as account_runtime
import dashboard_app as app
import dashboard_market_app as market
import dashboard_marketcoinux_app as v332
import dashboard_mobile_market_app as mobilemarket
import dashboard_runtimefix_app as runtimefix
import dashboard_share_runtime_app as share_runtime


class DashboardV332RollbackTests(unittest.TestCase):
    def test_active_runtime_keeps_v332_under_v3329_share_layer(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_share_runtime_app")
        self.assertEqual(app.VERSION, share_runtime.VERSION)
        self.assertIs(app.make_handler, share_runtime.make_v3321_handler)
        self.assertIn("V3_32_9_SHARE_CARDS", share_runtime.VERSION)
        self.assertIn("V3_32_8_WATCHLIST_SYNC", account_runtime.VERSION)
        self.assertIn("V3_32_6_SURFACE_PARITY", runtimefix.VERSION)
        account_source = inspect.getsource(account_runtime)
        share_source = inspect.getsource(share_runtime)
        repair_source = inspect.getsource(runtimefix)
        self.assertIn("base.make_v3321_handler", share_source)
        self.assertIn("runtimefix.make_v3321_handler", account_source)
        self.assertIn('path == "/account/password"', account_source)
        self.assertIn('path == "/payment/notify"', account_source)
        self.assertIn('"watchlist_sync": "managed_account_cross_device"', account_source)
        self.assertIn("v332.make_v332_handler", repair_source)
        self.assertIn("session and self._is_premium(session)", repair_source)
        self.assertIn("_serve_mobile_market", repair_source)
        self.assertIn("_serve_mobile_coin", repair_source)

    def test_free_and_premium_market_layers_are_different(self):
        base = market.market_center_page("nonce")
        free_body = v332.enhance_market_page(base, "nonce", premium_access=False)
        premium_body = v332.enhance_market_page(base, "nonce", premium_access=True)
        self.assertIn("const PREMIUM=false", free_body)
        self.assertIn("const PREMIUM=true", premium_body)
        self.assertNotEqual(free_body, premium_body)

    def test_v332_repair_and_mobile_helpers_do_not_write_core_or_membership_data(self):
        source = inspect.getsource(v332)
        repair_source = inspect.getsource(runtimefix)
        mobile_source = inspect.getsource(mobilemarket)
        account_source = inspect.getsource(account_runtime)
        share_source = inspect.getsource(share_runtime)
        self.assertNotIn("def do_POST", source)
        self.assertNotIn("def do_POST", repair_source)
        self.assertNotIn("def do_POST", mobile_source)
        self.assertIn("def do_POST", account_source)
        self.assertNotIn("def do_POST", share_source)
        self.assertIn('path == "/account/password"', account_source)
        self.assertIn('path == "/payment/notify"', account_source)
        self.assertIn('path == "/api/account/watchlist"', account_source)
        self.assertIn('"/mobile/watchlist/update"', account_source)
        for contract_source in (source, repair_source, account_source, share_source):
            self.assertIn('"signal_engine": "unchanged"' if contract_source in (source, account_source, share_source) else '"signal_engine":"unchanged"', contract_source)
        self.assertIn("render_market_page", mobile_source)
        self.assertIn("render_coin_page", mobile_source)


if __name__ == "__main__":
    unittest.main()
