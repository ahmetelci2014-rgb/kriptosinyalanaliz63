from __future__ import annotations

import time

import numpy as np
import pandas as pd

import premium_regime_transition as regime


def _frame(closes, seconds, wick=0.004, volume=1000.0):
    closes = np.asarray(closes, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) * (1.0 + wick)
    lows = np.minimum(opens, closes) * (1.0 - wick)
    volumes = np.full(len(closes), float(volume))
    volumes[-12:] = float(volume) * 1.35
    start = int(time.time() * 1000) - len(closes) * seconds * 1000
    timestamps = [start + i * seconds * 1000 for i in range(len(closes))]
    return pd.DataFrame(
        {
            "time": timestamps,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        }
    )


def _base(direction, stage="TRIGGER", score=96, opposite=35, entry=100.0, stop=99.0):
    return {
        "direction": direction,
        "stage": stage,
        "score": score,
        "opposite_score": opposite,
        "entry": entry,
        "stop": stop,
        "features": {
            "volume_ratio": 1.8,
            "volume_wake": 1.3,
            "internal_break_long": direction == "LONG",
            "internal_break_short": direction == "SHORT",
        },
        "conditions": {"internal_break": True},
    }


def test_promotes_early_multi_hour_long_start(tmp_path):
    regime.begin(str(tmp_path / "regime.json"))

    df4 = _frame(
        list(np.linspace(100, 98, 60)) + list(np.linspace(98, 103, 20)),
        4 * 3600,
    )
    df1 = _frame(
        list(np.linspace(100, 99, 55)) + list(np.linspace(99, 104, 25)),
        3600,
    )
    df15 = _frame(
        list(np.linspace(100, 99.5, 55)) + list(np.linspace(99.5, 104.2, 25)),
        15 * 60,
    )
    base = _base("LONG", entry=104.0, stop=102.8)

    signal = regime.analyze_live_candidate(
        "TESTUSDT",
        base,
        df15,
        df1,
        df4,
        104.0,
        now_ts=int(time.time()),
    )

    assert signal is not None
    assert signal["direction"] == "LONG"
    assert signal["regime_transition_mode"] == regime.MODE_TREND_START
    assert signal["score"] >= regime.MIN_LIVE_SCORE
    assert signal["regime_start_delay_percent"] <= regime.MAX_START_DELAY_PERCENT


def test_promotes_generic_short_reversal_without_prior_tp3(tmp_path):
    regime.begin(str(tmp_path / "regime.json"))

    df4 = _frame(
        list(np.linspace(100, 100, 62))
        + list(np.linspace(100, 116, 14))
        + [115.0, 113.8, 113.2, 113.0],
        4 * 3600,
        wick=0.006,
    )
    peak = df4["high"].idxmax()
    df4.loc[peak, "high"] *= 1.015

    df1 = _frame(
        list(np.linspace(100, 116, 60)) + list(np.linspace(116, 111.8, 20)),
        3600,
        wick=0.003,
    )
    df15 = _frame(
        list(np.linspace(100, 116, 58)) + list(np.linspace(116, 111.5, 22)),
        15 * 60,
        wick=0.002,
    )
    base = _base("SHORT", entry=111.5, stop=113.0)

    signal = regime.analyze_live_candidate(
        "TESTUSDT",
        base,
        df15,
        df1,
        df4,
        111.5,
        now_ts=int(time.time()),
    )

    assert signal is not None
    assert signal["direction"] == "SHORT"
    assert signal["regime_transition_mode"] == regime.MODE_REVERSAL
    assert signal["regime_prior_move_percent"] >= regime.MIN_PRIOR_REVERSAL_MOVE_PERCENT
    assert signal["regime_reversal_pullback_percent"] <= regime.MAX_REVERSAL_PULLBACK_PERCENT


def test_rejects_when_15m_and_1h_do_not_support_direction(tmp_path):
    regime.begin(str(tmp_path / "regime.json"))

    df4 = _frame(
        list(np.linspace(100, 98, 60)) + list(np.linspace(98, 103, 20)),
        4 * 3600,
    )
    # Both lower timeframes still point down while V2 says LONG.
    df1 = _frame(list(np.linspace(104, 100, 80)), 3600)
    df15 = _frame(list(np.linspace(104, 100, 80)), 15 * 60)
    base = _base("LONG", entry=103.0, stop=101.8)

    signal = regime.analyze_live_candidate(
        "TESTUSDT",
        base,
        df15,
        df1,
        df4,
        103.0,
        now_ts=int(time.time()),
    )

    assert signal is None
    assert regime._state()["records"][-1]["reason"] == "15M_1H_YON_TEYIDI_YETERSIZ"


def test_prep_never_becomes_live_trade(tmp_path):
    regime.begin(str(tmp_path / "regime.json"))

    df4 = _frame(list(np.linspace(100, 104, 80)), 4 * 3600)
    df1 = _frame(list(np.linspace(100, 104, 80)), 3600)
    df15 = _frame(list(np.linspace(100, 104, 80)), 15 * 60)
    base = _base("LONG", stage="PREP", score=100, opposite=20, entry=104.0, stop=102.8)

    assert (
        regime.analyze_live_candidate(
            "TESTUSDT",
            base,
            df15,
            df1,
            df4,
            104.0,
            now_ts=int(time.time()),
        )
        is None
    )
