from datetime import datetime
from pathlib import Path

import market_first_daily_report as report
import market_first_simple_mode as simple_mode


class FakeBot:
    TRADE_LEDGER_FILE = "trade_ledger.json"
    OPEN_SIGNALS_FILE = "open_signals.json"

    def __init__(self, files=None):
        self.files = dict(files or {})

    def load_json_file(self, name, default):
        return self.files.get(name, default)

    def save_json_file(self, name, payload):
        self.files[name] = payload


def ts(hour=12, minute=0):
    return int(datetime(2026, 9, 6, hour, minute, tzinfo=report.TRT).timestamp())


def test_real_trade_and_background_are_separate_without_double_counting():
    bot = FakeBot({
        "trade_ledger.json": {
            "trades": {
                "real": {
                    "symbol": "AAVEUSDT", "direction": "LONG", "first_at": ts(10),
                    "best_favorable_percent": 4.3, "worst_adverse_percent": 0.8,
                    "final_result": "TP2", "closed": True,
                }
            }
        },
        "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {
            "episodes": {
                "same": {
                    "symbol": "AAVEUSDT", "direction": "LONG", "first_at": ts(9),
                    "best_favorable_percent": 5.0, "outcome": "TP1_FIRST", "tp1_at": ts(11),
                },
                "background": {
                    "symbol": "ARBUSDT", "direction": "LONG", "first_at": ts(9),
                    "best_favorable_percent": 11.8, "outcome": "NO_ENTRY", "resolved": True,
                },
            }
        },
        "market_first_swing_2h_ledger.json": {"episodes": {}},
        "market_first_early_ledger.json": {"episodes": {}},
    })
    payload = report.build_report(bot, now=ts(23, 45))
    assert [(x["symbol"], x["direction"]) for x in payload["real_trades"]] == [("AAVEUSDT", "LONG")]
    assert [(x["symbol"], x["direction"]) for x in payload["background"]] == [("ARBUSDT", "LONG")]
    assert payload["real_trades"][0]["result"] == "TP2"


def test_background_good_move_without_event_order_is_not_overstated():
    bot = FakeBot({
        "trade_ledger.json": {}, "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {"episodes": {}},
        "market_first_swing_2h_ledger.json": {"episodes": {}},
        "market_first_early_ledger.json": {"episodes": {
            "x": {
                "symbol": "XPLUSDT", "direction": "SHORT", "alert_time": ts(8),
                "best_favorable_percent": 4.9, "worst_adverse_percent": 0.7,
                "outcome": "GOOD_MOVE", "resolved": True,
            }
        }},
    })
    payload = report.build_report(bot, now=ts(23, 45))
    row = payload["background"][0]
    assert row["result"] == "LEHTE HAREKET / SIRA BELİRSİZ"
    assert row["first_touch"] == "UNKNOWN"
    text = "\n".join(report.format_report(payload))
    assert "XPLUSDT SHORT | +4.9% | LEHTE HAREKET / SIRA BELİRSİZ" in text
    assert "kâr" in text.lower()


def test_background_tp_first_is_strict_missed_entry():
    bot = FakeBot({
        "trade_ledger.json": {}, "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {"episodes": {
            "x": {
                "episode_id": "ENSUSDT:SHORT:1",
                "symbol": "ENSUSDT", "direction": "SHORT", "first_at": ts(9),
                "best_favorable_percent": 3.2, "worst_adverse_percent": 0.5,
                "first_decisive_event": "TP1_FIRST", "tp1_at": ts(9, 30),
                "resolved": True,
            }
        }},
        "market_first_swing_2h_ledger.json": {"episodes": {}},
        "market_first_early_ledger.json": {"episodes": {}},
    })
    payload = report.build_report(bot, now=ts(23, 45))
    row = payload["background"][0]
    assert row["first_touch"] == "TP_FIRST"
    assert row["result"] == "TP ÖNCE / GİRİŞ YOK"
    assert payload["summary"]["background_tp_first"] == 1


def test_background_sl_first_is_explicit_not_labeled_success():
    bot = FakeBot({
        "trade_ledger.json": {}, "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {"episodes": {
            "v": {
                "symbol": "VIRTUALUSDT", "direction": "LONG", "first_at": ts(13),
                "best_favorable_percent": 2.2, "worst_adverse_percent": 1.4,
                "first_decisive_event": "SL_FIRST", "outcome": "SL_FIRST_THEN_TP2_RECOVERY",
                "resolved": True,
            }
        }},
        "market_first_swing_2h_ledger.json": {"episodes": {}},
        "market_first_early_ledger.json": {"episodes": {}},
    })
    payload = report.build_report(bot, now=ts(23, 45))
    row = payload["background"][0]
    assert row["first_touch"] == "SL_FIRST"
    assert row["result"] == "SL ÖNCE / GİRİŞ YOK"
    assert payload["summary"]["background_sl_first"] == 1


def test_merge_keeps_favorable_and_adverse_from_same_representative_episode():
    bot = FakeBot({
        "trade_ledger.json": {}, "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {"episodes": {
            "clean": {
                "episode_id": "PONS:SHORT:CLEAN",
                "symbol": "PONSUSDT", "direction": "SHORT", "first_at": ts(9),
                "best_favorable_percent": 4.0, "worst_adverse_percent": 0.6,
                "first_decisive_event": "TP1_FIRST", "resolved": True,
            }
        }},
        "market_first_swing_2h_ledger.json": {"episodes": {
            "wild": {
                "episode_id": "PONS:SHORT:WILD",
                "symbol": "PONSUSDT", "direction": "SHORT", "first_at": ts(10),
                "best_favorable_percent": 8.4, "worst_adverse_percent": 7.8,
                "outcome": "GOOD_MOVE", "resolved": True,
            }
        }},
        "market_first_early_ledger.json": {"episodes": {}},
    })
    payload = report.build_report(bot, now=ts(23, 45))
    row = payload["background"][0]
    assert row["episode_id"] == "PONS:SHORT:CLEAN"
    assert row["favorable_percent"] == 4.0
    assert row["adverse_percent"] == 0.6
    assert row["episode_count"] == 2
    assert set(row["sources"]) == {"ENTRY_PLAN", "SWING_2H"}


def test_diagnosis_reason_is_carried_into_background_json():
    bot = FakeBot({
        "trade_ledger.json": {}, "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {"episodes": {
            "x": {
                "episode_id": "ENSUSDT:SHORT:123",
                "symbol": "ENSUSDT", "direction": "SHORT", "first_at": ts(9),
                "best_favorable_percent": 2.0, "worst_adverse_percent": 0.3,
                "first_decisive_event": "TP1_FIRST", "resolved": True,
            }
        }},
        "market_first_swing_2h_ledger.json": {"episodes": {}},
        "market_first_early_ledger.json": {"episodes": {}},
        "market_first_entry_condition_diagnosis.json": {
            "items": [{
                "episode_id": "ENSUSDT:SHORT:123",
                "symbol": "ENSUSDT", "direction": "SHORT", "first_at": ts(9),
                "primary_obstacle": "VOLUME_5M_BELOW_0_50_LATEST",
                "outcome": "TP3_REACHED",
            }]
        },
    })
    payload = report.build_report(bot, now=ts(23, 45))
    row = payload["background"][0]
    assert row["rejection_reason"] == "VOLUME_5M_BELOW_0_50_LATEST"
    assert row["diagnostic_outcome"] == "TP3_REACHED"


def test_real_stop_diagnosis_splits_positive_then_stop():
    bot = FakeBot({
        "trade_ledger.json": {"trades": {
            "a": {
                "symbol": "JTOUSDT", "direction": "LONG", "first_at": ts(10),
                "best_favorable_percent": 0.7, "worst_adverse_percent": 1.0,
                "final_result": "STOP", "closed": True,
            },
            "b": {
                "symbol": "BNBUSDT", "direction": "LONG", "first_at": ts(11),
                "best_favorable_percent": 0.0, "worst_adverse_percent": 0.8,
                "final_result": "STOP", "closed": True,
            },
        }},
        "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {"episodes": {}},
        "market_first_swing_2h_ledger.json": {"episodes": {}},
        "market_first_early_ledger.json": {"episodes": {}},
    })
    payload = report.build_report(bot, now=ts(23, 45))
    rows = {row["symbol"]: row for row in payload["real_trades"]}
    assert rows["JTOUSDT"]["diagnosis"] == "KÂR GÖRDÜ → STOP"
    assert rows["BNBUSDT"]["diagnosis"] == "DİREKT/ZAYIF STOP"
    assert payload["summary"]["real_stop_after_positive_move"] == 1


def test_report_waits_until_2345_turkiye_time():
    bot = FakeBot({})
    sent = []
    assert report.maybe_send(bot, lambda text, delivery_key=None: sent.append(text) or True, now=ts(23, 30)) is False
    assert sent == []


def test_report_sends_only_once_per_day_and_persists_state():
    bot = FakeBot({})
    sent = []

    def sender(text, delivery_key=None):
        sent.append((text, delivery_key))
        return True

    assert report.maybe_send(bot, sender, now=ts(23, 45)) is True
    first_count = len(sent)
    assert first_count >= 1
    assert bot.files[report.STATE_FILE]["last_sent_date"] == "2026-09-06"
    assert report.REPORT_FILE in bot.files
    assert report.maybe_send(bot, sender, now=ts(23, 59)) is False
    assert len(sent) == first_count


def test_long_report_chunks_without_dropping_rows():
    payload = {
        "date": "2026-09-06",
        "real_trades": [],
        "background": [
            {
                "symbol": f"COIN{i:03d}USDT", "direction": "LONG",
                "favorable_percent": float(i % 20), "adverse_percent": 0.5,
                "result": "LEHTE HAREKET / SIRA BELİRSİZ",
            }
            for i in range(180)
        ],
        "summary": {"real_trade_count": 0, "background_count": 180},
    }
    chunks = report.format_report(payload)
    assert len(chunks) > 1
    joined = "\n".join(chunks)
    for i in range(180):
        assert f"COIN{i:03d}USDT" in joined
    assert all(len(chunk) <= report.CHUNK_LIMIT + 80 for chunk in chunks)


def test_daily_report_is_not_suppressed_by_simple_telegram_mode():
    assert simple_mode.should_suppress("📋 GÜNLÜK İŞLEM ÖZETİ | 06.09.2026") is False


def test_live_workflow_stays_single_external_5m_job_and_persists_daily_files():
    text = Path(".github/workflows/main.yml").read_text(encoding="utf-8")
    # cron-job.org is the single 5-minute scheduler; GitHub keeps only the
    # workflow_dispatch endpoint so native schedule cannot collide with it.
    assert "workflow_dispatch:" in text
    assert "schedule:" not in text
    assert "cron:" not in text
    assert text.count("python market_first_live_simple.py") == 1
    assert "sleep 300" not in text
    assert "python post_result_shadow.py || true" in text
    assert "market_first_daily_report_state.json" in text
    assert "market_first_daily_report.json" in text
