import pandas as pd

from market_first_historical_replay_v2 import select_eval_times_v2, _quote_volume


def _frame(count=140):
    rows = []
    ts = 0
    price = 100.0
    for idx in range(count):
        open_price = price
        close = open_price * (1.003 if idx % 5 == 0 else 1.0001)
        high = max(open_price, close) * 1.001
        low = min(open_price, close) * 0.999
        rows.append([ts, open_price, high, low, close, 1000.0])
        price = close
        ts += 5 * 60_000
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])


def test_minute_capture_contains_offsets_inside_event_bar():
    frame = _frame()
    start = 0
    end = int(frame.iloc[-1]["time"]) + 5 * 60_000
    result = select_eval_times_v2(frame, start, end, max_events=200)

    assert result == sorted(result)
    assert len(result) > 0
    # Minute-level replay should contain timestamps not limited to 5m boundaries.
    assert any(value % (5 * 60_000) != 0 for value in result)
    assert all(start <= value <= end for value in result)


def test_minute_capture_respects_cap():
    frame = _frame(220)
    end = int(frame.iloc[-1]["time"]) + 5 * 60_000
    result = select_eval_times_v2(frame, 0, end, max_events=17)
    assert len(result) <= 17


def test_quote_volume_uses_base_volume_fallback():
    ticker = {"quoteVolume": None, "baseVolume": 25.0, "last": 4.0}
    assert _quote_volume(ticker) == 100.0
