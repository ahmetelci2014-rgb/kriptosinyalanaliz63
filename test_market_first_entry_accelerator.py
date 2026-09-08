import unittest

import market_first_entry_accelerator as accelerator
import market_first_entry_plan as entry_plan
import market_first_fast_entry as fast_entry
import market_first_live_direction_guard as direction_guard


class EntryAcceleratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        accelerator.install()

    def test_controlled_thresholds_are_active(self):
        self.assertEqual(entry_plan.ENTRY_MIN_SCORE, 76)
        self.assertAlmostEqual(entry_plan.MIN_ENTRY_VOLUME_RATIO, 0.50)
        self.assertAlmostEqual(entry_plan.MAX_PLAN_RISK_PERCENT, 1.45)
        self.assertAlmostEqual(entry_plan.MIN_ROOM_R, 1.45)
        self.assertEqual(fast_entry.MIN_FAST_SCORE, 70)
        self.assertAlmostEqual(fast_entry.MIN_MOVE_3_PERCENT, 0.15)
        self.assertAlmostEqual(fast_entry.MIN_MOVE_5_PERCENT, 0.20)
        self.assertAlmostEqual(fast_entry.MIN_VOLUME_RATIO, 0.70)
        self.assertAlmostEqual(fast_entry.MAX_EXTENSION_ATR, 1.25)

    def test_confirmed_plan_can_proceed_when_raw_micro_object_is_absent(self):
        allowed, diag = direction_guard.fresh_entry_plan_confirmation(None, "LONG")
        self.assertTrue(allowed)
        self.assertEqual(diag.get("reason"), "ENTRY_PLAN_NATIVE_CONFIRMATION")

    def test_direction_conflict_is_still_blocked(self):
        decision = {
            "direction": "SHORT",
            "move_3m_percent": -0.20,
            "move_5m_percent": -0.30,
            "relative_strength_5m": 0.40,
            "breakout_20m": True,
        }
        allowed, diag = direction_guard.fresh_entry_plan_confirmation(decision, "LONG")
        self.assertFalse(allowed)
        self.assertEqual(diag.get("reason"), "MICRO_DIRECTION_CONFLICT")

    def test_small_fresh_progress_is_enough_near_confirmed_plan(self):
        decision = {
            "direction": "LONG",
            "move_3m_percent": 0.04,
            "move_5m_percent": 0.02,
            "relative_strength_5m": 0.0,
            "breakout_20m": False,
        }
        allowed, diag = direction_guard.fresh_entry_plan_confirmation(decision, "LONG")
        self.assertTrue(allowed)
        self.assertTrue(diag.get("confirmations", {}).get("move_3m"))

    def test_low_score_early_noise_is_not_fast_traded(self):
        decision = {
            "stage": "EARLY",
            "trade_eligible": False,
            "direction": "LONG",
            "score": 60,
            "move_1m_percent": 0.20,
            "move_3m_percent": 0.40,
            "move_5m_percent": 0.60,
            "volume_ratio_1m": 2.0,
            "extension_atr_5m": 0.5,
            "breakout_20m": True,
        }
        promoted, reason, diag = fast_entry.promote_initial_early(
            decision,
            "OK",
            df5m=None,
            df15m=None,
            df1h=None,
            current_price=1.0,
            context=None,
        )
        self.assertFalse(diag.get("promoted"))
        self.assertEqual(diag.get("reason"), "FAST_SCORE_WEAK")
        self.assertFalse(promoted.get("trade_eligible"))


if __name__ == "__main__":
    unittest.main()
