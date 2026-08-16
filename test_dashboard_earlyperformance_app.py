from __future__ import annotations

import unittest

import dashboard_earlyperformance_app as perf


class FakeMarketClient:
    def __init__(self):
        self.calls = []

    def get_candles(self, symbol, bar, anchor):
        self.calls.append((symbol, bar, anchor))
        return {
            "source": "TEST",
            "candles": [
                {"ts": anchor - 20, "high": 101.0, "low": 99.0, "close": 100.5},
                {"ts": anchor + 40, "high": 103.2, "low": 100.0, "close": 102.8},
            ],
        }


def row(system="PREMIUM", trade_id="1", opened=1_700_000_000):
    return {
        "id": trade_id,
        "system": system,
        "system_label": perf.SYSTEM_LABELS.get(system, system),
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry": 100.0,
        "sl": 98.0,
        "tp1": 103.0,
        "outcome": "TP1",
        "opened_at": opened,
        "closed_at": opened + 1200,
    }


class EarlyPerformanceSelectionTests(unittest.TestCase):
    def test_selection_balances_systems_and_caps_each_system(self):
        rows = []
        for system in perf.SYSTEM_ORDER:
            for index in range(5):
                rows.append(row(system, f"{system}-{index}", 1_700_000_000 - index))
        selected = perf.select_history_rows({"recent_results": rows})
        self.assertEqual(len(selected), 12)
        for system in perf.SYSTEM_ORDER:
            self.assertEqual(sum(1 for item in selected if item["system"] == system), 3)

    def test_invalid_missing_risk_row_is_skipped(self):
        bad = row()
        bad["sl"] = None
        self.assertEqual(perf.select_history_rows({"recent_results": [bad]}), [])


class EarlyPerformanceSummaryTests(unittest.TestCase):
    def test_summary_rates_and_average_r(self):
        rows = [
            {"status":"ok","first_event":"TP1_FIRST","mfe_r":1.5,"mae_r":-0.3,"window_close_r":0.8,"data_quality":"COMPLETE"},
            {"status":"ok","first_event":"SL_FIRST","mfe_r":0.4,"mae_r":-1.1,"window_close_r":-0.5,"data_quality":"PARTIAL"},
            {"status":"ok","first_event":"NONE","mfe_r":0.7,"mae_r":-0.2,"window_close_r":0.2,"data_quality":"COMPLETE"},
        ]
        out = perf.summarize_pulses(rows)
        self.assertEqual(out["sample"], 3)
        self.assertEqual(out["tp1_first"], 1)
        self.assertEqual(out["sl_first"], 1)
        self.assertAlmostEqual(out["tp1_first_rate"], 33.3)
        self.assertAlmostEqual(out["positive_close_rate"], 66.7)
        self.assertAlmostEqual(out["average_mfe_r"], 0.8667)
        self.assertAlmostEqual(out["average_mae_r"], -0.5333)
        self.assertFalse(out["insufficient_sample"])

    def test_history_payload_uses_cache(self):
        client = FakeMarketClient()
        cache = perf.HistoricalPulseCache(ttl_seconds=3600)
        data = {"recent_results": [row()]}
        first = perf.build_history_payload(data, client, cache)
        self.assertEqual(first["overall"]["sample"], 1)
        self.assertEqual(len(client.calls), 1)
        second = perf.build_history_payload(data, client, cache)
        self.assertEqual(second["overall"]["sample"], 1)
        self.assertEqual(len(client.calls), 1)


class EarlyPerformancePageTests(unittest.TestCase):
    def test_page_is_read_only_and_has_api_contract(self):
        body = perf.page("nonce318")
        self.assertIn("İlk 15 Dakika Performans Analizi", body)
        self.assertIn("/api/early-performance", body)
        self.assertIn('nonce="nonce318"', body)
        self.assertNotIn("method:'POST'", body)
        self.assertNotIn('method="post"', body.lower())

    def test_navigation_injection_is_idempotent(self):
        body = '<a href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a><button class="fav" id="favBtn">x</button>'
        once = perf.enhance_navigation(body)
        twice = perf.enhance_navigation(once)
        self.assertEqual(once, twice)
        self.assertGreaterEqual(once.count('href="/early-performance"'), 1)


class EarlyPerformanceSourceBoundaryTests(unittest.TestCase):
    def test_version_and_core_boundary(self):
        self.assertIn("V3_18", perf.VERSION)
        source = open(perf.__file__, encoding="utf-8").read()
        self.assertIn('"signal_engine":"unchanged"', source)
        self.assertIn('"telegram":"unchanged"', source)
        self.assertIn('"trade_management":"unchanged"', source)
        self.assertIn('"ledger_write":"unchanged"', source)
        self.assertNotIn("strategy.py", source)


if __name__ == "__main__":
    unittest.main()
