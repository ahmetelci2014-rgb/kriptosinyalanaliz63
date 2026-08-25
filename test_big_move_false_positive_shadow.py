from __future__ import annotations

import big_move_false_positive_shadow as shadow


def _record(direction="LONG"):
    if direction == "LONG":
        return {
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "started_at": 1_000,
            "entry": 100.0,
            "tp1": 100.8,
            "tp2": 101.6,
            "tp3": 103.0,
            "sl": 99.0,
            "status": "TRACKING",
            "tp1_hit": False,
            "tp2_hit": False,
            "tp3_hit": False,
        }
    return {
        "symbol": "TESTUSDT",
        "direction": "SHORT",
        "started_at": 1_000,
        "entry": 100.0,
        "tp1": 99.2,
        "tp2": 98.4,
        "tp3": 97.0,
        "sl": 101.0,
        "status": "TRACKING",
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
    }


def _candle(at, high, low, close, open_=100.0):
    return {
        "time": at,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def test_long_sl_before_tp1_is_false_positive():
    record = _record("LONG")
    changed = shadow._simulate(
        record,
        [_candle(1_200, high=100.3, low=98.8, close=99.2)],
        now_value=1_300,
    )

    assert changed is True
    assert record["status"] == "RESOLVED"
    assert record["final_result"] == "SL"
    assert record["classification"] == "FALSE_POSITIVE_SL"
    assert record["r_result"] == -1.0


def test_long_tp1_then_next_candle_be_is_partial():
    record = _record("LONG")
    shadow._simulate(
        record,
        [
            _candle(1_200, high=101.0, low=99.4, close=100.6),
            _candle(1_500, high=100.9, low=99.8, close=100.1),
        ],
        now_value=1_600,
    )

    assert record["status"] == "RESOLVED"
    assert record["tp1_hit"] is True
    assert record["final_result"] == "TP1_SONRASI_BE"
    assert record["classification"] == "PARTIAL_THEN_BE"
    assert record["r_result"] == 0.4


def test_short_candidate_can_reach_tp3():
    record = _record("SHORT")
    shadow._simulate(
        record,
        [_candle(1_200, high=100.2, low=96.8, close=97.5)],
        now_value=1_300,
    )

    assert record["status"] == "RESOLVED"
    assert record["tp1_hit"] is True
    assert record["tp2_hit"] is True
    assert record["tp3_hit"] is True
    assert record["final_result"] == "TP3"
    assert record["classification"] == "STRONG_SUCCESS_TP3"
    assert record["r_result"] == 1.9


def test_observe_deduplicates_same_symbol_direction_within_hour(tmp_path):
    state_path = tmp_path / "big_move_false_positive_shadow.json"
    shadow.begin(str(state_path))
    candidate = {
        "source": "BIG_MOVE_ENTRY",
        "symbol": "BNBUSDT",
        "direction": "LONG",
        "entry": 700.0,
        "tp1": 708.0,
        "tp2": 716.0,
        "tp3": 730.0,
        "sl": 690.0,
        "score": 98,
    }

    first = shadow.observe(candidate, now_value=10_000)
    second = shadow.observe(candidate, now_value=10_300)

    assert first is not None
    assert second is None
    assert shadow.summary()["promoted_candidates"] == 1
