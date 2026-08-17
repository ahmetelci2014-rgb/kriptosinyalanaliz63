from __future__ import annotations

import inspect
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_accountflow_runtime_app as v3328
import dashboard_app as app
import dashboard_share_runtime_app as runtime
import dashboard_sharecard_app as cards
import dashboard_shareui_app as shareui


OPEN = {"symbol": "BTCUSDT", "direction": "LONG", "system": "Premium MTF", "entry": 100.0, "tp1": 102.0, "tp2": 104.0, "tp3": 106.0, "sl": 98.0, "score": 91, "opened_at": 1_765_000_000}
RESULT = {**OPEN, "outcome": "TP2", "r_result": 1.4, "closed_at": 1_765_003_600}
CANDLES = [{"open": 99.0, "high": 100.2, "low": 98.7, "close": 99.8}, {"open": 99.8, "high": 101.0, "low": 99.5, "close": 100.7}, {"open": 100.7, "high": 102.3, "low": 100.4, "close": 102.0}]


class ShareCardTests(unittest.TestCase):
    def test_selector_finds_only_real_panel_record(self):
        data = {"open_trades": [OPEN], "recent_results": [RESULT]}
        query = {key: [value] for key, value in cards.selector_params(OPEN, "open", "signal").items()}
        found = cards.find_record(data, query)
        self.assertIsNotNone(found)
        self.assertEqual(found[0], "open")
        self.assertEqual(found[1]["symbol"], "BTCUSDT")
        tampered = dict(query)
        tampered["entry"] = ["999"]
        self.assertIsNone(cards.find_record(data, tampered))

    def test_signal_svg_uses_real_levels_and_social_size(self):
        svg = cards.render_svg(OPEN, kind="open", stage="signal", candles=CANDLES, source="OKX_PUBLIC")
        self.assertIn('width="1080" height="1350"', svg)
        self.assertIn("YENİ İŞLEM SİNYALİ", svg)
        self.assertIn("BTCUSDT", svg)
        for text in ("GİRİŞ 100", "TP1 102", "TP2 104", "TP3 106", "SL 98"):
            self.assertIn(text, svg)
        self.assertIn("Bilgilendirme amaçlıdır", svg)
        self.assertNotIn("username", svg.lower())

    def test_result_svg_marks_tp_sl_be_style_contract(self):
        tp = cards.render_svg(RESULT, kind="result", stage="result", candles=CANDLES)
        self.assertIn("İŞLEM SONUCU", tp)
        self.assertIn("TP2 GERÇEKLEŞTİ", tp)
        self.assertIn("+1.40R", tp)
        sl = cards.render_svg({**RESULT, "outcome": "SL", "r_result": -1}, kind="result", stage="result", candles=CANDLES)
        self.assertIn("SL GERÇEKLEŞTİ", sl)
        be = cards.render_svg({**RESULT, "outcome": "BE", "r_result": 0}, kind="result", stage="result", candles=CANDLES)
        self.assertIn("BE GERÇEKLEŞTİ", be)

    def test_preview_exports_png_and_uses_web_share_with_fallback(self):
        page = cards.render_page(OPEN, kind="open", stage="signal", candles=CANDLES, source="PUBLIC", nonce="nonce-test")
        self.assertIn("↗ Paylaş", page)
        self.assertIn("PNG indir", page)
        self.assertIn("navigator.share", page)
        self.assertIn(".toBlob", page)
        self.assertIn('nonce="nonce-test"', page)

    def test_mobile_buttons_are_links_and_do_not_add_javascript(self):
        body = '<html><head><style></style></head><body><article class="card"><div><strong>BTCUSDT</strong><small class="system">Premium MTF</small></div><span class="tag long">LONG</span></article><article class="resultcard"><div class="resultmain"><strong>BTCUSDT</strong><small>Premium MTF · LONG</small></div><span class="tag tp">TP2</span></article></body></html>'
        enhanced = shareui.enhance_mobile(body, {"open_trades": [OPEN], "recent_results": [RESULT]}, view="signals")
        self.assertEqual(enhanced.count('class="share-mobile"'), 2)
        self.assertIn("/share/trade?", enhanced)
        self.assertNotIn("<script", enhanced.lower())

    def test_desktop_share_script_has_valid_javascript_syntax(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        page = shareui.desktop_script("nonce-test")
        code = re.search(r"<script[^>]*>(.*)</script>", page, flags=re.S).group(1)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "share-ui.js"
            path.write_text(code, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_preview_script_has_valid_javascript_syntax(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        page = cards.render_page(OPEN, kind="open", stage="signal", candles=CANDLES, source="PUBLIC", nonce="nonce-test")
        code = re.findall(r"<script[^>]*>(.*?)</script>", page, flags=re.S)[-1]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "share-preview.js"
            path.write_text(code, encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_runtime_contract_preserves_v3328_and_blocks_free_share(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_share_runtime_app")
        self.assertEqual(app.VERSION, runtime.VERSION)
        self.assertIn("V3_32_9_SHARE_CARDS", runtime.VERSION)
        self.assertIn("V3_32_8_WATCHLIST_SYNC", v3328.VERSION)
        source = inspect.getsource(runtime)
        self.assertIn("base.make_v3321_handler", source)
        self.assertIn('path in {"/share/trade", "/share/card.svg"}', source)
        self.assertIn("if not self._is_premium(session)", source)
        self.assertIn('self._redirect("/premium")', source)
        for forbidden in ("strategy.py", "config.py", "trade_ledger.json", "open_signals.json"):
            self.assertNotIn(forbidden, inspect.getsource(cards))

    def test_docker_contains_share_modules(self):
        docker = Path("Dockerfile.dashboard").read_text(encoding="utf-8")
        ignore = Path(".dockerignore").read_text(encoding="utf-8")
        for name in ("dashboard_sharecard_app.py", "dashboard_shareui_app.py", "dashboard_share_runtime_app.py"):
            self.assertIn(name, docker)
            self.assertIn("!" + name, ignore)


if __name__ == "__main__":
    unittest.main()
