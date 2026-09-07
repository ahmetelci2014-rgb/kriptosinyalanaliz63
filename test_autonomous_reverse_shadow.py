import math

import pandas as pd

import autonomous_reverse_shadow as ar


def _trend_frame(up: bool, volume_spike: bool = True) -> pd.DataFrame:
    rows = []
    price = 100.0
    for i in range(260):
        drift = 0.12 if up else -0.12
        open_price = price
        close = max(1.0, price + drift)
        high = max(open_price, close) + 0.08
        low = min(open_price, close) - 0.08
        volume = 1800.0 if volume_spike and i >= 255 else 1000.0
        rows.append([i * 60_000, open_price, high, low, close, volume])
        price = close
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


def test_r_multiple_long_short():
    assert ar.r_multiple("LONG", 100, 102, 2) == 1.0
    assert ar.r_multiple("SHORT", 100, 98, 2) == 1.0
    assert ar.r_multiple("LONG", 100, 98, 2) == -1.0


def test_reverse_size_is_capped_and_monotonic():
    assert ar.size_factor_for_score(90) == 0.25
    assert ar.size_factor_for_score(93) == 0.40
    assert ar.size_factor_for_score(96) == 0.60
    assert ar.size_factor_for_score(99) == 0.75


def test_cost_model_is_positive():
    value = ar.estimate_round_trip_cost_r(100, 1)
    assert value > 0
    assert math.isclose(value, 0.14, rel_tol=1e-9)


def test_long_position_detects_strong_bearish_reverse(monkeypatch):
    def fake_indicator_frames(df5, df15, df1h):
        def make(down: bool):
            data = _trend_frame(up=not down)
            data["ema20"] = data["close"].ewm(span=20, adjust=False).mean()
            data["ema20_slope"] = data["ema20"] - data["ema20"].shift(3)
            data["macd_hist"] = -1.0 if down else 1.0
            data["rsi"] = 35.0 if down else 65.0
            data["volume_ratio"] = 1.5
            return data
        return make(True), make(True), make(True)

    monkeypatch.setattr(ar, "indicator_frames", fake_indicator_frames)
    dummy = _trend_frame(up=False)
    result = ar.calculate_reverse_score("LONG", dummy, dummy, dummy)
    assert result["hard_gate"] is True
    assert result["reverse_to"] == "SHORT"
    assert result["score"] >= ar.REVERSE_SCORE_THRESHOLD


def test_short_position_detects_strong_bullish_reverse(monkeypatch):
    def fake_indicator_frames(df5, df15, df1h):
        def make(up: bool):
            data = _trend_frame(up=up)
            data["ema20"] = data["close"].ewm(span=20, adjust=False).mean()
            data["ema20_slope"] = data["ema20"] - data["ema20"].shift(3)
            data["macd_hist"] = 1.0 if up else -1.0
            data["rsi"] = 65.0 if up else 35.0
            data["volume_ratio"] = 1.5
            return data
        return make(True), make(True), make(True)

    monkeypatch.setattr(ar, "indicator_frames", fake_indicator_frames)
    dummy = _trend_frame(up=True)
    result = ar.calculate_reverse_score("SHORT", dummy, dummy, dummy)
    assert result["hard_gate"] is True
    assert result["reverse_to"] == "LONG"
    assert result["score"] >= ar.REVERSE_SCORE_THRESHOLD
