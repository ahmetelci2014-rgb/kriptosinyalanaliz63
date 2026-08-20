import json
import math
import time

import pandas as pd

import movement_start_v2_shadow as movement


def _frame(n=90, base=100.0, drift=0.03):
    rows = []
    prev = base
    for i in range(n):
        close = base + i * drift + math.sin(i / 5.0) * 0.08
        open_ = prev
        high = max(open_, close) + 0.18
        low = min(open_, close) - 0.18
        volume = 1000.0 + (i % 7) * 25.0
        rows.append({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        prev = close
    return pd.DataFrame(rows)


def _strong_long_features():
    return {
        "atr_compression": 0.70,
        "range_compression": 0.65,
        "squeeze_recent": True,
        "squeeze_release": True,
        "support_hold": True,
        "resistance_hold": False,
        "higher_low": True,
        "lower_high": False,
        "liquidity_sweep_long": True,
        "liquidity_sweep_short": False,
        "internal_break_long": True,
        "internal_break_short": False,
        "volume_wake": 1.35,
        "volume_ratio": 1.55,
        "ema20_slope": 0.04,
        "ema_turn": 0.03,
        "rsi5": 55.0,
        "rsi_slope": 5.0,
        "close_power": 78.0,
        "fifteen_long_ok": True,
        "fifteen_short_ok": False,
        "one_hour_long_ok": True,
        "one_hour_short_ok": False,
        "four_hour_long_ok": True,
        "four_hour_short_ok": True,
        "long_risk_percent": 0.85,
        "short_risk_percent": 3.5,
        "room_long_r": 2.5,
        "room_short_r": 0.0,
    }


def test_strong_5m_structure_reaches_trigger():
    features = _strong_long_features()
    score, conditions = movement.score_direction(features, "LONG")
    assert score >= movement.TRIGGER_SCORE
    assert conditions["liquidity_sweep"]
    assert conditions["internal_break"]
    assert conditions["volume_confirm"]
    assert movement._stage(score, conditions) == "TRIGGER"


def test_open_realtime_bar_does_not_change_features():
    df5 = _frame(90, drift=0.02)
    df15 = _frame(90, drift=0.04)
    df1 = _frame(90, drift=0.06)
    df4 = _frame(90, drift=0.08)

    changed = df5.copy()
    changed.loc[changed.index[-1], ["open", "high", "low", "close", "volume"]] = [
        150.0, 180.0, 70.0, 175.0, 9999999.0
    ]

    first = movement.extract_features(df5, df15, df1, df4, None)
    second = movement.extract_features(changed, df15, df1, df4, None)
    assert first is not None and second is not None
    assert first == second


def test_prep_upgrades_and_2r_first_is_learned(tmp_path, monkeypatch):
    state_file = tmp_path / "movement_start_v2_shadow.json"
    movement.begin(str(state_file))
    now = int(time.time())

    base_result = {
        "symbol": "RAYUSDT",
        "direction": "LONG",
        "stage": "PREP",
        "score": 70,
        "opposite_score": 40,
        "entry": 100.0,
        "stop": 99.0,
        "risk_abs": 1.0,
        "risk_percent": 1.0,
        "target_2r": 102.0,
        "target_3r": 103.0,
        "target_5r": 105.0,
        "features": _strong_long_features(),
        "conditions": {"internal_break": False},
        "version": movement.VERSION,
    }
    current = dict(base_result)
    monkeypatch.setattr(movement, "analyze", lambda *args, **kwargs: dict(current))

    created = movement.observe("RAYUSDT", None, None, None, None, 100.0, now_ts=now)
    assert created is not None
    assert created["event"] == "NEW"
    assert created["record"]["initial_stage"] == "PREP"

    current["stage"] = "ARMED"
    current["score"] = 80
    upgraded = movement.observe("RAYUSDT", None, None, None, None, 100.4, now_ts=now + 300)
    assert upgraded is not None
    assert upgraded["event"] == "UPGRADE"
    assert upgraded["record"]["best_stage"] == "ARMED"

    movement.observe("RAYUSDT", None, None, None, None, 102.2, now_ts=now + 600)
    movement.finish(str(state_file))
    data = json.loads(state_file.read_text(encoding="utf-8"))
    open_record = data["open"]["RAYUSDT_LONG"]
    assert open_record["first_resolution"] == "R2_FIRST"
    assert open_record["hit_2r_at"] > 0
    assert open_record["max_favorable_r"] >= 2.0


def test_one_hour_opposition_penalizes_candidate():
    features = _strong_long_features()
    baseline, _ = movement.score_direction(features, "LONG")
    features["one_hour_long_ok"] = False
    score, conditions = movement.score_direction(features, "LONG")
    assert not conditions["one_hour_not_opposing"]
    assert score < baseline


def test_module_is_shadow_only():
    assert not hasattr(movement, "send_telegram")
    assert not hasattr(movement, "place_order")
