"""Evidence-backed exception lane for RECOVERY_STRICT.

The system's recent realised PnL can force Profit Survival into RECOVERY_STRICT.
That protection is useful, but the previous implementation also blanket-blocked
the statistically strongest ENTRY_PLAN background cohort. This module opens a
very narrow bridge for that cohort without weakening HALT, Final Execution,
order-flow/CVD vetoes, duplicate/cooldown/portfolio guards or stop geometry.

Historical evidence used by this lane comes from Background Live Bridge. Generic
pre-entry shadow data is deliberately excluded because its estimated net R is
negative. No exchange orders are placed and no stop is widened.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any, Dict, Mapping, Tuple

import market_first_background_live_bridge as bridge
import market_first_entry_plan as entry_plan
import market_first_profit_survival_gate as survival_gate
import market_first_profit_survival_v2 as survival_v2

VERSION = "MARKET_FIRST_RECOVERY_EVIDENCE_LANE_V1_2026_09_15"

# Historical cohort requirements. These are intentionally above the ordinary
# Background Live Bridge floors and are based on the current durable evidence:
# recent ENTRY_PLAN 88%+, clean ENTRY_PLAN 85%+, ENTRY+Swing 94%, both directions
# ~89-92%, long-run ENTRY_PLAN ~71%.
MIN_RECENT_SAMPLES = 100
MIN_RECENT_RATE = 0.85
MIN_CLEAN_SAMPLES = 25
MIN_CLEAN_RATE = 0.82
MIN_ENTRY_SWING_SAMPLES = 100
MIN_ENTRY_SWING_RATE = 0.90
MIN_DIRECTION_SAMPLES = 50
MIN_DIRECTION_RATE = 0.85
MIN_LONG_RUN_SAMPLES = 500
MIN_LONG_RUN_RATE = 0.70
MIN_SWING_SAMPLES = 180
MIN_SWING_RATE = 0.56

# Current setup requirements while RECOVERY_STRICT is active.
MIN_SCORE = 94
MAX_RISK_PERCENT = 0.65
MIN_ROOM_R = 3.00
MAX_EXTENSION_ATR = 0.90
MIN_VOLUME_RATIO_5M = 1.10
MIN_VOLUME_RATIO_15M = 0.90
MAX_ZONE_DISTANCE_PERCENT = 1.10
MIN_EXPECTED_MOVE_PERCENT = 1.75
MIN_TARGET_R = 3.00
MAX_PROMOTIONS_PER_RUN = 1

_INSTALLED = False
_ORIGINAL_BG_QUALIFY = None
_ORIGINAL_PROMOTE = None
_ORIGINAL_RECOVERY_REASON = None
_RECOVERY_PROMOTIONS = 0
_RUN_COUNTS: Counter = Counter()


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


def _market_aligned(item: Mapping[str, Any], direction: str) -> bool:
    """Require explicit same-side market context; no countertrend exception."""
    preferred = str(item.get("market_preferred_direction") or "").upper()
    if preferred:
        return preferred == direction
    regime = str(item.get("market_regime") or "").upper()
    if "BEAR" in regime:
        return direction == "SHORT"
    if "BULL" in regime:
        return direction == "LONG"
    return False


def _profile_evidence(profile: Mapping[str, Any], direction: str) -> Dict[str, Any]:
    recent = profile.get("recent_entry_plan") if isinstance(profile.get("recent_entry_plan"), Mapping) else {}
    clean = profile.get("recent_clean_entry_plan") if isinstance(profile.get("recent_clean_entry_plan"), Mapping) else {}
    combined = profile.get("recent_entry_plus_swing") if isinstance(profile.get("recent_entry_plus_swing"), Mapping) else {}
    directions = profile.get("direction") if isinstance(profile.get("direction"), Mapping) else {}
    directional = directions.get(direction) if isinstance(directions.get(direction), Mapping) else {}
    long_run = profile.get("long_run_entry_plan") if isinstance(profile.get("long_run_entry_plan"), Mapping) else {}
    swing = profile.get("swing_v2") if isinstance(profile.get("swing_v2"), Mapping) else {}
    return {
        "recent_samples": _si(recent.get("samples")),
        "recent_rate": _sf(recent.get("tp_first_rate")),
        "clean_samples": _si(clean.get("samples")),
        "clean_rate": _sf(clean.get("tp_first_rate")),
        "entry_swing_samples": _si(combined.get("samples")),
        "entry_swing_rate": _sf(combined.get("tp_first_rate")),
        "direction_samples": _si(directional.get("samples")),
        "direction_rate": _sf(directional.get("tp_first_rate")),
        "long_run_samples": _si(long_run.get("samples")),
        "long_run_rate": _sf(long_run.get("tp_first_rate")),
        "swing_samples": _si(swing.get("samples")),
        "swing_rate": _sf(swing.get("tp_first_rate")),
    }


def _profile_is_strong(evidence: Mapping[str, Any]) -> Tuple[bool, str]:
    checks = (
        (_si(evidence.get("recent_samples")) >= MIN_RECENT_SAMPLES, "RECENT_SAMPLES"),
        (_sf(evidence.get("recent_rate")) >= MIN_RECENT_RATE, "RECENT_RATE"),
        (_si(evidence.get("clean_samples")) >= MIN_CLEAN_SAMPLES, "CLEAN_SAMPLES"),
        (_sf(evidence.get("clean_rate")) >= MIN_CLEAN_RATE, "CLEAN_RATE"),
        (_si(evidence.get("entry_swing_samples")) >= MIN_ENTRY_SWING_SAMPLES, "ENTRY_SWING_SAMPLES"),
        (_sf(evidence.get("entry_swing_rate")) >= MIN_ENTRY_SWING_RATE, "ENTRY_SWING_RATE"),
        (_si(evidence.get("direction_samples")) >= MIN_DIRECTION_SAMPLES, "DIRECTION_SAMPLES"),
        (_sf(evidence.get("direction_rate")) >= MIN_DIRECTION_RATE, "DIRECTION_RATE"),
        (_si(evidence.get("long_run_samples")) >= MIN_LONG_RUN_SAMPLES, "LONG_RUN_SAMPLES"),
        (_sf(evidence.get("long_run_rate")) >= MIN_LONG_RUN_RATE, "LONG_RUN_RATE"),
        (_si(evidence.get("swing_samples")) >= MIN_SWING_SAMPLES, "SWING_SAMPLES"),
        (_sf(evidence.get("swing_rate")) >= MIN_SWING_RATE, "SWING_RATE"),
    )
    for ok, reason in checks:
        if not ok:
            return False, reason
    return True, "OK"


def recovery_background_qualifies(
    plan: Mapping[str, Any] | None,
    profile: Mapping[str, Any],
    survival_health: Mapping[str, Any],
    *,
    consume_slot: bool = False,
) -> Tuple[bool, Dict[str, Any]]:
    """Pure RECOVERY_STRICT qualification plus optional per-run slot consume."""
    global _RECOVERY_PROMOTIONS
    if not isinstance(plan, Mapping):
        return False, {"reason": "NO_PLAN"}
    mode = str(survival_health.get("mode") or "NORMAL").upper()
    if mode == "HALT":
        return False, {"reason": "SURVIVAL_HALT", "survival_reason": survival_health.get("reason")}
    if mode != "RECOVERY_STRICT":
        return False, {"reason": f"NOT_RECOVERY_{mode}"}
    if str(plan.get("status") or "").upper() != "PREP":
        return False, {"reason": "NOT_PREP"}
    if not bool(profile.get("enabled")):
        return False, {"reason": "BACKGROUND_EDGE_NOT_VALIDATED"}

    direction = str(plan.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, {"reason": "DIRECTION"}

    historical = _profile_evidence(profile, direction)
    historical_ok, historical_reason = _profile_is_strong(historical)
    if not historical_ok:
        return False, {"reason": f"RECOVERY_HISTORY_{historical_reason}", **historical}

    structures = tuple(str(plan.get(f"structure_{tf}") or "").upper() for tf in ("5m", "15m", "1h"))
    if any(value != direction for value in structures):
        return False, {"reason": "RECOVERY_MTF_NOT_FULLY_ALIGNED", "structures": structures, **historical}
    if not _market_aligned(plan, direction):
        return False, {"reason": "RECOVERY_MARKET_NOT_ALIGNED", **historical}
    if bool(plan.get("reversal")):
        return False, {"reason": "RECOVERY_REVERSAL", **historical}

    evidence: Dict[str, Any] = {
        "recovery_evidence_lane": True,
        "recovery_evidence_version": VERSION,
        "score": _si(plan.get("score")),
        "zone_distance_percent": round(_sf(plan.get("zone_distance_percent"), 999.0), 4),
        "volume_ratio_5m": round(_sf(plan.get("volume_ratio_5m")), 3),
        "volume_ratio_15m": round(_sf(plan.get("volume_ratio_15m")), 3),
        "risk_percent": round(_sf(plan.get("risk_percent"), 999.0), 4),
        "room_r": round(_sf(plan.get("room_r")), 3),
        "extension_atr_5m": round(_sf(plan.get("extension_atr_5m"), 999.0), 3),
        "structures": structures,
        "market_aligned": True,
        **historical,
    }
    checks = (
        (evidence["score"] >= MIN_SCORE, "RECOVERY_SCORE"),
        (evidence["zone_distance_percent"] <= MAX_ZONE_DISTANCE_PERCENT, "RECOVERY_ZONE_DISTANCE"),
        (evidence["volume_ratio_5m"] >= MIN_VOLUME_RATIO_5M, "RECOVERY_VOLUME_5M"),
        (evidence["volume_ratio_15m"] >= MIN_VOLUME_RATIO_15M, "RECOVERY_VOLUME_15M"),
        (0 < evidence["risk_percent"] <= MAX_RISK_PERCENT, "RECOVERY_RISK"),
        (evidence["room_r"] >= MIN_ROOM_R, "RECOVERY_ROOM"),
        (evidence["extension_atr_5m"] <= MAX_EXTENSION_ATR, "RECOVERY_EXTENSION"),
    )
    for ok, reason in checks:
        if not ok:
            return False, {"reason": reason, **evidence}

    if consume_slot and _RECOVERY_PROMOTIONS >= MAX_PROMOTIONS_PER_RUN:
        return False, {"reason": "RECOVERY_EVIDENCE_LANE_LIMIT", **evidence}
    if consume_slot:
        _RECOVERY_PROMOTIONS += 1
    return True, {"reason": "RECOVERY_EVIDENCE_LANE_A_PLUS_PLUS", **evidence}


def recovery_gate_reason(
    decision: Mapping[str, Any],
    original_reason=None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Apply a score-94 exception only to a validated recovery evidence trade.

    All original A++ flow/structure/derivatives checks are reused by temporarily
    satisfying only V1's score floor. No other original recovery rule is relaxed.
    """
    original_reason = original_reason or survival_gate._recovery_reason
    if not bool(decision.get("recovery_evidence_lane")):
        return original_reason(decision)

    lane_evidence = decision.get("recovery_evidence_evidence")
    if not isinstance(lane_evidence, Mapping):
        return False, "RECOVERY_EVIDENCE_METADATA", {}
    if not bool(decision.get("background_live_bridge")):
        return False, "RECOVERY_EVIDENCE_SOURCE", {}

    direction = str(decision.get("direction") or "").upper()
    actual_score = _si(decision.get("score"))
    risk = _sf(decision.get("risk_percent"), 99.0)
    expected = max(_sf(decision.get("expected_move_percent")), _sf(decision.get("profit_target_percent")))
    target_r = max(
        _sf(decision.get("profit_target_r")),
        _sf(decision.get("technical_target_r")),
        _sf(decision.get("room_r")),
    )
    extension = _sf(decision.get("extension_atr_5m"), 999.0)

    hard = {
        "actual_score": actual_score,
        "risk_percent": round(risk, 4),
        "expected_move_percent": round(expected, 4),
        "target_r": round(target_r, 4),
        "extension_atr_5m": round(extension, 4),
        "strict_market_aligned": _market_aligned(decision, direction),
        "recovery_evidence_lane": True,
        "recovery_evidence_version": VERSION,
    }
    if bool(decision.get("shadow_edge_relaxed_profit_gate")):
        return False, "RECOVERY_NO_RELAXED_SHADOW_EDGE", hard
    if actual_score < MIN_SCORE:
        return False, "RECOVERY_EVIDENCE_SCORE", hard
    if risk <= 0 or risk > MAX_RISK_PERCENT:
        return False, "RECOVERY_EVIDENCE_RISK", hard
    if expected < MIN_EXPECTED_MOVE_PERCENT:
        return False, "RECOVERY_EVIDENCE_EXPECTED_MOVE", hard
    if target_r < MIN_TARGET_R:
        return False, "RECOVERY_EVIDENCE_TARGET_R", hard
    if extension > MAX_EXTENSION_ATR:
        return False, "RECOVERY_EVIDENCE_EXTENSION", hard
    if not hard["strict_market_aligned"]:
        return False, "RECOVERY_EVIDENCE_MARKET", hard

    # Revalidate the historical proof at the final gate. This prevents a stale or
    # manually tagged decision from obtaining the exception.
    historical_ok, historical_reason = _profile_is_strong(lane_evidence)
    if not historical_ok:
        return False, f"RECOVERY_EVIDENCE_HISTORY_{historical_reason}", {**hard, **dict(lane_evidence)}

    boosted = dict(decision)
    boosted["score"] = max(actual_score, survival_gate.RECOVERY_MIN_SCORE)
    # Countertrend exceptions are not allowed for the evidence lane. We already
    # proved explicit market alignment above, then preserve it for the base gate.
    boosted["market_preferred_direction"] = direction
    ok, reason, base_evidence = original_reason(boosted)
    evidence = dict(base_evidence or {})
    evidence.update(hard)
    evidence["score"] = actual_score
    evidence["historical_recent_rate"] = _sf(lane_evidence.get("recent_rate"))
    evidence["historical_clean_rate"] = _sf(lane_evidence.get("clean_rate"))
    evidence["historical_entry_swing_rate"] = _sf(lane_evidence.get("entry_swing_rate"))
    evidence["historical_direction_rate"] = _sf(lane_evidence.get("direction_rate"))
    evidence["historical_long_run_rate"] = _sf(lane_evidence.get("long_run_rate"))
    if not ok:
        return False, reason, evidence
    return True, "OK_RECOVERY_EVIDENCE_LANE", evidence


def _decorate_promoted_decision(out: Dict[str, Any], plan: Mapping[str, Any]) -> Dict[str, Any]:
    bridge_evidence = plan.get("background_live_bridge_evidence")
    if not isinstance(bridge_evidence, Mapping):
        return out
    if not bool(bridge_evidence.get("recovery_evidence_lane")):
        return out
    out["background_live_bridge"] = True
    out["background_live_bridge_version"] = plan.get("background_live_bridge_version")
    out["background_live_bridge_evidence"] = dict(bridge_evidence)
    out["recovery_evidence_lane"] = True
    out["recovery_evidence_version"] = VERSION
    out["recovery_evidence_evidence"] = dict(bridge_evidence)
    return out


def install() -> None:
    global _INSTALLED, _ORIGINAL_BG_QUALIFY, _ORIGINAL_PROMOTE, _ORIGINAL_RECOVERY_REASON, _RECOVERY_PROMOTIONS
    if _INSTALLED:
        return
    _INSTALLED = True
    _RECOVERY_PROMOTIONS = 0

    _ORIGINAL_BG_QUALIFY = bridge.background_promotion_qualifies
    _ORIGINAL_PROMOTE = entry_plan.promote_to_decision
    _ORIGINAL_RECOVERY_REASON = survival_gate._recovery_reason

    def background_qualify_with_recovery(plan, profile=None, survival_health=None):
        active_profile = profile if isinstance(profile, Mapping) else bridge._PROFILE
        health = survival_health if isinstance(survival_health, Mapping) else survival_v2._current_health_v2()
        mode = str(health.get("mode") or "NORMAL").upper()
        if mode == "NORMAL":
            ok, evidence = _ORIGINAL_BG_QUALIFY(plan, active_profile, health)
        elif mode == "RECOVERY_STRICT":
            ok, evidence = recovery_background_qualifies(
                plan, active_profile, health, consume_slot=True
            )
        else:
            ok, evidence = False, {"reason": f"SURVIVAL_{mode}", "survival_reason": health.get("reason")}
        _RUN_COUNTS[str(evidence.get("reason") or "UNKNOWN")] += 1
        return ok, evidence

    def promote_to_decision_with_recovery(existing, plan):
        out = _ORIGINAL_PROMOTE(existing, plan)
        return _decorate_promoted_decision(out, plan)

    def recovery_reason_with_evidence(decision):
        ok, reason, evidence = recovery_gate_reason(decision, _ORIGINAL_RECOVERY_REASON)
        _RUN_COUNTS[reason] += 1
        return ok, reason, evidence

    bridge.background_promotion_qualifies = background_qualify_with_recovery
    entry_plan.promote_to_decision = promote_to_decision_with_recovery
    survival_gate._recovery_reason = recovery_reason_with_evidence


def summary() -> Dict[str, Any]:
    profile = bridge._PROFILE if isinstance(getattr(bridge, "_PROFILE", None), Mapping) else {}
    live_rates = _profile_evidence(profile, "SHORT") if profile else {}
    return {
        "version": VERSION,
        "mode": "RECOVERY_STRICT_EVIDENCE_BACKED_ENTRY_PLAN_ONLY",
        "thresholds": {
            "min_score": MIN_SCORE,
            "max_risk_percent": MAX_RISK_PERCENT,
            "min_room_r": MIN_ROOM_R,
            "max_extension_atr": MAX_EXTENSION_ATR,
            "min_volume_ratio_5m": MIN_VOLUME_RATIO_5M,
            "min_volume_ratio_15m": MIN_VOLUME_RATIO_15M,
            "min_expected_move_percent": MIN_EXPECTED_MOVE_PERCENT,
            "min_target_r": MIN_TARGET_R,
            "max_promotions_per_run": MAX_PROMOTIONS_PER_RUN,
            "min_recent_rate": MIN_RECENT_RATE,
            "min_clean_rate": MIN_CLEAN_RATE,
            "min_entry_swing_rate": MIN_ENTRY_SWING_RATE,
            "min_direction_rate": MIN_DIRECTION_RATE,
            "min_long_run_rate": MIN_LONG_RUN_RATE,
        },
        "current_background_snapshot": live_rates,
        "promotions_this_run": _RECOVERY_PROMOTIONS,
        "run_counts": dict(_RUN_COUNTS),
        "halt_bypass": False,
        "final_execution_bypass": False,
        "exchange_orders": False,
        "stop_widening": False,
    }
