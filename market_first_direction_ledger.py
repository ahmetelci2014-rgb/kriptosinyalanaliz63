"""Persistent outcome ledger for Market First dual-direction decisions.

This module is observational only. It never creates or blocks a signal. It records
what the direction engine decided, whether a reversal preparation was available,
and how price behaved afterwards. Real Telegram trade delivery is stored
separately from hypothetical directional outcomes.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional

VERSION = "MARKET_FIRST_DIRECTION_LEDGER_V1_2026_09_05"
LEDGER_FILE = "market_first_direction_ledger.json"
SUMMARY_FILE = "market_first_direction_summary.json"
TRACK_SECONDS = 6 * 60 * 60
EPISODE_BUCKET_SECONDS = 30 * 60
GOOD_MOVE_PERCENT = 0.50


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def load(bot: Any) -> Dict[str, Any]:
    payload = bot.load_json_file(LEDGER_FILE, {"version": VERSION, "episodes": {}})
    if not isinstance(payload, dict):
        payload = {}
    payload["version"] = VERSION
    if not isinstance(payload.get("episodes"), dict):
        payload["episodes"] = {}
    return payload


def _episode_key(symbol: str, current_direction: str, engine_direction: str, now: int) -> str:
    bucket = int(now) // EPISODE_BUCKET_SECONDS
    selected = engine_direction or "NONE"
    return f"{symbol}:{current_direction}:{selected}:{bucket}"


def _plan_geometry(plan: Optional[Mapping[str, Any]], decision: Mapping[str, Any]) -> Dict[str, float]:
    source = plan if isinstance(plan, Mapping) else decision
    return {
        "zone_low": _sf(source.get("zone_low") if plan else decision.get("entry_plan_zone_low")),
        "zone_high": _sf(source.get("zone_high") if plan else decision.get("entry_plan_zone_high")),
        "ideal_entry": _sf(source.get("ideal_entry") if plan else decision.get("entry_plan_ideal_entry")),
        "sl": _sf(source.get("sl")),
        "tp1": _sf(source.get("tp1")),
        "tp2": _sf(source.get("tp2")),
        "tp3": _sf(source.get("tp3")),
        "risk_percent": _sf(source.get("risk_percent")),
        "room_r": _sf(source.get("room_r")),
    }


def register_decision(
    ledger: Dict[str, Any],
    *,
    decision: Mapping[str, Any],
    diag: Mapping[str, Any],
    now: int,
    reversal_plan: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    symbol = str(decision.get("symbol") or "").upper()
    current_direction = str(decision.get("direction") or "").upper()
    if not symbol or current_direction not in {"LONG", "SHORT"}:
        return None

    selected = str(diag.get("selected_direction") or "").upper()
    if selected not in {"LONG", "SHORT"}:
        selected = ""
    tracked_direction = selected or current_direction
    price = _sf(decision.get("current_price") or decision.get("entry"))
    if price <= 0:
        return None

    key = _episode_key(symbol, current_direction, selected, now)
    episodes = ledger.setdefault("episodes", {})
    episode = episodes.get(key)
    if not isinstance(episode, dict):
        episode = {
            "episode_id": key,
            "symbol": symbol,
            "observed_at": int(now),
            "observation_price": price,
            "current_direction": current_direction,
            "engine_selected_direction": selected or None,
            "tracked_direction": tracked_direction,
            "engine_reason": diag.get("reason"),
            "reversal": bool(diag.get("reversal") and selected and selected != current_direction),
            "long_score": int(_sf((diag.get("long") or {}).get("score"))),
            "short_score": int(_sf((diag.get("short") or {}).get("score"))),
            "selected_score": int(_sf(diag.get("selected_score"))),
            "other_score": int(_sf(diag.get("other_score"))),
            "margin": int(_sf(diag.get("margin"))),
            "confirmations": int(_sf(diag.get("confirmations"))),
            "confirmation_flags": dict(diag.get("confirmation_flags") or {}),
            "structures": dict(diag.get("structures") or {}),
            "direction_engine_version": diag.get("version"),
            "candidate_score": int(_sf(decision.get("score"))),
            "entry_plan_trade": bool(decision.get("entry_plan_trade")),
            "best_favorable_percent": 0.0,
            "worst_adverse_percent": 0.0,
            "best_favorable_price": price,
            "worst_adverse_price": price,
            "last_price": price,
            "last_checked_at": int(now),
            "tp1_at": 0,
            "tp2_at": 0,
            "tp3_at": 0,
            "sl_at": 0,
            "first_decisive_event": None,
            "ambiguous_first_bar": False,
            "real_entry_signal_sent": False,
            "real_entry_signal_at": 0,
            "real_entry_signal_direction": None,
            "resolved": False,
            "outcome": None,
        }
        episode.update(_plan_geometry(reversal_plan, decision))
        if isinstance(reversal_plan, Mapping):
            episode["reversal_plan_status"] = reversal_plan.get("status")
            episode["reversal_plan_score"] = int(_sf(reversal_plan.get("score")))
            episode["reversal_plan_version"] = reversal_plan.get("direction_engine_version")
        episodes[key] = episode
    else:
        episode["last_checked_at"] = int(now)
        episode["engine_reason"] = diag.get("reason")
        episode["long_score"] = int(_sf((diag.get("long") or {}).get("score")))
        episode["short_score"] = int(_sf((diag.get("short") or {}).get("score")))
        episode["margin"] = int(_sf(diag.get("margin")))
        if isinstance(reversal_plan, Mapping):
            episode.update(_plan_geometry(reversal_plan, decision))
            episode["reversal_plan_status"] = reversal_plan.get("status")
            episode["reversal_plan_score"] = int(_sf(reversal_plan.get("score")))

    return episode


def _last_bar_high_low(df5m: Any, current_price: float) -> tuple[float, float]:
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


def _touches(episode: Mapping[str, Any], high: float, low: float) -> Dict[str, bool]:
    direction = str(episode.get("tracked_direction") or "").upper()
    tp1 = _sf(episode.get("tp1"))
    tp2 = _sf(episode.get("tp2"))
    tp3 = _sf(episode.get("tp3"))
    sl = _sf(episode.get("sl"))
    if direction == "LONG":
        return {
            "tp1": tp1 > 0 and high >= tp1,
            "tp2": tp2 > 0 and high >= tp2,
            "tp3": tp3 > 0 and high >= tp3,
            "sl": sl > 0 and low <= sl,
        }
    return {
        "tp1": tp1 > 0 and low <= tp1,
        "tp2": tp2 > 0 and low <= tp2,
        "tp3": tp3 > 0 and low <= tp3,
        "sl": sl > 0 and high >= sl,
    }


def update_symbol_market(
    ledger: Dict[str, Any],
    symbol: str,
    current_price: float,
    df5m: Any,
    now: int,
) -> int:
    symbol = str(symbol or "").upper()
    current_price = _sf(current_price)
    if not symbol or current_price <= 0:
        return 0

    high, low = _last_bar_high_low(df5m, current_price)
    changed = 0
    for episode in (ledger.get("episodes") or {}).values():
        if not isinstance(episode, dict) or episode.get("resolved"):
            continue
        if str(episode.get("symbol") or "").upper() != symbol:
            continue
        observed_at = int(episode.get("observed_at") or 0)
        if observed_at <= 0 or now < observed_at:
            continue
        if now - observed_at > TRACK_SECONDS:
            continue

        entry = _sf(episode.get("observation_price"))
        if entry <= 0:
            continue
        direction = str(episode.get("tracked_direction") or "").upper()
        if direction == "LONG":
            favorable_price = high
            adverse_price = low
            favorable = max(0.0, (high - entry) / entry * 100.0)
            adverse = max(0.0, (entry - low) / entry * 100.0)
        elif direction == "SHORT":
            favorable_price = low
            adverse_price = high
            favorable = max(0.0, (entry - low) / entry * 100.0)
            adverse = max(0.0, (high - entry) / entry * 100.0)
        else:
            continue

        if favorable > _sf(episode.get("best_favorable_percent")):
            episode["best_favorable_percent"] = round(favorable, 4)
            episode["best_favorable_price"] = favorable_price
        if adverse > _sf(episode.get("worst_adverse_percent")):
            episode["worst_adverse_percent"] = round(adverse, 4)
            episode["worst_adverse_price"] = adverse_price

        touches = _touches(episode, high, low)
        same_bar_tp1_sl = touches["tp1"] and touches["sl"] and not episode.get("tp1_at") and not episode.get("sl_at")
        if same_bar_tp1_sl:
            episode["ambiguous_first_bar"] = True
        if touches["tp1"] and not episode.get("tp1_at"):
            episode["tp1_at"] = int(now)
        if touches["tp2"] and not episode.get("tp2_at"):
            episode["tp2_at"] = int(now)
        if touches["tp3"] and not episode.get("tp3_at"):
            episode["tp3_at"] = int(now)
        if touches["sl"] and not episode.get("sl_at"):
            episode["sl_at"] = int(now)

        if not episode.get("first_decisive_event") and not same_bar_tp1_sl:
            if touches["tp1"] and not touches["sl"]:
                episode["first_decisive_event"] = "TP1_FIRST"
            elif touches["sl"] and not touches["tp1"]:
                episode["first_decisive_event"] = "SL_FIRST"

        episode["last_price"] = current_price
        episode["last_checked_at"] = int(now)
        changed += 1
    return changed


def mark_real_signal(ledger: Dict[str, Any], signal: Mapping[str, Any], sent: bool, now: int) -> None:
    if not sent:
        return
    symbol = str(signal.get("symbol") or "").upper()
    direction = str(signal.get("direction") or "").upper()
    candidates = []
    for episode in (ledger.get("episodes") or {}).values():
        if not isinstance(episode, dict) or episode.get("resolved"):
            continue
        if str(episode.get("symbol") or "").upper() != symbol:
            continue
        if str(episode.get("tracked_direction") or "").upper() != direction:
            continue
        candidates.append(episode)
    if not candidates:
        return
    episode = max(candidates, key=lambda item: int(item.get("observed_at") or 0))
    episode["real_entry_signal_sent"] = True
    episode["real_entry_signal_at"] = int(now)
    episode["real_entry_signal_direction"] = direction


def finalize_expired(ledger: Dict[str, Any], now: int) -> int:
    closed = 0
    for episode in (ledger.get("episodes") or {}).values():
        if not isinstance(episode, dict) or episode.get("resolved"):
            continue
        observed_at = int(episode.get("observed_at") or 0)
        if observed_at <= 0 or now - observed_at < TRACK_SECONDS:
            continue

        first = str(episode.get("first_decisive_event") or "")
        if episode.get("ambiguous_first_bar") and not first:
            outcome = "AMBIGUOUS_TP1_SL_FIRST_BAR"
        elif first == "SL_FIRST" and episode.get("tp1_at"):
            outcome = "SL_FIRST_THEN_RECOVERY"
        elif episode.get("tp3_at"):
            outcome = "TP3_REACHED"
        elif episode.get("tp2_at"):
            outcome = "TP2_REACHED"
        elif episode.get("tp1_at"):
            outcome = "TP1_REACHED"
        elif first == "SL_FIRST" or episode.get("sl_at"):
            outcome = "SL_REACHED"
        else:
            favorable = _sf(episode.get("best_favorable_percent"))
            adverse = _sf(episode.get("worst_adverse_percent"))
            if favorable >= GOOD_MOVE_PERCENT and favorable > adverse:
                outcome = "DIRECTION_GOOD"
            elif adverse >= GOOD_MOVE_PERCENT and adverse > favorable:
                outcome = "DIRECTION_BAD"
            else:
                outcome = "DIRECTION_MIXED"

        episode["resolved"] = True
        episode["resolved_at"] = int(now)
        episode["outcome"] = outcome
        closed += 1
    return closed


def summary(ledger: Mapping[str, Any], now: int) -> Dict[str, Any]:
    episodes = [item for item in (ledger.get("episodes") or {}).values() if isinstance(item, Mapping)]
    resolved = [item for item in episodes if item.get("resolved")]
    reversals = [item for item in episodes if item.get("reversal")]
    selected_long = sum(1 for item in episodes if item.get("engine_selected_direction") == "LONG")
    selected_short = sum(1 for item in episodes if item.get("engine_selected_direction") == "SHORT")
    rejected = sum(1 for item in episodes if not item.get("engine_selected_direction"))
    outcomes: Dict[str, int] = {}
    for item in resolved:
        key = str(item.get("outcome") or "UNKNOWN")
        outcomes[key] = outcomes.get(key, 0) + 1

    reversal_resolved = [item for item in reversals if item.get("resolved")]
    reversal_tp_first = sum(1 for item in reversal_resolved if item.get("first_decisive_event") == "TP1_FIRST")
    reversal_sl_first = sum(1 for item in reversal_resolved if item.get("first_decisive_event") == "SL_FIRST")

    return {
        "version": VERSION,
        "generated_at": int(now),
        "total": len(episodes),
        "open": len(episodes) - len(resolved),
        "resolved": len(resolved),
        "selected_long": selected_long,
        "selected_short": selected_short,
        "engine_no_selection": rejected,
        "reversal_total": len(reversals),
        "reversal_resolved": len(reversal_resolved),
        "reversal_tp1_first": reversal_tp_first,
        "reversal_sl_first": reversal_sl_first,
        "real_entry_signal_sent": sum(1 for item in episodes if item.get("real_entry_signal_sent")),
        "outcomes": outcomes,
        "note": "Direction-ledger outcomes are observational unless real_entry_signal_sent=true; they are not realised P&L.",
    }


def save(bot: Any, ledger: Dict[str, Any], now: int) -> None:
    ledger["version"] = VERSION
    ledger["generated_at"] = int(now)
    bot.save_json_file(LEDGER_FILE, ledger)
    bot.save_json_file(SUMMARY_FILE, summary(ledger, now))
