"""Adaptive quality layer for Premium Early Breakout and Regime Transition.

This module does not open exchange orders. It is installed by
premium_quality_profit_runner.py before the normal Premium runner starts.
It adds four live protections learned from recorded outcomes:

- calibrated Early Breakout score (weak flow can no longer look like 100/100),
- same-direction health / stop-cluster protection with time decay,
- entry-timing checks using 5M breakout extension + remaining 15M room,
- fresh Market Outlook context as an additional regime guard.

The existing Premium validation, cost, duplicate, market, portfolio and ledger
layers remain authoritative and run afterwards.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

VERSION = "PREMIUM_QUALITY_LAYER_V1_2026_08_24"
STATE_FILE = "premium_quality_state.json"
PERFORMANCE_FILE = "performance.json"
MARKET_OUTLOOK_FILE = "market_outlook_state.json"

MIN_EARLY_LIVE_SCORE = 91
OUTLOOK_MAX_AGE_SECONDS = 60 * 60
DIRECTION_TIGHT_MAX_AGE_SECONDS = 4 * 60 * 60
DIRECTION_PAUSE_MAX_AGE_SECONDS = 2 * 60 * 60
KEEP_SECONDS = 14 * 24 * 60 * 60
MAX_RECORDS = 1800
TR_TIMEZONE = timezone(timedelta(hours=3))

_STATE: Dict[str, Any] = {}
_STATE_PATH = STATE_FILE
_DIRTY = False


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".premium_quality.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def begin(path: str = STATE_FILE) -> None:
    global _STATE, _STATE_PATH, _DIRTY
    _STATE_PATH = path
    data = _load_json(path)
    if not data:
        data = {"version": VERSION, "updated_at": 0, "records": [], "summary": {}}
    data.setdefault("records", [])
    data.setdefault("summary", {})
    data["version"] = VERSION
    _STATE = data
    _DIRTY = False


def tr_day_key(now_ts: int) -> str:
    return datetime.fromtimestamp(int(now_ts), TR_TIMEZONE).strftime("%Y-%m-%d")


def _clock_age_seconds(clock_text: Any, now_ts: int) -> Optional[int]:
    try:
        hour, minute, second = [int(part) for part in str(clock_text).split(":")]
        now_local = datetime.fromtimestamp(int(now_ts), TR_TIMEZONE)
        event_local = now_local.replace(hour=hour, minute=minute, second=second, microsecond=0)
        age = int((now_local - event_local).total_seconds())
        if age < -60:
            event_local -= timedelta(days=1)
            age = int((now_local - event_local).total_seconds())
        return max(0, age)
    except Exception:
        return None


def _terminal_rows(performance: Dict[str, Any], direction: str, now_ts: int) -> Tuple[list[Dict[str, Any]], Dict[str, Any]]:
    day = ((performance.get("days") or {}).get(tr_day_key(now_ts)) or {})
    history = day.get("closed_history") if isinstance(day.get("closed_history"), list) else []
    terminal_names = {"SL", "BE", "EXPIRED", "TP3"}
    rows = [
        row for row in history
        if isinstance(row, dict)
        and str(row.get("direction") or "").upper() == direction
        and str(row.get("result") or "").upper() in terminal_names
    ]
    return rows, day


def direction_health(
    direction: str,
    now_ts: int,
    performance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return {"mode": "NORMAL", "reason": "NO_DIRECTION", "sample": 0}

    performance = performance if isinstance(performance, dict) else _load_json(PERFORMANCE_FILE)
    rows, day = _terminal_rows(performance, direction, now_ts)
    recent = rows[-8:]
    consecutive_sl = 0
    for row in reversed(rows):
        if str(row.get("result") or "").upper() == "SL":
            consecutive_sl += 1
        else:
            break

    stop_count = sum(1 for row in recent if str(row.get("result") or "").upper() == "SL")
    stop_rate = (stop_count / len(recent) * 100.0) if recent else 0.0
    day_direction_stops = int(((day.get("direction_stops") or {}).get(direction)) or 0)
    last_age = _clock_age_seconds(rows[-1].get("time"), now_ts) if rows else None

    mode = "NORMAL"
    reason = "HEALTHY_OR_INSUFFICIENT_SAMPLE"
    if last_age is not None and last_age <= DIRECTION_PAUSE_MAX_AGE_SECONDS:
        if consecutive_sl >= 4 or (
            len(recent) >= 6 and stop_rate >= 70.0 and day_direction_stops >= 5
        ):
            mode = "PAUSE"
            reason = "RECENT_STOP_CLUSTER"
    if mode == "NORMAL" and last_age is not None and last_age <= DIRECTION_TIGHT_MAX_AGE_SECONDS:
        if consecutive_sl >= 2 or (len(recent) >= 4 and stop_rate >= 45.0) or day_direction_stops >= 4:
            mode = "TIGHT"
            reason = "DIRECTION_EDGE_WEAKENED"

    return {
        "mode": mode,
        "reason": reason,
        "sample": len(recent),
        "consecutive_sl": consecutive_sl,
        "recent_stop_rate_percent": round(stop_rate, 2),
        "day_direction_stops": day_direction_stops,
        "last_terminal_age_seconds": last_age,
    }


def market_outlook_context(
    direction: str,
    now_ts: int,
    outlook: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    direction = str(direction or "").upper()
    wanted = "UP" if direction == "LONG" else "DOWN"
    opposite = "DOWN" if wanted == "UP" else "UP"
    outlook = outlook if isinstance(outlook, dict) else _load_json(MARKET_OUTLOOK_FILE)
    snapshots = outlook.get("snapshots") if isinstance(outlook.get("snapshots"), list) else []
    if not snapshots:
        return {"mode": "UNKNOWN", "reason": "OUTLOOK_YOK", "fresh": False}

    row = snapshots[-1] if isinstance(snapshots[-1], dict) else {}
    at = int(_sf(row.get("ts"), 0) or 0)
    age = max(0, int(now_ts) - at) if at > 0 else None
    if age is None or age > OUTLOOK_MAX_AGE_SECONDS:
        return {
            "mode": "UNKNOWN",
            "reason": "OUTLOOK_ESKI",
            "fresh": False,
            "age_seconds": age,
        }

    data = row.get("outlook") if isinstance(row.get("outlook"), dict) else {}
    d6 = str(data.get("direction_6h") or "").upper()
    d24 = str(data.get("direction_24h") or "").upper()
    c6 = int(_sf(data.get("confidence_6h"), 0) or 0)
    c24 = int(_sf(data.get("confidence_24h"), 0) or 0)
    suitability = int(_sf(data.get("long_suitability" if direction == "LONG" else "short_suitability"), 5) or 0)

    mode = "NORMAL"
    reason = "OUTLOOK_UYUMLU_VEYA_NOTR"
    if d6 == opposite and d24 == opposite and c6 >= 70 and c24 >= 70 and suitability <= 3:
        mode = "BLOCK"
        reason = "6H_24H_GUCLU_TERS"
    elif (
        (d6 == opposite and c6 >= 65)
        or (d24 == opposite and c24 >= 72)
        or suitability <= 3
    ):
        mode = "TIGHT"
        reason = "OUTLOOK_TERS_UYARI"

    return {
        "mode": mode,
        "reason": reason,
        "fresh": True,
        "age_seconds": age,
        "direction_6h": d6,
        "direction_24h": d24,
        "confidence_6h": c6,
        "confidence_24h": c24,
        "suitability": suitability,
    }


def entry_timing_profile(candidate: Dict[str, Any], base_result: Dict[str, Any]) -> Dict[str, Any]:
    direction = str(candidate.get("direction") or base_result.get("direction") or "").upper()
    features = base_result.get("features") if isinstance(base_result.get("features"), dict) else {}
    conditions = base_result.get("conditions") if isinstance(base_result.get("conditions"), dict) else {}
    signal_price = _sf(features.get("signal_price"), _sf(base_result.get("entry"), _sf(candidate.get("entry"), 0.0))) or 0.0
    atr5 = _sf(features.get("atr5"), 0.0) or 0.0
    local_high = _sf(features.get("local_high"), 0.0) or 0.0
    local_low = _sf(features.get("local_low"), 0.0) or 0.0
    room_key = "room_long_r" if direction == "LONG" else "room_short_r"
    room_r = _sf(features.get(room_key))

    extension_atr = None
    if atr5 > 0 and signal_price > 0:
        if direction == "LONG" and local_high > 0:
            extension_atr = max(0.0, (signal_price - local_high) / atr5)
        elif direction == "SHORT" and local_low > 0:
            extension_atr = max(0.0, (local_low - signal_price) / atr5)

    phase = "FRESH_BREAKOUT"
    if room_r is not None and room_r < 0.80:
        phase = "ROOM_TIGHT"
    elif extension_atr is not None and extension_atr > 1.40:
        phase = "BREAKOUT_EXTENDED"

    return {
        "phase": phase,
        "room_r": round(room_r, 4) if room_r is not None else None,
        "break_extension_atr": round(extension_atr, 4) if extension_atr is not None else None,
        "liquidity_sweep": bool(conditions.get("liquidity_sweep")),
        "structure_hold": bool(conditions.get("structure_hold")),
    }


def calibrated_early_score(
    candidate: Dict[str, Any],
    base_result: Dict[str, Any],
    health: Dict[str, Any],
    outlook: Dict[str, Any],
    timing: Dict[str, Any],
) -> int:
    stage = str(candidate.get("early_breakout_stage") or base_result.get("stage") or "").upper()
    base_score = int(_sf(candidate.get("early_breakout_base_score"), _sf(base_result.get("score"), 0)) or 0)
    opposite = int(_sf(candidate.get("early_breakout_opposite_score"), _sf(base_result.get("opposite_score"), 0)) or 0)
    gap = base_score - opposite
    core = int(_sf(candidate.get("early_breakout_core_count"), 0) or 0)
    flow_score = _sf(candidate.get("early_breakout_flow_score"))
    flow_confirmed = bool(candidate.get("early_breakout_flow_confirmed"))
    four_ok = bool(candidate.get("early_breakout_four_hour_ok"))
    exceptional = bool(candidate.get("early_breakout_exceptional"))
    features = base_result.get("features") if isinstance(base_result.get("features"), dict) else {}
    volume = _sf(candidate.get("volume_ratio"), _sf(features.get("volume_ratio"), 0.0)) or 0.0
    wake = _sf(features.get("volume_wake"), 0.0) or 0.0

    score = 78
    score += {"PREP": 0, "ARMED": 3, "TRIGGER": 6}.get(stage, 0)
    score += min(7, max(0, (base_score - 72) // 3))
    score += 3 if gap >= 30 else (2 if gap >= 20 else (1 if gap >= 15 else 0))
    score += 3 if core >= 7 else (2 if core >= 6 else (1 if core >= 5 else 0))
    score += 2 if volume >= 2.0 else (1 if volume >= 1.5 else 0)
    score += 1 if wake >= 1.30 else 0
    score += 2 if four_ok else -2
    score += 7 if exceptional else 0

    if flow_confirmed:
        score += 7
    elif flow_score is None:
        score -= 1
    elif flow_score >= 70:
        score += 4
    elif flow_score >= 55:
        score += 2
    elif flow_score >= 45:
        score += 1
    elif flow_score >= 35:
        score -= 3
    elif flow_score >= 30:
        score -= 6
    else:
        score -= 14

    room_r = _sf(timing.get("room_r"))
    extension_atr = _sf(timing.get("break_extension_atr"))
    if room_r is not None:
        if room_r >= 1.5:
            score += 2
        elif room_r >= 1.0:
            score += 1
        elif room_r < 0.80:
            score -= 2
    if extension_atr is not None and extension_atr > 1.40:
        score -= 2
    if str(health.get("mode")) == "TIGHT":
        score -= 3
    if str(outlook.get("mode")) == "TIGHT":
        score -= 3

    perfect = bool(
        flow_confirmed
        and base_score >= 98
        and stage == "TRIGGER"
        and (room_r is None or room_r >= 1.25)
        and str(health.get("mode")) == "NORMAL"
        and str(outlook.get("mode")) in {"NORMAL", "UNKNOWN"}
    )
    return max(0, min(100 if perfect else 99, int(score)))


def assess_early_candidate(
    candidate: Dict[str, Any],
    base_result: Dict[str, Any],
    now_ts: int,
    *,
    performance: Optional[Dict[str, Any]] = None,
    outlook: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    direction = str(candidate.get("direction") or "").upper()
    health = direction_health(direction, now_ts, performance)
    market = market_outlook_context(direction, now_ts, outlook)
    timing = entry_timing_profile(candidate, base_result)
    flow_score = _sf(candidate.get("early_breakout_flow_score"))
    flow_confirmed = bool(candidate.get("early_breakout_flow_confirmed"))
    risk = _sf(candidate.get("risk_percent"))
    room_r = _sf(timing.get("room_r"))
    extension_atr = _sf(timing.get("break_extension_atr"))

    evidence = {"health": health, "market": market, "timing": timing}

    if health.get("mode") == "PAUSE":
        return None, "DIRECTION_HEALTH_PAUSE", evidence
    if market.get("mode") == "BLOCK":
        return None, "MARKET_OUTLOOK_BLOCK", evidence
    if room_r is not None and room_r < 0.55:
        return None, "15M_ROOM_YETERSIZ", evidence
    if extension_atr is not None and extension_atr > 2.25:
        return None, "BREAKOUT_FAZLA_UZAMIS", evidence
    if (
        extension_atr is not None
        and extension_atr > 1.65
        and not flow_confirmed
        and (flow_score is None or flow_score < 50)
    ):
        return None, "UZAMIS_BREAKOUT_FLOW_YETERSIZ", evidence
    if (
        health.get("mode") == "TIGHT"
        and risk is not None
        and risk < 0.65
        and not timing.get("liquidity_sweep")
        and not flow_confirmed
    ):
        return None, "TIGHT_STOP_TEYITSIZ", evidence

    score = calibrated_early_score(candidate, base_result, health, market, timing)
    evidence["calibrated_score"] = score
    if score < MIN_EARLY_LIVE_SCORE:
        return None, "CALIBRATED_SCORE_LOW", evidence

    result = dict(candidate)
    result["score"] = score
    result["quality"] = "A+ ERKEN BREAKOUT" if score >= 97 else ("A ERKEN BREAKOUT" if score >= 93 else "A- ERKEN BREAKOUT")
    result["quality_layer_version"] = VERSION
    result["direction_health"] = health
    result["market_outlook_quality"] = market
    result["entry_timing_quality"] = timing
    result["entry_reason"] = (
        str(result.get("entry_reason") or "").rstrip(" .")
        + f"; faz={timing.get('phase')}"
    )
    return result, "ALLOW", evidence


def assess_regime_candidate(
    candidate: Dict[str, Any],
    now_ts: int,
    *,
    performance: Optional[Dict[str, Any]] = None,
    outlook: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    direction = str(candidate.get("direction") or "").upper()
    mode = str(candidate.get("regime_transition_mode") or "").upper()
    score = int(_sf(candidate.get("score"), 0) or 0)
    health = direction_health(direction, now_ts, performance)
    market = market_outlook_context(direction, now_ts, outlook)
    evidence = {"health": health, "market": market, "regime_mode": mode}

    # A true reversal route must be able to escape the old direction bias. For
    # that specific case, a stale direction cluster/outlook becomes TIGHT rather
    # than an absolute veto; the regime module's own 4H/1H origin rules remain.
    is_reversal = "REVERSAL" in mode
    if health.get("mode") == "PAUSE" and not (is_reversal and score >= 98):
        return None, "REGIME_DIRECTION_HEALTH_PAUSE", evidence
    if market.get("mode") == "BLOCK" and not is_reversal:
        return None, "REGIME_MARKET_OUTLOOK_BLOCK", evidence

    result = dict(candidate)
    penalty = 0
    if health.get("mode") in {"TIGHT", "PAUSE"}:
        penalty += 2
    if market.get("mode") in {"TIGHT", "BLOCK"}:
        penalty += 2
    if penalty:
        result["score"] = max(0, score - penalty)
    result["quality_layer_version"] = VERSION
    result["direction_health"] = health
    result["market_outlook_quality"] = market
    return result, "ALLOW", evidence


def _record(route: str, candidate: Optional[Dict[str, Any]], decision: str, reason: str, evidence: Dict[str, Any], now_ts: int) -> None:
    global _DIRTY
    if not _STATE:
        begin()
    rows = _STATE.setdefault("records", [])
    rows.append({
        "at": int(now_ts),
        "route": route,
        "symbol": (candidate or {}).get("symbol"),
        "direction": (candidate or {}).get("direction"),
        "decision": decision,
        "reason": reason,
        "score": (candidate or {}).get("score"),
        "health": evidence.get("health"),
        "market": evidence.get("market"),
        "timing": evidence.get("timing"),
        "calibrated_score": evidence.get("calibrated_score"),
    })
    cutoff = int(now_ts) - KEEP_SECONDS
    rows[:] = [row for row in rows if int(row.get("at") or 0) >= cutoff][-MAX_RECORDS:]
    _DIRTY = True


def install(early_module: Any, regime_module: Any) -> None:
    if not getattr(early_module.analyze_live_candidate, "_premium_quality_wrapped", False):
        original_early = early_module.analyze_live_candidate

        def early_wrapped(*args: Any, **kwargs: Any):
            candidate = original_early(*args, **kwargs)
            if not isinstance(candidate, dict):
                return candidate
            base_result = args[1] if len(args) > 1 and isinstance(args[1], dict) else kwargs.get("base_result") or {}
            now_ts = int(kwargs.get("now_ts") or time.time())
            allowed, reason, evidence = assess_early_candidate(candidate, base_result, now_ts)
            _record("EARLY_BREAKOUT", candidate, "ALLOW" if allowed else "REJECT", reason, evidence, now_ts)
            if allowed is None:
                print(candidate.get("symbol"), "PREMIUM QUALITY EARLY RED:", reason)
            elif int(allowed.get("score") or 0) != int(candidate.get("score") or 0):
                print(candidate.get("symbol"), "PREMIUM QUALITY SCORE:", candidate.get("score"), "->", allowed.get("score"))
            return allowed

        early_wrapped._premium_quality_wrapped = True  # type: ignore[attr-defined]
        early_module.analyze_live_candidate = early_wrapped

    if not getattr(regime_module.analyze_live_candidate, "_premium_quality_wrapped", False):
        original_regime = regime_module.analyze_live_candidate

        def regime_wrapped(*args: Any, **kwargs: Any):
            candidate = original_regime(*args, **kwargs)
            if not isinstance(candidate, dict):
                return candidate
            now_ts = int(kwargs.get("now_ts") or time.time())
            allowed, reason, evidence = assess_regime_candidate(candidate, now_ts)
            _record("REGIME_TRANSITION", candidate, "ALLOW" if allowed else "REJECT", reason, evidence, now_ts)
            if allowed is None:
                print(candidate.get("symbol"), "PREMIUM QUALITY REGIME RED:", reason)
            return allowed

        regime_wrapped._premium_quality_wrapped = True  # type: ignore[attr-defined]
        regime_module.analyze_live_candidate = regime_wrapped


def finish() -> Dict[str, Any]:
    global _DIRTY
    if not _STATE:
        begin()
    rows = _STATE.get("records") if isinstance(_STATE.get("records"), list) else []
    decisions = Counter(str(row.get("decision") or "UNKNOWN") for row in rows)
    reasons = Counter(str(row.get("reason") or "UNKNOWN") for row in rows if row.get("decision") == "REJECT")
    routes = Counter(str(row.get("route") or "UNKNOWN") for row in rows if row.get("decision") == "ALLOW")
    summary = {
        "version": VERSION,
        "records": len(rows),
        "allowed": decisions.get("ALLOW", 0),
        "rejected": decisions.get("REJECT", 0),
        "allowed_by_route": dict(routes),
        "top_reject_reasons": dict(reasons.most_common(12)),
    }
    _STATE["summary"] = summary
    _STATE["updated_at"] = int(time.time())
    _STATE["version"] = VERSION
    if _DIRTY or not os.path.exists(_STATE_PATH):
        _atomic_save(_STATE_PATH, _STATE)
        _DIRTY = False
    return summary
