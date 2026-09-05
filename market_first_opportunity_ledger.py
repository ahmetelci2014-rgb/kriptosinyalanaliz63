"""Unified shadow ledger for Market First pre-trade opportunities.

The ledger answers two different questions without mixing them:
1) Was the directional idea useful after the first preparation alert?
2) Did the system actually produce a tradable ENTRY / Telegram trade signal?

Preparation performance is hypothetical. It must never be reported as realised P&L.
Existing MARKET_FIRST_EARLY episodes remain in their own ledger and are summarised
alongside this file so EARLY and ENTRY_PLAN quality can be compared independently.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple

VERSION = "MARKET_FIRST_OPPORTUNITY_LEDGER_V1_2026_09_05"
LEDGER_FILE = "market_first_entry_plan_ledger.json"
SUMMARY_FILE = "market_first_opportunity_summary.json"
EARLY_LEDGER_FILE = "market_first_early_ledger.json"
MAX_EPISODES = 2000
ENTRY_WINDOW_SECONDS = 3 * 60 * 60
TRACK_WINDOW_SECONDS = 6 * 60 * 60


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _directional_percent(direction: str, start: float, end: float) -> float:
    if min(start, end) <= 0:
        return 0.0
    raw = (end / start - 1.0) * 100.0
    return raw if str(direction).upper() == "LONG" else -raw


def empty_ledger() -> Dict[str, Any]:
    return {"version": VERSION, "episodes": {}, "updated_at": 0}


def load(bot: Any) -> Dict[str, Any]:
    data = bot.load_json_file(LEDGER_FILE, empty_ledger())
    if not isinstance(data, dict):
        data = empty_ledger()
    data.setdefault("version", VERSION)
    data.setdefault("episodes", {})
    if not isinstance(data.get("episodes"), dict):
        data["episodes"] = {}
    return data


def _prune(ledger: Dict[str, Any]) -> None:
    episodes = ledger.setdefault("episodes", {})
    if len(episodes) <= MAX_EPISODES:
        return
    ordered = sorted(
        episodes.items(),
        key=lambda pair: int((pair[1] or {}).get("first_at") or 0),
        reverse=True,
    )
    ledger["episodes"] = dict(ordered[:MAX_EPISODES])


def save(bot: Any, ledger: Dict[str, Any], now: int) -> None:
    ledger["version"] = VERSION
    ledger["updated_at"] = int(now)
    _prune(ledger)
    bot.save_json_file(LEDGER_FILE, ledger)


def episode_id(symbol: str, direction: str, first_at: int) -> str:
    return f"{str(symbol).upper()}:{str(direction).upper()}:{int(first_at)}"


def _open_episode(
    ledger: Mapping[str, Any],
    symbol: str,
    direction: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    episodes = ledger.get("episodes", {}) if isinstance(ledger, Mapping) else {}
    if not isinstance(episodes, Mapping):
        return None
    wanted_symbol = str(symbol).upper().strip()
    wanted_direction = str(direction or "").upper().strip()
    candidates = []
    for item in episodes.values():
        if not isinstance(item, dict) or bool(item.get("resolved")):
            continue
        if str(item.get("symbol") or "").upper() != wanted_symbol:
            continue
        if wanted_direction and str(item.get("direction") or "").upper() != wanted_direction:
            continue
        candidates.append(item)
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.get("first_at") or 0))


def active_symbols(ledger: Mapping[str, Any], now: int) -> list[str]:
    episodes = ledger.get("episodes", {}) if isinstance(ledger, Mapping) else {}
    result = []
    seen = set()
    if not isinstance(episodes, Mapping):
        return result
    ordered = sorted(
        (item for item in episodes.values() if isinstance(item, Mapping)),
        key=lambda item: int(item.get("first_at") or 0),
        reverse=True,
    )
    for item in ordered:
        if bool(item.get("resolved")):
            continue
        first_at = int(item.get("first_at") or 0)
        if first_at and now - first_at > TRACK_WINDOW_SECONDS:
            continue
        symbol = str(item.get("symbol") or "").strip()
        if symbol and symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result


def prioritize_tracking_symbols(
    selected: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    ledger: Mapping[str, Any],
    plan_state: Mapping[str, Any],
    *,
    now: int,
    max_total: int,
) -> list[str]:
    """Keep active preparation episodes in every deep scan until tracking ends."""
    available = {str(row.get("symbol") or "") for row in rows if isinstance(row, Mapping)}
    priority = active_symbols(ledger, now)

    plans = plan_state.get("plans", {}) if isinstance(plan_state, Mapping) else {}
    if isinstance(plans, Mapping):
        legacy = sorted(
            (item for item in plans.items() if isinstance(item[1], Mapping)),
            key=lambda pair: int(pair[1].get("updated_at") or pair[1].get("last_prep_at") or 0),
            reverse=True,
        )
        for key, value in legacy:
            status = str(value.get("status") or "").upper()
            updated = int(value.get("updated_at") or value.get("last_prep_at") or 0)
            if status not in {"PREP", "ENTRY_SENT"} or (updated and now - updated > TRACK_WINDOW_SECONDS):
                continue
            symbol = str(key).split(":", 1)[0]
            if symbol and symbol not in priority:
                priority.append(symbol)

    merged: list[str] = []
    seen = set()
    for symbol in list(priority) + [str(x) for x in selected]:
        if not symbol or symbol in seen or symbol not in available:
            continue
        seen.add(symbol)
        merged.append(symbol)
        if len(merged) >= max(1, int(max_total)):
            break
    return merged


def _snapshot(plan: Mapping[str, Any]) -> Dict[str, Any]:
    fields = (
        "score",
        "status",
        "market_label",
        "market_regime",
        "market_score",
        "market_strength",
        "market_preferred_direction",
        "market_breadth_5m",
        "major_move_5m_percent",
        "zone_low",
        "zone_high",
        "ideal_entry",
        "zone_distance_percent",
        "chase_distance_atr",
        "sl",
        "tp1",
        "tp2",
        "tp3",
        "risk_percent",
        "room_r",
        "structure_5m",
        "structure_15m",
        "structure_1h",
        "volume_ratio_5m",
        "volume_ratio_15m",
        "extension_atr_5m",
        "quote_volume_24h",
    )
    return {field: plan.get(field) for field in fields if field in plan}


def register_or_refresh_plan(
    ledger: Dict[str, Any],
    plan: Mapping[str, Any],
    now: int,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    symbol = str(plan.get("symbol") or "").upper().strip()
    direction = str(plan.get("direction") or "").upper().strip()
    prep_price = _sf(plan.get("current_price"))
    if not symbol or direction not in {"LONG", "SHORT"} or prep_price <= 0:
        return None, False

    existing = _open_episode(ledger, symbol, direction)
    if existing is not None:
        existing["latest_plan"] = _snapshot(plan)
        existing["latest_plan_status"] = str(plan.get("status") or "")
        existing["updated_at"] = int(now)
        return existing, False

    eid = episode_id(symbol, direction, now)
    episode = {
        "episode_id": eid,
        "source": "MARKET_FIRST_ENTRY_PLAN",
        "symbol": symbol,
        "direction": direction,
        "first_at": int(now),
        "prep_price": prep_price,
        "prep_risk": abs(prep_price - _sf(plan.get("sl"))),
        "initial": _snapshot(plan),
        "latest_plan": _snapshot(plan),
        "latest_plan_status": str(plan.get("status") or ""),
        "telegram_prep_sent": False,
        "prep_message_at": None,
        "zone_touched": False,
        "zone_touch_at": None,
        "entry_condition_met": False,
        "entry_condition_at": None,
        "entry_promoted": False,
        "entry_promoted_at": None,
        "entry_signal_sent": False,
        "entry_signal_sent_at": None,
        "entry_signal_price": None,
        "entry_send_failed": False,
        "entry_send_failed_at": None,
        "last_price": prep_price,
        "last_directional_percent": 0.0,
        "best_favorable_percent": 0.0,
        "worst_adverse_percent": 0.0,
        "best_favorable_r": 0.0,
        "worst_adverse_r": 0.0,
        "best_favorable_before_entry_percent": 0.0,
        "best_favorable_before_entry_r": 0.0,
        "tp1_at": None,
        "tp2_at": None,
        "tp3_at": None,
        "sl_at": None,
        "first_decisive_event": None,
        "tp1_before_entry_signal": False,
        "sl_before_entry_signal": False,
        "entry_window_expired": False,
        "resolved": False,
        "outcome": None,
        "updated_at": int(now),
    }
    ledger.setdefault("episodes", {})[eid] = episode
    return episode, True


def mark_prep_sent(ledger: Dict[str, Any], plan: Mapping[str, Any], now: int) -> bool:
    episode = _open_episode(ledger, str(plan.get("symbol") or ""), str(plan.get("direction") or ""))
    if episode is None:
        episode, _ = register_or_refresh_plan(ledger, plan, now)
    if episode is None:
        return False
    episode["telegram_prep_sent"] = True
    episode["prep_message_at"] = int(now)
    episode["updated_at"] = int(now)
    return True


def mark_entry_condition(
    ledger: Dict[str, Any],
    plan: Mapping[str, Any],
    now: int,
    *,
    promoted: bool = False,
    micro_conflict: bool = False,
) -> bool:
    episode = _open_episode(ledger, str(plan.get("symbol") or ""), str(plan.get("direction") or ""))
    if episode is None:
        episode, _ = register_or_refresh_plan(ledger, plan, now)
    if episode is None:
        return False
    if not episode.get("entry_condition_met"):
        episode["entry_condition_met"] = True
        episode["entry_condition_at"] = int(now)
    if promoted:
        episode["entry_promoted"] = True
        episode["entry_promoted_at"] = int(now)
    if micro_conflict:
        episode["micro_direction_conflict_at_entry"] = int(now)
    episode["updated_at"] = int(now)
    return True


def mark_entry_send_result(
    ledger: Dict[str, Any],
    signal: Mapping[str, Any],
    sent: bool,
    now: int,
) -> bool:
    symbol = str(signal.get("symbol") or "")
    direction = str(signal.get("direction") or "")
    episode = _open_episode(ledger, symbol, direction)
    if episode is None:
        return False
    if sent:
        episode["entry_signal_sent"] = True
        episode["entry_signal_sent_at"] = int(now)
        episode["entry_signal_price"] = _sf(signal.get("sent_price") or signal.get("entry"))
    else:
        episode["entry_send_failed"] = True
        episode["entry_send_failed_at"] = int(now)
        guard = signal.get("pre_send_market_guard")
        liquidity = signal.get("pre_send_liquidity_guard")
        portfolio = signal.get("portfolio_risk")
        episode["entry_send_failure_context"] = {
            "market_guard": dict(guard) if isinstance(guard, Mapping) else None,
            "liquidity_guard": dict(liquidity) if isinstance(liquidity, Mapping) else None,
            "portfolio_risk": dict(portfolio) if isinstance(portfolio, Mapping) else None,
        }
    episode["updated_at"] = int(now)
    return True


def _bar_extremes(df5m: Any, current_price: float) -> Tuple[float, float]:
    high = current_price
    low = current_price
    try:
        if df5m is not None and len(df5m) > 0:
            row = df5m.iloc[-1]
            high = max(high, _sf(row.get("high"), current_price))
            low_value = _sf(row.get("low"), current_price)
            low = min(low, low_value if low_value > 0 else current_price)
    except Exception:
        pass
    return high, low


def _touches(direction: str, high: float, low: float, level: float, kind: str) -> bool:
    if level <= 0:
        return False
    direction = str(direction).upper()
    if kind == "SL":
        return low <= level if direction == "LONG" else high >= level
    return high >= level if direction == "LONG" else low <= level


def _update_one_episode(
    episode: Dict[str, Any],
    current_price: float,
    high: float,
    low: float,
    now: int,
) -> bool:
    if bool(episode.get("resolved")):
        return False
    direction = str(episode.get("direction") or "").upper()
    prep_price = _sf(episode.get("prep_price"))
    if prep_price <= 0 or current_price <= 0:
        return False

    if direction == "LONG":
        favorable_price = max(current_price, high)
        adverse_price = min(current_price, low)
    else:
        favorable_price = min(current_price, low)
        adverse_price = max(current_price, high)

    current_pct = _directional_percent(direction, prep_price, current_price)
    favorable_pct = max(0.0, _directional_percent(direction, prep_price, favorable_price))
    adverse_pct = max(0.0, -_directional_percent(direction, prep_price, adverse_price))
    best_pct = max(_sf(episode.get("best_favorable_percent")), favorable_pct)
    worst_pct = max(_sf(episode.get("worst_adverse_percent")), adverse_pct)

    prep_risk = _sf(episode.get("prep_risk"))
    favorable_abs = abs(favorable_price - prep_price)
    adverse_abs = abs(adverse_price - prep_price)
    best_r = max(_sf(episode.get("best_favorable_r")), favorable_abs / prep_risk if prep_risk > 0 else 0.0)
    worst_r = max(_sf(episode.get("worst_adverse_r")), adverse_abs / prep_risk if prep_risk > 0 else 0.0)

    episode["last_price"] = current_price
    episode["last_directional_percent"] = round(current_pct, 4)
    episode["best_favorable_percent"] = round(best_pct, 4)
    episode["worst_adverse_percent"] = round(worst_pct, 4)
    episode["best_favorable_r"] = round(best_r, 4)
    episode["worst_adverse_r"] = round(worst_r, 4)
    episode["updated_at"] = int(now)

    if not episode.get("entry_signal_sent"):
        episode["best_favorable_before_entry_percent"] = round(
            max(_sf(episode.get("best_favorable_before_entry_percent")), favorable_pct), 4
        )
        episode["best_favorable_before_entry_r"] = round(
            max(_sf(episode.get("best_favorable_before_entry_r")), favorable_abs / prep_risk if prep_risk > 0 else 0.0),
            4,
        )

    initial = episode.get("initial") if isinstance(episode.get("initial"), Mapping) else {}
    latest = episode.get("latest_plan") if isinstance(episode.get("latest_plan"), Mapping) else {}
    zone_low = _sf(initial.get("zone_low") or latest.get("zone_low"))
    zone_high = _sf(initial.get("zone_high") or latest.get("zone_high"))
    if not episode.get("zone_touched") and zone_low > 0 and zone_high > 0 and high >= zone_low and low <= zone_high:
        episode["zone_touched"] = True
        episode["zone_touch_at"] = int(now)

    tp1 = _sf(initial.get("tp1") or latest.get("tp1"))
    tp2 = _sf(initial.get("tp2") or latest.get("tp2"))
    tp3 = _sf(initial.get("tp3") or latest.get("tp3"))
    sl = _sf(initial.get("sl") or latest.get("sl"))

    tp1_before = episode.get("tp1_at") is not None
    sl_before = episode.get("sl_at") is not None
    hit_tp1 = _touches(direction, high, low, tp1, "TP")
    hit_tp2 = _touches(direction, high, low, tp2, "TP")
    hit_tp3 = _touches(direction, high, low, tp3, "TP")
    hit_sl = _touches(direction, high, low, sl, "SL")

    if hit_tp1 and episode.get("tp1_at") is None:
        episode["tp1_at"] = int(now)
    if hit_tp2 and episode.get("tp2_at") is None:
        episode["tp2_at"] = int(now)
    if hit_tp3 and episode.get("tp3_at") is None:
        episode["tp3_at"] = int(now)
    if hit_sl and episode.get("sl_at") is None:
        episode["sl_at"] = int(now)

    if not episode.get("first_decisive_event"):
        new_tp1 = hit_tp1 and not tp1_before
        new_sl = hit_sl and not sl_before
        if new_tp1 and new_sl:
            episode["first_decisive_event"] = "AMBIGUOUS_SAME_5M_BAR"
            episode["first_decisive_at"] = int(now)
        elif new_tp1:
            episode["first_decisive_event"] = "TP1_FIRST"
            episode["first_decisive_at"] = int(now)
        elif new_sl:
            episode["first_decisive_event"] = "SL_FIRST"
            episode["first_decisive_at"] = int(now)

    entry_sent_at = int(episode.get("entry_signal_sent_at") or 0)
    if episode.get("tp1_at") and (not entry_sent_at or int(episode["tp1_at"]) < entry_sent_at):
        episode["tp1_before_entry_signal"] = True
    if episode.get("sl_at") and (not entry_sent_at or int(episode["sl_at"]) < entry_sent_at):
        episode["sl_before_entry_signal"] = True

    first_at = int(episode.get("first_at") or now)
    if not episode.get("entry_signal_sent") and now - first_at >= ENTRY_WINDOW_SECONDS:
        episode["entry_window_expired"] = True
        episode.setdefault("entry_window_expired_at", int(now))

    if episode.get("tp3_at"):
        first = str(episode.get("first_decisive_event") or "")
        if first == "SL_FIRST":
            outcome = "SL_FIRST_THEN_TP3_RECOVERY"
        elif first == "AMBIGUOUS_SAME_5M_BAR":
            outcome = "AMBIGUOUS_THEN_TP3"
        else:
            outcome = "TP3_REACHED"
        _resolve(episode, now, outcome)
    elif now - first_at >= TRACK_WINDOW_SECONDS:
        _resolve(episode, now, _timeout_outcome(episode))
    return True


def update_symbol_market(
    ledger: Dict[str, Any],
    symbol: str,
    current_price: float,
    df5m: Any,
    now: int,
) -> int:
    if current_price <= 0:
        return 0
    high, low = _bar_extremes(df5m, current_price)
    touched = 0
    episodes = ledger.get("episodes", {})
    if not isinstance(episodes, Mapping):
        return 0
    for episode in episodes.values():
        if not isinstance(episode, dict) or bool(episode.get("resolved")):
            continue
        if str(episode.get("symbol") or "") != str(symbol):
            continue
        if _update_one_episode(episode, current_price, high, low, now):
            touched += 1
    return touched


def _timeout_outcome(episode: Mapping[str, Any]) -> str:
    first = str(episode.get("first_decisive_event") or "")
    if first == "TP1_FIRST":
        if episode.get("tp3_at"):
            return "TP3_REACHED"
        if episode.get("tp2_at"):
            return "TP2_REACHED"
        return "TP1_REACHED"
    if first == "SL_FIRST":
        if episode.get("tp2_at"):
            return "SL_FIRST_THEN_TP2_RECOVERY"
        if episode.get("tp1_at"):
            return "SL_FIRST_THEN_TP1_RECOVERY"
        return "SL_FIRST"
    if first == "AMBIGUOUS_SAME_5M_BAR":
        return "AMBIGUOUS_WITH_TARGET" if episode.get("tp1_at") else "AMBIGUOUS_SAME_5M_BAR"
    if _sf(episode.get("best_favorable_r")) >= 0.50:
        return "DIRECTIONAL_MOVE_NO_TP1"
    if _sf(episode.get("best_favorable_r")) > 0:
        return "SMALL_FAVORABLE_ONLY"
    return "NO_FAVORABLE_MOVE"


def _resolve(episode: Dict[str, Any], now: int, outcome: str) -> None:
    if bool(episode.get("resolved")):
        return
    first_at = int(episode.get("first_at") or now)
    episode["resolved"] = True
    episode["closed_at"] = int(now)
    episode["duration_minutes"] = round(max(0.0, (now - first_at) / 60.0), 2)
    episode["outcome"] = str(outcome)
    episode["final_directional_percent"] = round(_sf(episode.get("last_directional_percent")), 4)
    episode["final_directional_r"] = round(
        abs(_sf(episode.get("last_price")) - _sf(episode.get("prep_price"))) / _sf(episode.get("prep_risk"))
        if _sf(episode.get("prep_risk")) > 0
        else 0.0,
        4,
    )


def finalize_expired(ledger: Dict[str, Any], now: int) -> int:
    closed = 0
    episodes = ledger.get("episodes", {})
    if not isinstance(episodes, Mapping):
        return 0
    for episode in episodes.values():
        if not isinstance(episode, dict) or bool(episode.get("resolved")):
            continue
        first_at = int(episode.get("first_at") or now)
        if not episode.get("entry_signal_sent") and now - first_at >= ENTRY_WINDOW_SECONDS:
            episode["entry_window_expired"] = True
            episode.setdefault("entry_window_expired_at", int(now))
        if now - first_at >= TRACK_WINDOW_SECONDS:
            _resolve(episode, now, _timeout_outcome(episode))
            closed += 1
    return closed


def ledger_summary(ledger: Mapping[str, Any]) -> Dict[str, Any]:
    episodes = ledger.get("episodes", {}) if isinstance(ledger, Mapping) else {}
    values = [item for item in episodes.values() if isinstance(item, Mapping)] if isinstance(episodes, Mapping) else []
    resolved = [item for item in values if item.get("resolved")]
    tp1_before_entry = [item for item in values if item.get("tp1_before_entry_signal")]
    return {
        "total": len(values),
        "open": sum(1 for item in values if not item.get("resolved")),
        "resolved": len(resolved),
        "telegram_prep_sent": sum(1 for item in values if item.get("telegram_prep_sent")),
        "zone_touched": sum(1 for item in values if item.get("zone_touched")),
        "entry_condition_met": sum(1 for item in values if item.get("entry_condition_met")),
        "entry_signal_sent": sum(1 for item in values if item.get("entry_signal_sent")),
        "entry_send_failed": sum(1 for item in values if item.get("entry_send_failed")),
        "tp1_reached": sum(1 for item in values if item.get("tp1_at")),
        "tp2_reached": sum(1 for item in values if item.get("tp2_at")),
        "tp3_reached": sum(1 for item in values if item.get("tp3_at")),
        "sl_reached": sum(1 for item in values if item.get("sl_at")),
        "tp1_first": sum(1 for item in values if item.get("first_decisive_event") == "TP1_FIRST"),
        "sl_first": sum(1 for item in values if item.get("first_decisive_event") == "SL_FIRST"),
        "ambiguous_first_bar": sum(1 for item in values if item.get("first_decisive_event") == "AMBIGUOUS_SAME_5M_BAR"),
        "tp1_before_entry_signal": len(tp1_before_entry),
        "notified_tp1_before_entry_signal": sum(1 for item in tp1_before_entry if item.get("telegram_prep_sent")),
        "stop_then_recovery": sum(
            1 for item in values
            if item.get("first_decisive_event") == "SL_FIRST" and item.get("tp1_at")
        ),
        "avg_best_favorable_r_resolved": round(
            sum(_sf(item.get("best_favorable_r")) for item in resolved) / len(resolved), 4
        ) if resolved else 0.0,
        "outcomes": _count_outcomes(resolved),
    }


def _count_outcomes(values: Iterable[Mapping[str, Any]]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for item in values:
        outcome = str(item.get("outcome") or "UNRESOLVED")
        result[outcome] = result.get(outcome, 0) + 1
    return dict(sorted(result.items()))


def _early_summary(bot: Any) -> Dict[str, Any]:
    data = bot.load_json_file(EARLY_LEDGER_FILE, {})
    episodes = data.get("episodes", {}) if isinstance(data, Mapping) else {}
    values = [item for item in episodes.values() if isinstance(item, Mapping)] if isinstance(episodes, Mapping) else []
    resolved = [item for item in values if item.get("resolved")]
    positive = sum(1 for item in resolved if item.get("outcome") in {"GOOD_MOVE", "STRONG_MOVE"})
    return {
        "total": len(values),
        "open": sum(1 for item in values if not item.get("resolved")),
        "resolved": len(resolved),
        "positive": positive,
        "negative": len(resolved) - positive,
        "positive_rate_resolved": round(positive / len(resolved), 4) if resolved else None,
        "outcomes": _count_outcomes(resolved),
        "note": "EARLY is directional observation only; it is not realised trade P&L.",
    }


def combined_summary(bot: Any, ledger: Mapping[str, Any], now: int) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "generated_at": int(now),
        "entry_plan": ledger_summary(ledger),
        "early_move": _early_summary(bot),
        "definitions": {
            "tp1_before_entry_signal": "Preparation reached its TP1 direction before a real ENTRY signal was sent.",
            "entry_signal_sent": "A real Telegram trade signal was sent; still no exchange order is opened automatically.",
            "prep_performance": "Hypothetical observation from preparation-message price, never realised P&L.",
        },
    }


def save_combined_summary(bot: Any, ledger: Mapping[str, Any], now: int) -> Dict[str, Any]:
    payload = combined_summary(bot, ledger, now)
    bot.save_json_file(SUMMARY_FILE, payload)
    return payload
