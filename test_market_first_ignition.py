from __future__ import annotations

import pandas as pd

import market_first_ignition as ignition
import market_first_ignition_hooks as hooks
import market_first_strategy as strategy


def _one_minute_frame(*, weak_volume: bool = False) -> pd.DataFrame:
    rows = []
    for i in range(30):
        open_price = 100.00
        high = 100.20
        low = 99.80
        close = 100.02
        volume = 100.0
        rows.append([open_price, high, low, close, volume])
    # Small directional pressure while price is still pressing the old range edge.
    rows[-5] = [100.05, 100.12, 100.03, 100.09, 110.0]
    rows[-4] = [100.07, 100.15, 100.05, 100.11, 115.0]
    rows[-3] = [100.08, 100.17, 100.07, 100.14, 130.0 if not weak_volume else 100.0]
    rows[-2] = [100.12, 100.19, 100.10, 100.16, 150.0 if not weak_volume else 100.0]
    rows[-1] = [100.15, 100.20, 100.13, 100.18, 180.0 if not weak_volume else 100.0]
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


def _five_minute_frame() -> pd.DataFrame:
    rows = []
    for i in range(31):
        if i < 26:
            rows.append([100.0, 100.55, 99.45, 100.0, 1000.0])
        else:
            rows.append([100.0, 100.22, 99.78, 100.0, 1000.0])
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"])


def _context() -> strategy.MarketContext:
    return strategy.MarketContext(
        regime="BULL",
        preferred_direction="LONG",
        score=22.0,
        strength=22.0,
        breadth_5m=0.58,
        breadth_24h=0.62,
        major_move_5m_percent=0.03,
        allow_countertrend=True,
        majors={},
    )


def _long_structure(*args, **kwargs):
    return {
        "direction": "LONG",
        "extension_atr": 0.45,
        "atr": 0.30,
        "swing_low_12": 99.7,
        "swing_high_12": 100.2,
        "range_low_72": 98.0,
        "range_high_72": 103.0,
    }


def test_detects_prebreakout_before_large_momentum(monkeypatch):
    monkeypatch.setattr(ignition.strategy, "_structure", _long_structure)
    decision, reason, diag = ignition.detect_ignition(
        None,
        "NO_ACCELERATION",
        symbol="TESTUSDT",
        df1m=_one_minute_frame(),
        df5m=_five_minute_frame(),
        df15m=_five_minute_frame(),
        df1h=_five_minute_frame(),
        current_price=100.18,
        quote_volume_24h=25_000_000,
        context=_context(),
    )
    assert reason == "OK"
    assert diag["promoted"] is True
    assert decision is not None
    assert decision["stage"] == "EARLY"
    assert decision["trade_eligible"] is False
    assert decision["ignition_setup"] is True
    assert decision["direction"] == "LONG"
    assert abs(decision["move_5m_percent"]) < 0.20
    assert decision["distance_to_breakout_percent"] < 0.10
    assert decision["compression_ratio_5m"] < ignition.MAX_COMPRESSION_RATIO


def test_rejects_quiet_setup_without_volume_waking(monkeypatch):
    monkeypatch.setattr(ignition.strategy, "_structure", _long_structure)
    decision, reason, diag = ignition.detect_ignition(
        None,
        "NO_ACCELERATION",
        symbol="TESTUSDT",
        df1m=_one_minute_frame(weak_volume=True),
        df5m=_five_minute_frame(),
        df15m=_five_minute_frame(),
        df1h=_five_minute_frame(),
        current_price=100.18,
        quote_volume_24h=25_000_000,
        context=_context(),
    )
    assert decision is None
    assert reason == "NO_ACCELERATION"
    assert diag["reason"] == "IGNITION_VOLUME_NOT_WAKING"


def test_scout_lane_prefers_small_liquid_aligned_move():
    good = {"symbol": "GOODUSDT", "quote_volume": 20_000_000, "change_24h": 4.0}
    zero = {"symbol": "ZEROUSDT", "quote_volume": 20_000_000, "change_24h": 4.0}
    illiquid = {"symbol": "ILLUSDT", "quote_volume": 100_000, "change_24h": 4.0}
    moves = {"GOODUSDT": 0.18, "ZEROUSDT": 0.0, "ILLUSDT": 0.18}
    assert hooks._scout_priority(good, moves) > 0
    assert hooks._scout_priority(zero, moves) < 0
    assert hooks._scout_priority(illiquid, moves) < 0
