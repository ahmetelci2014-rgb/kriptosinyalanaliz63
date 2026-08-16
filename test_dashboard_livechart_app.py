from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_livechart_app as live


class LiveChartPageTests(unittest.TestCase):
    def test_page_contains_live_chart_and_refresh_contract(self):
        base = '<html><head><style></style></head><body><div class="chart-wrap"><canvas id="chart"></canvas></div><span id="chartInfo">x</span></body></html>'
        body = live.enhance_live_chart_page(base, "nonce123")
        self.assertIn('id="v315LiveChart"', body)
        self.assertIn('id="v315ChartHud"', body)
        self.assertIn('id="v315LiveBadge"', body)
        self.assertIn('id="v315-live-chart-script"', body)
        self.assertIn('const TICK_MS = 2500;', body)
        self.assertIn('const CANDLE_SYNC_MS = 12000;', body)
        self.assertIn('const DETAIL_SYNC_MS = 18000;', body)
        self.assertIn("addEventListener('wheel'", body)
        self.assertIn("addEventListener('pointerdown'", body)
        self.assertIn("addEventListener('dblclick'", body)
        self.assertIn('ResizeObserver', body)
        self.assertIn('/api/market/overview', body)
        self.assertIn('/api/market/candles', body)
        self.assertIn('/api/coin-center/summary', body)
        self.assertNotIn('location.reload()', body)
        self.assertIn('nonce="nonce123"', body)

    def test_enhancement_is_idempotent(self):
        base = '<html><head><style></style></head><body><div class="chart-wrap"><canvas id="chart"></canvas></div><span id="chartInfo">x</span></body></html>'
        once = live.enhance_live_chart_page(base, "n")
        twice = live.enhance_live_chart_page(once, "n")
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="v315LiveChart"'), 1)

    def test_inline_javascript_has_valid_syntax_when_node_exists(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node komutu bu ortamda yok")
        base = '<html><head><style></style></head><body><div class="chart-wrap"><canvas id="chart"></canvas></div><span id="chartInfo">x</span></body></html>'
        body = live.enhance_live_chart_page(base, "nonce123")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", body, flags=re.S)
        self.assertEqual(len(scripts), 1)
        with tempfile.TemporaryDirectory(prefix="livechart-js-") as directory:
            path = Path(directory) / "livechart.js"
            path.write_text(scripts[0], encoding="utf-8")
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class LiveChartSourceTests(unittest.TestCase):
    def test_runtime_uses_short_market_cache(self):
        source = Path(live.__file__).read_text(encoding="utf-8")
        self.assertIn('ResilientMarketDataClient(cache_seconds=2)', source)
        self.assertIn('OKXMarketOverviewClient(cache_seconds=2)', source)
        self.assertIn('signal_engine":"unchanged"', source)
        self.assertIn('telegram":"unchanged"', source)


if __name__ == "__main__":
    unittest.main()
