from __future__ import annotations

import unittest

import dashboard_systemquality_app as quality


class SystemQualityMathTests(unittest.TestCase):
    def test_strong_profile_needs_multiple_positive_dimensions(self):
        final = {"sample":20,"tp_rate":70.0,"sl_rate":20.0,"exact_r_sample":20,"net_r":20.0}
        early = {"sample":8,"tp1_first_rate":70.0,"sl_first_rate":10.0,"positive_close_rate":75.0,"average_mfe_r":1.4,"average_mae_r":-0.3,"average_close_r":0.6}
        out = quality.build_system_profile("PREMIUM", final, early)
        self.assertEqual(out["band"], "STRONG")
        self.assertEqual(out["confidence"], "HIGH")
        self.assertGreater(out["score"], 72)
        self.assertGreater(out["final"]["average_r"], 0)
        self.assertIn("İlk 15 dk ortalama R pozitif", out["strengths"])

    def test_risky_profile_marks_early_and_final_risk(self):
        final = {"sample":20,"tp_rate":20.0,"sl_rate":60.0,"exact_r_sample":20,"net_r":-12.0}
        early = {"sample":8,"tp1_first_rate":10.0,"sl_first_rate":60.0,"positive_close_rate":20.0,"average_mfe_r":0.3,"average_mae_r":-1.0,"average_close_r":-0.5}
        out = quality.build_system_profile("SCALP", final, early)
        self.assertEqual(out["band"], "RISKY")
        self.assertLess(out["score"], 45)
        self.assertIn("İlk 15 dk ortalama R negatif", out["flags"])
        self.assertIn("SL ilk teması TP1'den yüksek", out["flags"])
        self.assertIn("Kapanışlarda SL oranı yüksek", out["flags"])

    def test_tiny_sample_is_explicitly_insufficient(self):
        final = {"sample":2,"tp_rate":100.0,"sl_rate":0.0,"exact_r_sample":2,"net_r":4.0}
        early = {"sample":2,"tp1_first_rate":100.0,"sl_first_rate":0.0,"positive_close_rate":100.0,"average_close_r":1.0}
        out = quality.build_system_profile("PUMP_DUMP", final, early)
        self.assertEqual(out["band"], "DATA_INSUFFICIENT")
        self.assertEqual(out["confidence"], "VERY_LOW")
        self.assertIn("İlk 15 dk örneği az", out["flags"])
        self.assertIn("Kapanış örneği az", out["flags"])

    def test_quality_payload_preserves_all_systems(self):
        data = {"performance":{"systems":[
            {"system":"PREMIUM","sample":10,"tp_rate":60,"sl_rate":30,"exact_r_sample":10,"net_r":4},
        ]}}
        early = {"systems":[
            {"system":"PREMIUM","sample":3,"tp1_first_rate":66.7,"sl_first_rate":33.3,"positive_close_rate":66.7,"average_close_r":0.3},
        ]}
        out = quality.build_quality_payload(data, early)
        self.assertEqual(len(out["profiles"]), 4)
        self.assertTrue(out["safety"]["automatic_filter"] is False)
        self.assertEqual(out["safety"]["signal_engine"], "unchanged")


class SystemQualityPageTests(unittest.TestCase):
    def test_page_contract_is_read_only(self):
        body = quality.page("nonce319")
        self.assertIn("Sistem Kalite Profili", body)
        self.assertIn("/api/system-quality", body)
        self.assertIn("GÖZLEMSEL KALİTE PROFİLİ", body)
        self.assertIn('nonce="nonce319"', body)
        self.assertNotIn("method:'POST'", body)
        self.assertNotIn('method="post"', body.lower())

    def test_navigation_is_idempotent(self):
        early = '<a class="btn" href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a>'
        once = quality.enhance_navigation(early, "/early-performance")
        twice = quality.enhance_navigation(once, "/early-performance")
        self.assertEqual(once, twice)
        self.assertEqual(once.count('href="/system-quality"'), 1)

    def test_coin_navigation_uses_existing_favorite_anchor(self):
        body = '<button class="fav" id="favBtn" title="Favori">x</button>'
        out = quality.enhance_navigation(body, "/coin-center")
        self.assertIn('href="/system-quality"', out)


class SystemQualitySourceBoundaryTests(unittest.TestCase):
    def test_version_and_core_boundary(self):
        self.assertIn("V3_19", quality.VERSION)
        source = open(quality.__file__, encoding="utf-8").read()
        self.assertIn('"signal_engine":"unchanged"', source)
        self.assertIn('"telegram":"unchanged"', source)
        self.assertIn('"trade_management":"unchanged"', source)
        self.assertIn('"ledger_write":"unchanged"', source)
        self.assertIn('"automatic_filter":False', source)
        self.assertNotIn("strategy.py", source)
        self.assertNotIn("main.py", source)


if __name__ == "__main__":
    unittest.main()
