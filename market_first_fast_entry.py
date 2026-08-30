"""Immediate actionable entry for very short Market First moves.

The normal Market First score was intentionally conservative, but live evidence
showed that useful 1-5 minute moves can finish before the normal trade gate opens.
This module therefore gives the SAME Market First strategy a narrow fast lane:
qualified fresh momentum can become actionable on its first observation.

It does not place exchange orders. Fresh-major, liquidity, duplicate, portfolio,
recent-stop and final-entry guards still run in the ordinary send path.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_strategy as strategy

VERSION = "MARKET_FIRST_FAST_ENTRY_V2_2026_08_30"

# Live evidence (BICO and similar short-lived moves) showed that the old 66/70
# admission pair was often waiting for confirmation until the scalp was nearly
# over. Lower only the *awareness* floor used by this same strategy; hard market,
# structure, extension, risk and pre-send guards remain below.
FAST_ALERT_SCORE = 58
if strategy.MIN_ALERT_SCORE > FAST_ALERT_SCORE:
    strategy.MIN_ALERT_SCORE = FAST_ALERT_SCORE

MIN_FAST_SCORE = 58
MIN_MOVE_3_PERCENT = 0.35
MIN_MOVE_5_PERCENT = 0.50
MAX_MOVE_3_PERCENT = 1.55
MAX_MOVE_5_PERCENT = 2.00
MIN_VOLUME_RATIO = 0.50
MAX_EXTENSION_ATR = 1.55
MAX_FAST_RISK_PERCENT = 1.50
MIN_FAST_ROOM_R = 1.25
FAST_TP1_R = 0.55
FAST_TP2_R = 1.00
FAST_TP3_R = 1.50


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _signed_for(direction: str, value: Any) -> float:
    number = _sf(value)
    return number if str(direction).upper() == "LONG" else -number


def _risk_plan(
    direction: str,
    entry: float,
    s5: Mapping[str, Any],
    s15: Mapping[str, Any],
) -> Tuple[Optional[Dict[str, float]], str]:
    atr5 = _sf(s5.get("atr"))
    if entry <= 0 or atr5 <= 0:
        return None, "FAST_RISK_DATA"

    if direction == "LONG":
        raw_sl = _sf(s5.get("swing_low_12")) - atr5 * 0.10
        risk = entry - raw_sl
    else:
        raw_sl = _sf(s5.get("swing_high_12")) + atr5 * 0.10
        risk = raw_sl - entry
    if risk <= 0:
        return None, "FAST_RISK_GEOMETRY"

    risk_percent = risk / entry * 100.0
    if risk_percent < strategy.MIN_RISK_PERCENT:
        risk = entry * strategy.MIN_RISK_PERCENT / 100.0
        raw_sl = entry - risk if direction == "LONG" else entry + risk
        risk_percent = strategy.MIN_RISK_PERCENT
    if risk_percent > min(strategy.MAX_RISK_PERCENT, MAX_FAST_RISK_PERCENT):
        return None, "FAST_RISK_WIDE"

    if direction == "LONG":
        opposing = _sf(s15.get("range_high_72"))
        room = opposing - entry if opposing > entry else 0.0
    else:
        opposing = _sf(s15.get("range_low_72"))
        room = entry - opposing if 0 < opposing < entry else 0.0
    room_r = room / risk if risk > 0 and room > 0 else 99.0
    if 0 < room_r < MIN_FAST_ROOM_R:
        return None, "FAST_NO_ROOM"

    if direction == "LONG":
        tp1 = entry + risk * FAST_TP1_R
        tp2 = entry + risk * FAST_TP2_R
        tp3 = entry + risk * FAST_TP3_R
    else:
        tp1 = entry - risk * FAST_TP1_R
        tp2 = entry - risk * FAST_TP2_R
        tp3 = entry - risk * FAST_TP3_R
    if min(raw_sl, tp1, tp2, tp3) <= 0:
        return None, "FAST_RISK_GEOMETRY"

    return {
        "sl": round(raw_sl, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "risk_percent": round(risk_percent, 3),
        "room_r": round(room_r, 2),
    }, "OK"


def promote_initial_early(
    decision: Optional[Mapping[str, Any]],
    reason: str,
    *,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
    context: strategy.MarketContext,
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    """Turn a fresh qualified EARLY move into an immediate short-lived trade."""
    diagnostics: Dict[str, Any] = {"promoted": False, "version": VERSION}
    if not isinstance(decision, Mapping):
        diagnostics["reason"] = "FAST_NO_DECISION"
        return None, reason, diagnostics

    current = dict(decision)
    if str(current.get("stage") or "").upper() != "EARLY":
        diagnostics["reason"] = "FAST_NOT_EARLY"
        return current, reason, diagnostics
    if bool(current.get("trade_eligible")):
        diagnostics["reason"] = "FAST_ALREADY_READY"
        return current, reason, diagnostics

    direction = str(current.get("direction") or "").upper()
    score = int(_sf(current.get("score")))
    move1 = _signed_for(direction, current.get("move_1m_percent"))
    move3 = _signed_for(direction, current.get("move_3m_percent"))
    move5 = _signed_for(direction, current.get("move_5m_percent"))
    volume = _sf(current.get("volume_ratio_1m"))
    extension = _sf(current.get("extension_atr_5m"))
    breakout = bool(current.get("breakout_20m"))
    diagnostics.update({
        "direction": direction,
        "score": score,
        "move1": round(move1, 4),
        "move3": round(move3, 4),
        "move5": round(move5, 4),
        "volume": round(volume, 3),
        "extension_atr": round(extension, 3),
        "breakout": breakout,
    })

    if direction not in {"LONG", "SHORT"} or score < MIN_FAST_SCORE:
        diagnostics["reason"] = "FAST_SCORE_WEAK"
        return current, reason, diagnostics
    if not (MIN_MOVE_3_PERCENT <= move3 <= MAX_MOVE_3_PERCENT):
        diagnostics["reason"] = "FAST_3M_OUTSIDE"
        return current, reason, diagnostics
    if not (MIN_MOVE_5_PERCENT <= move5 <= MAX_MOVE_5_PERCENT):
        diagnostics["reason"] = "FAST_5M_OUTSIDE"
        return current, reason, diagnostics
    # If there is no clean 20m break, demand stronger continuation so the lower
    # score floor does not turn tiny random noise into a trade.
    if not breakout and move3 < 0.60 and move5 < 0.90:
        diagnostics["reason"] = "FAST_NO_BREAKOUT_WEAK_PROGRESS"
        return current, reason, diagnostics
    if move1 < -0.10:
        diagnostics["reason"] = "FAST_1M_REVERSING"
        return current, reason, diagnostics
    if volume < MIN_VOLUME_RATIO:
        diagnostics["reason"] = "FAST_VOLUME_WEAK"
        return current, reason, diagnostics
    if extension > MAX_EXTENSION_ATR:
        diagnostics["reason"] = "FAST_EXTENSION_HIGH"
        return current, reason, diagnostics

    s5 = strategy._structure(df5m, current_price)
    s15 = strategy._structure(df15m, current_price)
    s1h = strategy._structure(df1h, current_price)
    if s5 is None or s15 is None or s1h is None:
        diagnostics["reason"] = "FAST_STRUCTURE_DATA"
        return current, reason, diagnostics

    opposite = "SHORT" if direction == "LONG" else "LONG"
    if str(s5.get("direction")) != direction:
        diagnostics["reason"] = "FAST_5M_NOT_ALIGNED"
        return current, reason, diagnostics
    if str(s15.get("direction")) not in {direction, "NEUTRAL"}:
        diagnostics["reason"] = "FAST_15M_OPPOSED"
        return current, reason, diagnostics
    if str(s1h.get("direction")) == opposite:
        diagnostics["reason"] = "FAST_1H_OPPOSED"
        return current, reason, diagnostics

    _, market_allowed = strategy._market_component(direction, context)
    if not market_allowed and not bool(current.get("independent_move")):
        diagnostics["reason"] = "FAST_MARKET_OPPOSED"
        return current, reason, diagnostics

    risk, risk_reason = _risk_plan(direction, current_price, s5, s15)
    if risk is None:
        diagnostics["reason"] = risk_reason
        return current, reason, diagnostics

    current.update(risk)
    current.update({
        "stage": "READY",
        "trade_eligible": True,
        "alert_eligible": False,
        "fast_entry": True,
        "fast_entry_version": VERSION,
        "fast_entry_reason": "FIRST_FRESH_MOMENTUM_V2",
        "fast_tp1_r": FAST_TP1_R,
        "fast_tp2_r": FAST_TP2_R,
        "fast_tp3_r": FAST_TP3_R,
    })
    diagnostics["promoted"] = True
    diagnostics["reason"] = "FAST_READY"
    diagnostics["risk_percent"] = current.get("risk_percent")
    diagnostics["room_r"] = current.get("room_r")
    return current, "OK", diagnostics


def decorate_signal(signal: Optional[Dict[str, Any]], decision: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if signal is None or not bool(decision.get("fast_entry")):
        return signal
    signal["entry_type"] = "MARKET_FIRST_FAST"
    signal["quality"] = "HIZLI MARKET FIRST"
    signal["quality_note"] = "İlk tespitte hızlı giriş; ikinci tur teyidi beklenmez."
    signal["fast_entry"] = True
    signal["rr_tp1"] = FAST_TP1_R
    signal["rr_tp2"] = FAST_TP2_R
    signal["rr_tp3"] = FAST_TP3_R
    return signal
