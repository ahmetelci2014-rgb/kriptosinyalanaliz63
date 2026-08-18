from __future__ import annotations

import time
import unittest

import pandas as pd

import big_move_route as route
import big_move_route_runner as route_runner


class BigMoveRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Workflow gerçekte runner üzerinden çalışır; test de aynı hedef sıralama
        # korumasını kullanmalı.
        route_runner.apply_target_order_guard()

    def _frame(self, count: int = 120) -> pd.DataFrame:
        rows = []
        for i in range(count):
            close = 100.0 + i * 0.08
            rows.append({
                "ts": 1_700_000_000_000 + i * 15 * 60 * 1000,
                "open": close - 0.30,
                "high": close + (1.20 if i % 12 == 6 else 0.45),
                "low": close - 0.45,
                "close": close,
                "ema20": close - 0.20,
                "ema50": close - 0.80,
                "ema200": close - 2.00,
                "rsi14": 57.0,
                "atr14": 1.0,
                "adx14": 30.0,
                "volume_ratio": 1.20,
            })
        return pd.DataFrame(rows)

    def test_confirm_15m_long(self):
        frame = self._frame(80)
        ok, detail = route.confirm_15m(frame, "LONG")
        self.assertTrue(ok, detail)
        self.assertEqual(detail["reason"], "OK")

    def test_route_projection_has_big_target(self):
        h4 = self._frame(120)
        # Sentetik giriş, yakın 4H engel filtresini yanlışlıkla tetiklemeyecek
        # şekilde kurulmuştur. Amaç burada strateji filtresini değil, runtime
        # hedef sıralamasını doğrulamaktır.
        projection = route.build_route_projection(
            h4,
            "LONG",
            entry=100.0,
            stop=99.8,
        )
        self.assertIsNotNone(projection)
        self.assertGreaterEqual(projection["main_target_r"], 3.0)
        self.assertGreater(projection["tp2"], 100.0)
        self.assertLess(projection["tp1"], projection["tp2"])
        self.assertGreater(projection["tp3"], projection["tp2"])

    def test_nearby_h4_obstacle_is_rejected(self):
        h4 = self._frame(120)
        projection = route_runner.base.build_route_projection(
            h4,
            "LONG",
            entry=108.0,
            stop=107.0,
        )
        self.assertIsNone(projection)

    def test_entry_zone_contains_entry(self):
        low, high = route.build_entry_zone(100.0, 2.0)
        self.assertLess(low, 100.0)
        self.assertGreater(high, 100.0)

    def test_signal_message_is_explicit(self):
        signal = {
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry_zone_low": 100.0,
            "entry_zone_high": 101.0,
            "entry": 100.5,
            "stop": 98.0,
            "risk_percent": 2.49,
            "tp1": 104.0,
            "tp2": 108.0,
            "tp3": 113.0,
            "tp1_r": 1.4,
            "main_target_r": 3.0,
            "extended_target_r": 5.0,
            "main_target_zone_low": 107.5,
            "main_target_zone_high": 108.5,
            "potential_percent": 7.46,
            "setup_type": "PULLBACK_PLUS_RETEST",
            "score": 96,
            "h4_adx": 31.0,
            "m15_adx": 24.0,
            "m15_volume_ratio": 1.3,
        }
        message = route.signal_message(signal)
        self.assertIn("GİRİŞ ONAYLANDI", message)
        self.assertIn("ANA hedef", message)
        self.assertIn("BTCUSDT", message)
        self.assertIn("olasılıklı fiyat rotası", message)

    def test_cooldown(self):
        state = route.empty_state()
        key = route.route_key("BTCUSDT", "LONG")
        state["last_signal_by_symbol_direction"][key] = int(time.time())
        self.assertFalse(route.cooldown_ok(state, "BTCUSDT", "LONG"))
        self.assertTrue(route.cooldown_ok(state, "BTCUSDT", "SHORT"))


if __name__ == "__main__":
    unittest.main()
