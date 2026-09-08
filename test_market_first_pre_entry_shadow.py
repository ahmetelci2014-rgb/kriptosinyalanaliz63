import unittest

import pandas as pd

import market_first_pre_entry_shadow as shadow


class MarketFirstPreEntryShadowTests(unittest.TestCase):
    def plan(self, **updates):
        row = {
            "status": "PREP",
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "score": 84,
            "current_price": 100.0,
            "sl": 99.0,
            "zone_low": 99.8,
            "zone_high": 100.1,
            "zone_distance_percent": 0.10,
            "structure_5m": "NEUTRAL",
            "structure_15m": "LONG",
            "structure_1h": "LONG",
            "volume_ratio_5m": 0.40,
            "volume_ratio_15m": 0.90,
            "extension_atr_5m": 0.50,
            "risk_percent": 1.0,
            "room_r": 2.2,
            "market_regime": "CHOP",
        }
        row.update(updates)
        return row

    def bars(self, rows):
        # Last row is forming and must not be used by shadow result tracking.
        data = list(rows) + [{"open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": 1.0}]
        return pd.DataFrame(data)

    def test_neutral_5m_lowish_volume_near_zone_qualifies(self):
        ok, reason = shadow.qualifies(self.plan())
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")
        self.assertIn("5M_NEUTRAL", shadow.profile_reason(self.plan()))
        self.assertIn("VOLUME_EARLY", shadow.profile_reason(self.plan()))

    def test_quality_floor_rejects_weak_preparation(self):
        ok, reason = shadow.qualifies(self.plan(score=74))
        self.assertFalse(ok)
        self.assertEqual(reason, "SCORE")
        ok, reason = shadow.qualifies(self.plan(volume_ratio_5m=0.20))
        self.assertFalse(ok)
        self.assertEqual(reason, "VOLUME")
        ok, reason = shadow.qualifies(self.plan(zone_distance_percent=0.40))
        self.assertFalse(ok)
        self.assertEqual(reason, "ZONE_DISTANCE")

    def test_build_trade_reanchors_targets_to_actual_shadow_entry(self):
        trade, reason = shadow.build_trade(self.plan(current_price=100.0, sl=99.0), 1000)
        self.assertEqual(reason, "OK")
        self.assertIsNotNone(trade)
        self.assertAlmostEqual(trade["tp1"], 100.75)
        self.assertAlmostEqual(trade["tp2"], 101.25)
        self.assertAlmostEqual(trade["tp3"], 102.0)
        self.assertAlmostEqual(trade["cost_r"], 0.14, places=4)

    def test_long_tp1_first_closes_positive_net_r(self):
        trade, _ = shadow.build_trade(self.plan(), 1000)
        frame = self.bars([
            {"open": 100.0, "high": 100.80, "low": 99.60, "close": 100.70, "volume": 1.0},
        ])
        result = shadow.track_virtual(trade, frame, 100.70, 1300)
        self.assertEqual(result, "TP1_FIRST")
        self.assertEqual(trade["first_result"], "TP1_FIRST")
        self.assertAlmostEqual(trade["gross_r"], 0.75)
        self.assertGreater(trade["net_r"], 0)

    def test_same_bar_tp_and_sl_is_counted_as_sl_first(self):
        trade, _ = shadow.build_trade(self.plan(), 1000)
        frame = self.bars([
            {"open": 100.0, "high": 101.0, "low": 98.8, "close": 100.4, "volume": 1.0},
        ])
        result = shadow.track_virtual(trade, frame, 100.4, 1300)
        self.assertEqual(result, "SL_FIRST")
        self.assertEqual(trade["first_result"], "SL_FIRST")
        self.assertLess(trade["net_r"], -1.0)

    def test_short_side_tracking_is_symmetric(self):
        plan = self.plan(
            direction="SHORT",
            structure_5m="NEUTRAL",
            structure_15m="SHORT",
            structure_1h="SHORT",
            current_price=100.0,
            sl=101.0,
        )
        trade, _ = shadow.build_trade(plan, 1000)
        frame = self.bars([
            {"open": 100.0, "high": 100.2, "low": 99.20, "close": 99.3, "volume": 1.0},
        ])
        result = shadow.track_virtual(trade, frame, 99.3, 1300)
        self.assertEqual(result, "TP1_FIRST")

    def test_summary_reports_tp1_first_rate_and_profile_net_r(self):
        a, _ = shadow.build_trade(self.plan(symbol="AUSDT"), 1000)
        b, _ = shadow.build_trade(self.plan(symbol="BUSDT"), 2000)
        a.update({"status": "CLOSED", "first_result": "TP1_FIRST", "gross_r": 0.75, "net_r": 0.61, "closed_at": 1300})
        b.update({"status": "CLOSED", "first_result": "SL_FIRST", "gross_r": -1.0, "net_r": -1.14, "closed_at": 2300})
        ledger = {"trades": {a["trade_id"]: a, b["trade_id"]: b}}
        summary = shadow.build_summary(ledger)
        self.assertEqual(summary["decided_tp1_vs_sl"], 2)
        self.assertEqual(summary["tp1_first"], 1)
        self.assertEqual(summary["sl_first"], 1)
        self.assertAlmostEqual(summary["tp1_first_rate"], 0.5)
        self.assertAlmostEqual(summary["estimated_net_r"], -0.53)


if __name__ == "__main__":
    unittest.main()
