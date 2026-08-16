import inspect
import unittest

import dashboard_datahealth_app as datahealth
import dashboard_progressive_app as progressive
import dashboard_stable_app as stable


BASE_HTML = '<!doctype html><html><head><style></style></head><body><div class="summary" id="homeMetrics"></div></body></html>'


class ProgressiveDisclosureTests(unittest.TestCase):
    def cumulative_body(self):
        body = stable.enhance_admin_product_view(BASE_HTML, "nonce-test")
        body = datahealth.enhance_data_health(body, "nonce-test")
        return progressive.enhance_progressive_ui(body, "nonce-test")

    def test_keeps_existing_layers_and_adds_one_progressive_script(self):
        body = self.cumulative_body()
        self.assertIn('id="v323MemberFocus"', body)
        self.assertIn('id="v324SignalGuide"', body)
        self.assertIn('id="v326DataHealth"', body)
        self.assertIn('id="v327-progressive-script"', body)
        self.assertEqual(body.count('id="v327-progressive-script"'), 1)

    def test_details_are_hidden_by_default_but_not_removed(self):
        body = self.cumulative_body()
        self.assertIn('#v326DataHealth.v327-collapsed .v326-grid{display:none}', body)
        self.assertIn('#v324SignalGuide.v327-collapsed .v324-list{display:none}', body)
        self.assertIn('Sistem ayrıntılarını göster', body)
        self.assertIn('Sinyal açıklamalarını göster', body)
        self.assertIn('aria-expanded', body)

    def test_health_attention_auto_opens_details(self):
        body = self.cumulative_body()
        self.assertIn("t.includes('KONTROL ET')", body)
        self.assertIn("t.includes('İZLE')", body)
        self.assertIn('MutationObserver(inspect)', body)

    def test_signal_count_can_update_optional_detail_label(self):
        body = self.cumulative_body()
        self.assertIn("querySelectorAll('.v324-card').length", body)
        self.assertIn('Sinyal açıklamalarını göster (${count})', body)

    def test_enhancement_is_idempotent(self):
        body = self.cumulative_body()
        self.assertEqual(progressive.enhance_progressive_ui(body, "nonce-test"), body)

    def test_source_contract_is_presentation_only(self):
        source = inspect.getsource(progressive)
        self.assertNotIn('def do_POST', source)
        self.assertIn('datahealth.make_v326_handler', source)
        self.assertIn('"progressive_disclosure": True', source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == '__main__':
    unittest.main()
