from __future__ import annotations

import numpy as np
import pandas as pd

import market_first_2h_context_shadow as shadow


def trend_frame(direction: str, rows: int = 120) -> pd.DataFrame:
    if direction == "LONG":
        close = np.linspace(100.0, 140.0, rows)
    else:
        close = np.linspace(140.0, 100.0, rows)
    return pd.DataFrame(
        {
            "open": close * (0.999 if direction == "LONG" else 1.001),
            "high": close * 1.004,
            "low": close * 0.996,
            "close": close,
            "volume": np.linspace(1000.0, 1800.0, rows),
        }
    )


def test_2h_context_detects_direction_and_alignment():
    up = trend_frame("LONG")
    down = trend_frame("SHORT")

    long_ctx = shadow.evaluate_context(
        "LONG", up, current_price=float(up.iloc[-1]["close"]), structure_1h="LONG"
    )
    short_ctx = shadow.evaluate_context(
        "SHORT", down, current_price=float(down.iloc[-1]["close"]), structure_1h="SHORT"
    )

    assert long_ctx["context_2h_direction"] == "LONG"
    assert long_ctx["context_2h_alignment"] == "ALIGNED"
    assert long_ctx["context_2h_1h_alignment"] == "ALIGNED"
    assert long_ctx["context_2h_rsi14"] > 50
    assert long_ctx["context_2h_adx14"] > 0
    assert long_ctx["context_2h_supertrend"] == "LONG"

    assert short_ctx["context_2h_direction"] == "SHORT"
    assert short_ctx["context_2h_alignment"] == "ALIGNED"
    assert short_ctx["context_2h_1h_alignment"] == "ALIGNED"
    assert short_ctx["context_2h_rsi14"] < 50
    assert short_ctx["context_2h_supertrend"] == "SHORT"


def test_2h_context_marks_opposed_without_blocking():
    down = trend_frame("SHORT")
    ctx = shadow.evaluate_context(
        "LONG", down, current_price=float(down.iloc[-1]["close"]), structure_1h="LONG"
    )
    assert ctx["context_2h_alignment"] == "OPPOSED"
    assert ctx["context_2h_shadow_only"] is True

    info = shadow.summary()
    assert info["changes_trade_admission"] is False
    assert info["changes_score"] is False
    assert info["changes_direction"] is False
    assert info["changes_stop_or_targets"] is False
    assert info["telegram"] is False


def test_missing_2h_data_stays_observational():
    ctx = shadow.evaluate_context("LONG", None, current_price=100.0, structure_1h="LONG")
    assert ctx["context_2h_alignment"] == "NO_2H_DATA"
    assert ctx["context_2h_direction"] == "UNKNOWN"
    assert ctx["context_2h_shadow_only"] is True
