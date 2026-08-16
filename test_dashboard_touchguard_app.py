from __future__ import annotations

import inspect
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_alert_app as alert
import dashboard_mobileux_app as mobile
import dashboard_touchguard_app as touch


class TouchGuardTests(unittest.TestCase):
    def current_home(self) -> str:
        body = alert.alert_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf-touch"},
            "nonce-touch",
        )
        return mobile.enhance_mobile_ui(body, "nonce-touch")

    def test_real_home_disables_closed_overlay_and_drawer_pointer_capture(self):
        body = touch.enhance_touch_guard(self.current_home(), "nonce-touch")
        self.assertIn('id="focusOverlay"', body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn('id="notifyOverlay"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('.focus-overlay:not(.open),.notify-overlay:not(.open)', body)
        self.assertIn('.focus-drawer:not(.open),.notify-drawer:not(.open)', body)
        self.assertIn('pointer-events:none!important', body)
        self.assertIn('visibility:hidden!important', body)
        self.assertIn('id="v335-touchguard-script"', body)

    def test_mobile_navigation_is_explicitly_clickable(self):
        body = touch.enhance_touch_guard(self.current_home(), "n")
        self.assertIn('.mobile-nav{pointer-events:auto!important;z-index:70!important}', body)
        self.assertIn('.mobile-nav button,.mobile-nav a{pointer-events:auto!important', body)
        self.assertIn("el.style.pointerEvents='auto'", body)
        self.assertIn("el.style.touchAction='manipulation'", body)

    def test_body_overflow_recovery_only_when_drawers_are_closed(self):
        source = inspect.getsource(touch)
        self.assertIn("const focusOpen=normalizeClosedLayer('focusOverlay','focusDrawer')", source)
        self.assertIn("const notifyOpen=normalizeClosedLayer('notifyOverlay','notifyDrawer')", source)
        self.assertIn("if(!focusOpen&&!notifyOpen&&document.body.style.overflow==='hidden')document.body.style.overflow=''", source)
        self.assertIn("window.addEventListener('pageshow',repair)", source)
        self.assertIn("document.addEventListener('visibilitychange'", source)

    def test_browser_script_has_valid_javascript_syntax_when_node_is_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")
        script = touch.SCRIPT
        js = script.split(">\n", 1)[1].rsplit("\n</script>", 1)[0]
        with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as handle:
            handle.write(js)
            path = Path(handle.name)
        try:
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        finally:
            path.unlink(missing_ok=True)

    def test_layer_is_idempotent_and_presentation_only(self):
        body = self.current_home()
        once = touch.enhance_touch_guard(body, "n")
        twice = touch.enhance_touch_guard(once, "n")
        self.assertEqual(once, twice)
        source = inspect.getsource(touch)
        self.assertNotIn("def do_POST", source)
        self.assertIn("mobile.make_v334_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
