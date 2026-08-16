from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_tradeprogress_app as progress


class TradeProgressPageTests(unittest.TestCase):
    def _base(self) -> str:
        return '<html><head><style></style></head><body><div id="lastPrice">0,08295</div><div class="layout"></div></body></html>'

    def test_page_contains_live_progress_contract(self):
        body = progress.enhance_trade_progress_page(self._base(), "nonce316")
        self.assertIn('id="v316TradeProgress"', body)
        self.assertIn('id="v316Move"', body)
        self.assertIn('id="v316R"', body)
        self.assertIn('id="v316TargetDistance"', body)
        self.assertIn('id="v316SlDistance"', body)
        self.assertIn('id="v316Fill"', body)
        self.assertIn('id="v316Entry"', body)
        self.assertIn('id="v316Now"', body)
        self.assertIn('id="v316-trade-progress-script"', body)
        self.assertIn('MutationObserver', body)
        self.assertIn('/api/coin-center/summary', body)
        self.assertIn('setInterval(syncTrade,18000)', body)
        self.assertIn('setInterval(readLivePrice,500)', body)
        self.assertIn('directionSign', body)
        self.assertIn("==='SHORT' ? -1 : 1", body)
        self.assertIn('sign*(p-entry)/risk', body)
        self.assertIn('sign*(targetValue-p)', body)
        self.assertIn('sign*(p-sl)', body)
        self.assertIn('Gerçek hesap P/L değildir', body)
        self.assertIn('Salt okunur · emir açmaz', body)
        self.assertNotIn('location.reload()', body)
        self.assertNotIn("method:'POST'", body)
        self.assertNotIn('method:"POST"', body)
        self.assertIn('nonce="nonce316"', body)

    def test_enhancement_is_idempotent(self):
        once = progress.enhance_trade_progress_page(self._base(), "n")
        twice = progress.enhance_trade_progress_page(once, "n")
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="v316TradeProgress"'), 1)

    def test_missing_anchor_fails_closed(self):
        with self.assertRaises(RuntimeError):
            progress.enhance_trade_progress_page('<html><style></style><body></body></html>', 'n')

    def test_inline_javascript_has_valid_syntax_when_node_exists(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node komutu bu ortamda yok")
        body = progress.enhance_trade_progress_page(self._base(), "nonce316")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", body, flags=re.S)
        self.assertEqual(len(scripts), 1)
        with tempfile.TemporaryDirectory(prefix="tradeprogress-js-") as directory:
            path = Path(directory) / "tradeprogress.js"
            path.write_text(scripts[0], encoding="utf-8")
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class TradeProgressSourceTests(unittest.TestCase):
    def test_runtime_keeps_live_market_cache_and_core_boundary(self):
        source = Path(progress.__file__).read_text(encoding="utf-8")
        self.assertIn('ResilientMarketDataClient(cache_seconds=2)', source)
        self.assertIn('OKXMarketOverviewClient(cache_seconds=2)', source)
        self.assertIn("'signal_engine':'unchanged'", source)
        self.assertIn("'telegram':'unchanged'", source)
        self.assertNotIn('strategy.py', source.split('PROGRESS_CSS', 1)[1])


if __name__ == "__main__":
    unittest.main()
