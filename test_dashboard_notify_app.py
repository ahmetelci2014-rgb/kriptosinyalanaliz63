import unittest

from dashboard_notify_app import notification_dashboard_page


class DashboardNotifyV23Tests(unittest.TestCase):
    def test_member_page_has_notification_center_and_keeps_v22_features(self):
        body = notification_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn("Bildirim Merkezi", body)
        self.assertIn('id="notifyTrigger"', body)
        self.assertIn('id="notifyBadge"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn("Okunmamış", body)
        self.assertIn("Tümünü okundu yap", body)
        self.assertIn("kripto_notify_read_v23", body)
        self.assertIn("localStorage", body)
        self.assertIn("Bugünün akışı", body)
        self.assertIn("Favorilerim", body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn('nonce="nonce-test"', body)
        self.assertNotIn('data-view="system"><span>◉</span><b>Sistem</b>', body)

    def test_notification_items_open_existing_coin_focus_layer(self):
        body = notification_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn("data-focus-symbol", body)
        self.assertIn("Coin analizini aç", body)
        self.assertIn("kripto-dashboard-data", body)
        self.assertIn("open_trades", body)
        self.assertIn("recent_results", body)

    def test_admin_keeps_management_and_advanced_fallback(self):
        body = notification_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-test"},
            "nonce-admin",
        )
        self.assertIn('data-view="system"', body)
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)
        self.assertIn("Bildirim Merkezi", body)


if __name__ == "__main__":
    unittest.main()
