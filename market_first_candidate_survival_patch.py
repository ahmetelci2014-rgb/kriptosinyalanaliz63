"""Keep observational candidate visibility subordinate to capital survival.

Candidate Visibility is useful for showing strong opportunities that fail a final
trade gate, but it must never encourage manual trading while the capital-survival
system is deliberately protecting the account.

Rules:
- HALT: show no strong-candidate alerts.
- RECOVERY_STRICT: a candidate may be visible only if it independently satisfies
  the same A++ recovery requirements used for a real trade.
- NORMAL: preserve the existing Candidate Visibility thresholds.
"""
from __future__ import annotations

from typing import Any, Mapping, Tuple

import market_first_candidate_visibility as visibility
import market_first_profit_survival_gate as survival

VERSION = "MARKET_FIRST_CANDIDATE_SURVIVAL_PATCH_V1_2026_09_14"
_INSTALLED = False


def survival_allows_candidate(
    decision: Mapping[str, Any] | None,
    health: Mapping[str, Any] | None = None,
) -> Tuple[bool, str]:
    if not isinstance(decision, Mapping):
        return False, "NO_DECISION"
    health = health if isinstance(health, Mapping) else survival._current_health()
    mode = str(health.get("mode") or "NORMAL").upper()

    if mode == "HALT":
        return False, f"SURVIVAL_HALT:{health.get('reason') or 'HALT'}"

    if mode == "RECOVERY_STRICT":
        ok, reason, _ = survival._recovery_reason(decision)
        if not ok:
            return False, f"SURVIVAL_RECOVERY:{reason}"

    return True, "OK"


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original = visibility.candidate_is_visible

    def candidate_is_visible_with_survival(decision):
        allowed, reason = survival_allows_candidate(decision)
        if not allowed:
            return False, reason
        return original(decision)

    visibility.candidate_is_visible = candidate_is_visible_with_survival


def summary():
    health = survival._current_health()
    return {
        "version": VERSION,
        "mode": health.get("mode"),
        "reason": health.get("reason"),
        "halt_suppresses_candidates": True,
        "recovery_requires_a_plus_plus": True,
    }
