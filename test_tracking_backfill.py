from __future__ import annotations

import tracking_backfill as backfill


class FakeExchange:
    def __init__(self, rows, hard_page_limit=None):
        self.rows = list(rows)
        self.hard_page_limit = hard_page_limit
        self.calls = []

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls.append((symbol, timeframe, since, limit))
        effective = int(limit or 100)
        if self.hard_page_limit is not None:
            effective = min(effective, self.hard_page_limit)
        start_ms = int(since or 0)
        eligible = [row for row in self.rows if int(row[0]) >= start_ms]
        return eligible[:effective]


def candles(minutes):
    return [
        [minute * 60_000, 100.0, 101.0, 99.0, 100.5, 10.0]
        for minute in range(minutes + 1)
    ]


def test_long_gap_uses_pages_and_recovers_full_range():
    exchange = FakeExchange(candles(220), hard_page_limit=100)
    legacy_calls = []

    def legacy(exchange, symbol, timeframe, since_seconds, limit=180):
        legacy_calls.append((symbol, timeframe, since_seconds, limit))
        return [{"time": -1}]

    wrapped = backfill.make_gap_safe_fetcher(
        legacy,
        to_okx_symbol=lambda symbol: symbol,
        now_fn=lambda: 220 * 60,
    )

    rows = wrapped(exchange, "BTCUSDT", "1m", 0, limit=180)

    assert legacy_calls == []
    assert len(rows) == 221
    assert rows[0]["time"] == 0
    assert rows[-1]["time"] == 220 * 60
    assert len(exchange.calls) >= 3
    assert [row["time"] for row in rows] == sorted({row["time"] for row in rows})


def test_normal_gap_keeps_legacy_behavior():
    exchange = FakeExchange(candles(60))
    legacy_calls = []

    def legacy(exchange, symbol, timeframe, since_seconds, limit=180):
        legacy_calls.append((symbol, timeframe, since_seconds, limit))
        return [{"time": 123}]

    wrapped = backfill.make_gap_safe_fetcher(
        legacy,
        to_okx_symbol=lambda symbol: symbol,
        now_fn=lambda: 60 * 60,
    )

    rows = wrapped(exchange, "ETHUSDT", "1m", 0, limit=180)

    assert rows == [{"time": 123}]
    assert legacy_calls == [("ETHUSDT", "1m", 0, 180)]
    assert exchange.calls == []


def test_expected_count_detects_gap_larger_than_single_limit():
    assert backfill.expected_candle_count("1m", 0, 181 * 60) > 180
    assert backfill.expected_candle_count("5m", 0, 60 * 60) < 180
