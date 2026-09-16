"""V2 tuning for the Selective Live Lane.

V1 still required two Direction Engine confirmations. Live diagnostics showed a
cleaner early pattern that can be missed by that rule: fully aligned 5M/15M/1H,
market-aligned ENTRY_PLAN, low risk and >=3R target, one structural confirmation,
near-neutral taker/CVD level, but a strong direction-adjusted CVD impulse already
turning with the trade.

V2 permits that pattern while keeping these hard blocks:
- HALT is untouched;
- Profit Quality still runs first;
- no reversal;
- no counter-market trade;
- no relaxed Shadow Edge profit exception;
- no materially opposite taker+CVD, CVD impulse or book pressure;
- historical direction evidence remains >=92% TP-first over >=120 samples;
- risk <=0.65%, target >=3R, expected move >=1.75%, extension <=0.90 ATR.

No exchange order is placed and no stop is widened.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_background_live_bridge as background_bridge
import market_first_selective_live_lane as base

VERSION = "MARKET_FIRST_SELECTIVE_LIVE_LANE_V2_2026_09_16"
MIN_CONFIRMATIONS = 1
MIN_FLOW_WHEN_NO_FRESH = 0.10
MIN_IMPULSE_WHEN_FLOW_NEUTRAL = 0.20
MIN_BOOK_WHEN_FLOW_NEUTRAL = -0.05
MIN_DERIVATIVES_SOFT_SCORE = -1
MAX_OPPOSITE_FLOW = -0.10

_INSTALLED = False


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
        "taker_alignment": round(taker, 4),
        "cvd_alignment": round(cvd, 4),
        "cvd_impulse_alignment": round(cvd_impulse, 4),
        "book_alignment": round(book, 4),
        "structures": [s5, s15, s1h],
        "market_aligned": True,
        **history,
    }

    checks = (
        (score >= base.MIN_SCORE, "SCORE"),
        (0 < risk <= base.MAX_RISK_PERCENT, "RISK"),
        (target_r >= base.MIN_TARGET_R, "TARGET_R"),
        (expected >= base.MIN_EXPECTED_MOVE_PERCENT, "EXPECTED_MOVE"),
        (confirmations >= MIN_CONFIRMATIONS, "CONFIRMATIONS"),
        (volume5 >= base.MIN_VOLUME_RATIO_5M, "VOLUME_5M"),
        (volume15 >= base.MIN_VOLUME_RATIO_15M, "VOLUME_15M"),
        (extension <= base.MAX_EXTENSION_ATR, "EXTENSION"),
        (derivatives_soft >= MIN_DERIVATIVES_SOFT_SCORE, "DERIVATIVES_SOFT"),
    )
    for ok, reason in checks:
        if not ok:
            return False, {"reason": reason, **evidence}

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
    return True, {"reason": "SELECTIVE_A_PLUS_PLUS_V2", **evidence}


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


def summary() -> Dict[str, Any]:
    payload = dict(base.summary())
    payload["version"] = VERSION
    payload["v2_rules"] = {
        "min_confirmations": MIN_CONFIRMATIONS,
        "min_flow_when_no_fresh": MIN_FLOW_WHEN_NO_FRESH,
        "min_impulse_when_flow_neutral": MIN_IMPULSE_WHEN_FLOW_NEUTRAL,
        "min_book_when_flow_neutral": MIN_BOOK_WHEN_FLOW_NEUTRAL,
        "min_derivatives_soft_score": MIN_DERIVATIVES_SOFT_SCORE,
        "shadow_edge_relaxed_bypass": False,
    }
    return payload


def finish() -> Dict[str, Any]:
    payload = summary()
    base._atomic_save(payload)
    return payload
