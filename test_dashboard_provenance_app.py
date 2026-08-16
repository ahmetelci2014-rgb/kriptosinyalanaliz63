import inspect
import unittest

import dashboard_provenance_app as app


class DataProvenanceTests(unittest.TestCase):
    def test_path_types_separate_shadow_from_real_analysis(self):
        self.assertEqual(app.PATH_TYPES["/early-performance"], ("SYSTEM", "MARKET", "DERIVED"))
        self.assertIn("SHADOW", app.PATH_TYPES["/admin/improvement-center"])
        self.assertNotIn("SHADOW", app.PATH_TYPES["/system-quality"])
        self.assertNotIn("SHADOW", app.PATH_TYPES["/learning-center"])

    def test_provenance_bar_explains_account_pnl_boundary(self):
        body = app.provenance_bar("/early-performance")
        self.assertIn("SİSTEM KAYDI", body)
        self.assertIn("PUBLIC PİYASA", body)
        self.assertIn("HESAPLANAN ANALİZ", body)
        self.assertIn("Gerçek hesap P/L değildir", body)
        self.assertNotIn("GÖLGE / SİMÜLASYON", body)

    def test_improvement_center_marks_shadow(self):
        body = app.provenance_bar("/admin/improvement-center")
        self.assertIn("SİSTEM KAYDI", body)
        self.assertIn("HESAPLANAN ANALİZ", body)
        self.assertIn("GÖLGE / SİMÜLASYON", body)

    def test_admin_hub_tools_get_source_tags(self):
        source = (
            '<html><head><style></style></head><body>'
            '<section class="v321-admin-hub" id="v321AdminAnalysisHub">'
            '<a class="v321-tool" href="/learning-center"><span></span><div><b>Öğrenme</b><small>x</small></div><em>→</em></a>'
            '<a class="v321-tool" href="/admin/improvement-center"><span></span><div><b>İyileştirme</b><small>x</small></div><em>→</em></a>'
            '</section></body></html>'
        )
        out = app.enhance_page(source, "/admin/center")
        self.assertIn('id="v322SourceBar"', out)
        self.assertIn('id="v322SourceFacts"', out)
        self.assertIn("PUBLIC PİYASA", out)
        self.assertIn("GÖLGE / SİMÜLASYON", out)
        self.assertIn("v322-card-source", out)

    def test_enhancement_is_idempotent(self):
        source = '<html><head><style></style></head><body><section class="hero"></section></body></html>'
        once = app.enhance_page(source, "/system-quality")
        twice = app.enhance_page(once, "/system-quality")
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="v322SourceBar"'), 1)

    def test_source_facts_identify_internal_and_public_sources(self):
        facts = app.source_facts_block()
        self.assertIn("open_signals.json", facts)
        self.assertIn("trade_ledger.json", facts)
        self.assertIn("OKX public", facts)
        self.assertIn("Binance public fallback", facts)
        self.assertIn("post-result shadow", facts)

    def test_core_boundary_is_explicit(self):
        source = inspect.getsource(app)
        self.assertIn('"real_account_pnl": False', source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)
        self.assertNotIn("import strategy", source)
        self.assertNotIn("from strategy", source)


if __name__ == "__main__":
    unittest.main()
