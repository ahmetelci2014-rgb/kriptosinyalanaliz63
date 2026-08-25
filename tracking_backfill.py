"""Gap-safe OHLCV backfill for Premium open-trade tracking.

This module changes no entry/exit rule. It only replaces ``main.fetch_candles_since``
with a paginated equivalent when the elapsed gap is larger than the caller's
single-request candle limit. Normal short gaps keep using the original function.
"""
from __future__ import annotations

import math
import os
import time
from typing import Any, Callable, Dict, Iterable, List, Optional

VERSION = "TRACKING_BACKFILL_V1_2026_08_25"
MAX_BACKFILL_CANDLES = int(os.getenv("TRACKING_BACKFILL_MAX_CANDLES", "1500"))
MAX_BACKFILL_PAGES = int(os.getenv("TRACKING_BACKFILL_MAX_PAGES", "8"))
EXCHANGE_PAGE_LIMIT = 300

TIMEFRAME_SECONDS = {
    "1m": 60,
    "3m": 3 * 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
    "2h": 2 * 60 * 60,
    "4h": 4 * 60 * 60,
}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _normalize_rows(rows: Iterable[Iterable[Any]], start_seconds: int) -> List[Dict[str, float]]:
    dedup: Dict[int, Dict[str, float]] = {}
    for item in rows or []:
        try:
            if not isinstance(item, (list, tuple)) or len(item) < 5:
                continue
            candle_time = int(float(item[0]) / 1000)
            if candle_time < start_seconds:
                continue
            dedup[candle_time] = {
                "time": candle_time,
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
            }
        except Exception:
            continue
    return [dedup[key] for key in sorted(dedup)]


def expected_candle_count(
    timeframe: str,
    since_seconds: int,
    end_seconds: Optional[int] = None,
) -> Optional[int]:
    seconds = TIMEFRAME_SECONDS.get(str(timeframe or ""))
    if not seconds:
        return None
    end_value = int(end_seconds if end_seconds is not None else time.time())
    start_value = max(0, int(since_seconds))
    if end_value <= start_value:
        return 1
    return max(1, int(math.ceil((end_value - start_value) / seconds)) + 2)


def fetch_paginated(
    exchange: Any,
    symbol: str,
    timeframe: str,
    since_seconds: int,
    *,
    to_okx_symbol: Callable[[str], str],
    end_seconds: Optional[int] = None,
    max_candles: int = MAX_BACKFILL_CANDLES,
    max_pages: int = MAX_BACKFILL_PAGES,
) -> List[Dict[str, float]]:
    """Fetch a continuous OHLCV range in chronological pages.

    The returned shape matches ``main.fetch_candles_since``. Deduplication by
    candle timestamp makes overlapping OKX pages harmless.
    """
    timeframe_seconds = TIMEFRAME_SECONDS.get(str(timeframe or ""))
    if not timeframe_seconds:
        return []

    start_value = max(0, int(since_seconds))
    end_value = int(end_seconds if end_seconds is not None else time.time())
    cursor = start_value
    page_count = 0
    raw_by_time: Dict[int, List[Any]] = {}
    market_symbol = to_okx_symbol(symbol)

    while (
        cursor <= end_value
        and len(raw_by_time) < max(1, int(max_candles))
        and page_count < max(1, int(max_pages))
    ):
        remaining = max(1, int(max_candles) - len(raw_by_time))
        request_limit = min(EXCHANGE_PAGE_LIMIT, remaining)
        batch = exchange.fetch_ohlcv(
            market_symbol,
            timeframe=timeframe,
            since=cursor * 1000,
            limit=request_limit,
        )
        page_count += 1
        if not batch:
            break

        latest = cursor
        accepted = 0
        for item in batch:
            try:
                candle_time = int(float(item[0]) / 1000)
            except Exception:
                continue
            if candle_time < start_value:
                continue
            if candle_time > end_value + timeframe_seconds:
                continue
            raw_by_time[candle_time] = list(item)
            latest = max(latest, candle_time)
            accepted += 1

        next_cursor = latest + timeframe_seconds
        if accepted <= 0 or next_cursor <= cursor:
            break
        cursor = next_cursor

        # A page already reached the current/ending candle.
        if latest >= end_value - timeframe_seconds:
            break

    rows = _normalize_rows(raw_by_time.values(), start_value)
    if len(rows) > max_candles:
        rows = rows[:max_candles]
    return rows


def make_gap_safe_fetcher(
    original: Callable[..., Any],
    *,
    to_okx_symbol: Callable[[str], str],
    now_fn: Callable[[], int] = lambda: int(time.time()),
) -> Callable[..., Any]:
    if getattr(original, "_tracking_backfill_wrapped", False):
        return original

    def wrapped(
        exchange: Any,
        symbol: str,
        timeframe: str,
        since_seconds: int,
        limit: int = 180,
    ) -> Any:
        now_value = int(now_fn())
        expected = expected_candle_count(timeframe, since_seconds, now_value)
        requested_limit = max(1, _safe_int(limit, 180))

        # Preserve the existing behavior for ordinary workflow cadence.
        if expected is None or expected <= requested_limit:
            return original(
                exchange,
                symbol,
                timeframe,
                since_seconds,
                limit=requested_limit,
            )

        rows = fetch_paginated(
            exchange,
            symbol,
            timeframe,
            since_seconds,
            to_okx_symbol=to_okx_symbol,
            end_seconds=now_value,
        )

        if rows:
            gap_minutes = max(0, int((now_value - int(since_seconds)) / 60))
            print(
                "TRACKING BACKFILL:",
                symbol,
                timeframe,
                "gap_min=",
                gap_minutes,
                "expected=",
                expected,
                "fetched=",
                len(rows),
            )
            if len(rows) < min(expected - 1, MAX_BACKFILL_CANDLES):
                print(
                    "TRACKING BACKFILL UYARI:",
                    symbol,
                    "beklenen aralığın tamamı gelmemiş olabilir.",
                )
            return rows

        # Network/adapter edge case: retain legacy fallback rather than losing
        # tracking completely for this run.
        return original(
            exchange,
            symbol,
            timeframe,
            since_seconds,
            limit=requested_limit,
        )

    wrapped._tracking_backfill_wrapped = True  # type: ignore[attr-defined]
    wrapped._tracking_backfill_version = VERSION  # type: ignore[attr-defined]
    return wrapped


def install(bot: Any) -> bool:
    original = getattr(bot, "fetch_candles_since", None)
    converter = getattr(bot, "to_okx_symbol", None)
    if not callable(original) or not callable(converter):
        return False
    if getattr(original, "_tracking_backfill_wrapped", False):
        return True
    bot.fetch_candles_since = make_gap_safe_fetcher(
        original,
        to_okx_symbol=converter,
        now_fn=bot.now_ts,
    )
    print(
        "Tracking Backfill:",
        VERSION,
        "| uzun workflow boşluklarında eksik mumlar sayfalı tamamlanır",
    )
    return True
