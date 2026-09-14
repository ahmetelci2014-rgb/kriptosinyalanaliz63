"""Adaptive 1H structure + Fibonacci contact entry engine for Market First.

This is an additive live-entry bridge, not a second trading strategy. It can turn
an existing Market First PREP plan into ENTRY when a confirmed closed-candle 1H
market structure (HL->HH for LONG or LH->LL for SHORT) retraces into the
0.618-0.65 Fibonacci band and a closed 5M candle reacts in the planned direction.

Safety properties:
- uses closed candles for pivot/contact confirmation (no forming-candle repaint),
- never creates a setup without an existing Market First PREP,
- never places exchange orders and never widens stops,
- remains subordinate to Profit Quality, derivatives/order-flow, ML, Final
  Execution, Profit Survival, duplicate/cooldown and portfolio guards,
- reads its own realised trade cohort from trade_ledger.json and auto-disables
  live promotion if the measured FIB cohort becomes negative.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import pandas as pd

import market_first_entry_plan as entry_plan
import market_first_profit_survival_v2 as survival_v2
import market_first_runner as runner
import market_first_strategy as strategy

VERSION = "MARKET_FIRST_STRUCTURE_FIBO_CONTACT_V1_2026_09_14"
STATE_FILE = "market_first_structure_fibo_contact.json"

FIB_MIN = 0.618
FIB_MAX = 0.650
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MAX_SETUP_AGE_1H_BARS = 24
MIN_IMPULSE_ATR = 1.60
MAX_IMPULSE_ATR = 8.00
MAX_CONTACT_AGE_5M_BARS = 2
MAX_POST_CONTACT_DRIFT_PERCENT = 0.35

# Controlled live probation. Once its own realised cohort is large enough the
# engine either graduates to ACTIVE or disables itself automatically.
MIN_LIVE_HEALTH_SAMPLES = 8
MAX_LIVE_STOP_RATE = 0.55
MIN_ACTIVE_AVG_R = 0.10
MAX_ACTIVE_STOP_RATE = 0.45
MAX_PROMOTIONS_PER_RUN = 1

# NORMAL/ACTIVE thresholds.
MIN_BASE_PLAN_SCORE = 86
MIN_ADJUSTED_SCORE = 92
MAX_RISK_PERCENT = 0.70
MIN_ROOM_R = 3.00
MIN_VOLUME_RATIO_5M = 0.90
MIN_VOLUME_RATIO_15M = 0.80
MAX_EXTENSION_ATR = 1.15

# Before enough own realised samples exist, be materially stricter.
PROBATION_MIN_SCORE = 94
PROBATION_MAX_RISK_PERCENT = 0.65
PROBATION_MIN_ROOM_R = 3.25
PROBATION_MIN_VOLUME_RATIO_5M = 1.00
PROBATION_MIN_VOLUME_RATIO_15M = 0.90

# Overall system RECOVERY_STRICT is never bypassed. The FIB engine may only offer
# an A++ candidate into the common downstream gate during recovery.
RECOVERY_MIN_SCORE = 96
RECOVERY_MAX_RISK_PERCENT = 0.60
RECOVERY_MIN_ROOM_R = 3.50
RECOVERY_MIN_VOLUME_RATIO_5M = 1.20
RECOVERY_MIN_VOLUME_RATIO_15M = 1.00
RECOVERY_MAX_EXTENSION_ATR = 0.90

FIB_FIELDS = (
    "structure_fibo_contact",
    "structure_fibo_version",
    "fibo_structure_pattern",
    "fibo_setup_id",
    "fibo_ratio_min",
    "fibo_ratio_max",
    "fibo_zone_low",
    "fibo_zone_high",
    "fibo_impulse_low",
    "fibo_impulse_high",
    "fibo_impulse_atr",
    "fibo_setup_age_1h_bars",
    "fibo_contact_age_5m_bars",
    "fibo_contact_close",
    "fibo_contact_strength",
    "fibo_retracement_ratio",
    "fibo_base_score",
    "fibo_adjusted_score",
    "fibo_live_mode",
)

_INSTALLED = False
_PRESENTATION_INSTALLED = False
_ORIGINAL_EVALUATE = None
_ORIGINAL_PROMOTE = None
_ORIGINAL_DECORATE = None
_ORIGINAL_SNAPSHOT = None
_ORIGINAL_SAVE_OPEN = None
_RUN_COUNTS: Counter = Counter()
_RUN_PROMOTED: list[Dict[str, Any]] = []
_RUN_PROMOTION_COUNT = 0
_HEALTH_CACHE: Dict[str, Any] = {"at": 0, "payload": {}}


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _arg(args, kwargs, name: str, index: int, default=None):
    if name in kwargs:
        return kwargs.get(name)
    return args[index] if len(args) > index else default


def _atomic_save(path: str, payload: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=folder,
            prefix=".structure_fibo.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _closed_frame(df: Any, min_len: int = 20) -> Optional[pd.DataFrame]:
    try:
        return strategy._closed_frame(df, min_len=min_len)
    except Exception:
        return None


def _pivot_points(frame: pd.DataFrame) -> list[Dict[str, Any]]:
    points: list[Dict[str, Any]] = []
    if frame is None or len(frame) < PIVOT_LEFT + PIVOT_RIGHT + 5:
        return points

    highs = [float(value) for value in frame["high"].tolist()]
    lows = [float(value) for value in frame["low"].tolist()]
    for index in range(PIVOT_LEFT, len(frame) - PIVOT_RIGHT):
        high = highs[index]
        low = lows[index]
        left_high = max(highs[index - PIVOT_LEFT:index])
        right_high = max(highs[index + 1:index + 1 + PIVOT_RIGHT])
        left_low = min(lows[index - PIVOT_LEFT:index])
        right_low = min(lows[index + 1:index + 1 + PIVOT_RIGHT])

        if high > left_high and high >= right_high:
            points.append({"type": "H", "index": index, "price": high})
        if low < left_low and low <= right_low:
            points.append({"type": "L", "index": index, "price": low})

    return sorted(points, key=lambda item: int(item["index"]))


def _prior(points: Iterable[Mapping[str, Any]], kind: str, before_index: int) -> Optional[Mapping[str, Any]]:
    eligible = [
        item for item in points
        if str(item.get("type")) == kind and int(item.get("index") or -1) < before_index
    ]
    return eligible[-1] if eligible else None


def detect_structure_impulse(
    df1h: Any,
    direction: str,
    current_price: float = 0.0,
) -> Optional[Dict[str, Any]]:
    """Detect the most recent confirmed 1H HL->HH / LH->LL impulse.

    The final, potentially-forming candle is excluded by strategy._closed_frame.
    Pivots additionally require two closed candles on their right side.
    """
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None

    frame = _closed_frame(df1h, min_len=36)
    if frame is None:
        return None
    points = _pivot_points(frame)
    if len(points) < 4:
        return None

    structure = strategy._structure(df1h, current_price)
    atr = _sf((structure or {}).get("atr")) if isinstance(structure, Mapping) else 0.0
    if atr <= 0:
        return None

    last_index = len(frame) - 1

    if direction == "LONG":
        highs = [item for item in points if item["type"] == "H"]
        for h2 in reversed(highs):
            l2 = _prior(points, "L", int(h2["index"]))
            if not l2:
                continue
            h1 = _prior(points, "H", int(l2["index"]))
            if not h1:
                continue
            l1 = _prior(points, "L", int(h1["index"]))
            if not l1:
                continue
            if float(h2["price"]) <= float(h1["price"]):
                continue
            if float(l2["price"]) <= float(l1["price"]):
                continue
            age = last_index - int(h2["index"])
            if age < 0 or age > MAX_SETUP_AGE_1H_BARS:
                continue
            low = float(l2["price"])
            high = float(h2["price"])
            impulse_atr = (high - low) / atr if atr > 0 else 0.0
            if not (MIN_IMPULSE_ATR <= impulse_atr <= MAX_IMPULSE_ATR):
                continue
            return {
                "direction": "LONG",
                "pattern": "HL->HH",
                "impulse_low": low,
                "impulse_high": high,
                "start_index": int(l2["index"]),
                "end_index": int(h2["index"]),
                "age_1h_bars": age,
                "impulse_atr": round(impulse_atr, 3),
                "prior_low": float(l1["price"]),
                "prior_high": float(h1["price"]),
            }

    lows = [item for item in points if item["type"] == "L"]
    for l2 in reversed(lows):
        h2 = _prior(points, "H", int(l2["index"]))
        if not h2:
            continue
        l1 = _prior(points, "L", int(h2["index"]))
        if not l1:
            continue
        h1 = _prior(points, "H", int(l1["index"]))
        if not h1:
            continue
        if float(l2["price"]) >= float(l1["price"]):
            continue
        if float(h2["price"]) >= float(h1["price"]):
            continue
        age = last_index - int(l2["index"])
        if age < 0 or age > MAX_SETUP_AGE_1H_BARS:
            continue
        low = float(l2["price"])
        high = float(h2["price"])
        impulse_atr = (high - low) / atr if atr > 0 else 0.0
        if not (MIN_IMPULSE_ATR <= impulse_atr <= MAX_IMPULSE_ATR):
            continue
        return {
            "direction": "SHORT",
            "pattern": "LH->LL",
            "impulse_low": low,
            "impulse_high": high,
            "start_index": int(h2["index"]),
            "end_index": int(l2["index"]),
            "age_1h_bars": age,
            "impulse_atr": round(impulse_atr, 3),
            "prior_low": float(l1["price"]),
            "prior_high": float(h1["price"]),
        }
    return None


def fibonacci_zone(impulse: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    direction = str(impulse.get("direction") or "").upper()
    low = _sf(impulse.get("impulse_low"))
    high = _sf(impulse.get("impulse_high"))
    span = high - low
    if direction not in {"LONG", "SHORT"} or low <= 0 or span <= 0:
        return None

    if direction == "LONG":
        a = high - span * FIB_MAX
        b = high - span * FIB_MIN
    else:
        a = low + span * FIB_MIN
        b = low + span * FIB_MAX
    zone_low = min(a, b)
    zone_high = max(a, b)
    return {
        "low": round(zone_low, 12),
        "high": round(zone_high, 12),
        "mid": round((zone_low + zone_high) / 2.0, 12),
    }


def _distance_to_zone_percent(price: float, zone_low: float, zone_high: float) -> float:
    if price <= 0 or zone_low <= 0 or zone_high <= 0:
        return 999.0
    if zone_low <= price <= zone_high:
        return 0.0
    edge = zone_low if price < zone_low else zone_high
    return abs(price - edge) / price * 100.0


def _retracement_ratio(direction: str, current_price: float, impulse: Mapping[str, Any]) -> float:
    low = _sf(impulse.get("impulse_low"))
    high = _sf(impulse.get("impulse_high"))
    span = high - low
    if current_price <= 0 or span <= 0:
        return 0.0
    if direction == "LONG":
        return (high - current_price) / span
    return (current_price - low) / span


def contact_evidence(
    df5m: Any,
    direction: str,
    current_price: float,
    zone: Mapping[str, Any],
) -> Optional[Dict[str, Any]]:
    """Require a closed 5M touch plus directional close/reaction."""
    frame = _closed_frame(df5m, min_len=20)
    if frame is None:
        return None

    direction = str(direction or "").upper()
    zone_low = _sf(zone.get("low"))
    zone_high = _sf(zone.get("high"))
    zone_mid = _sf(zone.get("mid"))
    if direction not in {"LONG", "SHORT"} or min(zone_low, zone_high, zone_mid) <= 0:
        return None

    drift = _distance_to_zone_percent(current_price, zone_low, zone_high)
    if drift > MAX_POST_CONTACT_DRIFT_PERCENT:
        return None

    recent = frame.tail(MAX_CONTACT_AGE_5M_BARS + 1).reset_index(drop=True)
    rows = list(recent.iterrows())
    for reverse_age, (_, candle) in enumerate(reversed(rows)):
        open_price = _sf(candle.get("open"))
        high = _sf(candle.get("high"))
        low = _sf(candle.get("low"))
        close = _sf(candle.get("close"))
        if min(open_price, high, low, close) <= 0:
            continue
        overlap = low <= zone_high and high >= zone_low
        if not overlap:
            continue

        if direction == "LONG":
            reacted = close > open_price and close >= zone_mid
            invalid_live = current_price < zone_low * 0.998
        else:
            reacted = close < open_price and close <= zone_mid
            invalid_live = current_price > zone_high * 1.002
        if reacted and not invalid_live:
            body_percent = abs(close - open_price) / open_price * 100.0
            return {
                "contact_age_5m_bars": reverse_age,
                "contact_close": close,
                "contact_body_percent": round(body_percent, 4),
                "contact_strength": "CLOSED_DIRECTIONAL_REACTION",
                "drift_percent": round(drift, 4),
            }
    return None


def fib_live_health(ledger: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    if ledger is None:
        try:
            ledger = runner.bot.load_trade_ledger()
        except Exception:
            ledger = {}
    trades = ledger.get("trades") if isinstance(ledger, Mapping) else {}
    trades = trades if isinstance(trades, Mapping) else {}

    rows = [
        item for item in trades.values()
        if isinstance(item, Mapping) and bool(item.get("structure_fibo_contact"))
    ]
    settled = []
    losses = 0
    positive = 0
    r_values = []
    for trade in rows:
        result = str(trade.get("final_result") or "").upper()
        if result in {"", "OPEN", "NONE"}:
            continue
        r_value = trade.get("r_result")
        r_number = None
        try:
            if r_value is not None and math.isfinite(float(r_value)):
                r_number = float(r_value)
        except Exception:
            r_number = None

        # Neutral BE/profit-lock cases remain in the audit but do not inflate the
        # directional win/loss denominator unless a measurable R exists.
        if result == "SL":
            losses += 1
            settled.append(trade)
        elif result in {"TP3", "TP2_SONRASI_BE", "TP1_SONRASI_BE"}:
            positive += 1
            settled.append(trade)
        elif r_number is not None and abs(r_number) > 1e-9:
            settled.append(trade)
            if r_number > 0:
                positive += 1
            elif r_number < 0:
                losses += 1

        if r_number is not None:
            r_values.append(r_number)

    samples = positive + losses
    stop_rate = losses / samples if samples else 0.0
    avg_r = sum(r_values) / len(r_values) if r_values else 0.0

    if samples < MIN_LIVE_HEALTH_SAMPLES:
        mode = "PROBATION"
        reason = "OWN_SAMPLE_WARMUP"
    elif avg_r <= 0.0 or stop_rate >= MAX_LIVE_STOP_RATE:
        mode = "DISABLED"
        reason = "NEGATIVE_REALIZED_FIB_COHORT"
    elif avg_r >= MIN_ACTIVE_AVG_R and stop_rate <= MAX_ACTIVE_STOP_RATE:
        mode = "ACTIVE"
        reason = "POSITIVE_REALIZED_FIB_COHORT"
    else:
        mode = "PROBATION"
        reason = "OWN_EDGE_NOT_CONFIRMED"

    return {
        "mode": mode,
        "reason": reason,
        "samples": samples,
        "positive": positive,
        "losses": losses,
        "stop_rate": round(stop_rate, 4),
        "average_r": round(avg_r, 4),
        "r_samples": len(r_values),
        "tracked_total": len(rows),
    }


def _cached_live_health() -> Dict[str, Any]:
    now = runner.bot.now_ts()
    cached_at = _si(_HEALTH_CACHE.get("at"))
    payload = _HEALTH_CACHE.get("payload")
    if isinstance(payload, Mapping) and payload and now - cached_at < 30:
        return dict(payload)
    health = fib_live_health()
    _HEALTH_CACHE["at"] = now
    _HEALTH_CACHE["payload"] = health
    return health


def _adjusted_score(plan: Mapping[str, Any], impulse: Mapping[str, Any], contact: Mapping[str, Any]) -> int:
    score = _si(plan.get("score"))
    bonus = 2  # confirmed HL->HH / LH->LL structure
    if _sf(impulse.get("impulse_atr")) >= 2.50:
        bonus += 2
    if _sf(plan.get("volume_ratio_5m")) >= 1.20:
        bonus += 2
    if _sf(plan.get("volume_ratio_15m")) >= 1.00:
        bonus += 1
    if _si(contact.get("contact_age_5m_bars")) == 0:
        bonus += 1
    return min(100, score + bonus)


def fibo_contact_qualifies(
    plan: Mapping[str, Any],
    impulse: Mapping[str, Any],
    contact: Mapping[str, Any],
    risk: Mapping[str, Any],
    *,
    live_health: Mapping[str, Any],
    survival_health: Mapping[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    direction = str(plan.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, {"reason": "DIRECTION"}
    if str(plan.get("status") or "").upper() != "PREP":
        return False, {"reason": "NOT_PREP"}
    if str(impulse.get("direction") or "").upper() != direction:
        return False, {"reason": "IMPULSE_DIRECTION"}

    live_mode = str(live_health.get("mode") or "PROBATION").upper()
    if live_mode == "DISABLED":
        return False, {"reason": "OWN_FIB_EDGE_DISABLED", "live_health": dict(live_health)}

    survival_mode = str(survival_health.get("mode") or "NORMAL").upper()
    if survival_mode == "HALT":
        return False, {"reason": "SURVIVAL_HALT", "survival_reason": survival_health.get("reason")}

    # This engine is deliberately market-first. No countertrend FIB promotions.
    if str(plan.get("market_preferred_direction") or "").upper() != direction:
        return False, {"reason": "MARKET_NOT_ALIGNED"}
    if str(plan.get("structure_5m") or "").upper() != direction:
        return False, {"reason": "5M_NOT_ALIGNED"}
    if str(plan.get("structure_15m") or "").upper() != direction:
        return False, {"reason": "15M_NOT_ALIGNED"}
    if str(plan.get("structure_1h") or "").upper() != direction:
        return False, {"reason": "1H_NOT_ALIGNED"}

    base_score = _si(plan.get("score"))
    adjusted_score = _adjusted_score(plan, impulse, contact)
    risk_percent = _sf(risk.get("risk_percent"), 999.0)
    room_r = _sf(risk.get("room_r"))
    volume5 = _sf(plan.get("volume_ratio_5m"))
    volume15 = _sf(plan.get("volume_ratio_15m"))
    extension = _sf(plan.get("extension_atr_5m"), 999.0)

    if base_score < MIN_BASE_PLAN_SCORE:
        return False, {"reason": "BASE_SCORE", "base_score": base_score}
    if not (MIN_IMPULSE_ATR <= _sf(impulse.get("impulse_atr")) <= MAX_IMPULSE_ATR):
        return False, {"reason": "IMPULSE_ATR"}

    min_score = MIN_ADJUSTED_SCORE
    max_risk = MAX_RISK_PERCENT
    min_room = MIN_ROOM_R
    min_v5 = MIN_VOLUME_RATIO_5M
    min_v15 = MIN_VOLUME_RATIO_15M
    max_extension = MAX_EXTENSION_ATR

    if live_mode == "PROBATION":
        min_score = max(min_score, PROBATION_MIN_SCORE)
        max_risk = min(max_risk, PROBATION_MAX_RISK_PERCENT)
        min_room = max(min_room, PROBATION_MIN_ROOM_R)
        min_v5 = max(min_v5, PROBATION_MIN_VOLUME_RATIO_5M)
        min_v15 = max(min_v15, PROBATION_MIN_VOLUME_RATIO_15M)

    if survival_mode == "RECOVERY_STRICT":
        min_score = max(min_score, RECOVERY_MIN_SCORE)
        max_risk = min(max_risk, RECOVERY_MAX_RISK_PERCENT)
        min_room = max(min_room, RECOVERY_MIN_ROOM_R)
        min_v5 = max(min_v5, RECOVERY_MIN_VOLUME_RATIO_5M)
        min_v15 = max(min_v15, RECOVERY_MIN_VOLUME_RATIO_15M)
        max_extension = min(max_extension, RECOVERY_MAX_EXTENSION_ATR)

    evidence = {
        "reason": "STRUCTURE_FIBO_CONTACT",
        "base_score": base_score,
        "adjusted_score": adjusted_score,
        "risk_percent": round(risk_percent, 4),
        "room_r": round(room_r, 3),
        "volume_ratio_5m": round(volume5, 3),
        "volume_ratio_15m": round(volume15, 3),
        "extension_atr_5m": round(extension, 3),
        "impulse_atr": round(_sf(impulse.get("impulse_atr")), 3),
        "live_mode": live_mode,
        "survival_mode": survival_mode,
        "thresholds": {
            "min_score": min_score,
            "max_risk_percent": max_risk,
            "min_room_r": min_room,
            "min_volume_ratio_5m": min_v5,
            "min_volume_ratio_15m": min_v15,
            "max_extension_atr": max_extension,
        },
    }

    if adjusted_score < min_score:
        return False, {**evidence, "reason": "ADJUSTED_SCORE"}
    if risk_percent <= 0 or risk_percent > max_risk:
        return False, {**evidence, "reason": "RISK"}
    if room_r < min_room:
        return False, {**evidence, "reason": "ROOM"}
    if volume5 < min_v5:
        return False, {**evidence, "reason": "VOLUME_5M"}
    if volume15 < min_v15:
        return False, {**evidence, "reason": "VOLUME_15M"}
    if extension > max_extension:
        return False, {**evidence, "reason": "EXTENSION"}
    return True, evidence


def _copy_fib_fields(target: Dict[str, Any], source: Mapping[str, Any]) -> Dict[str, Any]:
    for key in FIB_FIELDS:
        if key in source:
            target[key] = source.get(key)
    return target


def install() -> None:
    global _INSTALLED, _ORIGINAL_EVALUATE, _ORIGINAL_PROMOTE, _ORIGINAL_DECORATE
    global _ORIGINAL_SNAPSHOT, _ORIGINAL_SAVE_OPEN
    if _INSTALLED:
        return
    _INSTALLED = True

    _ORIGINAL_EVALUATE = entry_plan.evaluate_entry_plan
    _ORIGINAL_PROMOTE = entry_plan.promote_to_decision
    _ORIGINAL_DECORATE = entry_plan.decorate_signal
    _ORIGINAL_SNAPSHOT = runner.bot.signal_diagnostic_snapshot
    _ORIGINAL_SAVE_OPEN = runner.bot.save_open_signal

    def evaluate_with_structure_fibo(*args, **kwargs):
        global _RUN_PROMOTION_COUNT
        plan, reason = _ORIGINAL_EVALUATE(*args, **kwargs)
        if not isinstance(plan, Mapping) or str(plan.get("status") or "").upper() != "PREP":
            return plan, reason
        if _RUN_PROMOTION_COUNT >= MAX_PROMOTIONS_PER_RUN:
            _RUN_COUNTS["RUN_PROMOTION_LIMIT"] += 1
            return plan, reason

        direction = str(plan.get("direction") or "").upper()
        current_price = _sf(_arg(args, kwargs, "current_price", 4, plan.get("current_price")))
        df5m = _arg(args, kwargs, "df5m", 1)
        df15m = _arg(args, kwargs, "df15m", 2)
        df1h = _arg(args, kwargs, "df1h", 3)

        impulse = detect_structure_impulse(df1h, direction, current_price)
        if not impulse:
            _RUN_COUNTS["NO_CONFIRMED_STRUCTURE"] += 1
            return plan, reason
        zone = fibonacci_zone(impulse)
        if not zone:
            _RUN_COUNTS["NO_FIB_ZONE"] += 1
            return plan, reason
        contact = contact_evidence(df5m, direction, current_price, zone)
        if not contact:
            _RUN_COUNTS["NO_CLOSED_FIB_CONTACT"] += 1
            return plan, reason

        s5 = strategy._structure(df5m, current_price)
        s15 = strategy._structure(df15m, current_price)
        if not isinstance(s5, Mapping) or not isinstance(s15, Mapping):
            _RUN_COUNTS["NO_RISK_STRUCTURE"] += 1
            return plan, reason
        risk, risk_reason = entry_plan._risk_geometry(direction, current_price, s5, s15)
        if not isinstance(risk, Mapping):
            _RUN_COUNTS[f"RISK_{risk_reason}"] += 1
            return plan, reason

        live_health = _cached_live_health()
        survival_health = survival_v2._current_health_v2()
        ok, evidence = fibo_contact_qualifies(
            plan,
            impulse,
            contact,
            risk,
            live_health=live_health,
            survival_health=survival_health,
        )
        if not ok:
            _RUN_COUNTS[str(evidence.get("reason") or "REJECTED")] += 1
            return plan, reason

        retracement = _retracement_ratio(direction, current_price, impulse)
        setup_id = (
            f"{plan.get('symbol')}:{direction}:"
            f"{int(impulse.get('start_index') or 0)}:{int(impulse.get('end_index') or 0)}:"
            f"{round(_sf(impulse.get('impulse_low')), 8)}:{round(_sf(impulse.get('impulse_high')), 8)}"
        )
        promoted = dict(plan)
        promoted.update(risk)
        promoted.update({
            "status": "ENTRY",
            "score": int(evidence["adjusted_score"]),
            "current_price": round(current_price, 12),
            "zone_low": _sf(zone.get("low")),
            "zone_high": _sf(zone.get("high")),
            "ideal_entry": _sf(zone.get("mid")),
            "zone_distance_percent": round(
                _distance_to_zone_percent(current_price, _sf(zone.get("low")), _sf(zone.get("high"))), 4
            ),
            "structure_fibo_contact": True,
            "structure_fibo_version": VERSION,
            "fibo_structure_pattern": impulse.get("pattern"),
            "fibo_setup_id": setup_id,
            "fibo_ratio_min": FIB_MIN,
            "fibo_ratio_max": FIB_MAX,
            "fibo_zone_low": _sf(zone.get("low")),
            "fibo_zone_high": _sf(zone.get("high")),
            "fibo_impulse_low": _sf(impulse.get("impulse_low")),
            "fibo_impulse_high": _sf(impulse.get("impulse_high")),
            "fibo_impulse_atr": _sf(impulse.get("impulse_atr")),
            "fibo_setup_age_1h_bars": _si(impulse.get("age_1h_bars")),
            "fibo_contact_age_5m_bars": _si(contact.get("contact_age_5m_bars")),
            "fibo_contact_close": _sf(contact.get("contact_close")),
            "fibo_contact_strength": contact.get("contact_strength"),
            "fibo_retracement_ratio": round(retracement, 4),
            "fibo_base_score": _si(plan.get("score")),
            "fibo_adjusted_score": int(evidence["adjusted_score"]),
            "fibo_live_mode": live_health.get("mode"),
        })
        _RUN_PROMOTION_COUNT += 1
        _RUN_COUNTS["PROMOTED"] += 1
        _RUN_PROMOTED.append({
            "symbol": promoted.get("symbol"),
            "direction": direction,
            "pattern": impulse.get("pattern"),
            "score": promoted.get("score"),
            "risk_percent": promoted.get("risk_percent"),
            "room_r": promoted.get("room_r"),
            "fibo_zone_low": promoted.get("fibo_zone_low"),
            "fibo_zone_high": promoted.get("fibo_zone_high"),
            "live_mode": promoted.get("fibo_live_mode"),
            "survival_mode": survival_health.get("mode"),
        })
        print(
            "STRUCTURE FIBO CONTACT -> ENTRY:",
            promoted.get("symbol"), direction,
            "| pattern=", impulse.get("pattern"),
            "| fib=", promoted.get("fibo_zone_low"), promoted.get("fibo_zone_high"),
            "| score=", promoted.get("score"),
            "| risk=", promoted.get("risk_percent"),
            "| roomR=", promoted.get("room_r"),
        )
        return promoted, "STRUCTURE_FIBO_CONTACT_ENTRY"

    def promote_with_fib(existing, plan):
        decision = _ORIGINAL_PROMOTE(existing, plan)
        if isinstance(decision, dict) and isinstance(plan, Mapping) and bool(plan.get("structure_fibo_contact")):
            _copy_fib_fields(decision, plan)
        return decision

    def decorate_with_fib(signal, decision):
        decorated = _ORIGINAL_DECORATE(signal, decision)
        if isinstance(decorated, dict) and isinstance(decision, Mapping) and bool(decision.get("structure_fibo_contact")):
            _copy_fib_fields(decorated, decision)
        return decorated

    def snapshot_with_fib(signal):
        snapshot = dict(_ORIGINAL_SNAPSHOT(signal))
        if isinstance(signal, Mapping) and bool(signal.get("structure_fibo_contact")):
            _copy_fib_fields(snapshot, signal)
        return snapshot

    def save_open_with_fib(signal):
        _ORIGINAL_SAVE_OPEN(signal)
        if not isinstance(signal, Mapping) or not bool(signal.get("structure_fibo_contact")):
            return
        try:
            open_signals = runner.bot.load_open_signals()
            key = (
                f"{signal.get('symbol')}_"
                f"{signal.get('direction')}_"
                f"{signal.get('source', 'MTF')}"
            )
            saved = open_signals.get(key)
            if not isinstance(saved, dict):
                return
            _copy_fib_fields(saved, signal)
            open_signals[key] = saved
            runner.bot.save_open_signals(open_signals)
            runner.bot.ledger_update_open_snapshot(saved)
        except Exception as exc:
            print("Structure Fibo metadata kayıt hatası:", type(exc).__name__, exc)

    entry_plan.evaluate_entry_plan = evaluate_with_structure_fibo
    entry_plan.promote_to_decision = promote_with_fib
    entry_plan.decorate_signal = decorate_with_fib
    runner.bot.signal_diagnostic_snapshot = snapshot_with_fib
    runner.bot.save_open_signal = save_open_with_fib


def install_presentation() -> None:
    global _PRESENTATION_INSTALLED
    if _PRESENTATION_INSTALLED:
        return
    _PRESENTATION_INSTALLED = True
    original = runner._format_trade_message

    def format_with_fib(signal):
        text = original(signal)
        if isinstance(signal, Mapping) and bool(signal.get("structure_fibo_contact")):
            text += (
                "\n🧬 Giriş modeli: 1H yapı + FIB 0.618–0.65 temas"
                f"\n📐 Yapı: {signal.get('fibo_structure_pattern')}"
            )
        return text

    runner._format_trade_message = format_with_fib


def finish() -> Dict[str, Any]:
    health = _cached_live_health()
    payload = {
        "version": VERSION,
        "mode": "ADAPTIVE_REAL_ENTRY_NO_ORDERS_NO_STOP_WIDENING",
        "live_health": health,
        "thresholds": {
            "fib_band": [FIB_MIN, FIB_MAX],
            "min_base_plan_score": MIN_BASE_PLAN_SCORE,
            "normal_min_adjusted_score": MIN_ADJUSTED_SCORE,
            "normal_max_risk_percent": MAX_RISK_PERCENT,
            "normal_min_room_r": MIN_ROOM_R,
            "probation_min_score": PROBATION_MIN_SCORE,
            "recovery_min_score": RECOVERY_MIN_SCORE,
            "max_promotions_per_run": MAX_PROMOTIONS_PER_RUN,
            "auto_disable_min_samples": MIN_LIVE_HEALTH_SAMPLES,
            "auto_disable_stop_rate": MAX_LIVE_STOP_RATE,
        },
        "run_counts": dict(_RUN_COUNTS),
        "promoted": _RUN_PROMOTED[-10:],
        "note": (
            "Closed-candle 1H structure and 5M contact only. Downstream Profit Quality, "
            "flow, execution and survival gates remain mandatory."
        ),
    }
    _atomic_save(STATE_FILE, payload)
    return payload


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "fib_band": [FIB_MIN, FIB_MAX],
        "confirmed_structure": "1H HL->HH / LH->LL",
        "contact_confirmation": "CLOSED_5M_DIRECTIONAL_REACTION",
        "max_promotions_per_run": MAX_PROMOTIONS_PER_RUN,
        "own_live_health": _cached_live_health(),
        "auto_disable_negative_cohort": True,
        "exchange_orders": False,
        "stop_widening": False,
        "downstream_guards_bypassed": False,
    }
