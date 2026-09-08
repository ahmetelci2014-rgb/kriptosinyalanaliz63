"""Expose hidden Market First entry-plan promotion blockers.

The entry-plan overlay intentionally refuses promotion when the raw micro
direction points opposite to a valid higher-timeframe ENTRY plan. Historically
that branch returned the underlying strategy's generic ``OK`` reason, so the
opportunity ledger recorded many cases as UNKNOWN.

This patch does not relax or add any trade eligibility. It only replaces that
generic reason with ``ENTRY_PLAN_DIRECTION_CONFLICT`` before the opportunity
tracking wrapper records the episode.
"""
from __future__ import annotations

from typing import Any, Mapping

import market_first_entry_plan as entry_plan
import market_first_live_direction_guard as direction_guard
import market_first_live_entry_plan as live_entry_plan
import market_first_runner as runner

VERSION = "MARKET_FIRST_PROMOTION_REASON_PATCH_V1_2026_09_08"
_INSTALLED = False


def _arg(args, kwargs, name: str, index: int, default=None):
    if name in kwargs:
        return kwargs.get(name)
    return args[index] if len(args) > index else default


def classify_reason(
    plan: Mapping[str, Any] | None,
    decision: Mapping[str, Any] | None,
    fallback_reason: Any,
) -> str:
    fallback = str(fallback_reason or "")
    if not isinstance(plan, Mapping) or str(plan.get("status") or "").upper() != "ENTRY":
        return fallback
    if not isinstance(decision, Mapping):
        return fallback

    planned = str(plan.get("direction") or "").upper()
    current = str(decision.get("direction") or "").upper()
    if planned in {"LONG", "SHORT"} and current in {"LONG", "SHORT"} and planned != current:
        return "ENTRY_PLAN_DIRECTION_CONFLICT"
    return fallback


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Ensure the actual entry-plan wrapper is installed first. Entry Accelerator
    # constants have already been applied by market_first_live_simple.
    live_entry_plan.install_entry_plan()
    original_analyze = runner.analyze_candidate

    def analyze_with_explicit_promotion_reason(*args, **kwargs):
        decision, reason = original_analyze(*args, **kwargs)
        # Existing explicit reasons are already useful. The hidden conflict was
        # primarily emitted as generic OK/empty from the underlying micro path.
        if str(reason or "").upper() not in {"", "OK"} or not isinstance(decision, Mapping):
            return decision, reason

        symbol = str(_arg(args, kwargs, "symbol", 0, "") or "")
        current_price = float(_arg(args, kwargs, "current_price", 5, 0.0) or 0.0)
        quote_volume = float(_arg(args, kwargs, "quote_volume_24h", 6, 0.0) or 0.0)
        raw_context = _arg(args, kwargs, "context", 7)
        context, _ = direction_guard.neutralize_breadth_conflict(raw_context)
        if not symbol or current_price <= 0 or context is None:
            return decision, reason

        plan, _ = entry_plan.evaluate_entry_plan(
            symbol=symbol,
            df5m=_arg(args, kwargs, "df5m", 2),
            df15m=_arg(args, kwargs, "df15m", 3),
            df1h=_arg(args, kwargs, "df1h", 4),
            current_price=current_price,
            quote_volume_24h=quote_volume,
            context=context,
        )
        explicit = classify_reason(plan, decision, reason)
        if explicit != str(reason or ""):
            print(
                "ENTRY PROMOTION BLOCKER:", symbol,
                "| plan=", (plan or {}).get("direction") if isinstance(plan, Mapping) else None,
                "| micro=", decision.get("direction"),
                "| reason=", explicit,
            )
        return decision, explicit

    runner.analyze_candidate = analyze_with_explicit_promotion_reason


def summary() -> dict[str, Any]:
    return {
        "version": VERSION,
        "behavior_changed": False,
        "explicit_reason": "ENTRY_PLAN_DIRECTION_CONFLICT",
    }
