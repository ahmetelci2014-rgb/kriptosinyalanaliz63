import unittest

from dashboard_home_app import home_dashboard_page


class DashboardHomeV22Tests(unittest.TestCase):
    def test_member_home_has_smart_sections_and_focus_layer(self):
        body = home_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn("Bugünün akışı", body)
        self.assertIn("Favorilerim", body)
        self.assertIn("Öne çıkan açık sinyaller", body)
        self.assertIn('id="homeSmartMetrics"', body)
        self.assertIn('id="homeFavoriteMarket"', body)
        self.assertIn("kripto-dashboard-data", body)
        self.assertIn("kripto_focus_favs", body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn('nonce="nonce-test"', body)
        self.assertNotIn('data-view="system"><span>◉</span><b>Sistem</b>', body)

    def test_admin_keeps_system_navigation_and_advanced_fallback(self):
        body = home_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-test"},
            "nonce-admin",
        )
        self.assertIn('data-view="system"', body)
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)
        self.assertIn('href="/market-center"', body)

    def test_old_home_compatibility_targets_remain_hidden(self):
        body = home_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('id="homeMetrics"', body)
        self.assertIn('id="homeOpen"', body)
        self.assertIn('id="homeResults"', body)
        self.assertIn("home-hidden-compat", body)


if __name__ == "__main__":
    unittest.main()
