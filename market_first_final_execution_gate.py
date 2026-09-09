"""Final execution-quality gate for Market First live trades.

This is deliberately the last decision gate before a candidate becomes a real
Telegram trade. It does not create signals, place orders, widen stops or bypass
any existing Profit Quality / TAO / portfolio / cooldown guard.

V2.1 fixes the wrapper order: the upstream decision-to-signal pipeline now runs
first so the Direction Engine can attach its live diagnostics and Profit Quality
can reject non-qualifying candidates before this final continuation/flow veto.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Tuple

import market_first_runner as runner

VERSION = "MARKET_FIRST_FINAL_EXECUTION_GATE_V2_1_2026_09_09"
STATE_FILE = "market_first_final_execution_gate.json"
MODE = "FINAL_DECISION_AFTER_UPSTREAM_QUALITY_NO_STOP_WIDENING_NO_ORDERS"

MIN_DIRECTION_CONFIRMATIONS = 3
MIN_TECHNICAL_EXPECTED_MOVE_PERCENT = 0.85
MIN_FLOW_ALIGNMENT_WITHOUT_FRESH_MICRO = 0.15
MAX_OPPOSITE_CVD_IMPULSE = -0.10
MAX_OPPOSITE_BOOK_ALIGNMENT = -0.10
MAX_OPPOSITE_FLOW = -0.10
MIN_DERIVATIVES_SOFT_SCORE_WITHOUT_FRESH_MICRO = 0

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
    if bool(_flags(decision).get("fresh_micro")):
        return True
    direction = str(decision.get("direction") or "").upper()
    move3 = _aligned_move(direction, decision.get("move_3m_percent"))
    move5 = _aligned_move(direction, decision.get("move_5m_percent"))
    breakout = bool(decision.get("breakout_20m"))
    return (move3 >= 0.10 and move5 >= 0.15) or (breakout and move5 >= 0.10)


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
    if confirmations < MIN_DIRECTION_CONFIRMATIONS:
        return False, "DIRECTION_CONFIRMATIONS_BELOW_3", {"confirmations": confirmations}

    technical_expected = _sf(decision.get("expected_move_percent"))
    if technical_expected < MIN_TECHNICAL_EXPECTED_MOVE_PERCENT:
        return False, "NEAR_TECHNICAL_EXPECTATION_TOO_SMALL", {
            "technical_expected_move_percent": technical_expected,
        }

    fresh_micro = _has_fresh_micro(decision)
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
        "fresh_micro": fresh_micro,
        "taker_alignment": round(taker, 4),
        "cvd_alignment": round(cvd, 4),
        "cvd_impulse_alignment": round(cvd_impulse, 4),
        "book_alignment": round(book, 4),
        "derivatives_soft_score": derivatives_soft,
        "tao_quality_bridge": bool(decision.get("tao_quality_bridge")),
    }

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
            "min_technical_expected_move_percent": MIN_TECHNICAL_EXPECTED_MOVE_PERCENT,
            "min_flow_alignment_without_fresh_micro": MIN_FLOW_ALIGNMENT_WITHOUT_FRESH_MICRO,
            "max_opposite_cvd_impulse": MAX_OPPOSITE_CVD_IMPULSE,
            "max_opposite_book_alignment": MAX_OPPOSITE_BOOK_ALIGNMENT,
            "min_derivatives_soft_score_without_fresh_micro": MIN_DERIVATIVES_SOFT_SCORE_WITHOUT_FRESH_MICRO,
        },
        "run_counts": dict(_RUN_COUNTS),
        "accepted": _RUN_ACCEPTED[-20:],
        "note": (
            "Execution-gate observations only; not realised PnL. Upstream Profit Quality/TAO pipeline "
            "runs before this gate. Stops are not widened by this module."
        ),
    }
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, prefix=".final_gate.", suffix=".tmp", delete=False) as handle:
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
        # Critical order: run the existing pipeline first. Entry Plan adds the
        # Direction Engine diagnostics inside that call, Profit Quality applies
        # its >=2% / >=2.5R gate, and the TAO bridge decorates only valid signals.
        # Only a surviving signal reaches this final continuation/flow veto.
        signal = original_decision_to_signal(decision)
        if not isinstance(signal, Mapping):
            _RUN_COUNTS["UPSTREAM_REJECTED"] += 1
            return signal
        if not isinstance(decision, Mapping):
            return signal

        ok, reason, evidence = _execution_reason(decision)
        _RUN_COUNTS[reason] += 1
        if not ok:
            print("FINAL EXECUTION GATE ELENDİ:", decision.get("symbol"), reason, evidence)
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
        "min_direction_confirmations": MIN_DIRECTION_CONFIRMATIONS,
        "min_technical_expected_move_percent": MIN_TECHNICAL_EXPECTED_MOVE_PERCENT,
        "flow_required_when_no_fresh_micro": True,
        "stop_widening": False,
        "exchange_orders": False,
        "run_counts": dict(_RUN_COUNTS),
    }
