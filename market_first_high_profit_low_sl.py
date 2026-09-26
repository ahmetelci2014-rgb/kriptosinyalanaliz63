"""A+ high-profit / low-SL live admission layer for Market First V6.

This layer runs after Profit Quality and Final Execution. It does not create
signals, widen stops or place exchange orders. Its purpose is to make the real
Telegram trade lane more selective:

- meaningful structural profit room,
- strong reward/risk efficiency,
- fresh micro confirmation at execution time,
- no opposite 2H structure,
- no visibly extended 5M entry.

Candidates rejected here remain available to the existing background/internal
tracking layers; only the real-trade promotion is blocked.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Tuple

import market_first_runner as runner

VERSION = "MARKET_FIRST_HIGH_PROFIT_LOW_SL_V1_2026_09_26"
MODE = "A_PLUS_HIGH_PROFIT_LOW_SL_LIVE_ONLY"
STATE_FILE = "market_first_high_profit_low_sl.json"

MIN_SCORE = 90
MIN_TARGET_PERCENT = 2.50
MIN_TARGET_R = 3.00
MAX_EXTENSION_ATR_5M = 1.00
MAX_RISK_PERCENT = 1.00
REQUIRE_FRESH_MICRO = True
BLOCK_OPPOSITE_2H = True

_INSTALLED = False
_RUN_COUNTS: Counter = Counter()
_RUN_ACCEPTED: list[Dict[str, Any]] = []


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _risk_percent(signal: Mapping[str, Any], decision: Mapping[str, Any]) -> float:
    explicit = _sf(signal.get("risk_percent")) or _sf(decision.get("risk_percent"))
    if explicit > 0:
        return explicit
    entry = _sf(signal.get("entry")) or _sf(decision.get("current_price"))
    sl = _sf(signal.get("sl")) or _sf(decision.get("sl"))
    if min(entry, sl) <= 0:
        return 0.0
    return abs(entry - sl) / entry * 100.0


def _fresh_micro(signal: Mapping[str, Any], decision: Mapping[str, Any]) -> bool:
    final_gate = signal.get("final_execution_gate")
    if isinstance(final_gate, Mapping) and "fresh_micro" in final_gate:
        return bool(final_gate.get("fresh_micro"))

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

    profit_certified = bool(signal.get("profit_quality_version"))
    execution_certified = bool(signal.get("final_execution_gate_version"))
    if not profit_certified:
        return False, "PROFIT_QUALITY_NOT_CERTIFIED", {}
    if not execution_certified:
        return False, "FINAL_EXECUTION_NOT_CERTIFIED", {}

    score = int(_sf(signal.get("score")) or _sf(decision.get("score")))
    target_percent = _sf(signal.get("profit_target_percent")) or _sf(decision.get("profit_target_percent"))
    target_r = (
        _sf(signal.get("rr_tp3"))
        or _sf(signal.get("profit_target_r"))
        or _sf(decision.get("profit_target_r"))
    )
    risk_percent = _risk_percent(signal, decision)
    extension_atr_5m = _sf(decision.get("extension_atr_5m"), _sf(signal.get("extension_atr_5m")))
    fresh_micro = _fresh_micro(signal, decision)
    structure_2h = str(
        decision.get("profit_structure_2h")
        or signal.get("profit_structure_2h")
        or "NEUTRAL"
    ).upper()
    opposite = "SHORT" if direction == "LONG" else "LONG"

    evidence = {
        "score": score,
        "target_percent": round(target_percent, 4),
        "target_r": round(target_r, 3),
        "risk_percent": round(risk_percent, 4),
        "extension_atr_5m": round(extension_atr_5m, 4),
        "fresh_micro": fresh_micro,
        "structure_2h": structure_2h,
        "direction": direction,
    }

    if score < MIN_SCORE:
        return False, "A_PLUS_SCORE_BELOW_90", evidence
    if target_percent < MIN_TARGET_PERCENT:
        return False, "A_PLUS_TARGET_BELOW_2_5P", evidence
    if target_r < MIN_TARGET_R:
        return False, "A_PLUS_TARGET_R_BELOW_3", evidence
    if risk_percent <= 0 or risk_percent > MAX_RISK_PERCENT:
        return False, "A_PLUS_RISK_ABOVE_1P", evidence
    if extension_atr_5m > MAX_EXTENSION_ATR_5M:
        return False, "A_PLUS_ENTRY_EXTENDED", evidence
    if BLOCK_OPPOSITE_2H and structure_2h == opposite:
        return False, "A_PLUS_2H_OPPOSITE", evidence
    if REQUIRE_FRESH_MICRO and not fresh_micro:
        return False, "A_PLUS_NO_FRESH_MICRO", evidence

    return True, "A_PLUS", evidence


def _save_summary() -> Dict[str, Any]:
    payload = {
        "version": VERSION,
        "mode": MODE,
        "thresholds": {
            "min_score": MIN_SCORE,
            "min_target_percent": MIN_TARGET_PERCENT,
            "min_target_r": MIN_TARGET_R,
            "max_extension_atr_5m": MAX_EXTENSION_ATR_5M,
            "max_risk_percent": MAX_RISK_PERCENT,
            "require_fresh_micro": REQUIRE_FRESH_MICRO,
            "block_opposite_2h": BLOCK_OPPOSITE_2H,
        },
        "run_counts": dict(_RUN_COUNTS),
        "accepted": _RUN_ACCEPTED[-20:],
        "note": (
            "Admission observations only, not realised PnL. This layer never widens stops "
            "and never places exchange orders."
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
            prefix=".high_profit_low_sl.",
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

    def decision_to_signal_a_plus(decision):
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
            print("HIGH PROFIT / LOW SL ELENDİ:", decision.get("symbol"), reason, evidence)
            return None

        out = dict(signal)
        out["high_profit_low_sl_version"] = VERSION
        out["high_profit_low_sl_grade"] = "A+"
        out["high_profit_low_sl"] = evidence
        _RUN_COUNTS["ACCEPTED"] += 1
        _RUN_ACCEPTED.append({
            "symbol": out.get("symbol"),
            "direction": out.get("direction"),
            **evidence,
        })
        return out

    def format_a_plus_trade(signal):
        text = original_format(signal)
        if not isinstance(signal, Mapping) or signal.get("high_profit_low_sl_grade") != "A+":
            return text
        marker = "⭐ Kalite skoru:"
        line = "💎 Sınıf: A+ Yüksek Kâr / Düşük SL Riski\n"
        if marker in text:
            return text.replace(marker, line + marker, 1)
        return text

    runner.decision_to_signal = decision_to_signal_a_plus
    runner._format_trade_message = format_a_plus_trade


def finish() -> Dict[str, Any]:
    return _save_summary()


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "live_real_trade_requires_a_plus": True,
        "min_score": MIN_SCORE,
        "min_target_percent": MIN_TARGET_PERCENT,
        "min_target_r": MIN_TARGET_R,
        "max_extension_atr_5m": MAX_EXTENSION_ATR_5M,
        "require_fresh_micro": REQUIRE_FRESH_MICRO,
        "block_opposite_2h": BLOCK_OPPOSITE_2H,
        "stop_widening": False,
        "exchange_orders": False,
        "run_counts": dict(_RUN_COUNTS),
    }
