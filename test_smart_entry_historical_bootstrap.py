import pandas as pd

import smart_entry_historical_bootstrap as research


def test_zone_price_long_short_symmetry():
    assert research.zone_price("LONG", 100.0, 120.0, 0.50) == 110.0
    assert research.zone_price("SHORT", 120.0, 100.0, 0.50) == 110.0
    long_price = research.zone_price("LONG", 100.0, 120.0, 0.618)
    short_price = research.zone_price("SHORT", 120.0, 100.0, 0.618)
    assert round(abs(long_price - 110.0), 8) == round(abs(short_price - 110.0), 8)


def test_first_hit_is_direction_symmetric():
    long_future = pd.DataFrame([
        {"high": 101.2, "low": 99.7},
        {"high": 103.2, "low": 100.4},
    ])
    short_future = pd.DataFrame([
        {"high": 100.3, "low": 98.8},
        {"high": 99.6, "low": 96.8},
    ])
    long_result = research._first_hit("LONG", 100.0, 99.0, long_future)
    short_result = research._first_hit("SHORT", 100.0, 101.0, short_future)
    assert long_result["result"] == "TP3"
    assert short_result["result"] == "TP3"
    assert long_result["hit_3r"] is True
    assert short_result["hit_3r"] is True


def test_same_candle_stop_and_target_is_conservative():
    future = pd.DataFrame([{"high": 101.5, "low": 98.8}])
    result = research._first_hit("LONG", 100.0, 99.0, future)
    assert result["result"] == "SL_FIRST"


def _rows(zone: str, count: int, hit2_count: int, sl_count: int, start_ts: int):
    rows = []
    for i in range(count):
        # Spread wins/losses over the whole chronology so train and holdout both
        # contain representative outcomes.
        hit2 = ((i * hit2_count) % count) < hit2_count if hit2_count else False
        sl = ((i * sl_count) % count) < sl_count if sl_count else False
        rows.append({
            "symbol": "TESTUSDT",
            "direction": "LONG" if i % 2 == 0 else "SHORT",
            "detected_at": start_ts + i * 900,
            "zone": zone,
            "result": "SL_FIRST" if sl else ("TP3" if hit2 else "TIMEOUT"),
            "hit_1r": hit2,
            "hit_2r": hit2,
            "hit_3r": hit2 and i % 3 == 0,
            "mfe_r": 3.0 if hit2 else 0.5,
            "mae_r": 1.0 if sl else 0.35,
        })
    return rows


def test_model_requires_train_and_holdout_evidence(monkeypatch):
    monkeypatch.setattr(research, "MIN_TRAIN_SAMPLE", 10)
    monkeypatch.setattr(research, "MIN_HOLDOUT_SAMPLE", 4)
    good = _rows("GOLDEN_052_062", 40, 30, 4, 1_780_000_000)
    bad = _rows("SHALLOW_030_042", 40, 10, 20, 1_780_100_000)
    model = research.build_model(good + bad)
    assert model["zones"]["GOLDEN_052_062"]["evidence_status"] == "HISTORICALLY_VALIDATED"
    assert model["zones"]["SHALLOW_030_042"]["evidence_status"] == "EDGE_NOT_PROVEN"
    assert model["best_validated_zone"] == "GOLDEN_052_062"


def test_detect_events_uses_only_prior_breakout_structure(monkeypatch):
    monkeypatch.setattr(research, "EVENT_LOOKBACK_BARS", 8)
    monkeypatch.setattr(research, "EVENT_HORIZON_BARS", 4)
    monkeypatch.setattr(research, "MIN_EVENT_SEPARATION_BARS", 2)
    monkeypatch.setattr(research, "MIN_IMPULSE_PERCENT", 0.5)
    monkeypatch.setattr(research, "MIN_IMPULSE_ATR", 0.5)

    rows = []
    price = 100.0
    for i in range(24):
        close = price + i * 0.05
        high = close + 0.15
        low = close - 0.15
        if i == 12:
            close = 102.0
            high = 102.2
            low = 101.5
        rows.append({
            "time": (1_780_000_000 + i * 900) * 1000,
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1000.0,
            "atr": 0.4,
        })
    df = pd.DataFrame(rows)
    events = research.detect_events("TESTUSDT", df)
    assert any(event.direction == "LONG" and event.event_index == 12 for event in events)
