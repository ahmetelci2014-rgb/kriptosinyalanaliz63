import inspect
import unittest

import dashboard_adminhub_app as adminhub
import dashboard_provenance_app as provenance
import dashboard_stable_app as stable


HOME_HTML = '''<!doctype html><html><head><style>.x{}</style></head><body>
<div class="summary" id="homeMetrics"></div>
<nav class="mobile-nav"><a href="/account"><span>○</span>Hesap</a></nav>
</body></html>'''


class StablePanelRegressionTests(unittest.TestCase):
    def test_admin_product_view_keeps_member_focus_and_signal_guide_together(self):
        body = stable.enhance_admin_product_view(HOME_HTML, "nonce-test")
        self.assertIn('id="v323MemberFocus"', body)
        self.assertIn('id="v324SignalGuide"', body)
        self.assertIn("YÖNETİCİ · ÜRÜN GÖRÜNÜMÜ", body)
        self.assertIn('class="v323-mobile-results"', body)
        self.assertIn("Coin Merkezi'nde aç", body)
        self.assertEqual(body.count('id="v323MemberFocus"'), 1)
        self.assertEqual(body.count('id="v324SignalGuide"'), 1)

    def test_cumulative_home_can_also_keep_data_provenance(self):
        body = stable.enhance_admin_product_view(HOME_HTML, "nonce-test")
        body = provenance.enhance_page(body, "/")
        self.assertIn('id="v323MemberFocus"', body)
        self.assertIn('id="v324SignalGuide"', body)
        self.assertIn('id="v322SourceBar"', body)
        self.assertIn("SİSTEM KAYDI", body)
        self.assertIn("PUBLIC PİYASA", body)

    def test_admin_analysis_hub_contract_is_preserved(self):
        body = adminhub.admin_analysis_hub()
        self.assertIn("Öğrenme Merkezi", body)
        self.assertIn("Sistem Kalite Profili", body)
        self.assertIn("İlk 15 dk Analizi", body)
        self.assertIn("Performans Zekâsı", body)
        self.assertIn("İyileştirme Karar Merkezi", body)

    def test_stable_layer_does_not_add_trade_writes(self):
        source = inspect.getsource(stable)
        self.assertNotIn("def do_POST", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)
        self.assertIn("signalguide.make_v324_handler", source)


if __name__ == "__main__":
    unittest.main()
