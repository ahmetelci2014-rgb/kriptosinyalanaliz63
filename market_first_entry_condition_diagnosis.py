"""Diagnose why good preparations never became an ENTRY condition.

This module is observational only. It focuses on clean, resolved preparations
that reached TP1 or better without a real Telegram trade and *never* recorded an
ENTRY condition. It breaks that gap into zone/timing, 5m structure, volume,
score and risk geometry signals using the opportunity ledger already produced by
Market First.

Important: ledger fields are snapshots collected across an episode, so this
report describes likely blockers and timing patterns; it does not pretend those
fields were necessarily simultaneous. It never changes thresholds, sends
Telegram messages or places exchange orders.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import time
from typing import Any, Dict, Iterable, Mapping, Optional

VERSION = "MARKET_FIRST_ENTRY_CONDITION_DIAGNOSIS_V1_2026_09_08"
LEDGER_FILE = "market_first_entry_plan_ledger.json"
REPORT_FILE = "market_first_entry_condition_diagnosis.json"

# Current Entry Accelerator V1 reference values. These are used only for
# diagnosis; changing them here does not change live eligibility.
ENTRY_SCORE_REFERENCE = 76
VOLUME_5M_REFERENCE = 0.50
SHADOW_VOLUME_5M_FLOOR = 0.35
MAX_RISK_REFERENCE = 1.45
MIN_ROOM_REFERENCE = 1.45
MAX_EXTENSION_REFERENCE = 1.25
TOP_LIMIT = 40


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


def _save(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=False)
        handle.write("\n")


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _minutes(start: Any, end: Any) -> Optional[float]:
    a = _si(start)
    b = _si(end)
    if a <= 0 or b <= 0 or b < a:
        return None
    return round((b - a) / 60.0, 2)


def _episodes(ledger: Mapping[str, Any]) -> list[Dict[str, Any]]:
    raw = ledger.get("episodes") if isinstance(ledger, Mapping) else None
    if not isinstance(raw, Mapping):
        return []
    return [dict(row) for row in raw.values() if isinstance(row, Mapping)]


def _clean(rows: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    return [dict(row) for row in rows if not bool(row.get("exclude_from_clean_prep_stats"))]


def _latest(row: Mapping[str, Any]) -> Mapping[str, Any]:
    latest = row.get("latest_plan")
    if isinstance(latest, Mapping):
        return latest
    initial = row.get("initial")
    return initial if isinstance(initial, Mapping) else {}


def _direction(row: Mapping[str, Any]) -> str:
    return str(row.get("direction") or "").upper()


def _flags(row: Mapping[str, Any]) -> list[str]:
    plan = _latest(row)
    direction = _direction(row)
    score = _si(plan.get("score"))
    s5 = str(plan.get("structure_5m") or "").upper()
    s15 = str(plan.get("structure_15m") or "").upper()
    s1h = str(plan.get("structure_1h") or "").upper()
    volume5 = _sf(plan.get("volume_ratio_5m"))
    risk = _sf(plan.get("risk_percent"), 99.0)
    room = _sf(plan.get("room_r"), 0.0)
    extension = _sf(plan.get("extension_atr_5m"), 99.0)
    result: list[str] = []

    if not bool(row.get("zone_touched")):
        result.append("ZONE_NEVER_TOUCHED")
    if direction not in {"LONG", "SHORT"} or s15 != direction or s1h != direction:
        result.append("HIGHER_TF_NOT_ALIGNED_LATEST")
    if score < ENTRY_SCORE_REFERENCE:
        result.append("SCORE_BELOW_76_LATEST")
    if s5 == "NEUTRAL":
        result.append("STRUCTURE_5M_NEUTRAL_LATEST")
    elif s5 != direction:
        result.append("STRUCTURE_5M_OPPOSITE_LATEST")
    if volume5 < VOLUME_5M_REFERENCE:
        result.append("VOLUME_5M_BELOW_0_50_LATEST")
    if risk > MAX_RISK_REFERENCE:
        result.append("RISK_ABOVE_1_45_LATEST")
    if 0 < room < MIN_ROOM_REFERENCE:
        result.append("ROOM_BELOW_1_45_LATEST")
    if extension > MAX_EXTENSION_REFERENCE:
        result.append("EXTENSION_ABOVE_1_25_LATEST")
    minutes = _minutes(row.get("first_at"), row.get("tp1_at"))
    if minutes is not None and minutes <= 30:
        result.append("TP1_WITHIN_30M")
    elif minutes is not None and minutes <= 60:
        result.append("TP1_WITHIN_60M")
    return result


def _primary_obstacle(row: Mapping[str, Any], flags: list[str]) -> str:
    # Prioritize structural blockers over softer micro-quality observations.
    priority = [
        "ZONE_NEVER_TOUCHED",
        "HIGHER_TF_NOT_ALIGNED_LATEST",
        "SCORE_BELOW_76_LATEST",
        "STRUCTURE_5M_OPPOSITE_LATEST",
        "STRUCTURE_5M_NEUTRAL_LATEST",
        "VOLUME_5M_BELOW_0_50_LATEST",
        "RISK_ABOVE_1_45_LATEST",
        "ROOM_BELOW_1_45_LATEST",
        "EXTENSION_ABOVE_1_25_LATEST",
    ]
    for key in priority:
        if key in flags:
            return key
    if bool(row.get("zone_touched")):
        return "LIKELY_TIMING_WINDOW_MISSED"
    return "UNCLASSIFIED"


def _shadow_profile(row: Mapping[str, Any]) -> bool:
    """Historical profile for a future PRE-ENTRY shadow lane, never a live rule."""
    plan = _latest(row)
    direction = _direction(row)
    s5 = str(plan.get("structure_5m") or "").upper()
    s15 = str(plan.get("structure_15m") or "").upper()
    s1h = str(plan.get("structure_1h") or "").upper()
    score = _si(plan.get("score"))
    volume5 = _sf(plan.get("volume_ratio_5m"))
    risk = _sf(plan.get("risk_percent"), 99.0)
    room = _sf(plan.get("room_r"), 0.0)
    extension = _sf(plan.get("extension_atr_5m"), 99.0)
    return (
        bool(row.get("zone_touched"))
        and direction in {"LONG", "SHORT"}
        and s15 == direction
        and s1h == direction
        and s5 in {direction, "NEUTRAL"}
        and score >= 80
        and volume5 >= SHADOW_VOLUME_5M_FLOOR
        and risk <= MAX_RISK_REFERENCE
        and room >= MIN_ROOM_REFERENCE
        and extension <= MAX_EXTENSION_REFERENCE
    )


def _detail(row: Mapping[str, Any]) -> Dict[str, Any]:
    plan = _latest(row)
    flags = _flags(row)
    minutes = _minutes(row.get("first_at"), row.get("tp1_at"))
    return {
        "episode_id": row.get("episode_id"),
        "symbol": row.get("symbol"),
        "direction": _direction(row),
        "score": _si(plan.get("score")),
        "primary_obstacle": _primary_obstacle(row, flags),
        "diagnostic_flags": flags,
        "shadow_pre_entry_profile": _shadow_profile(row),
        "zone_touched": bool(row.get("zone_touched")),
        "structure_5m": str(plan.get("structure_5m") or "").upper(),
        "structure_15m": str(plan.get("structure_15m") or "").upper(),
        "structure_1h": str(plan.get("structure_1h") or "").upper(),
        "volume_ratio_5m": round(_sf(plan.get("volume_ratio_5m")), 3),
        "volume_ratio_15m": round(_sf(plan.get("volume_ratio_15m")), 3),
        "extension_atr_5m": round(_sf(plan.get("extension_atr_5m")), 3),
        "risk_percent": round(_sf(plan.get("risk_percent")), 3),
        "room_r": round(_sf(plan.get("room_r")), 2),
        "market_regime": plan.get("market_regime"),
        "best_favorable_r": round(_sf(row.get("best_favorable_r")), 4),
        "best_favorable_percent": round(_sf(row.get("best_favorable_percent")), 4),
        "worst_adverse_percent": round(_sf(row.get("worst_adverse_percent")), 4),
        "tp1_minutes": minutes,
        "tp2_reached": bool(row.get("tp2_at")),
        "tp3_reached": bool(row.get("tp3_at")),
        "outcome": row.get("outcome"),
    }


def build_report(ledger: Mapping[str, Any], *, generated_at: Optional[int] = None) -> Dict[str, Any]:
    now = int(generated_at if generated_at is not None else time.time())
    clean = _clean(_episodes(ledger))
    source = [
        row for row in clean
        if bool(row.get("resolved"))
        and not bool(row.get("entry_signal_sent"))
        and not bool(row.get("entry_condition_met"))
        and bool(row.get("tp1_at"))
    ]
    details = [_detail(row) for row in source]
    primary = Counter(str(row.get("primary_obstacle")) for row in details)
    flag_counts = Counter(flag for row in details for flag in row.get("diagnostic_flags", []))
    shadow = [row for row in details if row.get("shadow_pre_entry_profile")]
    tp3 = [row for row in details if row.get("tp3_reached")]
    quick30 = [row for row in details if row.get("tp1_minutes") is not None and _sf(row.get("tp1_minutes")) <= 30]
    quick60 = [row for row in details if row.get("tp1_minutes") is not None and _sf(row.get("tp1_minutes")) <= 60]

    obstacle_stats: Dict[str, Dict[str, Any]] = {}
    grouped: dict[str, list[Dict[str, Any]]] = defaultdict(list)
    for row in details:
        grouped[str(row.get("primary_obstacle"))].append(row)
    for key, rows in grouped.items():
        rs = [_sf(row.get("best_favorable_r")) for row in rows]
        obstacle_stats[key] = {
            "count": len(rows),
            "share": _ratio(len(rows), len(details)),
            "tp3_count": sum(1 for row in rows if row.get("tp3_reached")),
            "tp1_within_30m": sum(1 for row in rows if row.get("tp1_minutes") is not None and _sf(row.get("tp1_minutes")) <= 30),
            "avg_best_favorable_r": round(sum(rs) / len(rs), 4) if rs else 0.0,
        }

    details.sort(
        key=lambda row: (
            1 if row.get("tp3_reached") else 0,
            _sf(row.get("best_favorable_r")),
        ),
        reverse=True,
    )
    shadow.sort(key=lambda row: _sf(row.get("best_favorable_r")), reverse=True)

    return {
        "version": VERSION,
        "generated_at": now,
        "source_ledger_version": ledger.get("version") if isinstance(ledger, Mapping) else None,
        "definition": (
            "Clean resolved preparations that reached TP1+ without a Telegram trade and never recorded ENTRY condition. "
            "Latest-plan flags are diagnostic snapshots, not guaranteed co-temporal blocker proofs."
        ),
        "reference_thresholds_observational_only": {
            "entry_score": ENTRY_SCORE_REFERENCE,
            "volume_5m": VOLUME_5M_REFERENCE,
            "shadow_volume_5m_floor": SHADOW_VOLUME_5M_FLOOR,
            "max_risk_percent": MAX_RISK_REFERENCE,
            "min_room_r": MIN_ROOM_REFERENCE,
            "max_extension_atr": MAX_EXTENSION_REFERENCE,
        },
        "summary": {
            "before_entry_condition_missed": len(details),
            "tp3_missed": len(tp3),
            "tp1_within_30m": len(quick30),
            "tp1_within_60m": len(quick60),
            "shadow_pre_entry_profile_count": len(shadow),
            "shadow_pre_entry_profile_share": _ratio(len(shadow), len(details)),
        },
        "primary_obstacles": dict(primary.most_common()),
        "diagnostic_flags": dict(flag_counts.most_common()),
        "obstacle_stats": obstacle_stats,
        "top_before_entry_misses": details[:TOP_LIMIT],
        "top_shadow_pre_entry_profiles": shadow[:TOP_LIMIT],
        "next_step_hint": (
            "Do not loosen live thresholds from this report alone. Compare several post-accelerator snapshots; "
            "if one obstacle/profile remains dominant, test that change in shadow before live promotion."
        ),
    }


def run() -> Dict[str, Any]:
    ledger = _load(LEDGER_FILE, {"episodes": {}})
    report = build_report(ledger)
    _save(REPORT_FILE, report)
    print(
        "ENTRY CONDITION DIAGNOSIS | missed-before-entry=", report["summary"]["before_entry_condition_missed"],
        "| shadow-profile=", report["summary"]["shadow_pre_entry_profile_count"],
        "| largest=", next(iter(report["primary_obstacles"]), None),
    )
    return report


if __name__ == "__main__":
    run()
