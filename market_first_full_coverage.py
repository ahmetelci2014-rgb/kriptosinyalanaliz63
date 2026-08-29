"""Rolling full-universe deep coverage for the single Market First system.

The normal selector keeps the strongest/freshest candidates at the front. This
layer preserves that priority set and uses spare per-run capacity to rotate
through every remaining eligible OKX USDT perpetual crypto contract. The goal is
full deep-analysis coverage without trying to make ~1,000+ OHLC API calls in one
5-minute run.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from market_first_strategy import MAJOR_WEIGHTS

MAX_DEEP_SCAN_PER_RUN = 128
PRIORITY_SLOTS = 64
STATE_CURSOR_KEY = "full_deep_coverage_cursor"


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def expand_full_universe_coverage(
    rows: Sequence[Mapping[str, Any]],
    state: Dict[str, Any],
    selected: Sequence[str],
    *,
    max_total: int = MAX_DEEP_SCAN_PER_RUN,
    priority_slots: int = PRIORITY_SLOTS,
) -> Tuple[List[str], Dict[str, Any]]:
    """Preserve priority picks, then rotate through the whole eligible universe.

    BTC/ETH/SOL are excluded from this altcoin rotation because the Market First
    major-market stage already deep-analyzes them on 5m/15m/1h/4h every run.
    Every other eligible symbol is either already in the priority set or becomes
    eligible for the rotating coverage lane.
    """
    universe = sorted(
        {
            str(row.get("symbol") or "").strip()
            for row in rows
            if str(row.get("symbol") or "").strip()
            and str(row.get("symbol") or "").strip() not in MAJOR_WEIGHTS
            and _sf(row.get("price"), 0.0) > 0.0
        }
    )

    if not universe:
        state[STATE_CURSOR_KEY] = 0
        return [], {
            "universe_count": 0,
            "priority_kept": 0,
            "coverage_added": 0,
            "deep_total": 0,
            "cursor_start": 0,
            "cursor_end": 0,
        }

    max_total = max(1, int(max_total))
    priority_slots = max(0, min(int(priority_slots), max_total))
    allowed = set(universe)

    merged: List[str] = []
    seen = set()

    for raw_symbol in selected:
        symbol = str(raw_symbol or "").strip()
        if symbol not in allowed or symbol in seen:
            continue
        merged.append(symbol)
        seen.add(symbol)
        if len(merged) >= priority_slots:
            break

    priority_kept = len(merged)
    cursor_start = int(_sf(state.get(STATE_CURSOR_KEY), 0.0)) % len(universe)
    walked = 0
    coverage_added: List[str] = []

    # Walk at most one full universe loop. Priority symbols encountered in the
    # lane count as covered this run because they are already in `merged`.
    while len(merged) < max_total and walked < len(universe):
        symbol = universe[(cursor_start + walked) % len(universe)]
        walked += 1
        if symbol in seen:
            continue
        seen.add(symbol)
        merged.append(symbol)
        coverage_added.append(symbol)

    cursor_end = (cursor_start + walked) % len(universe)
    state[STATE_CURSOR_KEY] = cursor_end

    summary = {
        "universe_count": len(universe),
        "priority_kept": priority_kept,
        "coverage_added": len(coverage_added),
        "deep_total": len(merged),
        "cursor_start": cursor_start,
        "cursor_end": cursor_end,
        "max_per_run": max_total,
    }
    return merged, summary
