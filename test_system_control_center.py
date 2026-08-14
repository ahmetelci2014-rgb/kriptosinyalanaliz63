import json
import os
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

class FutureTimestampTests(unittest.TestCase):
    def test_funding_next_timestamp_is_not_activity(self):
        now = int(time.time())
        data = {
            "last_run": now - 120,
            "funding_next_timestamp": (now + 6 * 3600) * 1000,
        }
        self.assertEqual(
            scc.extract_latest_timestamp(data),
            now - 120,
        )

    def test_generic_far_future_timestamp_is_rejected(self):
        now = int(time.time())
        data = {
            "last_update": now - 60,
            "some_timestamp": now + 2 * 3600,
        }
        self.assertEqual(
            scc.extract_latest_timestamp(data),
            now - 60,
        )

    def test_health_age_never_negative_for_small_clock_skew(self):
        now = int(time.time())
        future = now + 60
        files = [{
            "path": "x.json",
            "exists": True,
            "valid_json": True,
            "latest_timestamp": future,
        }]
        status, _, age = scc.health_status(
            files,
            future,
            6.0,
            None,
        )
        self.assertEqual(status, "GREEN")
        self.assertEqual(age, 0.0)

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

    def test_yellow_large_file(self):
        now = int(time.time())
        files = [{
            "path": "large.json",
            "exists": True,
            "valid_json": True,
            "bytes": scc.FILE_SIZE_YELLOW_BYTES,
            "latest_timestamp": now - 60,
        }]
        status, reasons, _ = scc.health_status(files, now - 60, 6.0, None)
        self.assertEqual(status, "YELLOW")
        self.assertTrue(any("büyüme" in reason for reason in reasons))

    def test_red_critical_file_size(self):
        now = int(time.time())
        files = [{
            "path": "critical.json",
            "exists": True,
            "valid_json": True,
            "bytes": scc.FILE_SIZE_RED_BYTES,
            "latest_timestamp": now - 60,
        }]
        status, reasons, _ = scc.health_status(files, now - 60, 6.0, None)
        self.assertEqual(status, "RED")
        self.assertTrue(any("Kritik dosya" in reason for reason in reasons))

    def test_yellow_stale(self):
        now = int(time.time())
        files = [{"path": "x.json", "exists": True, "valid_json": True, "latest_timestamp": now - 10 * 3600}]
        self.assertEqual(scc.health_status(files, now - 10 * 3600, 6.0, None)[0], "YELLOW")

class JsonStorageTests(unittest.TestCase):
    def test_global_storage_guard_detects_unreferenced_invalid_json(self):
        with tempfile.TemporaryDirectory() as td:
            original = os.getcwd()
            os.chdir(td)
            try:
                Path("valid.json").write_text('{"ok": true}', encoding="utf-8")
                Path("broken.json").write_text('{"ok":', encoding="utf-8")
                result = scc.json_storage_component()
            finally:
                os.chdir(original)

        self.assertEqual(result["health"], "RED")
        self.assertEqual(result["metrics"]["file_count"], 2)
        self.assertTrue(any("broken.json" in reason for reason in result["health_reasons"]))


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

class CriticalAlertTests(unittest.TestCase):
    def red_report(self):
        return {
            "generated_at_utc": "2026-08-14T00:00:00+00:00",
            "executive": {
                "overall_health": "RED",
                "critical_components": ["PREMIUM"],
            },
            "components": {
                "PREMIUM": {
                    "label": "Premium MTF",
                    "health": "RED",
                    "health_reasons": ["Bozuk JSON: trade_ledger.json"],
                }
            },
        }

    def test_red_alert_is_sent_once_then_deduplicated(self):
        calls = []

        def sender(message, token, chat_id):
            calls.append((message, token, chat_id))
            return True

        with tempfile.TemporaryDirectory() as td:
            state_file = str(Path(td) / "alert.json")
            first = scc.maybe_send_critical_alert(
                self.red_report(),
                state_file=state_file,
                token="test-token",
                chat_id="test-chat",
                current_ts=1_800_000_000,
                sender=sender,
            )
            second = scc.maybe_send_critical_alert(
                self.red_report(),
                state_file=state_file,
                token="test-token",
                chat_id="test-chat",
                current_ts=1_800_000_100,
                sender=sender,
            )

        self.assertTrue(first["sent"])
        self.assertEqual(second["reason"], "COOLDOWN")
        self.assertEqual(len(calls), 1)

    def test_non_red_report_never_sends(self):
        report = self.red_report()
        report["executive"]["overall_health"] = "YELLOW"
        sent = scc.maybe_send_critical_alert(
            report,
            token="test-token",
            chat_id="test-chat",
            sender=lambda *_: self.fail("sender çağrılmamalı"),
        )
        self.assertEqual(sent["reason"], "NOT_RED")


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
