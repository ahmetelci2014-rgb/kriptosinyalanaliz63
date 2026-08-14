import unittest

from dashboard_alert_app import alert_dashboard_page


class DashboardAlertV24Tests(unittest.TestCase):
    def test_member_page_has_sound_toggle_colored_alerts_and_existing_notifications(self):
        body = alert_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('id="soundToggle"', body)
        self.assertIn('id="alertStack"', body)
        self.assertIn("AudioContext", body)
        self.assertIn("kripto_alert_sound_v24", body)
        self.assertIn("kripto_alert_seen_v24", body)
        self.assertIn("signal-long", body)
        self.assertIn("signal-short", body)
        self.assertIn(".alert-toast.tp", body)
        self.assertIn(".alert-toast.sl", body)
        self.assertIn("if(o.startsWith('TP'))return 'tp'", body)
        self.assertIn("if(o==='SL')return 'sl'", body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn("Bugünün akışı", body)
        self.assertIn('nonce="nonce-test"', body)

    def test_semantic_section_colors_are_present_without_admin_leak(self):
        body = alert_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('.nav-item[data-view="signals"].active', body)
        self.assertIn('.nav-item[data-view="trades"].active', body)
        self.assertIn('.nav-item[data-view="results"].active', body)
        self.assertIn('#page-signals .panel', body)
        self.assertIn('#page-results .panel', body)
        self.assertNotIn('data-view="system"><span>◉</span><b>Sistem</b>', body)

    def test_admin_keeps_admin_navigation_and_fallbacks(self):
        body = alert_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-test"},
            "nonce-admin",
        )
        self.assertIn('data-view="system"', body)
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)
        self.assertIn('href="/market-center"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('id="soundToggle"', body)


if __name__ == "__main__":
    unittest.main()
