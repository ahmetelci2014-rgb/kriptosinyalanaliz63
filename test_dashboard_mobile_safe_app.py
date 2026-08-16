from __future__ import annotations

import inspect
import unittest

import dashboard_mobile_safe_app as safe
import dashboard_mobile_recovery_app as recovery


class DummyHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class MobileSafeTests(unittest.TestCase):
    def sample_data(self):
        return {
            "open_trades": [
                {"symbol": "BTCUSDT", "direction": "LONG", "system_label": "Premium MTF", "entry": 100, "tp1": 103, "sl": 98, "score": 91},
                {"symbol": "ETHUSDT", "direction": "SHORT", "system_label": "Scalp Radar", "entry": 200, "tp1": 195, "sl": 204, "progress": "AÇIK"},
            ],
            "recent_results": [
                {"symbol": "SOLUSDT", "direction": "LONG", "system_label": "Premium MTF", "outcome": "TP2", "r_result": 1.25},
                {"symbol": "XRPUSDT", "direction": "SHORT", "system_label": "Scalp Radar", "outcome": "SL", "r_result": -1.0},
            ],
        }

    def test_mobile_detection_and_classic_escape_hatch(self):
        self.assertTrue(safe._mobile_request(DummyHeaders({"User-Agent": "Mozilla Android Mobile"}), {}))
        self.assertTrue(safe._mobile_request(DummyHeaders({"Sec-CH-UA-Mobile": "?1"}), {}))
        self.assertTrue(safe._mobile_request(DummyHeaders({"User-Agent": "Desktop"}), {"mobile": ["1"]}))
        self.assertFalse(safe._mobile_request(DummyHeaders({"User-Agent": "Mozilla Android Mobile"}), {"classic": ["1"]}))
        self.assertFalse(safe._mobile_request(DummyHeaders({"User-Agent": "Desktop"}), {}))

    def test_safe_page_is_server_rendered_and_javascript_free(self):
        session = {"username": "member", "csrf": "csrf-safe"}
        body = safe.mobile_safe_page(session, self.sample_data(), "home")
        self.assertIn("Mobil güvenli görünüm", body)
        self.assertNotIn("<script", body.lower())
        self.assertNotIn("onclick=", body.lower())
        self.assertIn('href="/mobile-safe?view=signals"', body)
        self.assertIn('href="/mobile-safe?view=trades"', body)
        self.assertIn('href="/mobile-safe?view=results"', body)
        self.assertIn('href="/account"', body)
        self.assertIn('href="/market-center"', body)
        self.assertIn("BTCUSDT", body)
        self.assertIn("SOLUSDT", body)

    def test_each_safe_view_renders_real_data_without_spa(self):
        session = {"username": "member", "csrf": "csrf-safe"}
        signals = safe.mobile_safe_page(session, self.sample_data(), "signals")
        trades = safe.mobile_safe_page(session, self.sample_data(), "trades")
        results = safe.mobile_safe_page(session, self.sample_data(), "results")
        self.assertIn("Sinyaller", signals)
        self.assertIn("Skor", signals)
        self.assertIn("İşlemler", trades)
        self.assertIn("Giriş / TP1 / SL", trades)
        self.assertIn("Sonuçlar", results)
        self.assertIn("TP2", results)
        self.assertIn("SL", results)
        for body in (signals, trades, results):
            self.assertNotIn("<script", body.lower())

    def test_runtime_is_presentation_only_and_wraps_v336(self):
        source = inspect.getsource(safe)
        self.assertNotIn("def do_POST", source)
        self.assertIn("recovery.make_v336_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)
        self.assertEqual(recovery.VERSION, "KRIPTO_KONTROL_MERKEZI_V3_36_MOBILE_RECOVERY_2026_08_16")


if __name__ == "__main__":
    unittest.main()
