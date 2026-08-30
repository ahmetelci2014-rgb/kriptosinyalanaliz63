"""Convert proven Market First EARLY alerts into timely trade candidates.

This is not a second strategy. It is a narrow continuation bridge for alerts that
Market First already detected. The bridge exists because a fresh impulse can be
correctly flagged EARLY, then lose its 1m acceleration before the rule score ever
reaches the normal READY threshold. In that case the lifecycle can show +1%..+2%
progress while the trade gate never opens.

Safety principles:
- only an already-active Market First alert may use the bridge;
- only NEW/CONTINUE alerts are eligible (never LATE/DEAD);
- current 5m and 15m structure must still align with the alert direction;
- current market regime must not hard-oppose the direction;
- a strong short-term reversal cancels confirmation;
- stop geometry, 0.40%-1.80% risk width and a minimum room check remain;
- fresh-major, liquidity, portfolio, duplicate and final-entry guards still run
  later in the normal live send path;
- no exchange order is placed.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

import market_first_strategy as strategy

VERSION = "MARKET_FIRST_EARLY_TO_TRADE_V1_2026_08_30"

MIN_FAVORABLE_PERCENT = 0.45
MAX_FAVORABLE_PERCENT = 2.35
MAX_ALERT_AGE_MINUTES = 60.0
MAX_EXTENSION_ATR = 1.65
MIN_FOLLOWTHROUGH_ROOM_R = 1.30
MIN_EARLY_VOLUME_RATIO = 0.55

_ALLOWED_EMPTY_DECISION_REASONS = {"NO_ACCELERATION", "LOW_SCORE"}


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _directional_percent(direction: str, start: float, end: float) -> float:
    if min(start, end) <= 0:
        return 0.0
    raw = (end / start - 1.0) * 100.0
    return raw if str(direction).upper() == "LONG" else -raw


def active_followthrough_alerts(
    state: Mapping[str, Any],
    *,
    now: int,
    available_symbols: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Return the newest still-actionable alert per symbol."""
    available = set(str(x) for x in available_symbols) if available_symbols is not None else None
    result: Dict[str, Dict[str, Any]] = {}
    active = state.get("active_alerts", {}) if isinstance(state, Mapping) else {}
    if not isinstance(active, Mapping):
        return result

    for item in active.values():
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip()
        direction = str(item.get("direction") or "").upper().strip()
        status = str(item.get("status") or "NEW").upper().strip()
        first_at = int(_sf(item.get("first_at"), 0.0))
        if not symbol or direction not in {"LONG", "SHORT"}:
            continue
        if available is not None and symbol not in available:
            continue
        if status not in {"NEW", "CONTINUE"}:
            continue
        age_minutes = (now - first_at) / 60.0 if first_at > 0 else 9999.0
        if age_minutes < 0 or age_minutes > MAX_ALERT_AGE_MINUTES:
            continue
        candidate = dict(item)
        candidate["age_minutes"] = round(age_minutes, 3)
        previous = result.get(symbol)
        if previous is None or first_at > int(_sf(previous.get("first_at"), 0.0)):
            result[symbol] = candidate
    return result


def prioritize_active_alerts(
    selected: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    state: Mapping[str, Any],
    *,
    max_total: int,
    now: int,
) -> Tuple[list[str], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Force actionable EARLY alerts into every deep scan until resolved."""
    available = {str(row.get("symbol") or "") for row in rows if isinstance(row, Mapping)}
    alerts = active_followthrough_alerts(state, now=now, available_symbols=available)
    priority = sorted(
        alerts,
        key=lambda symbol: int(_sf(alerts[symbol].get("first_at"), 0.0)),
        reverse=True,
    )
    merged: list[str] = []
    seen = set()
    for symbol in list(priority) + [str(x) for x in selected]:
        if not symbol or symbol in seen or symbol not in available:
            continue
        seen.add(symbol)
        merged.append(symbol)
        if len(merged) >= max(1, int(max_total)):
            break
    return merged, alerts, {
        "active_followthrough_count": len(alerts),
        "forced_symbols": priority[:12],
        "deep_after": len(merged),
        "version": VERSION,
    }


def _continuation_score_ok(initial_score: int, current_score: int, favorable: float) -> bool:
    score = max(int(initial_score), int(current_score))
    if favorable >= 0.90:
        return score >= 66
    if favorable >= 0.65:
        return score >= 70
    return score >= 72


def _continuation_risk_plan(
    direction: str,
    entry: float,
    s5: Mapping[str, Any],
    s15: Mapping[str, Any],
) -> Tuple[Optional[Dict[str, float]], str]:
    """Same Market First stop geometry with a slightly less rigid room floor."""
    atr5 = _sf(s5.get("atr"))
    if entry <= 0 or atr5 <= 0:
        return None, "FOLLOW_RISK_DATA"

    if direction == "LONG":
        raw_sl = _sf(s5.get("swing_low_12")) - atr5 * 0.10
        risk = entry - raw_sl
    else:
        raw_sl = _sf(s5.get("swing_high_12")) + atr5 * 0.10
        risk = raw_sl - entry
    if risk <= 0:
        return None, "FOLLOW_RISK_GEOMETRY"

    risk_percent = risk / entry * 100.0
    if risk_percent < strategy.MIN_RISK_PERCENT:
        risk = entry * strategy.MIN_RISK_PERCENT / 100.0
        raw_sl = entry - risk if direction == "LONG" else entry + risk
        risk_percent = strategy.MIN_RISK_PERCENT
    if risk_percent > strategy.MAX_RISK_PERCENT:
        return None, "FOLLOW_RISK_WIDE"

    if direction == "LONG":
        opposing = _sf(s15.get("range_high_72"))
        room = opposing - entry if opposing > entry else 0.0
    else:
        opposing = _sf(s15.get("range_low_72"))
        room = entry - opposing if 0 < opposing < entry else 0.0
    room_r = room / risk if risk > 0 and room > 0 else 99.0
    if 0 < room_r < MIN_FOLLOWTHROUGH_ROOM_R:
        return None, "FOLLOW_NO_ROOM"

    if direction == "LONG":
        tp1 = entry + risk * strategy.TP1_R
        tp2 = entry + risk * strategy.TP2_R
        tp3 = entry + risk * strategy.TP3_R
    else:
        tp1 = entry - risk * strategy.TP1_R
        tp2 = entry - risk * strategy.TP2_R
        tp3 = entry - risk * strategy.TP3_R
    if min(raw_sl, tp1, tp2, tp3) <= 0:
        return None, "FOLLOW_RISK_GEOMETRY"

    return {
        "sl": round(raw_sl, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "risk_percent": round(risk_percent, 3),
        "room_r": round(room_r, 2),
    }, "OK"


def promote_active_alert(
    decision: Optional[Mapping[str, Any]],
    reason: str,
    alert: Optional[Mapping[str, Any]],
    *,
    symbol: str,
    df1m: Any,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
    quote_volume_24h: float,
    context: strategy.MarketContext,
    now: int,
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    """Promote an already-proven EARLY alert when continuation is confirmed."""
    diagnostics: Dict[str, Any] = {"promoted": False, "version": VERSION}
    if not isinstance(alert, Mapping):
        return dict(decision) if isinstance(decision, Mapping) else None, reason, diagnostics

    direction = str(alert.get("direction") or "").upper()
    status = str(alert.get("status") or "NEW").upper()
    alert_price = _sf(alert.get("alert_price"))
    first_at = int(_sf(alert.get("first_at"), 0.0))
    age_minutes = (now - first_at) / 60.0 if first_at > 0 else 9999.0
    favorable = _directional_percent(direction, alert_price, current_price)
    diagnostics.update({
        "direction": direction,
        "status": status,
        "age_minutes": round(age_minutes, 3),
        "favorable_percent": round(favorable, 4),
    })

    if direction not in {"LONG", "SHORT"} or status not in {"NEW", "CONTINUE"}:
        diagnostics["reason"] = "FOLLOW_ALERT_NOT_ACTIONABLE"
        return dict(decision) if isinstance(decision, Mapping) else None, reason, diagnostics
    if age_minutes < 0 or age_minutes > MAX_ALERT_AGE_MINUTES:
        diagnostics["reason"] = "FOLLOW_ALERT_TOO_OLD"
        return dict(decision) if isinstance(decision, Mapping) else None, reason, diagnostics
    if favorable < MIN_FAVORABLE_PERCENT:
        diagnostics["reason"] = "FOLLOW_NOT_CONFIRMED_YET"
        return dict(decision) if isinstance(decision, Mapping) else None, reason, diagnostics
    if favorable > MAX_FAVORABLE_PERCENT:
        diagnostics["reason"] = "FOLLOW_TOO_LATE"
        return dict(decision) if isinstance(decision, Mapping) else None, reason, diagnostics

    existing = dict(decision) if isinstance(decision, Mapping) else None
    if existing is None and str(reason or "") not in _ALLOWED_EMPTY_DECISION_REASONS:
        diagnostics["reason"] = f"FOLLOW_BASE_{reason or 'REJECTED'}"
        return None, reason, diagnostics
    if existing is not None:
        if str(existing.get("direction") or "").upper() != direction:
            diagnostics["reason"] = "FOLLOW_DIRECTION_FLIP"
            return existing, reason, diagnostics
        if str(existing.get("stage") or "").upper() == "LATE":
            diagnostics["reason"] = "FOLLOW_BASE_LATE"
            return existing, reason, diagnostics
        if bool(existing.get("trade_eligible")):
            diagnostics["reason"] = "FOLLOW_ALREADY_TRADE_READY"
            return existing, reason, diagnostics

    s5 = strategy._structure(df5m, current_price)
    s15 = strategy._structure(df15m, current_price)
    s1h = strategy._structure(df1h, current_price)
    if s5 is None or s15 is None or s1h is None:
        diagnostics["reason"] = "FOLLOW_STRUCTURE_DATA"
        return existing, reason, diagnostics
    if str(s5.get("direction")) != direction or str(s15.get("direction")) != direction:
        diagnostics["reason"] = "FOLLOW_STRUCTURE_NOT_CONFIRMED"
        return existing, reason, diagnostics
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if str(s1h.get("direction")) == opposite:
        diagnostics["reason"] = "FOLLOW_1H_OPPOSED"
        return existing, reason, diagnostics

    _, market_allowed = strategy._market_component(direction, context)
    if not market_allowed:
        diagnostics["reason"] = "FOLLOW_MARKET_OPPOSED"
        return existing, reason, diagnostics

    extension = _sf(s5.get("extension_atr"))
    if extension > MAX_EXTENSION_ATR:
        diagnostics["reason"] = "FOLLOW_EXTENSION_HIGH"
        return existing, reason, diagnostics

    acceleration = strategy._acceleration(df1m, current_price)
    if isinstance(acceleration, Mapping):
        accel_direction = str(acceleration.get("direction") or "").upper()
        move3 = abs(_sf(acceleration.get("move_3m_percent")))
        if accel_direction and accel_direction != direction and move3 >= 0.30:
            diagnostics["reason"] = "FOLLOW_SHORT_REVERSAL"
            return existing, reason, diagnostics
        live_volume = _sf(acceleration.get("volume_ratio"))
    else:
        live_volume = 0.0
    supporting_volume = max(live_volume, _sf(s5.get("volume_ratio")))
    if favorable < 0.90 and supporting_volume < MIN_EARLY_VOLUME_RATIO:
        diagnostics["reason"] = "FOLLOW_VOLUME_WEAK"
        return existing, reason, diagnostics

    initial_score = int(_sf(alert.get("score"), 0.0))
    current_score = int(_sf((existing or {}).get("score"), 0.0))
    if not _continuation_score_ok(initial_score, current_score, favorable):
        diagnostics["reason"] = "FOLLOW_SCORE_WEAK"
        return existing, reason, diagnostics

    risk, risk_reason = _continuation_risk_plan(direction, current_price, s5, s15)
    if risk is None:
        diagnostics["reason"] = risk_reason
        return existing, reason, diagnostics

    if isinstance(acceleration, Mapping):
        move1_signed = _sf(acceleration.get("move_1m_percent"))
        move3_signed = _sf(acceleration.get("move_3m_percent"))
        move5_signed = _sf(acceleration.get("move_5m_percent"))
        breakout = bool(acceleration.get("breakout"))
        volume_ratio = _sf(acceleration.get("volume_ratio"), supporting_volume)
        relative = strategy._relative_strength(direction, move5_signed, context.major_move_5m_percent)
    else:
        move1_signed = move3_signed = move5_signed = 0.0
        breakout = False
        volume_ratio = supporting_volume
        relative = 0.0

    promoted = dict(existing or {})
    promoted.update({
        "symbol": symbol,
        "direction": direction,
        "source": strategy.SOURCE,
        "score": max(initial_score, current_score),
        "stage": "READY",
        "current_price": round(current_price, 10),
        "quote_volume_24h": round(_sf(quote_volume_24h), 2),
        "market_regime": context.regime,
        "market_label": strategy.market_label(context),
        "market_score": context.score,
        "market_strength": context.strength,
        "market_preferred_direction": context.preferred_direction,
        "market_breadth_5m": context.breadth_5m,
        "major_move_5m_percent": context.major_move_5m_percent,
        "independent_move": bool((existing or {}).get("independent_move")),
        "move_1m_percent": round(move1_signed, 4),
        "move_3m_percent": round(move3_signed, 4),
        "move_5m_percent": round(move5_signed, 4),
        "volume_ratio_1m": round(volume_ratio, 3),
        "breakout_20m": breakout,
        "relative_strength_5m": round(relative, 4),
        "extension_atr_5m": round(extension, 3),
        "structure_5m": str(s5.get("direction")),
        "structure_15m": str(s15.get("direction")),
        "structure_1h": str(s1h.get("direction")),
        "alert_eligible": False,
        "trade_eligible": True,
        "risk_reject_reason": None,
        "followthrough_confirmed": True,
        "followthrough_favorable_percent": round(favorable, 3),
        "followthrough_alert_score": initial_score,
        "followthrough_current_score": current_score,
        "followthrough_alert_status": status,
        "followthrough_version": VERSION,
    })
    promoted.update(risk)
    diagnostics.update({
        "promoted": True,
        "reason": "FOLLOWTHROUGH_CONFIRMED",
        "room_r": risk.get("room_r"),
        "risk_percent": risk.get("risk_percent"),
        "score": promoted.get("score"),
    })
    return promoted, "OK", diagnostics


def decorate_followthrough_signal(signal: Optional[Mapping[str, Any]], decision: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if signal is None:
        return None
    result = dict(signal)
    if not bool(decision.get("followthrough_confirmed")):
        return result
    result.update({
        "quality": "A MARKET FIRST DEVAM TEYİDİ",
        "quality_note": "ERKEN uyarı sonrası hareket devamı + 5m/15m yapı teyidi.",
        "entry_type": "MARKET_FIRST_FOLLOWTHROUGH",
        "zone_name": "Early follow-through confirmation",
        "followthrough_confirmed": True,
        "followthrough_favorable_percent": _sf(decision.get("followthrough_favorable_percent")),
        "followthrough_alert_score": int(_sf(decision.get("followthrough_alert_score"), 0.0)),
        "followthrough_version": VERSION,
    })
    return result
