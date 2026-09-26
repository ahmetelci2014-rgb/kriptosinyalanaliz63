"""A++ convergence gate for Market First real Telegram trades.

Purpose:
- keep the existing A+ High Profit / Low SL gate intact,
- require the real trade to come from a genuine Entry Plan ENTRY,
- require 2H/1H/15M directional convergence,
- require enough structural room for TP2/TP3 continuation,
- require fresh micro execution confirmation,
- reject late/overextended 2H entries.

This module does not create signals, widen stops, place exchange orders or send
extra Telegram alerts. Rejected candidates remain available to the existing
background/internal ledgers.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Tuple

import market_first_runner as runner

VERSION = "MARKET_FIRST_A_PLUS_PLUS_CONVERGENCE_V1_2026_09_26"
MODE = "A_PLUS_PLUS_2H_ENTRY_PLAN_FRESH_MICRO_LIVE_ONLY"
STATE_FILE = "market_first_a_plus_plus_convergence.json"

MIN_ENTRY_PLAN_ROOM_R = 3.00
MAX_2H_EXTENSION_ATR = 1.55
REQUIRE_ENTRY_PLAN = True
REQUIRE_2H_ALIGNMENT = True
REQUIRE_FRESH_MICRO = True

_INSTALLED = False
_RUN_COUNTS: Counter = Counter()
_RUN_ACCEPTED: list[Dict[str, Any]] = []


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _aligned_move(direction: str, value: Any) -> float:
    raw = _sf(value)
    return raw if str(direction).upper() == "LONG" else -raw


def _fresh_micro(signal: Mapping[str, Any], decision: Mapping[str, Any]) -> bool:
    gate = signal.get("final_execution_gate")
    if isinstance(gate, Mapping) and "fresh_micro" in gate:
        return bool(gate.get("fresh_micro"))

    engine = decision.get("direction_engine")
    if isinstance(engine, Mapping):
        flags = engine.get("confirmation_flags")
        if isinstance(flags, Mapping) and "fresh_micro" in flags:
            return bool(flags.get("fresh_micro"))
    return False


def evaluate_signal(
    decision: Mapping[str, Any] | None,
    signal: Mapping[str, Any] | None,
) -> Tuple[bool, str, Dict[str, Any]]:
    if not isinstance(decision, Mapping) or not isinstance(signal, Mapping):
        return False, "INVALID_INPUT", {}

    direction = str(signal.get("direction") or decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION", {}

    a_plus_certified = bool(signal.get("high_profit_low_sl_version"))
    a_plus_grade = str(signal.get("high_profit_low_sl_grade") or "").upper()
    if not a_plus_certified or a_plus_grade != "A+":
        return False, "A_PLUS_NOT_CERTIFIED", {}

    entry_plan_trade = bool(decision.get("entry_plan_trade"))
    entry_plan_version = str(decision.get("entry_plan_version") or "")
    stage = str(decision.get("stage") or "").upper()
    room_r = _sf(decision.get("room_r"), _sf(signal.get("room_r")))

    structure_15m = str(decision.get("structure_15m") or "").upper()
    structure_1h = str(decision.get("structure_1h") or "").upper()
    structure_2h = str(
        decision.get("context_2h_direction")
        or signal.get("context_2h_direction")
        or decision.get("profit_structure_2h")
        or signal.get("profit_structure_2h")
        or decision.get("structure_2h")
        or "UNKNOWN"
    ).upper()
    context_2h_alignment = str(
        decision.get("context_2h_alignment")
        or signal.get("context_2h_alignment")
        or ""
    ).upper()
    context_2h_1h_alignment = str(
        decision.get("context_2h_1h_alignment")
        or signal.get("context_2h_1h_alignment")
        or ""
    ).upper()
    extension_2h = _sf(
        decision.get("context_2h_extension_atr"),
        _sf(signal.get("context_2h_extension_atr"), _sf(decision.get("extension_atr_2h"))),
    )

    fresh_micro = _fresh_micro(signal, decision)
    move3 = _aligned_move(direction, decision.get("move_3m_percent"))
    move5 = _aligned_move(direction, decision.get("move_5m_percent"))
    breakout = bool(decision.get("breakout_20m"))
    if move3 >= 0.10 and move5 >= 0.15:
        micro_source = "ALIGNED_3M_5M"
    elif breakout and move5 >= 0.10:
        micro_source = "BREAKOUT_20M"
    elif fresh_micro:
        micro_source = "DIRECTION_ENGINE_FRESH"
    else:
        micro_source = "NONE"

    evidence = {
        "direction": direction,
        "entry_plan_trade": entry_plan_trade,
        "entry_plan_version": entry_plan_version,
        "stage": stage,
        "room_r": round(room_r, 3),
        "structure_2h": structure_2h,
        "structure_1h": structure_1h,
        "structure_15m": structure_15m,
        "context_2h_alignment": context_2h_alignment,
        "context_2h_1h_alignment": context_2h_1h_alignment,
        "extension_atr_2h": round(extension_2h, 4),
        "fresh_micro": fresh_micro,
        "micro_source": micro_source,
        "aligned_move_3m_percent": round(move3, 4),
        "aligned_move_5m_percent": round(move5, 4),
    }

    if REQUIRE_ENTRY_PLAN and (not entry_plan_trade or not entry_plan_version):
        return False, "A_PLUS_PLUS_NOT_ENTRY_PLAN", evidence
    if stage != "READY":
        return False, "A_PLUS_PLUS_NOT_READY", evidence
    if room_r < MIN_ENTRY_PLAN_ROOM_R:
        return False, "A_PLUS_PLUS_ROOM_BELOW_3R", evidence
    if structure_15m != direction or structure_1h != direction:
        return False, "A_PLUS_PLUS_15M_1H_NOT_ALIGNED", evidence

    if REQUIRE_2H_ALIGNMENT:
        if structure_2h != direction:
            return False, "A_PLUS_PLUS_2H_NOT_ALIGNED", evidence
        if context_2h_alignment and context_2h_alignment != "ALIGNED":
            return False, "A_PLUS_PLUS_2H_CONTEXT_NOT_ALIGNED", evidence
        if context_2h_1h_alignment and context_2h_1h_alignment != "ALIGNED":
            return False, "A_PLUS_PLUS_2H_1H_NOT_ALIGNED", evidence

    if extension_2h > MAX_2H_EXTENSION_ATR:
        return False, "A_PLUS_PLUS_2H_EXTENDED", evidence
    if REQUIRE_FRESH_MICRO and not fresh_micro:
        return False, "A_PLUS_PLUS_NO_FRESH_MICRO", evidence

    return True, "A_PLUS_PLUS", evidence


def _save_summary() -> Dict[str, Any]:
    payload = {
        "version": VERSION,
        "mode": MODE,
        "thresholds": {
            "min_entry_plan_room_r": MIN_ENTRY_PLAN_ROOM_R,
            "max_2h_extension_atr": MAX_2H_EXTENSION_ATR,
            "require_entry_plan": REQUIRE_ENTRY_PLAN,
            "require_2h_alignment": REQUIRE_2H_ALIGNMENT,
            "require_fresh_micro": REQUIRE_FRESH_MICRO,
        },
        "run_counts": dict(_RUN_COUNTS),
        "accepted": _RUN_ACCEPTED[-20:],
        "note": (
            "A++ convergence observations only. Rejected candidates remain in background tracking. "
            "No stop widening and no exchange orders."
        ),
    }
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".a_plus_plus_convergence.",
            suffix=".tmp",
            delete=False,
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
    original_format = runner._format_trade_message

    def decision_to_signal_convergence(decision):
        signal = original_decision_to_signal(decision)
        if not isinstance(signal, Mapping):
            _RUN_COUNTS["UPSTREAM_REJECTED"] += 1
            return signal
        if not isinstance(decision, Mapping):
            _RUN_COUNTS["INVALID_DECISION"] += 1
            return None

        ok, reason, evidence = evaluate_signal(decision, signal)
        _RUN_COUNTS[reason] += 1
        if not ok:
            print("A++ CONVERGENCE ELENDİ:", decision.get("symbol"), reason, evidence)
            return None

        out = dict(signal)
        out["a_plus_plus_convergence_version"] = VERSION
        out["a_plus_plus_convergence_grade"] = "A++"
        out["a_plus_plus_convergence"] = evidence
        _RUN_COUNTS["ACCEPTED"] += 1
        _RUN_ACCEPTED.append({
            "symbol": out.get("symbol"),
            "direction": out.get("direction"),
            **evidence,
        })
        return out

    def format_converged_trade(signal):
        text = original_format(signal)
        if not isinstance(signal, Mapping) or signal.get("a_plus_plus_convergence_grade") != "A++":
            return text
        marker = "💎 Sınıf: A+ Yüksek Kâr / Düşük SL Riski\n"
        line = "🔗 Birleşik teyit: A++ | 2H + Entry Plan + Taze Mikro\n"
        if marker in text:
            return text.replace(marker, marker + line, 1)
        return text

    runner.decision_to_signal = decision_to_signal_convergence
    runner._format_trade_message = format_converged_trade


def finish() -> Dict[str, Any]:
    return _save_summary()


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "live_real_trade_requires_a_plus_plus": True,
        "min_entry_plan_room_r": MIN_ENTRY_PLAN_ROOM_R,
        "max_2h_extension_atr": MAX_2H_EXTENSION_ATR,
        "require_entry_plan": REQUIRE_ENTRY_PLAN,
        "require_2h_alignment": REQUIRE_2H_ALIGNMENT,
        "require_fresh_micro": REQUIRE_FRESH_MICRO,
        "stop_widening": False,
        "exchange_orders": False,
        "run_counts": dict(_RUN_COUNTS),
    }
