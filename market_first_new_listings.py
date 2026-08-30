"""New-listing priority lane for the single Market First V5 system.

This module does not create a second strategy and does not relax entry rules.
It only reserves a few deep-scan slots for newly appeared OKX USDT perpetual
crypto contracts so a fresh listing is not missed before normal ranking has
enough history to notice it.
"""
from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Mapping, Sequence, Tuple

NEW_LISTING_WATCH_SECONDS = 24 * 60 * 60
NEW_LISTING_TRACKER_RETENTION_SECONDS = 3 * 24 * 60 * 60
MAX_NEW_LISTING_SLOTS = 8
MIN_NEW_LISTING_QUOTE_VOLUME = 75_000.0
STATE_TRACKER_KEY = "new_listing_first_seen"
STATE_BOOTSTRAP_KEY = "new_listing_tracker_bootstrapped_at"


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def prioritize_new_listings(
    rows: Sequence[Mapping[str, Any]],
    state: Dict[str, Any],
    selected: Sequence[str],
    *,
    max_total: int = 64,
    now: int | None = None,
) -> Tuple[List[str], Dict[str, Any]]:
    """Put recently discovered contracts at the front of deep-scan selection.

    Discovery is based on the persisted previous-price universe. On a brand-new
    state file we bootstrap silently instead of treating every existing market as
    a new listing. A newly discovered contract then receives priority for 24h,
    subject to a small minimum quote-volume gate. Signal/risk rules remain
    unchanged after selection.
    """
    ts = int(now if now is not None else time.time())
    previous_prices = state.get("previous_prices")
    if not isinstance(previous_prices, Mapping):
        previous_prices = {}

    tracker = state.setdefault(STATE_TRACKER_KEY, {})
    if not isinstance(tracker, dict):
        tracker = {}
        state[STATE_TRACKER_KEY] = tracker

    current_symbols = {
        str(row.get("symbol") or "").strip()
        for row in rows
        if str(row.get("symbol") or "").strip()
    }

    bootstrapped = bool(state.get(STATE_BOOTSTRAP_KEY))
    discovered_now: List[str] = []

    if not bootstrapped:
        state[STATE_BOOTSTRAP_KEY] = ts
        # Existing persisted prices let us safely identify contracts that truly
        # appeared since the previous run. With an empty state, establish a
        # baseline and wait for the next run rather than flagging every market.
        if previous_prices:
            for symbol in sorted(current_symbols):
                if symbol not in previous_prices:
                    tracker[symbol] = ts
                    discovered_now.append(symbol)
    else:
        for symbol in sorted(current_symbols):
            if symbol not in previous_prices and symbol not in tracker:
                tracker[symbol] = ts
                discovered_now.append(symbol)

    # Keep the state compact. Removed/old markets do not need permanent history.
    for symbol, first_seen in list(tracker.items()):
        age = ts - int(_sf(first_seen))
        if age > NEW_LISTING_TRACKER_RETENTION_SECONDS:
            tracker.pop(symbol, None)

    row_by_symbol = {
        str(row.get("symbol") or "").strip(): row
        for row in rows
        if str(row.get("symbol") or "").strip()
    }

    candidates = []
    for symbol, first_seen in tracker.items():
        row = row_by_symbol.get(symbol)
        if not row:
            continue
        age = max(0, ts - int(_sf(first_seen)))
        if age > NEW_LISTING_WATCH_SECONDS:
            continue
        quote_volume = _sf(row.get("quote_volume"))
        if quote_volume < MIN_NEW_LISTING_QUOTE_VOLUME:
            continue
        # Newest first; among equally new symbols prefer the more liquid one.
        candidates.append((int(first_seen), quote_volume, symbol))

    priority_symbols = [
        symbol
        for _, _, symbol in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)
    ][:MAX_NEW_LISTING_SLOTS]

    merged: List[str] = []
    seen = set()
    for symbol in [*priority_symbols, *selected]:
        symbol = str(symbol or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        merged.append(symbol)
        if len(merged) >= max(1, int(max_total)):
            break

    summary = {
        "discovered_now": discovered_now,
        "active_priority": priority_symbols,
        "priority_count": len(priority_symbols),
        "tracked_count": len(tracker),
        "min_quote_volume": MIN_NEW_LISTING_QUOTE_VOLUME,
        "watch_hours": NEW_LISTING_WATCH_SECONDS // 3600,
    }
    return merged, summary


# Imported for its live-main-only EARLY alert bookkeeping hook. It does not
# change selection or entry rules and remains inert on PR/test branches.
import market_first_early_ledger_hooks as _early_ledger_hooks  # noqa: E402,F401
