import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import system_control_center as scc

class TimestampTests(unittest.TestCase):
    def test_latest_timestamp(self):
        now = int(time.time())
        data = {"last_update": now - 100, "records": {"x": {"opened_at": now - 50, "price": 1786455613.123}}}
        self.assertEqual(scc.extract_latest_timestamp(data), now - 50)

    def test_millisecond_timestamp(self):
        now = int(time.time())
        self.assertEqual(scc.extract_latest_timestamp({"entry_timestamp": (now - 10) * 1000}), now - 10)

class HealthTests(unittest.TestCase):
    def test_green(self):
        now = int(time.time())
        files = [{"path": "x.json", "exists": True, "valid_json": True, "latest_timestamp": now - 60}]
        with patch("system_control_center.Path.exists", return_value=True):
            status, _, age = scc.health_status(files, now - 60, 6.0, "wf.yml")
        self.assertEqual(status, "GREEN")
        self.assertIsNotNone(age)

    def test_red_invalid(self):
        files = [{"path": "x.json", "exists": True, "valid_json": False, "latest_timestamp": 0}]
        self.assertEqual(scc.health_status(files, 0, 6.0, None)[0], "RED")

    def test_yellow_stale(self):
        now = int(time.time())
        files = [{"path": "x.json", "exists": True, "valid_json": True, "latest_timestamp": now - 10 * 3600}]
        self.assertEqual(scc.health_status(files, now - 10 * 3600, 6.0, None)[0], "YELLOW")

class OpenTests(unittest.TestCase):
    def test_unique_open_dedup(self):
        cache = {"s.json": {"a": {"x": {"trade_id": "T1", "closed": False}}, "b": {"x": {"trade_id": "T1", "closed": False}}}}
        self.assertEqual(scc.unique_open_count(cache, [("s.json", "a"), ("s.json", "b")]), 1)

class MetricsTests(unittest.TestCase):
    def test_position_metrics(self):
        state = {"open_trades": {"t1": {"symbol": "LABUSDT", "direction": "SHORT", "closed": False}}}
        ledger = {"summary": {"total_closed": 3, "net_r_after_costs": 2.5}}
        metrics = scc.position_trend_metrics(state, ledger)
        self.assertEqual(metrics["open"], 1)
        self.assertEqual(metrics["closed"], 3)
        self.assertEqual(metrics["net_r_after_costs"], 2.5)

    def test_all_market_metrics(self):
        ledger = {"summary": {"overall": {"total": 2, "open": 0, "closed": 2, "net_r": -2.0}}}
        metrics = scc.all_market_metrics(ledger)
        self.assertEqual(metrics["closed"], 2)
        self.assertEqual(metrics["net_r"], -2.0)

class IntegrationTests(unittest.TestCase):
    def test_read_only_source_files(self):
        now = int(time.time())
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "position_trend_shadow_state.json"
            ledger = Path(td) / "position_trend_shadow_ledger.json"
            state.write_text(json.dumps({"last_run": now, "open_trades": {"x": {"symbol": "LABUSDT", "direction": "SHORT", "opened_at": now - 100}}}), encoding="utf-8")
            ledger.write_text(json.dumps({"last_update": now, "summary": {"total_closed": 0, "net_r_after_costs": 0}}), encoding="utf-8")
            before_state, before_ledger = state.read_bytes(), ledger.read_bytes()

            subset = {
                "POSITION_TREND": {
                    "label": "Ana Trend",
                    "kind": "SHADOW",
                    "files": [state.name, ledger.name],
                    "workflow": None,
                    "stale_hours": 8.0,
                    "open_paths": [(state.name, "open_trades")],
                }
            }
            with patch.object(scc, "COMPONENTS", subset):
                report = scc.build_report(td)

            self.assertEqual(report["components"]["POSITION_TREND"]["health"], "GREEN")
            self.assertEqual(report["components"]["POSITION_TREND"]["metrics"]["open"], 1)
            self.assertEqual(state.read_bytes(), before_state)
            self.assertEqual(ledger.read_bytes(), before_ledger)

class SafetyTests(unittest.TestCase):
    def test_mode(self):
        self.assertIn("NO_ORDERS", scc.MODE)
        self.assertIn("NO_SIGNAL_CHANGE", scc.MODE)
        self.assertIn("NO_AUTO_APPLY", scc.MODE)

    def test_no_trade_or_network_calls(self):
        source = Path("system_control_center.py").read_text(encoding="utf-8")
        for needle in ["create_order(", "create_market_order(", "send_telegram(", "requests.post(", "ccxt."]:
            self.assertNotIn(needle, source)

if __name__ == "__main__":
    unittest.main(verbosity=2)
