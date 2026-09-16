"""Preserve ENTRY_PLAN timeframe context after PREP/ENTRY promotion.

The plan already calculates 5M and 15M volume ratios. Older promotion code kept
only a compatibility ``volume_ratio_1m`` field sourced from the 5M ratio, which
made later quality layers unable to verify the original 15M participation.

This patch is data propagation only. It does not change any threshold, promote a
new trade, send Telegram, place orders or widen stops.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping

import market_first_entry_plan as entry_plan

VERSION = "MARKET_FIRST_ENTRY_PLAN_CONTEXT_PATCH_V1_2026_09_16"
_INSTALLED = False
_ORIGINAL_PROMOTE = None


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def decorate_promoted(out: Mapping[str, Any], plan: Mapping[str, Any]) -> Dict[str, Any]:
    promoted = dict(out)
    promoted["volume_ratio_5m"] = _sf(plan.get("volume_ratio_5m"))
    promoted["volume_ratio_15m"] = _sf(plan.get("volume_ratio_15m"))
    promoted["entry_plan_context_patch_version"] = VERSION
    return promoted


def install() -> None:
    global _INSTALLED, _ORIGINAL_PROMOTE
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL_PROMOTE = entry_plan.promote_to_decision

    def promote_with_context(existing, plan):
        out = _ORIGINAL_PROMOTE(existing, plan)
        return decorate_promoted(out, plan)

    entry_plan.promote_to_decision = promote_with_context


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "data_propagation_only": True,
        "fields": ["volume_ratio_5m", "volume_ratio_15m"],
        "strategy_mutation": False,
        "exchange_orders": False,
        "stop_widening": False,
    }
