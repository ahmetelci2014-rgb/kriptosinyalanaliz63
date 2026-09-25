"""TAO Profit Quality compatibility layer.

Historical Market First evidence moved the ordinary Profit Quality 5M volume
floor from 0.65x to 0.50x. The old TAO-only volume exception is therefore no
longer needed: a valid TAO pullback at >=0.50x passes the same ordinary volume
rule as every other setup.

This module remains installed as a compatibility layer so older call sites do
not break. It never bypasses the current Profit Quality gate.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple

import market_first_profit_quality_v1 as profit_quality

VERSION = "MARKET_FIRST_TAO_PROFIT_PATCH_V2_HISTORICAL_2026_09_25"
MIN_CURRENT_VOLUME_5M = 0.50
MIN_RECENT_PEAK_VOLUME_5M = 1.50
MIN_RECENT_PEAK_VOLUME_15M = 1.20

_INSTALLED = False
_ORIGINAL = None


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def tao_volume_exception(decision: Mapping[str, Any]) -> bool:
    return bool(decision.get("tao_quality_bridge")) and (
        _sf(decision.get("tao_current_volume_5m")) >= MIN_CURRENT_VOLUME_5M
        and _sf(decision.get("tao_recent_peak_volume_5m")) >= MIN_RECENT_PEAK_VOLUME_5M
        and _sf(decision.get("tao_recent_peak_volume_15m")) >= MIN_RECENT_PEAK_VOLUME_15M
    )


def install() -> None:
    global _INSTALLED, _ORIGINAL
    if _INSTALLED:
        return
    _INSTALLED = True
    _ORIGINAL = profit_quality._quality_reason

    def quality_reason_with_tao(decision: Mapping[str, Any]) -> Tuple[bool, str]:
        # The ordinary historical floor is now the same 0.50x floor TAO used.
        # Keep this wrapper transparent: no TAO setup may bypass current rules.
        return _ORIGINAL(decision)

    profit_quality._quality_reason = quality_reason_with_tao


def status() -> dict:
    return {
        "version": VERSION,
        "installed": _INSTALLED,
        "scope": "COMPATIBILITY_ONLY_NO_BYPASS",
        "min_current_volume_5m": MIN_CURRENT_VOLUME_5M,
        "min_recent_peak_volume_5m": MIN_RECENT_PEAK_VOLUME_5M,
        "min_recent_peak_volume_15m": MIN_RECENT_PEAK_VOLUME_15M,
        "other_profit_quality_rules": "UNCHANGED_AND_AUTHORITATIVE",
    }
