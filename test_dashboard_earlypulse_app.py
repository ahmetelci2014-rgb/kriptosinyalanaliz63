from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import dashboard_earlypulse_app as pulse
import dashboard_tradeprogress_app as progress


class EarlyPulseMathTests(unittest.TestCase):
    def test_long_first_15m_direction_math_and_tp_first(self):
        trade = {"direction":"LONG","opened_at":1_700_000_000,"entry":100.0,"sl":98.0,"tp1":103.0,"system":"Premium"}
        candles = [
            {"ts":1_699_999_980,"high":101.0,"low":99.0,"close":100.5},
            {"ts":1_700_000_040,"high":103.2,"low":100.0,"close":102.8},
        ]
        out = pulse.analyze_first_15m(trade, candles, now_ts=1_700_000_120, source="TEST")
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["mfe_r"], 1.6)
        self.assertAlmostEqual(out["mae_r"], -0.5)
        self.assertEqual(out["first_event"], "TP1_FIRST")
        self.assertAlmostEqual(out["window_close_r"], 1.4)

    def test_short_first_15m_direction_math_and_sl_first(self):
        trade = {"direction":"SHORT","opened_at":1_700_000_000,"entry":100.0,"sl":102.0,"tp1":97.0,"system":"Scalp"}
        candles = [
            {"ts":1_699_999_980,"high":101.0,"low":98.0,"close":99.0},
            {"ts":1_700_000_040,"high":102.2,"low":98.5,"close":101.5},
        ]
        out = pulse.analyze_first_15m(trade, candles, now_ts=1_700_000_120, source="TEST")
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["mfe_r"], 1.0)
        self.assertAlmostEqual(out["mae_r"], -1.1)
        self.assertEqual(out["first_event"], "SL_FIRST")
        self.assertAlmostEqual(out["window_close_r"], -0.75)

    def test_same_candle_tp_and_sl_is_marked_ambiguous(self):
        trade = {"direction":"LONG","opened_at":1_700_000_000,"entry":100.0,"sl":98.0,"tp1":102.0}
        candles = [{"ts":1_699_999_980,"high":102.2,"low":97.8,"close":100.1}]
        out = pulse.analyze_first_15m(trade, candles, now_ts=1_700_000_040)
        self.assertEqual(out["first_event"], "TP1_SL_SAME_CANDLE")
        self.assertTrue(out["first_event_candle_ambiguous"])

    def test_missing_levels_fail_closed(self):
        out = pulse.analyze_first_15m({"direction":"LONG","opened_at":1_700_000_000,"entry":100}, [], now_ts=1_700_000_010)
        self.assertEqual(out["status"], "risk_levels_missing")


class EarlyPulsePageTests(unittest.TestCase):
    def _base(self) -> str:
        raw = '<html><head><style></style></head><body><div id="lastPrice">100</div><div class="layout"></div></body></html>'
        return progress.enhance_trade_progress_page(raw, "nonce316")

    def test_page_contains_early_pulse_contract(self):
        body = pulse.enhance_early_pulse_page(self._base(), "nonce317")
        self.assertIn('id="v317EarlyPulse"', body)
        self.assertIn('id="v317Mfe"', body)
        self.assertIn('id="v317Mae"', body)
        self.assertIn('id="v317Event"', body)
        self.assertIn('id="v317CloseR"', body)
        self.assertIn('id="v317Fill"', body)
        self.assertIn('id="v317-early-pulse-script"', body)
        self.assertIn('/api/coin-center/early-pulse', body)
        self.assertIn('setInterval', body)
        self.assertIn('Salt okunur gözlem · işlem yönetimi kuralı değildir', body)
        self.assertNotIn("method:'POST'", body)
        self.assertNotIn('method:"POST"', body)
        self.assertIn('nonce="nonce317"', body)

    def test_enhancement_is_idempotent(self):
        once = pulse.enhance_early_pulse_page(self._base(), "n")
        twice = pulse.enhance_early_pulse_page(once, "n")
        self.assertEqual(once, twice)
        self.assertEqual(once.count('id="v317EarlyPulse"'), 1)

    def test_missing_v316_anchor_fails_closed(self):
        with self.assertRaises(RuntimeError):
            pulse.enhance_early_pulse_page('<html><head><style></style></head><body><div class="layout"></div></body></html>', 'n')

    def test_inline_javascript_has_valid_syntax(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node komutu bu ortamda yok")
        body = pulse.enhance_early_pulse_page(self._base(), "nonce317")
        scripts = re.findall(r"<script[^>]*>(.*?)</script>", body, flags=re.S)
        target = [script for script in scripts if '__v317EarlyPulse' in script]
        self.assertEqual(len(target), 1)
        with tempfile.TemporaryDirectory(prefix="earlypulse-js-") as directory:
            path = Path(directory) / "earlypulse.js"
            path.write_text(target[0], encoding="utf-8")
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, msg=result.stderr)


class EarlyPulseSourceTests(unittest.TestCase):
    def test_runtime_keeps_core_boundary(self):
        source = Path(pulse.__file__).read_text(encoding="utf-8")
        self.assertIn("'signal_engine':'unchanged'", source)
        self.assertIn("'telegram':'unchanged'", source)
        self.assertIn("'trade_management':'unchanged'", source)
        self.assertIn("get_candles(summary.get('symbol') or symbol, '1m', opened_at)", source)
        self.assertNotIn('strategy.py', source.split('EARLY_CSS', 1)[1])
        self.assertNotIn('main.py', source.split('EARLY_CSS', 1)[1])


if __name__ == "__main__":
    unittest.main()
