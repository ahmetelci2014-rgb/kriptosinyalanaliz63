import unittest

import okx_demo_executor as demo


class OKXDemoExecutorTests(unittest.TestCase):
    def test_symbol_conversion(self):
        self.assertEqual(
            demo.okx_inst_id("FILUSDT"),
            "FIL-USDT-SWAP",
        )

    def test_contract_sizing(self):
        result = demo.compute_contracts(
            margin_usdt=5,
            leverage=2,
            last=1.0,
            ct_val="0.1",
            lot_sz="1",
            min_sz="1",
        )
        self.assertEqual(result["contracts"], "100")
        self.assertAlmostEqual(
            result["estimated_notional_usdt"],
            10.0,
            places=6,
        )

    def test_select_signal_rejects_old_or_tp1_hit(self):
        now = 2_000
        signals = {
            "old": {
                "trade_id": "OLD",
                "score": 99,
                "entry_distance_at_send_percent": 0.10,
                "opened_at": 1,
                "tp1_hit": False,
                "closed": False,
            },
            "tp1": {
                "trade_id": "TP1",
                "score": 99,
                "entry_distance_at_send_percent": 0.10,
                "opened_at": 1_900,
                "tp1_hit": True,
                "closed": False,
            },
            "good": {
                "trade_id": "GOOD",
                "score": 95,
                "entry_distance_at_send_percent": 0.20,
                "opened_at": 1_950,
                "tp1_hit": False,
                "closed": False,
            },
        }
        picked = demo.select_signal(
            signals,
            trade_id=None,
            now_ts=now,
            min_score=91,
            max_age_minutes=30,
        )
        self.assertEqual(picked["trade_id"], "GOOD")

    def test_plan_preserves_direction_and_protection(self):
        signal = {
            "trade_id": "FIL_SHORT_1",
            "symbol": "FILUSDT",
            "direction": "SHORT",
            "source": "15M_ENTRY",
            "entry": 1.0,
            "sl": 1.02,
            "tp3": 0.95,
            "score": 98,
        }
        instrument = {
            "ctVal": "0.1",
            "lotSz": "1",
            "minSz": "1",
            "tickSz": "0.001",
        }
        plan = demo.make_plan(
            signal=signal,
            instrument=instrument,
            last=0.999,
            margin_usdt=5,
            leverage=2,
            max_drift_percent=0.25,
        )
        self.assertEqual(plan["side"], "sell")
        self.assertEqual(plan["posSide"], "short")
        self.assertEqual(plan["sl"], "1.02")
        self.assertEqual(plan["tp3"], "0.95")

    def test_deterministic_client_ids(self):
        first = demo.deterministic_client_ids("TRADE_123")
        second = demo.deterministic_client_ids("TRADE_123")
        self.assertEqual(first, second)
        self.assertLessEqual(len(first[0]), 32)
        self.assertLessEqual(len(first[1]), 32)


if __name__ == "__main__":
    unittest.main()
