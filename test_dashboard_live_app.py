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
    PanelConfig,
    SessionStore,
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
            handler = make_handler(
                config,
                service,
                sessions,
                LoginRateLimiter(),
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
                self.assertEqual(
                    data["live_source"]["mode"],
                    "LOCAL_REPOSITORY_FILES",
                )
                self.assertEqual(
                    response.getheader("Cache-Control"),
                    "no-store, max-age=0",
                )

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
