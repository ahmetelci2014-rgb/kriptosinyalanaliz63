from __future__ import annotations

import numpy as np
import pandas as pd

import market_first_profit_quality_v1 as pq


def make_frame(direction="LONG", rows=110, start=1.0, end=1.04, spike=0.0):
    values = np.linspace(start, end, rows)
    if direction == "SHORT":
        values = np.linspace(end, start, rows)
    high = values + 0.002
    low = values - 0.002
    if spike:
        if direction == "LONG":
            high[-20] = max(high[-20], end * (1 + spike))
        else:
            low[-20] = min(low[-20], start * (1 - spike))
    return pd.DataFrame(
        {
            "time": np.arange(rows) * 60_000,
            "open": values,
            "high": high,
            "low": low,
            "close": values,
            "volume": np.linspace(100, 160, rows),
        }
    )


def test_profit_target_long_requires_meaningful_structure():
    df15 = make_frame("LONG", start=1.00, end=1.03, spike=0.03)
    df1 = make_frame("LONG", start=0.98, end=1.03, spike=0.04)
    target = pq._profit_target(
        direction="LONG",
        entry=1.03,
        risk_percent=0.60,
        df15m=df15,
        df1h=df1,
    )
    assert target is not None
    assert target["profit_target_percent"] >= 2.0
    assert target["profit_target_r"] >= 2.5
    assert target["profit_target"] > 1.03


def test_profit_target_short_is_symmetric():
    df15 = make_frame("SHORT", start=0.97, end=1.02, spike=0.03)
    df1 = make_frame("SHORT", start=0.96, end=1.03, spike=0.04)
    target = pq._profit_target(
        direction="SHORT",
        entry=0.97,
        risk_percent=0.60,
        df15m=df15,
        df1h=df1,
    )
    assert target is not None
    assert target["profit_target_percent"] >= 2.0
    assert target["profit_target"] < 0.97


def test_quality_gate_rejects_small_expected_move():
    decision = {
        "direction": "LONG",
        "score": 94,
        "risk_percent": 0.6,
        "volume_ratio_5m": 1.1,
        "profit_target_percent": 1.5,
        "profit_target_r": 3.0,
        "market_preferred_direction": "LONG",
        "target_confidence": "YÜKSEK",
    }
    ok, reason = pq._quality_reason(decision)
    assert not ok
    assert reason == "EXPECTED_MOVE_BELOW_2P"


def test_quality_gate_accepts_strong_two_percent_plus_setup():
    decision = {
        "direction": "SHORT",
        "score": 92,
        "risk_percent": 0.7,
        "volume_ratio_5m": 0.9,
        "profit_target_percent": 2.4,
        "profit_target_r": 3.4,
        "market_preferred_direction": "SHORT",
        "target_confidence": "ORTA",
    }
    ok, reason = pq._quality_reason(decision)
    assert ok
    assert reason == "OK"


def test_reanchored_targets_make_tp1_at_least_one_percent_at_floor():
    signal = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "entry": 1.0,
        "sl": 0.994,
        "tp1": 1.003,
        "tp2": 1.005,
        "tp3": 1.008,
        "score": 92,
        "risk_percent": 0.6,
    }
    decision = {
        "direction": "LONG",
        "current_price": 1.0,
        "profit_target": 1.02,
        "profit_target_percent": 2.0,
        "profit_target_r": 3.333,
        "profit_target_source": "1H direnç",
        "score": 92,
    }
    out = pq._reanchor_signal(signal, decision)
    assert round((out["tp1"] / out["entry"] - 1) * 100, 6) == 1.0
    assert round((out["tp2"] / out["entry"] - 1) * 100, 6) == 1.5
    assert round((out["tp3"] / out["entry"] - 1) * 100, 6) == 2.0
    assert out["legacy_tp3"] == 1.008
    assert out["rr_tp3"] >= 3.3


def test_short_reanchoring_and_single_message():
    signal = {
        "symbol": "TESTUSDT",
        "direction": "SHORT",
        "entry": 1.0,
        "sl": 1.008,
        "tp1": 0.997,
        "tp2": 0.995,
        "tp3": 0.992,
        "score": 95,
        "risk_percent": 0.8,
    }
    decision = {
        "direction": "SHORT",
        "current_price": 1.0,
        "profit_target": 0.975,
        "profit_target_percent": 2.5,
        "profit_target_r": 3.125,
        "profit_target_source": "2H destek",
        "score": 95,
    }
    out = pq._reanchor_signal(signal, decision)
    assert out["tp1"] > out["tp2"] > out["tp3"]
    text = pq._trade_message(out)
    assert text.startswith("🚨 KALİTELİ KRİPTO İŞLEM")
    assert "TP1" in text and "TP2" in text and "TP3 / Ana hedef" in text
    assert "~%1.25" in text
    assert "~%1.88" in text
    assert "~%2.50" in text


def test_summary_documents_single_entry_mode_and_profit_floors():
    info = pq.summary()
    assert info["telegram_entry_messages"] == "FINAL_TRADE_ONLY"
    assert info["early_entry_telegram"] is False
    assert info["big_move_telegram"] is False
    assert info["tp1_min_percent_at_threshold"] == 1.0
    assert info["tp2_min_percent_at_threshold"] == 1.5
    assert info["tp3_min_percent_at_threshold"] == 2.0
