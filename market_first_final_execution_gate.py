"""Final execution-quality gate for Market First live trades.

This is deliberately the last decision gate before a candidate becomes a real
Telegram trade. It does not create signals, place orders, widen stops or bypass
any existing Profit Quality / TAO / portfolio / cooldown guard.

V2.4 fixes a live bottleneck discovered after Profit Quality began re-anchoring
TP1/TP2/TP3. Final Execution now evaluates a merged post-quality snapshot so the
fresh signal target fields win over stale pre-quality decision fields. The
historical exact-3-confirmation rule remains the default; a 4-confirmation setup
may pass only under a narrow high-quality exception with fresh micro, strong
profit room, low extension and aligned higher timeframes.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Tuple

import market_first_runner as runner

VERSION = "MARKET_FIRST_FINAL_EXECUTION_GATE_V2_4_SNAPSHOT_FIX_2026_09_27"
STATE_FILE = "market_first_final_execution_gate.json"
MODE = "POST_PROFIT_SIGNAL_SNAPSHOT_WITH_NARROW_4CONF_EXCEPTION"

MIN_DIRECTION_CONFIRMATIONS = 3
MAX_DIRECTION_CONFIRMATIONS = 3
MIN_TECHNICAL_EXPECTED_MOVE_PERCENT = 0.85
MIN_FLOW_ALIGNMENT_WITHOUT_FRESH_MICRO = 0.15
MAX_OPPOSITE_CVD_IMPULSE = -0.10
MAX_OPPOSITE_BOOK_ALIGNMENT = -0.10
MAX_OPPOSITE_FLOW = -0.10
MIN_DERIVATIVES_SOFT_SCORE_WITHOUT_FRESH_MICRO = 0

# Historical evidence still favors exact 3 confirmations. Four confirmations are
# allowed only for a tightly constrained profit-runner profile that will still
# face A+ and A++ downstream gates.
FOUR_CONFIRM_EXCEPTION_ENABLED = True
FOUR_CONFIRM_MIN_SCORE = 92
FOUR_CONFIRM_MIN_TARGET_PERCENT = 2.50
FOUR_CONFIRM_MIN_TARGET_R = 3.50
FOUR_CONFIRM_MAX_EXTENSION_ATR_5M = 0.90
FOUR_CONFIRM_MAX_EXTENSION_ATR_2H = 1.25

_INSTALLED = False
_RUN_COUNTS: Counter = Counter()
_RUN_ACCEPTED: list[Dict[str, Any]] = []


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


def _aligned_move(direction: str, value: Any) -> float:
    raw = _sf(value)
    return raw if str(direction).upper() == "LONG" else -raw


def _engine(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    engine = decision.get("direction_engine")
    return engine if isinstance(engine, Mapping) else {}


def _flags(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    flags = _engine(decision).get("confirmation_flags")
    return flags if isinstance(flags, Mapping) else {}


def _has_fresh_micro(decision: Mapping[str, Any]) -> bool:
    gate = decision.get("final_execution_gate")
    if isinstance(gate, Mapping) and "fresh_micro" in gate:
        return bool(gate.get("fresh_micro"))
    if bool(_flags(decision).get("fresh_micro")):
        return True
    direction = str(decision.get("direction") or "").upper()
    move3 = _aligned_move(direction, decision.get("move_3m_percent"))
    move5 = _aligned_move(direction, decision.get("move_5m_percent"))
    breakout = bool(decision.get("breakout_20m"))
    return (move3 >= 0.10 and move5 >= 0.15) or (breakout and move5 >= 0.10)


def _target_percent(item: Mapping[str, Any]) -> float:
    return (
        _sf(item.get("profit_target_percent"))
        or _sf(item.get("expected_move_percent"))
        or _sf(item.get("expected_move_high_percent"))
    )


def _target_r(item: Mapping[str, Any]) -> float:
    direct = (
        _sf(item.get("rr_tp3"))
        or _sf(item.get("profit_target_r"))
        or _sf(item.get("technical_target_r"))
    )
    if direct > 0:
        return direct
    expected = _target_percent(item)
    risk = _sf(item.get("risk_percent"))
    return expected / risk if expected > 0 and risk > 0 else 0.0


def _execution_snapshot(decision: Mapping[str, Any], signal: Mapping[str, Any]) -> Dict[str, Any]:
    """Merge live decision context with post-Profit-Quality signal fields.

    Profit Quality re-anchors TP1/TP2/TP3 and writes a fresh expected move onto
    the signal. Using only the older decision object could therefore reject a
    candidate as "near target too small" even after it had passed the >=2% / >=2.5R
    quality gate. Signal fields intentionally override decision fields here while
    diagnostic structures that only exist on the decision remain available.
    """
    merged = dict(decision)
    for key, value in signal.items():
        if value is not None:
            merged[key] = value
    if "direction_engine" not in merged and isinstance(decision.get("direction_engine"), Mapping):
        merged["direction_engine"] = decision.get("direction_engine")
    return merged


def _strong_four_confirmation_exception(
    item: Mapping[str, Any],
    confirmations: int,
    fresh_micro: bool,
) -> Tuple[bool, Dict[str, Any]]:
    direction = str(item.get("direction") or "").upper()
    score = _si(item.get("score"))
    target_percent = _target_percent(item)
    target_r = _target_r(item)
    extension_5m = _sf(item.get("extension_atr_5m"), 999.0)
    extension_2h = _sf(
        item.get("context_2h_extension_atr"),
        _sf(item.get("extension_atr_2h"), 999.0),
    )
    structure_15m = str(item.get("structure_15m") or "").upper()
    structure_1h = str(item.get("structure_1h") or "").upper()
    structure_2h = str(
        item.get("context_2h_direction")
        or item.get("profit_structure_2h")
        or item.get("structure_2h")
        or "UNKNOWN"
    ).upper()
    alignment_2h = str(item.get("context_2h_alignment") or "").upper()

    evidence = {
        "score": score,
        "target_percent": round(target_percent, 4),
        "target_r": round(target_r, 3),
        "extension_atr_5m": round(extension_5m, 4),
        "extension_atr_2h": round(extension_2h, 4),
        "structure_2h": structure_2h,
        "alignment_2h": alignment_2h,
    }

    ok = bool(
        FOUR_CONFIRM_EXCEPTION_ENABLED
        and confirmations == 4
        and fresh_micro
        and score >= FOUR_CONFIRM_MIN_SCORE
        and target_percent >= FOUR_CONFIRM_MIN_TARGET_PERCENT
        and target_r >= FOUR_CONFIRM_MIN_TARGET_R
        and extension_5m <= FOUR_CONFIRM_MAX_EXTENSION_ATR_5M
        and extension_2h <= FOUR_CONFIRM_MAX_EXTENSION_ATR_2H
        and structure_15m == direction
        and structure_1h == direction
        and structure_2h == direction
        and alignment_2h in {"", "ALIGNED"}
    )
    return ok, evidence


def _execution_reason(decision: Mapping[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION", {}

    engine = _engine(decision)
    if not engine:
        return False, "DIRECTION_ENGINE_MISSING_AFTER_UPSTREAM", {}

    selected = str(engine.get("selected_direction") or "").upper()
    if selected and selected != direction:
        return False, "DIRECTION_ENGINE_CONFLICT", {"selected_direction": selected}

    confirmations = _si(engine.get("confirmations"))
    fresh_micro = _has_fresh_micro(decision)
    structure_15m = str(decision.get("structure_15m") or "").upper()
    structure_1h = str(decision.get("structure_1h") or "").upper()
    technical_expected = _target_percent(decision)
    target_r = _target_r(decision)

    if confirmations < MIN_DIRECTION_CONFIRMATIONS:
        return False, "DIRECTION_CONFIRMATIONS_BELOW_3", {
            "confirmations": confirmations,
            "fresh_micro": fresh_micro,
        }

    four_exception = False
    four_exception_evidence: Dict[str, Any] = {}
    if confirmations > MAX_DIRECTION_CONFIRMATIONS:
        four_exception, four_exception_evidence = _strong_four_confirmation_exception(
            decision, confirmations, fresh_micro
        )
        if not four_exception:
            return False, "DIRECTION_CONFIRMATIONS_ABOVE_3_LATE", {
                "confirmations": confirmations,
                "fresh_micro": fresh_micro,
                **four_exception_evidence,
            }

    if technical_expected < MIN_TECHNICAL_EXPECTED_MOVE_PERCENT:
        return False, "NEAR_TECHNICAL_EXPECTATION_TOO_SMALL", {
            "technical_expected_move_percent": technical_expected,
        }

    taker_available = bool(decision.get("taker_available"))
    cvd_available = bool(decision.get("cvd_available"))
    book_available = bool(decision.get("book_available"))
    derivatives_available = bool(decision.get("derivatives_available"))

    taker = _sf(decision.get("taker_imbalance_alignment"))
    cvd = _sf(decision.get("cvd_ratio"))
    direction_key = str(direction).lower()
    direction_block = engine.get(direction_key)
    if isinstance(direction_block, Mapping):
        cvd = _sf(direction_block.get("cvd_alignment"), cvd)
        taker = _sf(direction_block.get("taker_alignment"), taker)

    cvd_impulse = _sf(decision.get("cvd_impulse_alignment"))
    book = _sf(decision.get("book_imbalance_alignment"))
    derivatives_soft = _si(decision.get("derivatives_soft_score"))

    evidence = {
        "confirmations": confirmations,
        "technical_expected_move_percent": round(technical_expected, 4),
        "target_r": round(target_r, 3),
        "fresh_micro": fresh_micro,
        "four_confirmation_exception": four_exception,
        "structure_15m": structure_15m,
        "structure_1h": structure_1h,
        "taker_alignment": round(taker, 4),
        "cvd_alignment": round(cvd, 4),
        "cvd_impulse_alignment": round(cvd_impulse, 4),
        "book_alignment": round(book, 4),
        "derivatives_soft_score": derivatives_soft,
        "tao_quality_bridge": bool(decision.get("tao_quality_bridge")),
    }
    if four_exception:
        evidence.update({f"four_exception_{key}": value for key, value in four_exception_evidence.items()})

    # Strong opposite flow remains a hard stop-risk veto. The release above does
    # not weaken CVD/book protection.
    if taker_available and cvd_available and taker <= MAX_OPPOSITE_FLOW and cvd <= MAX_OPPOSITE_FLOW:
        return False, "TAKER_CVD_OPPOSITE", evidence
    if cvd_available and cvd_impulse < MAX_OPPOSITE_CVD_IMPULSE:
        return False, "CVD_IMPULSE_OPPOSITE", evidence
    if book_available and book < MAX_OPPOSITE_BOOK_ALIGNMENT:
        return False, "BOOK_PRESSURE_OPPOSITE", evidence

    if not fresh_micro:
        if not taker_available or taker < MIN_FLOW_ALIGNMENT_WITHOUT_FRESH_MICRO:
            return False, "NO_FRESH_MICRO_TAKER_WEAK", evidence
        if not cvd_available or cvd < MIN_FLOW_ALIGNMENT_WITHOUT_FRESH_MICRO:
            return False, "NO_FRESH_MICRO_CVD_WEAK", evidence
        if derivatives_available and derivatives_soft < MIN_DERIVATIVES_SOFT_SCORE_WITHOUT_FRESH_MICRO:
            return False, "NO_FRESH_MICRO_DERIVATIVES_WEAK", evidence

    return True, "OK", evidence


def _save_summary() -> Dict[str, Any]:
    payload = {
        "version": VERSION,
        "mode": MODE,
        "thresholds": {
            "min_direction_confirmations": MIN_DIRECTION_CONFIRMATIONS,
            "default_max_direction_confirmations": MAX_DIRECTION_CONFIRMATIONS,
            "min_technical_expected_move_percent": MIN_TECHNICAL_EXPECTED_MOVE_PERCENT,
            "min_flow_alignment_without_fresh_micro": MIN_FLOW_ALIGNMENT_WITHOUT_FRESH_MICRO,
            "max_opposite_cvd_impulse": MAX_OPPOSITE_CVD_IMPULSE,
            "max_opposite_book_alignment": MAX_OPPOSITE_BOOK_ALIGNMENT,
            "four_confirmation_exception_enabled": FOUR_CONFIRM_EXCEPTION_ENABLED,
            "four_confirmation_min_score": FOUR_CONFIRM_MIN_SCORE,
            "four_confirmation_min_target_percent": FOUR_CONFIRM_MIN_TARGET_PERCENT,
            "four_confirmation_min_target_r": FOUR_CONFIRM_MIN_TARGET_R,
            "four_confirmation_max_extension_atr_5m": FOUR_CONFIRM_MAX_EXTENSION_ATR_5M,
            "four_confirmation_max_extension_atr_2h": FOUR_CONFIRM_MAX_EXTENSION_ATR_2H,
            "min_derivatives_soft_score_without_fresh_micro": MIN_DERIVATIVES_SOFT_SCORE_WITHOUT_FRESH_MICRO,
        },
        "run_counts": dict(_RUN_COUNTS),
        "accepted": _RUN_ACCEPTED[-20:],
        "note": (
            "Execution-gate observations only; not realised PnL. Final Execution uses the post-Profit-Quality "
            "signal snapshot. Four confirmations remain blocked except for the narrow strong fresh/2H-aligned exception."
        ),
    }
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=folder,
            prefix=".final_gate.", suffix=".tmp", delete=False
        ) as handle:
            temp_path = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
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
    return payload


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_decision_to_signal = runner.decision_to_signal

    def decision_to_signal_final_gate(decision):
        # Upstream must run first: Profit Quality creates the post-quality signal
        # with its re-anchored targets. Final Execution evaluates that fresh target
        # state together with Direction Engine / flow fields from the decision.
        signal = original_decision_to_signal(decision)
        if not isinstance(signal, Mapping):
            _RUN_COUNTS["UPSTREAM_REJECTED"] += 1
            return signal
        if not isinstance(decision, Mapping):
            return signal

        snapshot = _execution_snapshot(decision, signal)
        ok, reason, evidence = _execution_reason(snapshot)
        _RUN_COUNTS[reason] += 1
        if not ok:
            print("FINAL EXECUTION GATE ELENDİ:", snapshot.get("symbol"), reason, evidence)
            return None

        out = dict(signal)
        out["final_execution_gate_version"] = VERSION
        out["final_execution_gate"] = evidence
        _RUN_COUNTS["ACCEPTED"] += 1
        _RUN_ACCEPTED.append({
            "symbol": out.get("symbol"),
            "direction": out.get("direction"),
            "score": out.get("score"),
            **evidence,
        })
        return out

    runner.decision_to_signal = decision_to_signal_final_gate


def finish() -> Dict[str, Any]:
    return _save_summary()


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "upstream_quality_runs_first": True,
        "uses_post_profit_signal_snapshot": True,
        "min_direction_confirmations": MIN_DIRECTION_CONFIRMATIONS,
        "default_max_direction_confirmations": MAX_DIRECTION_CONFIRMATIONS,
        "four_confirmation_strong_exception": FOUR_CONFIRM_EXCEPTION_ENABLED,
        "min_technical_expected_move_percent": MIN_TECHNICAL_EXPECTED_MOVE_PERCENT,
        "flow_required_when_no_fresh_micro": True,
        "opposite_flow_veto_unchanged": True,
        "stop_widening": False,
        "exchange_orders": False,
        "run_counts": dict(_RUN_COUNTS),
    }
