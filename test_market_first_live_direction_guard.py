from market_first_strategy import MarketContext
import market_first_live_direction_guard as guard


def _context(regime, preferred, breadth):
    return MarketContext(
        regime=regime,
        preferred_direction=preferred,
        score=30.0 if preferred == "LONG" else -30.0,
        strength=30.0,
        breadth_5m=breadth,
        breadth_24h=0.5,
        major_move_5m_percent=0.0,
        allow_countertrend=True,
        majors={},
    )


def test_normal_bull_preference_is_neutralized_when_breadth_collapses():
    context, diag = guard.neutralize_breadth_conflict(_context("BULL", "LONG", 0.29))
    assert diag["active"] is True
    assert diag["reason"] == "BULL_BREADTH_CONFLICT"
    assert context.regime == "BULL"
    assert context.preferred_direction is None


def test_normal_bear_preference_is_neutralized_symmetrically_when_breadth_surges():
    context, diag = guard.neutralize_breadth_conflict(_context("BEAR", "SHORT", 0.71))
    assert diag["active"] is True
    assert diag["reason"] == "BEAR_BREADTH_CONFLICT"
    assert context.regime == "BEAR"
    assert context.preferred_direction is None


def test_strong_regime_is_not_weakened_by_breadth_guard():
    original = _context("BULL_STRONG", "LONG", 0.29)
    context, diag = guard.neutralize_breadth_conflict(original)
    assert diag["active"] is False
    assert context is original
    assert context.preferred_direction == "LONG"


def test_render_virtual_like_zero_micro_movement_cannot_promote_entry_plan():
    decision = {
        "direction": "LONG",
        "move_3m_percent": 0.0,
        "move_5m_percent": 0.0,
        "relative_strength_5m": 0.0,
        "breakout_20m": False,
    }
    allowed, diag = guard.fresh_entry_plan_confirmation(decision, "LONG")
    assert allowed is False
    assert diag["reason"] == "STALE_MICRO_NO_ENTRY"


def test_entry_plan_can_promote_when_fresh_movement_aligns():
    decision = {
        "direction": "SHORT",
        "move_3m_percent": -0.12,
        "move_5m_percent": -0.18,
        "relative_strength_5m": 0.05,
        "breakout_20m": False,
    }
    allowed, diag = guard.fresh_entry_plan_confirmation(decision, "SHORT")
    assert allowed is True
    assert diag["confirmations"]["move_3m"] is True
    assert diag["confirmations"]["move_5m"] is True


def test_virtual_like_long_is_blocked_by_opposing_taker_and_cvd():
    result = guard.evaluate_live_flow_veto(
        {
            "direction": "LONG",
            "taker_available": True,
            "taker_imbalance_alignment": -0.425666,
            "cvd_available": True,
            "cvd_ratio": -0.425666,
            "book_available": True,
            "book_opposing_wall_ratio": 6.101,
        }
    )
    assert result["blocked"] is True
    assert result["reason"] == "LIVE_FLOW_TAKER_CVD_OPPOSE"


def test_short_is_blocked_by_mirror_image_buy_pressure():
    result = guard.evaluate_live_flow_veto(
        {
            "direction": "SHORT",
            "taker_available": True,
            "taker_imbalance": 0.42,
            "cvd_available": True,
            "cvd_ratio": 0.36,
            "book_available": False,
        }
    )
    assert result["blocked"] is True
    assert result["reason"] == "LIVE_FLOW_TAKER_CVD_OPPOSE"


def test_single_order_book_wall_never_blocks_without_opposing_taker_flow():
    result = guard.evaluate_live_flow_veto(
        {
            "direction": "LONG",
            "taker_available": True,
            "taker_imbalance_alignment": 0.08,
            "cvd_available": True,
            "cvd_ratio": -0.10,
            "book_available": True,
            "book_opposing_wall_ratio": 9.0,
        }
    )
    assert result["blocked"] is False
    assert result["reason"] == "LIVE_FLOW_OK"


def test_missing_flow_data_fails_open():
    result = guard.evaluate_live_flow_veto({"direction": "LONG"})
    assert result["blocked"] is False
