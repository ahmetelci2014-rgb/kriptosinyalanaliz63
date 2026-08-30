import unittest
from unittest.mock import patch

import market_first_fast_entry as fast
import market_first_strategy as strategy


class FastEntryTests(unittest.TestCase):
    def context(self):
        return strategy.MarketContext(
            regime="CHOP",
            preferred_direction=None,
            score=0.0,
            strength=0.0,
            breadth_5m=0.5,
            breadth_24h=0.5,
            major_move_5m_percent=0.0,
            allow_countertrend=True,
            majors={},
        )

    def structures(self):
        s5 = {
            "direction": "LONG",
            "atr": 1.0,
            "swing_low_12": 99.6,
            "swing_high_12": 100.8,
            "range_low_72": 97.0,
            "range_high_72": 103.0,
        }
        s15 = {
            "direction": "LONG",
            "atr": 1.5,
            "swing_low_12": 98.0,
            "swing_high_12": 101.0,
            "range_low_72": 96.0,
            "range_high_72": 104.0,
        }
        s1h = {"direction": "NEUTRAL"}
        return s5, s15, s1h

    def decision(self, **changes):
        row = {
            "symbol": "BICOUSDT",
            "direction": "LONG",
            "stage": "EARLY",
            "score": 74,
            "trade_eligible": False,
            "alert_eligible": True,
            "move_1m_percent": 0.57,
            "move_3m_percent": 0.92,
            "move_5m_percent": 1.27,
            "volume_ratio_1m": 0.64,
            "extension_atr_5m": 0.90,
            "breakout_20m": True,
            "independent_move": False,
        }
        row.update(changes)
        return row

    def test_fast_import_lowers_awareness_floor(self):
        self.assertEqual(strategy.MIN_ALERT_SCORE, fast.FAST_ALERT_SCORE)
        self.assertEqual(fast.FAST_ALERT_SCORE, 58)

    def test_bico_like_first_observation_becomes_actionable(self):
        with patch.object(strategy, "_structure", side_effect=self.structures()):
            promoted, reason, diag = fast.promote_initial_early(
                self.decision(),
                "OK",
                df5m=object(),
                df15m=object(),
                df1h=object(),
                current_price=100.0,
                context=self.context(),
            )
        self.assertEqual(reason, "OK")
        self.assertTrue(diag["promoted"])
        self.assertTrue(promoted["trade_eligible"])
        self.assertTrue(promoted["fast_entry"])
        self.assertFalse(promoted["alert_eligible"])
        self.assertLess(promoted["sl"], 100.0)
        self.assertGreater(promoted["tp1"], 100.0)

    def test_near_threshold_bico_like_move_can_trade_without_second_run(self):
        with patch.object(strategy, "_structure", side_effect=self.structures()):
            promoted, reason, diag = fast.promote_initial_early(
                self.decision(score=60, breakout_20m=False),
                "OK",
                df5m=object(),
                df15m=object(),
                df1h=object(),
                current_price=100.0,
                context=self.context(),
            )
        self.assertEqual(reason, "OK")
        self.assertTrue(diag["promoted"])
        self.assertTrue(promoted["trade_eligible"])

    def test_one_minute_reversal_does_not_chase(self):
        with patch.object(strategy, "_structure", side_effect=self.structures()):
            promoted, _, diag = fast.promote_initial_early(
                self.decision(move_1m_percent=-0.20),
                "OK",
                df5m=object(),
                df15m=object(),
                df1h=object(),
                current_price=100.0,
                context=self.context(),
            )
        self.assertFalse(diag["promoted"])
        self.assertEqual(diag["reason"], "FAST_1M_REVERSING")
        self.assertFalse(promoted["trade_eligible"])

    def test_already_extended_move_is_not_fast_entry(self):
        with patch.object(strategy, "_structure", side_effect=self.structures()):
            promoted, _, diag = fast.promote_initial_early(
                self.decision(move_5m_percent=2.80),
                "OK",
                df5m=object(),
                df15m=object(),
                df1h=object(),
                current_price=100.0,
                context=self.context(),
            )
        self.assertFalse(diag["promoted"])
        self.assertEqual(diag["reason"], "FAST_5M_OUTSIDE")
        self.assertFalse(promoted["trade_eligible"])

    def test_tiny_noise_without_breakout_stays_out(self):
        with patch.object(strategy, "_structure", side_effect=self.structures()):
            promoted, _, diag = fast.promote_initial_early(
                self.decision(
                    score=60,
                    move_3m_percent=0.40,
                    move_5m_percent=0.55,
                    breakout_20m=False,
                ),
                "OK",
                df5m=object(),
                df15m=object(),
                df1h=object(),
                current_price=100.0,
                context=self.context(),
            )
        self.assertFalse(diag["promoted"])
        self.assertEqual(diag["reason"], "FAST_NO_BREAKOUT_WEAK_PROGRESS")


if __name__ == "__main__":
    unittest.main()
