from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_chartcoord_app as coord


class ChartCoordinationPageTests(unittest.TestCase):
    def test_coordination_layer_is_injected_once(self):
        base = coord.chartfix.coin_center_page_v3141("nonce123", "BTCUSDT")
        once = coord.enhance_coordination_page(base, "nonce123")
        twice = coord.enhance_coordination_page(once, "nonce123")
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="v3142-chart-coordination-script"'), 1)
        self.assertIn('data-v3142-wait', once)
        self.assertIn('GRACE_MS = 6500', once)
        self.assertIn('preferPrimary()', once)
        self.assertIn('Grafik alınamadı', once)

    def test_coordination_prefers_primary_chart_before_recovery(self):
        body = coord.enhance_coordination_page(
            coord.chartfix.coin_center_page_v3141("n", "ETHUSDT"),
            "n",
        )
        self.assertIn("if (preferPrimary())", body)
        self.assertIn("recovery.style.display = 'none'", body)
        self.assertIn("markWaiting(true)", body)
        self.assertIn("Date.now() >= deadline", body)
        self.assertNotIn("/alınamadı|yükleniyor/i.test(text)", coord.COORD_SCRIPT)

    def test_inline_javascript_has_valid_syntax_when_node_exists(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node komutu bu ortamda yok")
        body = coord.enhance_coordination_page(
            coord.chartfix.coin_center_page_v3141("nonce123", "SOLUSDT"),
            "nonce123",
        )
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", body, flags=re.S)
        self.assertGreaterEqual(len(scripts), 4)
        with tempfile.TemporaryDirectory(prefix="coin-chartcoord-js-") as directory:
            for index, script in enumerate(scripts):
                path = Path(directory) / f"script-{index}.js"
                path.write_text(script, encoding="utf-8")
                result = subprocess.run(
                    [node, "--check", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 0, msg=f"script {index}: {result.stderr}")


if __name__ == "__main__":
    unittest.main()
