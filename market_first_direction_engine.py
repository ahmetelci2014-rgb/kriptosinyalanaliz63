"""Dual-direction evidence engine for Market First V5.

The scanner first finds an interesting coin.  This module then scores LONG and
SHORT independently from the same current evidence instead of inheriting the
higher-timeframe direction blindly.  A contrary direction may become a
preparation candidate, but never becomes an automatic reverse trade: normal 5m
zone confirmation and every existing pre-send guard still have to pass.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_entry_plan as entry_plan
import market_first_strategy as strategy

VERSION = "MARKET_FIRST_DIRECTION_ENGINE_V2_2026_09_05"

MIN_SELECTED_SCORE = 70
MIN_SELECTED_MARGIN = 12
MIN_REVERSAL_SCORE = 82
MIN_REVERSAL_MARGIN = 18
MIN_REVERSAL_CONFIRMATIONS = 3


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _clip_score(value: float) -> int:
    return int(round(max(0.0, min(100.0, value))))


def _sign(direction: str) -> float:
    return 1.0 if str(direction).upper() == "LONG" else -1.0


def _opposite(direction: str) -> str:
    return "SHORT" if str(direction).upper() == "LONG" else "LONG"


def _structure_points(structure_direction: str, direction: str, aligned: int, opposed: int) -> int:
    structure_direction = str(structure_direction or "").upper()
    if structure_direction == direction:
        return aligned
    if structure_direction == _opposite(direction):
        return -opposed
    return 0


def _move_points(aligned_move: float, weak: float, strong: float, weak_points: int, strong_points: int) -> int:
    if aligned_move >= strong:
        return strong_points
    if aligned_move >= weak:
        return weak_points
    if aligned_move <= -strong:
        return -strong_points
    if aligned_move <= -weak:
        return -weak_points
    return 0


def _flow_points(alignment: float, weak: float, strong: float, weak_points: int, strong_points: int) -> int:
    if alignment >= strong:
        return strong_points
    if alignment >= weak:
        return weak_points
    if alignment <= -strong:
        return -strong_points
    if alignment <= -weak:
        return -weak_points
    return 0


def score_from_evidence(
    *,
    direction: str,
    decision: Mapping[str, Any],
    structures: Mapping[str, Mapping[str, Any]],
    context: strategy.MarketContext,
) -> Tuple[int, Dict[str, Any]]:
    """Score one direction from the same evidence used for its mirror image."""
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return 0, {"reason": "DIRECTION_INVALID"}

    sign = _sign(direction)
    s5 = structures.get("5m") or {}
    s15 = structures.get("15m") or {}
    s1h = structures.get("1h") or {}
    components: Dict[str, int] = {}

    # Higher timeframes matter, but they must not outweigh a violent live-flow
    # reversal by themselves.  5m is deliberately closer in weight to 15m/1h.
    components["structure_1h"] = _structure_points(s1h.get("direction"), direction, 9, 6)
    components["structure_15m"] = _structure_points(s15.get("direction"), direction, 8, 6)
    components["structure_5m"] = _structure_points(s5.get("direction"), direction, 7, 5)

    move3 = _sf(decision.get("move_3m_percent")) * sign
    move5 = _sf(decision.get("move_5m_percent")) * sign
    components["move_3m"] = _move_points(move3, 0.10, 0.30, 5, 9)
    components["move_5m"] = _move_points(move5, 0.15, 0.45, 5, 9)

    current_direction = str(decision.get("direction") or "").upper()
    if bool(decision.get("breakout_20m")):
        components["breakout"] = 6 if current_direction == direction else -3
    else:
        components["breakout"] = 0

    breadth = _sf(getattr(context, "breadth_5m", 0.50), 0.50)
    if breadth <= 0.35:
        components["breadth"] = 10 if direction == "SHORT" else -10
    elif breadth <= 0.45:
        components["breadth"] = 5 if direction == "SHORT" else -5
    elif breadth >= 0.65:
        components["breadth"] = 10 if direction == "LONG" else -10
    elif breadth >= 0.55:
        components["breadth"] = 5 if direction == "LONG" else -5
    else:
        components["breadth"] = 0

    # Market regime is a modest prior, not a directional command.
    preferred = str(getattr(context, "preferred_direction", "") or "").upper()
    components["market_prior"] = 4 if preferred == direction else (-4 if preferred in {"LONG", "SHORT"} else 0)

    taker_available = bool(decision.get("taker_available"))
    cvd_available = bool(decision.get("cvd_available"))
    book_available = bool(decision.get("book_available"))
    taker_alignment = _sf(decision.get("taker_imbalance")) * sign
    cvd_alignment = _sf(decision.get("cvd_ratio")) * sign
    cvd_impulse_alignment = _sf(decision.get("cvd_impulse")) * sign
    book_alignment = _sf(decision.get("book_imbalance")) * sign
    opposing_wall_ratio = _sf(decision.get("book_opposing_wall_ratio"))

    components["taker"] = (
        _flow_points(taker_alignment, 0.18, 0.35, 10, 18) if taker_available else 0
    )
    components["cvd"] = (
        _flow_points(cvd_alignment, 0.18, 0.35, 9, 16) if cvd_available else 0
    )
    components["cvd_impulse"] = (
        _flow_points(cvd_impulse_alignment, 0.25, 0.55, 3, 6) if cvd_available else 0
    )
    components["book"] = (
        _flow_points(book_alignment, 0.12, 0.25, 2, 4) if book_available else 0
    )

    # The wall ratio is direction-specific to the *current* scanner direction.
    # Therefore a large wall is evidence against that current direction and in
    # favor of its mirror. It is only corroborating evidence; wall alone can
    # never satisfy the reversal confirmation requirement.
    wall_points = 0
    if book_available and current_direction in {"LONG", "SHORT"}:
        magnitude = 10 if opposing_wall_ratio >= 8.0 else (6 if opposing_wall_ratio >= 4.0 else 0)
        if magnitude:
            wall_points = -magnitude if direction == current_direction else magnitude
    components["opposing_wall"] = wall_points

    raw_score = 50 + sum(components.values())
    score = _clip_score(raw_score)
    return score, {
        "direction": direction,
        "score": score,
        "components": components,
        "move_3m_alignment": round(move3, 4),
        "move_5m_alignment": round(move5, 4),
        "taker_alignment": round(taker_alignment, 6),
        "cvd_alignment": round(cvd_alignment, 6),
        "cvd_impulse_alignment": round(cvd_impulse_alignment, 6),
        "book_alignment": round(book_alignment, 6),
        "opposing_wall_ratio": round(opposing_wall_ratio, 4),
        "breadth_5m": round(breadth, 4),
    }


def choose_direction(
    *,
    decision: Mapping[str, Any],
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
    context: strategy.MarketContext,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Return a direction only when one side wins by a meaningful margin."""
    s5 = strategy._structure(df5m, current_price)
    s15 = strategy._structure(df15m, current_price)
    s1h = strategy._structure(df1h, current_price)
    if s5 is None or s15 is None or s1h is None:
        return None, {"version": VERSION, "reason": "STRUCTURE_DATA"}

    structures = {"5m": s5, "15m": s15, "1h": s1h}
    long_score, long_diag = score_from_evidence(
        direction="LONG", decision=decision, structures=structures, context=context
    )
    short_score, short_diag = score_from_evidence(
        direction="SHORT", decision=decision, structures=structures, context=context
    )

    if long_score >= short_score:
        selected, selected_score, other_score = "LONG", long_score, short_score
    else:
        selected, selected_score, other_score = "SHORT", short_score, long_score
    margin = selected_score - other_score

    current_direction = str(decision.get("direction") or "").upper()
    reversal = current_direction in {"LONG", "SHORT"} and selected != current_direction

    wall_supports_reverse = (
        bool(decision.get("book_available"))
        and _sf(decision.get("book_opposing_wall_ratio")) >= 4.0
        and selected != current_direction
    )
    confirmation_flags = {
        "structure_5m": str(s5.get("direction") or "").upper() == selected,
        "fresh_micro": (
            _sf(decision.get("move_3m_percent")) * _sign(selected) >= 0.10
            or _sf(decision.get("move_5m_percent")) * _sign(selected) >= 0.15
        ),
        "taker": bool(decision.get("taker_available"))
        and _sf(decision.get("taker_imbalance")) * _sign(selected) >= 0.18,
        "cvd": bool(decision.get("cvd_available"))
        and _sf(decision.get("cvd_ratio")) * _sign(selected) >= 0.18,
        "breadth": (
            (selected == "SHORT" and _sf(context.breadth_5m, 0.50) <= 0.35)
            or (selected == "LONG" and _sf(context.breadth_5m, 0.50) >= 0.65)
        ),
        "opposing_wall": wall_supports_reverse,
    }
    confirmations = sum(1 for value in confirmation_flags.values() if value)

    reason = "SELECTED"
    allowed = selected_score >= MIN_SELECTED_SCORE and margin >= MIN_SELECTED_MARGIN
    if reversal:
        allowed = (
            allowed
            and selected_score >= MIN_REVERSAL_SCORE
            and margin >= MIN_REVERSAL_MARGIN
            and confirmations >= MIN_REVERSAL_CONFIRMATIONS
        )
        if not allowed:
            reason = "REVERSAL_NOT_CONFIRMED"
    elif not allowed:
        reason = "DIRECTION_MARGIN_WEAK"

    diag = {
        "version": VERSION,
        "reason": reason,
        "selected_direction": selected if allowed else None,
        "selected_score": selected_score,
        "other_score": other_score,
        "margin": margin,
        "current_direction": current_direction or None,
        "reversal": reversal,
        "confirmations": confirmations,
        "confirmation_flags": confirmation_flags,
        "long": long_diag,
        "short": short_diag,
        "structures": {
            "5m": str(s5.get("direction") or ""),
            "15m": str(s15.get("direction") or ""),
            "1h": str(s1h.get("direction") or ""),
        },
    }
    return (selected if allowed else None), diag


def build_direction_plan(
    *,
    symbol: str,
    direction: str,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
    quote_volume_24h: float,
    context: strategy.MarketContext,
    direction_score: int,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Build a normal pullback/retest plan for an independently chosen direction."""
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None, "DIRECTION_INVALID"
    if current_price <= 0:
        return None, "PLAN_NO_PRICE"
    if quote_volume_24h < entry_plan.MIN_QUOTE_VOLUME_24H:
        return None, "PLAN_LOW_VOLUME"

    s5 = strategy._structure(df5m, current_price)
    s15 = strategy._structure(df15m, current_price)
    s1h = strategy._structure(df1h, current_price)
    if s5 is None or s15 is None or s1h is None:
        return None, "PLAN_STRUCTURE_DATA"

    _, market_allowed = strategy._market_component(direction, context)
    if not market_allowed:
        return None, "PLAN_MARKET_OPPOSED"

    atr5 = _sf(s5.get("atr"))
    anchor = entry_plan._choose_anchor(direction, current_price, s5, s15)
    zone = entry_plan._zone(direction, current_price, anchor, atr5)
    if not zone:
        return None, "PLAN_ZONE_DATA"

    zone_low = _sf(zone.get("low"))
    zone_high = _sf(zone.get("high"))
    distance = entry_plan._distance_to_zone_percent(current_price, zone_low, zone_high)
    if direction == "LONG":
        chase_atr = (current_price - zone_high) / atr5 if atr5 > 0 and current_price > zone_high else 0.0
    else:
        chase_atr = (zone_low - current_price) / atr5 if atr5 > 0 and current_price < zone_low else 0.0

    geometry_entry = current_price if zone_low <= current_price <= zone_high else _sf(zone.get("anchor"))
    risk, risk_reason = entry_plan._risk_geometry(direction, geometry_entry, s5, s15)
    if risk is None:
        return None, risk_reason

    base_score = entry_plan._plan_score(
        direction, current_price, zone_low, zone_high, risk, s5, s15, context
    )
    score = max(int(base_score), int(direction_score))

    if chase_atr >= entry_plan.CHASE_DISTANCE_ATR:
        status = "CHASED"
    elif distance > entry_plan.MAX_PREP_DISTANCE_PERCENT:
        return None, "PLAN_TOO_FAR"
    else:
        inside = zone_low <= current_price <= zone_high
        s5_confirmed = str(s5.get("direction") or "").upper() == direction
        volume_ok = _sf(s5.get("volume_ratio")) >= entry_plan.MIN_ENTRY_VOLUME_RATIO
        status = "ENTRY" if inside and s5_confirmed and volume_ok and score >= entry_plan.ENTRY_MIN_SCORE else "PREP"

    result: Dict[str, Any] = {
        "version": entry_plan.VERSION,
        "direction_engine_version": VERSION,
        "direction_engine_score": int(direction_score),
        "symbol": str(symbol),
        "direction": direction,
        "status": status,
        "score": score,
        "current_price": round(current_price, 10),
        "quote_volume_24h": round(_sf(quote_volume_24h), 2),
        "zone_low": zone_low,
        "zone_high": zone_high,
        "ideal_entry": _sf(zone.get("anchor")),
        "zone_distance_percent": round(distance, 4),
        "chase_distance_atr": round(chase_atr, 4),
        "structure_5m": str(s5.get("direction") or ""),
        "structure_15m": str(s15.get("direction") or ""),
        "structure_1h": str(s1h.get("direction") or ""),
        "volume_ratio_5m": round(_sf(s5.get("volume_ratio")), 3),
        "volume_ratio_15m": round(_sf(s15.get("volume_ratio")), 3),
        "extension_atr_5m": round(_sf(s5.get("extension_atr")), 3),
        "market_regime": context.regime,
        "market_label": strategy.market_label(context),
        "market_score": context.score,
        "market_strength": context.strength,
        "market_preferred_direction": context.preferred_direction,
        "market_breadth_5m": context.breadth_5m,
        "major_move_5m_percent": context.major_move_5m_percent,
        "direction_engine_reversal": True,
    }
    result.update(risk)
    return result, "OK"


def format_reversal_preparation(plan: Mapping[str, Any], diag: Mapping[str, Any]) -> str:
    direction = str(plan.get("direction") or "")
    icon = "🟢" if direction == "LONG" else "🔴"
    return (
        f"🔄 YÖN DEĞİŞİMİ HAZIRLIĞI | {plan.get('symbol')}\n"
        f"{icon} {direction}\n"
        f"📊 LONG skor: {int((diag.get('long') or {}).get('score') or 0)} | "
        f"SHORT skor: {int((diag.get('short') or {}).get('score') or 0)}\n"
        f"📍 İdeal giriş bölgesi: {plan.get('zone_low')} - {plan.get('zone_high')}\n"
        f"💵 Mevcut: {plan.get('current_price')}\n"
        f"🛑 Geçersizlik/SL: {plan.get('sl')}\n"
        f"🎯 TP1: {plan.get('tp1')} | TP2: {plan.get('tp2')} | TP3: {plan.get('tp3')}\n"
        f"📈 15M/1H: {plan.get('structure_15m')}/{plan.get('structure_1h')} | "
        f"5M: {plan.get('structure_5m')}\n"
        f"⚖️ Yön farkı: {diag.get('margin')} puan\n"
        f"⏳ Eski yön iptal; yeni yön için 5M bölge teyidi bekleniyor."
    )
