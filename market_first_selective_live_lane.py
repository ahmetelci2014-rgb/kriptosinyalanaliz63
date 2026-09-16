"""Selective RECOVERY_STRICT live lane for Market First.

Purpose:
- keep HALT absolute;
- keep Profit Quality and all send/duplicate/portfolio guards intact;
- allow a *narrow* ENTRY_PLAN exception when the background direction has a
  large, durable TP-first edge and the current setup is A++;
- never allow opposite order-flow, reversals, counter-market entries or wide risk.

This lane only relaxes two redundant confirmation counts:
- Final Execution: 3 -> 2 confirmations for a fully aligned A++ ENTRY_PLAN;
- Profit Survival: 96 score / 4 confirmations may use 94 score / 2 confirmations
  when every stricter lane condition below is satisfied.

It does not place exchange orders, widen stops, bypass HALT, or guarantee profit.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_background_live_bridge as background_bridge
import market_first_final_execution_gate as final_gate
import market_first_profit_survival_gate as survival_gate

VERSION = "MARKET_FIRST_SELECTIVE_LIVE_LANE_V1_2026_09_16"
STATE_FILE = "market_first_selective_live_lane.json"

# Historical proof. These thresholds are directional on purpose: the lane should
# not average a strong direction together with a weak one.
MIN_GENERAL_RECENT_SAMPLES = 200
MIN_GENERAL_RECENT_RATE = 0.80
MIN_CLEAN_SAMPLES = 30
MIN_CLEAN_RATE = 0.85
MIN_ENTRY_SWING_SAMPLES = 200
MIN_ENTRY_SWING_RATE = 0.85
MIN_DIRECTION_SAMPLES = 120
MIN_DIRECTION_RATE = 0.92
MIN_LONG_RUN_SAMPLES = 800
MIN_LONG_RUN_RATE = 0.70
MIN_SWING_SAMPLES = 300
MIN_SWING_RATE = 0.58

# Current A++ setup requirements.
MIN_SCORE = 94
MAX_RISK_PERCENT = 0.65
MIN_TARGET_R = 3.00
MIN_EXPECTED_MOVE_PERCENT = 1.75
MIN_CONFIRMATIONS = 2
MIN_VOLUME_RATIO_5M = 0.90
MIN_VOLUME_RATIO_15M = 0.80
MAX_EXTENSION_ATR = 0.90
MIN_FLOW_WHEN_NO_FRESH = 0.10
MAX_OPPOSITE_FLOW = -0.10
MAX_RELAXED_ACCEPTS_PER_RUN = 2

_INSTALLED = False
_ORIGINAL_EXECUTION_REASON = None
_ORIGINAL_RECOVERY_REASON = None
_RELAXED_ACCEPTS = 0
_RUN_COUNTS: Counter = Counter()
_ACCEPTED: list[Dict[str, Any]] = []


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


def _market_aligned(decision: Mapping[str, Any], direction: str) -> bool:
    preferred = str(decision.get("market_preferred_direction") or "").upper()
    if preferred:
        return preferred == direction
    regime = str(decision.get("market_regime") or "").upper()
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
        "general_recent_samples": _si(recent.get("samples")),
        "general_recent_rate": _sf(recent.get("tp_first_rate")),
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


def _history_is_strong(evidence: Mapping[str, Any]) -> Tuple[bool, str]:
    checks = (
        (_si(evidence.get("general_recent_samples")) >= MIN_GENERAL_RECENT_SAMPLES, "GENERAL_RECENT_SAMPLES"),
        (_sf(evidence.get("general_recent_rate")) >= MIN_GENERAL_RECENT_RATE, "GENERAL_RECENT_RATE"),
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


def selective_lane_qualifies(
    decision: Mapping[str, Any] | None,
    profile: Optional[Mapping[str, Any]] = None,
) -> Tuple[bool, Dict[str, Any]]:
    if not isinstance(decision, Mapping):
        return False, {"reason": "NO_DECISION"}
    if not bool(decision.get("entry_plan_trade")):
        return False, {"reason": "ENTRY_PLAN_ONLY"}

    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, {"reason": "DIRECTION"}

    active_profile = profile if isinstance(profile, Mapping) else getattr(background_bridge, "_PROFILE", {})
    if not isinstance(active_profile, Mapping) or not bool(active_profile.get("enabled")):
        return False, {"reason": "BACKGROUND_PROFILE_DISABLED"}

    history = _profile_evidence(active_profile, direction)
    history_ok, history_reason = _history_is_strong(history)
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
    if not _market_aligned(decision, direction):
        return False, {"reason": "MARKET_NOT_ALIGNED", **history}

    confirmations = _si(engine.get("confirmations"))
    score = _si(decision.get("score"))
    risk = _sf(decision.get("risk_percent"), 99.0)
    target_r = max(
        _sf(decision.get("profit_target_r")),
        _sf(decision.get("technical_target_r")),
        _sf(decision.get("room_r")),
    )
    expected = max(
        _sf(decision.get("expected_move_percent")),
        _sf(decision.get("profit_target_percent")),
    )
    volume5 = _sf(decision.get("volume_ratio_5m"), _sf(decision.get("volume_ratio_1m")))
    volume15 = _sf(decision.get("volume_ratio_15m"))
    extension = _sf(decision.get("extension_atr_5m"), 999.0)

    flags = engine.get("confirmation_flags") if isinstance(engine.get("confirmation_flags"), Mapping) else {}
    fresh_micro = bool(flags.get("fresh_micro"))

    direction_block = engine.get(direction.lower()) if isinstance(engine.get(direction.lower()), Mapping) else {}
    taker = _sf(direction_block.get("taker_alignment"), _sf(decision.get("taker_imbalance_alignment")))
    cvd = _sf(direction_block.get("cvd_alignment"), _sf(decision.get("cvd_ratio")))
    cvd_impulse = _sf(decision.get("cvd_impulse_alignment"))
    book = _sf(decision.get("book_imbalance_alignment"))
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
        "fresh_micro": fresh_micro,
        "taker_alignment": round(taker, 4),
        "cvd_alignment": round(cvd, 4),
        "cvd_impulse_alignment": round(cvd_impulse, 4),
        "book_alignment": round(book, 4),
        "structures": [s5, s15, s1h],
        "market_aligned": True,
        **history,
    }

    checks = (
        (score >= MIN_SCORE, "SCORE"),
        (0 < risk <= MAX_RISK_PERCENT, "RISK"),
        (target_r >= MIN_TARGET_R, "TARGET_R"),
        (expected >= MIN_EXPECTED_MOVE_PERCENT, "EXPECTED_MOVE"),
        (confirmations >= MIN_CONFIRMATIONS, "CONFIRMATIONS"),
        (volume5 >= MIN_VOLUME_RATIO_5M, "VOLUME_5M"),
        (volume15 >= MIN_VOLUME_RATIO_15M, "VOLUME_15M"),
        (extension <= MAX_EXTENSION_ATR, "EXTENSION"),
    )
    for ok, reason in checks:
        if not ok:
            return False, {"reason": reason, **evidence}

    # Hard flow vetoes remain absolute. The lane never fights live opposing flow.
    if taker_available and cvd_available and taker <= MAX_OPPOSITE_FLOW and cvd <= MAX_OPPOSITE_FLOW:
        return False, {"reason": "TAKER_CVD_OPPOSITE", **evidence}
    if cvd_available and cvd_impulse < MAX_OPPOSITE_FLOW:
        return False, {"reason": "CVD_IMPULSE_OPPOSITE", **evidence}
    if book_available and book < MAX_OPPOSITE_FLOW:
        return False, {"reason": "BOOK_OPPOSITE", **evidence}

    # If there is no fresh micro impulse, require both live taker and CVD to agree.
    if not fresh_micro:
        if not taker_available or not cvd_available:
            return False, {"reason": "NO_FRESH_FLOW_UNAVAILABLE", **evidence}
        if taker < MIN_FLOW_WHEN_NO_FRESH or cvd < MIN_FLOW_WHEN_NO_FRESH:
            return False, {"reason": "NO_FRESH_FLOW_WEAK", **evidence}

    return True, {"reason": "SELECTIVE_A_PLUS_PLUS", **evidence}


def _atomic_save(payload: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=folder,
            prefix=".selective_live_lane.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, STATE_FILE)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def install() -> None:
    global _INSTALLED, _ORIGINAL_EXECUTION_REASON, _ORIGINAL_RECOVERY_REASON, _RELAXED_ACCEPTS
    if _INSTALLED:
        return
    _INSTALLED = True
    _RELAXED_ACCEPTS = 0
    _ORIGINAL_EXECUTION_REASON = final_gate._execution_reason
    _ORIGINAL_RECOVERY_REASON = survival_gate._recovery_reason

    def execution_reason_with_selective_lane(decision):
        ok, reason, evidence = _ORIGINAL_EXECUTION_REASON(decision)
        if ok:
            return ok, reason, evidence
        if reason != "DIRECTION_CONFIRMATIONS_BELOW_3":
            return ok, reason, evidence

        lane_ok, lane = selective_lane_qualifies(decision)
        _RUN_COUNTS[f"FINAL_{lane.get('reason') or 'UNKNOWN'}"] += 1
        if not lane_ok:
            return ok, reason, evidence

        if isinstance(decision, dict):
            decision["selective_live_lane_final_pass"] = True
            decision["selective_live_lane_evidence"] = dict(lane)
        merged = dict(evidence or {})
        merged.update(lane)
        return True, "OK_SELECTIVE_LIVE_LANE", merged

    def recovery_reason_with_selective_lane(decision):
        global _RELAXED_ACCEPTS
        ok, reason, evidence = _ORIGINAL_RECOVERY_REASON(decision)
        if ok:
            return ok, reason, evidence

        # Never relax any failure except the two deliberately redundant floors.
        if reason not in {"RECOVERY_SCORE", "RECOVERY_CONFIRMATIONS"}:
            return ok, reason, evidence
        if not isinstance(decision, Mapping) or not bool(decision.get("selective_live_lane_final_pass")):
            return ok, reason, evidence

        lane_ok, lane = selective_lane_qualifies(decision)
        _RUN_COUNTS[f"SURVIVAL_{lane.get('reason') or 'UNKNOWN'}"] += 1
        if not lane_ok:
            return ok, reason, evidence

        if bool(decision.get("selective_live_lane_slot_consumed")):
            merged = dict(evidence or {})
            merged.update(lane)
            return True, "OK_SELECTIVE_LIVE_LANE", merged

        if _RELAXED_ACCEPTS >= MAX_RELAXED_ACCEPTS_PER_RUN:
            _RUN_COUNTS["SURVIVAL_LANE_LIMIT"] += 1
            return False, "SELECTIVE_LIVE_LANE_LIMIT", lane

        _RELAXED_ACCEPTS += 1
        if isinstance(decision, dict):
            decision["selective_live_lane_slot_consumed"] = True
        _RUN_COUNTS["SURVIVAL_RELAXED_ACCEPTED"] += 1
        accepted = {
            "symbol": decision.get("symbol"),
            "direction": decision.get("direction"),
            "score": decision.get("score"),
            "risk_percent": decision.get("risk_percent"),
            "target_r": lane.get("target_r"),
            "expected_move_percent": lane.get("expected_move_percent"),
            "confirmations": lane.get("confirmations"),
            "direction_rate": lane.get("direction_rate"),
        }
        _ACCEPTED.append(accepted)
        merged = dict(evidence or {})
        merged.update(lane)
        return True, "OK_SELECTIVE_LIVE_LANE", merged

    final_gate._execution_reason = execution_reason_with_selective_lane
    survival_gate._recovery_reason = recovery_reason_with_selective_lane


def summary() -> Dict[str, Any]:
    profile = getattr(background_bridge, "_PROFILE", {})
    short = _profile_evidence(profile, "SHORT") if isinstance(profile, Mapping) else {}
    long = _profile_evidence(profile, "LONG") if isinstance(profile, Mapping) else {}
    return {
        "version": VERSION,
        "mode": "RECOVERY_STRICT_DIRECTIONAL_A_PLUS_PLUS_ONLY",
        "thresholds": {
            "min_score": MIN_SCORE,
            "max_risk_percent": MAX_RISK_PERCENT,
            "min_target_r": MIN_TARGET_R,
            "min_expected_move_percent": MIN_EXPECTED_MOVE_PERCENT,
            "min_confirmations": MIN_CONFIRMATIONS,
            "min_volume_ratio_5m": MIN_VOLUME_RATIO_5M,
            "min_volume_ratio_15m": MIN_VOLUME_RATIO_15M,
            "max_extension_atr": MAX_EXTENSION_ATR,
            "min_direction_samples": MIN_DIRECTION_SAMPLES,
            "min_direction_rate": MIN_DIRECTION_RATE,
            "max_relaxed_accepts_per_run": MAX_RELAXED_ACCEPTS_PER_RUN,
        },
        "direction_snapshot": {"SHORT": short, "LONG": long},
        "relaxed_accepts_this_run": _RELAXED_ACCEPTS,
        "run_counts": dict(_RUN_COUNTS),
        "accepted": _ACCEPTED[-20:],
        "halt_bypass": False,
        "opposite_flow_bypass": False,
        "profit_quality_bypass": False,
        "exchange_orders": False,
        "stop_widening": False,
    }


def finish() -> Dict[str, Any]:
    payload = summary()
    _atomic_save(payload)
    return payload
