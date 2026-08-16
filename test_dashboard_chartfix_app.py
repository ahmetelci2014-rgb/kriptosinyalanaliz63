from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import dashboard_chartfix_app as fix
from dashboard_live_app import MarketDataError, OKXMarketDataClient


class ResilientMarketTests(unittest.TestCase):
    def test_falls_back_when_okx_candles_are_unavailable(self):
        client = fix.ResilientMarketDataClient(cache_seconds=5)
        rows = [
            [1700000000000, "100", "105", "95", "102", "12"],
            [1700000900000, "102", "108", "101", "107", "14"],
        ]
        with mock.patch.object(OKXMarketDataClient, "get_candles", side_effect=MarketDataError("okx down")):
            with mock.patch.object(client, "_request_binance", return_value=rows):
                payload = client.get_candles("BTCUSDT", "15m")
        self.assertTrue(payload["fallback"])
        self.assertEqual(payload["source"], "BINANCE_FUTURES_PUBLIC_FALLBACK")
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(len(payload["candles"]), 2)
        self.assertEqual(payload["last_price"], 107.0)
        self.assertEqual(payload["primary_source_error"], "OKX_UNAVAILABLE")

    def test_primary_okx_result_is_preserved(self):
        client = fix.ResilientMarketDataClient(cache_seconds=5)
        primary = {"symbol": "BTCUSDT", "bar": "15m", "candles": [{"close": 100}], "source": "OKX_PUBLIC_NO_API_KEY"}
        with mock.patch.object(OKXMarketDataClient, "get_candles", return_value=primary):
            payload = client.get_candles("BTCUSDT", "15m")
        self.assertIs(payload, primary)
        self.assertEqual(payload["source"], "OKX_PUBLIC_NO_API_KEY")


class ChartRecoveryPageTests(unittest.TestCase):
    def test_page_contains_canvas_and_svg_recovery(self):
        body = fix.coin_center_page_v3141("nonce123", "BTCUSDT")
        self.assertIn('id="chart"', body)
        self.assertIn('id="levelOverlay"', body)
        self.assertIn('id="chartRecovery"', body)
        self.assertIn('id="v3141-chart-recovery-script"', body)
        self.assertIn("/api/market/candles", body)
        self.assertIn("/api/coin-center/summary", body)
        self.assertIn("Giriş", body)
        self.assertIn("TP1", body)
        self.assertIn("SL", body)
        self.assertIn('nonce="nonce123"', body)

    def test_recovery_enhancement_is_idempotent(self):
        base = fix.chartlevel.coin_center_page_v314("n", "ETHUSDT")
        once = fix.enhance_recovery_page(base, "n")
        twice = fix.enhance_recovery_page(once, "n")
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="chartRecovery"'), 1)

    def test_inline_javascript_has_valid_syntax_when_node_exists(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node komutu bu ortamda yok")
        body = fix.coin_center_page_v3141("nonce123", "SOLUSDT")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", body, flags=re.S)
        self.assertGreaterEqual(len(scripts), 3)
        with tempfile.TemporaryDirectory(prefix="coin-js-") as directory:
            for index, script in enumerate(scripts):
                path = Path(directory) / f"script-{index}.js"
                path.write_text(script, encoding="utf-8")
                result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0, msg=f"script {index}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
