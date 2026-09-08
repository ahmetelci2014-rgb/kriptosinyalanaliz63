"""Controlled entry acceleration for Market First V5.

The live system was finding many valid preparations but promoting very few of
those preparations into real trade candidates.  This module keeps every final
safety guard intact while moving confirmation closer to the actual entry zone:

- a high-quality ENTRY plan may use its own 15m+1h+5m/volume confirmation when
  the raw momentum strategy has not produced a separate micro decision yet;
- small, same-direction micro progress is enough once the pullback plan itself is
  already confirmed;
- breakout fast-entry can trigger earlier, but only with a higher score, stronger
  volume, tighter extension/risk and adequate room.

It never sends Telegram messages or exchange orders by itself.  It only tunes the
existing Market First decision gates before the normal ML, duplicate, portfolio,
major-market, live-flow and liquidity guards run.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Tuple

import market_first_entry_plan as entry_plan
import market_first_fast_entry as fast_entry
import market_first_live_direction_guard as direction_guard

VERSION = "MARKET_FIRST_ENTRY_ACCELERATOR_V1_2026_09_08"
_INSTALLED = False
_ORIGINAL_FRESH = None

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


def _plan_native_confirmation(
    decision: Mapping[str, Any] | None,
    planned_direction: str,
) -> Tuple[bool, Dict[str, Any]]:
    """Fail open only when the strict ENTRY plan exists but raw micro is absent.

    This function is called only after ``evaluate_entry_plan`` has returned ENTRY,
    which already requires 15m+1h agreement, 5m structure alignment, the entry
    zone, volume, risk geometry and the configured score floor.
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
    global _INSTALLED, _ORIGINAL_FRESH
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
        "safety_guards_bypassed": False,
    }
