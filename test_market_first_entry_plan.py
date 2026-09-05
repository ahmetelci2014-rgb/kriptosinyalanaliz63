from unittest.mock import patch

import market_first_entry_plan as plan
import market_first_strategy as strategy


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


def _structure(direction, *, ema20=100.0, ema50=99.5, atr=0.5, volume=1.0, swing_low=99.2, swing_high=100.8, range_low=96.0, range_high=104.0):
    return {
        "direction": direction,
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "extension_atr": 0.3,
        "volume_ratio": volume,
        "swing_low_12": swing_low,
        "swing_high_12": swing_high,
        "range_low_72": range_low,
        "range_high_72": range_high,
    }


def test_long_pullback_becomes_preparation_before_confirmation():
    s5 = _structure("SHORT")
    s15 = _structure("LONG")
    s1h = _structure("LONG")
    with patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]):
        result, reason = plan.evaluate_entry_plan(
            symbol="AAAUSDT",
            df5m=object(),
            df15m=object(),
            df1h=object(),
            current_price=100.0,
            quote_volume_24h=5_000_000,
            context=_context(),
        )
    assert reason == "OK"
    assert result["direction"] == "LONG"
    assert result["status"] == "PREP"
    assert result["zone_low"] < 100.0 < result["zone_high"]


def test_long_zone_with_5m_confirmation_becomes_entry():
    s5 = _structure("LONG")
    s15 = _structure("LONG")
    s1h = _structure("LONG")
    with patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]):
        result, reason = plan.evaluate_entry_plan(
            symbol="AAAUSDT",
            df5m=object(),
            df15m=object(),
            df1h=object(),
            current_price=100.0,
            quote_volume_24h=5_000_000,
            context=_context("LONG"),
        )
    assert reason == "OK"
    assert result["status"] == "ENTRY"
    assert result["direction"] == "LONG"
    assert result["sl"] < result["current_price"] < result["tp1"]
    assert result["score"] >= plan.ENTRY_MIN_SCORE


def test_short_pullback_becomes_preparation_before_confirmation():
    s5 = _structure("LONG", ema50=100.5)
    s15 = _structure("SHORT", ema50=100.5)
    s1h = _structure("SHORT", ema50=100.5)
    with patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]):
        result, reason = plan.evaluate_entry_plan(
            symbol="BBBUSDT",
            df5m=object(),
            df15m=object(),
            df1h=object(),
            current_price=100.0,
            quote_volume_24h=5_000_000,
            context=_context(),
        )
    assert reason == "OK"
    assert result["direction"] == "SHORT"
    assert result["status"] == "PREP"


def test_short_zone_with_5m_confirmation_becomes_entry():
    s5 = _structure("SHORT", ema50=100.5)
    s15 = _structure("SHORT", ema50=100.5)
    s1h = _structure("SHORT", ema50=100.5)
    with patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]):
        result, reason = plan.evaluate_entry_plan(
            symbol="BBBUSDT",
            df5m=object(),
            df15m=object(),
            df1h=object(),
            current_price=100.0,
            quote_volume_24h=5_000_000,
            context=_context("SHORT"),
        )
    assert reason == "OK"
    assert result["status"] == "ENTRY"
    assert result["direction"] == "SHORT"
    assert result["tp1"] < result["current_price"] < result["sl"]
    assert result["score"] >= plan.ENTRY_MIN_SCORE


def test_long_move_far_above_planned_zone_is_chased_not_entry():
    s5 = _structure("LONG", ema20=100.0, ema50=99.5, atr=0.5, swing_low=99.2)
    s15 = _structure("LONG", ema20=99.8, ema50=99.0)
    s1h = _structure("LONG", ema20=99.5, ema50=99.0)
    with patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]):
        result, reason = plan.evaluate_entry_plan(
            symbol="CCCUSDT",
            df5m=object(),
            df15m=object(),
            df1h=object(),
            current_price=101.0,
            quote_volume_24h=5_000_000,
            context=_context("LONG"),
        )
    assert reason == "OK"
    assert result["status"] == "CHASED"


def test_higher_timeframes_must_agree_before_any_plan():
    s5 = _structure("LONG")
    s15 = _structure("LONG")
    s1h = _structure("SHORT")
    with patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]):
        result, reason = plan.evaluate_entry_plan(
            symbol="DDDUSDT",
            df5m=object(),
            df15m=object(),
            df1h=object(),
            current_price=100.0,
            quote_volume_24h=5_000_000,
            context=_context(),
        )
    assert result is None
    assert reason == "PLAN_HIGHER_TF_NOT_ALIGNED"


def test_entry_plan_promotes_into_existing_ready_shape():
    candidate = {
        "symbol": "AAAUSDT",
        "direction": "LONG",
        "status": "ENTRY",
        "score": 80,
        "current_price": 100.0,
        "quote_volume_24h": 5_000_000,
        "market_regime": "CHOP",
        "market_label": "KARIŞIK",
        "market_score": 0.0,
        "market_strength": 0.0,
        "market_preferred_direction": None,
        "market_breadth_5m": 0.5,
        "major_move_5m_percent": 0.0,
        "extension_atr_5m": 0.3,
        "structure_5m": "LONG",
        "structure_15m": "LONG",
        "structure_1h": "LONG",
        "volume_ratio_5m": 1.0,
        "zone_low": 99.9,
        "zone_high": 100.1,
        "ideal_entry": 100.0,
        "sl": 99.2,
        "tp1": 100.44,
        "tp2": 100.84,
        "tp3": 101.28,
        "risk_percent": 0.8,
        "room_r": 5.0,
    }
    promoted = plan.promote_to_decision(None, candidate)
    assert promoted["stage"] == "READY"
    assert promoted["trade_eligible"] is True
    assert promoted["entry_plan_trade"] is True
    assert promoted["direction"] == "LONG"
