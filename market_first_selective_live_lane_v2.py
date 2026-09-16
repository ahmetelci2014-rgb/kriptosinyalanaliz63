"""V3 tuning for the Selective Live Lane.

The live system was correctly identifying strong continuation moves but two
failure modes remained:
1) score 92-93 continuation entries could be rejected by RECOVERY_STRICT even
   after the normal final-execution gate had accepted them;
2) borderline score 94-95 entries could use the one-confirmation exception too
   easily, which is undesirable after examples such as BCH.

V3 therefore adds a narrow momentum-continuation path for score 92-93 while
requiring at least two Direction Engine confirmations for every score below 96.
Score 96+ may still use the one-confirmation improving-CVD path. All existing hard
blocks remain: full 5M/15M/1H alignment, market alignment, risk/target/volume/
extension limits, no reversal, no relaxed Shadow Edge and no materially opposite
flow. HALT is never bypassed.

No exchange order is placed and no stop is widened.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_background_live_bridge as background_bridge
import market_first_profit_survival_gate as survival_gate
import market_first_selective_live_lane as base

VERSION = "MARKET_FIRST_SELECTIVE_LIVE_LANE_V3_MOMENTUM_2026_09_16"
MIN_CONFIRMATIONS = 1
MIN_BORDERLINE_CONFIRMATIONS = 2
MIN_CONTINUATION_SCORE = 92
MIN_CONTINUATION_CONFIRMATIONS = 2
MIN_CONTINUATION_VOLUME_RATIO_5M = 0.90
MIN_CONTINUATION_VOLUME_RATIO_15M = 0.80
MIN_CONTINUATION_MOVE_3M = 0.08
MIN_CONTINUATION_MOVE_5M = 0.15
MIN_CONTINUATION_BREAKOUT_MOVE_5M = 0.10
MIN_FLOW_WHEN_NO_FRESH = 0.10
MIN_IMPULSE_WHEN_FLOW_NEUTRAL = 0.20
MIN_BOOK_WHEN_FLOW_NEUTRAL = -0.05
MIN_DERIVATIVES_SOFT_SCORE = -1
MAX_OPPOSITE_FLOW = -0.10

_INSTALLED = False
_POST_FINAL_RECOVERY_PATCHED = False


def _aligned_move(direction: str, value: Any) -> float:
    raw = base._sf(value)
    return raw if str(direction).upper() == "LONG" else -raw


def selective_lane_qualifies(
    decision: Mapping[str, Any] | None,
    profile: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    if not isinstance(decision, Mapping):
        return False, {"reason": "NO_DECISION"}
    if not bool(decision.get("entry_plan_trade")):
        return False, {"reason": "ENTRY_PLAN_ONLY"}
    if bool(decision.get("shadow_edge_relaxed_profit_gate")):
        return False, {"reason": "NO_RELAXED_SHADOW_EDGE"}

    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, {"reason": "DIRECTION"}

    active_profile = profile if isinstance(profile, Mapping) else getattr(background_bridge, "_PROFILE", {})
    if not isinstance(active_profile, Mapping) or not bool(active_profile.get("enabled")):
        return False, {"reason": "BACKGROUND_PROFILE_DISABLED"}

    history = base._profile_evidence(active_profile, direction)
    history_ok, history_reason = base._history_is_strong(history)
    if not history_ok:
        return False, {"reason": f"HISTORY_{history_reason}", **history}

    engine = decision.get("direction_engine") if isinstance(decision.get("direction_engine"), Mapping) else {}
    if not engine:
        return False, {"reason": "DIRECTION_ENGINE_MISSING", **history}
    if bool(engine.get("reversal")):
        return False, {"reason": "REVERSAL", **history}

    selected = str(engine.get("selected_direction") or "").upper()
    if selected and selected != direction:
        return False, {"reason": "ENGINE_CONFLICT", "selected_direction": selected, **history}

    structures = engine.get("structures") if isinstance(engine.get("structures"), Mapping) else {}
    s5 = str(structures.get("5m") or decision.get("structure_5m") or "").upper()
    s15 = str(structures.get("15m") or decision.get("structure_15m") or "").upper()
    s1h = str(structures.get("1h") or decision.get("structure_1h") or "").upper()
    if any(value != direction for value in (s5, s15, s1h)):
        return False, {"reason": "MTF_NOT_FULLY_ALIGNED", "structures": [s5, s15, s1h], **history}
    if not base._market_aligned(decision, direction):
        return False, {"reason": "MARKET_NOT_ALIGNED", **history}

    confirmations = base._si(engine.get("confirmations"))
    score = base._si(decision.get("score"))
    risk = base._sf(decision.get("risk_percent"), 99.0)
    target_r = max(
        base._sf(decision.get("profit_target_r")),
        base._sf(decision.get("technical_target_r")),
        base._sf(decision.get("room_r")),
    )
    expected = max(
        base._sf(decision.get("expected_move_percent")),
        base._sf(decision.get("profit_target_percent")),
    )
    volume5 = base._sf(decision.get("volume_ratio_5m"), base._sf(decision.get("volume_ratio_1m")))
    volume15 = base._sf(decision.get("volume_ratio_15m"))
    extension = base._sf(decision.get("extension_atr_5m"), 999.0)
    derivatives_soft = base._si(decision.get("derivatives_soft_score"))

    flags = engine.get("confirmation_flags") if isinstance(engine.get("confirmation_flags"), Mapping) else {}
    fresh_micro = bool(flags.get("fresh_micro"))
    move3 = _aligned_move(direction, decision.get("move_3m_percent"))
    move5 = _aligned_move(direction, decision.get("move_5m_percent"))
    breakout = bool(decision.get("breakout_20m"))
    directional_momentum = (
        fresh_micro
        or (move3 >= MIN_CONTINUATION_MOVE_3M and move5 >= MIN_CONTINUATION_MOVE_5M)
        or (breakout and move5 >= MIN_CONTINUATION_BREAKOUT_MOVE_5M)
    )

    direction_block = engine.get(direction.lower()) if isinstance(engine.get(direction.lower()), Mapping) else {}
    taker = base._sf(direction_block.get("taker_alignment"), base._sf(decision.get("taker_imbalance_alignment")))
    cvd = base._sf(direction_block.get("cvd_alignment"), base._sf(decision.get("cvd_ratio")))
    cvd_impulse = base._sf(decision.get("cvd_impulse_alignment"))
    book = base._sf(decision.get("book_imbalance_alignment"))
    taker_available = bool(decision.get("taker_available"))
    cvd_available = bool(decision.get("cvd_available"))
    book_available = bool(decision.get("book_available"))

    evidence: Dict[str, Any] = {
        "selective_live_lane": True,
        "selective_live_lane_version": VERSION,
        "score": score,
        "risk_percent": round(risk, 4),
        "target_r": round(target_r, 4),
        "expected_move_percent": round(expected, 4),
        "confirmations": confirmations,
        "volume_ratio_5m": round(volume5, 4),
        "volume_ratio_15m": round(volume15, 4),
        "extension_atr_5m": round(extension, 4),
        "derivatives_soft_score": derivatives_soft,
        "fresh_micro": fresh_micro,
        "move_3m_aligned": round(move3, 4),
        "move_5m_aligned": round(move5, 4),
        "breakout_20m": breakout,
        "directional_momentum": directional_momentum,
        "taker_alignment": round(taker, 4),
        "cvd_alignment": round(cvd, 4),
        "cvd_impulse_alignment": round(cvd_impulse, 4),
        "book_alignment": round(book, 4),
        "structures": [s5, s15, s1h],
        "market_aligned": True,
        **history,
    }

    if score < MIN_CONTINUATION_SCORE:
        return False, {"reason": "SCORE", **evidence}

    score_path = "STANDARD_96_PLUS"
    required_confirmations = MIN_CONFIRMATIONS
    if score < base.MIN_SCORE:
        score_path = "MOMENTUM_CONTINUATION_92_93"
        required_confirmations = MIN_CONTINUATION_CONFIRMATIONS
        if not directional_momentum:
            return False, {"reason": "LOW_SCORE_NO_CONTINUATION", **evidence}
        if volume5 < MIN_CONTINUATION_VOLUME_RATIO_5M:
            return False, {"reason": "LOW_SCORE_VOLUME_5M", **evidence}
        if volume15 < MIN_CONTINUATION_VOLUME_RATIO_15M:
            return False, {"reason": "LOW_SCORE_VOLUME_15M", **evidence}
    elif score < 96:
        # 94-95 is no longer allowed through the one-confirmation shortcut. This
        # directly hardens the borderline path without penalising true A++ 96+.
        score_path = "BORDERLINE_94_95"
        required_confirmations = MIN_BORDERLINE_CONFIRMATIONS

    evidence["score_path"] = score_path
    evidence["required_confirmations"] = required_confirmations
    if confirmations < required_confirmations:
        return False, {"reason": "CONFIRMATIONS", **evidence}

    checks = (
        (0 < risk <= base.MAX_RISK_PERCENT, "RISK"),
        (target_r >= base.MIN_TARGET_R, "TARGET_R"),
        (expected >= base.MIN_EXPECTED_MOVE_PERCENT, "EXPECTED_MOVE"),
        (volume5 >= base.MIN_VOLUME_RATIO_5M, "VOLUME_5M"),
        (volume15 >= base.MIN_VOLUME_RATIO_15M, "VOLUME_15M"),
        (extension <= base.MAX_EXTENSION_ATR, "EXTENSION"),
        (derivatives_soft >= MIN_DERIVATIVES_SOFT_SCORE, "DERIVATIVES_SOFT"),
    )
    for ok, reason in checks:
        if not ok:
            return False, {"reason": reason, **evidence}

    # Hard flow vetoes remain absolute.
    if taker_available and cvd_available and taker <= MAX_OPPOSITE_FLOW and cvd <= MAX_OPPOSITE_FLOW:
        return False, {"reason": "TAKER_CVD_OPPOSITE", **evidence}
    if cvd_available and cvd_impulse < MAX_OPPOSITE_FLOW:
        return False, {"reason": "CVD_IMPULSE_OPPOSITE", **evidence}
    if book_available and book < MAX_OPPOSITE_FLOW:
        return False, {"reason": "BOOK_OPPOSITE", **evidence}

    flow_path = "FRESH_MICRO" if fresh_micro else None
    if not fresh_micro:
        normal_flow = (
            taker_available
            and cvd_available
            and taker >= MIN_FLOW_WHEN_NO_FRESH
            and cvd >= MIN_FLOW_WHEN_NO_FRESH
        )
        improving_impulse = (
            taker_available
            and cvd_available
            and taker > MAX_OPPOSITE_FLOW
            and cvd > MAX_OPPOSITE_FLOW
            and cvd_impulse >= MIN_IMPULSE_WHEN_FLOW_NEUTRAL
            and (not book_available or book >= MIN_BOOK_WHEN_FLOW_NEUTRAL)
        )
        if normal_flow:
            flow_path = "FLOW_ALIGNED"
        elif improving_impulse:
            flow_path = "IMPROVING_CVD_IMPULSE"
        else:
            return False, {"reason": "NO_FRESH_OR_IMPROVING_FLOW", **evidence}

    evidence["flow_path"] = flow_path
    reason = (
        "SELECTIVE_MOMENTUM_CONTINUATION_V3"
        if score_path == "MOMENTUM_CONTINUATION_92_93"
        else "SELECTIVE_A_PLUS_PLUS_V3"
    )
    return True, {"reason": reason, **evidence}


def _install_post_final_recovery_patch() -> None:
    """Allow a qualifying lane after an ordinary final-gate pass.

    Base V1 only relaxed RECOVERY_SCORE/CONFIRMATIONS when the final execution
    gate itself had needed the selective confirmation exception. A score-92/93
    candidate with 3 normal confirmations could therefore pass final execution
    and still die at RECOVERY_SCORE. At this point the survival wrapper runs after
    the upstream/final execution stack, so a surviving signal has already passed
    that gate; it is safe to evaluate the same narrow selective lane here.
    """
    global _POST_FINAL_RECOVERY_PATCHED
    if _POST_FINAL_RECOVERY_PATCHED:
        return
    _POST_FINAL_RECOVERY_PATCHED = True

    wrapped = survival_gate._recovery_reason

    def recovery_reason_with_post_final_lane(decision):
        ok, reason, evidence = wrapped(decision)
        if ok:
            return ok, reason, evidence
        if reason not in {"RECOVERY_SCORE", "RECOVERY_CONFIRMATIONS"}:
            return ok, reason, evidence
        if not isinstance(decision, Mapping):
            return ok, reason, evidence

        lane_ok, lane = selective_lane_qualifies(decision)
        base._RUN_COUNTS[f"POST_FINAL_{lane.get('reason') or 'UNKNOWN'}"] += 1
        if not lane_ok:
            return ok, reason, evidence

        if bool(decision.get("selective_live_lane_slot_consumed")):
            merged = dict(evidence or {})
            merged.update(lane)
            return True, "OK_SELECTIVE_LIVE_LANE_POST_FINAL", merged

        if base._RELAXED_ACCEPTS >= base.MAX_RELAXED_ACCEPTS_PER_RUN:
            base._RUN_COUNTS["POST_FINAL_LANE_LIMIT"] += 1
            return False, "SELECTIVE_LIVE_LANE_LIMIT", lane

        base._RELAXED_ACCEPTS += 1
        if isinstance(decision, dict):
            decision["selective_live_lane_slot_consumed"] = True
            decision["selective_live_lane_post_final_pass"] = True
            decision["selective_live_lane_evidence"] = dict(lane)
        base._RUN_COUNTS["POST_FINAL_RELAXED_ACCEPTED"] += 1
        base._ACCEPTED.append({
            "symbol": decision.get("symbol"),
            "direction": decision.get("direction"),
            "score": decision.get("score"),
            "risk_percent": decision.get("risk_percent"),
            "target_r": lane.get("target_r"),
            "expected_move_percent": lane.get("expected_move_percent"),
            "confirmations": lane.get("confirmations"),
            "direction_rate": lane.get("direction_rate"),
            "score_path": lane.get("score_path"),
            "post_final": True,
        })
        merged = dict(evidence or {})
        merged.update(lane)
        return True, "OK_SELECTIVE_LIVE_LANE_POST_FINAL", merged

    survival_gate._recovery_reason = recovery_reason_with_post_final_lane


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Base install closures resolve this symbol at runtime, so patch the pure
    # qualifier before installing the wrappers.
    base.VERSION = VERSION
    base.MIN_CONFIRMATIONS = MIN_CONFIRMATIONS
    base.selective_lane_qualifies = selective_lane_qualifies
    base.install()
    _install_post_final_recovery_patch()


def summary() -> Dict[str, Any]:
    payload = dict(base.summary())
    payload["version"] = VERSION
    payload["v3_rules"] = {
        "min_confirmations_96_plus": MIN_CONFIRMATIONS,
        "min_confirmations_94_95": MIN_BORDERLINE_CONFIRMATIONS,
        "min_continuation_score": MIN_CONTINUATION_SCORE,
        "min_continuation_confirmations": MIN_CONTINUATION_CONFIRMATIONS,
        "min_continuation_volume_5m": MIN_CONTINUATION_VOLUME_RATIO_5M,
        "min_continuation_volume_15m": MIN_CONTINUATION_VOLUME_RATIO_15M,
        "min_continuation_move_3m": MIN_CONTINUATION_MOVE_3M,
        "min_continuation_move_5m": MIN_CONTINUATION_MOVE_5M,
        "min_continuation_breakout_move_5m": MIN_CONTINUATION_BREAKOUT_MOVE_5M,
        "min_flow_when_no_fresh": MIN_FLOW_WHEN_NO_FRESH,
        "min_impulse_when_flow_neutral": MIN_IMPULSE_WHEN_FLOW_NEUTRAL,
        "min_book_when_flow_neutral": MIN_BOOK_WHEN_FLOW_NEUTRAL,
        "min_derivatives_soft_score": MIN_DERIVATIVES_SOFT_SCORE,
        "post_final_recovery_score_exception": True,
        "shadow_edge_relaxed_bypass": False,
    }
    return payload


def finish() -> Dict[str, Any]:
    payload = summary()
    base._atomic_save(payload)
    return payload
