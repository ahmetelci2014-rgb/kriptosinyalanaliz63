import unittest
from unittest.mock import patch

import market_first_reversal_capture_v2 as reversal
import market_first_strategy as strategy


class ReversalCaptureV2Tests(unittest.TestCase):
    def context(self):
        return strategy.MarketContext(
            regime="BEAR",
            preferred_direction="SHORT",
            score=-25.0,
            strength=25.0,
            breadth_5m=0.35,
            breadth_24h=0.45,
            major_move_5m_percent=-0.10,
            allow_countertrend=True,
            majors={},
        )

    def dead_long_state(self, *, first_at=900, terminal_at=960):
        return {
            "active_alerts": {
                "SOPHUSDT:LONG": {
                    "symbol": "SOPHUSDT",
                    "direction": "LONG",
                    "status": "DEAD",
                    "first_at": first_at,
                    "updated_at": terminal_at,
                    "alert_price": 1.0,
                    "score": 60,
                }
            }
        }

    def test_recent_dead_alert_is_forced_back_into_scan(self):
        state = self.dead_long_state()
        rows = [{"symbol": "BTCUSDT"}, {"symbol": "SOPHUSDT"}]
        forced = reversal.reversal_priority_symbols(state, rows, now=1000)
        self.assertEqual(forced, ["SOPHUSDT"])

    def test_old_or_non_dead_alert_is_not_forced(self):
        state = self.dead_long_state(first_at=1, terminal_at=10)
        rows = [{"symbol": "SOPHUSDT"}]
        self.assertEqual(reversal.reversal_priority_symbols(state, rows, now=20_000), [])
        state = self.dead_long_state()
        state["active_alerts"]["SOPHUSDT:LONG"]["status"] = "CONTINUE"
        self.assertEqual(reversal.reversal_priority_symbols(state, rows, now=1000), [])

    def test_soph_style_dead_long_can_become_short_candidate(self):
        state = self.dead_long_state()
        acceleration = {
            "direction": "SHORT",
            "move_1m_percent": -0.20,
            "move_3m_percent": -0.40,
            "move_5m_percent": -0.60,
            "volume_ratio": 1.40,
            "breakout": True,
        }
        s5 = {"direction": "SHORT", "extension_atr": 0.55}
        s15 = {"direction": "SHORT"}
        s1h = {"direction": "NEUTRAL"}
        risk = {
            "sl": 0.990,
            "tp1": 0.972,
            "tp2": 0.966,
            "tp3": 0.956,
            "risk_percent": 1.02,
            "room_r": 2.10,
        }

        with patch.object(strategy, "_acceleration", return_value=acceleration), \
             patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]), \
             patch.object(strategy, "_risk_plan", return_value=(risk, "OK")):
            promoted, reason, diag = reversal.evaluate_reversal(
                state=state,
                symbol="SOPHUSDT",
                df1m=object(),
                df5m=object(),
                df15m=object(),
                df1h=object(),
                current_price=0.98,
                quote_volume_24h=5_000_000,
                context=self.context(),
                existing_decision=None,
                existing_reason="LOW_SCORE",
                now=1000,
            )

        self.assertEqual(reason, "OK")
        self.assertTrue(diag.get("promoted"))
        self.assertEqual(promoted.get("direction"), "SHORT")
        self.assertTrue(promoted.get("trade_eligible"))
        self.assertTrue(promoted.get("reversal_capture"))
        self.assertGreaterEqual(promoted.get("reversal_capture_score"), 82)
        self.assertIn("SOPHUSDT:LONG:900", state.get("reversal_capture_history", {}))

    def test_small_failure_does_not_flip(self):
        state = self.dead_long_state()
        promoted, reason, diag = reversal.evaluate_reversal(
            state=state,
            symbol="SOPHUSDT",
            df1m=None,
            df5m=None,
            df15m=None,
            df1h=None,
            current_price=0.996,
            quote_volume_24h=5_000_000,
            context=self.context(),
            existing_decision=None,
            existing_reason="LOW_SCORE",
            now=1000,
        )
        self.assertIsNone(promoted)
        self.assertEqual(reason, "LOW_SCORE")
        self.assertFalse(diag.get("promoted"))
        self.assertEqual(diag.get("reason"), "REVERSAL_OLD_DIRECTION_NOT_FAILED_ENOUGH")

    def test_old_1h_direction_blocks_ordinary_whipsaw(self):
        state = self.dead_long_state()
        acceleration = {
            "direction": "SHORT",
            "move_1m_percent": -0.15,
            "move_3m_percent": -0.32,
            "move_5m_percent": -0.50,
            "volume_ratio": 1.0,
            "breakout": False,
        }
        s5 = {"direction": "SHORT", "extension_atr": 0.40}
        s15 = {"direction": "SHORT"}
        s1h = {"direction": "LONG"}

        with patch.object(strategy, "_acceleration", return_value=acceleration), \
             patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]):
            promoted, _, diag = reversal.evaluate_reversal(
                state=state,
                symbol="SOPHUSDT",
                df1m=object(),
                df5m=object(),
                df15m=object(),
                df1h=object(),
                current_price=0.99,
                quote_volume_24h=5_000_000,
                context=self.context(),
                existing_decision=None,
                existing_reason="LOW_SCORE",
                now=1000,
            )

        self.assertIsNone(promoted)
        self.assertEqual(diag.get("reason"), "REVERSAL_1H_STILL_OLD_DIRECTION")

    def test_promotion_cooldown_prevents_repeat_spam(self):
        state = self.dead_long_state()
        key = "SOPHUSDT:LONG:900"
        state["reversal_capture_history"] = {key: {"promoted_at": 950}}
        promoted, _, diag = reversal.evaluate_reversal(
            state=state,
            symbol="SOPHUSDT",
            df1m=None,
            df5m=None,
            df15m=None,
            df1h=None,
            current_price=0.98,
            quote_volume_24h=5_000_000,
            context=self.context(),
            existing_decision=None,
            existing_reason="LOW_SCORE",
            now=1000,
        )
        self.assertIsNone(promoted)
        self.assertEqual(diag.get("reason"), "REVERSAL_PROMOTION_COOLDOWN")


if __name__ == "__main__":
    unittest.main()
