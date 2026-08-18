import unittest

import paper_demo_executor as mod


class PaperDemoExecutorTests(unittest.TestCase):
    def signal(self, direction="LONG"):
        return {
            "trade_id": "TEST_1",
            "symbol": "TESTUSDT",
            "direction": direction,
            "source": "15M_ENTRY",
            "score": 98,
            "entry": 100.0,
            "sl": 99.0 if direction == "LONG" else 101.0,
            "tp1": 100.55 if direction == "LONG" else 99.45,
            "tp2": 101.05 if direction == "LONG" else 98.95,
            "tp3": 101.60 if direction == "LONG" else 98.40,
            "opened_at": 1000,
            "entry_distance_at_send_percent": 0.1,
            "tp1_hit": False,
            "closed": False,
        }

    def test_candidate_requires_score_and_freshness(self):
        sig = self.signal()
        self.assertTrue(mod.signal_is_structurally_eligible(sig, 1200))
        sig["score"] = 90
        self.assertFalse(mod.signal_is_structurally_eligible(sig, 1200))
        sig["score"] = 98
        self.assertFalse(mod.signal_is_structurally_eligible(sig, 4000))

    def test_fill_guard(self):
        sig = self.signal()
        ok, _ = mod.candidate_can_fill(sig, 100.1)
        self.assertTrue(ok)
        ok, _ = mod.candidate_can_fill(sig, 100.4)
        self.assertFalse(ok)

    def test_long_tp1_then_be(self):
        pos = mod.open_paper_position(self.signal("LONG"), 100.0, 1000)
        mod.process_candles(
            pos,
            [
                {"ts": 1060, "open": 100.0, "high": 100.7, "low": 99.8, "close": 100.6},
                {"ts": 1120, "open": 100.6, "high": 100.8, "low": 99.9, "close": 100.0},
            ],
        )
        self.assertTrue(pos["tp1_hit"])
        self.assertEqual(pos["final_result"], "BE_AFTER_TP1")
        self.assertGreater(pos["realized_pnl_usdt"], 0)

    def test_short_tp3(self):
        pos = mod.open_paper_position(self.signal("SHORT"), 100.0, 1000)
        mod.process_candles(
            pos,
            [
                {"ts": 1060, "open": 100.0, "high": 100.2, "low": 99.3, "close": 99.4},
                {"ts": 1120, "open": 99.4, "high": 99.5, "low": 98.3, "close": 98.5},
            ],
        )
        self.assertEqual(pos["final_result"], "TP3")
        self.assertGreater(pos["realized_pnl_usdt"], 0)

    def test_same_candle_stop_first(self):
        pos = mod.open_paper_position(self.signal("LONG"), 100.0, 1000)
        mod.process_candles(
            pos,
            [{"ts": 1060, "open": 100.0, "high": 101.8, "low": 98.8, "close": 100.5}],
        )
        self.assertEqual(pos["final_result"], "SL")


if __name__ == "__main__":
    unittest.main()
