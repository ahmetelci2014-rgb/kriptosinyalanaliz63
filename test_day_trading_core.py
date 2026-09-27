import unittest

import day_trading_core as d


def sample_trade(direction="LONG"):
    entry = 100.0
    risk = 1.0
    sign = 1 if direction == "LONG" else -1
    return {
        "trade_id": f"T_{direction}",
        "symbol": "TESTUSDT",
        "ccxt_symbol": "TEST/USDT:USDT",
        "direction": direction,
        "entry": entry,
        "initial_stop": entry - sign * risk,
        "current_stop": entry - sign * risk,
        "risk_abs": risk,
        "tp1": entry + sign * risk * d.TP1_R,
        "tp2": entry + sign * risk * d.TP2_R,
        "tp3": entry + sign * risk * d.TP3_R,
        "stage": 0,
        "remaining": 1.0,
        "realized_r": 0.0,
        "stop_r": -1.0,
        "status": "OPEN",
        "opened_at": 1,
        "opened_day": "2026-09-27",
        "events": [],
    }


class DayTradingCoreTests(unittest.TestCase):
    def test_full_stop_is_minus_one_r_before_cost(self):
        state = d._default_state()
        ledger = d._default_ledger()
        trade = sample_trade("LONG")
        state["open_trades"][trade["trade_id"]] = trade
        closed = d._apply_bar(
            state,
            ledger,
            trade,
            {"ts": 1_000_000, "high": 100.2, "low": 98.9},
        )
        self.assertTrue(closed)
        row = ledger["trades"][trade["trade_id"]]
        self.assertEqual(row["final_result"], "SL")
        self.assertAlmostEqual(row["gross_r"], -1.0, places=6)
        self.assertAlmostEqual(row["net_r"], -1.0 - d.ESTIMATED_COST_R, places=6)

    def test_tp1_then_protected_stop_keeps_positive_r(self):
        state = d._default_state()
        ledger = d._default_ledger()
        trade = sample_trade("LONG")
        state["open_trades"][trade["trade_id"]] = trade

        closed = d._apply_bar(
            state,
            ledger,
            trade,
            {"ts": 1_000_000, "high": 101.3, "low": 100.0},
        )
        self.assertFalse(closed)
        self.assertEqual(trade["stage"], 1)
        self.assertAlmostEqual(trade["current_stop"], 100.1, places=6)

        closed = d._apply_bar(
            state,
            ledger,
            trade,
            {"ts": 1_300_000, "high": 100.5, "low": 100.05},
        )
        self.assertTrue(closed)
        row = ledger["trades"][trade["trade_id"]]
        self.assertEqual(row["final_result"], "PROTECTED_AFTER_TP1")
        self.assertAlmostEqual(row["gross_r"], 0.485, places=6)
        self.assertGreater(row["net_r"], 0)

    def test_tp3_path_returns_two_point_zero_two_r_before_cost(self):
        state = d._default_state()
        ledger = d._default_ledger()
        trade = sample_trade("LONG")
        state["open_trades"][trade["trade_id"]] = trade

        self.assertFalse(d._apply_bar(state, ledger, trade, {"ts": 1_000_000, "high": 101.3, "low": 100.2}))
        self.assertFalse(d._apply_bar(state, ledger, trade, {"ts": 1_300_000, "high": 102.1, "low": 100.2}))
        self.assertTrue(d._apply_bar(state, ledger, trade, {"ts": 1_600_000, "high": 103.1, "low": 101.2}))

        row = ledger["trades"][trade["trade_id"]]
        self.assertEqual(row["final_result"], "TP3")
        self.assertAlmostEqual(row["gross_r"], 2.02, places=6)

    def test_daily_two_full_stops_blocks_new_trades(self):
        state = d._default_state()
        day = d._day_row(state)
        day["full_stops"] = d.MAX_FULL_STOPS_PER_DAY
        ok, reason = d._can_open(state, "LONG")
        self.assertFalse(ok)
        self.assertEqual(reason, "DAILY_STOP_LIMIT")

    def test_same_direction_exposure_is_limited(self):
        state = d._default_state()
        trade = sample_trade("SHORT")
        state["open_trades"][trade["trade_id"]] = trade
        ok, reason = d._can_open(state, "SHORT")
        self.assertFalse(ok)
        self.assertEqual(reason, "MAX_SAME_DIRECTION")


if __name__ == "__main__":
    unittest.main()
