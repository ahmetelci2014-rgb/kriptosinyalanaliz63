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
        self.write_json(root, "trade_ledger.json", {"trades": {"p-closed": {"trade_id": "p-closed", "symbol": "SOLUSDT", "direction": "LONG", "setup": "MTF", "final_result": "TP3", "r_result": 1.6, "entry": 10, "tp1": 10.5, "tp2": 11, "tp3": 12, "sl": 9, "exit_price": 12, "opened_at": 4500, "closed_at": 5000}}})
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
            self.assertEqual(data["open_risk"]["long"], 1)
            self.assertEqual(data["open_risk"]["short"], 1)
            self.assertAlmostEqual(data["open_risk"]["average_stop_percent"], 6.0)
            self.assertAlmostEqual(data["open_risk"]["average_tp1_rr"], 1.0)
            self.assertAlmostEqual(data["open_risk"]["average_tp3_rr"], 3.0)
            self.assertEqual(data["open_risk"]["widest_stop_symbol"], "ETHUSDT")
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
            self.assertEqual(data["performance_windows"]["ALL"]["exact_r_sample"], 3)
            self.assertEqual(data["performance_windows"]["7D"]["exact_r_sample"], 0)
            directions = {
                row["direction"]: row
                for row in data["result_breakdown"]["directions"]
            }
            self.assertEqual(directions["LONG"]["sample"], 2)
            self.assertAlmostEqual(directions["LONG"]["net_r"], 1.6)
            self.assertAlmostEqual(directions["LONG"]["average_r"], 0.8)
            self.assertEqual(directions["SHORT"]["sample"], 1)
            self.assertAlmostEqual(directions["SHORT"]["net_r"], -1.0)
            self.assertEqual(
                data["result_breakdown"]["recent_sequence"],
                {"type": "TP", "count": 1},
            )
            self.assertEqual(len(data["result_breakdown"]["daily_30d"]), 30)
            self.assertEqual(len(data["period_comparisons"]["7D"]["rows"]), 5)
            self.assertEqual(len(data["period_comparisons"]["30D"]["rows"]), 5)
            closed = next(row for row in data["recent_results"] if row["id"] == "p-closed")
            self.assertEqual(closed["tp3"], 12.0)
            self.assertEqual(closed["sl"], 9.0)
            self.assertEqual(closed["source"], "MTF")

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
            self.assertIn("Açık Risk Özeti", html)
            self.assertIn("Yön ve Gün Analizi", html)
            self.assertIn("dailyCanvas", html)
            self.assertIn('class="quick-nav"', html)
            self.assertIn("Dönem Karşılaştırması", html)
            self.assertIn("comparisonWindow", html)
            self.assertIn("CSV indir", html)
            self.assertIn("exportFilteredResults", html)

    def test_live_html_adds_authenticated_market_chart_only_in_live_mode(self):
        html = render_dashboard(
            None,
            live_endpoint="/api/dashboard",
            market_endpoint="/api/market/candles",
            script_nonce="test-nonce",
        )
        self.assertIn("Coin ve İşlem Grafiği", html)
        self.assertIn("/api/market/candles", html)
        self.assertIn("OKX herkese açık veri", html)
        self.assertIn("data-market-symbol", html)
        self.assertIn("İşlem İnceleme Merkezi", html)
        self.assertIn("performanceWindow", html)
        self.assertIn("resultOutcome", html)
        self.assertIn("resultPagination", html)
        self.assertIn("20 / sayfa", html)
        self.assertIn("Açık Risk Özeti", html)
        self.assertIn("Yön ve Gün Analizi", html)
        self.assertIn("Coin Grafiği", html)
        self.assertIn("renderDirection", html)
        self.assertIn("Dönem Karşılaştırması", html)
        self.assertIn("renderComparison", html)
        self.assertIn("CSV indir", html)
        self.assertIn('nonce="test-nonce"', html)

    def test_daily_result_breakdown_uses_turkey_day_and_exact_r_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            current = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
            current_ts = int(current.timestamp())
            premium = json.loads((root / "trade_ledger.json").read_text(encoding="utf-8"))
            premium["trades"]["p-closed"]["closed_at"] = current_ts - 3600
            self.write_json(root, "trade_ledger.json", premium)
            scalp = json.loads((root / "scalp_performance_ledger.json").read_text(encoding="utf-8"))
            scalp["records"][0]["trade_closed_at"] = current_ts - 7200
            self.write_json(root, "scalp_performance_ledger.json", scalp)
            pump = json.loads((root / "pump_performance_ledger.json").read_text(encoding="utf-8"))
            pump["records"][0]["trade_closed_at"] = current_ts - 10800
            self.write_json(root, "pump_performance_ledger.json", pump)

            breakdown = build_dashboard_data(root, now=current)["result_breakdown"]
            today = breakdown["daily_30d"][-1]
            self.assertEqual(today["date"], "2026-08-14")
            self.assertEqual(today["count"], 3)
            self.assertEqual(today["exact_r_sample"], 3)
            self.assertAlmostEqual(today["net_r"], 0.6)
            self.assertEqual(breakdown["recent_sequence"], {"type": "TP", "count": 1})
            self.assertEqual(breakdown["best_day"]["date"], "2026-08-14")

    def test_period_comparison_separates_current_and_previous_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            current = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
            current_ts = int(current.timestamp())

            premium = json.loads((root / "trade_ledger.json").read_text(encoding="utf-8"))
            premium["trades"]["p-closed"]["closed_at"] = current_ts - 86400
            premium["trades"]["p-previous"] = {
                "trade_id": "p-previous",
                "symbol": "ADAUSDT",
                "direction": "LONG",
                "final_result": "SL",
                "r_result": -1,
                "closed_at": current_ts - 10 * 86400,
            }
            self.write_json(root, "trade_ledger.json", premium)

            scalp = json.loads((root / "scalp_performance_ledger.json").read_text(encoding="utf-8"))
            scalp["records"][0]["trade_closed_at"] = current_ts - 2 * 86400
            scalp["records"].append({
                "id": "s-previous",
                "stage": "REAL_SIGNAL",
                "symbol": "LINKUSDT",
                "direction": "SHORT",
                "trade_outcome": "TP1",
                "trade_result_r": 1.2,
                "trade_closed_at": current_ts - 11 * 86400,
            })
            self.write_json(root, "scalp_performance_ledger.json", scalp)

            pump = json.loads((root / "pump_performance_ledger.json").read_text(encoding="utf-8"))
            pump["records"][0]["trade_closed_at"] = current_ts - 3 * 86400
            self.write_json(root, "pump_performance_ledger.json", pump)

            comparison = build_dashboard_data(root, now=current)["period_comparisons"]["7D"]
            total = next(row for row in comparison["rows"] if row["system"] == "ALL")
            self.assertEqual(total["current"]["sample"], 3)
            self.assertAlmostEqual(total["current"]["net_r"], 0.6)
            self.assertEqual(total["previous"]["sample"], 2)
            self.assertAlmostEqual(total["previous"]["net_r"], 0.2)
            self.assertAlmostEqual(total["net_r_delta"], 0.4)
            premium_row = next(
                row for row in comparison["rows"] if row["system"] == "PREMIUM"
            )
            self.assertAlmostEqual(premium_row["net_r_delta"], 2.6)

    def test_source_freshness_accepts_iso_and_millisecond_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            current = datetime(2026, 8, 14, 12, 0, tzinfo=timezone.utc)
            current_ts = int(current.timestamp())
            premium = json.loads((root / "open_signals.json").read_text(encoding="utf-8"))
            premium["BTC_SHORT"]["last_checked_at"] = "2026-08-14T11:45:00Z"
            self.write_json(root, "open_signals.json", premium)
            scalp = json.loads((root / "scalp_radar_state.json").read_text(encoding="utf-8"))
            scalp["last_sent"] = {"BTCUSDT_LONG": current_ts - 300}
            scalp["early_last_sent"] = {"ETHUSDT_SHORT": current_ts - 120}
            self.write_json(root, "scalp_radar_state.json", scalp)
            report = json.loads((root / "system_control_center_report.json").read_text(encoding="utf-8"))
            report["generated_at"] = current_ts * 1000
            self.write_json(root, "system_control_center_report.json", report)
            data = build_dashboard_data(root, now=current)
            statuses = {row["filename"]: row for row in data["sources"]}
            self.assertEqual(statuses["open_signals.json"]["status"], "FRESH")
            self.assertEqual(statuses["scalp_radar_state.json"]["status"], "FRESH")
            self.assertEqual(statuses["system_control_center_report.json"]["status"], "FRESH")
            self.assertFalse(any(
                "scalp_radar_state.json: kritik" in warning
                for warning in data["data_quality"]["warnings"]
            ))

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
