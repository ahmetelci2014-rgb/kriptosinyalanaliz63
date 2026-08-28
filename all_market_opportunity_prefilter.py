"""Fast all-market opportunity prefilter for Simple Core.

Purpose:
- Keep the existing liquid/core scan universe intact.
- Screen the rest of active OKX USDT perpetual markets cheaply.
- Promote only the strongest excluded movers into the expensive 1H/15M/5M
  Simple Core analysis.

This module never sends Telegram messages and never places exchange orders.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, Iterable, List, Tuple

VERSION = "ALL_MARKET_PREFILTER_V1_2026_08_28"

# We deliberately keep a lower liquidity floor for discovery than the main
# 500k USDT core universe, but do not promote ultra-thin contracts to live
# analysis where slippage/manipulation risk is excessive.
DISCOVERY_MIN_24H_QUOTE_VOLUME = 100_000.0

# Only the most active excluded symbols receive a small 5m OHLCV request.
MAX_EXCLUDED_TICKER_SHORTLIST = 70
MAX_EXTRA_DEEP_SCAN = 35

# Activity gates. Passing any one is enough to become an extra deep-scan
# candidate; final trade quality is still decided by Simple Core.
MIN_15M_MOVE_PERCENT = 0.45
MIN_5M_MOVE_PERCENT = 0.25
MIN_VOLUME_SPIKE_RATIO = 1.60

PREFILTER_5M_LIMIT = 24
REQUEST_PAUSE_SECONDS = 0.025


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        number = float(value)
        if number != number:
            return default
        return number
    except Exception:
        return default


def _normalized_symbol(market: Dict[str, Any]) -> str | None:
    if not market.get("swap"):
        return None
    if market.get("active") is False:
        return None
    if str(market.get("quote") or "").upper() != "USDT":
        return None
    settle = str(market.get("settle") or "USDT").upper()
    if settle != "USDT":
        return None
    base = str(market.get("base") or "").upper().strip()
    if not base:
        return None
    return f"{base}USDT"


def _quote_volume(ticker: Dict[str, Any]) -> float:
    quote = _safe_float(ticker.get("quoteVolume"))
    if quote > 0:
        return quote
    base = _safe_float(ticker.get("baseVolume"))
    last = _safe_float(ticker.get("last"))
    if base > 0 and last > 0:
        return base * last
    return 0.0


def _ticker_abs_change_percent(ticker: Dict[str, Any]) -> float:
    pct = _safe_float(ticker.get("percentage"))
    if pct:
        return abs(pct)
    last = _safe_float(ticker.get("last"))
    open_price = _safe_float(ticker.get("open"))
    if last > 0 and open_price > 0:
        return abs(last / open_price - 1.0) * 100.0
    return 0.0


def _ticker_score(quote_volume: float, abs_change_percent: float) -> float:
    # Change identifies activity; log-volume prevents thin contracts from
    # dominating only because of one oversized percentage candle.
    volume_weight = max(0.0, math.log10(max(quote_volume, 1.0)) - 4.0)
    return abs_change_percent * 1.5 + volume_weight


def _closed_ohlcv(raw: Iterable[Iterable[Any]]) -> List[List[float]]:
    rows: List[List[float]] = []
    for candle in raw or []:
        values = list(candle)
        if len(values) < 6:
            continue
        rows.append([
            _safe_float(values[0]),
            _safe_float(values[1]),
            _safe_float(values[2]),
            _safe_float(values[3]),
            _safe_float(values[4]),
            _safe_float(values[5]),
        ])
    # OKX/CCXT normally returns the still-forming candle at the end. Excluding
    # it avoids promoting a market because of a transient unfinished wick.
    return rows[:-1] if len(rows) >= 2 else []


def _micro_activity(raw: Iterable[Iterable[Any]]) -> Dict[str, Any] | None:
    candles = _closed_ohlcv(raw)
    if len(candles) < 16:
        return None

    last = candles[-1]
    last3 = candles[-3:]
    prior8 = candles[-11:-3]
    prior12 = candles[-15:-3]

    open_5m = last[1]
    close_5m = last[4]
    open_15m = last3[0][1]
    close_15m = last3[-1][4]

    if min(open_5m, close_5m, open_15m, close_15m) <= 0:
        return None

    move_5m = abs(close_5m / open_5m - 1.0) * 100.0
    move_15m = abs(close_15m / open_15m - 1.0) * 100.0

    recent_volume = sum(row[5] for row in last3) / 3.0
    baseline_volume = sum(row[5] for row in prior12) / max(len(prior12), 1)
    volume_ratio = recent_volume / baseline_volume if baseline_volume > 0 else 0.0

    prior_high = max(row[2] for row in prior8)
    prior_low = min(row[3] for row in prior8)
    breakout_up = close_5m > prior_high > 0
    breakout_down = close_5m < prior_low and prior_low > 0
    breakout = bool(breakout_up or breakout_down)

    direction = "LONG" if close_15m >= open_15m else "SHORT"
    score = (
        move_15m * 2.0
        + move_5m
        + min(volume_ratio, 4.0) * 0.65
        + (1.25 if breakout else 0.0)
    )

    qualifies = bool(
        move_15m >= MIN_15M_MOVE_PERCENT
        or move_5m >= MIN_5M_MOVE_PERCENT
        or volume_ratio >= MIN_VOLUME_SPIKE_RATIO
        or breakout
    )

    return {
        "qualifies": qualifies,
        "direction_hint": direction,
        "move_5m_percent": round(move_5m, 3),
        "move_15m_percent": round(move_15m, 3),
        "volume_ratio": round(volume_ratio, 2),
        "breakout": breakout,
        "activity_score": round(score, 4),
    }


def build_scan_universe(
    exchange: Any,
    core_symbols: Iterable[str],
) -> Tuple[List[str], Dict[str, Any]]:
    """Return core symbols plus promoted opportunities from excluded markets."""
    core = list(dict.fromkeys(str(item).upper() for item in core_symbols if item))
    core_set = set(core)

    meta: Dict[str, Any] = {
        "version": VERSION,
        "core_count": len(core),
        "active_usdt_swap_count": 0,
        "excluded_count": 0,
        "ticker_eligible_excluded_count": 0,
        "micro_screened_count": 0,
        "promoted_extra_count": 0,
        "promoted_extras": [],
        "errors": [],
    }

    try:
        markets = exchange.load_markets()
    except Exception as exc:
        meta["errors"].append(f"load_markets:{exc}")
        return core, meta

    normalized_to_ccxt: Dict[str, str] = {}
    for market in markets.values():
        normalized = _normalized_symbol(market)
        ccxt_symbol = str(market.get("symbol") or "").strip()
        if normalized and ccxt_symbol:
            normalized_to_ccxt[normalized] = ccxt_symbol

    active_symbols = set(normalized_to_ccxt)
    excluded = sorted(active_symbols - core_set)
    meta["active_usdt_swap_count"] = len(active_symbols)
    meta["excluded_count"] = len(excluded)

    if not excluded:
        return core, meta

    try:
        tickers = exchange.fetch_tickers()
    except Exception as exc:
        meta["errors"].append(f"fetch_tickers:{exc}")
        return core, meta

    ticker_candidates: List[Dict[str, Any]] = []
    for normalized in excluded:
        ccxt_symbol = normalized_to_ccxt.get(normalized)
        ticker = tickers.get(ccxt_symbol) or {}
        quote_volume = _quote_volume(ticker)
        if quote_volume < DISCOVERY_MIN_24H_QUOTE_VOLUME:
            continue

        abs_change = _ticker_abs_change_percent(ticker)
        ticker_candidates.append({
            "symbol": normalized,
            "ccxt_symbol": ccxt_symbol,
            "quote_volume": quote_volume,
            "abs_change_24h_percent": abs_change,
            "ticker_score": _ticker_score(quote_volume, abs_change),
        })

    ticker_candidates.sort(
        key=lambda item: (
            item["ticker_score"],
            item["quote_volume"],
        ),
        reverse=True,
    )
    ticker_candidates = ticker_candidates[:MAX_EXCLUDED_TICKER_SHORTLIST]
    meta["ticker_eligible_excluded_count"] = len(ticker_candidates)

    promoted: List[Dict[str, Any]] = []
    for item in ticker_candidates:
        try:
            raw = exchange.fetch_ohlcv(
                item["ccxt_symbol"],
                timeframe="5m",
                limit=PREFILTER_5M_LIMIT,
            )
            activity = _micro_activity(raw)
            meta["micro_screened_count"] += 1
            if activity and activity["qualifies"]:
                promoted.append({**item, **activity})
        except Exception as exc:
            if len(meta["errors"]) < 8:
                meta["errors"].append(f"{item['symbol']}:{exc}")
        time.sleep(REQUEST_PAUSE_SECONDS)

    promoted.sort(
        key=lambda item: (
            item["activity_score"],
            item["ticker_score"],
        ),
        reverse=True,
    )
    promoted = promoted[:MAX_EXTRA_DEEP_SCAN]

    extras = [item["symbol"] for item in promoted]
    meta["promoted_extra_count"] = len(extras)
    meta["promoted_extras"] = [
        {
            "symbol": item["symbol"],
            "direction_hint": item["direction_hint"],
            "quote_volume": round(item["quote_volume"], 2),
            "abs_change_24h_percent": round(item["abs_change_24h_percent"], 2),
            "move_5m_percent": item["move_5m_percent"],
            "move_15m_percent": item["move_15m_percent"],
            "volume_ratio": item["volume_ratio"],
            "breakout": item["breakout"],
            "activity_score": item["activity_score"],
        }
        for item in promoted
    ]

    return core + extras, meta
