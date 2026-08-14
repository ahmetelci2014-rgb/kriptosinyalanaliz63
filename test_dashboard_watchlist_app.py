import unittest

from dashboard_watchlist_app import watchlist_dashboard_page


class DashboardWatchlistV25Tests(unittest.TestCase):
    def test_member_page_has_watchlist_and_keeps_alert_layers(self):
        body = watchlist_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('id="page-watchlist"', body)
        self.assertIn("İzleme Listesi", body)
        self.assertIn('id="watchGrid"', body)
        self.assertIn('id="watchAddInput"', body)
        self.assertIn("kripto_focus_favs", body)
        self.assertIn("/api/market/overview", body)
        self.assertIn("/api/market/candles", body)
        self.assertIn("bar=15m", body)
        self.assertIn("RSI 14", body)
        self.assertIn("Aktif sinyal", body)
        self.assertIn('id="soundToggle"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn('nonce="nonce-test"', body)
        self.assertNotIn('data-view="system"><span>◉</span><b>Sistem</b>', body)

    def test_watchlist_is_integrated_into_spa_navigation(self):
        body = watchlist_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('data-view="watchlist"', body)
        self.assertIn("watchlist:'İzleme Listesi'", body)
        self.assertIn("setInterval", body)
        self.assertIn("30000", body)
        self.assertIn("indicatorCache", body)
        self.assertIn("120000", body)
        self.assertIn("Math.min(3,queue.length)", body)
        self.assertIn('data-focus-symbol=', body)
        self.assertIn("/market-center?symbol=", body)

    def test_admin_keeps_management_system_and_advanced_fallback(self):
        body = watchlist_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-test"},
            "nonce-admin",
        )
        self.assertIn('data-view="system"', body)
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)
        self.assertIn('href="/market-center"', body)
        self.assertIn('id="page-watchlist"', body)
        self.assertIn('id="soundToggle"', body)


if __name__ == "__main__":
    unittest.main()
