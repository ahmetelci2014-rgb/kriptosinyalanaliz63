"""Regression tests for read-only background candidate stop/recovery observations."""
from __future__ import annotations

import market_first_candidate_recovery_shadow as rec


BASE = 1_800_000_000  # minute aligned


def _candidate(direction="LONG", policy=rec.ELIGIBLE_POLICY):
    return {
        "symbol": "TESTUSDT", "direction": direction,
        "tracking_policy": policy, "status": "CLOSED",
        "first_result": "SL_FIRST", "final_result": "SL_FIRST",
        "entry": 100, "sl": 99 if direction == "LONG" else 101,
        "tp1": 101 if direction == "LONG" else 99,
        "tp2": 102 if direction == "LONG" else 98,
        "tp3": 103 if direction == "LONG" else 97,
        "opened_at": BASE - 600, "closed_at": BASE,
    }


def _seed_one(direction="LONG"):
    source = {"trades": {"episode1": _candidate(direction)}}
    ledger = {"episodes": {}}
    assert rec.seed(source, ledger, BASE + 120) == 1
    return ledger["episodes"]["episode1"]


def test_only_validated_first_stop_candidates_seed():
    source = {"trades": {
        "old": _candidate(policy="OLDER_TRACKER"),
        "good": _candidate(),
        "late": {**_candidate(), "closed_at": BASE - 3 * 3600},
        "tp_first": {**_candidate(), "first_result": "TP1_FIRST",
                     "final_result": "SL_AFTER_TP1"},
    }}
    ledger = {"episodes": {}}
    assert rec.seed(source, ledger, BASE + 120) == 1
    assert list(ledger["episodes"]) == ["good"]
    assert rec.seed(source, ledger, BASE + 180) == 0


def test_long_stop_then_returns_to_original_entry_and_tp1():
    episode = _seed_one()
    assert episode["first_eligible_bar_at"] == BASE + 60
    rec.advance(episode, [
        {"ts": BASE, "high": 104, "low": 98},  # stop minute: MUST ignore
        {"ts": BASE + 60, "high": 99.4, "low": 98.0},
        {"ts": BASE + 120, "high": 100.2, "low": 99.4},
        {"ts": BASE + 180, "high": 101.2, "low": 100.1},
    ], BASE + 240)
    assert episode["return_to_entry_at"] == BASE + 120
    assert episode["tp1_after_sl_at"] == BASE + 180
    assert episode["tp2_after_sl_at"] is None
    assert episode["coverage_complete"] is True


def test_short_stop_then_reversal_to_short_tp3():
    episode = _seed_one("SHORT")
    rec.advance(episode, [
        {"ts": BASE + 60, "high": 102.0, "low": 101.2},
        {"ts": BASE + 120, "high": 101.0, "low": 99.9},
        {"ts": BASE + 180, "high": 100.0, "low": 96.9},
    ], BASE + 240)
    assert episode["return_to_entry_at"] == BASE + 120
    assert episode["tp1_after_sl_at"] == BASE + 180
    assert episode["tp2_after_sl_at"] == BASE + 180
    assert episode["tp3_after_sl_at"] == BASE + 180


def test_missing_minutes_flag_partial_not_loss():
    episode = _seed_one()
    rec.advance(episode, [{"ts": BASE + 180, "high": 99, "low": 98}], BASE + 240)
    assert episode["coverage_complete"] is False
    assert episode["missing_candles"] == 2
    rec.advance(episode, [], BASE + rec.TRACK_SECONDS + 60)
    assert episode["status"] == "PARTIAL_COVERAGE"
    summary = rec.summarize({"episodes": {"x": episode}}, BASE + rec.TRACK_SECONDS + 60)
    assert summary["complete"] == 0
    assert summary["confirmed_full_window_no_tp1"] == 0


def test_no_partial_or_open_record_claimed_as_confirmed_no_recovery():
    incomplete = _seed_one()
    report = rec.summarize({"episodes": {"pending": incomplete}}, BASE + 500)
    assert report["tracking"] == 1
    assert report["confirmed_full_window_no_tp1"] == 0


def test_fetch_discards_open_and_pre_stop_candles():
    class Exchange:
        def fetch_ohlcv(self, symbol, timeframe, since, limit):
            assert symbol == "TEST/USDT:USDT"
            assert timeframe == "1m"
            return [
                [(BASE + 0) * 1000, 100, 111, 98, 100, 10],
                [(BASE + 60) * 1000, 100, 100, 99, 99, 10],
                [(BASE + 120) * 1000, 99, 101, 99, 100, 10],
            ]

    candles = rec.fetch_closed_candles(Exchange(), "TESTUSDT", BASE + 60, BASE + 180)
    assert [x["ts"] for x in candles] == [BASE + 60, BASE + 120]
    assert not rec.fetch_closed_candles(Exchange(), "TESTUSDT", BASE + 60, BASE + 120)[-1]["ts"] > BASE + 60
