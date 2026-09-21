"""Adaptive bridge from background evidence to real Market First ENTRY decisions.

This module is the missing link between the system's shadow/background learning
and the live trade pipeline. Historical/background evidence is allowed to change
a PREP plan into an ENTRY plan only when that evidence is statistically strong
and the current setup is A++ quality.

V2 aligns this bridge with the V6 Balanced Core live architecture. The retired
Profit Survival/Recovery mode is no longer allowed to veto every background
promotion before the current quality/execution gates can inspect it. The legacy
survival_health argument is retained only as observational metadata so older
tests/callers remain compatible.

It never sends Telegram directly, never places exchange orders, never widens a
stop and never bypasses downstream Profit Quality, derivatives/order-flow, ML,
Final Execution, duplicate, cooldown, portfolio or Balanced Core guards.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
from typing import Any, Dict, Mapping, Tuple

import market_first_entry_plan as entry_plan

VERSION = "MARKET_FIRST_BACKGROUND_LIVE_BRIDGE_V2_2026_09_22"
STATE_FILE = "market_first_background_live_bridge.json"
HISTORY_FILE = "market_first_background_edge_history.json"
DAILY_FILE = "market_first_daily_report.json"
OPPORTUNITY_FILE = "market_first_opportunity_summary.json"
SWING_FILE = "market_first_swing_2h_summary.json"

MAX_HISTORY_EPISODES = 3000
MIN_RECENT_ENTRY_PLAN_SAMPLES = 12
MIN_RECENT_ENTRY_PLAN_RATE = 0.68
MIN_LONG_RUN_ENTRY_SAMPLES = 500
MIN_LONG_RUN_ENTRY_RATE = 0.67
MIN_SWING_SAMPLES = 180
MIN_SWING_RATE = 0.56
MIN_DIRECTIONAL_RECENT_SAMPLES = 6
MIN_DIRECTIONAL_RECENT_RATE = 0.58

PROMOTE_MIN_SCORE = 92
PROMOTE_MAX_ZONE_DISTANCE_PERCENT = 1.10
PROMOTE_MIN_VOLUME_RATIO_5M = 1.10
PROMOTE_MIN_VOLUME_RATIO_15M = 0.90
PROMOTE_MAX_RISK_PERCENT = 0.65
PROMOTE_MIN_ROOM_R = 3.00
PROMOTE_MAX_EXTENSION_ATR = 1.15

_INSTALLED = False
_ORIGINAL_EVALUATE = None
_PROFILE: Dict[str, Any] = {}
_RUN_COUNTS: Counter = Counter()
_RUN_PROMOTED: list[Dict[str, Any]] = []


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


def _load(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def _atomic_save(path: str, payload: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=folder,
            prefix=".background_live_bridge.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
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


def refresh_history() -> Dict[str, Any]:
    history = _load(HISTORY_FILE, {"version": VERSION, "episodes": {}})
    if not isinstance(history, dict):
        history = {"version": VERSION, "episodes": {}}
    episodes = history.setdefault("episodes", {})
    if not isinstance(episodes, dict):
        episodes = {}
        history["episodes"] = episodes

    daily = _load(DAILY_FILE, {})
    rows = daily.get("background") if isinstance(daily, Mapping) else []
    rows = rows if isinstance(rows, list) else []
    added = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        first_touch = str(row.get("first_touch") or "").upper()
        if first_touch not in {"TP_FIRST", "SL_FIRST"}:
            continue
        episode_id = str(row.get("episode_id") or "").strip()
        if not episode_id or episode_id in episodes:
            continue
        sources = row.get("sources") if isinstance(row.get("sources"), list) else []
        sources = sorted({str(item).upper() for item in sources if str(item).strip()})
        episodes[episode_id] = {
            "episode_id": episode_id,
            "date": str(row.get("origin_date") or daily.get("date") or ""),
            "symbol": row.get("symbol"),
            "direction": str(row.get("direction") or "").upper(),
            "sources": sources,
            "first_touch": first_touch,
            "tp_first": first_touch == "TP_FIRST",
            "opposite_direction_seen": bool(row.get("opposite_direction_seen")),
            "favorable_percent": round(_sf(row.get("favorable_percent")), 4),
            "adverse_percent": round(_sf(row.get("adverse_percent")), 4),
        }
        added += 1

    if len(episodes) > MAX_HISTORY_EPISODES:
        keys = list(episodes.keys())[-MAX_HISTORY_EPISODES:]
        history["episodes"] = {key: episodes[key] for key in keys}
        episodes = history["episodes"]

    history["version"] = VERSION
    history["last_daily_date"] = daily.get("date") if isinstance(daily, Mapping) else None
    history["episode_count"] = len(episodes)
    history["added_last_refresh"] = added
    _atomic_save(HISTORY_FILE, history)
    return history


def _cohort(rows: list[Mapping[str, Any]]) -> Dict[str, Any]:
    wins = sum(1 for row in rows if bool(row.get("tp_first")))
    losses = sum(1 for row in rows if str(row.get("first_touch") or "").upper() == "SL_FIRST")
    total = wins + losses
    rate = wins / total if total else 0.0
    return {
        "samples": total,
        "tp_first": wins,
        "sl_first": losses,
        "tp_first_rate": round(rate, 4),
    }


def build_profile(
    history: Mapping[str, Any] | None = None,
    opportunity: Mapping[str, Any] | None = None,
    swing: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    history = history if isinstance(history, Mapping) else {}
    opportunity = opportunity if isinstance(opportunity, Mapping) else {}
    swing = swing if isinstance(swing, Mapping) else {}

    episodes = history.get("episodes") if isinstance(history.get("episodes"), Mapping) else {}
    rows = [row for row in episodes.values() if isinstance(row, Mapping)]
    entry_rows = [row for row in rows if "ENTRY_PLAN" in (row.get("sources") or [])]
    clean_entry_rows = [row for row in entry_rows if not bool(row.get("opposite_direction_seen"))]
    entry_swing_rows = [
        row for row in entry_rows
        if "SWING_2H" in (row.get("sources") or [])
    ]

    recent_entry = _cohort(entry_rows[-250:])
    recent_clean = _cohort(clean_entry_rows[-250:])
    recent_entry_swing = _cohort(entry_swing_rows[-250:])
    direction = {
        side: _cohort([row for row in entry_rows[-300:] if str(row.get("direction") or "").upper() == side])
        for side in ("LONG", "SHORT")
    }

    clean = opportunity.get("entry_plan_clean") if isinstance(opportunity.get("entry_plan_clean"), Mapping) else {}
    long_wins = _si(clean.get("tp1_first"))
    long_losses = _si(clean.get("sl_first"))
    long_total = long_wins + long_losses
    long_rate = long_wins / long_total if long_total else 0.0

    swing_wins = _si(swing.get("v2_tp1_first"))
    swing_losses = _si(swing.get("v2_sl_first"))
    swing_total = swing_wins + swing_losses
    swing_rate = swing_wins / swing_total if swing_total else 0.0

    recent_ok = (
        recent_entry["samples"] >= MIN_RECENT_ENTRY_PLAN_SAMPLES
        and recent_entry["tp_first_rate"] >= MIN_RECENT_ENTRY_PLAN_RATE
    )
    long_run_ok = long_total >= MIN_LONG_RUN_ENTRY_SAMPLES and long_rate >= MIN_LONG_RUN_ENTRY_RATE
    swing_ok = swing_total >= MIN_SWING_SAMPLES and swing_rate >= MIN_SWING_RATE
    enabled = bool(recent_ok and long_run_ok and swing_ok)

    return {
        "version": VERSION,
        "enabled": enabled,
        "recent_entry_plan": recent_entry,
        "recent_clean_entry_plan": recent_clean,
        "recent_entry_plus_swing": recent_entry_swing,
        "direction": direction,
        "long_run_entry_plan": {
            "samples": long_total,
            "tp_first_rate": round(long_rate, 4),
        },
        "swing_v2": {
            "samples": swing_total,
            "tp_first_rate": round(swing_rate, 4),
        },
        "guards": {
            "recent_ok": recent_ok,
            "long_run_ok": long_run_ok,
            "swing_ok": swing_ok,
        },
    }


def load_profile() -> Dict[str, Any]:
    history = refresh_history()
    return build_profile(
        history,
        _load(OPPORTUNITY_FILE, {}),
        _load(SWING_FILE, {}),
    )


def background_promotion_qualifies(
    plan: Mapping[str, Any] | None,
    profile: Mapping[str, Any] | None = None,
    survival_health: Mapping[str, Any] | None = None,
) -> Tuple[bool, Dict[str, Any]]:
    profile = profile if isinstance(profile, Mapping) else _PROFILE
    if not isinstance(plan, Mapping):
        return False, {"reason": "NO_PLAN"}
    if str(plan.get("status") or "").upper() != "PREP":
        return False, {"reason": "NOT_PREP"}
    if not bool(profile.get("enabled")):
        return False, {"reason": "BACKGROUND_EDGE_NOT_VALIDATED"}

    # V6 Balanced Core deliberately retired the old Survival/Recovery veto stack.
    # Keeping that legacy mode as a hard prerequisite here made the bridge inert:
    # PREP candidates were rejected before the current Profit Quality + Final
    # Execution + Balanced Core gates could inspect them. Retain an explicitly
    # supplied legacy health object only as diagnostic evidence; it is not a V6
    # admission gate.
    legacy_health = survival_health if isinstance(survival_health, Mapping) else {}
    legacy_mode = str(legacy_health.get("mode") or "").upper()

    direction = str(plan.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, {"reason": "DIRECTION"}

    directional = (profile.get("direction") or {}).get(direction, {})
    if (
        _si(directional.get("samples")) >= MIN_DIRECTIONAL_RECENT_SAMPLES
        and _sf(directional.get("tp_first_rate")) < MIN_DIRECTIONAL_RECENT_RATE
    ):
        return False, {
            "reason": "DIRECTIONAL_BACKGROUND_EDGE_WEAK",
            "direction_samples": _si(directional.get("samples")),
            "direction_rate": _sf(directional.get("tp_first_rate")),
        }

    structures = tuple(str(plan.get(f"structure_{tf}") or "").upper() for tf in ("5m", "15m", "1h"))
    if any(value != direction for value in structures):
        return False, {"reason": "MTF_NOT_FULLY_ALIGNED", "structures": structures}

    preferred = str(plan.get("market_preferred_direction") or "").upper()
    if preferred and preferred != direction:
        return False, {"reason": "MARKET_DIRECTION_NOT_ALIGNED", "preferred": preferred}

    evidence = {
        "score": _si(plan.get("score")),
        "zone_distance_percent": round(_sf(plan.get("zone_distance_percent"), 999.0), 4),
        "volume_ratio_5m": round(_sf(plan.get("volume_ratio_5m")), 3),
        "volume_ratio_15m": round(_sf(plan.get("volume_ratio_15m")), 3),
        "risk_percent": round(_sf(plan.get("risk_percent"), 999.0), 4),
        "room_r": round(_sf(plan.get("room_r")), 3),
        "extension_atr_5m": round(_sf(plan.get("extension_atr_5m"), 999.0), 3),
        "recent_entry_plan_rate": _sf((profile.get("recent_entry_plan") or {}).get("tp_first_rate")),
        "long_run_entry_plan_rate": _sf((profile.get("long_run_entry_plan") or {}).get("tp_first_rate")),
        "swing_v2_rate": _sf((profile.get("swing_v2") or {}).get("tp_first_rate")),
        "direction_rate": _sf(directional.get("tp_first_rate")),
        "direction_samples": _si(directional.get("samples")),
        "live_admission_mode": "V6_BALANCED_CORE",
        "legacy_survival_mode_observed": legacy_mode or None,
        "legacy_survival_reason_observed": legacy_health.get("reason"),
    }

    checks = (
        (evidence["score"] >= PROMOTE_MIN_SCORE, "SCORE"),
        (evidence["zone_distance_percent"] <= PROMOTE_MAX_ZONE_DISTANCE_PERCENT, "ZONE_DISTANCE"),
        (evidence["volume_ratio_5m"] >= PROMOTE_MIN_VOLUME_RATIO_5M, "VOLUME_5M"),
        (evidence["volume_ratio_15m"] >= PROMOTE_MIN_VOLUME_RATIO_15M, "VOLUME_15M"),
        (0 < evidence["risk_percent"] <= PROMOTE_MAX_RISK_PERCENT, "RISK"),
        (evidence["room_r"] >= PROMOTE_MIN_ROOM_R, "ROOM"),
        (evidence["extension_atr_5m"] <= PROMOTE_MAX_EXTENSION_ATR, "EXTENSION"),
    )
    for ok, reason in checks:
        if not ok:
            return False, {"reason": reason, **evidence}
    return True, {"reason": "BACKGROUND_EDGE_A_PLUS_PLUS", **evidence}


def install() -> None:
    global _INSTALLED, _ORIGINAL_EVALUATE, _PROFILE
    if _INSTALLED:
        return
    _INSTALLED = True
    _PROFILE = load_profile()
    _ORIGINAL_EVALUATE = entry_plan.evaluate_entry_plan

    def evaluate_with_background_live_bridge(*args: Any, **kwargs: Any):
        plan, reason = _ORIGINAL_EVALUATE(*args, **kwargs)
        if not isinstance(plan, Mapping):
            return plan, reason
        if str(plan.get("status") or "").upper() == "ENTRY":
            _RUN_COUNTS["UPSTREAM_ENTRY"] += 1
            return plan, reason

        ok, evidence = background_promotion_qualifies(plan, _PROFILE)
        _RUN_COUNTS[str(evidence.get("reason") or "UNKNOWN")] += 1
        if not ok:
            return plan, reason

        promoted = dict(plan)
        promoted["status"] = "ENTRY"
        promoted["background_live_bridge"] = True
        promoted["background_live_bridge_version"] = VERSION
        promoted["background_live_bridge_evidence"] = evidence
        _RUN_COUNTS["PROMOTED_TO_ENTRY"] += 1
        _RUN_PROMOTED.append({
            "symbol": promoted.get("symbol"),
            "direction": promoted.get("direction"),
            "score": promoted.get("score"),
            **evidence,
        })
        print("BACKGROUND -> LIVE ENTRY:", promoted.get("symbol"), promoted.get("direction"), evidence)
        return promoted, reason

    entry_plan.evaluate_entry_plan = evaluate_with_background_live_bridge


def finish() -> Dict[str, Any]:
    payload = {
        "version": VERSION,
        "profile": dict(_PROFILE),
        "thresholds": {
            "min_recent_entry_plan_samples": MIN_RECENT_ENTRY_PLAN_SAMPLES,
            "min_recent_entry_plan_rate": MIN_RECENT_ENTRY_PLAN_RATE,
            "min_long_run_entry_samples": MIN_LONG_RUN_ENTRY_SAMPLES,
            "min_long_run_entry_rate": MIN_LONG_RUN_ENTRY_RATE,
            "min_swing_samples": MIN_SWING_SAMPLES,
            "min_swing_rate": MIN_SWING_RATE,
            "promote_min_score": PROMOTE_MIN_SCORE,
            "promote_max_zone_distance_percent": PROMOTE_MAX_ZONE_DISTANCE_PERCENT,
            "promote_min_volume_ratio_5m": PROMOTE_MIN_VOLUME_RATIO_5M,
            "promote_min_volume_ratio_15m": PROMOTE_MIN_VOLUME_RATIO_15M,
            "promote_max_risk_percent": PROMOTE_MAX_RISK_PERCENT,
            "promote_min_room_r": PROMOTE_MIN_ROOM_R,
            "promote_max_extension_atr": PROMOTE_MAX_EXTENSION_ATR,
        },
        "run_counts": dict(_RUN_COUNTS),
        "promoted": _RUN_PROMOTED[-20:],
        "note": (
            "Background first-touch evidence may promote A++ PREP to ENTRY under V6 Balanced Core. "
            "Legacy Survival/Recovery mode is observational only; every current downstream "
            "quality, execution, duplicate, cooldown, portfolio and core guard remains mandatory."
        ),
    }
    _atomic_save(STATE_FILE, payload)
    return payload


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "enabled": bool(_PROFILE.get("enabled")),
        "profile": _PROFILE,
        "run_counts": dict(_RUN_COUNTS),
        "promotions_this_run": len(_RUN_PROMOTED),
        "exchange_orders": False,
        "legacy_survival_veto": False,
        "live_admission_mode": "V6_BALANCED_CORE",
        "downstream_gates_bypassed": False,
    }
