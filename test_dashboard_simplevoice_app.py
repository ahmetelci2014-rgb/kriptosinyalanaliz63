from __future__ import annotations

import inspect
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_alert_app as alert
import dashboard_simplevoice_app as simplevoice


class SimpleVoiceUxTests(unittest.TestCase):
    def current_alert_home(self) -> str:
        return alert.alert_dashboard_page(
            {"username": "member", "role": "MEMBER", "csrf": "csrf-voice"},
            "nonce-voice",
        )

    def test_real_home_keeps_existing_alerts_and_adds_simple_voice_layer(self):
        body = self.current_alert_home()
        self.assertIn('id="soundToggle"', body)
        self.assertIn('id="notifyDrawer"', body)
        enhanced = simplevoice.enhance_simple_voice_ui(body, "nonce-voice")
        self.assertIn('id="v333-simplevoice-script"', enhanced)
        self.assertIn("v333Status", enhanced)
        self.assertIn("Sesli bildirim kapalı", enhanced)
        self.assertIn("speechSynthesis", enhanced)
        self.assertIn("SpeechSynthesisUtterance", enhanced)
        self.assertIn("AudioContext", enhanced)
        self.assertIn("tr-TR", enhanced)
        self.assertIn('nonce="nonce-voice"', enhanced)
        self.assertIn('id="soundToggle"', enhanced)
        self.assertIn('id="notifyDrawer"', enhanced)

    def test_browser_script_has_valid_javascript_syntax_when_node_is_available(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")
        script = simplevoice.SCRIPT.strip()
        js = script.split(">", 1)[1].rsplit("</script>", 1)[0]
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

    def test_first_load_is_silent_and_only_new_events_are_announced(self):
        source = inspect.getsource(simplevoice)
        self.assertIn("kripto_voice_initialized_v333", source)
        self.assertIn("events.forEach(e=>seen.add(e.id))", source)
        self.assertIn("if(fresh.length)announce(fresh[0])", source)
        self.assertIn('"existing_events_spoken_on_first_load": False', source)
        self.assertIn('"voice_events": ["signal", "TP", "SL", "BE"]', source)

    def test_voice_is_explicit_opt_in_and_browser_local(self):
        source = inspect.getsource(simplevoice)
        self.assertIn("localStorage.getItem(VOICE)==='1'", source)
        self.assertIn("localStorage.setItem(VOICE,on?'1':'0')", source)
        self.assertIn("Sesli bildirimler açık.", source)
        self.assertIn('"voice_notifications": "user_opt_in"', source)
        self.assertNotIn("Notification.requestPermission", source)

    def test_live_strip_does_not_overclaim_data_health(self):
        source = inspect.getsource(simplevoice)
        self.assertIn("data?.data_quality?.ok!==false", source)
        self.assertIn("SON GEÇERLİ VERİ", source)
        self.assertIn("VERİ BEKLENİYOR", source)
        self.assertIn("BAĞLANTIYI KONTROL ET", source)
        self.assertIn("age<=75", source)
        self.assertIn("age<=150", source)

    def test_mobile_nav_is_four_primary_targets_without_deleting_market_route(self):
        source = inspect.getsource(simplevoice)
        self.assertIn('.mobile-nav a[href="/market-center"]', source)
        self.assertIn('"mobile_primary_nav_max": 4', source)
        self.assertIn('"market_route": "preserved"', source)
        self.assertNotIn("location.assign('/market-center')", source)

    def test_layer_is_idempotent_and_presentation_only(self):
        body = self.current_alert_home()
        once = simplevoice.enhance_simple_voice_ui(body, "n")
        twice = simplevoice.enhance_simple_voice_ui(once, "n")
        self.assertEqual(once, twice)
        source = inspect.getsource(simplevoice)
        self.assertNotIn("def do_POST", source)
        self.assertIn("marketcoin.make_v332_handler", source)
        self.assertIn('"signal_engine": "unchanged"', source)
        self.assertIn('"telegram": "unchanged"', source)
        self.assertIn('"trade_management": "unchanged"', source)
        self.assertIn('"ledger_write": "unchanged"', source)


if __name__ == "__main__":
    unittest.main()
