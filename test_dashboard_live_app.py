import http.client
import io
import json
import re
import tempfile
import threading
import unittest
import urllib.parse
import urllib.error
from pathlib import Path
from unittest import mock

from dashboard_live_app import (
    GitHubJsonSource,
    LiveDashboardService,
    LocalJsonSource,
    LoginRateLimiter,
    OKXMarketDataClient,
    PanelConfig,
    ROLE_ADMIN,
    ROLE_MEMBER,
    SessionStore,
    authenticate_account,
    dashboard_for_session,
    make_handler,
    password_hash,
    verify_password,
)
from http.server import ThreadingHTTPServer


class LiveDashboardAppTests(unittest.TestCase):
    def write_json(self, root: Path, name: str, data):
        (root / name).write_text(
            json.dumps(data, ensure_ascii=False),
            encoding="utf-8",
        )

    def make_fixture(self, root: Path):
        self.write_json(root, "open_signals.json", {
            "BTC_LONG": {
                "trade_id": "p-open",
                "symbol": "BTCUSDT",
                "direction": "LONG",
                "entry": 100,
                "tp1": 101,
                "tp2": 102,
                "tp3": 103,
                "sl": 99,
                "opened_at": 1000,
                "closed": False,
            }
        })
        self.write_json(
            root,
            "scalp_radar_state.json",
            {"open_scalp_signals": {}},
        )
        self.write_json(
            root,
            "pump_radar_state.json",
            {"open_pump_signals": {}, "open_signals": {}},
        )
        self.write_json(
            root,
            "new_listing_performance_ledger.json",
            {"records": {}},
        )
        self.write_json(root, "trade_ledger.json", {"trades": {}})
        self.write_json(
            root,
            "scalp_performance_ledger.json",
            {"records": []},
        )
        self.write_json(
            root,
            "pump_performance_ledger.json",
            {"records": []},
        )
        self.write_json(
            root,
            "system_control_center_report.json",
            {
                "generated_at": 2000,
                "executive": {
                    "overall_health": "GREEN",
                    "health_counts": {
                        "GREEN": 1,
                        "YELLOW": 0,
                        "RED": 0,
                    },
                },
                "components": {},
            },
        )

    def test_password_hash_roundtrip(self):
        encoded = password_hash("güçlü-şifre", iterations=100_000)
        self.assertTrue(verify_password("güçlü-şifre", encoded, None))
        self.assertFalse(verify_password("yanlış", encoded, None))

    def test_admin_and_member_authentication_are_separate(self):
        config = PanelConfig(
            username="ahmet",
            password="admin-secret",
            password_hash_value=None,
            repository="owner/private-repo",
            ref="main",
            github_token="token",
            root=Path("."),
            refresh_seconds=30,
            cookie_secure=True,
            trust_proxy=True,
            session_hours=12,
            member_username="demo-member",
            member_password="member-secret",
        )
        self.assertEqual(
            authenticate_account(config, "ahmet", "admin-secret"),
            {"username": "ahmet", "role": ROLE_ADMIN},
        )
        self.assertEqual(
            authenticate_account(config, "demo-member", "member-secret"),
            {"username": "demo-member", "role": ROLE_MEMBER},
        )
        self.assertIsNone(
            authenticate_account(config, "demo-member", "admin-secret")
        )
        self.assertIsNone(
            authenticate_account(config, "ahmet", "member-secret")
        )

    def test_member_dashboard_filter_removes_internal_diagnostics(self):
        data = {
            "mode": "ADMIN",
            "live_source": {"mode": "PRIVATE_GITHUB"},
            "live_systems": [{"decision": "KORU"}],
            "open_risk": {"total": 2},
            "period_comparisons": {"7D": {"rows": [1]}},
            "sources": [{"filename": "secret.json"}],
            "data_quality": {"ok": False, "warnings": ["secret.json eski"]},
            "health": {
                "overall": "GREEN",
                "counts": {"green": 8, "yellow": 0, "red": 0},
                "generated_at": 10,
                "components": [{"decision": "İÇ KARAR"}],
            },
            "open_trades": [{"source": "MTF_SECRET"}],
            "recent_results": [{"source": "LEDGER_SECRET"}],
        }
        filtered = dashboard_for_session(
            data,
            {"username": "demo-member", "role": ROLE_MEMBER},
        )
        self.assertEqual(filtered["viewer"]["role"], ROLE_MEMBER)
        self.assertNotIn("live_source", filtered)
        self.assertEqual(filtered["live_systems"], [])
        self.assertEqual(filtered["period_comparisons"], {})
        self.assertEqual(filtered["sources"], [])
        self.assertEqual(filtered["health"]["components"], [])
        self.assertNotIn("secret.json", " ".join(filtered["data_quality"]["warnings"]))
        self.assertEqual(filtered["open_trades"][0]["source"], "Canlı Sinyal")
        self.assertEqual(filtered["recent_results"][0]["source"], "Sonuç Kaydı")

    def test_github_source_keeps_token_server_side_and_reuses_etag_cache(self):
        source = GitHubJsonSource("owner/private", "main", "top-secret")

        class FakeResponse:
            def __init__(self):
                self.headers = {"ETag": '"etag-1"'}

            def read(self):
                return json.dumps({"BTC": {"symbol": "BTCUSDT"}}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch(
            "dashboard_live_app.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as mocked:
            name, document, warning = source._fetch_one("open_signals.json")
            request = mocked.call_args.args[0]
            self.assertEqual(
                request.get_header("Authorization"),
                "Bearer top-secret",
            )
            self.assertEqual(name, "open_signals.json")
            self.assertEqual(document["BTC"]["symbol"], "BTCUSDT")
            self.assertIsNone(warning)

        not_modified = urllib.error.HTTPError(
            "https://api.github.com/test",
            304,
            "Not Modified",
            {},
            io.BytesIO(),
        )
        with mock.patch(
            "dashboard_live_app.urllib.request.urlopen",
            side_effect=not_modified,
        ):
            _name, cached, warning = source._fetch_one(
                "open_signals.json"
            )
            self.assertEqual(cached, document)
            self.assertIsNone(warning)
            self.assertNotIn("top-secret", json.dumps(cached))

    def test_okx_market_client_uses_public_api_and_normalizes_candles(self):
        client = OKXMarketDataClient(cache_seconds=20)

        class FakeResponse:
            def read(self):
                return json.dumps({
                    "code": "0",
                    "data": [
                        ["2000000", "101", "105", "99", "104", "12", "0", "0", "1"],
                        ["1000000", "100", "103", "98", "101", "10", "0", "0", "1"],
                    ],
                }).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with mock.patch(
            "dashboard_live_app.urllib.request.urlopen",
            return_value=FakeResponse(),
        ) as mocked:
            payload = client.get_candles("btc/usdt", "15m")
            cached = client.get_candles("BTCUSDT", "15m")
            request = mocked.call_args.args[0]
            self.assertEqual(mocked.call_count, 1)
            self.assertIsNone(request.get_header("Authorization"))
            self.assertIn("BTC-USDT-SWAP", request.full_url)
            self.assertEqual(payload["source"], "OKX_PUBLIC_NO_API_KEY")
            self.assertEqual(payload["symbol"], "BTCUSDT")
            self.assertEqual(payload["candles"][0]["open"], 100.0)
            self.assertEqual(payload["last_price"], 104.0)
            self.assertEqual(cached, payload)
            historical = client.get_candles("BTCUSDT", "15m", 1_700_000_000)
            historical_request = mocked.call_args.args[0]
            self.assertEqual(mocked.call_count, 2)
            self.assertIn("/history-candles?", historical_request.full_url)
            self.assertIn("after=", historical_request.full_url)
            self.assertEqual(historical["anchor"], 1_700_000_000)
        with self.assertRaises(ValueError):
            client.get_candles("../../etc/passwd", "15m")
        with self.assertRaises(ValueError):
            client.get_candles("BTCUSDT", "2H")
        with self.assertRaises(ValueError):
            client.get_candles("BTCUSDT", "15m", "not-a-time")

    def test_live_http_login_and_private_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.make_fixture(root)
            config = PanelConfig(
                username="ahmet",
                password="test-password",
                password_hash_value=None,
                repository="owner/private-repo",
                ref="main",
                github_token=None,
                root=root,
                refresh_seconds=10,
                cookie_secure=False,
                trust_proxy=False,
                session_hours=1,
            )
            service = LiveDashboardService(LocalJsonSource(root), 1)
            sessions = SessionStore(3600)

            class FakeMarketClient:
                def get_candles(self, symbol, bar, anchor=None):
                    normalized = OKXMarketDataClient.normalize_symbol(symbol)
                    OKXMarketDataClient.validate_bar(bar)
                    normalized_anchor = OKXMarketDataClient.normalize_anchor(anchor)
                    return {
                        "symbol": normalized,
                        "inst_id": "BTC-USDT-SWAP",
                        "market_type": "SWAP",
                        "bar": bar,
                        "candles": [{
                            "ts": 1,
                            "open": 100,
                            "high": 102,
                            "low": 99,
                            "close": 101,
                            "volume": 5,
                            "confirmed": True,
                        }],
                        "last_price": 101,
                        "fetched_at": 2,
                        "anchor": normalized_anchor or None,
                        "source": "OKX_PUBLIC_NO_API_KEY",
                    }

            handler = make_handler(
                config,
                service,
                sessions,
                LoginRateLimiter(),
                FakeMarketClient(),
            )
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = server.server_address

            try:
                connection = http.client.HTTPConnection(host, port, timeout=5)
                connection.request("GET", "/api/dashboard")
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()

                connection.request("GET", "/api/market/candles?symbol=BTCUSDT&bar=15m")
                response = connection.getresponse()
                self.assertEqual(response.status, 401)
                response.read()

                connection.request("GET", "/login")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                login_body = response.read().decode("utf-8")
                csrf_match = re.search(
                    r'name="csrf" value="([^"]+)"',
                    login_body,
                )
                self.assertIsNotNone(csrf_match)
                csrf = csrf_match.group(1)
                login_cookie = next(
                    value.split(";", 1)[0]
                    for key, value in response.getheaders()
                    if key.lower() == "set-cookie"
                    and value.startswith("panel_login_csrf=")
                )

                form = urllib.parse.urlencode({
                    "username": "ahmet",
                    "password": "test-password",
                    "csrf": csrf,
                })
                connection.request(
                    "POST",
                    "/login",
                    body=form,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Content-Length": str(len(form)),
                        "Cookie": login_cookie,
                    },
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 303)
                session_cookie = next(
                    value.split(";", 1)[0]
                    for key, value in response.getheaders()
                    if key.lower() == "set-cookie"
                    and value.startswith("panel_session=")
                )
                response.read()

                connection.request(
                    "GET",
                    "/",
                    headers={"Cookie": session_cookie},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                page = response.read().decode("utf-8")
                csp = response.getheader("Content-Security-Policy")
                self.assertIn("nonce-", csp)
                self.assertIn("/api/dashboard", page)
                self.assertIn("/api/market/candles", page)
                self.assertIn("Coin ve İşlem Grafiği", page)
                self.assertIn("İşlem İnceleme Merkezi", page)
                self.assertIn("Açık Risk Özeti", page)
                self.assertIn("Yön ve Gün Analizi", page)
                self.assertIn("dailyCanvas", page)
                self.assertIn('class="quick-nav"', page)
                self.assertIn("Dönem Karşılaştırması", page)
                self.assertIn("comparisonWindow", page)
                self.assertIn("CSV indir", page)
                self.assertIn("Canlı veri bağlanıyor", page)
                self.assertNotIn("test-password", page)
                self.assertNotIn("GITHUB_PANEL_TOKEN", page)

                connection.request(
                    "GET",
                    "/api/dashboard",
                    headers={"Cookie": session_cookie},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                data = json.loads(response.read().decode("utf-8"))
                self.assertEqual(data["summary"]["open_total"], 1)
                self.assertEqual(data["open_risk"]["long"], 1)
                self.assertEqual(data["open_risk"]["with_stop"], 1)
                self.assertEqual(len(data["result_breakdown"]["daily_30d"]), 30)
                self.assertEqual(
                    [row["direction"] for row in data["result_breakdown"]["directions"]],
                    ["LONG", "SHORT"],
                )
                self.assertEqual(data["period_comparisons"]["7D"]["days"], 7)
                self.assertEqual(data["period_comparisons"]["30D"]["days"], 30)
                self.assertEqual(
                    data["live_source"]["mode"],
                    "LOCAL_REPOSITORY_FILES",
                )
                self.assertEqual(data["viewer"]["role"], ROLE_ADMIN)
                self.assertEqual(
                    response.getheader("Cache-Control"),
                    "no-store, max-age=0",
                )

                member_token, member_session = sessions.create(
                    "demo-member",
                    ROLE_MEMBER,
                )
                self.assertEqual(member_session["role"], ROLE_MEMBER)
                member_cookie = f"panel_session={member_token}"
                connection.request(
                    "GET",
                    "/api/dashboard",
                    headers={"Cookie": member_cookie},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                member_data = json.loads(response.read().decode("utf-8"))
                self.assertEqual(member_data["viewer"]["role"], ROLE_MEMBER)
                self.assertFalse(member_data["viewer"]["is_admin"])
                self.assertNotIn("live_source", member_data)
                self.assertEqual(member_data["sources"], [])
                self.assertEqual(member_data["health"]["components"], [])
                self.assertEqual(member_data["period_comparisons"], {})
                self.assertEqual(
                    member_data["open_trades"][0]["source"],
                    "Canlı Sinyal",
                )

                connection.request(
                    "GET",
                    "/",
                    headers={"Cookie": member_cookie},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                member_page = response.read().decode("utf-8")
                self.assertIn("Üye · demo-member", member_page)
                self.assertIn("applyViewerPermissions", member_page)

                connection.request(
                    "GET",
                    "/api/market/candles?symbol=BTCUSDT&bar=15m",
                    headers={"Cookie": session_cookie},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                market = json.loads(response.read().decode("utf-8"))
                self.assertEqual(market["last_price"], 101)
                self.assertEqual(market["source"], "OKX_PUBLIC_NO_API_KEY")

                connection.request(
                    "GET",
                    "/api/market/candles?symbol=BTCUSDT&bar=15m&anchor=1700000000",
                    headers={"Cookie": session_cookie},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                historical = json.loads(response.read().decode("utf-8"))
                self.assertEqual(historical["anchor"], 1_700_000_000)

                connection.request(
                    "GET",
                    "/api/market/candles?symbol=BAD&bar=15m",
                    headers={"Cookie": session_cookie},
                )
                response = connection.getresponse()
                self.assertEqual(response.status, 400)
                response.read()

                connection.request("GET", "/healthz")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                health = json.loads(response.read().decode("utf-8"))
                self.assertEqual(health["status"], "ok")
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
