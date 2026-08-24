from __future__ import annotations

import numpy as np
import pandas as pd

import premium_big_move_live as big


def make_frame(direction="LONG", rows=80, start=0.98, end=1.0):
    values = np.linspace(start, end, rows)
    if direction == "SHORT":
        values = np.linspace(end, start, rows)
    frame = pd.DataFrame(
        {
            "time": np.arange(rows) * 60_000,
            "open": values,
            "high": values + 0.0008,
            "low": values - 0.0008,
            "close": values,
            "volume": np.linspace(100, 180, rows),
        }
    )
    return frame


def base(direction="LONG", stage="ARMED", score=88, opposite=35):
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "stage": stage,
        "score": score,
        "opposite_score": opposite,
        "entry": 1.0,
        "stop": 0.988 if direction == "LONG" else 0.992,
        "features": {
            "volume_ratio": 1.85,
            "volume_wake": 1.40,
        },
        "conditions": {},
    }


def flow(score=76, confirmed=True):
    return {
        "orderflow_score": score,
        "orderflow_confirmed": confirmed,
        "at": 1000,
    }


def setup_function():
    big.begin("/tmp/premium_big_move_test_state.json")


def test_long_big_move_breakout_promotes():
    df15 = make_frame("LONG", start=0.985, end=1.0)
    df1 = make_frame("LONG", start=0.97, end=1.0)
    df4 = make_frame("LONG", start=0.94, end=1.0)
    signal = big.analyze_live_candidate(
        "TESTUSDT",
        base("LONG"),
        df15,
        df1,
        df4,
        1.0004,
        flow_snapshot=flow(),
        now_ts=1000,
    )
    assert signal is not None
    assert signal["source"] == big.SOURCE
    assert signal["direction"] == "LONG"
    assert signal["score"] >= big.MIN_LIVE_SCORE
    assert signal["rr_tp3"] == 3.0


def test_short_big_move_breakout_is_symmetric():
    df15 = make_frame("SHORT", start=0.98, end=1.0)
    df1 = make_frame("SHORT", start=0.97, end=1.0)
    df4 = make_frame("SHORT", start=0.94, end=1.0)
    signal = big.analyze_live_candidate(
        "TESTUSDT",
        base("SHORT"),
        df15,
        df1,
        df4,
        0.9796,
        flow_snapshot=flow(),
        now_ts=1000,
    )
    assert signal is not None
    assert signal["direction"] == "SHORT"
    assert signal["tp3"] < signal["entry"]


def test_prep_is_not_live_big_move():
    df = make_frame("LONG")
    signal = big.analyze_live_candidate(
        "TESTUSDT",
        base("LONG", stage="PREP", score=90),
        df,
        df,
        df,
        1.0002,
        flow_snapshot=flow(),
        now_ts=1000,
    )
    assert signal is None


def test_late_breakout_extension_is_rejected():
    df15 = make_frame("LONG", start=0.985, end=1.0)
    df1 = make_frame("LONG", start=0.97, end=1.0)
    df4 = make_frame("LONG", start=0.94, end=1.0)
    signal = big.analyze_live_candidate(
        "TESTUSDT",
        base("LONG", stage="TRIGGER", score=96),
        df15,
        df1,
        df4,
        1.03,
        flow_snapshot=flow(),
        now_ts=1000,
    )
    assert signal is None


def test_strongly_opposite_orderflow_rejected():
    df15 = make_frame("LONG", start=0.985, end=1.0)
    df1 = make_frame("LONG", start=0.97, end=1.0)
    df4 = make_frame("LONG", start=0.94, end=1.0)
    signal = big.analyze_live_candidate(
        "TESTUSDT",
        base("LONG"),
        df15,
        df1,
        df4,
        1.0004,
        flow_snapshot=flow(score=10, confirmed=False),
        now_ts=1000,
    )
    assert signal is None


def test_message_is_short_and_trade_focused():
    signal = {
        "source": big.SOURCE,
        "direction": "LONG",
        "symbol": "POLUSDT",
        "entry": 0.08005,
        "tp1": 0.085,
        "tp2": 0.09,
        "tp3": 0.10,
        "sl": 0.077,
    }
    builder = big.make_trade_message_builder(lambda *a, **k: "legacy")
    text = builder(signal, current_price=signal["entry"], portfolio_risk={})
    assert text.startswith("🚀 PREMIUM BÜYÜK HAREKET")
    assert "LONG | POLUSDT" in text
    assert "Giriş:" in text
    assert "TP3:" in text
    assert "SL:" in text
    assert "Order Flow" not in text


def test_direct_gate_keeps_existing_validator_and_cost_gate():
    signal = {
        "source": big.SOURCE,
        "score": 97,
        "risk_percent": 1.2,
    }

    class Profit:
        @staticmethod
        def cost_viability(candidate):
            return {"ok": True}

    assert big.strong_direct_allowed(signal, 1.0, lambda s, p: (True, "ok"), Profit)
    assert not big.strong_direct_allowed(signal, 1.0, lambda s, p: (False, "bad"), Profit)
