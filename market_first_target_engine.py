"""Technical target planner for Market First Telegram guidance.

The strategy already produces directional decisions and hard risk geometry. This
module adds a separate *technical expectation* layer so a user can see, at the
moment an alert arrives, roughly how much room the setup has and which level is
the main technical objective.

It does not make a setup trade-eligible, does not place orders, and never bypasses
Market First guards. Targets are estimates built from 15M/1H/2H structure plus the
existing stop/TP geometry. They are deliberately labelled as technical estimates,
not guaranteed outcomes.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

import market_first_strategy as strategy
import market_first_swing_2h as swing_2h

VERSION = "MARKET_FIRST_TECHNICAL_TARGET_V1_2026_09_07"

MIN_DERIVED_RISK_PERCENT = 0.40
MAX_DERIVED_RISK_PERCENT = 1.80
MIN_STRUCTURAL_TARGET_R = 0.70
MAX_STRUCTURAL_TARGET_R = 3.25


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _directional_distance(direction: str, entry: float, target: float) -> float:
    if min(entry, target) <= 0:
        return 0.0
    return target - entry if str(direction).upper() == "LONG" else entry - target


def _directional_percent(direction: str, entry: float, target: float) -> float:
    distance = _directional_distance(direction, entry, target)
    return distance / entry * 100.0 if entry > 0 and distance > 0 else 0.0


def _derive_risk(
    direction: str,
    entry: float,
    s15: Optional[Mapping[str, Any]],
) -> Tuple[float, float]:
    """Return (sl, risk) for observational EARLY messages when no live SL exists."""
    if entry <= 0 or not isinstance(s15, Mapping):
        return 0.0, 0.0
    atr = _sf(s15.get("atr"))
    if atr <= 0:
        return 0.0, 0.0

    if direction == "LONG":
        raw_sl = _sf(s15.get("swing_low_12")) - atr * 0.10
        risk = entry - raw_sl
    else:
        raw_sl = _sf(s15.get("swing_high_12")) + atr * 0.10
        risk = raw_sl - entry
    if risk <= 0:
        return 0.0, 0.0

    risk_percent = risk / entry * 100.0
    if risk_percent < MIN_DERIVED_RISK_PERCENT:
        risk = entry * MIN_DERIVED_RISK_PERCENT / 100.0
        raw_sl = entry - risk if direction == "LONG" else entry + risk
    elif risk_percent > MAX_DERIVED_RISK_PERCENT:
        risk = entry * MAX_DERIVED_RISK_PERCENT / 100.0
        raw_sl = entry - risk if direction == "LONG" else entry + risk
    return raw_sl, risk


def _two_hour_context(df1h: Any, current_price: float) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "direction": "NEUTRAL",
        "range_high": 0.0,
        "range_low": 0.0,
    }
    try:
        df2h = swing_2h.aggregate_1h_to_2h(df1h)
    except Exception:
        df2h = None
    if df2h is None or not hasattr(df2h, "copy") or len(df2h) < 20:
        return result

    try:
        s2h = swing_2h._two_hour_structure(df2h, current_price)
    except Exception:
        s2h = None
    if isinstance(s2h, Mapping):
        result["direction"] = str(s2h.get("direction") or "NEUTRAL").upper()

    frame = df2h.copy()
    for column in ("high", "low"):
        if column not in frame.columns:
            return result
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["high", "low"])
    if len(frame) > 1:
        frame = frame.iloc[:-1]
    recent = frame.tail(36)
    if len(recent):
        result["range_high"] = _sf(recent["high"].max())
        result["range_low"] = _sf(recent["low"].min())
    return result


def _mechanical_targets(direction: str, entry: float, risk: float) -> Dict[str, float]:
    if min(entry, risk) <= 0:
        return {"tp1": 0.0, "tp2": 0.0, "tp3": 0.0}
    sign = 1.0 if direction == "LONG" else -1.0
    return {
        "tp1": entry + sign * risk * strategy.TP1_R,
        "tp2": entry + sign * risk * strategy.TP2_R,
        "tp3": entry + sign * risk * strategy.TP3_R,
    }


def _desired_target_r(
    direction: str,
    setup_kind: str,
    d15: str,
    d1h: str,
    d2h: str,
    market_preferred_direction: Optional[str],
) -> float:
    kind = str(setup_kind or "").upper()
    if kind == "SWING_2H":
        desired = 2.0 if d2h == direction and d1h == direction else 1.25
    elif d2h == direction and d1h == direction and d15 == direction:
        desired = 2.0
    elif d1h == direction and d15 == direction:
        desired = 1.25
    else:
        desired = 0.90 if kind == "EARLY_MOVE" else 1.0

    preferred = str(market_preferred_direction or "").upper()
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if preferred == direction:
        desired += 0.20
    elif preferred == opposite:
        desired -= 0.25
    return max(0.75, min(2.25, desired))


def _structural_candidates(
    direction: str,
    entry: float,
    risk: float,
    s15: Optional[Mapping[str, Any]],
    s1h: Optional[Mapping[str, Any]],
    two_hour: Mapping[str, Any],
) -> list[Tuple[str, float, float]]:
    raw: list[Tuple[str, float]] = []
    if direction == "LONG":
        if isinstance(s15, Mapping):
            raw.append(("15M direnç", _sf(s15.get("range_high_72"))))
        if isinstance(s1h, Mapping):
            raw.append(("1H direnç", _sf(s1h.get("range_high_72"))))
        raw.append(("2H direnç", _sf(two_hour.get("range_high"))))
    else:
        if isinstance(s15, Mapping):
            raw.append(("15M destek", _sf(s15.get("range_low_72"))))
        if isinstance(s1h, Mapping):
            raw.append(("1H destek", _sf(s1h.get("range_low_72"))))
        raw.append(("2H destek", _sf(two_hour.get("range_low"))))

    result: list[Tuple[str, float, float]] = []
    for label, level in raw:
        distance = _directional_distance(direction, entry, level)
        if distance <= 0 or risk <= 0:
            continue
        r_value = distance / risk
        if MIN_STRUCTURAL_TARGET_R <= r_value <= MAX_STRUCTURAL_TARGET_R:
            result.append((label, level, r_value))
    return result


def build_target_plan(
    *,
    direction: str,
    entry: float,
    df15m: Any,
    df1h: Any,
    sl: float = 0.0,
    tp1: float = 0.0,
    tp2: float = 0.0,
    tp3: float = 0.0,
    market_preferred_direction: Optional[str] = None,
    setup_kind: str = "TRADE",
) -> Dict[str, Any]:
    direction = str(direction or "").upper()
    entry = _sf(entry)
    if direction not in {"LONG", "SHORT"} or entry <= 0:
        return {}

    try:
        s15 = strategy._structure(df15m, entry)
    except Exception:
        s15 = None
    try:
        s1h = strategy._structure(df1h, entry)
    except Exception:
        s1h = None
    two_hour = _two_hour_context(df1h, entry)

    supplied_sl = _sf(sl)
    risk = abs(entry - supplied_sl) if supplied_sl > 0 else 0.0
    if risk <= 0:
        supplied_sl, risk = _derive_risk(direction, entry, s15)
    if risk <= 0:
        return {}

    mechanical = _mechanical_targets(direction, entry, risk)
    supplied_targets = {
        "tp1": _sf(tp1) or mechanical["tp1"],
        "tp2": _sf(tp2) or mechanical["tp2"],
        "tp3": _sf(tp3) or mechanical["tp3"],
    }

    d15 = str((s15 or {}).get("direction") or "NEUTRAL").upper()
    d1h = str((s1h or {}).get("direction") or "NEUTRAL").upper()
    d2h = str(two_hour.get("direction") or "NEUTRAL").upper()
    desired_r = _desired_target_r(
        direction,
        setup_kind,
        d15,
        d1h,
        d2h,
        market_preferred_direction,
    )

    candidates = _structural_candidates(direction, entry, risk, s15, s1h, two_hour)
    chosen_source = "R bazlı hedef"
    chosen_target = 0.0
    chosen_r = 0.0
    if candidates:
        source, level, r_value = min(candidates, key=lambda item: abs(item[2] - desired_r))
        chosen_source, chosen_target, chosen_r = source, level, r_value
    else:
        mechanical_choices = [
            ("TP1/R", supplied_targets["tp1"], strategy.TP1_R),
            ("TP2/R", supplied_targets["tp2"], strategy.TP2_R),
            ("TP3/R", supplied_targets["tp3"], strategy.TP3_R),
        ]
        chosen_source, chosen_target, chosen_r = min(
            mechanical_choices,
            key=lambda item: abs(item[2] - desired_r),
        )

    expected_percent = _directional_percent(direction, entry, chosen_target)
    tp1_percent = _directional_percent(direction, entry, supplied_targets["tp1"])
    tp3_percent = _directional_percent(direction, entry, supplied_targets["tp3"])
    high_percent = max(expected_percent, tp3_percent)
    low_percent = min(value for value in (tp1_percent, expected_percent) if value > 0) if max(tp1_percent, expected_percent) > 0 else 0.0

    aligned_2h = d2h == direction and d1h == direction and d15 == direction
    aligned_1h = d1h == direction and d15 == direction
    preferred = str(market_preferred_direction or "").upper()
    opposite = "SHORT" if direction == "LONG" else "LONG"
    if aligned_2h and preferred != opposite:
        confidence = "YÜKSEK"
    elif aligned_1h and preferred != opposite:
        confidence = "ORTA"
    else:
        confidence = "TEMKİNLİ"

    return {
        "target_engine_version": VERSION,
        "technical_target": round(chosen_target, 10),
        "technical_target_r": round(chosen_r, 3),
        "technical_target_source": chosen_source,
        "expected_move_percent": round(expected_percent, 3),
        "expected_move_low_percent": round(low_percent, 3),
        "expected_move_high_percent": round(high_percent, 3),
        "target_confidence": confidence,
        "target_structure_15m": d15,
        "target_structure_1h": d1h,
        "target_structure_2h": d2h,
        "target_reference_sl": round(supplied_sl, 10),
        "target_reference_tp1": round(supplied_targets["tp1"], 10),
        "target_reference_tp2": round(supplied_targets["tp2"], 10),
        "target_reference_tp3": round(supplied_targets["tp3"], 10),
        "target_setup_kind": str(setup_kind or "TRADE").upper(),
    }


def target_fields(source: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        key: value
        for key, value in source.items()
        if key.startswith("target_") or key in {
            "technical_target",
            "technical_target_r",
            "technical_target_source",
            "expected_move_percent",
            "expected_move_low_percent",
            "expected_move_high_percent",
        }
    }
