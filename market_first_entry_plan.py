"""Symmetric LONG/SHORT pre-trade planning layer for Market First.

Purpose:
- warn before a trade when 15m + 1h structure already agree,
- calculate a practical pullback/retest entry zone,
- promote only a zone-confirmed setup into the existing Market First trade path,
- mark a move as chased instead of encouraging late entries.

This is an entry-planning overlay for the existing Market First strategy. It does
not place exchange orders and it does not bypass the normal major-market,
liquidity, duplicate, recent-stop or portfolio guards.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

import market_first_strategy as strategy

VERSION = "MARKET_FIRST_ENTRY_PLAN_V1_2026_09_05"
STATE_FILE = "market_first_entry_plan_state.json"

MIN_QUOTE_VOLUME_24H = 750_000.0
MAX_PREP_DISTANCE_PERCENT = 1.20
MIN_ROOM_R = 1.35
MAX_PLAN_RISK_PERCENT = 1.65
ENTRY_MIN_SCORE = 72
MIN_ENTRY_VOLUME_RATIO = 0.40
CHASE_DISTANCE_ATR = 0.90
PREP_REPEAT_SECONDS = 2 * 60 * 60
PLAN_EXPIRE_SECONDS = 3 * 60 * 60

ZONE_MIN_HALF_PERCENT = 0.10
ZONE_MAX_HALF_PERCENT = 0.30
ZONE_ATR_HALF_MULTIPLIER = 0.18


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _choose_anchor(direction: str, current_price: float, s5: Mapping[str, Any], s15: Mapping[str, Any]) -> float:
    values = [
        _sf(s5.get("ema20")),
        _sf(s5.get("ema50")),
        _sf(s15.get("ema20")),
        _sf(s15.get("ema50")),
    ]
    values = [value for value in values if value > 0]
    if not values or current_price <= 0:
        return 0.0

    direction = str(direction).upper()
    if direction == "LONG":
        supports = [value for value in values if value <= current_price * 1.002]
        if supports:
            return max(supports)
    else:
        resistances = [value for value in values if value >= current_price * 0.998]
        if resistances:
            return min(resistances)

    return min(values, key=lambda value: abs(value - current_price))


def _zone(direction: str, current_price: float, anchor: float, atr5: float) -> Dict[str, float]:
    if min(current_price, anchor, atr5) <= 0:
        return {}
    atr_half_percent = atr5 * ZONE_ATR_HALF_MULTIPLIER / current_price * 100.0
    half_percent = _clip(
        max(ZONE_MIN_HALF_PERCENT, atr_half_percent),
        ZONE_MIN_HALF_PERCENT,
        ZONE_MAX_HALF_PERCENT,
    )
    half_value = anchor * half_percent / 100.0
    low = anchor - half_value
    high = anchor + half_value
    return {
        "anchor": round(anchor, 10),
        "low": round(low, 10),
        "high": round(high, 10),
        "half_percent": round(half_percent, 4),
    }


def _distance_to_zone_percent(price: float, low: float, high: float) -> float:
    if price <= 0 or min(low, high) <= 0:
        return 999.0
    if low <= price <= high:
        return 0.0
    edge = low if price < low else high
    return abs(price - edge) / price * 100.0


def _risk_geometry(
    direction: str,
    entry: float,
    s5: Mapping[str, Any],
    s15: Mapping[str, Any],
) -> tuple[Optional[Dict[str, float]], str]:
    atr5 = _sf(s5.get("atr"))
    if entry <= 0 or atr5 <= 0:
        return None, "PLAN_RISK_DATA"

    if direction == "LONG":
        raw_sl = _sf(s5.get("swing_low_12")) - atr5 * 0.10
        risk = entry - raw_sl
    else:
        raw_sl = _sf(s5.get("swing_high_12")) + atr5 * 0.10
        risk = raw_sl - entry

    if risk <= 0:
        return None, "PLAN_RISK_GEOMETRY"

    risk_percent = risk / entry * 100.0
    if risk_percent < strategy.MIN_RISK_PERCENT:
        risk = entry * strategy.MIN_RISK_PERCENT / 100.0
        raw_sl = entry - risk if direction == "LONG" else entry + risk
        risk_percent = strategy.MIN_RISK_PERCENT
    if risk_percent > min(strategy.MAX_RISK_PERCENT, MAX_PLAN_RISK_PERCENT):
        return None, "PLAN_RISK_WIDE"

    if direction == "LONG":
        opposing = _sf(s15.get("range_high_72"))
        room = opposing - entry if opposing > entry else 0.0
    else:
        opposing = _sf(s15.get("range_low_72"))
        room = entry - opposing if 0 < opposing < entry else 0.0
    room_r = room / risk if risk > 0 and room > 0 else 99.0
    if 0 < room_r < MIN_ROOM_R:
        return None, "PLAN_NO_ROOM"

    if direction == "LONG":
        tp1 = entry + risk * strategy.TP1_R
        tp2 = entry + risk * strategy.TP2_R
        tp3 = entry + risk * strategy.TP3_R
    else:
        tp1 = entry - risk * strategy.TP1_R
        tp2 = entry - risk * strategy.TP2_R
        tp3 = entry - risk * strategy.TP3_R

    if min(raw_sl, tp1, tp2, tp3) <= 0:
        return None, "PLAN_RISK_GEOMETRY"

    return {
        "sl": round(raw_sl, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "risk_percent": round(risk_percent, 3),
        "room_r": round(room_r, 2),
    }, "OK"


def _plan_score(
    direction: str,
    current_price: float,
    zone_low: float,
    zone_high: float,
    risk: Mapping[str, Any],
    s5: Mapping[str, Any],
    s15: Mapping[str, Any],
    context: strategy.MarketContext,
) -> int:
    score = 58
    s5_direction = str(s5.get("direction") or "")
    if s5_direction == direction:
        score += 10
    elif s5_direction == "NEUTRAL":
        score += 4

    v5 = _sf(s5.get("volume_ratio"))
    v15 = _sf(s15.get("volume_ratio"))
    if v5 >= 0.80:
        score += 5
    elif v5 >= 0.50:
        score += 3
    if v15 >= 0.80:
        score += 5
    elif v15 >= 0.50:
        score += 3

    distance = _distance_to_zone_percent(current_price, zone_low, zone_high)
    if distance == 0:
        score += 6
    elif distance <= 0.30:
        score += 4
    elif distance <= 0.60:
        score += 2

    room_r = _sf(risk.get("room_r"), 99.0)
    if room_r >= 2.0:
        score += 5
    elif room_r >= 1.50:
        score += 3

    risk_percent = _sf(risk.get("risk_percent"), 99.0)
    if risk_percent <= 1.00:
        score += 3
    elif risk_percent <= 1.35:
        score += 1

    preferred = str(context.preferred_direction or "").upper()
    if preferred == direction:
        score += 4
    elif not preferred:
        score += 2

    return int(round(_clip(score, 0, 100)))


def evaluate_entry_plan(
    *,
    symbol: str,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
    quote_volume_24h: float,
    context: strategy.MarketContext,
) -> tuple[Optional[Dict[str, Any]], str]:
    """Return PREP, ENTRY or CHASED for a symmetric LONG/SHORT pullback plan."""
    if current_price <= 0:
        return None, "PLAN_NO_PRICE"
    if quote_volume_24h < MIN_QUOTE_VOLUME_24H:
        return None, "PLAN_LOW_VOLUME"

    s5 = strategy._structure(df5m, current_price)
    s15 = strategy._structure(df15m, current_price)
    s1h = strategy._structure(df1h, current_price)
    if s5 is None or s15 is None or s1h is None:
        return None, "PLAN_STRUCTURE_DATA"

    d15 = str(s15.get("direction") or "").upper()
    d1h = str(s1h.get("direction") or "").upper()
    if d15 not in {"LONG", "SHORT"} or d15 != d1h:
        return None, "PLAN_HIGHER_TF_NOT_ALIGNED"
    direction = d15

    _, market_allowed = strategy._market_component(direction, context)
    if not market_allowed:
        return None, "PLAN_MARKET_OPPOSED"

    atr5 = _sf(s5.get("atr"))
    anchor = _choose_anchor(direction, current_price, s5, s15)
    zone = _zone(direction, current_price, anchor, atr5)
    if not zone:
        return None, "PLAN_ZONE_DATA"

    zone_low = _sf(zone.get("low"))
    zone_high = _sf(zone.get("high"))
    distance = _distance_to_zone_percent(current_price, zone_low, zone_high)

    if direction == "LONG":
        chase_atr = (current_price - zone_high) / atr5 if atr5 > 0 and current_price > zone_high else 0.0
    else:
        chase_atr = (zone_low - current_price) / atr5 if atr5 > 0 and current_price < zone_low else 0.0

    geometry_entry = current_price if zone_low <= current_price <= zone_high else _sf(zone.get("anchor"))
    risk, risk_reason = _risk_geometry(direction, geometry_entry, s5, s15)
    if risk is None:
        return None, risk_reason

    score = _plan_score(
        direction,
        current_price,
        zone_low,
        zone_high,
        risk,
        s5,
        s15,
        context,
    )

    if chase_atr >= CHASE_DISTANCE_ATR:
        status = "CHASED"
    elif distance > MAX_PREP_DISTANCE_PERCENT:
        return None, "PLAN_TOO_FAR"
    else:
        inside = zone_low <= current_price <= zone_high
        s5_confirmed = str(s5.get("direction") or "").upper() == direction
        volume_ok = _sf(s5.get("volume_ratio")) >= MIN_ENTRY_VOLUME_RATIO
        status = "ENTRY" if inside and s5_confirmed and volume_ok and score >= ENTRY_MIN_SCORE else "PREP"

    result: Dict[str, Any] = {
        "version": VERSION,
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
        "structure_15m": d15,
        "structure_1h": d1h,
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
    }
    result.update(risk)
    return result, "OK"


def promote_to_decision(existing: Optional[Mapping[str, Any]], plan: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn an ENTRY plan into the ordinary Market First READY decision shape."""
    promoted = dict(existing or {})
    promoted.update({
        "symbol": str(plan.get("symbol") or ""),
        "direction": str(plan.get("direction") or ""),
        "source": strategy.SOURCE,
        "score": max(int(_sf((existing or {}).get("score"))), int(_sf(plan.get("score")))),
        "stage": "READY",
        "current_price": _sf(plan.get("current_price")),
        "quote_volume_24h": _sf(plan.get("quote_volume_24h")),
        "market_regime": plan.get("market_regime"),
        "market_label": plan.get("market_label"),
        "market_score": _sf(plan.get("market_score")),
        "market_strength": _sf(plan.get("market_strength")),
        "market_preferred_direction": plan.get("market_preferred_direction"),
        "market_breadth_5m": _sf(plan.get("market_breadth_5m"), 0.50),
        "major_move_5m_percent": _sf(plan.get("major_move_5m_percent")),
        "independent_move": bool((existing or {}).get("independent_move")),
        "move_1m_percent": _sf((existing or {}).get("move_1m_percent")),
        "move_3m_percent": _sf((existing or {}).get("move_3m_percent")),
        "move_5m_percent": _sf((existing or {}).get("move_5m_percent")),
        "volume_ratio_1m": _sf(plan.get("volume_ratio_5m")),
        "breakout_20m": bool((existing or {}).get("breakout_20m")),
        "relative_strength_5m": _sf((existing or {}).get("relative_strength_5m")),
        "extension_atr_5m": _sf(plan.get("extension_atr_5m")),
        "structure_5m": plan.get("structure_5m"),
        "structure_15m": plan.get("structure_15m"),
        "structure_1h": plan.get("structure_1h"),
        "alert_eligible": False,
        "trade_eligible": True,
        "risk_reject_reason": None,
        "sl": _sf(plan.get("sl")),
        "tp1": _sf(plan.get("tp1")),
        "tp2": _sf(plan.get("tp2")),
        "tp3": _sf(plan.get("tp3")),
        "risk_percent": _sf(plan.get("risk_percent")),
        "room_r": _sf(plan.get("room_r"), 99.0),
        "entry_plan_trade": True,
        "entry_plan_version": VERSION,
        "entry_plan_zone_low": _sf(plan.get("zone_low")),
        "entry_plan_zone_high": _sf(plan.get("zone_high")),
        "entry_plan_ideal_entry": _sf(plan.get("ideal_entry")),
    })
    return promoted


def decorate_signal(signal: Optional[Dict[str, Any]], decision: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if signal is None or not bool(decision.get("entry_plan_trade")):
        return signal
    signal["entry_type"] = "MARKET_FIRST_ENTRY_PLAN"
    signal["quality"] = "A MARKET FIRST GİRİŞ PLANI"
    signal["quality_note"] = "15M+1H yön uyumu, pullback/retest bölgesi ve 5M giriş teyidi birlikte sağlandı."
    signal["zone_name"] = "LONG destek/retest" if signal.get("direction") == "LONG" else "SHORT direnç/retest"
    signal["ideal_entry"] = _sf(decision.get("entry_plan_ideal_entry"), _sf(signal.get("entry")))
    signal["entry_plan_trade"] = True
    signal["entry_plan_zone_low"] = _sf(decision.get("entry_plan_zone_low"))
    signal["entry_plan_zone_high"] = _sf(decision.get("entry_plan_zone_high"))
    signal["entry_plan_version"] = VERSION
    return signal


def format_preparation(plan: Mapping[str, Any]) -> str:
    direction = str(plan.get("direction") or "")
    icon = "🟢" if direction == "LONG" else "🔴"
    zone_low = _sf(plan.get("zone_low"))
    zone_high = _sf(plan.get("zone_high"))
    return (
        f"🎯 İŞLEM HAZIRLIĞI | {plan.get('symbol')}\n"
        f"{icon} {direction}\n"
        f"🌍 Piyasa: {plan.get('market_label')}\n"
        f"📍 İdeal giriş bölgesi: {zone_low:.10g} - {zone_high:.10g}\n"
        f"💵 Mevcut: {_sf(plan.get('current_price')):.10g}\n"
        f"🛑 Geçersizlik/SL: {_sf(plan.get('sl')):.10g}\n"
        f"🎯 TP1: {_sf(plan.get('tp1')):.10g} | TP2: {_sf(plan.get('tp2')):.10g} | TP3: {_sf(plan.get('tp3')):.10g}\n"
        f"📊 15M/1H: {plan.get('structure_15m')}/{plan.get('structure_1h')} | 5M: {plan.get('structure_5m')}\n"
        f"⭐ Hazırlık skoru: {int(_sf(plan.get('score')))}\n"
        f"⏳ Henüz giriş yok; bölge + 5M teyidi bekleniyor."
    )


def format_chased(plan: Mapping[str, Any]) -> str:
    direction = str(plan.get("direction") or "")
    icon = "🟢" if direction == "LONG" else "🔴"
    return (
        f"❌ GİRİŞİ KOVALAMA | {plan.get('symbol')}\n"
        f"{icon} {direction}\n"
        f"📍 Planlanan bölge: {_sf(plan.get('zone_low')):.10g} - {_sf(plan.get('zone_high')):.10g}\n"
        f"💵 Mevcut: {_sf(plan.get('current_price')):.10g}\n"
        f"⚠️ Fiyat planlanan bölgeden uzaklaştı. Retest/pullback olmadan yeni giriş önerilmez."
    )


def _key(plan: Mapping[str, Any]) -> str:
    return f"{plan.get('symbol')}:{plan.get('direction')}"


def should_emit_preparation(state: Dict[str, Any], plan: Mapping[str, Any], now: int) -> bool:
    plans = state.setdefault("plans", {})
    key = _key(plan)
    previous = plans.get(key) if isinstance(plans.get(key), Mapping) else {}
    last_prep = int(_sf(previous.get("last_prep_at"), 0.0))
    previous_status = str(previous.get("status") or "")
    if previous_status in {"ENTRY_SENT", "CHASED"} and now - int(_sf(previous.get("updated_at"), 0.0)) < PLAN_EXPIRE_SECONDS:
        return False
    if last_prep and now - last_prep < PREP_REPEAT_SECONDS:
        return False
    plans[key] = {
        "status": "PREP",
        "last_prep_at": now,
        "updated_at": now,
        "zone_low": _sf(plan.get("zone_low")),
        "zone_high": _sf(plan.get("zone_high")),
        "score": int(_sf(plan.get("score"))),
    }
    return True


def should_emit_chased(state: Dict[str, Any], plan: Mapping[str, Any], now: int) -> bool:
    plans = state.setdefault("plans", {})
    key = _key(plan)
    previous = plans.get(key) if isinstance(plans.get(key), Mapping) else {}
    if str(previous.get("status") or "") != "PREP":
        return False
    plans[key] = dict(previous)
    plans[key].update({"status": "CHASED", "updated_at": now})
    return True


def mark_entry_sent(state: Dict[str, Any], plan_or_signal: Mapping[str, Any], now: int) -> None:
    symbol = str(plan_or_signal.get("symbol") or "")
    direction = str(plan_or_signal.get("direction") or "")
    key = f"{symbol}:{direction}"
    plans = state.setdefault("plans", {})
    previous = plans.get(key) if isinstance(plans.get(key), Mapping) else {}
    plans[key] = dict(previous)
    plans[key].update({"status": "ENTRY_SENT", "updated_at": now, "entry_sent_at": now})


def prune_state(state: Dict[str, Any], now: int) -> None:
    plans = state.setdefault("plans", {})
    for key, value in list(plans.items()):
        updated_at = int(_sf((value or {}).get("updated_at"), 0.0)) if isinstance(value, Mapping) else 0
        if not updated_at or now - updated_at > PLAN_EXPIRE_SECONDS:
            plans.pop(key, None)
