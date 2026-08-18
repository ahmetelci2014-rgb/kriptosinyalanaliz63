import unittest

import entry_sequence_shadow as mod


class EntrySequenceShadowTests(unittest.TestCase):
    def test_classification_buckets(self):
        self.assertEqual(mod.classify_winner_pre_tp1_mae(0.10), "CLEAN_ENTRY")
        self.assertEqual(mod.classify_winner_pre_tp1_mae(0.40), "NORMAL_PULLBACK")
        self.assertEqual(mod.classify_winner_pre_tp1_mae(0.60), "EARLY_ENTRY_PRESSURE")
        self.assertEqual(
            mod.classify_winner_pre_tp1_mae(0.90), "HEAVY_EARLY_ENTRY_PRESSURE"
        )

    def test_freezes_mae_at_tp1(self):
        state = mod.empty_state()
        sig = {
            "trade_id": "X_SHORT_1",
            "symbol": "XUSDT",
            "direction": "SHORT",
            "source": "15M_ENTRY",
            "entry": 1.0,
            "sl": 1.1,
            "tp1": 0.95,
            "opened_at": 1000,
            "tp1_hit": False,
            "best_favorable_r": 0.20,
            "worst_adverse_r": 0.40,
            "last_checked_at": 1100,
        }
        state = mod.update_state(state, {"X": sig}, {"trades": {}}, now_ts=1100)
        rec = state["records"]["X_SHORT_1"]
        self.assertEqual(rec["pre_tp1_max_adverse_r"], 0.4)
        sig["tp1_hit"] = True
        sig["tp1_hit_at"] = 1300
        sig["best_favorable_r"] = 0.60
        sig["worst_adverse_r"] = 0.45
        state = mod.update_state(state, {"X": sig}, {"trades": {}}, now_ts=1300)
        rec = state["records"]["X_SHORT_1"]
        self.assertTrue(rec["pre_tp1_frozen"])
        self.assertEqual(rec["pre_tp1_max_adverse_r"], 0.45)
        self.assertEqual(rec["classification"], "NORMAL_PULLBACK")

        sig["worst_adverse_r"] = 0.95
        state = mod.update_state(state, {"X": sig}, {"trades": {}}, now_ts=1500)
        rec = state["records"]["X_SHORT_1"]
        self.assertEqual(rec["pre_tp1_max_adverse_r"], 0.45)

    def test_existing_tp1_is_marked_backfill_unreliable(self):
        state = mod.empty_state()
        sig = {
            "trade_id": "OLD_1",
            "symbol": "OLDUSDT",
            "direction": "SHORT",
            "opened_at": 1000,
            "tp1_hit": True,
            "tp1_hit_at": 1200,
            "worst_adverse_r": 0.9,
        }
        state = mod.update_state(state, {"OLD": sig}, {"trades": {}}, now_ts=1300)
        rec = state["records"]["OLD_1"]
        self.assertEqual(rec["sequence_quality"], "BACKFILL_UNRELIABLE")
        self.assertEqual(rec["classification"], "UNRESOLVED_SEQUENCE")

    def test_resolves_failed_before_tp1(self):
        state = mod.empty_state()
        sig = {
            "trade_id": "FAIL_1",
            "symbol": "FAILUSDT",
            "direction": "LONG",
            "opened_at": 1000,
            "tp1_hit": False,
            "best_favorable_r": 0.10,
            "worst_adverse_r": 0.70,
        }
        state = mod.update_state(state, {"FAIL": sig}, {"trades": {}}, now_ts=1100)
        ledger = {
            "trades": {
                "FAIL_1": {
                    "trade_id": "FAIL_1",
                    "status": "CLOSED",
                    "final_result": "SL",
                    "closed_at": 1200,
                    "r_result": -1.0,
                }
            }
        }
        state = mod.update_state(state, {}, ledger, now_ts=1200)
        rec = state["records"]["FAIL_1"]
        self.assertTrue(rec["resolved"])
        self.assertEqual(rec["classification"], "FAILED_BEFORE_TP1")


if __name__ == "__main__":
    unittest.main()
