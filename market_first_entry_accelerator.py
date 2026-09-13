"""Controlled entry acceleration for Market First V5.

The live system was finding many valid preparations but promoting very few of
those preparations into real trade candidates. This module keeps every final
safety guard intact while moving confirmation closer to the actual entry zone:

- a high-quality ENTRY plan may use its own 15m+1h+5m/volume confirmation when
  the raw momentum strategy has not produced a separate micro decision yet;
- small, same-direction micro progress is enough once the pullback plan itself is
  already confirmed;
- breakout fast-entry can trigger earlier, but only with a higher score, stronger
  volume, tighter extension/risk and adequate room;
- an exceptionally strong PREP can become ENTRY slightly before the narrow zone
  only when 5m/15m/1h, market direction, volume, risk and room are all A++.

The strict-early path only removes the redundant narrow-zone wait. It still goes
through Profit Quality, derivatives/order-flow, ML, Final Execution, Profit
Survival, duplicate, recent-stop and portfolio guards. It never sends Telegram
messages or exchange orders by itself.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

import market_first_entry_plan as entry_plan
import market_first_fast_entry as fast_entry
import market_first_live_direction_guard as direction_guard

VERSION = "MARKET_FIRST_ENTRY_ACCELERATOR_V2_2026_09_13"
_INSTALLED = False
_ORIGINAL_FRESH = None
_ORIGINAL_EVALUATE = None

# Pullback/zone path: slightly higher quality than the old generic ENTRY floor,
# but no longer dependent on a second raw-momentum decision being present.
ENTRY_MIN_SCORE = 76
ENTRY_MIN_VOLUME_RATIO_5M = 0.50
ENTRY_MAX_RISK_PERCENT = 1.45
ENTRY_MIN_ROOM_R = 1.45

# Fresh-micro path: a confirmed 15m+1h+5m pullback should be entered near the
# zone, not after another large impulse has already happened.
FRESH_MOVE_3M_PERCENT = 0.03
FRESH_MOVE_5M_PERCENT = 0.05
FRESH_RELATIVE_STRENGTH = 0.10

# Breakout path: detect the move earlier than the old 0.35%/0.50% requirement,
# while materially raising quality requirements so low-score noise is not traded.
FAST_MIN_SCORE = 70
FAST_MIN_MOVE_3M_PERCENT = 0.15
FAST_MIN_MOVE_5M_PERCENT = 0.20
FAST_MIN_VOLUME_RATIO = 0.70
FAST_MAX_EXTENSION_ATR = 1.25
FAST_MAX_RISK_PERCENT = 1.45
FAST_MIN_ROOM_R = 1.45

# A++ early-futures conversion. This is deliberately much stricter than the
# ordinary plan. It exists for the historical SUI/UNI-type miss where every
# directional structure and volume condition was already strong but price never
# revisited the very narrow pullback zone before TP targets were reached.
STRICT_EARLY_MIN_SCORE = 90
STRICT_EARLY_MAX_ZONE_DISTANCE_PERCENT = 0.70
STRICT_EARLY_MIN_VOLUME_RATIO_5M = 1.50
STRICT_EARLY_MIN_VOLUME_RATIO_15M = 1.20
STRICT_EARLY_MAX_RISK_PERCENT = 0.70
STRICT_EARLY_MIN_ROOM_R = 3.00
STRICT_EARLY_MAX_EXTENSION_ATR = 1.35


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def strict_early_entry_qualifies(plan: Mapping[str, Any] | None) -> Tuple[bool, Dict[str, Any]]:
    """Approve only an A++ PREP for early conversion to ordinary ENTRY.

    This function does not create a signal. A converted plan must still survive
    every downstream live gate, including the adaptive Profit Survival Gate.
    """
    if not isinstance(plan, Mapping):
        return False, {"reason": "NO_PLAN"}
    if str(plan.get("status") or "").upper() != "PREP":
        return False, {"reason": "NOT_PREP"}

    direction = str(plan.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, {"reason": "DIRECTION"}

    structures = (
        str(plan.get("structure_5m") or "").upper(),
        str(plan.get("structure_15m") or "").upper(),
        str(plan.get("structure_1h") or "").upper(),
    )
    if any(item != direction for item in structures):
        return False, {"reason": "MTF_NOT_FULLY_ALIGNED", "structures": structures}

    preferred = str(plan.get("market_preferred_direction") or "").upper()
    if preferred != direction:
        return False, {"reason": "MARKET_DIRECTION_NOT_ALIGNED", "preferred": preferred}

    evidence = {
        "score": _si(plan.get("score")),
        "zone_distance_percent": round(_sf(plan.get("zone_distance_percent"), 999.0), 4),
        "volume_ratio_5m": round(_sf(plan.get("volume_ratio_5m")), 3),
        "volume_ratio_15m": round(_sf(plan.get("volume_ratio_15m")), 3),
        "risk_percent": round(_sf(plan.get("risk_percent"), 999.0), 4),
        "room_r": round(_sf(plan.get("room_r")), 3),
        "extension_atr_5m": round(_sf(plan.get("extension_atr_5m"), 999.0), 3),
    }

    if evidence["score"] < STRICT_EARLY_MIN_SCORE:
        return False, {"reason": "SCORE", **evidence}
    if evidence["zone_distance_percent"] > STRICT_EARLY_MAX_ZONE_DISTANCE_PERCENT:
        return False, {"reason": "ZONE_DISTANCE", **evidence}
    if evidence["volume_ratio_5m"] < STRICT_EARLY_MIN_VOLUME_RATIO_5M:
        return False, {"reason": "VOLUME_5M", **evidence}
    if evidence["volume_ratio_15m"] < STRICT_EARLY_MIN_VOLUME_RATIO_15M:
        return False, {"reason": "VOLUME_15M", **evidence}
    if evidence["risk_percent"] > STRICT_EARLY_MAX_RISK_PERCENT:
        return False, {"reason": "RISK", **evidence}
    if evidence["room_r"] < STRICT_EARLY_MIN_ROOM_R:
        return False, {"reason": "ROOM", **evidence}
    if evidence["extension_atr_5m"] > STRICT_EARLY_MAX_EXTENSION_ATR:
        return False, {"reason": "EXTENSION", **evidence}

    return True, {"reason": "STRICT_EARLY_A_PLUS_PLUS", **evidence}


def _accelerated_evaluate_entry_plan(*args: Any, **kwargs: Any):
    plan, reason = _ORIGINAL_EVALUATE(*args, **kwargs)
    ok, evidence = strict_early_entry_qualifies(plan)
    if not ok:
        return plan, reason

    promoted = dict(plan)
    promoted["status"] = "ENTRY"
    promoted["strict_early_futures_entry"] = True
    promoted["strict_early_futures_entry_version"] = VERSION
    promoted["strict_early_futures_entry_evidence"] = evidence
    return promoted, reason


def _plan_native_confirmation(
    decision: Mapping[str, Any] | None,
    planned_direction: str,
) -> Tuple[bool, Dict[str, Any]]:
    """Fail open only when the strict ENTRY plan exists but raw micro is absent.

    This function is called only after ``evaluate_entry_plan`` has returned ENTRY,
    which already requires normal plan confirmation or the A++ early conversion.
    Downstream quality/execution/survival gates remain mandatory.
    """
    direction = str(planned_direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, {"reason": "PLAN_DIRECTION_INVALID"}

    if not isinstance(decision, Mapping) or not decision:
        return True, {
            "reason": "ENTRY_PLAN_NATIVE_CONFIRMATION",
            "planned_direction": direction,
            "confirmations": {"plan_native": True},
            "accelerator_version": VERSION,
        }

    return _ORIGINAL_FRESH(decision, direction)


def install() -> None:
    global _INSTALLED, _ORIGINAL_FRESH, _ORIGINAL_EVALUATE
    if _INSTALLED:
        return
    _INSTALLED = True

    # Keep awareness broad, but make the actual fast trade higher quality and
    # earlier in the move.
    fast_entry.MIN_FAST_SCORE = FAST_MIN_SCORE
    fast_entry.MIN_MOVE_3_PERCENT = FAST_MIN_MOVE_3M_PERCENT
    fast_entry.MIN_MOVE_5_PERCENT = FAST_MIN_MOVE_5M_PERCENT
    fast_entry.MIN_VOLUME_RATIO = FAST_MIN_VOLUME_RATIO
    fast_entry.MAX_EXTENSION_ATR = FAST_MAX_EXTENSION_ATR
    fast_entry.MAX_FAST_RISK_PERCENT = FAST_MAX_RISK_PERCENT
    fast_entry.MIN_FAST_ROOM_R = FAST_MIN_ROOM_R

    # Tighten the plan quality/risk geometry slightly, then remove the redundant
    # requirement for a separate raw-momentum object when the plan itself is
    # already a fully confirmed ENTRY.
    entry_plan.ENTRY_MIN_SCORE = ENTRY_MIN_SCORE
    entry_plan.MIN_ENTRY_VOLUME_RATIO = ENTRY_MIN_VOLUME_RATIO_5M
    entry_plan.MAX_PLAN_RISK_PERCENT = ENTRY_MAX_RISK_PERCENT
    entry_plan.MIN_ROOM_R = ENTRY_MIN_ROOM_R

    direction_guard.MIN_FRESH_MOVE_3M_PERCENT = FRESH_MOVE_3M_PERCENT
    direction_guard.MIN_FRESH_MOVE_5M_PERCENT = FRESH_MOVE_5M_PERCENT
    direction_guard.MIN_FRESH_RELATIVE_STRENGTH = FRESH_RELATIVE_STRENGTH

    _ORIGINAL_FRESH = direction_guard.fresh_entry_plan_confirmation
    direction_guard.fresh_entry_plan_confirmation = _plan_native_confirmation

    # Convert only A++ PREP plans slightly before the narrow pullback zone. This
    # happens at plan evaluation, so the ordinary downstream signal pipeline and
    # every capital-protection gate still run exactly as before.
    _ORIGINAL_EVALUATE = entry_plan.evaluate_entry_plan
    entry_plan.evaluate_entry_plan = _accelerated_evaluate_entry_plan


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "entry_min_score": ENTRY_MIN_SCORE,
        "entry_min_volume_ratio_5m": ENTRY_MIN_VOLUME_RATIO_5M,
        "entry_max_risk_percent": ENTRY_MAX_RISK_PERCENT,
        "entry_min_room_r": ENTRY_MIN_ROOM_R,
        "fresh_move_3m_percent": FRESH_MOVE_3M_PERCENT,
        "fresh_move_5m_percent": FRESH_MOVE_5M_PERCENT,
        "fast_min_score": FAST_MIN_SCORE,
        "fast_min_move_3m_percent": FAST_MIN_MOVE_3M_PERCENT,
        "fast_min_move_5m_percent": FAST_MIN_MOVE_5M_PERCENT,
        "fast_min_volume_ratio": FAST_MIN_VOLUME_RATIO,
        "fast_max_extension_atr": FAST_MAX_EXTENSION_ATR,
        "strict_early_enabled": True,
        "strict_early_min_score": STRICT_EARLY_MIN_SCORE,
        "strict_early_max_zone_distance_percent": STRICT_EARLY_MAX_ZONE_DISTANCE_PERCENT,
        "strict_early_min_volume_ratio_5m": STRICT_EARLY_MIN_VOLUME_RATIO_5M,
        "strict_early_min_volume_ratio_15m": STRICT_EARLY_MIN_VOLUME_RATIO_15M,
        "strict_early_max_risk_percent": STRICT_EARLY_MAX_RISK_PERCENT,
        "strict_early_min_room_r": STRICT_EARLY_MIN_ROOM_R,
        "strict_early_max_extension_atr": STRICT_EARLY_MAX_EXTENSION_ATR,
        "safety_guards_bypassed": False,
    }
