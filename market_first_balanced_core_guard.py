"""Final live-send invariants for Market First V6 Balanced Core.

This module protects the real Telegram trade lane from wrapper-order bypasses:

1) The immediate fast-entry path can call ``runner._send_trade`` from inside the
   inner Market First live wrapper before outer Profit Quality / Final Execution
   / High Profit-Low SL wrappers return.
2) Every real send must carry the High Profit / Low SL A+ certification.
3) A++ / 2H convergence remains available as observation evidence, but is no
   longer a mandatory live-send certification. This restores a more balanced
   Market First path without removing the proven A+ quality/execution gates.
4) Trades sitting on the strategy's minimum stop floor are especially sensitive
   to wick/noise. When the actual stop is <= 0.45%, require a fresh micro trigger.

The guard does not widen stops, create signals, place exchange orders or suppress
analysis/ledger tracking. Rejected fast-path candidates can continue through the
ordinary outer pipeline and become valid only after the required certifications
exist.
"""
from __future__ import annotations

from collections import Counter
import math
from typing import Any, Dict, Mapping, Tuple

import market_first_runner as runner

VERSION = "MARKET_FIRST_V6_BALANCED_CORE_GUARD_V4_A_PLUS_2026_09_29"
MIN_STOP_FLOOR_MAX_PERCENT = 0.45

_INSTALLED = False
_RUN_COUNTS: Counter = Counter()


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _risk_percent(signal: Mapping[str, Any]) -> float:
    explicit = _sf(signal.get("risk_percent"))
    if explicit > 0:
        return explicit
    entry = _sf(signal.get("entry"))
    sl = _sf(signal.get("sl"))
    if entry <= 0 or sl <= 0:
        return 0.0
    return abs(entry - sl) / entry * 100.0


def _fresh_micro(signal: Mapping[str, Any]) -> bool:
    final_gate = signal.get("final_execution_gate")
    if isinstance(final_gate, Mapping) and "fresh_micro" in final_gate:
        return bool(final_gate.get("fresh_micro"))

    flags = signal.get("direction_engine_confirmation_flags")
    if isinstance(flags, Mapping):
        return bool(flags.get("fresh_micro"))
    return False


def evaluate_signal(signal: Mapping[str, Any] | None) -> Tuple[bool, str, Dict[str, Any]]:
    if not isinstance(signal, Mapping):
        return False, "INVALID_SIGNAL", {}

    fast = bool(signal.get("fast_entry"))
    profit_certified = bool(signal.get("profit_quality_version"))
    execution_certified = bool(signal.get("final_execution_gate_version"))
    high_profit_certified = bool(signal.get("high_profit_low_sl_version"))
    high_profit_grade = str(signal.get("high_profit_low_sl_grade") or "").upper()
    convergence_certified = bool(signal.get("a_plus_plus_convergence_version"))
    convergence_grade = str(signal.get("a_plus_plus_convergence_grade") or "").upper()
    risk_percent = _risk_percent(signal)
    fresh_micro = _fresh_micro(signal)

    evidence = {
        "fast_entry": fast,
        "profit_quality_certified": profit_certified,
        "final_execution_certified": execution_certified,
        "high_profit_low_sl_certified": high_profit_certified,
        "high_profit_low_sl_grade": high_profit_grade,
        "a_plus_plus_convergence_certified": convergence_certified,
        "a_plus_plus_convergence_grade": convergence_grade,
        "a_plus_plus_live_required": False,
        "risk_percent": round(risk_percent, 4),
        "fresh_micro": fresh_micro,
    }

    if fast and not profit_certified:
        return False, "FAST_BEFORE_PROFIT_QUALITY", evidence
    if fast and not execution_certified:
        return False, "FAST_BEFORE_FINAL_EXECUTION", evidence

    # Tiny stops remain especially vulnerable to ordinary wick/noise.
    if 0 < risk_percent <= MIN_STOP_FLOOR_MAX_PERCENT and not fresh_micro:
        return False, "MIN_STOP_WITHOUT_FRESH_MICRO", evidence

    # Keep the proven A+ quality gate as the final live-quality invariant.
    if not high_profit_certified or high_profit_grade != "A+":
        return False, "HIGH_PROFIT_LOW_SL_NOT_CERTIFIED", evidence

    # A++ is deliberately observation-only in this balanced version. Its fields
    # stay in evidence so we can compare A+ trades with and without A++ alignment.
    return True, "OK", evidence


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_send_trade = runner._send_trade

    def balanced_core_send_trade(exchange, signal, ml_store):
        ok, reason, evidence = evaluate_signal(signal)
        _RUN_COUNTS[reason] += 1
        if not ok:
            print(
                "V6 CORE GUARD ELENDİ:",
                (signal or {}).get("symbol") if isinstance(signal, Mapping) else None,
                (signal or {}).get("direction") if isinstance(signal, Mapping) else None,
                "|", reason,
                "|", evidence,
            )
            return False

        if isinstance(signal, dict):
            signal["balanced_core_guard_version"] = VERSION
            signal["balanced_core_guard"] = evidence
        return original_send_trade(exchange, signal, ml_store)

    runner._send_trade = balanced_core_send_trade


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "fast_entry_requires_profit_quality": True,
        "fast_entry_requires_final_execution": True,
        "all_real_trades_require_high_profit_low_sl_a_plus": True,
        "all_real_trades_require_a_plus_plus_convergence": False,
        "a_plus_plus_mode": "OBSERVATION_ONLY",
        "min_stop_floor_max_percent": MIN_STOP_FLOOR_MAX_PERCENT,
        "min_stop_requires_fresh_micro": True,
        "stop_widening": False,
        "exchange_orders": False,
        "run_counts": dict(_RUN_COUNTS),
    }
