import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dashboard_builder import build_dashboard_data, render_dashboard, write_dashboard


class DashboardBuilderTests(unittest.TestCase):
    def write_json(self, root: Path, name: str, data):
        (root / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def make_fixture(self, root: Path):
        self.write_json(root, "open_signals.json", {
            "BTC_SHORT": {"trade_id": "p-open", "symbol": "BTCUSDT", "direction": "SHORT", "entry": 100, "tp1": 98, "tp2": 96, "tp3": 94, "sl": 102, "opened_at": 1000, "tp1_hit": True, "closed": False}
        })
        self.write_json(root, "scalp_radar_state.json", {
            "open_scalp_signals": {"ETH_LONG": {"performance_record_id": "s-open", "symbol": "ETHUSDT", "direction": "LONG", "entry": 10, "tp1": 11, "tp2": 12, "tp3": 13, "sl": 9, "opened_at": 2000, "closed": False}}
        })
        self.write_json(root, "pump_radar_state.json", {"open_pump_signals": {}, "open_signals": {}})
        self.write_json(root, "new_listing_performance_ledger.json", {"records": {}})
        self.write_json(root, "trade_ledger.json", {"trades": {"p-closed": {"trade_id": "p-closed", "symbol": "SOLUSDT", "direction": "LONG", "final_result": "TP3", "r_result": 1.6, "entry": 10, "exit_price": 12, "closed_at": 5000}}})
        self.write_json(root, "scalp_performance_ledger.json", {"records": [{"id": "s-closed", "stage": "REAL_SIGNAL", "symbol": "XRPUSDT", "direction": "SHORT", "trade_outcome": "STOP", "trade_result_r": -1, "trade_closed_at": 4000}]})
        self.write_json(root, "pump_performance_ledger.json", {"records": [{"id": "d-closed", "stage": "REAL_SIGNAL", "symbol": "DOGEUSDT", "direction": "LONG", "trade_outcome": "BREAKEVEN", "trade_result_r": 0, "trade_closed_at": 3000}]})
        self.write_json(root, "system_control_center_report.json", {
            "generated_at": 7000,
            "executive": {"overall_health": "GREEN", "health_counts": {"GREEN": 2, "YELLOW": 0, "RED": 0}},
            "components": {
                "PREMIUM": {"label": "Premium MTF", "kind": "LIVE_SIGNAL", "health": "GREEN", "health_reasons": ["Güncel"], "age_hours": 0.1, "metrics": {"open_count": 1}, "performance": {"decision_tr": "KORU", "sample_size": 40}},
                "SCALP": {"label": "Scalp", "kind": "LIVE_SIGNAL", "health": "GREEN", "health_reasons": ["Güncel"], "age_hours": 0.2, "metrics": {"open_count": 1}, "performance": {"decision_tr": "KORU", "sample_size": 20}},
            },
        })

    def test_collects_real_open_results_and_health(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            data = build_dashboard_data(root, now=datetime(2026, 8, 14, tzinfo=timezone.utc))
            self.assertEqual(data["summary"]["open_total"], 2)
            self.assertEqual(data["summary"]["closed_total"], 3)
            self.assertEqual(data["summary"]["tp"], 1)
            self.assertEqual(data["summary"]["sl"], 1)
            self.assertEqual(data["summary"]["be"], 1)
            self.assertAlmostEqual(data["summary"]["net_r"], 0.6)
            self.assertEqual(data["health"]["overall"], "GREEN")
            self.assertEqual(data["open_trades"][0]["symbol"], "ETHUSDT")
            self.assertEqual(data["performance"]["exact_r_sample"], 3)
            self.assertAlmostEqual(data["performance"]["net_r"], 0.6)
            self.assertAlmostEqual(data["performance"]["max_drawdown_r"], 1.0)
            premium = next(
                row
                for row in data["performance"]["systems"]
                if row["system"] == "PREMIUM"
            )
            self.assertEqual(premium["tp_rate"], 100.0)
            self.assertEqual(len(data["sources"]), 8)

    def test_html_is_self_contained_read_only_and_escapes_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            raw = json.loads((root / "open_signals.json").read_text(encoding="utf-8"))
            raw["BAD"] = {"trade_id": "bad", "symbol": "<img onerror=alert(1)>", "direction": "LONG", "closed": False}
            self.write_json(root, "open_signals.json", raw)
            html = render_dashboard(build_dashboard_data(root))
            self.assertIn("Kripto Kontrol Merkezi", html)
            self.assertIn("Salt okunur", html)
            self.assertIn("const DASHBOARD_DATA", html)
            self.assertNotIn("<img onerror=alert(1)>", html)
            self.assertNotIn("fetch(", html)
            self.assertNotIn("localStorage", html)
            self.assertNotIn("<script src=", html)

    def test_live_html_adds_authenticated_market_chart_only_in_live_mode(self):
        html = render_dashboard(
            None,
            live_endpoint="/api/dashboard",
            market_endpoint="/api/market/candles",
            script_nonce="test-nonce",
        )
        self.assertIn("Canlı Coin Grafiği", html)
        self.assertIn("/api/market/candles", html)
        self.assertIn("OKX herkese açık veri", html)
        self.assertIn("data-market-symbol", html)
        self.assertIn('nonce="test-nonce"', html)

    def test_source_freshness_accepts_iso_and_millisecond_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            current = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
            current_ts = int(current.timestamp())
            premium = json.loads((root / "open_signals.json").read_text(encoding="utf-8"))
            premium["BTC_SHORT"]["last_checked_at"] = "2026-08-14T11:45:00Z"
            self.write_json(root, "open_signals.json", premium)
            report = json.loads((root / "system_control_center_report.json").read_text(encoding="utf-8"))
            report["generated_at"] = current_ts * 1000
            self.write_json(root, "system_control_center_report.json", report)
            data = build_dashboard_data(root, now=current)
            statuses = {row["filename"]: row for row in data["sources"]}
            self.assertEqual(statuses["open_signals.json"]["status"], "FRESH")
            self.assertEqual(statuses["system_control_center_report.json"]["status"], "FRESH")

    def test_missing_or_invalid_files_do_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "open_signals.json").write_text("{bozuk", encoding="utf-8")
            data = build_dashboard_data(root)
            self.assertFalse(data["data_quality"]["ok"])
            self.assertEqual(data["summary"]["open_total"], 0)
            self.assertEqual(data["health"]["overall"], "UNKNOWN")

    def test_writes_dashboard_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            output = root / "nested" / "index.html"
            write_dashboard(root, output)
            self.assertTrue(output.exists())
            self.assertGreater(output.stat().st_size, 10000)


if __name__ == "__main__":
    unittest.main()
