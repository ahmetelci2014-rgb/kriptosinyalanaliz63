from __future__ import annotations

import inspect
import unittest

import dashboard_app as app
import dashboard_market_app as market
import dashboard_marketcoinux_app as v332


class DashboardV332RollbackTests(unittest.TestCase):
    def test_active_runtime_is_v332(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_marketcoinux_app")
        self.assertEqual(app.VERSION, v332.VERSION)
        self.assertIs(app.make_handler, v332.make_v332_handler)

    def test_free_and_premium_market_layers_are_different(self):
        base = market.market_center_page("nonce")
        free_body = v332.enhance_market_page(base, "nonce", premium_access=False)
        premium_body = v332.enhance_market_page(base, "nonce", premium_access=True)
        self.assertIn("const PREMIUM=false", free_body)
        self.assertIn("const PREMIUM=true", premium_body)
        self.assertNotEqual(free_body, premium_body)

    def test_v332_does_not_write_core_or_membership_data(self):
        source = inspect.getsource(v332)
        self.assertNotIn("def do_POST", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
