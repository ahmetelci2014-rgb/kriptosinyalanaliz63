from __future__ import annotations

import pandas as pd

import extreme_start_research as research


def _macro_frame(long_side: bool = True) -> pd.DataFrame:
    rows = []
    for i in range(40):
        base = 100.0 + i * (0.25 if long_side else -0.25)
        rows.append(
            {
                "time": (i + 1) * 4 * 60 * 60 * 1000,
                "open": base,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + (0.2 if long_side else -0.2),
                "volume": 1000.0,
                "atr": 2.0,
                "ema20": base,
                "ema20_slope": 0.2 if long_side else -0.2,
                "rsi": 55.0 if long_side else 45.0,
            }
        )
    if long_side:
        rows[28]["low"] = 96.0
    else:
        rows[28]["high"] = 104.0
    return pd.DataFrame(rows)


def _micro_frame(direction: str) -> pd.DataFrame:
    rows = []
    for i in range(40):
        price = 100.0
        rows.append(
            {
                "time": i * 15 * 60 * 1000,
                "open": price,
                "high": 100.35,
                "low": 99.65,
                "close": price,
                "volume": 1000.0,
                "atr": 0.50,
                "ema20": 100.0,
                "ema20_slope": 0.0,
                "rsi": 50.0,
                "volume_avg": 1000.0,
                "volume_ratio": 1.0,
            }
        )
    if direction == "LONG":
        rows[-1].update({"open": 100.1, "high": 101.2, "low": 100.0, "close": 101.1, "volume_ratio": 1.5})
    else:
        rows[-1].update({"open": 99.9, "high": 100.0, "low": 98.8, "close": 98.9, "volume_ratio": 1.5})
    return pd.DataFrame(rows)


def test_phase_from_atr_separates_start_from_late() -> None:
    assert research.phase_from_atr(1.0) == "NEAR_EXTREME"
    assert research.phase_from_atr(2.5) == "START_ENTRY"
    assert research.phase_from_atr(4.0) == "EARLY_ENTRY"
    assert research.phase_from_atr(8.0) == "LATE_CONTINUATION"


def test_macro_origin_long_and_short_are_symmetric() -> None:
    long_result = research._macro_origin(_macro_frame(True), "LONG", 101.0)
    short_result = research._macro_origin(_macro_frame(False), "SHORT", 99.0)
    assert long_result is not None
    assert short_result is not None
    assert long_result["origin_price"] == 96.0
    assert short_result["origin_price"] == 104.0
    assert long_result["move_from_origin_atr"] > 0
    assert short_result["move_from_origin_atr"] > 0


def test_micro_trigger_accepts_breakout_near_extreme_both_directions() -> None:
    long_result = research._micro_trigger(_micro_frame("LONG"), "LONG")
    short_result = research._micro_trigger(_micro_frame("SHORT"), "SHORT")
    assert long_result is not None
    assert short_result is not None
    assert 0.25 <= long_result["risk_percent"] <= research.MAX_RISK_PERCENT
    assert 0.25 <= short_result["risk_percent"] <= research.MAX_RISK_PERCENT


def test_same_candle_stop_and_tp_is_conservative_sl_first() -> None:
    future = pd.DataFrame(
        [
            {"high": 103.5, "low": 98.5},
            {"high": 104.0, "low": 101.0},
        ]
    )
    setup = {
        "event_index": 0,
        "entry": 100.0,
        "stop": 99.0,
        "direction": "LONG",
    }
    df = pd.concat([pd.DataFrame([{"high": 100.0, "low": 100.0}]), future], ignore_index=True)
    result = research.evaluate_setup(setup, df)
    assert result["result"] == "SL_FIRST"


def test_report_keeps_chronological_holdout() -> None:
    rows = []
    for i in range(10):
        rows.append(
            {
                "detected_at": i + 1,
                "direction": "LONG" if i % 2 == 0 else "SHORT",
                "phase": "START_ENTRY",
                "result": "TP3" if i >= 7 else "SL_FIRST",
                "move_from_origin_atr": 2.0,
                "move_from_origin_percent": 2.0,
                "mfe_r": 3.0 if i >= 7 else 0.2,
                "mae_r": 0.2 if i >= 7 else 1.0,
            }
        )
    report = research.build_report(rows)
    assert report["train"]["sample"] == 7
    assert report["holdout"]["sample"] == 3
    assert report["holdout"]["tp3_rate_percent"] == 100.0
