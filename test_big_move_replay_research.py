from __future__ import annotations

import pandas as pd

import big_move_replay_research as replay


def _synthetic_4h() -> pd.DataFrame:
    rows = []
    start = 1_700_000_000_000
    price = 100.0
    for i in range(70):
        if i < 20:
            close = 100.0 - i * 0.1
        elif i == 20:
            close = 98.0
        elif 20 < i <= 32:
            close = 98.0 + (i - 20) * 2.1
        else:
            close = 123.2 - (i - 32) * 0.15
        open_ = price
        high = max(open_, close) + 0.4
        low = min(open_, close) - 0.4
        if i == 20:
            low = 96.0
        rows.append([start + i * replay.TF_MS["4h"], open_, high, low, close, 1000 + i])
        price = close
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


def test_detect_big_long_event():
    events = replay.detect_big_events(_synthetic_4h())
    assert events
    longs = [row for row in events if row["direction"] == "LONG"]
    assert longs
    assert max(row["move_percent"] for row in longs) >= 20.0


def test_capture_stage_buckets():
    assert replay.capture_stage(1.5) == "COK_ERKEN"
    assert replay.capture_stage(4.0) == "ERKEN"
    assert replay.capture_stage(8.0) == "ORTA"
    assert replay.capture_stage(15.0) == "GEC"
    assert replay.capture_stage(25.0) == "COK_GEC"


def test_move_class_thresholds():
    assert replay.event_move_class(10.0) == "GUCLU_10P"
    assert replay.event_move_class(20.0) == "BUYUK_20P"
    assert replay.event_move_class(40.0) == "OLAGANUSTU_40P"


def test_available_share_long_and_short():
    assert round(replay.available_share_percent("LONG", 100.0, 105.0, 120.0), 2) == 75.0
    assert round(replay.available_share_percent("SHORT", 100.0, 95.0, 80.0), 2) == 75.0


def test_summary_uses_confirmed_detection_metrics():
    events = {
        "a": {
            "status": "REPLAY_OK",
            "event_move_class": "BUYUK_20P",
            "event_direction": "LONG",
            "detected_same_direction": True,
            "detected_confirmed": True,
            "detected_trigger": True,
            "early_confirmed": True,
            "chosen_detection_delay_percent": 3.0,
            "chosen_available_share_percent": 85.0,
        },
        "b": {
            "status": "REPLAY_OK",
            "event_move_class": "GUCLU_10P",
            "event_direction": "SHORT",
            "detected_same_direction": True,
            "detected_confirmed": False,
            "detected_trigger": False,
            "early_confirmed": False,
            "chosen_detection_delay_percent": 8.0,
            "chosen_available_share_percent": 55.0,
        },
    }
    summary = replay._summary(events)
    assert summary["sample"] == 2
    assert summary["same_direction_detection_rate_percent"] == 100.0
    assert summary["confirmed_detection_rate_percent"] == 50.0
    assert summary["trigger_detection_rate_percent"] == 50.0
    assert summary["early_confirmed_rate_percent"] == 50.0
    assert summary["avg_detection_delay_percent"] == 5.5
    assert summary["avg_available_share_percent"] == 70.0
