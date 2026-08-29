import pandas as pd

from market_first_historical_ml_seed import (
    _even_sample,
    resolve_historical_outcome,
    select_eval_times,
)


def _bars(rows):
    return pd.DataFrame(
        rows,
        columns=["time", "open", "high", "low", "close", "volume"],
    )


def test_even_sample_preserves_edges_and_order():
    values = list(range(10))
    sampled = _even_sample(values, 4)
    assert sampled[0] == 0
    assert sampled[-1] == 9
    assert sampled == sorted(sampled)
    assert len(sampled) == 4


def test_select_eval_times_is_no_future_and_capped():
    rows = []
    ts = 0
    price = 100.0
    for idx in range(140):
        open_price = price
        # Every fourth 5m candle has a visible move, enough for the cheap prefilter.
        close = open_price * (1.004 if idx % 4 == 0 else 1.0002)
        high = max(open_price, close) * 1.001
        low = min(open_price, close) * 0.999
        rows.append([ts, open_price, high, low, close, 1000.0])
        price = close
        ts += 5 * 60_000

    frame = _bars(rows)
    end_ms = int(frame.iloc[110]["time"]) + 5 * 60_000
    result = select_eval_times(frame, 0, end_ms, max_events=7)

    assert len(result) <= 7
    assert result == sorted(result)
    assert all(value <= end_ms for value in result)


def test_outcome_long_tp1_first_is_positive_after_cost():
    future = _bars(
        [
            [0, 100.0, 100.4, 99.8, 100.2, 1.0],
            [300000, 100.2, 101.0, 100.1, 100.8, 1.0],
        ]
    )
    result = resolve_historical_outcome(
        "LONG",
        entry=100.0,
        sl=99.0,
        tp1=100.75,
        risk_percent=1.0,
        future5=future,
        round_trip_cost_percent=0.12,
    )
    assert result["label"] == 1
    assert result["result"] == "HIST_TP1_FIRST"
    assert result["net_r"] > 0


def test_outcome_short_sl_first_is_negative():
    future = _bars(
        [[0, 100.0, 101.2, 99.7, 100.8, 1.0]]
    )
    result = resolve_historical_outcome(
        "SHORT",
        entry=100.0,
        sl=101.0,
        tp1=99.25,
        risk_percent=1.0,
        future5=future,
    )
    assert result["label"] == 0
    assert result["result"] == "HIST_SL_FIRST"
    assert result["net_r"] < -1.0


def test_same_bar_tp_and_sl_is_not_used_for_training():
    future = _bars(
        [[0, 100.0, 101.2, 98.8, 100.0, 1.0]]
    )
    result = resolve_historical_outcome(
        "LONG",
        entry=100.0,
        sl=99.0,
        tp1=100.75,
        risk_percent=1.0,
        future5=future,
    )
    assert result["label"] is None
    assert result["result"] == "AMBIGUOUS_SAME_BAR"


def test_timeout_label_uses_directional_net_r():
    future = _bars(
        [
            [0, 100.0, 100.3, 99.8, 100.2, 1.0],
            [300000, 100.2, 100.5, 100.0, 100.4, 1.0],
        ]
    )
    result = resolve_historical_outcome(
        "LONG",
        entry=100.0,
        sl=98.0,
        tp1=101.5,
        risk_percent=2.0,
        future5=future,
        round_trip_cost_percent=0.12,
    )
    assert result["result"] == "HIST_TIMEOUT_NET_R"
    assert result["label"] == 1
    assert result["net_r"] > 0
