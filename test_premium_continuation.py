import json

import pandas as pd

import premium_continuation as continuation


def _indicator_frame(direction="LONG"):
    rows = []
    for i in range(10):
        if direction == "LONG":
            close, ema20, ema50, slope, rsi = 100.20 + i * 0.01, 100.00 + i * 0.005, 99.50 + i * 0.004, 0.12, 60.0
        else:
            close, ema20, ema50, slope, rsi = 99.80 - i * 0.01, 100.00 - i * 0.005, 100.50 - i * 0.004, -0.12, 40.0
        rows.append({
            "close": close,
            "ema20": ema20,
            "ema50": ema50,
            "ema20_slope": slope,
            "rsi": rsi,
            "adx": 30.0,
            "atr": 0.40,
            "volume_ratio": 1.20,
        })
    return pd.DataFrame(rows)


def _write_state(path, now, **overrides):
    event = {
        "recorded_at": now,
        "symbol": "IOTAUSDT",
        "direction": "LONG",
        "source": "SHADOW_TREND_CONTINUATION",
        "shadow_ready": True,
        "move15_percent": 0.9777,
        "move30_percent": 0.2496,
        "price": 100.30,
        "ema20": 100.00,
        "ema20_slope_percent": 0.0383,
        "ema20_distance_percent": 0.30,
        "green_5m_count": 3,
        "red_5m_count": 1,
        "resume_confirmed": True,
        "rsi5": 58.6082,
        "vol1": 1.1391,
        "vol5": 2.4147,
    }
    event.update(overrides)
    path.write_text(json.dumps({"shadow_moves": [event]}), encoding="utf-8")


def test_iota_like_shadow_move_becomes_premium_continuation(tmp_path, monkeypatch):
    now = 1_000_000
    state = tmp_path / "pump_radar_state.json"
    _write_state(state, now)
    monkeypatch.setattr(continuation, "_frame", lambda df: df)

    f15 = _indicator_frame("LONG")
    f1 = _indicator_frame("LONG")
    f4 = _indicator_frame("LONG")

    signal = continuation.analyze_continuation(
        "IOTAUSDT",
        f15,
        f1,
        f4,
        current_price=100.35,
        state_file=str(state),
        now_ts=now,
    )

    assert signal is not None
    assert signal["source"] == "TREND_CONTINUATION"
    assert signal["direction"] == "LONG"
    assert signal["score"] >= continuation.MIN_SCORE
    assert continuation.MIN_RISK_PERCENT <= signal["risk_percent"] <= continuation.MAX_RISK_PERCENT
    assert "PREMIUM TREND DEVAM SİNYALİ" in signal["message"]


def test_stale_shadow_event_is_rejected(tmp_path, monkeypatch):
    now = 1_000_000
    state = tmp_path / "pump_radar_state.json"
    _write_state(state, now - continuation.MAX_EVENT_AGE_SECONDS - 1)
    monkeypatch.setattr(continuation, "_frame", lambda df: df)
    frame = _indicator_frame("LONG")

    assert continuation.analyze_continuation(
        "IOTAUSDT", frame, frame, frame,
        current_price=100.35,
        state_file=str(state),
        now_ts=now,
    ) is None


def test_opposing_one_hour_structure_is_rejected(tmp_path, monkeypatch):
    now = 1_000_000
    state = tmp_path / "pump_radar_state.json"
    _write_state(state, now)
    monkeypatch.setattr(continuation, "_frame", lambda df: df)

    f15 = _indicator_frame("LONG")
    f1 = _indicator_frame("SHORT")
    f4 = _indicator_frame("LONG")

    assert continuation.analyze_continuation(
        "IOTAUSDT", f15, f1, f4,
        current_price=100.35,
        state_file=str(state),
        now_ts=now,
    ) is None


def test_direct_gate_keeps_base_validation_and_cost_check():
    signal = {
        "source": "TREND_CONTINUATION",
        "signal_class": "TRADE",
        "score": 98,
        "risk_percent": 0.80,
        "zone_distance_percent": 0.30,
        "entry": 100.0,
        "sl": 99.2,
        "tp1": 100.44,
    }

    class Profit:
        @staticmethod
        def cost_viability(_signal):
            return {"ok": True}

    assert continuation.strong_direct_allowed(
        signal,
        100.0,
        lambda _signal, _price: (True, "ok"),
        Profit,
    )

    assert not continuation.strong_direct_allowed(
        signal,
        100.0,
        lambda _signal, _price: (False, "bad"),
        Profit,
    )
