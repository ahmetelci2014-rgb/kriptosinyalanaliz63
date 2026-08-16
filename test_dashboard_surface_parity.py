from __future__ import annotations

import inspect
import unittest
from pathlib import Path

import dashboard_accountflow_runtime_app as account_runtime
import dashboard_app as app
import dashboard_commercial_app as commercial
import dashboard_mobile_server_app as mobile
import dashboard_runtimefix_app as runtimefix
import dashboard_surface_parity_app as parity


class FakeScoreService:
    def get_score(self, symbol: str):
        table = {
            "BTCUSDT": {"score": 91, "direction": "YUKARI", "metrics": {"volume_ratio_15m": 1.8}},
            "ETHUSDT": {"score": 72, "direction": "AŞAĞI", "metrics": {"volume_ratio_15m": 1.1}},
            "SOLUSDT": {"score": 84, "direction": "YUKARI", "metrics": {"volume_ratio_15m": 2.1}},
        }
        return table.get(symbol, {"score": 40, "direction": "KARIŞIK", "metrics": {"volume_ratio_15m": 0.7}})


class DashboardSurfaceParityTests(unittest.TestCase):
    def test_free_mobile_navigation_is_consistent_and_not_duplicated(self):
        nav = parity.mobile_nav(commercial.PLAN_FREE, "account")
        self.assertEqual(nav.count("<a "), 4)
        self.assertIn('href="/mobile"', nav)
        self.assertIn('href="/mobile/market"', nav)
        self.assertIn('href="/mobile/premium"', nav)
        self.assertIn('href="/mobile/account"', nav)
        self.assertEqual(nav.count('href="/mobile/premium"'), 1)
        self.assertIn('class="active" href="/mobile/account"', nav)

    def test_premium_mobile_navigation_keeps_signal_trade_result_core(self):
        nav = parity.mobile_nav(commercial.PLAN_PREMIUM, "trades")
        self.assertEqual(nav.count("<a "), 5)
        self.assertIn('/mobile?view=signals', nav)
        self.assertIn('/mobile?view=trades', nav)
        self.assertIn('/mobile?view=results', nav)
        self.assertIn('class="active" href="/mobile?view=trades"', nav)
        self.assertIn('/mobile/account', nav)

    def test_signal_and_result_filters_are_server_side(self):
        data = {
            "open_trades": [
                {"symbol": "BTCUSDT", "direction": "LONG", "system": "PREMIUM"},
                {"symbol": "ETHUSDT", "direction": "SHORT", "system": "SCALP"},
                {"symbol": "SOLUSDT", "direction": "LONG", "system": "SCALP"},
            ],
            "recent_results": [
                {"symbol": "BTCUSDT", "outcome": "TP2", "system": "PREMIUM"},
                {"symbol": "ETHUSDT", "outcome": "SL", "system": "SCALP"},
                {"symbol": "SOLUSDT", "outcome": "BE", "system": "SCALP"},
            ],
        }
        signals = parity.filter_mobile_data(data, {"direction": ["LONG"], "q": ["SOL"]}, "signals")
        self.assertEqual([r["symbol"] for r in signals["open_trades"]], ["SOLUSDT"])
        results = parity.filter_mobile_data(data, {"outcome": ["SL"], "system": ["SCALP"]}, "results")
        self.assertEqual([r["symbol"] for r in results["recent_results"]], ["ETHUSDT"])
        self.assertEqual(len(data["open_trades"]), 3, "filtre kaynak veriyi değiştirmemeli")

    def test_mobile_filter_is_before_signal_list_and_new_pages_stay_javascript_free(self):
        session = {"username": "member", "csrf": "csrf-test"}
        data = {
            "open_trades": [{
                "symbol": "BTCUSDT", "direction": "LONG", "system": "PREMIUM",
                "entry": 100, "tp1": 105, "tp2": 110, "tp3": 115, "sl": 95,
            }],
            "recent_results": [],
        }
        raw = mobile.mobile_page(
            session, data, plan=commercial.PLAN_PREMIUM,
            plan_label="Premium", view="signals", is_admin=False,
        )
        body = parity.enhance_mobile_core(
            raw, plan=commercial.PLAN_PREMIUM, active="signals", query={}
        )
        self.assertIn('<form class="v3326-filter"', body)
        self.assertLess(body.index('<form class="v3326-filter"'), body.index("BTCUSDT"))

        watch = parity.render_watchlist_page(
            session, plan=commercial.PLAN_PREMIUM, plan_label="Premium",
            symbols=["BTCUSDT"],
            items=[{"symbol": "BTCUSDT", "last": 100, "change_24h_pct": 2.4}],
            data={"open_trades": [], "recent_results": []},
        )
        opportunities = parity.render_opportunities_page(
            session, plan=commercial.PLAN_PREMIUM, plan_label="Premium",
            rows=[{"symbol": "BTCUSDT", "last": 100, "change_24h_pct": 2.4, "group": "rising"}],
            meta={"filter": "all", "sort": "default", "q": ""},
            summary={"universe": 1, "up": 1, "down": 0},
        )
        for page in (watch, opportunities):
            self.assertNotIn("<script", page.lower())
            self.assertIn("/mobile/coin?symbol=BTCUSDT", page)

    def test_watchlist_preference_is_bounded_validated_and_http_only(self):
        symbols = []
        for symbol in ["BTCUSDT", "ETHUSDT", "BTCUSDT", "bad", "SOLUSDT"]:
            symbols = parity.update_watchlist(symbols, add=symbol)
        self.assertEqual(symbols, ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
        for i in range(20):
            symbols = parity.update_watchlist(symbols, add=f"X{i}USDT")
        self.assertLessEqual(len(symbols), parity.MAX_WATCH)
        cookie = parity.watch_cookie(symbols, secure=True)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Lax", cookie)
        self.assertIn("Secure", cookie)
        parsed = parity.read_watchlist(cookie.split(";", 1)[0])
        self.assertEqual(parsed, symbols)

    def test_opportunity_filters_reuse_existing_score_concept(self):
        payload = {
            "summary": {"universe": 3, "up": 2, "down": 1},
            "groups": {
                "rising": [
                    {"symbol": "BTCUSDT", "change_24h_pct": 3.2, "last": 10},
                    {"symbol": "SOLUSDT", "change_24h_pct": 2.0, "last": 8},
                ],
                "falling": [{"symbol": "ETHUSDT", "change_24h_pct": -2.5, "last": 7}],
                "volume": [],
                "active": [],
            },
        }
        rows, meta = parity.prepare_opportunities(
            payload, {"filter": ["score80"], "sort": ["score"]}, FakeScoreService()
        )
        self.assertEqual([r["symbol"] for r in rows], ["BTCUSDT", "SOLUSDT"])
        self.assertTrue(meta["need_score"])
        down, _ = parity.prepare_opportunities(payload, {"filter": ["down"]}, FakeScoreService())
        self.assertEqual([r["symbol"] for r in down], ["ETHUSDT"])

    def test_marketing_copy_states_sound_is_desktop_only(self):
        raw = "Sesli ve renkli yeni sinyal uyarısı | 🔒 Sesli ve renkli sinyal uyarıları"
        fixed = parity.correct_product_copy(raw)
        self.assertIn("Masaüstünde sesli ve renkli yeni sinyal uyarısı", fixed)
        self.assertIn("(masaüstü)", fixed)

    def test_runtime_contract_keeps_v3326_surface_parity_under_account_flow(self):
        source = inspect.getsource(runtimefix)
        helper = inspect.getsource(parity)
        account_source = inspect.getsource(account_runtime)
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_accountflow_runtime_app")
        self.assertEqual(app.VERSION, account_runtime.VERSION)
        self.assertIn("V3_32_8_WATCHLIST_SYNC", account_runtime.VERSION)
        self.assertIn("V3_32_6_SURFACE_PARITY", runtimefix.VERSION)
        self.assertIn('_serve_mobile_watchlist', source)
        self.assertIn('_serve_mobile_opportunities', source)
        self.assertIn('"mobile_filters": "server_rendered"', source)
        self.assertIn('"mobile_sound": "desktop_only_by_design"', source)
        self.assertIn('path == "/account/password"', account_source)
        self.assertIn('path == "/payment/notify"', account_source)
        self.assertIn('"watchlist_sync": "managed_account_cross_device"', account_source)
        self.assertNotIn("def do_POST", source)
        self.assertNotIn("def do_POST", helper)
        for forbidden in ("trade_ledger.json", "open_signals.json", "strategy.py", "config.py"):
            self.assertNotIn(forbidden, helper)

    def test_docker_and_audit_contract_include_parity_module(self):
        docker = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        ignore = Path(".dockerignore").read_text(encoding="utf-8")
        self.assertIn("dashboard_surface_parity_app.py", docker)
        self.assertIn("!dashboard_surface_parity_app.py", ignore)
        audit = Path("docs/panel-surface-audit-v3326.md")
        self.assertTrue(audit.exists())
        text = audit.read_text(encoding="utf-8")
        for term in ("GİRİŞSİZ", "FREE", "PREMIUM", "ADMIN", "İzleme Listesi", "Fırsat Merkezi"):
            self.assertIn(term, text)


if __name__ == "__main__":
    unittest.main()
