from pathlib import Path
from unittest.mock import patch

import pandas as pd

import market_first_swing_2h as swing
import market_first_strategy as strategy


def _frame(rows=110, start=100.0, step=0.08):
    data = []
    price = start
    for i in range(rows):
        open_price = price
        close = price + step
        data.append({
            "open": open_price,
            "high": max(open_price, close) + 0.10,
            "low": min(open_price, close) - 0.10,
            "close": close,
            "volume": 1000 + i,
        })
        price = close
    return pd.DataFrame(data)


def _context(preferred=None, regime="CHOP"):
    return strategy.MarketContext(
        regime=regime,
        preferred_direction=preferred,
        score=20.0 if preferred == "LONG" else -20.0 if preferred == "SHORT" else 0.0,
        strength=20.0 if preferred else 0.0,
        breadth_5m=0.5,
        breadth_24h=0.5,
        major_move_5m_percent=0.0,
        allow_countertrend=True,
        majors={},
    )


def _structure(direction, *, ema20=100.0, ema50=99.5, atr=0.7, volume=1.0, swing_low=98.8, swing_high=101.2, range_low=95.0, range_high=106.0, extension=0.6):
    return {
        "direction": direction,
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "extension_atr": extension,
        "volume_ratio": volume,
        "swing_low_12": swing_low,
        "swing_high_12": swing_high,
        "range_low_72": range_low,
        "range_high_72": range_high,
    }


def test_existing_110_hour_payload_builds_2h_without_extra_exchange_call():
    result = swing.aggregate_1h_to_2h(_frame())
    assert result is not None
    assert len(result) == 55
    assert set(["open", "high", "low", "close", "volume"]).issubset(result.columns)


def test_2h_and_1h_long_can_warn_while_15m_is_still_pullback_short():
    df1h = _frame()
    s2h = _structure("LONG", extension=0.5)
    s1h = _structure("LONG", extension=0.6)
    s15 = _structure("SHORT", ema20=100.1, ema50=99.8, atr=0.7, volume=1.2, swing_low=98.7, range_high=106.0)
    s5 = _structure("SHORT", volume=0.9)
    with patch.object(swing, "_two_hour_structure", return_value=s2h), patch.object(
        strategy, "_structure", side_effect=[s1h, s15, s5]
    ):
        plan, reason = swing.evaluate_swing_preparation(
            symbol="AAVEUSDT",
            df5m=object(),
            df15m=object(),
            df1h=df1h,
            current_price=100.2,
            quote_volume_24h=10_000_000,
            context=_context("LONG", "BULL"),
        )
    assert reason == "OK"
    assert plan["status"] == "SWING_PREP"
    assert plan["direction"] == "LONG"
    assert plan["structure_15m"] == "SHORT"
    assert plan["shadow_only"] is True


def test_short_side_is_symmetric():
    df1h = _frame(step=-0.08)
    s2h = _structure("SHORT", ema20=100.0, ema50=100.5, extension=0.5)
    s1h = _structure("SHORT", ema20=100.0, ema50=100.5, extension=0.6)
    s15 = _structure("NEUTRAL", ema20=100.0, ema50=100.5, atr=0.7, volume=1.2, swing_high=101.2, range_low=94.0)
    s5 = _structure("LONG", volume=0.9)
    with patch.object(swing, "_two_hour_structure", return_value=s2h), patch.object(
        strategy, "_structure", side_effect=[s1h, s15, s5]
    ):
        plan, reason = swing.evaluate_swing_preparation(
            symbol="XPLUSDT",
            df5m=object(),
            df15m=object(),
            df1h=df1h,
            current_price=99.8,
            quote_volume_24h=10_000_000,
            context=_context("SHORT", "BEAR"),
        )
    assert reason == "OK"
    assert plan["direction"] == "SHORT"
    assert plan["tp1"] < plan["ideal_entry"] < plan["sl"]


def test_2h_1h_disagreement_does_not_alert():
    df1h = _frame()
    with patch.object(swing, "_two_hour_structure", return_value=_structure("LONG")), patch.object(
        strategy, "_structure", side_effect=[_structure("SHORT"), _structure("NEUTRAL"), _structure("LONG")]
    ):
        plan, reason = swing.evaluate_swing_preparation(
            symbol="AAAUSDT",
            df5m=object(), df15m=object(), df1h=df1h,
            current_price=100.0, quote_volume_24h=10_000_000,
            context=_context(),
        )
    assert plan is None
    assert reason == "SWING_2H_1H_NOT_ALIGNED"


def test_active_swing_symbols_are_kept_in_deep_scan_without_growing_cap():
    ledger = {"episodes": {
        "x": {"symbol": "AAVEUSDT", "direction": "LONG", "first_at": 1000, "resolved": False},
        "y": {"symbol": "APTUSDT", "direction": "LONG", "first_at": 1000, "resolved": False},
    }}
    rows = [{"symbol": symbol} for symbol in ["AAVEUSDT", "APTUSDT", "ARBUSDT", "XPLUSDT"]]
    selected = ["ARBUSDT", "XPLUSDT"]
    result = swing.prioritize_active_symbols(selected, rows, ledger, now=1100, max_total=3)
    assert result == ["AAVEUSDT", "APTUSDT", "ARBUSDT"]
    assert len(result) == 3


def test_live_workflow_halves_launches_but_keeps_two_5m_cycles():
    text = Path(".github/workflows/main.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch" not in text
    assert 'cron: "*/10 * * * *"' in text
    assert "for cycle in 1 2" in text
    assert "sleep 300" in text
    assert "market_first_swing_2h_ledger.json" in text
