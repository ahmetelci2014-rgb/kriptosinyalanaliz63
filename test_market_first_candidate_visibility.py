import unittest

import market_first_candidate_visibility as visibility


class CandidateVisibilityTests(unittest.TestCase):
    def base_decision(self):
        return {
            "symbol": "TESTUSDT",
            "direction": "SHORT",
            "stage": "READY",
            "trade_eligible": True,
            "score": 96,
            "risk_percent": 0.60,
            "extension_atr_5m": 0.70,
            "structure_5m": "SHORT",
            "structure_15m": "SHORT",
            "structure_1h": "SHORT",
            "market_preferred_direction": "SHORT",
            "volume_ratio_5m": 1.20,
            "volume_ratio_1m": 1.10,
            "expected_move_percent": 1.80,
            "technical_target_r": 3.0,
            "room_r": 3.2,
            "direction_engine": {
                "confirmations": 4,
                "reversal": False,
                "selected_direction": "SHORT",
            },
        }

    def test_strong_aligned_rejected_candidate_is_visible(self):
        ok, reason = visibility.candidate_is_visible(self.base_decision())
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")

    def test_non_trade_candidate_is_hidden(self):
        decision = self.base_decision()
        decision["trade_eligible"] = False
        ok, reason = visibility.candidate_is_visible(decision)
        self.assertFalse(ok)
        self.assertEqual(reason, "NOT_TRADE_ELIGIBLE")

    def test_wide_risk_is_hidden(self):
        decision = self.base_decision()
        decision["risk_percent"] = 1.10
        ok, reason = visibility.candidate_is_visible(decision)
        self.assertFalse(ok)
        self.assertEqual(reason, "RISK")

    def test_countertrend_is_hidden(self):
        decision = self.base_decision()
        decision["market_preferred_direction"] = "LONG"
        ok, reason = visibility.candidate_is_visible(decision)
        self.assertFalse(ok)
        self.assertEqual(reason, "MARKET_OPPOSITE")

    def test_weak_confirmation_is_hidden(self):
        decision = self.base_decision()
        decision["direction_engine"]["confirmations"] = 2
        ok, reason = visibility.candidate_is_visible(decision)
        self.assertFalse(ok)
        self.assertEqual(reason, "CONFIRMATIONS")

    def test_cooldown_blocks_repeat(self):
        decision = self.base_decision()
        state = {"last_sent": {"TESTUSDT:SHORT": 1000}}
        self.assertFalse(visibility._can_send(state, decision, 1000 + 60))
        self.assertTrue(
            visibility._can_send(
                state,
                decision,
                1000 + visibility.ALERT_COOLDOWN_SECONDS,
            )
        )


if __name__ == "__main__":
    unittest.main()
