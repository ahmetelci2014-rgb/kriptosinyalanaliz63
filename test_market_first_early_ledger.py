import unittest

import market_first_early_ledger_hooks as ledger


class EarlyLedgerTests(unittest.TestCase):
    def decision(self, direction="LONG"):
        return {
            "symbol": "ZKPUSDT",
            "direction": direction,
            "current_price": 100.0,
            "score": 64,
            "market_label": "KARIŞIK",
            "market_regime": "CHOP",
            "move_1m_percent": 0.35 if direction == "LONG" else -0.35,
            "move_3m_percent": 0.72 if direction == "LONG" else -0.72,
            "move_5m_percent": 1.05 if direction == "LONG" else -1.05,
            "volume_ratio_1m": 0.75,
            "breakout_20m": True,
        }

    def test_long_early_alert_is_recorded_without_tp(self):
        store = ledger.empty_ledger()
        eid = ledger.register_episode(store, self.decision(), 1_000)
        self.assertIn(eid, store["episodes"])
        episode = store["episodes"][eid]
        self.assertNotIn("tp1", episode)
        self.assertEqual(episode["alert_price"], 100.0)
        self.assertEqual(episode["status"], "NEW")

        item = {
            "early_episode_id": eid,
            "direction": "LONG",
            "alert_price": 100.0,
            "last_price": 102.4,
            "best_favorable_percent": 2.4,
            "status": "DEAD",
        }
        self.assertTrue(ledger.update_episode(store, item, 1_600))
        episode = store["episodes"][eid]
        self.assertTrue(episode["resolved"])
        self.assertEqual(episode["outcome"], "STRONG_MOVE")
        self.assertEqual(episode["quality_label"], 1)
        self.assertAlmostEqual(episode["final_directional_percent"], 2.4, places=3)

    def test_short_direction_is_normalized_and_adverse_move_is_bad(self):
        store = ledger.empty_ledger()
        eid = ledger.register_episode(store, self.decision("SHORT"), 2_000)
        item = {
            "early_episode_id": eid,
            "direction": "SHORT",
            "alert_price": 100.0,
            "last_price": 101.2,
            "best_favorable_percent": 0.2,
            "status": "DEAD",
        }
        ledger.update_episode(store, item, 2_600)
        episode = store["episodes"][eid]
        self.assertLess(episode["final_directional_percent"], 0)
        self.assertGreaterEqual(episode["worst_adverse_percent"], 1.19)
        self.assertEqual(episode["outcome"], "BAD_MOVE")
        self.assertEqual(episode["quality_label"], 0)

    def test_summary_counts_outcomes(self):
        store = ledger.empty_ledger()
        store["episodes"] = {
            "a": {"resolved": True, "outcome": "GOOD_MOVE"},
            "b": {"resolved": True, "outcome": "BAD_MOVE"},
            "c": {"resolved": True, "outcome": "MIXED"},
            "d": {"resolved": False, "outcome": None},
        }
        summary = ledger.ledger_summary(store)
        self.assertEqual(summary, {"total": 4, "open": 1, "good": 1, "bad": 1, "mixed": 1})


if __name__ == "__main__":
    unittest.main()
