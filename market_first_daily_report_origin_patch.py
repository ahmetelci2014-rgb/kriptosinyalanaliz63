"""Reporting-only enrichment for Market First daily summary.

This patch fixes two observability gaps without changing live trading logic:
1) background episodes that started on an earlier day are marked as carry-over,
   so a multi-day move is not mistaken for a new candidate of the report day;
2) existing post_result_shadow data is surfaced for TP1/TP2 -> BE closures.

It never creates/promotes a signal, never changes entry/SL/TP rules and never
places an exchange order.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple

import market_first_daily_report as report

VERSION = "MARKET_FIRST_DAILY_ORIGIN_BE_PATCH_V1_2026_09_10"
_INSTALLED = False
_BASE_BUILD_REPORT = report.build_report
_BASE_ROW_LINE = report._row_line
_BASE_SECTIONS = report._sections


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number else default
    except Exception:
        return default


def _ts(value: Any) -> int:
    try:
        number = float(value or 0)
        if number > 10_000_000_000:
            number /= 1000.0
        return int(number) if number > 0 else 0
    except Exception:
        return 0


def _origin_date(candidate_at: Any) -> str:
    ts = _ts(candidate_at)
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(ts, report.TRT).date().isoformat()
    except Exception:
        return ""


def _latest_touch_ts(record: Mapping[str, Any]) -> int:
    return max(
        _ts(record.get(name))
        for name in (
            "closed_at", "resolved_at", "updated_at", "last_update",
            "opened_at", "first_at", "created_at", "timestamp",
        )
    )


def _real_record_index(bot: Any, target_date) -> Dict[Tuple[str, str], Mapping[str, Any]]:
    index: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for filename, containers in (
        (getattr(bot, "TRADE_LEDGER_FILE", "trade_ledger.json"), ("trades",)),
        (getattr(bot, "OPEN_SIGNALS_FILE", "open_signals.json"), ("open_signals", "trades")),
    ):
        payload = bot.load_json_file(filename, {})
        for record in report._iter_records(payload, containers=containers):
            symbol, direction = report._symbol_direction(record)
            if not symbol or direction not in {"LONG", "SHORT"}:
                continue
            if not report._record_touches_date(record, target_date):
                continue
            key = (symbol, direction)
            old = index.get(key)
            # Prefer the latest record, but prefer one that actually contains
            # post-result data when timestamps tie.
            if old is None:
                index[key] = record
                continue
            old_rank = (_latest_touch_ts(old), int(isinstance(old.get("post_result_shadow"), Mapping)))
            new_rank = (_latest_touch_ts(record), int(isinstance(record.get("post_result_shadow"), Mapping)))
            if new_rank >= old_rank:
                index[key] = record
    return index


def _be_followup(record: Mapping[str, Any]) -> Dict[str, Any]:
    follow = record.get("post_result_shadow")
    if not isinstance(follow, Mapping):
        return {}

    reached = follow.get("reached_levels")
    reached = reached if isinstance(reached, Mapping) else {}
    names = {str(name).upper() for name in reached.keys()}
    status = str(follow.get("status") or "").upper()

    if "TP3" in names:
        label = "BE SONRASI TP3"
    elif "TP2" in names:
        label = "BE SONRASI TP2"
    elif status == "TRACKING":
        label = "BE SONRASI TAKİP"
    elif status == "COMPLETED":
        label = "BE SONRASI HEDEF YOK"
    else:
        label = "BE SONRASI VERİ BEKLİYOR"

    return {
        "label": label,
        "status": status,
        "reached_levels": sorted(names),
        "max_favorable_percent": round(_sf(follow.get("max_favorable_percent")), 4),
        "max_adverse_percent": round(_sf(follow.get("max_adverse_percent")), 4),
    }


def build_report_enriched(
    bot: Any,
    now: Optional[int] = None,
    target_date=None,
) -> Dict[str, Any]:
    payload = _BASE_BUILD_REPORT(bot, now=now, target_date=target_date)
    date_text = str(payload.get("date") or "")
    try:
        target = datetime.strptime(date_text, "%Y-%m-%d").date()
    except Exception:
        target = target_date

    background = payload.get("background") if isinstance(payload.get("background"), list) else []
    new_count = 0
    carry_count = 0
    unknown_origin = 0

    for row in background:
        if not isinstance(row, dict):
            continue
        origin = _origin_date(row.get("candidate_at"))
        row["origin_date"] = origin or None
        if not origin or target is None:
            row["is_carryover"] = False
            unknown_origin += 1
            continue
        try:
            origin_day = datetime.strptime(origin, "%Y-%m-%d").date()
        except Exception:
            row["is_carryover"] = False
            unknown_origin += 1
            continue
        carry = origin_day < target
        row["is_carryover"] = carry
        if carry:
            carry_count += 1
        elif origin_day == target:
            new_count += 1
        else:
            unknown_origin += 1

    # New candidates are shown first. Old active/carry-over observations stay
    # visible, but can no longer masquerade as a same-day discovery.
    background.sort(
        key=lambda item: (
            bool(item.get("is_carryover")),
            -_sf(item.get("favorable_percent")),
            str(item.get("symbol") or ""),
            str(item.get("direction") or ""),
        )
    )

    summary = payload.setdefault("summary", {})
    summary["background_new_count"] = new_count
    summary["background_carryover_count"] = carry_count
    summary["background_unknown_origin_count"] = unknown_origin

    if target is not None:
        record_index = _real_record_index(bot, target)
    else:
        record_index = {}

    be_tp2 = 0
    be_tp3 = 0
    be_tracking = 0
    be_no_data = 0

    real_rows = payload.get("real_trades") if isinstance(payload.get("real_trades"), list) else []
    for row in real_rows:
        if not isinstance(row, dict):
            continue
        key = (str(row.get("symbol") or ""), str(row.get("direction") or ""))
        record = record_index.get(key)
        if not isinstance(record, Mapping):
            continue

        final_result = str(record.get("final_result") or "").upper()
        if "TP2_SONRASI_BE" in final_result:
            row["result"] = "TP2 + BE"
        elif "TP1_SONRASI_BE" in final_result:
            row["result"] = "TP1 + BE"

        if "+ BE" not in str(row.get("result") or ""):
            continue

        follow = _be_followup(record)
        if not follow:
            row["diagnosis"] = "BE SONRASI VERİ BEKLİYOR"
            be_no_data += 1
            continue

        row["diagnosis"] = follow["label"]
        row["post_be_status"] = follow["status"]
        row["post_be_reached_levels"] = follow["reached_levels"]
        row["post_be_max_favorable_percent"] = follow["max_favorable_percent"]
        row["post_be_max_adverse_percent"] = follow["max_adverse_percent"]

        if follow["label"] == "BE SONRASI TP3":
            be_tp3 += 1
        elif follow["label"] == "BE SONRASI TP2":
            be_tp2 += 1
        elif follow["label"] == "BE SONRASI TAKİP":
            be_tracking += 1
        else:
            be_no_data += 1

    summary["be_after_tp2"] = be_tp2
    summary["be_after_tp3"] = be_tp3
    summary["be_following"] = be_tracking
    summary["be_without_follow_data"] = be_no_data
    payload["report_enrichment_version"] = VERSION
    return payload


def _row_line_enriched(row: Mapping[str, Any]) -> str:
    text = _BASE_ROW_LINE(row)
    diagnosis = str(row.get("diagnosis") or "")
    if diagnosis.startswith("BE SONRASI") and diagnosis not in text:
        text += f" | {diagnosis}"
    if bool(row.get("is_carryover")):
        origin = str(row.get("origin_date") or "")
        try:
            origin = datetime.strptime(origin, "%Y-%m-%d").strftime("%d.%m")
        except Exception:
            origin = origin or "?"
        text += f" | ESKİ ADAY {origin}"
    return text


def _sections_enriched(payload: Mapping[str, Any]):
    lines = _BASE_SECTIONS(payload)
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    lines.extend([
        f"🗓️ Arka plan kökeni: {int(_sf(summary.get('background_new_count')))} yeni | "
        f"{int(_sf(summary.get('background_carryover_count')))} eski aday",
        f"🟡 BE sonrası: TP2 {int(_sf(summary.get('be_after_tp2')))} | "
        f"TP3 {int(_sf(summary.get('be_after_tp3')))} | "
        f"takipte {int(_sf(summary.get('be_following')))} | "
        f"veri bekliyor {int(_sf(summary.get('be_without_follow_data')))}",
    ])
    return lines


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return False
    report.build_report = build_report_enriched
    report._row_line = _row_line_enriched
    report._sections = _sections_enriched
    _INSTALLED = True
    return True


def summary() -> str:
    return f"{VERSION} | installed={_INSTALLED} | reporting_only=True"
