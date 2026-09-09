from datetime import datetime

import market_first_daily_report as report
import market_first_daily_report_origin_patch as patch


class FakeBot:
    TRADE_LEDGER_FILE = "trade_ledger.json"
    OPEN_SIGNALS_FILE = "open_signals.json"

    def __init__(self, files=None):
        self.files = dict(files or {})

    def load_json_file(self, name, default):
        return self.files.get(name, default)

    def save_json_file(self, name, payload):
        self.files[name] = payload


def ts(day, hour=12, minute=0):
    return int(datetime(2026, 9, day, hour, minute, tzinfo=report.TRT).timestamp())


def base_files():
    return {
        "trade_ledger.json": {},
        "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {"episodes": {}},
        "market_first_swing_2h_ledger.json": {"episodes": {}},
        "market_first_early_ledger.json": {"episodes": {}},
        "market_first_entry_condition_diagnosis.json": {},
    }


def test_old_background_episode_is_marked_carryover_not_same_day_candidate():
    files = base_files()
    files["market_first_swing_2h_ledger.json"] = {"episodes": {
        "vvv": {
            "episode_id": "VVVUSDT:LONG:old",
            "symbol": "VVVUSDT",
            "direction": "LONG",
            "first_at": ts(7, 18, 46),
            "updated_at": ts(9, 6, 32),
            "best_favorable_percent": 49.721,
            "worst_adverse_percent": 0.8803,
            "first_decisive_event": "TP1_FIRST",
            "tp1_at": ts(7, 19, 16),
            "resolved": True,
        },
        "new": {
            "episode_id": "ENSUSDT:SHORT:new",
            "symbol": "ENSUSDT",
            "direction": "SHORT",
            "first_at": ts(9, 9, 31),
            "best_favorable_percent": 5.66,
            "worst_adverse_percent": 1.42,
            "first_decisive_event": "TP1_FIRST",
            "tp1_at": ts(9, 10, 0),
            "resolved": True,
        },
    }}
    payload = patch.build_report_enriched(FakeBot(files), now=ts(9, 23, 45))
    rows = {(x["symbol"], x["direction"]): x for x in payload["background"]}
    assert rows[("VVVUSDT", "LONG")]["origin_date"] == "2026-09-07"
    assert rows[("VVVUSDT", "LONG")]["is_carryover"] is True
    assert rows[("ENSUSDT", "SHORT")]["origin_date"] == "2026-09-09"
    assert rows[("ENSUSDT", "SHORT")]["is_carryover"] is False
    assert payload["summary"]["background_new_count"] == 1
    assert payload["summary"]["background_carryover_count"] == 1
    assert payload["background"][0]["symbol"] == "ENSUSDT"
    assert "ESKİ ADAY 07.09" in patch._row_line_enriched(rows[("VVVUSDT", "LONG")])


def test_tp1_be_post_result_shadow_is_exposed_in_daily_report():
    files = base_files()
    files["trade_ledger.json"] = {"trades": {
        "a": {
            "symbol": "AAVEUSDT",
            "direction": "SHORT",
            "first_at": ts(9, 15),
            "closed_at": ts(9, 17),
            "entry": 100.0,
            "sl": 101.0,
            "best_favorable_percent": 0.4,
            "worst_adverse_percent": 0.2,
            "final_result": "TP1_SONRASI_BE",
            "closed": True,
            "post_result_shadow": {
                "status": "COMPLETED",
                "reached_levels": {
                    "TP2": {"first_reached_at": ts(9, 18)}
                },
                "max_favorable_percent": 1.25,
                "max_adverse_percent": 0.31,
            },
        }
    }}
    payload = patch.build_report_enriched(FakeBot(files), now=ts(9, 23, 45))
    row = payload["real_trades"][0]
    assert row["result"] == "TP1 + BE"
    assert row["diagnosis"] == "BE SONRASI TP2"
    assert row["post_be_reached_levels"] == ["TP2"]
    assert row["post_be_max_favorable_percent"] == 1.25
    assert payload["summary"]["be_after_tp2"] == 1
    assert payload["summary"]["be_after_tp3"] == 0


def test_tp1_be_without_shadow_data_is_explicitly_marked_waiting():
    files = base_files()
    files["trade_ledger.json"] = {"trades": {
        "a": {
            "symbol": "AXSUSDT",
            "direction": "SHORT",
            "first_at": ts(9, 15),
            "closed_at": ts(9, 16),
            "best_favorable_percent": 0.4,
            "worst_adverse_percent": 0.2,
            "final_result": "TP1_SONRASI_BE",
            "closed": True,
        }
    }}
    payload = patch.build_report_enriched(FakeBot(files), now=ts(9, 23, 45))
    row = payload["real_trades"][0]
    assert row["diagnosis"] == "BE SONRASI VERİ BEKLİYOR"
    assert payload["summary"]["be_without_follow_data"] == 1
