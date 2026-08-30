"""Pre-breakout ignition detector for the single Market First system.

The normal candidate engine is intentionally momentum-driven, so it can first see
some moves only after price has already expanded. This module adds an earlier,
non-trade EARLY observation path built from information available *before* or at
the first edge break:
- recent 5m range compression,
- price pressing near the prior 20m range edge,
- rising 1m/3m volume versus its own baseline,
- small but aligned 1m/3m/5m pressure,
- 5m/15m/1h structure and market regime.

It never creates an exchange order and never bypasses the ordinary fast-entry,
risk, major-market, liquidity, duplicate or portfolio guards. A true pre-breakout
setup is surfaced only as EARLY; if the break actually starts, the existing fast
entry path may promote it using its normal risk rules.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

import market_first_strategy as strategy

VERSION = "MARKET_FIRST_IGNITION_V1_2026_08_30"

MIN_IGNITION_SCORE = 58
MIN_QUOTE_VOLUME = 500_000.0
MAX_ABS_MOVE_5_PERCENT = 1.20
MAX_EXTENSION_ATR = 1.05
MAX_EDGE_DISTANCE_PERCENT = 0.45
MAX_EDGE_OVERSHOOT_PERCENT = 0.35
MAX_COMPRESSION_RATIO = 0.80
MIN_VOLUME_3M_RATIO = 1.10
MIN_DIRECTIONAL_3M_PERCENT = 0.025
MIN_DIRECTIONAL_5M_PERCENT = 0.035


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


def _normalize(df: Any) -> Optional[pd.DataFrame]:
    return strategy._normalize_frame(df)


def _volume_state(frame: pd.DataFrame) -> Tuple[float, float]:
    if len(frame) < 26:
        return 0.0, 0.0
    baseline = frame.iloc[-24:-4]
    values = [_sf(value) for value in baseline["volume"].tolist() if _sf(value) > 0]
    median = float(pd.Series(values).median()) if values else 0.0
    if median <= 0:
        return 0.0, 0.0
    current_ratio = _sf(frame.iloc[-1]["volume"]) / median
    recent3_ratio = float(frame.tail(3)["volume"].astype(float).mean()) / median
    return current_ratio, recent3_ratio


def _compression_ratio(df5m: Any) -> Optional[float]:
    closed = strategy._closed_frame(df5m, min_len=24)
    if closed is None or len(closed) < 20:
        return None
    recent = closed.tail(4)
    baseline = closed.iloc[-16:-4]
    recent_ranges = ((recent["high"] - recent["low"]) / recent["close"].replace(0, pd.NA) * 100.0).dropna()
    base_ranges = ((baseline["high"] - baseline["low"]) / baseline["close"].replace(0, pd.NA) * 100.0).dropna()
    if recent_ranges.empty or base_ranges.empty:
        return None
    base = float(base_ranges.median())
    if base <= 0:
        return None
    return float(recent_ranges.mean()) / base


def detect_ignition(
    decision: Optional[Mapping[str, Any]],
    reason: str,
    *,
    symbol: str,
    df1m: Any,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
    quote_volume_24h: float,
    context: strategy.MarketContext,
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    """Rescue a quiet pre-breakout setup as EARLY, never directly as a trade."""
    diagnostics: Dict[str, Any] = {"promoted": False, "version": VERSION}
    if isinstance(decision, Mapping):
        diagnostics["reason"] = "IGNITION_EXISTING_DECISION"
        return dict(decision), reason, diagnostics
    if str(reason or "") not in {"NO_ACCELERATION", "LOW_SCORE"}:
        diagnostics["reason"] = "IGNITION_REASON_NOT_ELIGIBLE"
        return None, reason, diagnostics
    if _sf(quote_volume_24h) < MIN_QUOTE_VOLUME:
        diagnostics["reason"] = "IGNITION_LIQUIDITY_LOW"
        return None, reason, diagnostics

    f1 = _normalize(df1m)
    if f1 is None or len(f1) < 26 or current_price <= 0:
        diagnostics["reason"] = "IGNITION_1M_DATA"
        return None, reason, diagnostics

    open1 = _sf(f1.iloc[-1]["open"])
    open3 = _sf(f1.iloc[-3]["open"])
    open5 = _sf(f1.iloc[-5]["open"])
    if min(open1, open3, open5) <= 0:
        diagnostics["reason"] = "IGNITION_PRICE_DATA"
        return None, reason, diagnostics

    move1 = _pct(open1, current_price)
    move3 = _pct(open3, current_price)
    move5 = _pct(open5, current_price)
    if abs(move5) > MAX_ABS_MOVE_5_PERCENT:
        diagnostics["reason"] = "IGNITION_ALREADY_MOVING"
        return None, reason, diagnostics

    prior20 = f1.iloc[-24:-4]
    prior_high = _sf(prior20["high"].max())
    prior_low = _sf(prior20["low"].min())
    if min(prior_high, prior_low) <= 0 or prior_high <= prior_low:
        diagnostics["reason"] = "IGNITION_RANGE_DATA"
        return None, reason, diagnostics
    position = (current_price - prior_low) / (prior_high - prior_low)

    # Direction is inferred only when price pressure and range location agree.
    direction = ""
    if move3 >= MIN_DIRECTIONAL_3M_PERCENT and move5 >= MIN_DIRECTIONAL_5M_PERCENT and position >= 0.60:
        direction = "LONG"
    elif move3 <= -MIN_DIRECTIONAL_3M_PERCENT and move5 <= -MIN_DIRECTIONAL_5M_PERCENT and position <= 0.40:
        direction = "SHORT"
    elif position >= 0.82 and move3 >= 0 and move5 >= 0.02:
        direction = "LONG"
    elif position <= 0.18 and move3 <= 0 and move5 <= -0.02:
        direction = "SHORT"
    if not direction:
        diagnostics["reason"] = "IGNITION_DIRECTION_UNCLEAR"
        return None, reason, diagnostics

    if direction == "LONG":
        edge_distance = max(0.0, (prior_high - current_price) / current_price * 100.0)
        overshoot = max(0.0, (current_price - prior_high) / prior_high * 100.0)
        breakout = current_price > prior_high
    else:
        edge_distance = max(0.0, (current_price - prior_low) / current_price * 100.0)
        overshoot = max(0.0, (prior_low - current_price) / prior_low * 100.0)
        breakout = current_price < prior_low
    if edge_distance > MAX_EDGE_DISTANCE_PERCENT or overshoot > MAX_EDGE_OVERSHOOT_PERCENT:
        diagnostics["reason"] = "IGNITION_TOO_FAR_FROM_EDGE"
        return None, reason, diagnostics

    compression = _compression_ratio(df5m)
    if compression is None or compression > MAX_COMPRESSION_RATIO:
        diagnostics["reason"] = "IGNITION_NO_COMPRESSION"
        return None, reason, diagnostics

    volume1, volume3 = _volume_state(f1)
    if volume3 < MIN_VOLUME_3M_RATIO and volume1 < 1.40:
        diagnostics["reason"] = "IGNITION_VOLUME_NOT_WAKING"
        return None, reason, diagnostics

    s5 = strategy._structure(df5m, current_price)
    s15 = strategy._structure(df15m, current_price)
    s1h = strategy._structure(df1h, current_price)
    if s5 is None or s15 is None or s1h is None:
        diagnostics["reason"] = "IGNITION_STRUCTURE_DATA"
        return None, reason, diagnostics
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if str(s15.get("direction")) == opposite or str(s1h.get("direction")) == opposite:
        diagnostics["reason"] = "IGNITION_HIGHER_TF_OPPOSED"
        return None, reason, diagnostics
    if str(s5.get("direction")) == opposite:
        diagnostics["reason"] = "IGNITION_5M_OPPOSED"
        return None, reason, diagnostics

    extension = _sf(s5.get("extension_atr"))
    if extension > MAX_EXTENSION_ATR:
        diagnostics["reason"] = "IGNITION_EXTENSION_HIGH"
        return None, reason, diagnostics

    market_points, market_allowed = strategy._market_component(direction, context)
    if not market_allowed:
        diagnostics["reason"] = "IGNITION_MARKET_OPPOSED"
        return None, reason, diagnostics

    relative = strategy._relative_strength(direction, move5, context.major_move_5m_percent)
    signed1 = move1 if direction == "LONG" else -move1
    signed3 = move3 if direction == "LONG" else -move3
    signed5 = move5 if direction == "LONG" else -move5

    score = 0
    score += 18 if compression <= 0.55 else 14 if compression <= 0.68 else 10
    score += 14 if edge_distance <= 0.12 else 11 if edge_distance <= 0.25 else 8
    score += 10 if volume3 >= 1.80 else 8 if volume3 >= 1.40 else 6
    score += 5 if signed3 >= 0.10 else 3 if signed3 >= 0.05 else 1
    score += 4 if signed5 >= 0.18 else 2 if signed5 >= 0.08 else 0
    if breakout:
        score += 5

    for structure, good, neutral in ((s5, 8, 3), (s15, 8, 2), (s1h, 6, 2)):
        if str(structure.get("direction")) == direction:
            score += good
        elif str(structure.get("direction")) == "NEUTRAL":
            score += neutral

    score += max(-8, min(14, int(market_points)))
    if relative >= 0.35:
        score += 6
    elif relative >= 0.15:
        score += 3
    elif relative < -0.15:
        score -= 3

    if quote_volume_24h >= 20_000_000:
        score += 4
    elif quote_volume_24h >= 5_000_000:
        score += 3
    elif quote_volume_24h >= 1_000_000:
        score += 1

    score = int(max(0, min(100, round(score))))
    diagnostics.update({
        "direction": direction,
        "score": score,
        "compression_ratio_5m": round(compression, 4),
        "distance_to_breakout_percent": round(edge_distance, 4),
        "edge_overshoot_percent": round(overshoot, 4),
        "range_position_20m": round(position, 4),
        "volume_ratio_1m": round(volume1, 3),
        "volume_ratio_3m": round(volume3, 3),
        "move3": round(signed3, 4),
        "move5": round(signed5, 4),
        "breakout": breakout,
    })
    if score < MIN_IGNITION_SCORE:
        diagnostics["reason"] = "IGNITION_SCORE_LOW"
        return None, reason, diagnostics

    current: Dict[str, Any] = {
        "symbol": str(symbol),
        "direction": direction,
        "source": strategy.SOURCE,
        "score": score,
        "stage": "EARLY",
        "current_price": round(current_price, 10),
        "quote_volume_24h": round(_sf(quote_volume_24h), 2),
        "market_regime": context.regime,
        "market_label": strategy.market_label(context),
        "market_score": context.score,
        "market_strength": context.strength,
        "market_preferred_direction": context.preferred_direction,
        "market_breadth_5m": context.breadth_5m,
        "market_breadth_24h": context.breadth_24h,
        "major_move_5m_percent": context.major_move_5m_percent,
        "independent_move": False,
        "move_1m_percent": round(move1, 4),
        "move_3m_percent": round(move3, 4),
        "move_5m_percent": round(move5, 4),
        "volume_ratio_1m": round(volume1, 3),
        "volume_ratio_3m": round(volume3, 3),
        "breakout_20m": bool(breakout),
        "relative_strength_5m": round(relative, 4),
        "extension_atr_5m": round(extension, 3),
        "structure_5m": str(s5.get("direction") or "NEUTRAL"),
        "structure_15m": str(s15.get("direction") or "NEUTRAL"),
        "structure_1h": str(s1h.get("direction") or "NEUTRAL"),
        "alert_eligible": True,
        "trade_eligible": False,
        "risk_reject_reason": None,
        "ignition_setup": True,
        "ignition_version": VERSION,
        "compression_ratio_5m": round(compression, 4),
        "distance_to_breakout_percent": round(edge_distance, 4),
        "edge_overshoot_percent": round(overshoot, 4),
        "range_position_20m": round(position, 4),
    }
    diagnostics["promoted"] = True
    diagnostics["reason"] = "IGNITION_EARLY"
    return current, "OK", diagnostics
