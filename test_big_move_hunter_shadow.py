import unittest

import big_move_hunter_shadow as hunter


class BigMoveHunterShadowTests(unittest.TestCase):
    def test_capture_stage_buckets(self):
        self.assertEqual(hunter.capture_stage(1.5), "COK_ERKEN")
        self.assertEqual(hunter.capture_stage(4.0), "ERKEN")
        self.assertEqual(hunter.capture_stage(8.0), "ORTA")
        self.assertEqual(hunter.capture_stage(15.0), "GEC")
        self.assertEqual(hunter.capture_stage(25.0), "COK_GEC")

    def test_move_classes(self):
        self.assertEqual(hunter.move_class(4.9), "HENUZ_BUYUK_DEGIL")
        self.assertEqual(hunter.move_class(5.0), "ANLAMLI_5P")
        self.assertEqual(hunter.move_class(10.0), "GUCLU_10P")
        self.assertEqual(hunter.move_class(20.0), "BUYUK_20P")
        self.assertEqual(hunter.move_class(40.0), "OLAGANUSTU_40P")

    def test_long_available_share(self):
        share = hunter.available_share_percent("LONG", 100.0, 102.0, 120.0)
        self.assertAlmostEqual(share, 90.0, places=4)

    def test_short_available_share(self):
        share = hunter.available_share_percent("SHORT", 100.0, 98.0, 80.0)
        self.assertAlmostEqual(share, 90.0, places=4)

    def test_early_strong_profile_is_high_candidate(self):
        record = {
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "movement_entry": 102.0,
            "current_price": 120.0,
            "status": "4H_DEVAM_GUCLU",
            "score": 91,
            "confidence": "YUKSEK",
            "initial_stage": "TRIGGER",
            "initial_base_score": 96,
            "best_base_score": 96,
            "trend_origin": {
                "price": 100.0,
                "at": 1_000_000,
                "method": "TEST",
                "life_hours": 24.0,
            },
        }
        result = hunter.evaluate_record(record)
        self.assertIsNotNone(result)
        self.assertEqual(result["capture_stage"], "COK_ERKEN")
        self.assertEqual(result["move_class"], "BUYUK_20P")
        self.assertEqual(result["research_label"], "BUYUK_HAREKET_ADAYI_GUCLU")
        self.assertGreaterEqual(result["available_share_of_observed_trend_percent"], 89.0)

    def test_late_detection_is_not_strong_candidate(self):
        record = {
            "symbol": "LATEUSDT",
            "direction": "LONG",
            "movement_entry": 125.0,
            "current_price": 130.0,
            "status": "4H_DEVAM_GUCLU",
            "score": 90,
            "confidence": "YUKSEK",
            "initial_stage": "TRIGGER",
            "initial_base_score": 99,
            "best_base_score": 99,
            "trend_origin": {
                "price": 100.0,
                "at": 1_000_000,
                "method": "TEST",
                "life_hours": 48.0,
            },
        }
        result = hunter.evaluate_record(record)
        self.assertIsNotNone(result)
        self.assertEqual(result["capture_stage"], "COK_GEC")
        self.assertNotEqual(result["research_label"], "BUYUK_HAREKET_ADAYI_GUCLU")
        self.assertLess(result["available_share_of_observed_trend_percent"], 20.0)


if __name__ == "__main__":
    unittest.main()
