import unittest

import dashboard_freepreview_app as app


class DashboardFreePreviewTests(unittest.TestCase):
    def test_version(self):
        self.assertIn("V3_3_FREE_PREVIEW", app.VERSION)

    def test_preview_uses_newest_open_signal_without_premium_fields(self):
        data = {
            "open_trades": [
                {
                    "symbol": "BTCUSDT",
                    "direction": "LONG",
                    "entry": 100,
                    "tp1": 102,
                    "tp2": 104,
                    "tp3": 106,
                    "sl": 98,
                    "score": 99,
                    "opened_at": 100,
                    "system_label": "Premium MTF",
                },
                {
                    "symbol": "ETHUSDT",
                    "direction": "SHORT",
                    "entry": 200,
                    "tp1": 196,
                    "tp2": 194,
                    "tp3": 190,
                    "sl": 204,
                    "score": 88,
                    "opened_at": 200,
                    "system_label": "Scalp",
                },
            ],
            "recent_results": [],
            "health": {"overall": "GREEN"},
        }
        payload = app.build_free_preview(data)
        signal = payload["free_signal"]
        self.assertEqual(signal["symbol"], "ETHUSDT")
        self.assertEqual(signal["direction"], "SHORT")
        self.assertEqual(signal["entry"], 200.0)
        self.assertEqual(signal["tp1"], 196.0)
        self.assertEqual(signal["sl"], 204.0)
        self.assertNotIn("tp2", signal)
        self.assertNotIn("tp3", signal)
        self.assertNotIn("score", signal)
        self.assertEqual(payload["locked_open_count"], 1)
        self.assertEqual(payload["limits"]["visible_open_signals"], 1)

    def test_recent_results_are_limited_and_safe(self):
        rows = []
        for i in range(8):
            rows.append({
                "symbol": f"COIN{i}USDT",
                "direction": "LONG" if i % 2 == 0 else "SHORT",
                "outcome": "TP1" if i % 3 else "SL",
                "closed_at": 100 + i,
                "entry": 1.0,
                "tp1": 1.1,
                "tp2": 1.2,
                "tp3": 1.3,
                "sl": 0.9,
                "r_result": 4.5,
            })
        payload = app.build_free_preview({"open_trades": [], "recent_results": rows})
        self.assertLessEqual(len(payload["recent_results"]), 5)
        self.assertEqual(payload["recent_results"][0]["symbol"], "COIN7USDT")
        for row in payload["recent_results"]:
            self.assertNotIn("entry", row)
            self.assertNotIn("tp1", row)
            self.assertNotIn("tp2", row)
            self.assertNotIn("tp3", row)
            self.assertNotIn("sl", row)
            self.assertNotIn("r_result", row)

    def test_free_page_has_real_signal_experience_and_premium_boundary(self):
        session = {"username": "demo", "role": "MEMBER", "csrf": "csrf-free"}
        body = app.free_preview_page(session, "nonce-1")
        self.assertIn("FREE GERÇEK DENEYİM", body)
        self.assertIn("Ücretsiz canlı işlem", body)
        self.assertIn("Entry", body)
        self.assertIn("TP1", body)
        self.assertIn("SL", body)
        self.assertIn("TP2 / TP3", body)
        self.assertIn("Fırsat Merkezi", body)
        self.assertIn("Son gerçek sonuçlar", body)
        self.assertIn("/api/free/preview", body)
        self.assertIn("BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,DOGEUSDT", body)
        self.assertIn('action="/logout"', body)
        self.assertIn('value="csrf-free"', body)
        self.assertIn("kazanç garantisi değildir", body)

    def test_signal_selection_does_not_use_result_fields(self):
        data = {
            "open_trades": [
                {"symbol": "AAAUSDT", "direction": "LONG", "entry": 1, "tp1": 1.1, "sl": 0.9, "opened_at": 10, "outcome": "TP3"},
                {"symbol": "BBBUSDT", "direction": "SHORT", "entry": 2, "tp1": 1.9, "sl": 2.1, "opened_at": 20, "outcome": "SL"},
            ],
            "recent_results": [],
        }
        payload = app.build_free_preview(data)
        self.assertEqual(payload["free_signal"]["symbol"], "BBBUSDT")


if __name__ == "__main__":
    unittest.main()
