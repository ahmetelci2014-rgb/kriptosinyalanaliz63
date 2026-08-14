import unittest

import dashboard_freepreview_app as freepreview
import dashboard_quality_app as quality
import dashboard_quality_runtime_app as app
import dashboard_transparency_app as transparency


class DashboardQualityRuntimeTests(unittest.TestCase):
    def test_runtime_quota_bar_survives_v34_follow_insertion(self):
        body = freepreview.free_preview_page({"username": "demo", "csrf": "csrf123"}, "nonce123")
        body = transparency.enhance_free_page(body, "nonce123")
        body = quality.enhance_free_quality(body, "nonce123")
        body = app.ensure_free_quota_bar(body, "nonce123")
        self.assertIn('id="v35FreeQuotaBar"', body)
        self.assertIn('id="v35FreeQuotaLocked"', body)
        self.assertIn("1 gerçek sinyal", body)
        self.assertIn("Son 5 kayıt", body)
        self.assertIn("6 canlı coin", body)
        self.assertIn("kripto-free-preview", body)
        self.assertIn('nonce="nonce123"', body)

    def test_runtime_quota_bar_is_idempotent(self):
        body = '<html><body><div class="grid"><div></div></div><script nonce="n"></script></body></html>'
        once = app.ensure_free_quota_bar(body, "n")
        twice = app.ensure_free_quota_bar(once, "n")
        self.assertEqual(twice.count('id="v35FreeQuotaBar"'), 1)
        self.assertEqual(twice.count('id="v35FreeQuotaLocked"'), 1)

    def test_version(self):
        self.assertEqual(app.VERSION, "KRIPTO_KONTROL_MERKEZI_V3_5_PRODUCT_QUALITY_R1_2026_08_15")


if __name__ == "__main__":
    unittest.main()
