import json
import time

import movement_start_shadow as movement


def _long_features():
    return {
        "price": 100.0,
        "close_15m": 100.0,
        "rsi_15m": 52.0,
        "rsi_slope": 4.0,
        "ema20": 99.8,
        "ema50": 99.7,
        "ema20_slope_pct": 0.02,
        "ema_turn": 0.05,
        "ema_distance_pct": 0.20,
        "atr_compression": 0.70,
        "range_compression": 0.55,
        "volume_wake": 1.30,
        "vol_ratio": 1.25,
        "close_power": 72.0,
        "higher_low": True,
        "lower_high": False,
        "support_hold": True,
        "resistance_hold": False,
        "dist_high_pct": 0.50,
        "dist_low_pct": 2.50,
        "breakout_long": False,
        "breakout_short": False,
        "one_hour_long_ok": True,
        "one_hour_short_ok": False,
        "four_hour_long_ok": True,
        "four_hour_short_ok": True,
    }


def test_long_accumulation_scores_as_early_start_candidate():
    features = _long_features()
    long_score, long_conditions = movement.score_direction(features, "LONG")
    short_score, _ = movement.score_direction(features, "SHORT")

    assert long_score >= movement.ARMED_SCORE
    assert long_score > short_score
    assert long_conditions["compression_atr"]
    assert long_conditions["structure_hold"]
    assert long_conditions["rsi_turning"]
    assert movement._stage(long_score, long_conditions) in {"ARMED", "TRIGGER"}


def test_strong_opposing_one_hour_penalizes_long_candidate():
    features = _long_features()
    baseline, _ = movement.score_direction(features, "LONG")
    features["one_hour_long_ok"] = False
    score, conditions = movement.score_direction(features, "LONG")

    assert not conditions["one_hour_not_opposing"]
    # Baseline skor 100'de tavanlandığı için ham -24 cezanın tamamı görülemez;
    # önemli olan güçlü ters 1H yapısının aday puanını belirgin düşürmesidir.
    assert score < baseline
    assert score <= 87


def test_shadow_record_learns_success_first(tmp_path, monkeypatch):
    state_file = tmp_path / "movement_start_shadow.json"
    movement.begin(str(state_file))
    now = int(time.time())

    result = {
        "direction": "LONG",
        "stage": "ARMED",
        "score": 82,
        "opposite_score": 55,
        "features": _long_features(),
        "conditions": {"breakout": False},
    }
    monkeypatch.setattr(movement, "analyze", lambda *args, **kwargs: result)

    event = movement.observe(
        "RAYUSDT", None, None, None, 100.0, now_ts=now
    )
    assert event is not None
    assert event["event"] == "NEW"
    assert event["record"]["entry"] == 100.0

    # Yaklaşık 5 dakika sonra +%2 bariyeri önce görülürse başarılı başlangıç.
    movement.observe(
        "RAYUSDT", None, None, None, 102.1, now_ts=now + 300
    )
    summary = movement.finish(str(state_file))

    data = json.loads(state_file.read_text(encoding="utf-8"))
    record = data["records"][0]
    assert record["outcome"] == "SUCCESS_FIRST"
    assert record["max_favorable_percent"] >= 2.0
    assert summary["success_first"] == 1


def test_shadow_record_learns_fail_first(tmp_path, monkeypatch):
    state_file = tmp_path / "movement_start_shadow.json"
    movement.begin(str(state_file))
    now = int(time.time())

    result = {
        "direction": "LONG",
        "stage": "PREP",
        "score": 68,
        "opposite_score": 54,
        "features": _long_features(),
        "conditions": {"breakout": False},
    }
    monkeypatch.setattr(movement, "analyze", lambda *args, **kwargs: result)

    movement.observe(
        "TESTUSDT", None, None, None, 100.0, now_ts=now
    )
    movement.observe(
        "TESTUSDT", None, None, None, 98.8, now_ts=now + 300
    )
    movement.finish(str(state_file))

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["records"][0]["outcome"] == "FAIL_FIRST"


def test_module_is_shadow_only():
    # Bu ilk sürümde Telegram/emir API yüzeyi bilinçli olarak yoktur.
    assert not hasattr(movement, "send_telegram")
    assert not hasattr(movement, "place_order")
