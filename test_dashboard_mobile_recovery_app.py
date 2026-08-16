from __future__ import annotations

import inspect
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_home_app as home
import dashboard_mobile_recovery_app as recovery
import dashboard_touchguard_app as touch


class MobileRecoveryTests(unittest.TestCase):
    def current_home(self) -> str:
        return home.home_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf-recovery"},
            "nonce-recovery",
        )

    def test_real_home_gets_emergency_recovery_layer(self):
        body = self.current_home()
        body = touch.enhance_touch_guard(body, "nonce-recovery")
        body = recovery.enhance_mobile_recovery(body, "nonce-recovery")
        self.assertIn('id="v336-mobile-recovery-script"', body)
        self.assertIn("mobileRecovery", body)
        self.assertIn(".focus-overlay,.focus-drawer,.notify-overlay,.notify-drawer", body)
        self.assertIn("display:none!important", body)
        self.assertIn("pointer-events:none!important", body)
        self.assertIn(".mobile-nav{pointer-events:auto!important", body)
        self.assertIn("captureMobileNavigation", body)
        self.assertIn("document.addEventListener('click',captureMobileNavigation,true)", body)
        self.assertIn("location.assign(href)", body)
        self.assertIn("switchMobileView", body)

    def test_recovery_resets_body_lock_and_closed_layers(self):
        source = inspect.getsource(recovery)
        self.assertIn("document.body.style.overflow=''", source)
        self.assertIn("el.classList.remove('open')", source)
        self.assertIn("el.setAttribute('aria-hidden','true')", source)
        self.assertIn("window.addEventListener('pageshow',hardReset)", source)
        self.assertIn("orientationchange", source)
        self.assertIn("touchend", source)

    def test_mobile_drawers_are_temporarily_disabled_only_in_presentation_layer(self):
        source = inspect.getsource(recovery)
        self.assertIn('"mobile_focus_drawer": "temporarily_disabled"', source)
        self.assertIn('"mobile_notification_drawer": "temporarily_disabled"', source)
        self.assertNotIn("def do_POST", source)
        self.assertIn("touch.make_v335_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)

    def test_browser_script_has_valid_javascript_syntax_when_node_is_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")
        script = recovery.SCRIPT
        start = script.find(">") + 1
        end = script.rfind("</script>")
        js = script[start:end]
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
