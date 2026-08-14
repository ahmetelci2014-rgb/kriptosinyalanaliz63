import unittest

from dashboard_memory_app import VERSION, memory_dashboard_page


class DashboardMemoryV210Tests(unittest.TestCase):
    def test_member_page_adds_opportunity_favorites_and_memory(self):
        body = memory_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn("KRIPTO_KONTROL_MERKEZI_V2_10_MEMORY_FAVORITES", VERSION)
        self.assertIn("kripto_focus_favs", body)
        self.assertIn("kripto_opportunity_preferences_v210", body)
        self.assertIn("data-opp-fav", body)
        self.assertIn("Filtreyi sıfırla", body)
        self.assertIn("localStorage.setItem(PREF_KEY", body)
        self.assertIn("restorePrefs", body)
        self.assertIn("toggleFavorite", body)
        self.assertIn('nonce="nonce-test"', body)

    def test_v29_route_and_v28_filters_remain(self):
        body = memory_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn("const HASH_KEY='view'", body)
        self.assertIn('id="oppFilterBar"', body)
        self.assertIn('data-filter="score80"', body)
        self.assertIn('id="oppSort"', body)
        self.assertIn('id="page-opportunities"', body)
        self.assertIn('id="page-watchlist"', body)
        self.assertIn('id="soundToggle"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('id="focusDrawer"', body)

    def test_admin_keeps_management_links(self):
        body = memory_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-admin"},
            "nonce-admin",
        )
        self.assertIn('data-view="system"', body)
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)


if __name__ == "__main__":
    unittest.main()
