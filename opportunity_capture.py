"""Shared opportunity-capture overrides for signal-only runners.

The live bots remain signal-only: this module never opens exchange orders.
Its narrow purpose is to stop an older opposite-direction signal from hiding a
new reversal opportunity. Same-direction duplication and portfolio capacity
limits remain intact.
"""
from __future__ import annotations

import copy
from typing import Any, Callable, Dict

OPPOSITE_DIRECTION_BLOCK = "SAME_COIN_OPPOSITE_DIRECTION"


def allow_opposite_direction_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Turn the legacy opposite-direction hard block into a visible warning.

    The original portfolio evaluator is still allowed to record its shadow
    decision. We only change the live caller's final decision so a LONG already
    on record cannot suppress a fresh SHORT opportunity (or vice versa).
    """
    if not isinstance(result, dict):
        return result
    if str(result.get("block_code") or "") != OPPOSITE_DIRECTION_BLOCK:
        return result

    updated = copy.deepcopy(result)
    old_reason = str(updated.get("block_reason") or "Ters yön açık sinyal var.")
    warnings = list(updated.get("warnings") or [])
    warnings.append(
        "YÖN DÖNÜŞÜ: aynı coinde ters yön açık olsa da yeni fırsat engellenmedi. "
        + old_reason
    )

    updated["hard_block"] = False
    updated["block_code"] = None
    updated["block_reason"] = None
    updated["warnings"] = warnings
    updated["has_soft_warning"] = True
    updated["opposite_direction_override"] = True
    updated["original_block_code"] = OPPOSITE_DIRECTION_BLOCK
    updated["original_block_reason"] = old_reason
    return updated


def make_opposite_direction_evaluator(
    evaluator: Callable[..., Dict[str, Any]],
) -> Callable[..., Dict[str, Any]]:
    """Wrap a portfolio evaluator without changing its public call shape."""
    if getattr(evaluator, "_opportunity_capture_wrapped", False):
        return evaluator

    def wrapped(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        return allow_opposite_direction_result(evaluator(*args, **kwargs))

    wrapped._opportunity_capture_wrapped = True  # type: ignore[attr-defined]
    return wrapped
