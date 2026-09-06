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


def test_background_good_move_without_real_entry_is_not_profit():
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
    assert row["result"] == "DOĞRU YÖN / GİRİŞ YOK"
    text = "\n".join(report.format_report(payload))
    assert "XPLUSDT SHORT | +4.9% | DOĞRU YÖN / GİRİŞ YOK" in text
    assert "kâr" in text.lower()


def test_background_sl_first_is_direction_wrong():
    bot = FakeBot({
        "trade_ledger.json": {}, "open_signals.json": {},
        "market_first_entry_plan_ledger.json": {"episodes": {
            "v": {
                "symbol": "VIRTUALUSDT", "direction": "LONG", "first_at": ts(13),
                "best_favorable_percent": 0.2, "worst_adverse_percent": 1.4,
                "first_decisive_event": "SL_FIRST", "outcome": "SL_FIRST", "resolved": True,
            }
        }},
        "market_first_swing_2h_ledger.json": {"episodes": {}},
        "market_first_early_ledger.json": {"episodes": {}},
    })
    payload = report.build_report(bot, now=ts(23, 45))
    assert payload["background"][0]["result"] == "YÖN TERS"


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
                "result": "DOĞRU YÖN / GİRİŞ YOK",
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


def test_live_workflow_stays_single_15m_job_and_persists_daily_files():
    text = Path(".github/workflows/main.yml").read_text(encoding="utf-8")
    assert 'cron: "*/15 * * * *"' in text
    assert text.count("python market_first_live_simple.py") == 1
    assert "sleep 300" not in text
    assert "market_first_daily_report_state.json" in text
    assert "market_first_daily_report.json" in text
