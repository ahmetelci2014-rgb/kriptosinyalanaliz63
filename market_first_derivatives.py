"""OKX derivatives/order-flow context for the single Market First system.

This module deliberately does not create a second strategy and does not hard
block trades. It enriches Market First decisions with public derivatives data:
- Open Interest change (5m / 15m),
- normalized Funding Rate,
- recent taker buy/sell imbalance,
- short-window CVD impulse from the same public trades,
- near-price order-book imbalance and opposing-wall concentration.

The values are persisted as ML features so the tree model can learn whether
these relationships actually improve this system's own outcomes. A small soft
confirmation score is exposed for candidate ranking; hard market, risk,
late-entry, cooldown and portfolio rules remain authoritative.

Order-book observations are intentionally soft only. Visible walls can be
cancelled/spoofed, so a single snapshot is never allowed to veto a trade.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from statistics import median
from typing import Any, Dict, Mapping, Optional, Sequence


DEFAULT_FUNDING_INTERVAL_HOURS = 8.0
MAX_REASONABLE_FUNDING_INTERVAL_HOURS = 24.0
TAKER_TRADE_LIMIT = 120
CVD_MIN_TRADES = 20
ORDER_BOOK_LIMIT = 50
ORDER_BOOK_MAX_DISTANCE_PERCENT = 1.0


@dataclass(frozen=True)
class DerivativesSnapshot:
    symbol: str
    derivatives_available: bool
    oi_history_available: bool
    oi_change_5m_percent: float
    oi_change_15m_percent: float
    open_interest_value: float
    funding_available: bool
    funding_rate: float
    funding_interval_hours: float
    funding_rate_8h_bps: float
    taker_available: bool
    taker_buy_quote: float
    taker_sell_quote: float
    taker_imbalance: float
    taker_trade_count: int
    taker_window_seconds: int
    cvd_available: bool
    cvd_quote: float
    cvd_ratio: float
    cvd_impulse: float
    book_available: bool
    book_bid_quote: float
    book_ask_quote: float
    book_imbalance: float
    book_opposing_wall_ratio: float
    book_depth_levels: int
    soft_score: int
    errors: tuple[str, ...]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _pct(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return (end / start - 1.0) * 100.0


def _method_supported(exchange: Any, camel_name: str, snake_name: str) -> bool:
    method = getattr(exchange, snake_name, None)
    if not callable(method):
        return False
    has = getattr(exchange, "has", None)
    if isinstance(has, Mapping) and has.get(camel_name) is False:
        return False
    return True


def _oi_value(item: Mapping[str, Any]) -> float:
    for key in (
        "openInterestValue",
        "openInterestAmount",
        "openInterest",
        "value",
        "amount",
    ):
        value = _sf(item.get(key))
        if value > 0:
            return value
    info = item.get("info")
    if isinstance(info, Mapping):
        for key in ("oiUsd", "oiCcy", "oi"):
            value = _sf(info.get(key))
            if value > 0:
                return value
    return 0.0


def _timestamp_ms(item: Mapping[str, Any]) -> int:
    try:
        value = item.get("timestamp")
        if value not in (None, ""):
            return int(float(value))
    except Exception:
        pass
    return 0


def _open_interest_metrics(exchange: Any, symbol: str) -> tuple[bool, float, float, float, Optional[str]]:
    if not _method_supported(
        exchange,
        "fetchOpenInterestHistory",
        "fetch_open_interest_history",
    ):
        return False, 0.0, 0.0, 0.0, "OI_HISTORY_UNSUPPORTED"

    try:
        rows = exchange.fetch_open_interest_history(
            symbol,
            timeframe="5m",
            limit=5,
        )
        if not isinstance(rows, Sequence):
            return False, 0.0, 0.0, 0.0, "OI_HISTORY_EMPTY"

        normalized = []
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            value = _oi_value(item)
            if value <= 0:
                continue
            normalized.append((_timestamp_ms(item), value))
        if len(normalized) < 2:
            return False, 0.0, 0.0, 0.0, "OI_HISTORY_SHORT"

        normalized.sort(key=lambda pair: pair[0])
        values = [value for _, value in normalized]
        latest = values[-1]
        change_5m = _pct(values[-2], latest)
        anchor_15m = values[-4] if len(values) >= 4 else values[0]
        change_15m = _pct(anchor_15m, latest)
        return True, round(change_5m, 5), round(change_15m, 5), latest, None
    except Exception as exc:
        return False, 0.0, 0.0, 0.0, f"OI:{type(exc).__name__}"


def _funding_metrics(exchange: Any, symbol: str) -> tuple[bool, float, float, float, Optional[str]]:
    if not _method_supported(exchange, "fetchFundingRate", "fetch_funding_rate"):
        return False, 0.0, DEFAULT_FUNDING_INTERVAL_HOURS, 0.0, "FUNDING_UNSUPPORTED"

    try:
        item = exchange.fetch_funding_rate(symbol)
        if not isinstance(item, Mapping):
            return False, 0.0, DEFAULT_FUNDING_INTERVAL_HOURS, 0.0, "FUNDING_EMPTY"
        rate = _sf(item.get("fundingRate"))
        funding_ts = _sf(item.get("fundingTimestamp"))
        next_ts = _sf(item.get("nextFundingTimestamp"))
        interval_hours = DEFAULT_FUNDING_INTERVAL_HOURS
        if next_ts > funding_ts > 0:
            candidate = (next_ts - funding_ts) / 3_600_000.0
            if 0 < candidate <= MAX_REASONABLE_FUNDING_INTERVAL_HOURS:
                interval_hours = candidate
        # Normalize all contracts to an 8-hour equivalent so 1h/2h/4h and 8h
        # contracts are comparable to the learning model.
        rate_8h_bps = rate * 10_000.0 * (8.0 / interval_hours)
        return (
            True,
            round(rate, 10),
            round(interval_hours, 4),
            round(rate_8h_bps, 6),
            None,
        )
    except Exception as exc:
        return False, 0.0, DEFAULT_FUNDING_INTERVAL_HOURS, 0.0, f"FUNDING:{type(exc).__name__}"


def _flow_metrics(
    exchange: Any,
    symbol: str,
) -> tuple[bool, float, float, float, int, int, bool, float, float, float, Optional[str]]:
    """Recent aggressive flow + normalized CVD impulse from the same trades.

    Taker imbalance answers "who dominated this small trade sample?". CVD
    impulse answers "is that aggressive pressure strengthening or weakening?"
    by comparing the first and second half of the chronological trade window.
    """
    if not _method_supported(exchange, "fetchTrades", "fetch_trades"):
        return False, 0.0, 0.0, 0.0, 0, 0, False, 0.0, 0.0, 0.0, "TRADES_UNSUPPORTED"

    try:
        rows = exchange.fetch_trades(symbol, limit=TAKER_TRADE_LIMIT)
        if not isinstance(rows, Sequence):
            return False, 0.0, 0.0, 0.0, 0, 0, False, 0.0, 0.0, 0.0, "TRADES_EMPTY"

        normalized: list[tuple[int, float]] = []
        buy_quote = 0.0
        sell_quote = 0.0
        timestamps = []
        count = 0
        sequence_fallback = 0

        for item in rows:
            if not isinstance(item, Mapping):
                continue
            side = str(item.get("side") or "").lower()
            if side not in {"buy", "sell"}:
                continue
            amount = _sf(item.get("amount"))
            price = _sf(item.get("price"))
            cost = _sf(item.get("cost"))
            quote = cost if cost > 0 else amount * price
            if quote <= 0:
                continue

            signed_quote = quote if side == "buy" else -quote
            if side == "buy":
                buy_quote += quote
            else:
                sell_quote += quote

            ts = _timestamp_ms(item)
            if ts <= 0:
                sequence_fallback += 1
                ts = sequence_fallback
            else:
                timestamps.append(ts)
            normalized.append((ts, signed_quote))
            count += 1

        total = buy_quote + sell_quote
        if total <= 0 or count < 5:
            return False, buy_quote, sell_quote, 0.0, count, 0, False, 0.0, 0.0, 0.0, "TRADES_SHORT"

        imbalance = (buy_quote - sell_quote) / total
        window_seconds = 0
        if len(timestamps) >= 2:
            window_seconds = max(0, int((max(timestamps) - min(timestamps)) / 1000))

        normalized.sort(key=lambda pair: pair[0])
        cvd_quote = sum(value for _, value in normalized)
        cvd_ratio = cvd_quote / total if total > 0 else 0.0
        cvd_available = len(normalized) >= CVD_MIN_TRADES
        cvd_impulse = 0.0
        if cvd_available:
            midpoint = max(1, len(normalized) // 2)
            early = normalized[:midpoint]
            late = normalized[midpoint:]

            def half_ratio(items: Sequence[tuple[int, float]]) -> float:
                absolute = sum(abs(value) for _, value in items)
                signed = sum(value for _, value in items)
                return signed / absolute if absolute > 0 else 0.0

            early_ratio = half_ratio(early)
            late_ratio = half_ratio(late)
            cvd_impulse = late_ratio - early_ratio

        return (
            True,
            round(buy_quote, 4),
            round(sell_quote, 4),
            round(imbalance, 6),
            count,
            window_seconds,
            cvd_available,
            round(cvd_quote, 4),
            round(cvd_ratio, 6),
            round(cvd_impulse, 6),
            None,
        )
    except Exception as exc:
        return False, 0.0, 0.0, 0.0, 0, 0, False, 0.0, 0.0, 0.0, f"TRADES:{type(exc).__name__}"


def _book_metrics(
    exchange: Any,
    symbol: str,
    direction: str,
) -> tuple[bool, float, float, float, float, int, Optional[str]]:
    """Depth-weighted near-price order-book context.

    Only levels inside 1% of mid are used. Closer levels receive more weight.
    Opposing wall concentration is a ratio versus the median same-side level;
    it is stored for ML/soft ranking only because visible walls can disappear.
    """
    if not _method_supported(exchange, "fetchOrderBook", "fetch_order_book"):
        return False, 0.0, 0.0, 0.0, 0.0, 0, "ORDER_BOOK_UNSUPPORTED"

    try:
        book = exchange.fetch_order_book(symbol, limit=ORDER_BOOK_LIMIT)
        if not isinstance(book, Mapping):
            return False, 0.0, 0.0, 0.0, 0.0, 0, "ORDER_BOOK_EMPTY"
        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not isinstance(bids, Sequence) or not isinstance(asks, Sequence) or not bids or not asks:
            return False, 0.0, 0.0, 0.0, 0.0, 0, "ORDER_BOOK_SHORT"

        best_bid = _sf(bids[0][0] if isinstance(bids[0], Sequence) and bids[0] else 0.0)
        best_ask = _sf(asks[0][0] if isinstance(asks[0], Sequence) and asks[0] else 0.0)
        if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
            return False, 0.0, 0.0, 0.0, 0.0, 0, "ORDER_BOOK_BAD_TOP"
        mid = (best_bid + best_ask) / 2.0
        if mid <= 0:
            return False, 0.0, 0.0, 0.0, 0.0, 0, "ORDER_BOOK_BAD_MID"

        def side_metrics(levels: Sequence[Any], is_bid: bool) -> tuple[float, list[float], int]:
            weighted_quote = 0.0
            raw_levels: list[float] = []
            used = 0
            for level in levels:
                if not isinstance(level, Sequence) or len(level) < 2:
                    continue
                price = _sf(level[0])
                amount = _sf(level[1])
                if price <= 0 or amount <= 0:
                    continue
                distance = ((mid - price) if is_bid else (price - mid)) / mid * 100.0
                if distance < 0:
                    distance = 0.0
                if distance > ORDER_BOOK_MAX_DISTANCE_PERCENT:
                    continue
                quote = price * amount
                if quote <= 0:
                    continue
                # 1.0 at top-of-book, 0.5 at the 1% boundary.
                weight = 1.0 - 0.5 * min(1.0, distance / ORDER_BOOK_MAX_DISTANCE_PERCENT)
                weighted_quote += quote * weight
                raw_levels.append(quote)
                used += 1
            return weighted_quote, raw_levels, used

        bid_quote, bid_levels, bid_count = side_metrics(bids, True)
        ask_quote, ask_levels, ask_count = side_metrics(asks, False)
        total = bid_quote + ask_quote
        if total <= 0 or bid_count < 2 or ask_count < 2:
            return False, bid_quote, ask_quote, 0.0, 0.0, bid_count + ask_count, "ORDER_BOOK_DEPTH_SHORT"

        imbalance = (bid_quote - ask_quote) / total
        opposing_levels = ask_levels if str(direction).upper() == "LONG" else bid_levels
        opposing_wall_ratio = 0.0
        if opposing_levels:
            med = median(opposing_levels)
            if med > 0:
                opposing_wall_ratio = max(opposing_levels) / med

        return (
            True,
            round(bid_quote, 4),
            round(ask_quote, 4),
            round(imbalance, 6),
            round(min(20.0, max(0.0, opposing_wall_ratio)), 4),
            bid_count + ask_count,
            None,
        )
    except Exception as exc:
        return False, 0.0, 0.0, 0.0, 0.0, 0, f"ORDER_BOOK:{type(exc).__name__}"


def _soft_score(
    oi_available: bool,
    oi5: float,
    oi15: float,
    taker_available: bool,
    taker_alignment: float,
    cvd_available: bool,
    cvd_impulse_alignment: float,
    book_available: bool,
    book_alignment: float,
    opposing_wall_ratio: float,
) -> int:
    """Small ranking hint only; never a hard admission rule."""
    score = 0
    if oi_available:
        if oi5 >= 0.20:
            score += 2
        elif oi5 <= -0.50:
            score -= 1
        if oi15 >= 0.50:
            score += 1
        elif oi15 <= -1.00:
            score -= 1

    if taker_available:
        if taker_alignment >= 0.18:
            score += 2
        elif taker_alignment <= -0.18:
            score -= 2

    if cvd_available:
        if cvd_impulse_alignment >= 0.30:
            score += 1
        elif cvd_impulse_alignment <= -0.30:
            score -= 1

    if book_available:
        if book_alignment >= 0.12:
            score += 1
        elif book_alignment <= -0.12:
            score -= 1
        # A concentrated opposing wall is only a mild penalty; order books can
        # be spoofed, so this can never become a hard block by itself.
        if opposing_wall_ratio >= 8.0:
            score -= 2
        elif opposing_wall_ratio >= 4.0:
            score -= 1

    return max(-7, min(7, score))


def fetch_derivatives_snapshot(exchange: Any, symbol: str, direction: str) -> DerivativesSnapshot:
    errors: list[str] = []
    oi_available, oi5, oi15, oi_value, oi_error = _open_interest_metrics(exchange, symbol)
    if oi_error:
        errors.append(oi_error)

    funding_available, funding_rate, funding_hours, funding_8h_bps, funding_error = _funding_metrics(exchange, symbol)
    if funding_error:
        errors.append(funding_error)

    (
        taker_available,
        buy_quote,
        sell_quote,
        taker_imbalance,
        taker_count,
        taker_window,
        cvd_available,
        cvd_quote,
        cvd_ratio,
        cvd_impulse,
        taker_error,
    ) = _flow_metrics(exchange, symbol)
    if taker_error:
        errors.append(taker_error)

    (
        book_available,
        book_bid_quote,
        book_ask_quote,
        book_imbalance,
        opposing_wall_ratio,
        book_depth_levels,
        book_error,
    ) = _book_metrics(exchange, symbol, direction)
    if book_error:
        errors.append(book_error)

    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    taker_alignment = taker_imbalance * sign
    cvd_impulse_alignment = cvd_impulse * sign
    book_alignment = book_imbalance * sign
    soft = _soft_score(
        oi_available,
        oi5,
        oi15,
        taker_available,
        taker_alignment,
        cvd_available,
        cvd_impulse_alignment,
        book_available,
        book_alignment,
        opposing_wall_ratio,
    )
    available = bool(
        oi_available
        or funding_available
        or taker_available
        or cvd_available
        or book_available
    )
    return DerivativesSnapshot(
        symbol=symbol,
        derivatives_available=available,
        oi_history_available=oi_available,
        oi_change_5m_percent=oi5,
        oi_change_15m_percent=oi15,
        open_interest_value=round(oi_value, 4),
        funding_available=funding_available,
        funding_rate=funding_rate,
        funding_interval_hours=funding_hours,
        funding_rate_8h_bps=funding_8h_bps,
        taker_available=taker_available,
        taker_buy_quote=buy_quote,
        taker_sell_quote=sell_quote,
        taker_imbalance=taker_imbalance,
        taker_trade_count=taker_count,
        taker_window_seconds=taker_window,
        cvd_available=cvd_available,
        cvd_quote=cvd_quote,
        cvd_ratio=cvd_ratio,
        cvd_impulse=cvd_impulse,
        book_available=book_available,
        book_bid_quote=book_bid_quote,
        book_ask_quote=book_ask_quote,
        book_imbalance=book_imbalance,
        book_opposing_wall_ratio=opposing_wall_ratio,
        book_depth_levels=book_depth_levels,
        soft_score=soft,
        errors=tuple(errors),
    )


def enrich_decision(decision: Dict[str, Any], snapshot: DerivativesSnapshot) -> Dict[str, Any]:
    sign = 1.0 if str(decision.get("direction") or "").upper() == "LONG" else -1.0
    decision.update(
        {
            "derivatives_available": snapshot.derivatives_available,
            "oi_history_available": snapshot.oi_history_available,
            "oi_change_5m_percent": snapshot.oi_change_5m_percent,
            "oi_change_15m_percent": snapshot.oi_change_15m_percent,
            "open_interest_value": snapshot.open_interest_value,
            "funding_available": snapshot.funding_available,
            "funding_rate": snapshot.funding_rate,
            "funding_interval_hours": snapshot.funding_interval_hours,
            "funding_rate_8h_bps": snapshot.funding_rate_8h_bps,
            # Positive means funding crowding is in the candidate direction.
            "funding_crowding_8h_bps": round(snapshot.funding_rate_8h_bps * sign, 6),
            "taker_available": snapshot.taker_available,
            "taker_buy_quote": snapshot.taker_buy_quote,
            "taker_sell_quote": snapshot.taker_sell_quote,
            "taker_imbalance": snapshot.taker_imbalance,
            # Positive means recent aggressive flow is aligned with the candidate.
            "taker_imbalance_alignment": round(snapshot.taker_imbalance * sign, 6),
            "taker_trade_count": snapshot.taker_trade_count,
            "taker_window_seconds": snapshot.taker_window_seconds,
            "cvd_available": snapshot.cvd_available,
            "cvd_quote": snapshot.cvd_quote,
            "cvd_ratio": snapshot.cvd_ratio,
            "cvd_impulse": snapshot.cvd_impulse,
            # Positive means aggressive flow is strengthening in candidate direction.
            "cvd_impulse_alignment": round(snapshot.cvd_impulse * sign, 6),
            "book_available": snapshot.book_available,
            "book_bid_quote": snapshot.book_bid_quote,
            "book_ask_quote": snapshot.book_ask_quote,
            "book_imbalance": snapshot.book_imbalance,
            # Positive means near-price visible depth favors candidate direction.
            "book_imbalance_alignment": round(snapshot.book_imbalance * sign, 6),
            "book_opposing_wall_ratio": snapshot.book_opposing_wall_ratio,
            "book_depth_levels": snapshot.book_depth_levels,
            "derivatives_soft_score": snapshot.soft_score,
            "derivatives_errors": list(snapshot.errors),
        }
    )
    return decision


def compact_confirmation(values: Mapping[str, Any]) -> str:
    # Keep Telegram compact. CVD/order-book stay mostly in diagnostics/ML instead
    # of turning the user message back into a technical wall of text.
    parts = []
    if values.get("oi_history_available"):
        parts.append(f"OI15 { _sf(values.get('oi_change_15m_percent')):+.2f}%")
    if values.get("taker_available"):
        aligned = _sf(values.get("taker_imbalance_alignment")) * 100.0
        parts.append(f"Taker {aligned:+.0f}%")
    if values.get("funding_available"):
        parts.append(f"Funding { _sf(values.get('funding_rate_8h_bps')):+.2f}bp/8s")
    return " | ".join(parts)
