import unittest

import tp3_runner_shadow as runner


class Tp3RunnerShadowTests(unittest.TestCase):
    def test_strong_continuation_is_runner_candidate(self):
        snapshot = {
            "status": "DEVAM_GUCLU",
            "score": 86,
            "confidence": "YUKSEK",
        }
        self.assertEqual(
            runner.classify_runner_candidate(snapshot),
            "RUNNER_STRONG_SHADOW",
        )

    def test_weak_continuation_is_not_runner_candidate(self):
        snapshot = {
            "status": "ZAYIFLIYOR",
            "score": 55,
            "confidence": "ORTA",
        }
        self.assertEqual(
            runner.classify_runner_candidate(snapshot),
            "NO_RUNNER_SHADOW",
        )

    def test_extension_classes(self):
        self.assertEqual(runner.classify_extension(1.20), "STRONG_EXTENSION")
        self.assertEqual(runner.classify_extension(0.60), "USEFUL_EXTENSION")
        self.assertEqual(runner.classify_extension(0.30), "LIMITED_EXTENSION")
        self.assertEqual(runner.classify_extension(0.10), "NO_MEANINGFUL_EXTENSION")

    def test_long_post_tp3_measurement(self):
        start = 1_000_000
        trade = {
            "direction": "LONG",
            "entry": 100.0,
            "sl": 99.0,
        }
        rows = []
        # Three complete 5M candles after TP3=101.60.
        for idx, (high, low, close) in enumerate(
            [
                (101.90, 101.50, 101.80),
                (102.20, 101.70, 102.00),
                (102.60, 101.90, 102.50),
            ]
        ):
            rows.append([
                (start + idx * 300) * 1000,
                101.60,
                high,
                low,
                close,
                1.0,
            ])

        result = runner.evaluate_rows(
            trade,
            rows,
            now_ts=start + 15 * 60,
            started_at=start,
            reference_price=101.60,
        )
        self.assertEqual(result["observations"], 3)
        self.assertAlmostEqual(result["max_favorable_r"], 1.0, places=4)
        self.assertIn("15", result["checkpoints"])

    def test_short_post_tp3_measurement(self):
        start = 2_000_000
        trade = {
            "direction": "SHORT",
            "entry": 100.0,
            "sl": 101.0,
        }
        rows = [
            [start * 1000, 98.40, 98.50, 98.10, 98.20, 1.0],
            [(start + 300) * 1000, 98.20, 98.30, 97.90, 98.00, 1.0],
            [(start + 600) * 1000, 98.00, 98.10, 97.60, 97.70, 1.0],
        ]
        result = runner.evaluate_rows(
            trade,
            rows,
            now_ts=start + 15 * 60,
            started_at=start,
            reference_price=98.40,
        )
        self.assertEqual(result["observations"], 3)
        self.assertAlmostEqual(result["max_favorable_r"], 0.8, places=4)
        self.assertIn("15", result["checkpoints"])


if __name__ == "__main__":
    unittest.main()
