"""Background outcome ledger for Market First EARLY alerts.

Every EARLY alert is treated as a virtual observation episode. No TP levels are
required. We store the original alert snapshot and then track direction-adjusted
movement, best favorable excursion, worst adverse excursion, duration and final
status. This gives later analysis/ML a clean history of whether an EARLY alert was
useful without pretending that a manual trade was actually opened.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, Mapping

VERSION = "MARKET_FIRST_EARLY_LEDGER_V2_ML_READY_2026_08_30"
LEDGER_FILE = "market_first_early_ledger.json"
MAX_EPISODES = 1500
_INSTALLED = False

SNAPSHOT_FIELDS = (
    "score",
    "stage",
    "market_label",
    "market_regime",
    "market_score",
    "market_strength",
    "market_breadth_5m",
    "market_breadth_24h",
    "major_move_5m_percent",
    "move_1m_percent",
    "move_3m_percent",
    "move_5m_percent",
    "volume_ratio_1m",
    "breakout_20m",
    "relative_strength_5m",
    "extension_atr_5m",
    "structure_5m",
    "structure_15m",
    "structure_1h",
    "independent_move",
    "quote_volume_24h",
    "risk_percent",
    "room_r",
    "risk_reject_reason",
    "derivatives_available",
    "oi_history_available",
    "oi_change_5m_percent",
    "oi_change_15m_percent",
    "funding_available",
    "funding_rate_8h_bps",
    "funding_crowding_8h_bps",
    "taker_available",
    "taker_imbalance_alignment",
    "cvd_available",
    "cvd_impulse_alignment",
    "book_available",
    "book_imbalance_alignment",
    "book_opposing_wall_ratio",
    "derivatives_soft_score",
)


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


def _load(bot: Any) -> Dict[str, Any]:
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


def _save(bot: Any, ledger: Dict[str, Any], now: int) -> None:
    ledger["version"] = VERSION
    ledger["updated_at"] = int(now)
    _prune(ledger)
    bot.save_json_file(LEDGER_FILE, ledger)


def episode_id(symbol: str, direction: str, first_at: int) -> str:
    return f"{str(symbol).upper()}:{str(direction).upper()}:{int(first_at)}"


def register_episode(
    ledger: Dict[str, Any],
    decision: Mapping[str, Any],
    now: int,
) -> str:
    symbol = str(decision.get("symbol") or "").upper().strip()
    direction = str(decision.get("direction") or "").upper().strip()
    price = _sf(decision.get("current_price"))
    eid = episode_id(symbol, direction, now)
    episodes = ledger.setdefault("episodes", {})
    if eid in episodes:
        return eid

    snapshot = {key: decision.get(key) for key in SNAPSHOT_FIELDS if key in decision}
    episodes[eid] = {
        "episode_id": eid,
        "source": "MARKET_FIRST_EARLY",
        "symbol": symbol,
        "direction": direction,
        "first_at": int(now),
        "alert_price": price,
        "initial": snapshot,
        "status": "NEW",
        "last_price": price,
        "last_directional_percent": 0.0,
        "best_favorable_percent": 0.0,
        "worst_adverse_percent": 0.0,
        "resolved": False,
        "outcome": None,
        "quality_label": None,
    }
    return eid


def update_episode(
    ledger: Dict[str, Any],
    item: Mapping[str, Any],
    now: int,
) -> bool:
    eid = str(item.get("early_episode_id") or "").strip()
    if not eid:
        return False
    episode = ledger.setdefault("episodes", {}).get(eid)
    if not isinstance(episode, dict):
        return False

    direction = str(episode.get("direction") or item.get("direction") or "").upper()
    alert_price = _sf(episode.get("alert_price") or item.get("alert_price"))
    last_price = _sf(item.get("last_price") or item.get("alert_price"))
    current = _directional_percent(direction, alert_price, last_price)
    best = max(
        _sf(episode.get("best_favorable_percent")),
        _sf(item.get("best_favorable_percent")),
        max(0.0, current),
    )
    worst = max(
        _sf(episode.get("worst_adverse_percent")),
        max(0.0, -current),
    )
    status = str(item.get("status") or episode.get("status") or "NEW").upper()

    episode["status"] = status
    episode["last_price"] = last_price
    episode["last_directional_percent"] = round(current, 4)
    episode["best_favorable_percent"] = round(best, 4)
    episode["worst_adverse_percent"] = round(worst, 4)
    episode["updated_at"] = int(now)

    if status == "DEAD" and not bool(episode.get("resolved")):
        episode["resolved"] = True
        episode["closed_at"] = int(now)
        episode["duration_minutes"] = round(
            max(0.0, (int(now) - int(episode.get("first_at") or now)) / 60.0),
            2,
        )
        episode["final_directional_percent"] = round(current, 4)

        # The target is now explicit: did the EARLY alert produce a clean,
        # monetisable directional move? MIXED is therefore a negative training
        # example rather than an unlabeled result. No synthetic TP/SL is used.
        if best >= 2.0:
            outcome, label = "STRONG_MOVE", 1
        elif best >= 0.75 and best >= max(0.75, worst * 1.20):
            outcome, label = "GOOD_MOVE", 1
        elif worst >= 0.90 and best < 0.50:
            outcome, label = "BAD_MOVE", 0
        else:
            outcome, label = "MIXED", 0
        episode["outcome"] = outcome
        episode["quality_label"] = label
    return True


def ledger_summary(ledger: Mapping[str, Any]) -> Dict[str, int]:
    episodes = ledger.get("episodes", {}) if isinstance(ledger, Mapping) else {}
    if not isinstance(episodes, Mapping):
        return {"total": 0, "open": 0, "good": 0, "bad": 0, "mixed": 0}
    values = [item for item in episodes.values() if isinstance(item, Mapping)]
    return {
        "total": len(values),
        "open": sum(1 for item in values if not item.get("resolved")),
        "good": sum(1 for item in values if item.get("outcome") in {"GOOD_MOVE", "STRONG_MOVE"}),
        "bad": sum(1 for item in values if item.get("outcome") == "BAD_MOVE"),
        "mixed": sum(1 for item in values if item.get("outcome") == "MIXED"),
    }


def install() -> None:
    """Patch only the EARLY alert bookkeeping functions in Market First runner."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    import market_first_runner as runner

    original_register = runner._register_alert
    original_lifecycle = runner._update_alert_lifecycle

    def register_with_ledger(state, decision, now):
        original_register(state, decision, now)
        key = runner._alert_key(str(decision.get("symbol") or ""), str(decision.get("direction") or ""))
        active = state.setdefault("active_alerts", {}).get(key)
        if not isinstance(active, dict):
            return
        ledger = _load(runner.bot)
        eid = register_episode(ledger, decision, int(now))
        active["early_episode_id"] = eid
        _save(runner.bot, ledger, int(now))

    def lifecycle_with_ledger(state, universe, now):
        changed = original_lifecycle(state, universe, now)
        ledger = _load(runner.bot)
        touched = 0
        active = state.setdefault("active_alerts", {})
        for key, item in active.items():
            if not isinstance(item, dict):
                continue
            if not item.get("early_episode_id"):
                # Adopt alerts that existed before this ledger was deployed. Such
                # legacy episodes remain useful for outcome statistics, but their
                # partial snapshots are intentionally excluded from ML training.
                first_at = int(item.get("first_at") or now)
                seed = {
                    "symbol": item.get("symbol"),
                    "direction": item.get("direction"),
                    "current_price": item.get("alert_price"),
                    "score": item.get("score"),
                    "market_label": item.get("market_label"),
                    "market_regime": item.get("market_regime"),
                }
                eid = register_episode(ledger, seed, first_at)
                item["early_episode_id"] = eid
            if update_episode(ledger, item, int(now)):
                touched += 1
        if touched:
            _save(runner.bot, ledger, int(now))
            print("MARKET FIRST EARLY LEDGER:", ledger_summary(ledger))
        return changed

    runner._register_alert = register_with_ledger
    runner._update_alert_lifecycle = lifecycle_with_ledger


# market_first_live imports market_first_new_listings on every run. The small
# opt-in below keeps tests/PR branches free of side effects while enabling the
# hooks automatically on the actual live main branch.
if str(os.getenv("GITHUB_REF_NAME") or "").strip() == "main":
    install()
