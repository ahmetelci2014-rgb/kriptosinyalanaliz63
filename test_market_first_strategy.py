import numpy as np
import pandas as pd

from market_first_strategy import (
    MAJOR_WEIGHTS,
    analyze_candidate,
    build_market_context,
    lifecycle_update,
)


def _trend_frame(direction="LONG", n=90, start=95.0, end=101.0, wick=0.45, volume=1000):
    values = (
        np.linspace(start, end, n)
        if direction == "LONG"
        else np.linspace(end, start, n)
    )
    opens = np.r_[values[0], values[:-1]]
    highs = np.maximum(opens, values) + wick
    lows = np.minimum(opens, values) - wick
    volumes = np.full(n, float(volume))
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": values,
            "volume": volumes,
        }
    )


def _bull_context():
    majors = {}
    for symbol in MAJOR_WEIGHTS:
        majors[symbol] = {
            "4h": _trend_frame("LONG", 100, 80, 100, 1.0),
            "1h": _trend_frame("LONG", 100, 90, 100, 0.8),
            "15m": _trend_frame("LONG", 100, 95, 100, 0.5),
            "5m": _trend_frame("LONG", 100, 98, 100, 0.25),
            "current_price": 100.1,
        }
    return build_market_context(majors, breadth_5m=0.62, breadth_24h=0.58)


def _accelerating_long_1m():
    n = 80
    values = np.linspace(99.0, 100.0, n)
    values[-6:] = [99.9, 100.0, 100.15, 100.35, 100.65, 101.0]
    opens = np.r_[values[0], values[:-1]]
    highs = np.maximum(opens, values) + 0.06
    lows = np.minimum(opens, values) - 0.06
    volumes = np.full(n, 1000.0)
    volumes[-1] = 1100.0
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": values,
            "volume": volumes,
        }
    )


def _countertrend_short_1m():
    n = 80
    values = np.linspace(99.0, 100.0, n)
    values[-6:] = [100.0, 99.95, 99.85, 99.7, 99.5, 99.3]
    opens = np.r_[values[0], values[:-1]]
    highs = np.maximum(opens, values) + 0.05
    lows = np.minimum(opens, values) - 0.05
    volumes = np.full(n, 1000.0)
    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": values,
            "volume": volumes,
        }
    )


def test_major_market_is_decided_before_coin_scan():
    context = _bull_context()
    assert context.regime == "BULL_STRONG"
    assert context.preferred_direction == "LONG"
    assert context.allow_countertrend is False
    assert context.breadth_5m == 0.62


def test_aligned_fresh_breakout_survives_market_first_logic():
    context = _bull_context()
    decision, reason = analyze_candidate(
        symbol="TESTUSDT",
        df1m=_accelerating_long_1m(),
        df5m=_trend_frame("LONG", 90, 96, 100.7, 0.5),
        df15m=_trend_frame("LONG", 90, 94, 100.5, 0.8),
        df1h=_trend_frame("LONG", 90, 90, 100.0, 1.0),
        current_price=101.0,
        quote_volume_24h=10_000_000,
        context=context,
    )

    assert reason == "OK"
    assert decision is not None
    assert decision["direction"] == "LONG"
    assert decision["stage"] in {"EARLY", "READY"}
    assert decision["alert_eligible"] is True
    assert decision["extension_atr_5m"] < 1.8


def test_ordinary_countertrend_short_is_blocked_in_strong_bull_market():
    context = _bull_context()
    decision, reason = analyze_candidate(
        symbol="SHORTXUSDT",
        df1m=_countertrend_short_1m(),
        df5m=_trend_frame("SHORT", 90, 96, 100, 0.5),
        df15m=_trend_frame("SHORT", 90, 94, 100, 0.8),
        df1h=_trend_frame("SHORT", 90, 90, 100, 1.0),
        current_price=99.3,
        quote_volume_24h=10_000_000,
        context=context,
    )

    assert decision is None
    assert reason == "MARKET_OPPOSED"


def test_alert_lifecycle_is_simple_continue_late_dead():
    continued = lifecycle_update(
        direction="LONG",
        alert_price=100.0,
        best_price=100.0,
        current_price=100.9,
        current_status="NEW",
        age_minutes=10,
    )
    assert continued["status"] == "CONTINUE"

    late = lifecycle_update(
        direction="LONG",
        alert_price=100.0,
        best_price=100.9,
        current_price=102.6,
        current_status="CONTINUE",
        age_minutes=20,
    )
    assert late["status"] == "LATE"

    dead = lifecycle_update(
        direction="LONG",
        alert_price=100.0,
        best_price=102.6,
        current_price=100.7,
        current_status="LATE",
        age_minutes=40,
    )
    assert dead["status"] == "DEAD"
