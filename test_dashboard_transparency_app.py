import unittest

import dashboard_commercial_app as commercial
import dashboard_freepreview_app as freepreview
import dashboard_transparency_app as app


class DashboardTransparencyV34Tests(unittest.TestCase):
    def test_public_results_are_latest_limited_and_level_free(self):
        rows = []
        for idx in range(9):
            rows.append({
                "symbol": f"COIN{idx}USDT",
                "direction": "LONG" if idx % 2 == 0 else "SHORT",
                "outcome": "TP1" if idx % 3 else "SL",
                "system": "TEST",
                "closed_at": 100 + idx,
                "entry": 1.23,
                "tp1": 1.30,
                "tp2": 1.40,
                "tp3": 1.50,
                "sl": 1.10,
                "score": 99,
            })
        payload = app.build_public_results({"recent_results": rows})
        self.assertEqual(len(payload["items"]), 6)
        self.assertEqual(payload["items"][0]["symbol"], "COIN8USDT")
        self.assertEqual(set(payload["items"][0]), {"symbol", "direction", "outcome", "system", "closed_at"})
        text = repr(payload)
        for forbidden in ("entry", "tp1", "tp2", "tp3", "sl", "score"):
            self.assertNotIn(f"'{forbidden}'", text)

    def test_public_results_skip_invalid_rows_without_cherry_pick(self):
        payload = app.build_public_results({"recent_results": [
            {"symbol": "BAD", "direction": "LONG", "outcome": "TP3", "closed_at": 300},
            {"symbol": "LOSSUSDT", "direction": "SHORT", "outcome": "SL", "closed_at": 200},
            {"symbol": "WINUSDT", "direction": "LONG", "outcome": "TP1", "closed_at": 100},
        ]})
        self.assertEqual([row["symbol"] for row in payload["items"]], ["LOSSUSDT", "WINUSDT"])
        self.assertEqual(payload["items"][0]["outcome"], "SL")

    def test_public_home_adds_real_results_without_removing_open_vitrine(self):
        body = app.enhance_public_home(commercial.public_home_page("nonce123"), "nonce123")
        self.assertIn("Son gerçek sonuçlar", body)
        self.assertIn("/api/public/results", body)
        self.assertIn("Ücretsiz başla", body)
        self.assertIn("Yalnız kazananlar seçilmez", body)
        self.assertIn('nonce="nonce123"', body)

    def test_free_page_tracks_previous_visible_signal_result_in_browser(self):
        base = freepreview.free_preview_page({"username": "demo", "csrf": "csrf123"}, "nonce123")
        body = app.enhance_free_page(base, "nonce123")
        self.assertIn(app.FREE_FOLLOW_STORAGE_KEY, body)
        self.assertIn("Takip ettiğin FREE işlem sonuçlandı", body)
        self.assertIn("kripto-free-preview", body)
        self.assertIn("/api/free/preview", body)
        self.assertIn("Premium'a geç", body)
        self.assertIn("TP2 / TP3", body)

    def test_v33_free_payload_boundary_still_level_limited(self):
        payload = freepreview.build_free_preview({
            "open_trades": [{
                "symbol": "BTCUSDT", "direction": "LONG", "entry": 100, "tp1": 101,
                "tp2": 102, "tp3": 103, "sl": 99, "score": 90, "opened_at": 1000,
            }],
            "recent_results": [],
        })
        self.assertEqual(payload["free_signal"]["symbol"], "BTCUSDT")
        self.assertNotIn("tp2", payload["free_signal"])
        self.assertNotIn("tp3", payload["free_signal"])
        self.assertNotIn("score", payload["free_signal"])
        self.assertFalse(payload["limits"]["analysis_score"])

    def test_version(self):
        self.assertEqual(app.VERSION, "KRIPTO_KONTROL_MERKEZI_V3_4_TRANSPARENCY_2026_08_14")


if __name__ == "__main__":
    unittest.main()
