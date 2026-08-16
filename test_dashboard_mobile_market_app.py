from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import dashboard_app as app
import dashboard_mobile_market_app as mobilemarket
import dashboard_runtimefix_app as runtimefix


class MobileMarketCoinTests(unittest.TestCase):
    def setUp(self):
        self.session = {"username": "uye", "csrf": "csrf-test"}
        self.items = [
            {"symbol": "BTCUSDT", "last": 64500.0, "change_24h_pct": 2.4, "high_24h": 65000, "low_24h": 62000, "volume_24h": 1200000, "kind": "OPEN", "direction": "LONG", "system_label": "Premium MTF"},
            {"symbol": "ETHUSDT", "last": 3400.0, "change_24h_pct": -1.1, "high_24h": 3500, "low_24h": 3300, "volume_24h": 900000},
        ]
        self.summary = {
            "symbol": "BTCUSDT",
            "open_trades": [{"symbol": "BTCUSDT", "direction": "LONG", "system": "Premium MTF", "entry": 64000.0, "tp1": 65000.0, "tp2": 66000.0, "tp3": 67000.0, "sl": 63000.0, "score": 91.0}],
            "results": [
                {"symbol": "BTCUSDT", "direction": "LONG", "system": "Premium MTF", "outcome": "TP2", "net_r": 1.4},
                {"symbol": "BTCUSDT", "direction": "SHORT", "system": "Scalp", "outcome": "SL", "net_r": -1.0},
            ],
            "performance": {"sample": 2, "tp": 1, "sl": 1, "be": 0, "tp_rate_percent": 50.0, "net_r": 0.4},
        }
        self.candles = [
            {"ts": 1, "close": 63800.0},
            {"ts": 2, "close": 64200.0},
            {"ts": 3, "close": 64500.0},
            {"ts": 4, "close": 64700.0},
        ]

    def test_free_market_is_public_only_and_javascript_free(self):
        body = mobilemarket.render_market_page(self.session, items=self.items, plan="FREE", plan_label="Ücretsiz", selected="BTCUSDT")
        self.assertIn("Piyasa Merkezi", body)
        self.assertIn("BTCUSDT", body)
        self.assertIn("+2.40%", body)
        self.assertIn("Premium detay", body)
        self.assertIn('href="/premium"', body)
        self.assertNotIn("LONG · açık", body)
        self.assertNotIn('/mobile/coin?symbol=BTCUSDT', body)
        self.assertNotIn("<script", body.lower())

    def test_premium_market_exposes_context_without_spa(self):
        body = mobilemarket.render_market_page(self.session, items=self.items, plan="PREMIUM", plan_label="Premium", selected="BTCUSDT")
        self.assertIn("LONG · açık", body)
        self.assertIn('/mobile/coin?symbol=BTCUSDT', body)
        self.assertIn("24s detayları", body)
        self.assertNotIn("<script", body.lower())

    def test_coin_page_prioritizes_entry_tp1_sl_and_server_svg(self):
        body = mobilemarket.render_coin_page(
            self.session,
            symbol="BTCUSDT",
            bar="15m",
            plan_label="Premium",
            overview_item=self.items[0],
            summary=self.summary,
            candles=self.candles,
            chart_source="OKX_PUBLIC_NO_API_KEY",
        )
        self.assertIn("Coin Merkezi", body)
        self.assertIn("Giriş", body)
        self.assertIn("TP1", body)
        self.assertIn("SL", body)
        self.assertIn("Diğer seviyeler ve skor", body)
        self.assertIn("TP2", body)
        self.assertIn("<svg", body)
        self.assertIn("sunucu SVG · JavaScript yok", body)
        self.assertIn("15m", body)
        self.assertIn("1H", body)
        self.assertIn("4H", body)
        self.assertIn("1D", body)
        self.assertIn("+0.40R", body)
        self.assertNotIn("<script", body.lower())

    def test_svg_chart_fails_closed_without_candles(self):
        self.assertIn("Grafik verisi şu anda alınamadı", mobilemarket._svg_chart([], self.summary["open_trades"][0]))

    def test_active_runtime_keeps_runtimefix_and_v3324_routes_are_presentation_only(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_runtimefix_app")
        self.assertEqual(app.VERSION, runtimefix.VERSION)
        self.assertIs(app.make_handler, runtimefix.make_v3321_handler)
        repair_source = inspect.getsource(runtimefix)
        helper_source = inspect.getsource(mobilemarket)
        self.assertIn('path in {"/mobile/market", "/market-center"}', repair_source)
        self.assertIn('path in {"/mobile/coin", "/coin-center"}', repair_source)
        self.assertIn('"mobile_chart": "svg_no_javascript"', repair_source)
        self.assertIn('"signal_engine": "unchanged"', repair_source)
        self.assertIn('"telegram": "unchanged"', repair_source)
        self.assertIn('"trade_management": "unchanged"', repair_source)
        self.assertIn('"ledger_write": "unchanged"', repair_source)
        self.assertNotIn("def do_POST", repair_source)
        self.assertNotIn("def do_POST", helper_source)
        dockerfile = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        dockerignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.assertIn("dashboard_mobile_market_app.py", dockerfile)
        self.assertIn("!dashboard_mobile_market_app.py", dockerignore)


if __name__ == "__main__":
    unittest.main()
