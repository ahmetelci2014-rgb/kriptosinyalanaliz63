import unittest

from dashboard_route_app import VERSION, route_dashboard_page


class DashboardRouteV29Tests(unittest.TestCase):
    def test_member_page_persists_active_view_in_hash(self):
        body = route_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn("KRIPTO_KONTROL_MERKEZI_V2_9_VIEW_PERSISTENCE", VERSION)
        self.assertIn("const HASH_KEY='view'", body)
        self.assertIn("history.replaceState", body)
        self.assertIn("requestAnimationFrame(restore)", body)
        self.assertIn("window.addEventListener('hashchange'", body)
        self.assertIn("data-view=\"opportunities\"", body)
        self.assertIn("data-view=\"watchlist\"", body)
        self.assertIn('nonce="nonce-test"', body)

    def test_existing_v28_features_remain(self):
        body = route_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('id="oppFilterBar"', body)
        self.assertIn('data-filter="score80"', body)
        self.assertIn('id="soundToggle"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn('/api/market/analysis-score', body)

    def test_admin_system_view_can_be_persisted(self):
        body = route_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-admin"},
            "nonce-admin",
        )
        self.assertIn('data-view="system"', body)
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)


if __name__ == "__main__":
    unittest.main()
