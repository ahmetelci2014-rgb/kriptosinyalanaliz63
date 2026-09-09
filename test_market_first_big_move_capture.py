from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import market_first_big_move_capture as capture


def candidate(direction="LONG", entry=1.0, tp2=None, tp3=None):
    sign = 1 if direction == "LONG" else -1
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "entry": entry,
        "sl": entry * (1 - sign * 0.01),
        "tp2": tp2 if tp2 is not None else entry * (1 + sign * 0.01),
        "tp3": tp3 if tp3 is not None else entry * (1 + sign * 0.02),
        "score": 96,
        "big_move_base_score": 90,
        "risk_percent": 1.0,
        "big_move_origin_move_percent": 2.0,
        "volume_ratio": 1.5,
    }


def frame(rows):
    data = []
    for at, high, low in rows:
        data.append(
            {
                "time": at * 1000,
                "open": 1.0,
                "high": high,
                "low": low,
                "close": 1.0,
                "volume": 100.0,
            }
        )
    return pd.DataFrame(data)


def reset_state():
    capture._STATE = capture._default_state()
    capture._DIRTY = False


def setup_function():
    reset_state()


def test_meaningful_room_requires_large_move_not_small_scalp():
    assert capture._has_meaningful_room(candidate("LONG", tp2=1.009, tp3=1.016))
    assert not capture._has_meaningful_room(candidate("LONG", tp2=1.005, tp3=1.008))
    assert capture._has_meaningful_room(candidate("SHORT", tp2=0.991, tp3=0.984))


def test_strong_opposite_market_blocks_big_move_alert():
    context = SimpleNamespace(preferred_direction="SHORT", allow_countertrend=False)
    assert not capture._market_allows(context, "LONG")
    assert capture._market_allows(context, "SHORT")
    flexible = SimpleNamespace(preferred_direction="SHORT", allow_countertrend=True)
    assert capture._market_allows(flexible, "LONG")


def test_long_tracking_marks_one_and_two_percent_moves():
    now = 1000
    item = candidate("LONG")
    capture._register_alert(item, now)
    df = frame(
        [
            (900, 1.001, 0.999),
            (1100, 1.011, 0.999),
            (1400, 1.021, 1.001),
            (1700, 1.022, 1.010),  # forming bar; ignored
        ]
    )
    capture._update_track("TESTUSDT", df, 1800)
    record = capture._STATE["open"]["TESTUSDT:LONG"]
    assert record["reached"]["1"] is True
    assert record["reached"]["2"] is True
    assert record["reached"]["3"] is False
    assert record["mfe_percent"] >= 2.0


def test_same_unseen_bar_stop_and_target_is_conservative_stop_first():
    now = 1000
    item = candidate("LONG")
    capture._register_alert(item, now)
    df = frame(
        [
            (900, 1.001, 0.999),
            (1100, 1.020, 0.989),
            (1400, 1.000, 0.999),  # forming bar; ignored
        ]
    )
    capture._update_track("TESTUSDT", df, 1500)
    assert "TESTUSDT:LONG" not in capture._STATE["open"]
    closed = capture._STATE["history"][-1]
    assert closed["status"] == "STOP_BEFORE_1P"
    assert closed["reached"]["1"] is False


def test_short_tracking_is_symmetric():
    now = 1000
    item = candidate("SHORT")
    capture._register_alert(item, now)
    df = frame(
        [
            (900, 1.001, 0.999),
            (1100, 1.001, 0.989),
            (1400, 0.999, 0.979),
            (1700, 0.995, 0.978),  # forming bar; ignored
        ]
    )
    capture._update_track("TESTUSDT", df, 1800)
    record = capture._STATE["open"]["TESTUSDT:SHORT"]
    assert record["reached"]["1"] is True
    assert record["reached"]["2"] is True
    assert record["mfe_percent"] >= 2.0


def test_summary_separates_large_move_milestones_from_stop():
    capture._register_alert(candidate("LONG"), 1000)
    record = capture._STATE["open"].pop("TESTUSDT:LONG")
    record["reached"]["1"] = True
    record["reached"]["2"] = True
    record["mfe_percent"] = 2.2
    record["status"] = "STOP_AFTER_MOVE"
    capture._STATE["history"].append(record)

    summary = capture._summary()
    assert summary["total_alerts"] == 1
    assert summary["reached_1p"] == 1
    assert summary["reached_2p"] == 1
    assert summary["stop_after_move"] == 1
    assert summary["by_direction"]["LONG"]["reached_2p"] == 1
