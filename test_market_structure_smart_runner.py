import market_structure_smart_runner as smart_runner


def _event(direction, trend):
    return {
        "result": {
            "symbol": "BTCUSDT",
            "direction": direction,
            "smart_structure": {"trend": trend, "trend_break_count": 2},
        }
    }


def test_long_is_blocked_only_by_down_smart_trend():
    assert smart_runner.smart_context_opposes(_event("LONG", -1)) is True
    assert smart_runner.smart_context_opposes(_event("LONG", 0)) is False
    assert smart_runner.smart_context_opposes(_event("LONG", 1)) is False


def test_short_is_blocked_only_by_up_smart_trend():
    assert smart_runner.smart_context_opposes(_event("SHORT", 1)) is True
    assert smart_runner.smart_context_opposes(_event("SHORT", 0)) is False
    assert smart_runner.smart_context_opposes(_event("SHORT", -1)) is False
