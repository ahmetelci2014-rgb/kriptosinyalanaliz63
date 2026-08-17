import unittest

import signal_quality_audit as audit


class SignalQualityAuditTests(unittest.TestCase):
    def test_root_cause_summary_uses_only_finalized_primary(self):
        records = [
            {
                "status": "CLOSED",
                "final_result": "SL",
                "stop_root_cause": {
                    "primary": "FITIL_DAR_STOP",
                    "provisional": False,
                },
                "post_stop_follow": {"status": "RETURNED_TO_TARGET"},
            },
            {
                "status": "CLOSED",
                "final_result": "SL",
                "stop_root_cause": {
                    "primary": "TAKIP_SURUYOR",
                    "preliminary": "MUHTEMEL_YANLIS_YON",
                    "provisional": True,
                },
                "post_stop_follow": {"status": "TRACKING"},
            },
        ]
        stats = audit.root_cause_stats(records)
        self.assertEqual(stats["finalized"], 1)
        self.assertEqual(stats["provisional"], 1)
        self.assertEqual(stats["primary_counts"]["FITIL_DAR_STOP"], 1)
        self.assertEqual(
            stats["preliminary_counts"]["MUHTEMEL_YANLIS_YON"],
            1,
        )
        self.assertEqual(stats["fitil_or_timing_count"], 1)
        self.assertEqual(stats["post_stop_returned_to_target"], 1)

    def test_timing_buckets_report_separate_performance(self):
        records = [
            {
                "status": "CLOSED",
                "final_result": "TP3",
                "r_result": 1.0,
                "entry_distance_at_send_percent": 0.05,
                "zone_distance_percent": 0.08,
                "tp1_progress_at_send_percent": 10,
            },
            {
                "status": "CLOSED",
                "final_result": "SL",
                "r_result": -1.0,
                "entry_distance_at_send_percent": 0.31,
                "zone_distance_percent": 0.32,
                "tp1_progress_at_send_percent": 35,
            },
        ]
        stats = audit.timing_stats(records)
        self.assertEqual(
            stats["by_entry_distance_at_send"]["VERY_CLOSE_0_10"]["tp3_rate_percent"],
            100.0,
        )
        self.assertEqual(
            stats["by_entry_distance_at_send"]["LIMIT_0_25_0_35"]["stop_rate_percent"],
            100.0,
        )
        self.assertEqual(
            stats["by_tp1_progress_at_send"]["LATE_BUT_ALLOWED_20_45"]["sample"],
            1,
        )

    def test_target_capacity_tp3_adds_post_close_extension(self):
        trade = {
            "status": "CLOSED",
            "final_result": "TP3",
            "entry": 100.0,
            "sl": 98.0,
            "tp3": 103.2,
            "best_favorable_r": 1.6,
            "post_result_shadow": {
                "status": "COMPLETED",
                "max_favorable_r": 1.1,
            },
        }
        self.assertAlmostEqual(
            audit.observed_max_r_with_post_result(trade),
            2.7,
            places=6,
        )

    def test_target_capacity_be_uses_entry_reference(self):
        trade = {
            "status": "CLOSED",
            "final_result": "TP1_SONRASI_BE",
            "entry": 100.0,
            "sl": 98.0,
            "tp3": 103.2,
            "best_favorable_r": 0.55,
            "post_result_shadow": {
                "status": "COMPLETED",
                "max_favorable_r": 2.2,
            },
        }
        self.assertAlmostEqual(
            audit.observed_max_r_with_post_result(trade),
            2.2,
            places=6,
        )

    def test_report_recent_window_and_flags(self):
        current_ts = 1_800_000_000
        trades = {}
        for index in range(12):
            trades[str(index)] = {
                "status": "CLOSED",
                "final_result": "SL",
                "closed_at": current_ts - 3600,
                "r_result": -1.0,
                "direction": "LONG",
                "source": "15M_ENTRY",
                "stop_root_cause": {
                    "primary": (
                        "FITIL_DAR_STOP"
                        if index < 8
                        else "MUHTEMEL_YANLIS_YON"
                    ),
                    "provisional": False,
                },
            }

        report = audit.build_report(
            {"trades": trades},
            post_v2={},
            post_v3={},
            current_ts=current_ts,
        )
        self.assertEqual(report["recent_14d"]["outcomes"]["sample"], 12)
        codes = {item["code"] for item in report["decision_flags"]}
        self.assertIn("STOP_TIMING_SHADOW_PRIORITY", codes)
        self.assertIn("DIRECTION_FILTER_REVIEW", codes)


if __name__ == "__main__":
    unittest.main()
