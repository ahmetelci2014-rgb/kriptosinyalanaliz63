import inspect
import unittest

import dashboard_home_app as home
import dashboard_sitewideux_app as sitewide


class SitewideUxTests(unittest.TestCase):
    def current_home_html(self):
        return home.home_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )

    def test_real_current_home_does_not_use_old_home_metrics_marker(self):
        body = self.current_home_html()
        self.assertIn('id="homeSmartMetrics"', body)
        self.assertNotIn('<div class="summary" id="homeMetrics"></div>', body)

    def test_sitewide_layer_restores_optional_blocks_on_real_current_home(self):
        body = self.current_home_html()
        enhanced = sitewide.enhance_sitewide_ui(body, "nonce-test", premium_access=True)
        self.assertIn('id="v324SignalGuide"', enhanced)
        self.assertIn('id="v326DataHealth"', enhanced)
        self.assertIn('id="v328-sitewide-script"', enhanced)
        self.assertIn("Daha fazla bilgi", enhanced)
        self.assertIn("homeSmartMetrics", enhanced)

    def test_free_home_does_not_force_premium_technical_blocks(self):
        body = self.current_home_html()
        enhanced = sitewide.enhance_sitewide_ui(body, "nonce-test", premium_access=False)
        self.assertNotIn('id="v324SignalGuide"', enhanced)
        self.assertNotIn('id="v326DataHealth"', enhanced)
        self.assertIn('id="v328-sitewide-script"', enhanced)

    def test_mobile_information_policy_is_on_demand(self):
        source = inspect.getsource(sitewide)
        self.assertIn('mobile_primary_nav_max": 5', source)
        self.assertIn('button[data-view="trades"]', source)
        self.assertIn('v32-mobile-admin-nav', source)
        self.assertIn('v328-card-toggle', source)
        self.assertIn('v328-level-toggle', source)
        self.assertIn("aria-expanded", source)

    def test_admin_center_is_preserved_while_duplicate_direct_nav_is_reduced(self):
        source = inspect.getsource(sitewide)
        self.assertIn('.sidebar button.admin-only[data-view="system"]', source)
        self.assertIn('.sidebar a.admin-only[href="/admin/users"]', source)
        self.assertIn('admin_tools": "preserved_via_admin_center"', source)

    def test_layer_is_read_only_and_keeps_live_core_unchanged(self):
        source = inspect.getsource(sitewide)
        self.assertNotIn("def do_POST", source)
        self.assertIn("progressive.make_v327_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
