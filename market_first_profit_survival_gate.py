"""Adaptive capital-survival gate for Market First live trades.

This module is the final capital-protection layer. It never creates a trade,
widens a stop, changes exchange orders, or bypasses existing quality gates.
Instead it learns from the latest real-trade outcome summary and today's live
stop count, then tightens the *last* Telegram trade decision when real results
are poor.

Modes:
- NORMAL: existing pipeline decides.
- RECOVERY_STRICT: after a statistically bad recent live cohort, only A++ aligned
  setups survive; relaxed Shadow Edge promotions are disabled temporarily.
- HALT: 3 consecutive same-day stops or 4 total same-day stops blocks new live
  entries for the rest of the local trading day. Tracking/results keep running.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Tuple

import market_first_runner as runner

VERSION = "MARKET_FIRST_PROFIT_SURVIVAL_GATE_V1_2026_09_13"
STATE_FILE = "market_first_profit_survival_gate.json"
DAILY_REPORT_FILE = "market_first_daily_report.json"
PERFORMANCE_FILE = "performance.json"
MODE = "FINAL_CAPITAL_PROTECTION_NO_PROMOTION_NO_ORDERS"

# Recent realised-health thresholds.
MIN_RECENT_CLOSED = 6
RECOVERY_STOP_RATE = 0.55
CRITICAL_STOP_RATE = 0.70

# Intraday hard circuit breakers (Europe/Istanbul local day).
MAX_DAILY_STOPS = 4
MAX_CONSECUTIVE_STOPS = 3

# A++ requirements used only while recovering from a poor real cohort.
RECOVERY_MIN_SCORE = 96
RECOVERY_MAX_RISK_PERCENT = 0.70
RECOVERY_MIN_TARGET_R = 2.75
RECOVERY_MIN_EXPECTED_MOVE_PERCENT = 1.75
RECOVERY_MIN_CONFIRMATIONS = 4
RECOVERY_MIN_FLOW_ALIGNMENT = 0.25
RECOVERY_MIN_DERIVATIVES_SOFT_SCORE = 1
RECOVERY_COUNTERTREND_MIN_VOLUME_1M = 2.50
RECOVERY_COUNTERTREND_MIN_MOVE_5M = 0.20

_INSTALLED = False
_RUN_COUNTS: Counter = Counter()
_RUN_ACCEPTED: list[Dict[str, Any]] = []
_LAST_HEALTH: Dict[str, Any] = {}


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


def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data
    except Exception:
        return default


def _istanbul_today() -> str:
    return datetime.now(timezone(timedelta(hours=3))).date().isoformat()


def _classify_real_result(value: Any) -> str:
    text = str(value or "").upper().strip()
    if not text or "AÇIK" in text or "OPEN" in text:
        return "OPEN"
    if "STOP" in text or text == "SL" or text.startswith("SL "):
        return "LOSS"
    if "TP" in text:
        return "WIN"
    if "BE" in text:
        return "NEUTRAL"
    return "OTHER"


def _daily_report_health(payload: Mapping[str, Any]) -> Dict[str, Any]:
    rows = payload.get("real_trades")
    if not isinstance(rows, list):
        rows = []
    wins = losses = neutral = other = open_count = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        kind = _classify_real_result(row.get("result"))
        if kind == "WIN":
            wins += 1
        elif kind == "LOSS":
            losses += 1
        elif kind == "NEUTRAL":
            neutral += 1
        elif kind == "OPEN":
            open_count += 1
        else:
            other += 1
    closed_directional = wins + losses
    stop_rate = losses / closed_directional if closed_directional else 0.0
    win_rate = wins / closed_directional if closed_directional else 0.0
    return {
        "date": str(payload.get("date") or ""),
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "other": other,
        "open": open_count,
        "closed_directional": closed_directional,
        "stop_rate": round(stop_rate, 6),
        "win_rate": round(win_rate, 6),
    }


def _intraday_health(payload: Mapping[str, Any], today: str) -> Dict[str, Any]:
    days = payload.get("days")
    day = days.get(today) if isinstance(days, Mapping) else None
    if not isinstance(day, Mapping):
        return {
            "date": today,
            "opened": 0,
            "sl": 0,
            "tp1": 0,
            "tp2": 0,
            "tp3": 0,
            "consecutive_stops": 0,
        }

    consecutive = 0
    history = day.get("closed_history")
    if isinstance(history, list):
        for row in reversed(history):
            if not isinstance(row, Mapping):
                continue
            result = str(row.get("result") or "").upper().strip()
            # Ignore milestone events; only settlement-like SL/BE breaks the streak.
            if result == "SL" or "STOP" in result:
                consecutive += 1
                continue
            if result in {"BE", "EXPIRED", "TIMEOUT"}:
                break

    return {
        "date": today,
        "opened": _si(day.get("opened")),
        "sl": _si(day.get("sl")),
        "tp1": _si(day.get("tp1")),
        "tp2": _si(day.get("tp2")),
        "tp3": _si(day.get("tp3")),
        "consecutive_stops": consecutive,
    }


def _report_is_recent(report_date: str, today: str) -> bool:
    try:
        left = datetime.fromisoformat(report_date).date()
        right = datetime.fromisoformat(today).date()
        delta = (right - left).days
        return 0 <= delta <= 2
    except Exception:
        return False


def _mode_from_health(daily: Mapping[str, Any], intraday: Mapping[str, Any], today: str) -> Tuple[str, str]:
    if _si(intraday.get("sl")) >= MAX_DAILY_STOPS:
        return "HALT", "DAILY_STOP_LIMIT"
    if _si(intraday.get("consecutive_stops")) >= MAX_CONSECUTIVE_STOPS:
        return "HALT", "CONSECUTIVE_STOP_LIMIT"

    closed = _si(daily.get("closed_directional"))
    stop_rate = _sf(daily.get("stop_rate"))
    recent = _report_is_recent(str(daily.get("date") or ""), today)
    if recent and closed >= MIN_RECENT_CLOSED and stop_rate >= CRITICAL_STOP_RATE:
        return "RECOVERY_STRICT", "CRITICAL_RECENT_STOP_RATE"
    if recent and closed >= MIN_RECENT_CLOSED and stop_rate >= RECOVERY_STOP_RATE:
        return "RECOVERY_STRICT", "POOR_RECENT_STOP_RATE"
    return "NORMAL", "OK"


def _engine(decision: Mapping[str, Any]) -> Mapping[str, Any]:
    value = decision.get("direction_engine")
    return value if isinstance(value, Mapping) else {}


def _direction_block(decision: Mapping[str, Any], direction: str) -> Mapping[str, Any]:
    block = _engine(decision).get(direction.lower())
    return block if isinstance(block, Mapping) else {}


def _aligned_move(direction: str, value: Any) -> float:
    raw = _sf(value)
    return raw if direction == "LONG" else -raw


def _recovery_reason(decision: Mapping[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION", {}

    score = _si(decision.get("score"))
    risk = _sf(decision.get("risk_percent"), 99.0)
    expected = max(
        _sf(decision.get("expected_move_percent")),
        _sf(decision.get("profit_target_percent")),
    )
    target_r = max(
        _sf(decision.get("profit_target_r")),
        _sf(decision.get("technical_target_r")),
        _sf(decision.get("room_r")),
    )
    engine = _engine(decision)
    confirmations = _si(engine.get("confirmations"))
    reversal = bool(engine.get("reversal"))

    structures = engine.get("structures") if isinstance(engine.get("structures"), Mapping) else {}
    s5 = str(structures.get("5m") or decision.get("structure_5m") or "").upper()
    s15 = str(structures.get("15m") or decision.get("structure_15m") or "").upper()
    s1h = str(structures.get("1h") or decision.get("structure_1h") or "").upper()
    structures_aligned = all(value == direction for value in (s5, s15, s1h))

    block = _direction_block(decision, direction)
    taker = _sf(block.get("taker_alignment"), _sf(decision.get("taker_imbalance_alignment")))
    cvd = _sf(block.get("cvd_alignment"), _sf(decision.get("cvd_ratio")))
    cvd_impulse = _sf(decision.get("cvd_impulse_alignment"))
    flow_strong = taker >= RECOVERY_MIN_FLOW_ALIGNMENT and cvd >= RECOVERY_MIN_FLOW_ALIGNMENT and cvd_impulse >= 0.0

    flags = engine.get("confirmation_flags") if isinstance(engine.get("confirmation_flags"), Mapping) else {}
    fresh_micro = bool(flags.get("fresh_micro"))
    if not fresh_micro:
        move3 = _aligned_move(direction, decision.get("move_3m_percent"))
        move5 = _aligned_move(direction, decision.get("move_5m_percent"))
        fresh_micro = (move3 >= 0.10 and move5 >= 0.15) or (bool(decision.get("breakout_20m")) and move5 >= 0.10)

    preferred = str(decision.get("market_preferred_direction") or "").upper()
    market_aligned = not preferred or preferred == direction
    if not market_aligned:
        independent = bool(decision.get("independent_move"))
        breakout = bool(decision.get("breakout_20m"))
        volume1 = _sf(decision.get("volume_ratio_1m"))
        move5 = _aligned_move(direction, decision.get("move_5m_percent"))
        market_aligned = independent and breakout and volume1 >= RECOVERY_COUNTERTREND_MIN_VOLUME_1M and move5 >= RECOVERY_COUNTERTREND_MIN_MOVE_5M

    evidence = {
        "score": score,
        "risk_percent": round(risk, 4),
        "expected_move_percent": round(expected, 4),
        "target_r": round(target_r, 4),
        "confirmations": confirmations,
        "structures_aligned": structures_aligned,
        "fresh_micro": fresh_micro,
        "taker_alignment": round(taker, 4),
        "cvd_alignment": round(cvd, 4),
        "cvd_impulse_alignment": round(cvd_impulse, 4),
        "flow_strong": flow_strong,
        "market_aligned": market_aligned,
        "reversal": reversal,
        "shadow_edge_relaxed": bool(decision.get("shadow_edge_relaxed_profit_gate")),
        "derivatives_soft_score": _si(decision.get("derivatives_soft_score")),
    }

    if bool(decision.get("shadow_edge_relaxed_profit_gate")):
        return False, "RECOVERY_NO_RELAXED_SHADOW_EDGE", evidence
    if score < RECOVERY_MIN_SCORE:
        return False, "RECOVERY_SCORE", evidence
    if risk <= 0 or risk > RECOVERY_MAX_RISK_PERCENT:
        return False, "RECOVERY_RISK", evidence
    if expected < RECOVERY_MIN_EXPECTED_MOVE_PERCENT:
        return False, "RECOVERY_EXPECTED_MOVE", evidence
    if target_r < RECOVERY_MIN_TARGET_R:
        return False, "RECOVERY_TARGET_R", evidence
    if reversal:
        return False, "RECOVERY_REVERSAL", evidence
    if confirmations < RECOVERY_MIN_CONFIRMATIONS:
        return False, "RECOVERY_CONFIRMATIONS", evidence
    if not structures_aligned:
        return False, "RECOVERY_STRUCTURE", evidence
    if not market_aligned:
        return False, "RECOVERY_MARKET_OPPOSED", evidence
    if not (fresh_micro or flow_strong):
        return False, "RECOVERY_NO_FRESH_OR_FLOW", evidence
    if _si(decision.get("derivatives_soft_score")) < RECOVERY_MIN_DERIVATIVES_SOFT_SCORE:
        return False, "RECOVERY_DERIVATIVES", evidence
    return True, "OK", evidence


def _current_health() -> Dict[str, Any]:
    today = _istanbul_today()
    daily_payload = _load_json(DAILY_REPORT_FILE, {})
    perf_payload = _load_json(PERFORMANCE_FILE, {})
    daily = _daily_report_health(daily_payload if isinstance(daily_payload, Mapping) else {})
    intraday = _intraday_health(perf_payload if isinstance(perf_payload, Mapping) else {}, today)
    mode, reason = _mode_from_health(daily, intraday, today)
    return {
        "today": today,
        "mode": mode,
        "reason": reason,
        "daily_report": daily,
        "intraday": intraday,
    }


def _atomic_save(payload: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=folder, prefix=".profit_survival.", suffix=".tmp", delete=False) as handle:
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
    global _INSTALLED, _LAST_HEALTH
    if _INSTALLED:
        return
    _INSTALLED = True
    _LAST_HEALTH = _current_health()

    original = runner.decision_to_signal

    def decision_to_signal_profit_survival(decision):
        signal = original(decision)
        if not isinstance(signal, Mapping):
            _RUN_COUNTS["UPSTREAM_REJECTED"] += 1
            return signal
        if not isinstance(decision, Mapping):
            return signal

        # Refresh health for every would-be real signal so same-day stop brakes
        # take effect without restarting the workflow job.
        health = _current_health()
        _LAST_HEALTH.clear()
        _LAST_HEALTH.update(health)
        gate_mode = str(health.get("mode") or "NORMAL")

        if gate_mode == "HALT":
            reason = str(health.get("reason") or "HALT")
            _RUN_COUNTS[reason] += 1
            print("PROFIT SURVIVAL HALT:", decision.get("symbol"), reason, health.get("intraday"))
            return None

        evidence: Dict[str, Any] = {}
        if gate_mode == "RECOVERY_STRICT":
            ok, reason, evidence = _recovery_reason(decision)
            _RUN_COUNTS[reason] += 1
            if not ok:
                print("PROFIT SURVIVAL ELENDİ:", decision.get("symbol"), reason, evidence)
                return None
        else:
            _RUN_COUNTS["NORMAL_PASS"] += 1

        out = dict(signal)
        out["profit_survival_gate_version"] = VERSION
        out["profit_survival_mode"] = gate_mode
        out["profit_survival_evidence"] = evidence
        _RUN_COUNTS["ACCEPTED"] += 1
        _RUN_ACCEPTED.append({
            "symbol": out.get("symbol"),
            "direction": out.get("direction"),
            "score": out.get("score"),
            "mode": gate_mode,
            **evidence,
        })
        return out

    runner.decision_to_signal = decision_to_signal_profit_survival


def finish() -> Dict[str, Any]:
    global _LAST_HEALTH
    _LAST_HEALTH = _current_health()
    payload = {
        "version": VERSION,
        "mode": MODE,
        "health": _LAST_HEALTH,
        "thresholds": {
            "min_recent_closed": MIN_RECENT_CLOSED,
            "recovery_stop_rate": RECOVERY_STOP_RATE,
            "critical_stop_rate": CRITICAL_STOP_RATE,
            "max_daily_stops": MAX_DAILY_STOPS,
            "max_consecutive_stops": MAX_CONSECUTIVE_STOPS,
            "recovery_min_score": RECOVERY_MIN_SCORE,
            "recovery_max_risk_percent": RECOVERY_MAX_RISK_PERCENT,
            "recovery_min_target_r": RECOVERY_MIN_TARGET_R,
            "recovery_min_expected_move_percent": RECOVERY_MIN_EXPECTED_MOVE_PERCENT,
            "recovery_min_confirmations": RECOVERY_MIN_CONFIRMATIONS,
            "recovery_min_flow_alignment": RECOVERY_MIN_FLOW_ALIGNMENT,
        },
        "run_counts": dict(_RUN_COUNTS),
        "accepted": _RUN_ACCEPTED[-20:],
        "note": "Capital-protection gate only; no profit guarantee, no order placement, no stop widening.",
    }
    _atomic_save(payload)
    return payload


def summary() -> Dict[str, Any]:
    health = _LAST_HEALTH or _current_health()
    return {
        "version": VERSION,
        "mode": MODE,
        "health": health,
        "run_counts": dict(_RUN_COUNTS),
        "exchange_orders": False,
        "stop_widening": False,
    }
