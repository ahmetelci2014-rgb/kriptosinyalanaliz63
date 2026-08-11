#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import unittest

import all_market_shadow as am


def market(symbol, base, active=True, swap=True):
    return {
        "symbol": symbol,
        "base": base,
        "quote": "USDT",
        "settle": "USDT",
        "active": active,
        "swap": swap,
    }


def ticker(volume, last=1.0, percentage=0.0, raw_base_volume=None):
    if raw_base_volume is None:
        raw_base_volume = volume
    return {
        "quoteVolume": volume,
        "baseVolume": raw_base_volume,
        "last": last,
        "percentage": percentage,
        "info": {
            "volCcy24h": str(raw_base_volume),
            "last": str(last),
        },
    }


class AllMarketShadowTests(unittest.TestCase):
    def test_eligible_market_filter(self):
        markets = {
            "A": market("AAA/USDT:USDT", "AAA"),
            "B": market("BBB/USDC:USDC", "BBB"),
            "C": market("USDC/USDT:USDT", "USDC"),
            "D": market("DDD/USDT:USDT", "DDD", active=False),
        }
        rows = am.eligible_markets(markets)
        self.assertEqual([r["symbol"] for r in rows], ["AAAUSDT"])

    def test_premium_universe_matches_priority_then_volume(self):
        markets = {
            "A": market("AAA/USDT:USDT", "AAA"),
            "B": market("BBB/USDT:USDT", "BBB"),
            "C": market("CCC/USDT:USDT", "CCC"),
            "D": market("DDD/USDT:USDT", "DDD"),
        }
        tickers = {
            "AAA/USDT:USDT": ticker(900_000),
            "BBB/USDT:USDT": ticker(2_000_000),
            "CCC/USDT:USDT": ticker(1_500_000),
            "DDD/USDT:USDT": ticker(100_000),
        }
        result = am.build_universe(
            markets, tickers,
            priority_coins=["AAAUSDT"],
            min_quote_volume=500_000,
            max_scan_coins=2,
        )
        self.assertEqual(result["premium_symbols"], ["AAAUSDT", "BBBUSDT"])
        outside = {r["symbol"]: r for r in result["outside"]}
        self.assertEqual(outside["CCCUSDT"]["outside_reason"], "OUTSIDE_PREMIUM_TOP300_LEGACY")
        self.assertEqual(outside["DDDUSDT"]["outside_reason"], "BELOW_PREMIUM_MIN_VOLUME_LEGACY")


    def test_corrected_notional_uses_okx_base_volume_times_last(self):
        item = ticker(
            volume=62_000,
            last=100_000,
            raw_base_volume=62_000,
        )
        self.assertEqual(am.legacy_premium_volume(item), 62_000)
        self.assertEqual(
            am.corrected_quote_notional_24h(item),
            6_200_000_000,
        )

    def test_audit_flags_live_outside_but_corrected_top300(self):
        markets = {
            "BTC": market("BTC/USDT:USDT", "BTC"),
            "ALT": market("ALT/USDT:USDT", "ALT"),
        }
        tickers = {
            # Legacy 62k < 500k, fakat yaklaşık notional 6.2 milyar USDT.
            "BTC/USDT:USDT": ticker(
                62_000, last=100_000, raw_base_volume=62_000
            ),
            # Legacy ve corrected ikisi de yüksek.
            "ALT/USDT:USDT": ticker(
                900_000, last=1.0, raw_base_volume=900_000
            ),
        }
        result = am.build_universe(
            markets, tickers,
            priority_coins=[],
            min_quote_volume=500_000,
            max_scan_coins=1,
        )
        outside = {r["symbol"]: r for r in result["outside"]}
        self.assertIn("BTCUSDT", outside)
        self.assertEqual(
            outside["BTCUSDT"]["volume_audit_class"],
            "LIVE_OUTSIDE_BUT_CORRECTED_TOP300",
        )
        self.assertTrue(outside["BTCUSDT"]["corrected_in_top300"])

    def test_rotation_never_exceeds_limit(self):
        rows = [
            {
                "symbol": f"C{i:03d}USDT",
                "quote_volume_24h": i * 1000,
                "change_24h_percent": i % 7,
            }
            for i in range(120)
        ]
        chosen, cursor = am.select_deep_scan(rows, 0, max_per_run=20, hot_count=5)
        self.assertEqual(len(chosen), 20)
        self.assertGreaterEqual(cursor, 0)
        self.assertEqual(len({r["symbol"] for r in chosen}), 20)

    def test_small_outside_scans_all(self):
        rows = [
            {"symbol": f"C{i}USDT", "quote_volume_24h": i, "change_24h_percent": 0}
            for i in range(5)
        ]
        chosen, cursor = am.select_deep_scan(rows, 4, max_per_run=20, hot_count=5)
        self.assertEqual(len(chosen), 5)
        self.assertEqual(cursor, 0)

    def test_entry_valid_long(self):
        sig = {
            "direction": "LONG",
            "entry": 100,
            "tp1": 101,
            "sl": 99,
        }
        ok, reason = am.entry_still_valid(sig, 100.1, 0.35, 45)
        self.assertTrue(ok)
        self.assertEqual(reason, "OK")

    def test_entry_rejects_late(self):
        sig = {
            "direction": "LONG",
            "entry": 100,
            "tp1": 101,
            "sl": 99,
        }
        ok, reason = am.entry_still_valid(sig, 100.6, 0.35, 45)
        self.assertFalse(ok)

    def test_direct_stop_is_minus_one_r(self):
        trade = {
            "status": "OPEN",
            "direction": "LONG",
            "entry": 100.0,
            "tp1": 101.0,
            "tp2": 102.0,
            "tp3": 103.0,
            "sl": 99.0,
            "opened_at": 1000,
            "last_checked_at": 1000,
            "events": [],
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "best_favorable_r": 0,
            "worst_adverse_r": 0,
        }
        candles = [{
            "time": 1080,
            "open": 100,
            "high": 100.2,
            "low": 98.9,
            "close": 99.0,
        }]
        am.process_trade_candles(trade, candles, 1200)
        self.assertEqual(trade["final_result"], "SL")
        self.assertEqual(trade["r_result"], -1.0)

    def test_tp1_then_be_r_matches_premium_management(self):
        trade = {
            "status": "OPEN",
            "direction": "LONG",
            "entry": 100.0,
            "tp1": 100.55,
            "tp2": 101.05,
            "tp3": 101.60,
            "sl": 99.0,
            "opened_at": 1000,
            "last_checked_at": 1000,
            "events": [],
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "best_favorable_r": 0,
            "worst_adverse_r": 0,
        }
        candles = [
            {"time": 1080, "open": 100, "high": 100.7, "low": 99.9, "close": 100.6},
            {"time": 1140, "open": 100.6, "high": 100.7, "low": 99.95, "close": 100.0},
        ]
        am.process_trade_candles(trade, candles, 1300)
        self.assertEqual(trade["final_result"], "TP1_SONRASI_BE")
        self.assertAlmostEqual(trade["r_result"], 0.275, places=4)

    def test_tp3_r_matches_premium_management(self):
        trade = {
            "status": "OPEN",
            "direction": "SHORT",
            "entry": 100.0,
            "tp1": 99.45,
            "tp2": 98.95,
            "tp3": 98.40,
            "sl": 101.0,
            "opened_at": 1000,
            "last_checked_at": 1000,
            "events": [],
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "best_favorable_r": 0,
            "worst_adverse_r": 0,
        }
        candles = [{
            "time": 1080,
            "open": 100,
            "high": 100.1,
            "low": 98.3,
            "close": 98.5,
        }]
        am.process_trade_candles(trade, candles, 1200)
        self.assertEqual(trade["final_result"], "TP3")
        self.assertAlmostEqual(trade["r_result"], 1.075, places=4)

    def test_same_candle_tp1_and_sl_uses_close_direction(self):
        trade = {
            "status": "OPEN",
            "direction": "LONG",
            "entry": 100.0,
            "tp1": 100.55,
            "tp2": 101.05,
            "tp3": 101.60,
            "sl": 99.0,
            "opened_at": 1000,
            "last_checked_at": 1000,
            "events": [],
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
            "best_favorable_r": 0,
            "worst_adverse_r": 0,
        }
        candles = [{
            "time": 1080,
            "open": 100,
            "high": 100.7,
            "low": 98.9,
            "close": 99.5,
        }]
        am.process_trade_candles(trade, candles, 1200)
        self.assertEqual(trade["final_result"], "SL")

    def test_same_symbol_open_is_blocked(self):
        ledger = {
            "trades": {
                "x": {
                    "symbol": "AAAUSDT",
                    "direction": "LONG",
                    "status": "OPEN",
                    "opened_at": 1000,
                }
            }
        }
        ok, reason = am.can_open_shadow(ledger, "AAAUSDT", "SHORT", 2000)
        self.assertFalse(ok)
        self.assertEqual(reason, "SAME_SYMBOL_OPEN")

    def test_summary_net_r(self):
        ledger = {
            "trades": {
                "a": {
                    "status": "CLOSED", "final_result": "SL", "r_result": -1.0,
                    "symbol": "A", "direction": "LONG", "source": "15M_ENTRY",
                    "outside_reason": "BELOW_PREMIUM_MIN_VOLUME_LEGACY",
                    "quote_volume_24h_at_open": 100_000,
                    "volume_rank_all_eligible_at_open": 401,
                    "opened_day": "2026-08-11",
                    "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
                },
                "b": {
                    "status": "CLOSED", "final_result": "TP3", "r_result": 1.075,
                    "symbol": "B", "direction": "SHORT", "source": "15M_ENTRY",
                    "outside_reason": "OUTSIDE_PREMIUM_TOP300_LEGACY",
                    "quote_volume_24h_at_open": 800_000,
                    "volume_rank_all_eligible_at_open": 330,
                    "opened_day": "2026-08-11",
                    "tp1_hit": True, "tp2_hit": True, "tp3_hit": True,
                },
            }
        }
        summary = am.build_summary(ledger)
        self.assertAlmostEqual(summary["overall"]["net_r"], 0.075, places=4)
        self.assertEqual(summary["overall"]["closed"], 2)

    def test_no_telegram_or_order_api_in_module_contract(self):
        self.assertIn("NO_TELEGRAM", am.MODE)
        self.assertIn("NO_ORDERS", am.MODE)


if __name__ == "__main__":
    unittest.main()
