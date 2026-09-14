"""Market First Profit Survival V2: rolling realised-health memory.

V1 protected the account after a bad daily report, but it could return to NORMAL
as soon as the next single day looked better. That forgets recent losses too
quickly. V2 keeps a two-completed-day realised memory and adds an intraday
warning mode before the hard daily stop limit.

This is an additive patch around market_first_profit_survival_gate. It does not
create trades, place exchange orders, widen stops, or weaken the existing A++
RECOVERY_STRICT rules.
"""
from __future__ import annotations

from datetime import datetime
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Tuple

import market_first_profit_survival_gate as gate

VERSION = "MARKET_FIRST_PROFIT_SURVIVAL_V2_ROLLING_2026_09_14"
STATE_FILE = "market_first_profit_survival_v2.json"

ROLLING_COMPLETED_DAYS = 2
ROLLING_MIN_CLOSED = 8
ROLLING_RECOVERY_STOP_RATE = 0.55
ROLLING_CRITICAL_STOP_RATE = 0.70
INTRADAY_RECOVERY_STOPS = 2
MAX_DAILY_STOPS = 3
MAX_CONSECUTIVE_STOPS = 3

_INSTALLED = False


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _day_before(day_key: str, today: str) -> bool:
    try:
        return datetime.fromisoformat(day_key).date() < datetime.fromisoformat(today).date()
    except Exception:
        return False


def _performance_day_health(day_key: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Approximate one completed day from the durable performance counters.

    TP1 is counted once per successful trade in the current tracker, while SL is
    the realised stop count. Later TP2/TP3 milestones are deliberately not added
    again, preventing one winner from being counted multiple times.
    """
    wins = max(0, _si(payload.get("tp1")))
    losses = max(0, _si(payload.get("sl")))
    closed = wins + losses
    stop_rate = losses / closed if closed else 0.0
    return {
        "date": day_key,
        "source": "performance",
        "wins": wins,
        "losses": losses,
        "closed_directional": closed,
        "stop_rate": round(stop_rate, 6),
    }


def rolling_completed_health(
    performance: Mapping[str, Any],
    daily_report: Mapping[str, Any],
    today: str,
) -> Dict[str, Any]:
    """Build a two-completed-day realised cohort without double counting.

    The compact daily report is preferred for its date because it classifies
    TP/BE/SL at trade level. Earlier completed days fall back to performance.json.
    """
    days = performance.get("days") if isinstance(performance, Mapping) else {}
    days = days if isinstance(days, Mapping) else {}

    exact = gate._daily_report_health(daily_report if isinstance(daily_report, Mapping) else {})
    exact_date = str(exact.get("date") or "")

    selected: list[Dict[str, Any]] = []
    used_dates = set()

    if (
        exact_date
        and _day_before(exact_date, today)
        and _si(exact.get("closed_directional")) > 0
    ):
        selected.append({
            "date": exact_date,
            "source": "daily_report",
            "wins": _si(exact.get("wins")),
            "losses": _si(exact.get("losses")),
            "closed_directional": _si(exact.get("closed_directional")),
            "stop_rate": _sf(exact.get("stop_rate")),
        })
        used_dates.add(exact_date)

    previous_keys = sorted(
        (
            str(key)
            for key in days.keys()
            if str(key) not in used_dates and _day_before(str(key), today)
        ),
        reverse=True,
    )
    for day_key in previous_keys:
        if len(selected) >= ROLLING_COMPLETED_DAYS:
            break
        row = days.get(day_key)
        if not isinstance(row, Mapping):
            continue
        health = _performance_day_health(day_key, row)
        if _si(health.get("closed_directional")) <= 0:
            continue
        selected.append(health)
        used_dates.add(day_key)

    # Keep the most recent completed dates even when daily-report insertion order
    # differs from the performance fallback order.
    selected = sorted(selected, key=lambda item: str(item.get("date") or ""), reverse=True)
    selected = selected[:ROLLING_COMPLETED_DAYS]

    wins = sum(_si(item.get("wins")) for item in selected)
    losses = sum(_si(item.get("losses")) for item in selected)
    closed = wins + losses
    stop_rate = losses / closed if closed else 0.0
    return {
        "days": selected,
        "day_count": len(selected),
        "wins": wins,
        "losses": losses,
        "closed_directional": closed,
        "stop_rate": round(stop_rate, 6),
        "win_rate": round(wins / closed, 6) if closed else 0.0,
    }


def _intraday_v2(performance: Mapping[str, Any], today: str) -> Dict[str, Any]:
    base = dict(gate._intraday_health(performance, today))
    days = performance.get("days") if isinstance(performance, Mapping) else {}
    day = days.get(today) if isinstance(days, Mapping) else None
    if isinstance(day, Mapping):
        base["direct_stops"] = _si(day.get("sl_after_no_return"))
    else:
        base["direct_stops"] = 0
    return base


def mode_from_health(
    daily: Mapping[str, Any],
    rolling: Mapping[str, Any],
    intraday: Mapping[str, Any],
    today: str,
) -> Tuple[str, str]:
    total_stops = _si(intraday.get("sl"))
    direct_stops = _si(intraday.get("direct_stops"))
    consecutive = _si(intraday.get("consecutive_stops"))

    if total_stops >= MAX_DAILY_STOPS or direct_stops >= MAX_DAILY_STOPS:
        return "HALT", "DAILY_STOP_LIMIT_V2"
    if consecutive >= MAX_CONSECUTIVE_STOPS:
        return "HALT", "CONSECUTIVE_STOP_LIMIT"

    # Do not wait for a third/fourth loss to tighten. After two same-day stops,
    # only the existing A++ recovery profile may pass.
    if total_stops >= INTRADAY_RECOVERY_STOPS or direct_stops >= INTRADAY_RECOVERY_STOPS:
        return "RECOVERY_STRICT", "INTRADAY_STOP_WARNING"

    rolling_closed = _si(rolling.get("closed_directional"))
    rolling_rate = _sf(rolling.get("stop_rate"))
    if rolling_closed >= ROLLING_MIN_CLOSED:
        if rolling_rate >= ROLLING_CRITICAL_STOP_RATE:
            return "RECOVERY_STRICT", "CRITICAL_ROLLING_STOP_RATE"
        if rolling_rate >= ROLLING_RECOVERY_STOP_RATE:
            return "RECOVERY_STRICT", "POOR_ROLLING_STOP_RATE"

    # Preserve V1's single-report protection as a fallback when the rolling
    # cohort is still too small.
    closed = _si(daily.get("closed_directional"))
    stop_rate = _sf(daily.get("stop_rate"))
    recent = gate._report_is_recent(str(daily.get("date") or ""), today)
    if recent and closed >= gate.MIN_RECENT_CLOSED and stop_rate >= gate.CRITICAL_STOP_RATE:
        return "RECOVERY_STRICT", "CRITICAL_RECENT_STOP_RATE"
    if recent and closed >= gate.MIN_RECENT_CLOSED and stop_rate >= gate.RECOVERY_STOP_RATE:
        return "RECOVERY_STRICT", "POOR_RECENT_STOP_RATE"
    return "NORMAL", "OK"


def _current_health_v2() -> Dict[str, Any]:
    today = gate._istanbul_today()
    daily_payload = gate._load_json(gate.DAILY_REPORT_FILE, {})
    perf_payload = gate._load_json(gate.PERFORMANCE_FILE, {})
    daily_payload = daily_payload if isinstance(daily_payload, Mapping) else {}
    perf_payload = perf_payload if isinstance(perf_payload, Mapping) else {}

    daily = gate._daily_report_health(daily_payload)
    rolling = rolling_completed_health(perf_payload, daily_payload, today)
    intraday = _intraday_v2(perf_payload, today)
    mode, reason = mode_from_health(daily, rolling, intraday, today)
    return {
        "today": today,
        "mode": mode,
        "reason": reason,
        "daily_report": daily,
        "rolling_realized": rolling,
        "intraday": intraday,
        "v2": VERSION,
    }


def _compat_mode_from_health(
    daily: Mapping[str, Any],
    intraday: Mapping[str, Any],
    today: str,
) -> Tuple[str, str]:
    # Compatibility for old tests/callers that invoke V1's helper directly.
    return mode_from_health(daily, {}, intraday, today)


def _save_state(payload: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=folder,
            prefix=".profit_survival_v2.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, STATE_FILE)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    # Patch the existing survival module in place so every downstream user
    # (including Candidate Visibility) sees exactly the same health state.
    gate.VERSION = VERSION
    gate.MAX_DAILY_STOPS = MAX_DAILY_STOPS
    gate.MAX_CONSECUTIVE_STOPS = MAX_CONSECUTIVE_STOPS
    gate._mode_from_health = _compat_mode_from_health
    gate._current_health = _current_health_v2


def summary() -> Dict[str, Any]:
    health = _current_health_v2()
    return {
        "version": VERSION,
        "rolling_completed_days": ROLLING_COMPLETED_DAYS,
        "rolling_min_closed": ROLLING_MIN_CLOSED,
        "rolling_recovery_stop_rate": ROLLING_RECOVERY_STOP_RATE,
        "intraday_recovery_stops": INTRADAY_RECOVERY_STOPS,
        "max_daily_stops": MAX_DAILY_STOPS,
        "max_consecutive_stops": MAX_CONSECUTIVE_STOPS,
        "health": health,
        "exchange_orders": False,
        "stop_widening": False,
    }


def finish() -> Dict[str, Any]:
    payload = summary()
    _save_state(payload)
    return payload
