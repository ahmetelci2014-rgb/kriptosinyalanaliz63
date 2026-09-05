"""Symmetric live-direction guard for Market First V5.

This module does not create signals or flip LONG into SHORT.  It only removes a
stale normal-regime directional advantage when 5m breadth strongly disagrees,
requires fresh micro confirmation before an entry-plan promotion, and vetoes a
trade when multiple live order-flow observations strongly oppose its direction.

All thresholds are direction-normalized so LONG and SHORT are treated as mirror
images of each other.
"""
from __future__ import annotations

from dataclasses import replace
import math
from typing import Any, Dict, Mapping, Tuple


BULL_BREADTH_CONFLICT_MAX = 0.35
BEAR_BREADTH_CONFLICT_MIN = 0.65

MIN_FRESH_MOVE_3M_PERCENT = 0.10
MIN_FRESH_MOVE_5M_PERCENT = 0.15
MIN_FRESH_RELATIVE_STRENGTH = 0.20

MAX_OPPOSING_TAKER_WITH_CVD = -0.30
MAX_OPPOSING_CVD_RATIO = -0.25
MAX_OPPOSING_TAKER_WITH_WALL = -0.20
MIN_OPPOSING_WALL_RATIO = 4.0

LIVE_FLOW_SIGNAL_FIELDS = (
    "cvd_available",
    "cvd_ratio",
    "cvd_impulse",
    "cvd_impulse_alignment",
    "book_available",
    "book_imbalance",
    "book_imbalance_alignment",
    "book_opposing_wall_ratio",
    "book_depth_levels",
)


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _direction_sign(direction: str) -> float:
    return 1.0 if str(direction).upper() == "LONG" else -1.0


def neutralize_breadth_conflict(context: Any) -> Tuple[Any, Dict[str, Any]]:
    """Remove only the normal BULL/BEAR preference when 5m breadth contradicts it.

    Strong/shock regimes are intentionally untouched.  We keep the regime label
    and market score for observability, but set preferred_direction to None so
    the underlying Market First market component becomes symmetric for this
    candidate evaluation.
    """
    if context is None:
        return context, {"active": False, "reason": "NO_CONTEXT"}

    regime = str(getattr(context, "regime", "") or "").upper()
    preferred = str(getattr(context, "preferred_direction", "") or "").upper()
    breadth = _sf(getattr(context, "breadth_5m", 0.50), 0.50)

    bull_conflict = (
        regime == "BULL"
        and preferred == "LONG"
        and breadth <= BULL_BREADTH_CONFLICT_MAX
    )
    bear_conflict = (
        regime == "BEAR"
        and preferred == "SHORT"
        and breadth >= BEAR_BREADTH_CONFLICT_MIN
    )
    if not (bull_conflict or bear_conflict):
        return context, {
            "active": False,
            "reason": "BREADTH_ALIGNED_OR_NOT_EXTREME",
            "regime": regime,
            "preferred_direction": preferred or None,
            "breadth_5m": round(breadth, 4),
        }

    try:
        adjusted = replace(context, preferred_direction=None)
    except Exception:
        # MarketContext is a frozen dataclass today.  If that implementation ever
        # changes, fail open rather than breaking the live scanner.
        return context, {
            "active": False,
            "reason": "CONTEXT_REPLACE_UNAVAILABLE",
            "regime": regime,
            "preferred_direction": preferred or None,
            "breadth_5m": round(breadth, 4),
        }

    return adjusted, {
        "active": True,
        "reason": "BULL_BREADTH_CONFLICT" if bull_conflict else "BEAR_BREADTH_CONFLICT",
        "regime": regime,
        "original_preferred_direction": preferred,
        "effective_preferred_direction": None,
        "breadth_5m": round(breadth, 4),
    }


def fresh_entry_plan_confirmation(
    decision: Mapping[str, Any] | None,
    planned_direction: str,
) -> Tuple[bool, Dict[str, Any]]:
    """Require current micro evidence before higher-TF entry-plan promotion."""
    direction = str(planned_direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, {"reason": "PLAN_DIRECTION_INVALID"}
    if not isinstance(decision, Mapping):
        return False, {"reason": "NO_LIVE_MICRO_CONFIRMATION"}

    current_direction = str(decision.get("direction") or "").upper()
    if current_direction and current_direction != direction:
        return False, {
            "reason": "MICRO_DIRECTION_CONFLICT",
            "micro_direction": current_direction,
            "planned_direction": direction,
        }

    sign = _direction_sign(direction)
    aligned_move_3m = _sf(decision.get("move_3m_percent")) * sign
    aligned_move_5m = _sf(decision.get("move_5m_percent")) * sign
    relative_strength = _sf(decision.get("relative_strength_5m"))
    breakout = bool(decision.get("breakout_20m"))

    confirmations = {
        "move_3m": aligned_move_3m >= MIN_FRESH_MOVE_3M_PERCENT,
        "move_5m": aligned_move_5m >= MIN_FRESH_MOVE_5M_PERCENT,
        "breakout": breakout,
        "relative_strength": relative_strength >= MIN_FRESH_RELATIVE_STRENGTH,
    }
    allowed = any(confirmations.values())
    return allowed, {
        "reason": "FRESH_MICRO_CONFIRMED" if allowed else "STALE_MICRO_NO_ENTRY",
        "planned_direction": direction,
        "aligned_move_3m_percent": round(aligned_move_3m, 4),
        "aligned_move_5m_percent": round(aligned_move_5m, 4),
        "relative_strength_5m": round(relative_strength, 4),
        "breakout_20m": breakout,
        "confirmations": confirmations,
    }


def evaluate_live_flow_veto(values: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Hard-veto only when multiple live flow observations agree against trade.

    A single order-book wall is never sufficient.  Missing derivatives data also
    fails open.  Direction normalization makes the exact same thresholds apply
    to LONG and SHORT candidates.
    """
    if not isinstance(values, Mapping):
        return {"blocked": False, "reason": "NO_FLOW_VALUES"}

    direction = str(values.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return {"blocked": False, "reason": "DIRECTION_INVALID"}

    sign = _direction_sign(direction)
    taker_available = bool(values.get("taker_available"))
    cvd_available = bool(values.get("cvd_available"))
    book_available = bool(values.get("book_available"))

    if "taker_imbalance_alignment" in values:
        taker_alignment = _sf(values.get("taker_imbalance_alignment"))
    else:
        taker_alignment = _sf(values.get("taker_imbalance")) * sign
    cvd_ratio_alignment = _sf(values.get("cvd_ratio")) * sign
    wall_ratio = _sf(values.get("book_opposing_wall_ratio"))

    taker_cvd_block = (
        taker_available
        and cvd_available
        and taker_alignment <= MAX_OPPOSING_TAKER_WITH_CVD
        and cvd_ratio_alignment <= MAX_OPPOSING_CVD_RATIO
    )
    taker_wall_block = (
        taker_available
        and book_available
        and taker_alignment <= MAX_OPPOSING_TAKER_WITH_WALL
        and wall_ratio >= MIN_OPPOSING_WALL_RATIO
    )

    if taker_cvd_block:
        reason = "LIVE_FLOW_TAKER_CVD_OPPOSE"
    elif taker_wall_block:
        reason = "LIVE_FLOW_TAKER_WALL_OPPOSE"
    else:
        reason = "LIVE_FLOW_OK"

    return {
        "blocked": bool(taker_cvd_block or taker_wall_block),
        "reason": reason,
        "direction": direction,
        "taker_alignment": round(taker_alignment, 6),
        "cvd_ratio_alignment": round(cvd_ratio_alignment, 6),
        "opposing_wall_ratio": round(wall_ratio, 4),
        "taker_available": taker_available,
        "cvd_available": cvd_available,
        "book_available": book_available,
    }
