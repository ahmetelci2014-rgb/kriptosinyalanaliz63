from __future__ import annotations

import inspect
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_alert_app as alert
import dashboard_mobileux_app as mobile
import dashboard_simplevoice_app as voice


class MobileUxV334Tests(unittest.TestCase):
    def current_voice_home(self) -> str:
        body = alert.alert_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf-mobile"},
            "nonce-mobile",
        )
        return voice.enhance_simple_voice_ui(body, "nonce-mobile")

    def test_real_home_keeps_voice_and_adds_mobile_repair_once(self):
        body = self.current_voice_home()
        self.assertIn('id="v333-simplevoice-script"', body)
        enhanced = mobile.enhance_mobile_ui(body, "nonce-mobile")
        self.assertIn('id="v333-simplevoice-script"', enhanced)
        self.assertIn('id="v334-mobile-script"', enhanced)
        self.assertIn('nonce="nonce-mobile"', enhanced)
        self.assertIn('body .mobile-nav a[href="/market-center"]{display:flex!important}', enhanced)
        self.assertIn('grid-template-columns:minmax(0,1fr) auto', enhanced)
        self.assertIn('padding-bottom:calc(86px + env(safe-area-inset-bottom))', enhanced)

    def test_market_is_restored_as_fifth_primary_mobile_target(self):
        source = inspect.getsource(mobile)
        self.assertIn('[data-view="home"]', source)
        self.assertIn('[data-view="signals"]', source)
        self.assertIn('a[href="/market-center"]', source)
        self.assertIn('[data-view="results"]', source)
        self.assertIn('a[href="/account"]', source)
        self.assertIn('"mobile_market_visible": True', source)
        self.assertIn('"mobile_navigation": ["Ana", "Sinyal", "Piyasa", "Sonuç", "Hesap"]', source)

    def test_touch_and_overflow_guards_are_present(self):
        source = inspect.getsource(mobile)
        self.assertIn('overflow-x:hidden!important', source)
        self.assertIn('min-height:58px!important', source)
        self.assertIn('font-size:16px!important', source)
        self.assertIn('touch-action:manipulation', source)
        self.assertIn('"mobile_touch_targets": "improved"', source)
        self.assertIn('"mobile_bottom_content_guard": True', source)

    def test_layer_is_idempotent_and_presentation_only(self):
        body = self.current_voice_home()
        once = mobile.enhance_mobile_ui(body, "n")
        twice = mobile.enhance_mobile_ui(once, "n")
        self.assertEqual(once, twice)
        source = inspect.getsource(mobile)
        self.assertNotIn("def do_POST", source)
        self.assertIn("voice.make_v333_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)

    def test_browser_script_has_valid_javascript_syntax_when_node_is_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")
        script = mobile.SCRIPT
        js = script.split(">\n", 1)[1].rsplit("\n</script>", 1)[0]
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(js)
            path = Path(handle.name)
        try:
            result = subprocess.run(
                [node, "--check", str(path)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
