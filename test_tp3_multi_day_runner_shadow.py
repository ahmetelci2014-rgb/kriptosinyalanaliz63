import unittest

import tp3_multi_day_runner_shadow as runner


class Tp3MultiDayRunnerShadowTests(unittest.TestCase):
    def test_short_measurement_tracks_multi_day_extension(self):
        start = 1_000_000
        trade = {
            "direction": "SHORT",
            "entry": 100.0,
            "sl": 101.0,
        }
        rows = []
        for hour in range(24):
            close = 98.4 - hour * 0.05
            rows.append([
                (start + hour * 3600) * 1000,
                close + 0.05,
                close + 0.10,
                close - 0.10,
                close,
                1.0,
            ])
        result = runner.measure_rows(
            trade,
            rows,
            now_ts=start + 24 * 3600,
            closed_at=start,
            reference_price=98.4,
        )
        self.assertGreater(result["max_favorable_r"], 1.0)
        self.assertIn("12", result["checkpoints"])
        self.assertIn("24", result["checkpoints"])

    def test_runner_value_class(self):
        self.assertEqual(
            runner._runner_class(1.2, 0.4),
            "MULTI_DAY_RUNNER_STRONG",
        )
        self.assertEqual(
            runner._runner_class(0.6, 0.5),
            "MULTI_DAY_RUNNER_USEFUL",
        )
        self.assertEqual(
            runner._runner_class(0.1, 0.2),
            "MULTI_DAY_RUNNER_NO_EDGE",
        )


if __name__ == "__main__":
    unittest.main()
