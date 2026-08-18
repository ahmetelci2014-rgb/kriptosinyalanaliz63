import json
import os
import tempfile
import unittest
from types import SimpleNamespace

import scalp_attack_guard_shadow as shadow
import scalp_live_runner as runner
import scalp_quality_config as cfg


class FakeExchange:
    def __init__(self, candles):
        self.candles = candles

    def fetch_ohlcv(self, *args, **kwargs):
        return self.candles


class ScalpQualityShadowTests(unittest.TestCase):
    def test_live_thresholds_are_single_config_values(self):
        radar = SimpleNamespace(
            ATTACK_LONG_MIN_CLOSE_POWER=62,
            ATTACK_SHORT_MAX_CLOSE_POWER=38,
        )
        runner.apply_live_thresholds(radar)
        self.assertEqual(radar.ATTACK_LONG_MIN_CLOSE_POWER, 70.0)
        self.assertEqual(radar.ATTACK_SHORT_MAX_CLOSE_POWER, 30.0)

    def test_compare_attack_returns_live_and_records_legacy_only(self):
        radar = SimpleNamespace(
            ATTACK_LONG_MIN_CLOSE_POWER=62,
            ATTACK_SHORT_MAX_CLOSE_POWER=38,
        )

        def original(*args, **kwargs):
            threshold = radar.ATTACK_LONG_MIN_CLOSE_POWER
            if threshold <= 62:
                return {"symbol": "TESTUSDT", "direction": "LONG"}, {"score": 90}
            return None, {"score": 80}

        recorded = []
        wrapped = runner.make_attack_wrapper(
            radar,
            original,
            recorder=lambda signal, legacy, live: recorded.append((signal, legacy, live)),
        )
        live_signal, live_debug = wrapped()
        self.assertIsNone(live_signal)
        self.assertEqual(live_debug["score"], 80)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(radar.ATTACK_LONG_MIN_CLOSE_POWER, cfg.LIVE_ATTACK_LONG_MIN_CLOSE_POWER)

    def test_shadow_tracks_tp3_without_auto_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            filename = os.path.join(tmp, "shadow.json")
            created = 1_800_000_000
            signal = {
                "symbol": "TESTUSDT",
                "direction": "LONG",
                "entry": 100.0,
                "sl": 99.0,
                "tp1": 100.65,
                "tp2": 101.15,
                "tp3": 101.70,
                "risk_percent": 1.0,
                "close_power": 65.0,
                "score": 90,
                "rsi1": 60,
                "rsi5": 58,
                "vol1": 2.0,
                "vol5": 1.5,
                "move1": 0.2,
                "move5": 0.5,
                "move15": 0.3,
            }
            rid = shadow.record_candidate(
                signal,
                {"score": 90},
                {"score": 80},
                filename=filename,
                current_ts=created,
            )
            self.assertTrue(rid)
            candles = [
                [(created + 60) * 1000, 100.0, 100.8, 99.8, 100.5, 1.0],
                [(created + 120) * 1000, 100.5, 101.8, 100.2, 101.7, 1.0],
            ]
            shadow.update_shadow(
                FakeExchange(candles),
                filename=filename,
                current_ts=created + 180,
            )
            with open(filename, encoding="utf-8") as handle:
                data = json.load(handle)
            record = data["records"][0]
            self.assertTrue(record["tp1_hit"])
            self.assertTrue(record["tp3_hit"])
            self.assertEqual(record["outcome"], "TP3")
            self.assertFalse(data["auto_apply"])
            self.assertFalse(data["summary"]["auto_apply"])

    def test_shadow_tracks_stop_before_tp1(self):
        with tempfile.TemporaryDirectory() as tmp:
            filename = os.path.join(tmp, "shadow.json")
            created = 1_800_000_000
            signal = {
                "symbol": "TESTUSDT",
                "direction": "SHORT",
                "entry": 100.0,
                "sl": 101.0,
                "tp1": 99.35,
                "tp2": 98.85,
                "tp3": 98.30,
                "risk_percent": 1.0,
                "close_power": 35.0,
                "score": 90,
            }
            shadow.record_candidate(
                signal,
                {"score": 90},
                {"score": 80},
                filename=filename,
                current_ts=created,
            )
            candles = [[(created + 60) * 1000, 100.0, 101.1, 99.8, 100.9, 1.0]]
            shadow.update_shadow(
                FakeExchange(candles),
                filename=filename,
                current_ts=created + 120,
            )
            with open(filename, encoding="utf-8") as handle:
                data = json.load(handle)
            self.assertEqual(data["records"][0]["outcome"], "STOP_BEFORE_TP1")


if __name__ == "__main__":
    unittest.main()
