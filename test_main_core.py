import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


# =========================================================
# TEST İÇİN DIŞ BAĞIMLILIKLARI İZOLE ET
# =========================================================
# Testler piyasaya bağlanmaz, Telegram mesajı göndermez ve gerçek
# config/strategy modüllerini çalıştırmaz. main.py içindeki saf kayıt,
# Net R, duplicate, teşhis ve portföy risk fonksiyonlarını doğrular.

fake_ccxt = types.ModuleType("ccxt")
fake_pandas = types.ModuleType("pandas")
fake_requests = types.ModuleType("requests")

sys.modules.setdefault("ccxt", fake_ccxt)
sys.modules.setdefault("pandas", fake_pandas)
sys.modules.setdefault("requests", fake_requests)

fake_config = types.ModuleType("config")

CONFIG_VALUES = {
    "BOT_NAME": "TEST BOT",
    "SYSTEM_NOTE": "TEST",
    "AUTO_TOP_VOLUME_SCAN": True,
    "MAX_SCAN_COINS": 10,
    "MIN_24H_QUOTE_VOLUME": 0,
    "PRIORITY_COINS": [],
    "ALLOW_LONG": True,
    "ALLOW_SHORT": True,
    "MAX_TRADE_SIGNALS_PER_RUN": 2,
    "MAX_RADAR_ALERTS_PER_RUN": 1,
    "MAX_OPEN_SIGNALS": 6,
    "RISK_MODE_STOP_COUNT": 5,
    "RISK_MODE_MAX_TRADE_SIGNALS": 1,
    "RISK_MODE_MAX_RADAR_ALERTS": 0,
    "RISK_MODE_ALLOW_RADAR_TRADE": False,
    "RADAR_TIMEFRAME": "5m",
    "ENTRY_TIMEFRAME": "15m",
    "CONFIRM_TIMEFRAME": "1h",
    "TREND_TIMEFRAME": "4h",
    "TRACK_TIMEFRAME": "1m",
    "RADAR_LIMIT": 220,
    "ENTRY_LIMIT": 220,
    "CONFIRM_LIMIT": 220,
    "TREND_LIMIT": 220,
    "TRACK_LIMIT": 180,
    "MAX_ENTRY_DISTANCE_PERCENT": 0.30,
    "MAX_TP1_PROGRESS_PERCENT": 45,
    "MARKET_GUARD_ENABLED": True,
    "MARKET_REFERENCE_COINS": [],
    "MARKET_LONG_MIN_OK_COUNT": 2,
    "MARKET_SHORT_MIN_OK_COUNT": 2,
    "MARKET_MAX_COUNTER_5M_MOVE_PERCENT": 1.0,
    "TRADE_DUPLICATE_BLOCK_SECONDS": 5400,
    "RADAR_DUPLICATE_BLOCK_SECONDS": 5400,
    "STOPPED_COIN_COOLDOWN_HOURS": 6,
    "MAX_OPEN_SIGNAL_HOURS": 18,
    "SEND_STATUS_EVERY_MINUTES": 60,
    "OPEN_SUMMARY_EVERY_MINUTES": 60,
    "DAILY_REPORT_HOUR": 23,
    "DAILY_REPORT_MINUTE": 45,
}

for name, value in CONFIG_VALUES.items():
    setattr(fake_config, name, value)

sys.modules["config"] = fake_config

fake_strategy = types.ModuleType("strategy")
fake_strategy.analyze_mtf_trade = lambda *args, **kwargs: None
fake_strategy.analyze_5m_radar = lambda *args, **kwargs: None
fake_strategy.format_price = lambda value: str(value)
sys.modules["strategy"] = fake_strategy

import main
import portfolio_risk


class JsonSafetyTests(unittest.TestCase):
    def test_atomic_json_write_and_replace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            filename = os.path.join(temp_dir, "trade_ledger.json")
            first_data = {
                "version": 1,
                "trades": {"TEST_LONG": {"status": "OPEN"}},
            }
            second_data = {
                "version": 2,
                "trades": {
                    "TEST_LONG": {
                        "status": "CLOSED",
                        "final_result": "TP3",
                    }
                },
            }

            self.assertTrue(main.save_json_file(filename, first_data))
            self.assertTrue(main.save_json_file(filename, second_data))

            with open(filename, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)

            self.assertEqual(loaded, second_data)
            remaining_temp_files = [
                name
                for name in os.listdir(temp_dir)
                if name.endswith(".tmp")
            ]
            self.assertEqual(remaining_temp_files, [])


class NetRTests(unittest.TestCase):
    def test_long_exit_r_without_tp1(self):
        signal = {
            "direction": "LONG",
            "entry": 100.0,
            "sl": 98.0,
            "tp1_hit": False,
        }
        self.assertEqual(main.calculate_exit_r(signal, 102.0), 1.0)

    def test_short_exit_r_without_tp1(self):
        signal = {
            "direction": "SHORT",
            "entry": 100.0,
            "sl": 102.0,
            "tp1_hit": False,
        }
        self.assertEqual(main.calculate_exit_r(signal, 98.0), 1.0)

    def test_tp1_partial_then_break_even(self):
        signal = {
            "direction": "LONG",
            "entry": 100.0,
            "sl": 98.0,
            "tp1": 101.0,
            "tp1_hit": True,
        }
        # TP1 = +0.50R. Pozisyonun yarısı TP1, kalan yarısı giriş:
        # toplam sonuç +0.25R.
        self.assertEqual(main.calculate_exit_r(signal, 100.0), 0.25)


class DuplicateTargetGuardTests(unittest.TestCase):
    def test_existing_tp1_event_is_detected(self):
        signal = {"trade_id": "ENAUSDT_LONG_15M_ENTRY_1"}
        ledger = {
            "trades": {
                signal["trade_id"]: {
                    "events": [
                        {"event": "OPENED", "time": 100},
                        {"event": "TP1", "time": 200},
                    ]
                }
            }
        }

        with patch.object(main, "load_trade_ledger", return_value=ledger):
            exists, event_time = main.ledger_event_info(signal, "TP1")

        self.assertTrue(exists)
        self.assertEqual(event_time, 200)

    def test_missing_tp2_event_is_not_detected(self):
        signal = {"trade_id": "ENAUSDT_LONG_15M_ENTRY_1"}
        ledger = {
            "trades": {
                signal["trade_id"]: {
                    "events": [{"event": "TP1", "time": 200}]
                }
            }
        }

        with patch.object(main, "load_trade_ledger", return_value=ledger):
            exists, event_time = main.ledger_event_info(signal, "TP2")

        self.assertFalse(exists)
        self.assertIsNone(event_time)


class StopRootCauseTests(unittest.TestCase):
    def test_fitil_or_tight_stop_after_target_return(self):
        trade = {
            "final_result": "SL",
            "duration_minutes": 100,
            "best_favorable_r": 0.20,
            "worst_adverse_r": 1.00,
            "risk_percent": 1.20,
            "post_stop_follow": {
                "status": "RETURNED_TO_TARGET",
                "returned_level": "TP1",
                "age_minutes": 141,
            },
        }

        result = main.classify_stop_root_cause(trade)
        self.assertEqual(result["primary"], "FITIL_DAR_STOP")
        self.assertFalse(result["provisional"])

    def test_probable_early_entry(self):
        trade = {
            "final_result": "SL",
            "duration_minutes": 20,
            "best_favorable_r": 0.05,
            "worst_adverse_r": 1.10,
            "risk_percent": 1.10,
            "source": "5M_RADAR",
            "adx_15m": 16.0,
            "post_stop_follow": {
                "status": "RETURNED_TO_TARGET",
                "returned_level": "TP1",
                "age_minutes": 60,
            },
        }

        result = main.classify_stop_root_cause(trade)
        self.assertEqual(result["primary"], "MUHTEMEL_ERKEN_GIRIS")

    def test_probable_wrong_direction(self):
        trade = {
            "final_result": "SL",
            "duration_minutes": 18,
            "best_favorable_r": 0.03,
            "worst_adverse_r": 1.10,
            "post_stop_follow": {
                "status": "NO_TP1_RETURN",
                "age_minutes": 240,
            },
        }

        result = main.classify_stop_root_cause(trade)
        self.assertEqual(result["primary"], "MUHTEMEL_YANLIS_YON")

    def test_following_stop_remains_provisional(self):
        trade = {
            "final_result": "SL",
            "duration_minutes": 25,
            "best_favorable_r": 0.05,
            "worst_adverse_r": 1.0,
            "post_stop_follow": {
                "status": "TRACKING",
                "age_minutes": 60,
            },
        }

        result = main.classify_stop_root_cause(trade)
        self.assertEqual(result["primary"], "TAKIP_SURUYOR")
        self.assertTrue(result["provisional"])


class VersionMetadataTests(unittest.TestCase):
    def test_github_commit_metadata(self):
        environment = {
            "GITHUB_SHA": "1234567890abcdef1234567890abcdef12345678",
            "GITHUB_RUN_ID": "98765",
            "GITHUB_RUN_NUMBER": "42",
            "GITHUB_WORKFLOW": "Premium MTF Futures Bot",
            "GITHUB_REF_NAME": "main",
        }

        with patch.dict(os.environ, environment, clear=False):
            metadata = main.build_runtime_version_metadata()

        self.assertEqual(metadata["git_sha"], environment["GITHUB_SHA"])
        self.assertEqual(
            metadata["git_sha_short"],
            environment["GITHUB_SHA"][:12],
        )
        self.assertEqual(metadata["github_run_number"], "42")


class PortfolioRiskTests(unittest.TestCase):
    def build_sources(
        self,
        temp_dir,
        main_signals=None,
        scalp_signals=None,
        pump_signals=None,
        swing_signals=None,
    ):
        paths = {
            "main": os.path.join(temp_dir, "open_signals.json"),
            "scalp": os.path.join(temp_dir, "scalp_radar_state.json"),
            "pump": os.path.join(temp_dir, "pump_radar_state.json"),
            "swing": os.path.join(temp_dir, "swing_radar_state.json"),
        }

        with open(paths["main"], "w", encoding="utf-8") as handle:
            json.dump(main_signals or {}, handle)

        with open(paths["scalp"], "w", encoding="utf-8") as handle:
            json.dump(
                {"open_scalp_signals": scalp_signals or {}},
                handle,
            )

        with open(paths["pump"], "w", encoding="utf-8") as handle:
            json.dump({"open_signals": pump_signals or {}}, handle)

        with open(paths["swing"], "w", encoding="utf-8") as handle:
            json.dump(
                {"open_swing_signals": swing_signals or {}},
                handle,
            )

        return {
            "MAIN_MTF": {
                "filename": paths["main"],
                "containers": [None],
            },
            "SCALP": {
                "filename": paths["scalp"],
                "containers": ["open_scalp_signals"],
            },
            "PUMP_DUMP": {
                "filename": paths["pump"],
                "containers": ["open_signals"],
            },
            "SWING": {
                "filename": paths["swing"],
                "containers": ["open_swing_signals"],
            },
        }

    def evaluate(
        self,
        symbol,
        direction,
        sources,
        max_direction_risk=4.0,
        max_total_risk=8.0,
    ):
        return portfolio_risk.evaluate_portfolio_risk(
            symbol,
            direction,
            "MAIN_MTF",
            state_sources=sources,
            max_direction_risk=max_direction_risk,
            max_total_risk=max_total_risk,
            record_shadow=False,
        )

    def test_same_coin_same_direction_blocks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sources = self.build_sources(
                temp_dir,
                scalp_signals={
                    "ENA_LONG": {
                        "symbol": "ENAUSDT",
                        "direction": "LONG",
                        "closed": False,
                    }
                },
            )
            result = self.evaluate("ENAUSDT", "LONG", sources)

        self.assertTrue(result["hard_block"])
        self.assertEqual(
            result["block_code"],
            "SAME_COIN_SAME_DIRECTION",
        )

    def test_same_coin_opposite_direction_blocks(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sources = self.build_sources(
                temp_dir,
                swing_signals={
                    "ENA_SHORT": {
                        "symbol": "ENA/USDT:USDT",
                        "direction": "SHORT",
                        "closed": False,
                    }
                },
            )
            result = self.evaluate("ENAUSDT", "LONG", sources)

        self.assertTrue(result["hard_block"])
        self.assertEqual(
            result["block_code"],
            "SAME_COIN_OPPOSITE_DIRECTION",
        )

    def test_tp1_signal_counts_half_risk(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            sources = self.build_sources(
                temp_dir,
                main_signals={
                    "BTC_LONG": {
                        "symbol": "BTCUSDT",
                        "direction": "LONG",
                        "tp1_hit": True,
                        "closed": False,
                    }
                },
            )
            result = self.evaluate("ETHUSDT", "LONG", sources)

        self.assertEqual(result["direction_risk_before"], 0.5)
        self.assertEqual(result["direction_risk_after"], 1.5)

    def test_direction_limit_exactly_four_is_soft_warning(self):
        # Üç açık LONG + yeni aday = 4.0. Sınır aşılmaz;
        # aday geçer fakat yoğunluk uyarısı oluşur.
        with tempfile.TemporaryDirectory() as temp_dir:
            sources = self.build_sources(
                temp_dir,
                main_signals={
                    "BTC_LONG": {
                        "symbol": "BTCUSDT",
                        "direction": "LONG",
                    },
                    "ETH_LONG": {
                        "symbol": "ETHUSDT",
                        "direction": "LONG",
                    },
                },
                scalp_signals={
                    "SOL_LONG": {
                        "symbol": "SOLUSDT",
                        "direction": "LONG",
                    },
                },
            )
            result = self.evaluate(
                "ADAUSDT",
                "LONG",
                sources,
                max_direction_risk=4.0,
            )

        self.assertFalse(result["hard_block"])
        self.assertTrue(result["has_soft_warning"])
        self.assertEqual(result["direction_risk_after"], 4.0)

    def test_direction_limit_above_four_hard_blocks(self):
        # Dört açık LONG + yeni aday = 5.0. V3 hard-cap gereği engellenir.
        with tempfile.TemporaryDirectory() as temp_dir:
            sources = self.build_sources(
                temp_dir,
                main_signals={
                    "BTC_LONG": {
                        "symbol": "BTCUSDT",
                        "direction": "LONG",
                    },
                    "ETH_LONG": {
                        "symbol": "ETHUSDT",
                        "direction": "LONG",
                    },
                },
                scalp_signals={
                    "SOL_LONG": {
                        "symbol": "SOLUSDT",
                        "direction": "LONG",
                    },
                    "XRP_LONG": {
                        "symbol": "XRPUSDT",
                        "direction": "LONG",
                    },
                },
            )
            result = self.evaluate(
                "ADAUSDT",
                "LONG",
                sources,
                max_direction_risk=4.0,
            )

        self.assertTrue(result["hard_block"])
        self.assertEqual(result["block_code"], "DIRECTION_RISK_LIMIT")
        self.assertEqual(result["direction_risk_after"], 5.0)


if __name__ == "__main__":
    unittest.main()
