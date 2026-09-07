import pandas as pd

import market_first_target_engine as target


def _trend_frame(start: float, end: float, rows: int = 110):
    step = (end - start) / max(1, rows - 1)
    closes = [start + step * i for i in range(rows)]
    return pd.DataFrame({
        "open": [value - step * 0.25 for value in closes],
        "high": [value + 0.50 for value in closes],
        "low": [value - 0.50 for value in closes],
        "close": closes,
        "volume": [1000.0 + (i % 7) * 10.0 for i in range(rows)],
    })


def test_long_target_uses_structure_and_reports_positive_expected_move():
    df1h = _trend_frame(100.0, 110.0)
    df15m = _trend_frame(104.0, 108.0, rows=90)
    plan = target.build_target_plan(
        direction="LONG",
        entry=108.0,
        df15m=df15m,
        df1h=df1h,
        sl=106.5,
        tp1=109.125,
        tp2=109.875,
        tp3=111.0,
        market_preferred_direction="LONG",
        setup_kind="TRADE",
    )
    assert plan["technical_target"] > 108.0
    assert plan["expected_move_percent"] > 0
    assert plan["expected_move_high_percent"] >= plan["expected_move_low_percent"] > 0
    assert plan["target_structure_1h"] == "LONG"
    assert plan["target_confidence"] in {"ORTA", "YÜKSEK"}


def test_short_target_is_symmetric():
    df1h = _trend_frame(110.0, 100.0)
    df15m = _trend_frame(106.0, 102.0, rows=90)
    plan = target.build_target_plan(
        direction="SHORT",
        entry=102.0,
        df15m=df15m,
        df1h=df1h,
        sl=103.5,
        tp1=100.875,
        tp2=100.125,
        tp3=99.0,
        market_preferred_direction="SHORT",
        setup_kind="TRADE",
    )
    assert 0 < plan["technical_target"] < 102.0
    assert plan["expected_move_percent"] > 0
    assert plan["target_structure_1h"] == "SHORT"


def test_early_move_without_existing_stop_gets_reference_geometry():
    df1h = _trend_frame(100.0, 110.0)
    df15m = _trend_frame(104.0, 108.0, rows=90)
    plan = target.build_target_plan(
        direction="LONG",
        entry=108.0,
        df15m=df15m,
        df1h=df1h,
        setup_kind="EARLY_MOVE",
    )
    assert 0 < plan["target_reference_sl"] < 108.0
    assert plan["target_reference_tp1"] > 108.0
    assert plan["target_reference_tp2"] > plan["target_reference_tp1"]
    assert plan["target_reference_tp3"] > plan["target_reference_tp2"]
    assert plan["expected_move_percent"] > 0


def test_invalid_direction_returns_no_target():
    assert target.build_target_plan(
        direction="NEUTRAL",
        entry=100.0,
        df15m=None,
        df1h=None,
    ) == {}
