import unittest

from dashboard_compact_app import compact_dashboard_page
from dashboard_live_app import ROLE_ADMIN, ROLE_MEMBER


class CompactDashboardTests(unittest.TestCase):
    def test_admin_page_has_compact_navigation_and_fallback(self):
        body = compact_dashboard_page(
            {"username": "ahmet", "role": ROLE_ADMIN, "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn("Kontrol Merkezi", body)
        self.assertIn("Ana Sayfa", body)
        self.assertIn("Sinyaller", body)
        self.assertIn("İşlemler", body)
        self.assertIn("Sonuçlar", body)
        self.assertIn("/market-center", body)
        self.assertIn("/advanced", body)
        self.assertIn("/admin/users", body)
        self.assertIn("mobile-nav", body)
        self.assertIn('nonce="nonce-test"', body)
        self.assertIn('value="csrf-test"', body)

    def test_member_page_hides_admin_links(self):
        body = compact_dashboard_page(
            {"username": "uye01", "role": ROLE_MEMBER, "csrf": "csrf-member"},
            "nonce-member",
        )
        self.assertIn("Üye", body)
        self.assertNotIn("/admin/users", body)
        self.assertNotIn('data-view="system"', body.split('<script', 1)[0])
        self.assertIn("/account", body)
        self.assertIn("/advanced", body)

    def test_compact_page_is_read_only_ui(self):
        body = compact_dashboard_page(
            {"username": "uye01", "role": ROLE_MEMBER, "csrf": "csrf-member"},
            "nonce-member",
        )
        self.assertIn("/api/dashboard", body)
        self.assertNotIn("placeMarketOrder", body)
        self.assertNotIn("closePosition", body)
        self.assertNotIn("API_SECRET", body)
        self.assertNotIn("strategy.py", body)


if __name__ == "__main__":
    unittest.main()
