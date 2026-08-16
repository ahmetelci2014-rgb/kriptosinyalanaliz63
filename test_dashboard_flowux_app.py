import inspect
import unittest

import dashboard_flowux_app as flowux
import dashboard_home_app as home
import dashboard_sitewideux_app as sitewide


class FlowUxTests(unittest.TestCase):
    def current_home_html(self):
        return home.home_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )

    def current_v328_html(self):
        return sitewide.enhance_sitewide_ui(
            self.current_home_html(), "nonce-test", premium_access=True
        )

    def test_real_current_spa_contains_three_product_pages(self):
        body = self.current_home_html()
        self.assertIn('id="page-signals"', body)
        self.assertIn('id="page-trades"', body)
        self.assertIn('id="page-results"', body)

    def test_v329_layers_on_current_v328_without_removing_previous_ui(self):
        body = self.current_v328_html()
        enhanced = flowux.enhance_flow_ui(body, "nonce-test", premium_access=True)
        self.assertIn('id="v328-sitewide-script"', enhanced)
        self.assertIn('id="v329-flow-script"', enhanced)
        self.assertIn("Canlı Sinyaller", enhanced)
        self.assertIn("İşlem Takibi", enhanced)
        self.assertIn("Filtrelenen", enhanced)
        self.assertEqual(enhanced.count('id="v329-flow-script"'), 1)
        self.assertEqual(
            flowux.enhance_flow_ui(enhanced, "nonce-test", premium_access=True),
            enhanced,
        )

    def test_signal_trade_result_information_policy_is_explicit(self):
        source = inspect.getsource(flowux)
        self.assertIn('signal_primary": "coin_direction_entry"', source)
        self.assertIn('signal_levels": "on_demand"', source)
        self.assertIn('trade_primary_levels": "entry_tp1_sl"', source)
        self.assertIn('trade_extra_levels": "tp2_tp3_on_demand"', source)
        self.assertIn('results": "compact_with_filtered_summary"', source)
        self.assertIn("v329-signal-level", source)
        self.assertIn("v329-trade-extra", source)

    def test_mobile_flow_uses_internal_steps_without_growing_primary_nav(self):
        source = inspect.getsource(flowux)
        self.assertIn("data-v329-view", source)
        self.assertIn("mobile_flow_without_nav_growth", source)
        self.assertNotIn("mobile-nav').appendChild", source)

    def test_premium_can_open_coin_center_and_free_keeps_market_route(self):
        premium = flowux.enhance_flow_ui(self.current_v328_html(), "nonce-test", premium_access=True)
        free = flowux.enhance_flow_ui(self.current_home_html(), "nonce-test", premium_access=False)
        self.assertIn("const PREMIUM=true", premium)
        self.assertIn("const PREMIUM=false", free)
        self.assertIn("/coin-center?symbol=", premium)
        self.assertIn("/market-center?symbol=", free)

    def test_layer_is_read_only_and_keeps_live_core_unchanged(self):
        source = inspect.getsource(flowux)
        self.assertNotIn("def do_POST", source)
        self.assertIn("sitewide.make_v328_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
