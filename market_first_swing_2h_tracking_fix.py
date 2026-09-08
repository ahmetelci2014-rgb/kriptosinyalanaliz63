"""Entry-aware tracking correction for Market First 2H Swing.

V1 swing preparations could start counting TP/SL before the planned swing entry
zone had actually traded. That makes hypothetical success statistics optimistic
when the alert price is already beyond a target derived from a lower/higher ideal
entry. This patch keeps the 2H layer observational, but makes *new* episodes
entry-aware and rotates active tracking fairly when more than 16 symbols are open.

Historical V1 episodes are deliberately left as legacy data instead of being
retroactively re-labelled with information that was never recorded.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping

import market_first_swing_2h as swing

VERSION = "MARKET_FIRST_SWING_2H_TRACKING_FIX_V2_2026_09_08"
TRACKING_VERSION = "ENTRY_ZONE_GATED_V2"
LEGACY_VERSION = "LEGACY_V1_UNGATED"
_INSTALLED = False

_BASE_REGISTER_PLAN = swing.register_plan
_BASE_UPDATE_SYMBOL_MARKET = swing.update_symbol_market
_BASE_FINALIZE_EXPIRED = swing.finalize_expired
_BASE_ACTIVE_SYMBOLS = swing.active_symbols
_BASE_SUMMARY = swing.summary


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _is_v2(episode: Mapping[str, Any]) -> bool:
    return str(episode.get("entry_tracking_version") or "") == TRACKING_VERSION


def _tracking_entry_reference(plan: Mapping[str, Any]) -> float:
    current = _sf(plan.get("current_price"))
    low = _sf(plan.get("zone_low"))
    high = _sf(plan.get("zone_high"))
    ideal = _sf(plan.get("ideal_entry"))
    if low > high:
        low, high = high, low
    if current > 0 and low > 0 and high > 0 and low <= current <= high:
        return current
    if ideal > 0:
        return ideal
    if low > 0 and high > 0:
        return (low + high) / 2.0
    return current


def _market_range(current_price: float, df5m: Any) -> tuple[float, float]:
    high = current_price
    low = current_price
    try:
        if df5m is not None and len(df5m) > 0:
            row = df5m.iloc[-1]
            high = max(high, _sf(row.get("high"), current_price))
            low = min(low, _sf(row.get("low"), current_price))
    except Exception:
        pass
    return high, low


def _zone_touched(episode: Mapping[str, Any], high: float, low: float) -> bool:
    zone_low = _sf(episode.get("zone_low"))
    zone_high = _sf(episode.get("zone_high"))
    if zone_low <= 0 or zone_high <= 0:
        return False
    if zone_low > zone_high:
        zone_low, zone_high = zone_high, zone_low
    return high >= zone_low and low <= zone_high


def register_plan_v2(
    ledger: Dict[str, Any],
    plan: Mapping[str, Any],
    now: int,
    alerted: bool,
) -> Dict[str, Any]:
    """Register new episodes with entry-zone activation; preserve legacy episodes."""
    symbol = str(plan.get("symbol") or "")
    direction = str(plan.get("direction") or "")
    existing = swing._open_episode(ledger, symbol, direction)
    episode = _BASE_REGISTER_PLAN(ledger, plan, now, alerted)

    if existing is not None:
        if not episode.get("entry_tracking_version"):
            episode["entry_tracking_version"] = LEGACY_VERSION
        return episode

    current = _sf(plan.get("current_price"))
    zone_low = _sf(plan.get("zone_low"))
    zone_high = _sf(plan.get("zone_high"))
    if zone_low > zone_high:
        zone_low, zone_high = zone_high, zone_low
    already_in_zone = (
        current > 0
        and zone_low > 0
        and zone_high > 0
        and zone_low <= current <= zone_high
    )

    episode.update(
        {
            "entry_tracking_version": TRACKING_VERSION,
            "tracking_fix_version": VERSION,
            "tracking_entry_reference": _tracking_entry_reference(plan),
            "entry_activated": bool(already_in_zone),
            "entry_at": int(now) if already_in_zone else 0,
            "entry_activation_reason": "ALERT_ALREADY_IN_ZONE" if already_in_zone else None,
            "entry_bar_targets_ignored": bool(already_in_zone),
        }
    )
    return episode


def _update_v2_episode(
    episode: Dict[str, Any],
    *,
    current_price: float,
    high: float,
    low: float,
    now: int,
) -> None:
    if not bool(episode.get("entry_activated")):
        if _zone_touched(episode, high, low):
            episode["entry_activated"] = True
            episode["entry_at"] = int(now)
            episode["entry_activation_reason"] = "ZONE_TOUCHED"
            # The 5m candle can contain prices both before and after the entry touch.
            # Ignore its targets to avoid inventing an optimistic intrabar sequence.
            episode["entry_bar_targets_ignored"] = True
        episode["updated_at"] = int(now)
        return

    entry_at = int(episode.get("entry_at") or 0)
    if entry_at and now <= entry_at:
        episode["updated_at"] = int(now)
        return

    entry = _sf(episode.get("tracking_entry_reference"))
    direction = str(episode.get("direction") or "").upper()
    if entry <= 0 or direction not in {"LONG", "SHORT"}:
        episode["updated_at"] = int(now)
        return

    if direction == "LONG":
        favorable = max(0.0, (high - entry) / entry * 100.0)
        adverse = max(0.0, (entry - low) / entry * 100.0)
        hits = {
            "tp1": _sf(episode.get("tp1")) > 0 and high >= _sf(episode.get("tp1")),
            "tp2": _sf(episode.get("tp2")) > 0 and high >= _sf(episode.get("tp2")),
            "tp3": _sf(episode.get("tp3")) > 0 and high >= _sf(episode.get("tp3")),
            "sl": _sf(episode.get("sl")) > 0 and low <= _sf(episode.get("sl")),
        }
    else:
        favorable = max(0.0, (entry - low) / entry * 100.0)
        adverse = max(0.0, (high - entry) / entry * 100.0)
        hits = {
            "tp1": _sf(episode.get("tp1")) > 0 and low <= _sf(episode.get("tp1")),
            "tp2": _sf(episode.get("tp2")) > 0 and low <= _sf(episode.get("tp2")),
            "tp3": _sf(episode.get("tp3")) > 0 and low <= _sf(episode.get("tp3")),
            "sl": _sf(episode.get("sl")) > 0 and high >= _sf(episode.get("sl")),
        }

    episode["best_favorable_percent"] = round(
        max(_sf(episode.get("best_favorable_percent")), favorable), 4
    )
    episode["worst_adverse_percent"] = round(
        max(_sf(episode.get("worst_adverse_percent")), adverse), 4
    )

    for name in ("tp1", "tp2", "tp3", "sl"):
        if hits[name] and not int(episode.get(f"{name}_at") or 0):
            episode[f"{name}_at"] = int(now)

    if episode.get("first_decisive_event") is None:
        if hits["tp1"] and hits["sl"]:
            episode["first_decisive_event"] = "AMBIGUOUS_SAME_BAR"
        elif hits["tp1"]:
            episode["first_decisive_event"] = "TP1_FIRST"
        elif hits["sl"]:
            episode["first_decisive_event"] = "SL_FIRST"

    episode["updated_at"] = int(now)


def update_symbol_market_v2(
    ledger: Dict[str, Any],
    symbol: str,
    current_price: float,
    df5m: Any,
    now: int,
) -> int:
    """Update legacy episodes as before, while gating V2 outcomes behind entry."""
    if current_price <= 0:
        return 0

    episodes = ledger.get("episodes") or {}
    legacy = {
        key: episode
        for key, episode in episodes.items()
        if isinstance(episode, dict) and not _is_v2(episode)
    }
    changed = _BASE_UPDATE_SYMBOL_MARKET(
        {"episodes": legacy}, symbol, current_price, df5m, now
    )

    high, low = _market_range(current_price, df5m)
    for episode in episodes.values():
        if (
            not isinstance(episode, dict)
            or not _is_v2(episode)
            or episode.get("resolved")
            or str(episode.get("symbol") or "") != symbol
        ):
            continue
        _update_v2_episode(
            episode,
            current_price=current_price,
            high=high,
            low=low,
            now=now,
        )
        changed += 1
    return changed


def _final_outcome(episode: Mapping[str, Any]) -> str:
    if int(episode.get("tp3_at") or 0):
        return "TP3_REACHED"
    if int(episode.get("tp2_at") or 0):
        return "TP2_REACHED"
    if int(episode.get("tp1_at") or 0):
        return "TP1_REACHED"
    if int(episode.get("sl_at") or 0):
        return "SL_FIRST_NO_TP"
    return "TIMEOUT"


def finalize_expired_v2(ledger: Dict[str, Any], now: int) -> int:
    """Give V2 up to 36h to enter, then 36h of post-entry outcome tracking."""
    episodes = ledger.get("episodes") or {}
    legacy = {
        key: episode
        for key, episode in episodes.items()
        if isinstance(episode, dict) and not _is_v2(episode)
    }
    count = _BASE_FINALIZE_EXPIRED({"episodes": legacy}, now)

    for episode in episodes.values():
        if not isinstance(episode, dict) or not _is_v2(episode) or episode.get("resolved"):
            continue
        first_at = int(episode.get("first_at") or 0)
        if not bool(episode.get("entry_activated")):
            if first_at and now - first_at >= swing.ACTIVE_TRACK_SECONDS:
                episode["outcome"] = "NO_ENTRY_TIMEOUT"
                episode["resolved"] = True
                episode["resolved_at"] = int(now)
                count += 1
            continue

        entry_at = int(episode.get("entry_at") or first_at)
        if entry_at and now - entry_at >= swing.ACTIVE_TRACK_SECONDS:
            episode["outcome"] = _final_outcome(episode)
            episode["resolved"] = True
            episode["resolved_at"] = int(now)
            count += 1
    return count


def _episode_still_trackable(episode: Mapping[str, Any], now: int) -> bool:
    if episode.get("resolved"):
        return False
    first_at = int(episode.get("first_at") or 0)
    if not _is_v2(episode):
        return not first_at or now - first_at <= swing.ACTIVE_TRACK_SECONDS
    if not bool(episode.get("entry_activated")):
        return not first_at or now - first_at <= swing.ACTIVE_TRACK_SECONDS
    entry_at = int(episode.get("entry_at") or first_at)
    return not entry_at or now - entry_at <= swing.ACTIVE_TRACK_SECONDS


def active_symbols_v2(ledger: Mapping[str, Any], now: int) -> list[str]:
    """Fairly rotate tracked symbols instead of permanently pinning oldest 16."""
    best_by_symbol: Dict[str, tuple[int, int, int]] = {}
    for episode in (ledger.get("episodes") or {}).values():
        if not isinstance(episode, Mapping) or not _episode_still_trackable(episode, now):
            continue
        symbol = str(episode.get("symbol") or "")
        if not symbol:
            continue
        # Activated/legacy outcomes need tighter observation than pre-entry waiting.
        waiting_for_entry = _is_v2(episode) and not bool(episode.get("entry_activated"))
        group = 1 if waiting_for_entry else 0
        updated_at = int(episode.get("updated_at") or episode.get("first_at") or 0)
        first_at = int(episode.get("first_at") or 0)
        key = (group, updated_at, first_at)
        previous = best_by_symbol.get(symbol)
        if previous is None or key < previous:
            best_by_symbol[symbol] = key

    ordered = sorted(best_by_symbol.items(), key=lambda item: item[1])
    return [symbol for symbol, _ in ordered[: swing.MAX_ACTIVE_PRIORITY]]


def summary_v2(ledger: Mapping[str, Any], now: int) -> Dict[str, Any]:
    payload = dict(_BASE_SUMMARY(ledger, now))
    episodes = [
        episode
        for episode in (ledger.get("episodes") or {}).values()
        if isinstance(episode, Mapping)
    ]
    tracking = [episode for episode in episodes if not episode.get("resolved")]
    v2 = [episode for episode in episodes if _is_v2(episode)]
    v2_tracking = [episode for episode in v2 if not episode.get("resolved")]

    tp1_first = sum(1 for episode in episodes if episode.get("first_decisive_event") == "TP1_FIRST")
    sl_first = sum(1 for episode in episodes if episode.get("first_decisive_event") == "SL_FIRST")
    ambiguous = sum(
        1 for episode in episodes if episode.get("first_decisive_event") == "AMBIGUOUS_SAME_BAR"
    )
    decided = tp1_first + sl_first + ambiguous

    v2_tp1 = sum(1 for episode in v2 if episode.get("first_decisive_event") == "TP1_FIRST")
    v2_sl = sum(1 for episode in v2 if episode.get("first_decisive_event") == "SL_FIRST")
    v2_ambiguous = sum(
        1 for episode in v2 if episode.get("first_decisive_event") == "AMBIGUOUS_SAME_BAR"
    )

    payload.update(
        {
            "tracking_fix_version": VERSION,
            "tracking_open": len(tracking),
            "tracking_decided": sum(1 for episode in tracking if episode.get("first_decisive_event")),
            "tracking_undecided": sum(1 for episode in tracking if not episode.get("first_decisive_event")),
            "decided_total": decided,
            "ambiguous_same_bar": ambiguous,
            "tp1_first_rate_decided_ex_ambiguous": round(tp1_first / (tp1_first + sl_first), 4)
            if tp1_first + sl_first
            else 0.0,
            "legacy_episode_count": sum(1 for episode in episodes if not _is_v2(episode)),
            "v2_episode_count": len(v2),
            "v2_tracking_open": len(v2_tracking),
            "v2_entry_activated": sum(1 for episode in v2 if episode.get("entry_activated")),
            "v2_waiting_entry": sum(
                1
                for episode in v2_tracking
                if not bool(episode.get("entry_activated"))
            ),
            "v2_no_entry_timeout": sum(
                1 for episode in v2 if episode.get("outcome") == "NO_ENTRY_TIMEOUT"
            ),
            "v2_tp1_first": v2_tp1,
            "v2_sl_first": v2_sl,
            "v2_ambiguous_same_bar": v2_ambiguous,
            "v2_tp1_first_rate_decided_ex_ambiguous": round(v2_tp1 / (v2_tp1 + v2_sl), 4)
            if v2_tp1 + v2_sl
            else 0.0,
            "metric_warning": (
                "Legacy V1 episodes were not entry-zone gated; evaluate the corrected "
                "system primarily from v2_* metrics after enough V2 samples accumulate."
            ),
            "note": (
                "2H swing is hypothetical opportunity tracking only. 'open' means still "
                "under observation and can already have a TP1/SL first-decision; V2 outcomes "
                "start only after the planned swing entry zone is touched."
            ),
        }
    )
    return payload


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    swing.register_plan = register_plan_v2
    swing.update_symbol_market = update_symbol_market_v2
    swing.finalize_expired = finalize_expired_v2
    swing.active_symbols = active_symbols_v2
    swing.summary = summary_v2


def status() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": "SHADOW_TRACKING_ONLY_NO_TRADE_ELIGIBILITY_CHANGE",
        "entry_gate": "2H swing zone must be touched before TP/SL tracking",
        "entry_wait_hours": swing.ACTIVE_TRACK_SECONDS // 3600,
        "post_entry_track_hours": swing.ACTIVE_TRACK_SECONDS // 3600,
        "active_priority": "least-recently-updated rotation; activated first",
        "legacy_policy": "preserved and labelled separately in summary",
    }
