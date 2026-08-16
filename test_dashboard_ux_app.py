import unittest

import dashboard_ux_app as ux


class ProfessionalUxTests(unittest.TestCase):
    def sample_body(self):
        return '''<!doctype html><html><head><title>Panel</title></head><body>
<section class="page active" id="page-home"><div>home</div></section>
<section class="page" id="page-results"><div class="panel"><div class="panel-body" id="resultsList"></div></div></section>
<div class="topbar"><div class="top-spacer"></div></div>
</body></html>'''

    def test_injects_daily_pulse_and_professional_class(self):
        page = ux.inject_professional_ux(self.sample_body(), "abc123")
        self.assertIn('class="v312-professional"', page)
        self.assertIn('id="v312DailyPulse"', page)
        self.assertIn('id="v312Headline"', page)
        self.assertIn('nonce="abc123"', page)

    def test_injects_result_pagination_and_mobile_ux(self):
        page = ux.inject_professional_ux(self.sample_body(), "nonce")
        self.assertIn("v312ResultPager", page)
        self.assertIn("PAGE_SIZE = 12", page)
        self.assertIn("@media(max-width:760px)", page)
        self.assertIn("mobile-nav", page)

    def test_dashboard_data_event_drives_daily_pulse(self):
        page = ux.inject_professional_ux(self.sample_body(), "nonce")
        self.assertIn("kripto-dashboard-data", page)
        self.assertIn("open_trades", page)
        self.assertIn("recent_results", page)
        self.assertIn("Bugün", page)

    def test_density_preference_is_local_browser_only(self):
        page = ux.inject_professional_ux(self.sample_body(), "nonce")
        self.assertIn("v312_density", page)
        self.assertIn("localStorage", page)
        self.assertNotIn("/api/admin", page)

    def test_does_not_duplicate_layer(self):
        once = ux.inject_professional_ux(self.sample_body(), "nonce")
        twice = ux.inject_professional_ux(once, "nonce")
        self.assertEqual(once, twice)
        self.assertEqual(twice.count('id="v312DailyPulse"'), 1)

    def test_non_dashboard_html_is_unchanged(self):
        body = "<html><head></head><body>login</body></html>"
        self.assertEqual(ux.inject_professional_ux(body, "nonce"), body)

    def test_safety_boundaries_are_explicit(self):
        source = ux.__doc__ or ""
        self.assertIn("Sinyal", source)
        self.assertIn("Telegram", source)
        self.assertEqual(ux.RESULT_PAGE_SIZE, 12)
        page = ux.inject_professional_ux(self.sample_body(), "nonce")
        self.assertNotIn("strategy.py", page)
        self.assertNotIn("config.py", page)
        self.assertNotIn("workflow_dispatch", page)


if __name__ == "__main__":
    unittest.main()
