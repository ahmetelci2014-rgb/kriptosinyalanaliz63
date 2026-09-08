"""Market First observability: entry funnel and missed-move analysis.

This module is deliberately observational. It reads the existing opportunity
ledger and diagnostics after a live Market First run and writes compact reports
that answer two questions:

1. Where did valid preparations stop before becoming a real Telegram trade?
2. Which unsent preparations later reached TP1/TP2/TP3, and why were they missed?

It does not modify strategy decisions, send Telegram messages, or place orders.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import time
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

VERSION = "MARKET_FIRST_OBSERVABILITY_V1_2026_09_08"

LEDGER_FILE = "market_first_entry_plan_ledger.json"
SUMMARY_FILE = "market_first_opportunity_summary.json"
DIAGNOSTICS_FILE = "market_first_diagnostics.json"
CONVERSION_REPORT_FILE = "market_first_entry_conversion_report.json"
MISSED_MOVE_REPORT_FILE = "market_first_missed_move_report.json"
HISTORY_FILE = "market_first_observability_history.json"
MAX_HISTORY = 200
TOP_MISSED_LIMIT = 40


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


def _episodes(ledger: Mapping[str, Any]) -> list[Dict[str, Any]]:
    raw = ledger.get("episodes") if isinstance(ledger, Mapping) else None
    if not isinstance(raw, Mapping):
        return []
    return [dict(item) for item in raw.values() if isinstance(item, Mapping)]


def _clean(episodes: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    return [
        dict(item)
        for item in episodes
        if not bool(item.get("exclude_from_clean_prep_stats"))
    ]


def _promotion_reason(item: Mapping[str, Any]) -> str:
    if bool(item.get("entry_promoted")):
        return "PROMOTED"
    guard = str(item.get("entry_guard_reason") or "").strip().upper()
    if guard:
        return guard
    reason = str(item.get("entry_not_promoted_reason") or "").strip().upper()
    if reason:
        return reason
    if bool(item.get("entry_condition_met")):
        return "ENTRY_CONDITION_NOT_PROMOTED_UNKNOWN"
    return "ENTRY_CONDITION_NOT_REACHED"


def _delivery_reason(item: Mapping[str, Any]) -> str:
    if bool(item.get("entry_signal_sent")):
        return "SENT"
    if bool(item.get("entry_send_failed")):
        return "TELEGRAM_SEND_FAILED"
    if bool(item.get("entry_promoted")):
        return "PROMOTED_BUT_FINAL_SEND_NOT_CONFIRMED"
    return _promotion_reason(item)


def _miss_stage(item: Mapping[str, Any]) -> str:
    if not bool(item.get("entry_condition_met")):
        return "BEFORE_ENTRY_CONDITION"
    if not bool(item.get("entry_promoted")):
        return "PROMOTION_GATE"
    if not bool(item.get("entry_signal_sent")):
        return "FINAL_DELIVERY_GATE"
    return "SENT"


def _miss_level(item: Mapping[str, Any]) -> str:
    if item.get("tp3_at"):
        return "TP3_MISSED"
    if item.get("tp2_at"):
        return "TP2_MISSED"
    if item.get("tp1_at"):
        return "TP1_MISSED"
    return "NO_TARGET"


def _minutes(start: Any, end: Any) -> Optional[float]:
    start_i = _si(start)
    end_i = _si(end)
    if start_i <= 0 or end_i <= 0 or end_i < start_i:
        return None
    return round((end_i - start_i) / 60.0, 2)


def _missed_item(item: Mapping[str, Any]) -> Dict[str, Any]:
    latest = item.get("latest_plan") if isinstance(item.get("latest_plan"), Mapping) else {}
    initial = item.get("initial") if isinstance(item.get("initial"), Mapping) else {}
    score = _si(latest.get("score"), _si(initial.get("score")))
    stage = _miss_stage(item)
    reason = _delivery_reason(item)
    tp1_minutes = _minutes(item.get("first_at"), item.get("tp1_at"))
    condition_to_tp1 = _minutes(item.get("entry_condition_at"), item.get("tp1_at"))
    return {
        "episode_id": item.get("episode_id"),
        "symbol": item.get("symbol"),
        "direction": item.get("direction"),
        "score": score,
        "miss_level": _miss_level(item),
        "miss_stage": stage,
        "reason": reason,
        "outcome": item.get("outcome"),
        "prep_price": _sf(item.get("prep_price")),
        "last_price": _sf(item.get("last_price")),
        "best_favorable_percent": round(_sf(item.get("best_favorable_percent")), 4),
        "best_favorable_r": round(_sf(item.get("best_favorable_r")), 4),
        "worst_adverse_percent": round(_sf(item.get("worst_adverse_percent")), 4),
        "entry_condition_met": bool(item.get("entry_condition_met")),
        "entry_promoted": bool(item.get("entry_promoted")),
        "entry_signal_sent": bool(item.get("entry_signal_sent")),
        "tp1_before_entry_signal": bool(item.get("tp1_before_entry_signal")),
        "minutes_prep_to_tp1": tp1_minutes,
        "minutes_entry_condition_to_tp1": condition_to_tp1,
        "market_regime": latest.get("market_regime") or initial.get("market_regime"),
        "structure_5m": latest.get("structure_5m"),
        "structure_15m": latest.get("structure_15m"),
        "structure_1h": latest.get("structure_1h"),
        "volume_ratio_5m": _sf(latest.get("volume_ratio_5m")),
        "volume_ratio_15m": _sf(latest.get("volume_ratio_15m")),
        "extension_atr_5m": _sf(latest.get("extension_atr_5m")),
        "risk_percent": _sf(latest.get("risk_percent")),
        "room_r": _sf(latest.get("room_r")),
        "first_at": _si(item.get("first_at")),
        "tp1_at": _si(item.get("tp1_at")),
        "resolved_at": _si(item.get("closed_at") or item.get("updated_at")),
    }


def build_reports(
    ledger: Mapping[str, Any],
    opportunity_summary: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    *,
    generated_at: Optional[int] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    now = int(generated_at if generated_at is not None else time.time())
    all_episodes = _episodes(ledger)
    clean = _clean(all_episodes)

    total = len(clean)
    zone = sum(1 for item in clean if item.get("zone_touched"))
    condition = sum(1 for item in clean if item.get("entry_condition_met"))
    promoted = sum(1 for item in clean if item.get("entry_promoted"))
    sent = sum(1 for item in clean if item.get("entry_signal_sent"))
    tp1_before = sum(1 for item in clean if item.get("tp1_before_entry_signal"))
    resolved = sum(1 for item in clean if item.get("resolved"))

    condition_rows = [item for item in clean if item.get("entry_condition_met")]
    not_promoted = [item for item in condition_rows if not item.get("entry_promoted")]
    promoted_unsent = [
        item for item in condition_rows
        if item.get("entry_promoted") and not item.get("entry_signal_sent")
    ]

    promotion_reasons = Counter(_promotion_reason(item) for item in not_promoted)
    delivery_reasons = Counter(_delivery_reason(item) for item in promoted_unsent)

    clean_summary = opportunity_summary.get("entry_plan_clean") if isinstance(opportunity_summary, Mapping) else {}
    clean_summary = clean_summary if isinstance(clean_summary, Mapping) else {}
    rejection_counts = diagnostics.get("rejection_counts") if isinstance(diagnostics, Mapping) else {}
    rejection_counts = dict(rejection_counts) if isinstance(rejection_counts, Mapping) else {}

    conversion = {
        "version": VERSION,
        "generated_at": now,
        "source_ledger_version": ledger.get("version") if isinstance(ledger, Mapping) else None,
        "market_regime": (diagnostics.get("market") or {}).get("regime") if isinstance(diagnostics.get("market"), Mapping) else None,
        "deep_scan_count": _si(diagnostics.get("deep_scan_count")),
        "funnel": {
            "clean_preparations": total,
            "resolved": resolved,
            "zone_touched": zone,
            "entry_condition_met": condition,
            "entry_promoted": promoted,
            "entry_signal_sent": sent,
            "tp1_before_entry_signal": tp1_before,
        },
        "conversion_rates": {
            "prep_to_zone": _ratio(zone, total),
            "zone_to_entry_condition": _ratio(condition, zone),
            "entry_condition_to_promotion": _ratio(promoted, condition),
            "promotion_to_real_signal": _ratio(sent, promoted),
            "entry_condition_to_real_signal": _ratio(sent, condition),
            "prep_to_real_signal": _ratio(sent, total),
            "tp1_before_signal_share": _ratio(tp1_before, total),
        },
        "promotion_block_reasons": dict(promotion_reasons.most_common()),
        "promoted_but_unsent_reasons": dict(delivery_reasons.most_common()),
        "runner_rejection_counts_latest_run": rejection_counts,
        "opportunity_summary_clean_snapshot": dict(clean_summary),
        "diagnosis": {
            "largest_promotion_blocker": promotion_reasons.most_common(1)[0][0] if promotion_reasons else None,
            "largest_promotion_blocker_count": promotion_reasons.most_common(1)[0][1] if promotion_reasons else 0,
            "condition_to_signal_gap": max(0, condition - sent),
            "promotion_to_signal_gap": max(0, promoted - sent),
        },
    }

    missed_source = [
        item for item in clean
        if item.get("resolved")
        and not item.get("entry_signal_sent")
        and bool(item.get("tp1_at"))
    ]
    missed = [_missed_item(item) for item in missed_source]
    rank = {"TP3_MISSED": 3, "TP2_MISSED": 2, "TP1_MISSED": 1, "NO_TARGET": 0}
    missed.sort(
        key=lambda item: (
            rank.get(str(item.get("miss_level")), 0),
            _sf(item.get("best_favorable_r")),
            _sf(item.get("best_favorable_percent")),
        ),
        reverse=True,
    )

    stage_counts = Counter(str(item.get("miss_stage")) for item in missed)
    reason_counts = Counter(str(item.get("reason")) for item in missed)
    level_counts = Counter(str(item.get("miss_level")) for item in missed)
    direction_counts = Counter(str(item.get("direction")) for item in missed)
    outcome_counts = Counter(str(item.get("outcome")) for item in missed)

    prep_to_tp1 = [
        _sf(item.get("minutes_prep_to_tp1"))
        for item in missed
        if item.get("minutes_prep_to_tp1") is not None
    ]
    condition_to_tp1 = [
        _sf(item.get("minutes_entry_condition_to_tp1"))
        for item in missed
        if item.get("minutes_entry_condition_to_tp1") is not None
    ]

    missed_report = {
        "version": VERSION,
        "generated_at": now,
        "definition": "Clean resolved preparation that reached TP1 or better without a real Telegram entry signal.",
        "total_missed_moves": len(missed),
        "miss_rate_of_clean_resolved": _ratio(len(missed), resolved),
        "levels": dict(level_counts.most_common()),
        "stages": dict(stage_counts.most_common()),
        "reasons": dict(reason_counts.most_common()),
        "directions": dict(direction_counts.most_common()),
        "outcomes": dict(outcome_counts.most_common()),
        "timing": {
            "avg_minutes_prep_to_tp1": round(sum(prep_to_tp1) / len(prep_to_tp1), 2) if prep_to_tp1 else 0.0,
            "avg_minutes_entry_condition_to_tp1": round(sum(condition_to_tp1) / len(condition_to_tp1), 2) if condition_to_tp1 else 0.0,
            "tp1_within_30m": sum(1 for value in prep_to_tp1 if value <= 30),
            "tp1_within_60m": sum(1 for value in prep_to_tp1 if value <= 60),
        },
        "top_missed": missed[:TOP_MISSED_LIMIT],
    }
    return conversion, missed_report


def _update_history(
    history: Mapping[str, Any] | None,
    conversion: Mapping[str, Any],
    missed: Mapping[str, Any],
) -> Dict[str, Any]:
    existing = history if isinstance(history, Mapping) else {}
    rows = existing.get("snapshots") if isinstance(existing.get("snapshots"), list) else []
    rows = [dict(row) for row in rows if isinstance(row, Mapping)]
    generated_at = _si(conversion.get("generated_at"))
    snapshot = {
        "generated_at": generated_at,
        "market_regime": conversion.get("market_regime"),
        "deep_scan_count": conversion.get("deep_scan_count"),
        "funnel": dict(conversion.get("funnel") or {}),
        "conversion_rates": dict(conversion.get("conversion_rates") or {}),
        "largest_promotion_blocker": (conversion.get("diagnosis") or {}).get("largest_promotion_blocker"),
        "total_missed_moves": _si(missed.get("total_missed_moves")),
        "miss_rate_of_clean_resolved": _sf(missed.get("miss_rate_of_clean_resolved")),
        "miss_levels": dict(missed.get("levels") or {}),
        "miss_stages": dict(missed.get("stages") or {}),
    }
    if not rows or _si(rows[-1].get("generated_at")) != generated_at:
        rows.append(snapshot)
    else:
        rows[-1] = snapshot
    rows = rows[-MAX_HISTORY:]
    return {
        "version": VERSION,
        "updated_at": generated_at,
        "snapshots": rows,
    }


def run() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    ledger = _load(LEDGER_FILE, {"episodes": {}})
    summary = _load(SUMMARY_FILE, {})
    diagnostics = _load(DIAGNOSTICS_FILE, {})
    now = int(time.time())
    conversion, missed = build_reports(ledger, summary, diagnostics, generated_at=now)
    history = _update_history(_load(HISTORY_FILE, {}), conversion, missed)
    _save(CONVERSION_REPORT_FILE, conversion)
    _save(MISSED_MOVE_REPORT_FILE, missed)
    _save(HISTORY_FILE, history)
    print(
        "ENTRY CONVERSION | condition=", (conversion.get("funnel") or {}).get("entry_condition_met"),
        "| promoted=", (conversion.get("funnel") or {}).get("entry_promoted"),
        "| sent=", (conversion.get("funnel") or {}).get("entry_signal_sent"),
        "| missed=", missed.get("total_missed_moves"),
        "| biggest blocker=", (conversion.get("diagnosis") or {}).get("largest_promotion_blocker"),
    )
    return conversion, missed


if __name__ == "__main__":
    run()
