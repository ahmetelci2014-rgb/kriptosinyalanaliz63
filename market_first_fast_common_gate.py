"""Final wrapper that prevents fast-entry candidates from bypassing common gates.

The original Market First fast path can try to send a ``fast_entry`` from inside
an inner ``decision_to_signal`` wrapper so the Telegram alert is emitted during
the candidate scan.  Later-installed Profit Quality / Final Execution / Profit
Survival wrappers therefore do not get a chance to reject that first send.

This module is intentionally installed *after* every decision gate.  It does two
small things:

1. Any premature ``_send_trade`` call for a fast entry is blocked unless the
   signal carries the approval marker added by this outermost decision wrapper.
2. Once the complete common ``decision_to_signal`` chain returns a surviving
   fast signal, the approval marker is added.  The runner's ordinary ranked
   end-of-scan send can then deliver it through the normal pre-send guards.

It creates no trades, relaxes no threshold, places no exchange order, and does
not change non-fast signals.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Mapping

import market_first_runner as runner

VERSION = "MARKET_FIRST_FAST_COMMON_GATE_V1_2026_09_16"
APPROVAL_FIELD = "fast_entry_common_gates_approved"
_INSTALLED = False
_RUN_COUNTS: Counter = Counter()


def _is_fast(signal: Any) -> bool:
    return isinstance(signal, Mapping) and bool(signal.get("fast_entry"))


def install() -> None:
    """Install last so a fast signal cannot send before the full gate chain."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_decision_to_signal = runner.decision_to_signal
    original_send_trade = runner._send_trade

    def decision_to_signal_after_common_gates(decision):
        signal = original_decision_to_signal(decision)
        if not _is_fast(signal):
            return signal

        # Reaching this point proves every previously installed common decision
        # gate accepted the fast candidate.  Mark a copy so the normal runner
        # queue may send it later through the ordinary pre-send guards.
        approved = dict(signal)
        approved[APPROVAL_FIELD] = True
        approved["fast_entry_common_gate_version"] = VERSION
        _RUN_COUNTS["FAST_COMMON_GATES_APPROVED"] += 1
        return approved

    def send_trade_after_common_gates(exchange, signal: Dict[str, Any], ml_store: Dict[str, Any]) -> bool:
        if _is_fast(signal) and not bool(signal.get(APPROVAL_FIELD)):
            # This is the old same-candidate immediate-send attempt.  Returning
            # False makes the inner fast wrapper hand the signal back upward,
            # where Profit Quality / Final Execution / Profit Survival can run.
            _RUN_COUNTS["FAST_PREMATURE_SEND_BLOCKED"] += 1
            print(
                "FAST COMMON GATE | erken direkt gönderim engellendi:",
                signal.get("symbol"),
                signal.get("direction"),
                "| ortak final kapıları beklenecek",
            )
            return False

        if _is_fast(signal):
            _RUN_COUNTS["FAST_APPROVED_SEND"] += 1
        return bool(original_send_trade(exchange, signal, ml_store))

    runner.decision_to_signal = decision_to_signal_after_common_gates
    runner._send_trade = send_trade_after_common_gates


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "approval_field": APPROVAL_FIELD,
        "fast_immediate_send": False,
        "fast_requires_common_decision_gates": True,
        "ordinary_ranked_send_after_approval": True,
        "exchange_orders": False,
        "run_counts": dict(_RUN_COUNTS),
    }
