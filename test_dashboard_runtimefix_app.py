from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_app as app
import dashboard_commercial_app as commercial
import dashboard_home_app as home
import dashboard_runtimefix_app as fix


class DashboardRuntimeFixTests(unittest.TestCase):
    def test_premium_classic_page_gets_independent_runtime_repair(self):
        body = home.home_dashboard_page(
            {"username": "premium", "role": "MEMBER", "csrf": "csrf"},
            "nonce-test",
        )
        repaired = fix.enhance_runtime_repair(body, "nonce-test")
        self.assertIn('id="v3321-runtime-repair-script"', repaired)
        self.assertIn("fetch('/api/dashboard'", repaired)
        self.assertIn("event.stopImmediatePropagation()", repaired)
        self.assertIn("document.querySelectorAll('.page')", repaired)
        self.assertIn("$('signalSearch')?.value", repaired)
        self.assertIn("$('refreshBtn')?.addEventListener", repaired)

    def test_free_page_remains_separate_and_does_not_get_premium_api(self):
        free = commercial.free_member_page(
            {"username": "free", "role": "MEMBER", "csrf": "csrf"},
            {"plan": commercial.PLAN_FREE, "plan_label": "Ücretsiz", "expires_at": None},
            "nonce-free",
        )
        repaired = fix.enhance_runtime_repair(free, "nonce-free")
        self.assertEqual(repaired, free)
        self.assertNotIn('id="v3321-runtime-repair-script"', repaired)
        self.assertNotIn("/api/dashboard", repaired)
        self.assertIn("/api/public/summary", repaired)

    def test_runtime_script_has_valid_javascript_syntax_when_node_exists(self):
        if not shutil.which("node"):
            self.skipTest("node not installed")
        html = fix.SCRIPT.replace("__NONCE__", "nonce-test")
        match = re.search(r"<script[^>]*>(.*)</script>", html, flags=re.S)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtimefix.js"
            path.write_text(match.group(1), encoding="utf-8")
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_stable_entrypoint_uses_runtime_repair(self):
        self.assertEqual(app.ACTIVE_MODULE, "dashboard_runtimefix_app")
        self.assertEqual(app.VERSION, fix.VERSION)
        self.assertIs(app.make_handler, fix.make_v3321_handler)

    def test_runtime_repair_is_presentation_only(self):
        source = Path("dashboard_runtimefix_app.py").read_text(encoding="utf-8")
        self.assertNotIn("def do_POST", source)
        self.assertIn("v332.make_v332_handler", source)
        self.assertIn('"free_runtime":"separate_preserved"', source)
        self.assertIn('"signal_engine":"unchanged"', source)
        self.assertIn('"telegram":"unchanged"', source)
        self.assertIn('"trade_management":"unchanged"', source)
        self.assertIn('"ledger_write":"unchanged"', source)


if __name__ == "__main__":
    unittest.main()
