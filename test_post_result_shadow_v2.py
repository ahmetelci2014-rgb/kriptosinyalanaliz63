import unittest

from post_result_shadow_v2 import build_report


def trade(result, source="15M_ENTRY", direction="LONG", reached=None, mfe=0.0, mae=0.0):
    return {
        "final_result": result, "source": source, "direction": direction,
        "post_result_shadow": {
            "status": "COMPLETED", "reached_levels": reached or {},
            "max_favorable_r": mfe, "max_adverse_r": mae,
            "checkpoints": {"15": {"directional_r_from_reference": 0.2}},
        },
    }


class PostResultShadowV2Tests(unittest.TestCase):
    def test_recovery_and_extension_rates(self):
        ledger = {"trades": {
            "a": trade("TP1_SONRASI_BE", reached={"TP2": {}, "TP3": {}}, mfe=1.2, mae=0.1),
            "b": trade("TP1_SONRASI_BE", mfe=0.2, mae=0.8),
        }}
        result = build_report(ledger, generated_at=1)["by_final_result"]["TP1_SONRASI_BE"]
        self.assertEqual(result["tp2_recovery_rate"], 50.0)
        self.assertEqual(result["tp3_recovery_rate"], 50.0)
        self.assertEqual(result["extension_1r_rate"], 50.0)
        self.assertEqual(result["adverse_0_5r_rate"], 50.0)

    def test_tracking_trade_is_excluded(self):
        item = trade("TP3")
        item["post_result_shadow"]["status"] = "TRACKING"
        report = build_report({"trades": {"x": item}}, generated_at=1)
        self.assertEqual(report["overall"]["sample"], 0)
        self.assertEqual(report["data_quality"]["tracking_samples_excluded"], 1)

    def test_small_groups_remain_observation_only(self):
        report = build_report({"trades": {"x": trade("TP3", mfe=2.0)}}, generated_at=1)
        self.assertEqual(report["by_final_result"]["TP3"]["evidence_gate"], "OBSERVE_ONLY")
        self.assertFalse(report["decision"]["automatic_rule_change"])


if __name__ == "__main__":
    unittest.main()
