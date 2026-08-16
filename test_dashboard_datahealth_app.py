import inspect
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import dashboard_datahealth_app as datahealth
import dashboard_stable_app as stable


class DataHeartbeatTests(unittest.TestCase):
    def write_json(self, root: Path, name: str, data):
        (root / name).write_text(json.dumps(data), encoding="utf-8")

    def test_scalp_uses_ledger_heartbeat_even_without_new_signal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 8, 16, 13, 0, tzinfo=timezone.utc)
            now_ts = int(now.timestamp())
            self.write_json(root, "scalp_radar_state.json", {"last_sent": {"OLD": now_ts - 7200}})
            self.write_json(root, "scalp_performance_ledger.json", {"updated_at": now_ts - 300})
            result = datahealth.build_system_freshness(root, now)
            scalp = next(row for row in result["rows"] if row["key"] == "SCALP")
            self.assertEqual(scalp["status"], "FRESH")
            self.assertEqual(scalp["age_minutes"], 5.0)

    def test_pump_shadow_time_cannot_fake_live_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 8, 16, 13, 0, tzinfo=timezone.utc)
            now_ts = int(now.timestamp())
            self.write_json(root, "pump_performance_ledger.json", {"updated_at": now_ts - 7200})
            self.write_json(root, "pump_radar_state.json", {
                "last_run": now_ts - 7200,
                "shadow_moves": [{"recorded_at": now_ts - 30}],
            })
            result = datahealth.build_system_freshness(root, now)
            pump = next(row for row in result["rows"] if row["key"] == "PUMP_DUMP")
            self.assertEqual(pump["status"], "STALE")
            self.assertGreater(pump["age_minutes"], 45)

    def test_system_control_has_hourly_tolerance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 8, 16, 13, 0, tzinfo=timezone.utc)
            now_ts = int(now.timestamp())
            self.write_json(root, "system_control_center_report.json", {"generated_at": now_ts - 70 * 60})
            result = datahealth.build_system_freshness(root, now)
            control = next(row for row in result["rows"] if row["key"] == "SYSTEM_CONTROL")
            self.assertEqual(control["status"], "FRESH")

    def test_v326_keeps_v325_member_and_signal_layers(self):
        html = '<!doctype html><html><head><style></style></head><body><div class="summary" id="homeMetrics"></div></body></html>'
        body = stable.enhance_admin_product_view(html, "nonce-test")
        body = datahealth.enhance_data_health(body, "nonce-test")
        self.assertIn('id="v323MemberFocus"', body)
        self.assertIn('id="v324SignalGuide"', body)
        self.assertIn('id="v326DataHealth"', body)
        self.assertEqual(body.count('id="v326DataHealth"'), 1)
        self.assertEqual(datahealth.enhance_data_health(body, "nonce-test"), body)

    def test_source_contract_is_read_only(self):
        source = inspect.getsource(datahealth)
        self.assertNotIn("def do_POST", source)
        self.assertIn("stable.make_v325_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)
        self.assertIn("shadow_moves / recorded_at okunmaz", source)


if __name__ == "__main__":
    unittest.main()
