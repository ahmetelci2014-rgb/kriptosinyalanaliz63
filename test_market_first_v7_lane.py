from types import SimpleNamespace

import pandas as pd
import pytest

import market_first_v7_lane as v7
import market_first_balanced_core_guard as core


def frames(direction="LONG"):
    series = []
    for i in range(73):
        if direction == "LONG":
            row = {"open": 99.80, "close": 99.84, "high": 100.10,
                   "low": 99.55, "volume": 120}
        else:
            row = {"open": 100.20, "close": 100.16, "high": 100.48,
                   "low": 99.90, "volume": 120}
        row["time"] = (1_800_000_000 + i * 300) * 1000
        series.append(row)
    if direction == "LONG":
        series[-2].update(open=99.72, close=100.0, high=100.20, low=99.70)
    else:
        series[-2].update(open=100.28, close=100.0, high=100.32, low=99.78)
    # Current, still-open candle must never be read for confirmation or stop.
    series[-1].update(open=999.0, close=999.0, high=999.0, low=0.01)
    a = pd.DataFrame(series)
    b = pd.DataFrame(series)
    c = pd.DataFrame(series)
    a.attrs["kind"] = "5m"
    b.attrs["kind"] = "15m"
    c.attrs["kind"] = "1h"
    return a, b, c


def mock_structure(monkeypatch, direction="LONG", volume5=1.20):
    def lookup(frame, current_price):
        kind = frame.attrs["kind"]
        if kind == "5m":
            return {"direction": direction, "atr": 0.45,
                    "ema20": 99.70 if direction == "LONG" else 100.30,
                    "ema9": 99.85 if direction == "LONG" else 100.15,
                    "volume_ratio": volume5}
        return {"direction": direction, "volume_ratio": 0.85}
    monkeypatch.setattr(v7.strategy, "_structure", lookup)


def candidate(monkeypatch, direction="LONG", volume5=1.20, price=100):
    mock_structure(monkeypatch, direction, volume5)
    a, b, c = frames(direction)
    return v7.evaluate(
        symbol="TESTUSDT", df5m=a, df15m=b, df1h=c,
        current_price=price, quote_volume_24h=10_000_000,
        context=SimpleNamespace(regime="CHOP", preferred_direction=None),
    )


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_v7_closed_bar_retest_and_stop_geometry(monkeypatch, direction):
    result, reason = candidate(monkeypatch, direction)
    assert reason == "OK"
    assert result["source"] == "MARKET_FIRST_V7"
    assert result["v7_setup"] == "PULLBACK"
    assert 0.50 <= result["risk_percent"] <= 1.55
    assert result["v7_closed_5m_time"] > 0
    ok, why, _ = v7.validate_live_signal(result)
    assert ok is True and why == "OK"
    ok, why, _ = core.evaluate_signal(result)
    assert ok is True and why == "V7_OK"
    assert result["entry"] == pytest.approx(100, rel=1e-6)
    assert result["sl"] != 0.01
    assert result["tp3"] != 999


def test_v7_skips_inadequate_volume(monkeypatch):
    result, reason = candidate(monkeypatch, volume5=0.3)
    assert result is None and reason == "VOLUME_LOW"


def test_v7_blocks_late_entry_even_if_last_closed_candle_is_valid(monkeypatch):
    result, reason = candidate(monkeypatch, price=100.5)
    assert result is None and reason == "STALE_5M_CLOSE"


def test_v7_does_not_use_unconfirmed_1m_movement(monkeypatch):
    result, reason = candidate(monkeypatch)
    assert reason == "OK"
    assert result.get("move_1m_percent") is None
    assert result["rr_tp3"] == 2.5


def test_v7_strong_opposite_market_block(monkeypatch):
    mock_structure(monkeypatch)
    a, b, c = frames()
    result, reason = v7.evaluate(
        symbol="TESTUSDT", df5m=a, df15m=b, df1h=c,
        current_price=100, quote_volume_24h=10_000_000,
        context=SimpleNamespace(regime="BEAR_STRONG", preferred_direction="SHORT"),
    )
    assert result is None and reason == "MARKET_OPPOSITE"


def test_v7_rejects_forged_wrong_side_and_wide_stop(monkeypatch):
    result, reason = candidate(monkeypatch)
    assert reason == "OK"
    wrong = dict(result, sl=result["entry"] + 0.8)
    assert v7.validate_live_signal(wrong)[0] is False
    wide = dict(result, sl=result["entry"] - 4.0)
    assert v7.validate_live_signal(wide)[0] is False
    fake = dict(result, v7_version="FAKE")
    assert v7.validate_live_signal(fake)[0] is False


def test_v7_spread_check_fails_closed(monkeypatch):
    class B:
        @staticmethod
        def to_okx_symbol(s):
            return s

    class R:
        bot = B()

    monkeypatch.setattr(v7, "_RUNNER", R())
    class E:
        def fetch_ticker(self, symbol):
            return {"bid": 99.6, "ask": 100.4}
    assert v7._fresh_spread(E(), "TESTUSDT")[0] is False


def test_v6_not_disabled_by_new_v7_guard():
    signal = {"symbol": "OLDUSDT", "direction": "LONG", "entry": 100, "sl": 99,
              "profit_quality_version": "PQ", "final_execution_gate_version": "FE",
              "final_execution_gate": {"fresh_micro": True},
              "high_profit_low_sl_version": "HP", "high_profit_low_sl_grade": "A+"}
    assert core.evaluate_signal(signal)[0] is True
