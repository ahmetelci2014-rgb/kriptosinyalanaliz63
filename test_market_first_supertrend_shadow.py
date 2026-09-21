import pandas as pd

import market_first_supertrend_shadow as shadow


def _trend_frame(direction: str, rows: int = 90) -> pd.DataFrame:
    values = []
    for index in range(rows):
        close = 100.0 + index * 0.5 if direction == "LONG" else 145.0 - index * 0.5
        values.append(
            {
                "open": close - 0.1 if direction == "LONG" else close + 0.1,
                "high": close + 0.4,
                "low": close - 0.4,
                "close": close,
                "volume": 1000.0 + index,
            }
        )
    return pd.DataFrame(values)


def test_supertrend_detects_clean_up_and_down_trends():
    up = shadow.supertrend_snapshot(_trend_frame("LONG"))
    down = shadow.supertrend_snapshot(_trend_frame("SHORT"))

    assert up is not None and up["direction"] == "LONG"
    assert down is not None and down["direction"] == "SHORT"
    assert up["line"] > 0 and down["line"] > 0


def test_shadow_alignment_is_symmetric():
    up = _trend_frame("LONG")
    down = _trend_frame("SHORT")

    aligned_long = shadow.evaluate_shadow("LONG", up, up)
    aligned_short = shadow.evaluate_shadow("SHORT", down, down)
    opposed = shadow.evaluate_shadow("LONG", down, down)
    mixed = shadow.evaluate_shadow("LONG", up, down)

    assert aligned_long["supertrend_shadow_alignment"] == "ALIGNED_BOTH"
    assert aligned_short["supertrend_shadow_alignment"] == "ALIGNED_BOTH"
    assert opposed["supertrend_shadow_alignment"] == "OPPOSED_BOTH"
    assert mixed["supertrend_shadow_alignment"] == "MIXED"


def test_shadow_is_explicitly_non_blocking():
    summary = shadow.summary()

    assert summary["changes_trade_admission"] is False
    assert summary["telegram"] is False
    assert summary["atr_period"] == 10
    assert summary["multiplier"] == 3.0
