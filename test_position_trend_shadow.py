import unittest
from unittest.mock import patch
import pandas as pd

import position_trend_shadow as pts


def synthetic_trend(direction="LONG", n=260):
    if direction == "LONG":
        closes = [100 + i * 0.5 for i in range(n)]
    else:
        closes = [300 - i * 0.5 for i in range(n)]

    rows = []
    for i, close in enumerate(closes):
        if direction == "LONG":
            open_ = close - 0.2
        else:
            open_ = close + 0.2
        rows.append([
            i * 3600000,
            open_,
            close + 0.5,
            close - 0.5,
            close,
            1000 + i,
        ])

    df = pd.DataFrame(
        rows,
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    return pts.add_indicators(df)


class VolumeTests(unittest.TestCase):
    def test_corrected_okx_notional_uses_base_volume_times_last(self):
        ticker = {
            "last": 100000,
            "quoteVolume": 62000,
            "info": {"volCcy24h": "62000"},
        }
        self.assertEqual(
            pts.corrected_quote_notional_24h(ticker),
            6_200_000_000,
        )

    def test_quote_volume_is_fallback(self):
        ticker = {"quoteVolume": 123456}
        self.assertEqual(
            pts.corrected_quote_notional_24h(ticker),
            123456,
        )


class TrendTests(unittest.TestCase):
    def test_long_direction_points_are_strong(self):
        df = synthetic_trend("LONG")
        self.assertGreaterEqual(
            pts.direction_points(df, "LONG"),
            4,
        )

    def test_short_direction_points_are_strong(self):
        df = synthetic_trend("SHORT")
        self.assertGreaterEqual(
            pts.direction_points(df, "SHORT"),
            4,
        )

    def test_qualified_direction_requires_h4_adx(self):
        d1 = synthetic_trend("LONG")
        h4 = synthetic_trend("LONG")
        h4.loc[h4.index[-1], "adx14"] = 25
        self.assertEqual(
            pts.qualified_direction(d1, h4),
            "LONG",
        )


class FundingTests(unittest.TestCase):
    def test_long_extreme_positive_funding_blocks(self):
        blocked, points = pts.funding_effect(
            "LONG",
            pts.FUNDING_BLOCK_ABS,
        )
        self.assertTrue(blocked)
        self.assertEqual(points, 0)

    def test_short_positive_funding_is_not_adverse(self):
        blocked, points = pts.funding_effect(
            "SHORT",
            0.001,
        )
        self.assertFalse(blocked)
        self.assertEqual(points, 5)


class OpenTradeTests(unittest.TestCase):
    def test_open_trade_starts_tracking_after_entry_candle(self):
        state = {"open_trades": {}}
        signal = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "created_at": 1000,
            "entry_candle_ts": 900000,
            "entry": 100.0,
            "stop": 98.0,
            "risk": 2.0,
            "risk_percent": 2.0,
            "tp1": 103.0,
            "tp2": 106.0,
            "tp3": 110.0,
        }
        trade_id = pts.open_shadow_trade(state, signal)
        self.assertEqual(
            state["open_trades"][trade_id]["last_checked_candle_ts"],
            900000,
        )


class TradePathTests(unittest.TestCase):
    def make_long_trade(self):
        return {
            "direction": "LONG",
            "entry": 100.0,
            "stop": 98.0,
            "active_stop": 98.0,
            "risk": 2.0,
            "risk_percent": 2.0,
            "tp1": 103.0,
            "tp2": 106.0,
            "tp3": 110.0,
            "remaining_fraction": 1.0,
            "realized_price_r": 0.0,
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "best_r": 0.0,
            "worst_r": 0.0,
            "closed": False,
        }

    def test_direct_stop_is_minus_one_r(self):
        trade = self.make_long_trade()
        trade = pts.process_bar(
            trade,
            {"high": 101.0, "low": 97.9},
        )
        self.assertTrue(trade["closed"])
        self.assertAlmostEqual(
            trade["realized_price_r"],
            -1.0,
            places=6,
        )

    def test_tp1_then_protect_stop_is_positive(self):
        trade = self.make_long_trade()

        trade = pts.process_bar(
            trade,
            {"high": 103.2, "low": 99.0},
        )
        self.assertTrue(trade["tp1_hit"])
        self.assertAlmostEqual(
            trade["active_stop"],
            99.5,
            places=6,
        )

        trade = pts.process_bar(
            trade,
            {"high": 102.0, "low": 99.4},
        )
        self.assertTrue(trade["closed"])
        self.assertEqual(
            trade["close_reason"],
            "AFTER_TP1_PROTECT",
        )
        self.assertAlmostEqual(
            trade["realized_price_r"],
            0.1875,
            places=6,
        )

    def test_tp2_then_trail_stop_locks_profit(self):
        trade = self.make_long_trade()

        trade = pts.process_bar(
            trade,
            {"high": 103.2, "low": 99.0},
        )
        trade = pts.process_bar(
            trade,
            {"high": 106.2, "low": 100.0},
        )
        self.assertTrue(trade["tp2_hit"])
        self.assertAlmostEqual(
            trade["active_stop"],
            101.0,
            places=6,
        )

        trade = pts.process_bar(
            trade,
            {"high": 105.0, "low": 100.8},
        )
        self.assertTrue(trade["closed"])
        self.assertAlmostEqual(
            trade["realized_price_r"],
            1.375,
            places=6,
        )

    def test_full_tp3_path_is_3_625_r_before_costs(self):
        trade = self.make_long_trade()

        trade = pts.process_bar(
            trade,
            {"high": 103.2, "low": 99.0},
        )
        trade = pts.process_bar(
            trade,
            {"high": 106.2, "low": 100.0},
        )
        trade = pts.process_bar(
            trade,
            {"high": 110.2, "low": 101.5},
        )
        self.assertTrue(trade["closed"])
        self.assertEqual(trade["close_reason"], "TP3")
        self.assertAlmostEqual(
            trade["realized_price_r"],
            3.625,
            places=6,
        )

    def test_ambiguous_bar_is_conservative_stop_first(self):
        trade = self.make_long_trade()
        trade = pts.process_bar(
            trade,
            {"high": 104.0, "low": 97.0},
        )
        self.assertTrue(trade["closed"])
        self.assertEqual(
            trade["close_reason"],
            "AMBIGUOUS_BAR_STOP_FIRST",
        )


class SummaryTests(unittest.TestCase):
    def test_summary_uses_after_cost_net_r(self):
        ledger = {
            "closed_trades": [
                {"net_r_after_costs": 1.0, "price_net_r": 1.1, "close_reason": "TP3", "hold_hours": 20, "funding_exact": True},
                {"net_r_after_costs": -0.5, "price_net_r": -0.4, "close_reason": "INITIAL_SL", "hold_hours": 10, "funding_exact": False},
            ]
        }
        summary = pts.rebuild_summary(ledger)
        self.assertEqual(summary["total_closed"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertAlmostEqual(summary["net_r_after_costs"], 0.5)


class SafetyTests(unittest.TestCase):
    def test_mode_is_shadow_only(self):
        self.assertIn("NO_TELEGRAM", pts.MODE)
        self.assertIn("NO_ORDERS", pts.MODE)

    def test_source_has_no_order_or_telegram_send(self):
        with open(
            "position_trend_shadow.py",
            "r",
            encoding="utf-8",
        ) as handle:
            source = handle.read()
        self.assertNotIn("create_order(", source)
        self.assertNotIn("create_market_order(", source)
        self.assertNotIn("send_telegram(", source)
        self.assertNotIn("requests.post(", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
