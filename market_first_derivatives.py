"""OKX derivatives/order-flow context for the single Market First system.

This module deliberately does not create a second strategy and does not hard
block trades. It enriches Market First decisions with public derivatives data:
- Open Interest change (5m / 15m),
- normalized Funding Rate,
- recent taker buy/sell imbalance.

The values are persisted as ML features so the tree model can learn whether
these relationships actually improve this system's own outcomes. A very small
soft confirmation score is also exposed for candidate ranking; hard market,
risk, late-entry, cooldown and portfolio rules remain authoritative.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, Mapping, Optional, Sequence


DEFAULT_FUNDING_INTERVAL_HOURS = 8.0
MAX_REASONABLE_FUNDING_INTERVAL_HOURS = 24.0
TAKER_TRADE_LIMIT = 120


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


def _taker_metrics(exchange: Any, symbol: str) -> tuple[bool, float, float, float, int, int, Optional[str]]:
    if not _method_supported(exchange, "fetchTrades", "fetch_trades"):
        return False, 0.0, 0.0, 0.0, 0, 0, "TRADES_UNSUPPORTED"

    try:
        rows = exchange.fetch_trades(symbol, limit=TAKER_TRADE_LIMIT)
        if not isinstance(rows, Sequence):
            return False, 0.0, 0.0, 0.0, 0, 0, "TRADES_EMPTY"
        buy_quote = 0.0
        sell_quote = 0.0
        timestamps = []
        count = 0
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
            if side == "buy":
                buy_quote += quote
            else:
                sell_quote += quote
            count += 1
            ts = _timestamp_ms(item)
            if ts > 0:
                timestamps.append(ts)

        total = buy_quote + sell_quote
        if total <= 0 or count < 5:
            return False, buy_quote, sell_quote, 0.0, count, 0, "TRADES_SHORT"
        imbalance = (buy_quote - sell_quote) / total
        window_seconds = 0
        if len(timestamps) >= 2:
            window_seconds = max(0, int((max(timestamps) - min(timestamps)) / 1000))
        return (
            True,
            round(buy_quote, 4),
            round(sell_quote, 4),
            round(imbalance, 6),
            count,
            window_seconds,
            None,
        )
    except Exception as exc:
        return False, 0.0, 0.0, 0.0, 0, 0, f"TRADES:{type(exc).__name__}"


def _soft_score(oi_available: bool, oi5: float, oi15: float, taker_available: bool, taker_alignment: float) -> int:
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
    return max(-4, min(5, score))


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
        taker_error,
    ) = _taker_metrics(exchange, symbol)
    if taker_error:
        errors.append(taker_error)

    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    taker_alignment = taker_imbalance * sign
    soft = _soft_score(
        oi_available,
        oi5,
        oi15,
        taker_available,
        taker_alignment,
    )
    available = bool(oi_available or funding_available or taker_available)
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
            "derivatives_soft_score": snapshot.soft_score,
            "derivatives_errors": list(snapshot.errors),
        }
    )
    return decision


def compact_confirmation(values: Mapping[str, Any]) -> str:
    parts = []
    if values.get("oi_history_available"):
        parts.append(f"OI15 { _sf(values.get('oi_change_15m_percent')):+.2f}%")
    if values.get("taker_available"):
        aligned = _sf(values.get("taker_imbalance_alignment")) * 100.0
        parts.append(f"Taker {aligned:+.0f}%")
    if values.get("funding_available"):
        parts.append(f"Funding { _sf(values.get('funding_rate_8h_bps')):+.2f}bp/8s")
    return " | ".join(parts)
