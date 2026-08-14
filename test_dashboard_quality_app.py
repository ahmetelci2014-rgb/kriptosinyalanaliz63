import unittest

import dashboard_commercial_app as commercial
import dashboard_freepreview_app as freepreview
import dashboard_quality_app as app
import dashboard_transparency_app as transparency


class DashboardQualityV35Tests(unittest.TestCase):
    def test_public_product_payload_is_safe_and_useful(self):
        data = {
            "open_trades": [
                {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100, "tp1": 101, "tp2": 102, "tp3": 103, "sl": 99, "score": 91},
                {"symbol": "ETHUSDT", "direction": "SHORT", "entry": 200, "tp1": 198, "tp2": 196, "tp3": 194, "sl": 204, "score": 88},
                {"symbol": "SOLUSDT", "direction": "LONG", "entry": 50, "tp1": 51, "sl": 49},
            ],
            "recent_results": [
                {"symbol": "BTCUSDT", "direction": "LONG", "outcome": "TP1", "closed_at": 300},
                {"symbol": "ETHUSDT", "direction": "SHORT", "outcome": "SL", "closed_at": 200},
            ],
            "health": {"overall": "GREEN"},
        }
        payload = app.build_public_product(data, {
            "package_name": "Premium 30 Gün",
            "price_label": "499 TL",
            "days": 30,
            "instructions": "SECRET IBAN SHOULD NOT LEAK",
            "package_code": "SECRET_CODE",
        })
        self.assertEqual(payload["system"]["open_count"], 3)
        self.assertEqual(payload["system"]["free_visible_open"], 1)
        self.assertEqual(payload["system"]["premium_locked_open"], 2)
        self.assertEqual(payload["premium"]["package_name"], "Premium 30 Gün")
        self.assertEqual(payload["premium"]["price_label"], "499 TL")
        self.assertEqual(payload["premium"]["days"], 30)
        text = repr(payload).lower()
        for forbidden in ("entry", "tp1", "tp2", "tp3", "sl", "score", "iban", "secret_code", "instructions", "username", "password"):
            self.assertNotIn(forbidden, text)

    def test_public_product_zero_open_is_not_fake_free_signal(self):
        payload = app.build_public_product({"open_trades": [], "recent_results": []}, {
            "package_name": "Premium", "price_label": "—", "days": 30,
        })
        self.assertEqual(payload["system"]["free_visible_open"], 0)
        self.assertEqual(payload["system"]["premium_locked_open"], 0)

    def test_public_home_has_quality_conversion_sections_and_keeps_transparency(self):
        body = commercial.public_home_page("nonce123")
        body = transparency.enhance_public_home(body, "nonce123")
        body = app.enhance_public_quality(body, "nonce123")
        self.assertIn("Nasıl çalışır?", body)
        self.assertIn("FREE ve PREMIUM farkı", body)
        self.assertIn("FREE gerçekten gerçek işlem gösteriyor mu?", body)
        self.assertIn("/api/public/product", body)
        self.assertIn("/api/public/results", body)
        self.assertIn("Son gerçek sonuçlar", body)
        self.assertIn("v35-mobile-cta", body)
        self.assertIn('id="plansLegacy"', body)
        self.assertIn('id="plans"', body)
        self.assertIn('nonce="nonce123"', body)

    def test_free_quality_keeps_real_signal_and_follow_layer(self):
        base = freepreview.free_preview_page({"username": "demo", "csrf": "csrf123"}, "nonce123")
        base = transparency.enhance_free_page(base, "nonce123")
        body = app.enhance_free_quality(base, "nonce123")
        self.assertIn("FREE ile gerçek sistemi ölç", body)
        self.assertIn("1 gerçek sinyal", body)
        self.assertIn("Son 5 kayıt", body)
        self.assertIn("6 canlı coin", body)
        self.assertIn("Premium özellikleri aç", body)
        self.assertIn(transparency.FREE_FOLLOW_STORAGE_KEY, body)
        self.assertIn("kripto-free-preview", body)
        self.assertIn("/api/free/preview", body)
        self.assertIn("Takip ettiğin FREE işlem sonuçlandı", body)

    def test_existing_free_payload_boundary_is_unchanged(self):
        payload = freepreview.build_free_preview({
            "open_trades": [{
                "symbol": "BTCUSDT", "direction": "LONG", "entry": 100, "tp1": 101,
                "tp2": 102, "tp3": 103, "sl": 99, "score": 90, "opened_at": 1000,
            }],
            "recent_results": [],
        })
        self.assertEqual(payload["free_signal"]["entry"], 100.0)
        self.assertEqual(payload["free_signal"]["tp1"], 101.0)
        self.assertEqual(payload["free_signal"]["sl"], 99.0)
        self.assertNotIn("tp2", payload["free_signal"])
        self.assertNotIn("tp3", payload["free_signal"])
        self.assertNotIn("score", payload["free_signal"])

    def test_version(self):
        self.assertEqual(app.VERSION, "KRIPTO_KONTROL_MERKEZI_V3_5_PRODUCT_QUALITY_2026_08_15")


if __name__ == "__main__":
    unittest.main()
