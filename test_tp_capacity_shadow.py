import unittest

import tp_capacity_shadow as shadow


class TPCapacityShadowTests(unittest.TestCase):
    def test_pre_close_reach_uses_all_measured_trades(self):
        records = [
            {"best_favorable_r": 0.4},
            {"best_favorable_r": 1.7},
            {"best_favorable_r": 2.6},
            {},
        ]
        result = shadow.pre_close_capacity(records)
        self.assertEqual(result["sample"], 3)
        self.assertEqual(result["reach"]["1.60R"]["reached"], 2)
        self.assertEqual(result["reach"]["2.50R"]["reached"], 1)

    def test_tp3_post_close_supplement_adds_extension(self):
        records = [{
            "final_result": "TP3",
            "entry": 100.0,
            "sl": 98.0,
            "tp3": 103.2,
            "best_favorable_r": 1.6,
            "post_result_shadow": {
                "status": "COMPLETED",
                "max_favorable_r": 1.0,
            },
        }]
        result = shadow.post_close_supplement(records)
        self.assertEqual(result["sample"], 1)
        self.assertEqual(result["upper_bound_reach"]["2.50R"]["reached"], 1)
        self.assertEqual(result["tp3_extension"]["+1.00R"]["reached"], 1)

    def test_current_tp3_realized_r_reflects_half_tp1(self):
        report = shadow.build_report({"trades": {}}, current_ts=1_800_000_000)
        self.assertEqual(
            report["current_structure"]["realized_r_if_tp1_then_tp3"],
            1.075,
        )


if __name__ == "__main__":
    unittest.main()
