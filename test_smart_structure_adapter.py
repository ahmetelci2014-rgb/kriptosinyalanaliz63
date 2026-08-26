import pandas as pd

import smart_structure_adapter as smart


def _frame():
    rows = []
    for i in range(30):
        base = 100.0 + i * 0.01
        high = 101.0
        low = 99.0
        close = 100.0 + (i % 3) * 0.02
        if i == 22:
            high = 102.0
            close = 101.5
        if i == 23:
            high = 102.0
            close = 101.4
        if i == 24:
            high = 103.0
            close = 102.5
        rows.append({"open": base, "high": high, "low": low, "close": close, "volume": 1000 + i * 5})
    return pd.DataFrame(rows)


def test_two_step_up_break_confirms_trend():
    states = smart.compute_state_frame(_frame(), entry_period=5, trend_period=5)
    assert not states.empty
    assert bool(states.iloc[22]["smart_watch_long_started"])
    assert int(states.iloc[23]["smart_watch_dir"]) == 1
    assert bool(states.iloc[24]["smart_confirm_long"])
    assert int(states.iloc[24]["smart_trend"]) == 1


def test_latest_features_excludes_open_candle():
    df = _frame()
    closed = smart.latest_features(df, entry_period=5, trend_period=5, exclude_open_candle=True)
    mutated = df.copy()
    mutated.loc[len(mutated) - 1, ["high", "close", "volume"]] = [999.0, 999.0, 999999.0]
    mutated_features = smart.latest_features(mutated, entry_period=5, trend_period=5, exclude_open_candle=True)
    assert closed == mutated_features
