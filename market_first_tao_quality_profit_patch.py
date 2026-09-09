"""Profit Quality compatibility patch for the strict TAO MTF pullback profile.

Profit Quality normally requires current 5M volume >= 0.65x.  TAO showed a
useful continuation pattern where the *recent* volume expansion was very strong
but volume had cooled during the pullback/retest.  This patch only relaxes that
single current-volume check when the decision has already passed the much
stricter TAO bridge evidence:

- current 5M volume >= 0.50x,
- recent 5M peak >= 1.50x,
- recent 15M peak >= 1.20x.

Every other Profit Quality rule (score, risk, >=2% target, >=2.5R, market and
confidence) is re-checked unchanged.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple

import market_first_profit_quality_v1 as profit_quality

VERSION = "MARKET_FIRST_TAO_PROFIT_PATCH_V1_2026_09_09"
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
        ok, reason = _ORIGINAL(decision)
        if ok or reason != "VOLUME_BELOW_0_65" or not tao_volume_exception(decision):
            return ok, reason

        # Re-run the original gate with only the current-volume observation
        # lifted to its ordinary floor. No other field is changed or bypassed.
        replay = dict(decision)
        replay["volume_ratio_5m"] = max(
            _sf(replay.get("volume_ratio_5m")),
            float(profit_quality.MIN_VOLUME_RATIO_5M),
        )
        return _ORIGINAL(replay)

    profit_quality._quality_reason = quality_reason_with_tao


def status() -> dict:
    return {
        "version": VERSION,
        "installed": _INSTALLED,
        "scope": "TAO_BRIDGE_CURRENT_VOLUME_ONLY",
        "min_current_volume_5m": MIN_CURRENT_VOLUME_5M,
        "min_recent_peak_volume_5m": MIN_RECENT_PEAK_VOLUME_5M,
        "min_recent_peak_volume_15m": MIN_RECENT_PEAK_VOLUME_15M,
        "other_profit_quality_rules": "UNCHANGED",
    }
