"""Kripto Kontrol Paneli için salt-okunur, çevrimdışı HTML üreticisi.

Bu modül yalnız repodaki mevcut JSON state/ledger dosyalarını okur. Borsaya,
Telegram'a veya herhangi bir dış servise bağlanmaz; emir ve sinyal üretmez.
"""

from __future__ import annotations

import argparse
import html
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


VERSION = "KRIPTO_KONTROL_PANELI_V1_5_2026_08_14"
TR_TIMEZONE = timezone(timedelta(hours=3))

SOURCE_SPECS = (
    ("open_signals.json", "Premium açık işlemler", 1.0, False),
    ("scalp_radar_state.json", "Scalp radar durumu", 1.0, False),
    ("pump_radar_state.json", "Pump/Dump radar durumu", 1.0, False),
    ("new_listing_performance_ledger.json", "Yeni liste kayıtları", 72.0, False),
    ("trade_ledger.json", "Premium performans", 24.0, False),
    ("scalp_performance_ledger.json", "Scalp performans", 24.0, False),
    ("pump_performance_ledger.json", "Pump/Dump performans", 24.0, False),
    ("system_control_center_report.json", "System Control raporu", 24.0, True),
)

TIMESTAMP_KEYS = {
    "generated_at",
    "updated_at",
    "last_update",
    "last_updated_at",
    "last_checked_at",
    "last_tracking_at",
    "last_run",
    "last_no_signal_report",
    "checked_at",
    "closed_at",
    "trade_closed_at",
    "finalized_at",
    "recorded_at",
    "sent_at",
    "opened_at",
}

TIMESTAMP_VALUE_CONTAINERS = {
    "last_scalp_signals",
    "last_sent",
    "early_last_sent",
    "prewatch_last_sent",
    "shadow_last_seen",
}

SYSTEM_LABELS = {
    "PREMIUM": "Premium MTF",
    "SCALP": "Scalp Radar",
    "PUMP_DUMP": "Pump / Dump",
    "NEW_LISTING": "Yeni Liste",
}

OUTCOME_ALIASES = {
    "STOP": "SL",
    "STOPLOSS": "SL",
    "STOP_LOSS": "SL",
    "BREAKEVEN": "BE",
    "BREAK_EVEN": "BE",
    "TP1_BE": "TP1_SONRASI_BE",
    "TP2_BE": "TP2_SONRASI_BE",
    "TP1_AFTER_BE": "TP1_SONRASI_BE",
    "TP2_AFTER_BE": "TP2_SONRASI_BE",
    "TIMEOUT": "EXPIRED",
}


def safe_float(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def safe_int(value: Any, default: int = 0) -> int:
    number = safe_float(value)
    return int(number) if number is not None else default


def read_json(root: Path, filename: str, default: Any, warnings: list[str]) -> Any:
    path = root / filename
    if not path.exists():
        warnings.append(f"{filename}: dosya bulunamadı")
        return default
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"{filename}: okunamadı ({type(exc).__name__})")
        return default
    return data


def iter_records(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    for key in ("records", "trades", "closed_positions"):
        value = data.get(key)
        if isinstance(value, dict):
            return [row for row in value.values() if isinstance(row, dict)]
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def normalize_outcome(value: Any) -> str:
    raw = str(value or "").strip().upper().replace(" ", "_")
    return OUTCOME_ALIASES.get(raw, raw)


def first_value(record: dict[str, Any], keys: Iterable[str], default: Any = None) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return default


def record_timestamp(record: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = parse_timestamp(record.get(key))
        if value > 0:
            return value
    return 0


def parse_timestamp(value: Any) -> int:
    """Unix saniyesi veya ISO-8601 değeri güvenli biçimde Unix saniyesine çevirir."""
    number = safe_float(value)
    if number is not None and number > 0:
        if number > 10_000_000_000:
            number /= 1000
        return int(number)
    if not isinstance(value, str) or not value.strip():
        return 0
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def latest_document_timestamp(value: Any) -> int:
    """Belgedeki bilinen zaman alanlarının en güncelini bulur."""
    latest = 0
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if key_text in TIMESTAMP_KEYS:
                latest = max(latest, parse_timestamp(child))
            if key_text in TIMESTAMP_VALUE_CONTAINERS and isinstance(child, dict):
                for timestamp_value in child.values():
                    if not isinstance(timestamp_value, (dict, list)):
                        latest = max(latest, parse_timestamp(timestamp_value))
            if isinstance(child, (dict, list)):
                latest = max(latest, latest_document_timestamp(child))
    elif isinstance(value, list):
        for child in value:
            latest = max(latest, latest_document_timestamp(child))
    return latest


def collect_source_freshness(
    root: Path,
    current: datetime,
    warnings: list[str],
) -> list[dict[str, Any]]:
    """Her panel kaynağını ayrı değerlendirir; sessiz veri kesintisini görünür kılar."""
    rows: list[dict[str, Any]] = []
    current_ts = int(current.timestamp())
    for filename, label, threshold_hours, critical in SOURCE_SPECS:
        local_warnings: list[str] = []
        document = read_json(root, filename, {}, local_warnings)
        warnings.extend(local_warnings)
        latest_at = latest_document_timestamp(document)
        age_hours = (
            round(max(0, current_ts - latest_at) / 3600, 2)
            if latest_at
            else None
        )
        if local_warnings:
            status = "ERROR"
        elif latest_at <= 0:
            status = "UNKNOWN"
        elif age_hours is not None and age_hours > threshold_hours:
            status = "STALE"
        else:
            status = "FRESH"
        if critical and status in {"STALE", "UNKNOWN", "ERROR"}:
            detail = "zaman bilgisi yok" if age_hours is None else f"{age_hours:.1f} saat eski"
            warnings.append(f"{filename}: kritik kaynak güncel değil ({detail})")
        rows.append({
            "filename": filename,
            "label": label,
            "critical": critical,
            "latest_at": latest_at,
            "age_hours": age_hours,
            "threshold_hours": threshold_hours,
            "status": status,
        })
    return rows


def build_performance_analytics(closed_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Sistem bazlı sonuç oranları ve kesin R değerlerinden özsermaye eğrisi üretir."""
    systems: list[dict[str, Any]] = []
    for system in SYSTEM_LABELS:
        rows = [row for row in closed_results if row["system"] == system]
        outcomes = Counter(row["outcome"] for row in rows)
        r_values = [row["r_result"] for row in rows if row["r_result"] is not None]
        tp = sum(
            count
            for outcome, count in outcomes.items()
            if outcome.startswith("TP") and "BE" not in outcome
        )
        sl = outcomes.get("SL", 0)
        be = sum(
            count
            for outcome, count in outcomes.items()
            if outcome == "BE" or "SONRASI_BE" in outcome
        )
        systems.append({
            "system": system,
            "label": SYSTEM_LABELS[system],
            "sample": len(rows),
            "tp": tp,
            "sl": sl,
            "be": be,
            "expired": outcomes.get("EXPIRED", 0),
            "tp_rate": round(tp / len(rows) * 100, 1) if rows else None,
            "sl_rate": round(sl / len(rows) * 100, 1) if rows else None,
            "exact_r_sample": len(r_values),
            "net_r": round(sum(r_values), 4) if r_values else None,
        })

    exact_rows = sorted(
        (row for row in closed_results if row["r_result"] is not None),
        key=lambda row: (safe_int(row.get("closed_at")), row.get("id", "")),
    )
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    points: list[dict[str, Any]] = []
    for row in exact_rows:
        cumulative += float(row["r_result"])
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
        points.append({
            "timestamp": safe_int(row.get("closed_at")),
            "r": round(float(row["r_result"]), 4),
            "cumulative_r": round(cumulative, 4),
            "system": row["system"],
            "trade_id": row["id"],
        })
    return {
        "systems": systems,
        "equity_curve": points,
        "net_r": round(cumulative, 4) if points else None,
        "exact_r_sample": len(points),
        "max_drawdown_r": round(max_drawdown, 4) if points else None,
    }


def display_symbol(record: dict[str, Any]) -> str:
    symbol = str(first_value(record, ("display_symbol", "symbol"), "—"))
    return symbol.replace("/USDT:USDT", "USDT").replace("/", "")


def normalize_open_trade(system: str, record: dict[str, Any], fallback_id: str) -> dict[str, Any]:
    tp1_hit = bool(record.get("tp1_hit"))
    tp2_hit = bool(record.get("tp2_hit"))
    tp3_hit = bool(record.get("tp3_hit"))
    if tp3_hit:
        progress = "TP3 GÖRÜLDÜ"
    elif tp2_hit:
        progress = "TP2 GÖRÜLDÜ"
    elif tp1_hit:
        progress = "TP1 GÖRÜLDÜ"
    else:
        progress = "AÇIK"

    entry = safe_float(first_value(record, ("entry", "analysis_entry", "alert_price")))
    tp1 = safe_float(record.get("tp1"))
    tp2 = safe_float(record.get("tp2"))
    tp3 = safe_float(record.get("tp3"))
    sl = safe_float(record.get("sl"))
    risk_distance = abs(entry - sl) if entry not in (None, 0) and sl is not None else None
    stop_percent = (
        round(risk_distance / abs(entry) * 100, 4)
        if risk_distance is not None and entry
        else None
    )
    tp1_rr = (
        round(abs(tp1 - entry) / risk_distance, 4)
        if tp1 is not None and entry is not None and risk_distance
        else None
    )
    tp3_rr = (
        round(abs(tp3 - entry) / risk_distance, 4)
        if tp3 is not None and entry is not None and risk_distance
        else None
    )

    return {
        "id": str(first_value(record, ("trade_id", "performance_record_id", "id", "record_id"), fallback_id)),
        "system": system,
        "system_label": SYSTEM_LABELS[system],
        "symbol": display_symbol(record),
        "direction": str(record.get("direction") or "—").upper(),
        "source": str(first_value(record, ("setup", "setup_name", "source", "alert_type"), "—")),
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl,
        "stop_percent": stop_percent,
        "tp1_rr": tp1_rr,
        "tp3_rr": tp3_rr,
        "last_price": safe_float(first_value(record, ("last_market_price", "latest_price", "price"))),
        "score": safe_float(record.get("score")),
        "opened_at": record_timestamp(record, "opened_at", "sent_at", "recorded_at", "first_seen_at"),
        "tp1_hit": tp1_hit,
        "tp2_hit": tp2_hit,
        "tp3_hit": tp3_hit,
        "progress": progress,
    }


def build_open_risk_summary(open_trades: list[dict[str, Any]]) -> dict[str, Any]:
    directions = Counter(row.get("direction") for row in open_trades)
    systems = Counter(row.get("system") for row in open_trades)
    stop_rows = [
        row for row in open_trades
        if row.get("stop_percent") is not None
    ]
    stop_values = [float(row["stop_percent"]) for row in stop_rows]
    tp1_values = [
        float(row["tp1_rr"])
        for row in open_trades
        if row.get("tp1_rr") is not None
    ]
    tp3_values = [
        float(row["tp3_rr"])
        for row in open_trades
        if row.get("tp3_rr") is not None
    ]
    widest = max(stop_rows, key=lambda row: float(row["stop_percent"])) if stop_rows else None
    return {
        "total": len(open_trades),
        "long": int(directions.get("LONG", 0)),
        "short": int(directions.get("SHORT", 0)),
        "unknown_direction": len(open_trades) - int(directions.get("LONG", 0)) - int(directions.get("SHORT", 0)),
        "with_stop": len(stop_rows),
        "missing_stop": len(open_trades) - len(stop_rows),
        "average_stop_percent": round(sum(stop_values) / len(stop_values), 4) if stop_values else None,
        "max_stop_percent": round(max(stop_values), 4) if stop_values else None,
        "widest_stop_symbol": widest.get("symbol") if widest else None,
        "wide_stop_count": sum(1 for value in stop_values if value > 2.0),
        "average_tp1_rr": round(sum(tp1_values) / len(tp1_values), 4) if tp1_values else None,
        "average_tp3_rr": round(sum(tp3_values) / len(tp3_values), 4) if tp3_values else None,
        "systems": [
            {
                "system": system,
                "label": SYSTEM_LABELS[system],
                "count": int(systems.get(system, 0)),
            }
            for system in SYSTEM_LABELS
        ],
    }


def build_result_breakdown(
    closed_results: list[dict[str, Any]],
    current: datetime,
) -> dict[str, Any]:
    direction_rows: list[dict[str, Any]] = []
    for direction in ("LONG", "SHORT"):
        rows = [row for row in closed_results if row.get("direction") == direction]
        outcomes = Counter(row["outcome"] for row in rows)
        exact_r = [float(row["r_result"]) for row in rows if row.get("r_result") is not None]
        tp = sum(
            count
            for outcome, count in outcomes.items()
            if outcome.startswith("TP") and "BE" not in outcome
        )
        sl = int(outcomes.get("SL", 0))
        direction_rows.append({
            "direction": direction,
            "sample": len(rows),
            "tp": tp,
            "sl": sl,
            "tp_rate": round(tp / len(rows) * 100, 1) if rows else None,
            "sl_rate": round(sl / len(rows) * 100, 1) if rows else None,
            "exact_r_sample": len(exact_r),
            "net_r": round(sum(exact_r), 4) if exact_r else None,
            "average_r": round(sum(exact_r) / len(exact_r), 4) if exact_r else None,
        })

    current_day = current.astimezone(TR_TIMEZONE).date()
    daily: list[dict[str, Any]] = []
    daily_index: dict[str, dict[str, Any]] = {}
    for days_ago in range(29, -1, -1):
        day = (current_day - timedelta(days=days_ago)).isoformat()
        row = {
            "date": day,
            "count": 0,
            "tp": 0,
            "sl": 0,
            "exact_r_sample": 0,
            "net_r": 0.0,
        }
        daily.append(row)
        daily_index[day] = row
    for result in closed_results:
        timestamp = safe_int(result.get("closed_at"))
        if timestamp <= 0:
            continue
        day = datetime.fromtimestamp(timestamp, TR_TIMEZONE).date().isoformat()
        target = daily_index.get(day)
        if target is None:
            continue
        target["count"] += 1
        outcome = str(result.get("outcome") or "")
        if outcome.startswith("TP") and "BE" not in outcome:
            target["tp"] += 1
        elif outcome == "SL":
            target["sl"] += 1
        if result.get("r_result") is not None:
            target["exact_r_sample"] += 1
            target["net_r"] += float(result["r_result"])
    for row in daily:
        row["net_r"] = round(float(row["net_r"]), 4)

    sequence_type = None
    sequence_count = 0
    for result in closed_results:
        outcome = str(result.get("outcome") or "")
        if outcome.startswith("TP") and "BE" not in outcome:
            category = "TP"
        elif outcome == "SL":
            category = "SL"
        elif outcome == "BE" or "BE" in outcome:
            category = "BE"
        elif outcome == "EXPIRED":
            category = "EXPIRED"
        else:
            category = "OTHER"
        if sequence_type is None:
            sequence_type = category
        if category != sequence_type:
            break
        sequence_count += 1

    measured_days = [row for row in daily if row["exact_r_sample"] > 0]
    best_day = max(measured_days, key=lambda row: row["net_r"]) if measured_days else None
    worst_day = min(measured_days, key=lambda row: row["net_r"]) if measured_days else None
    return {
        "directions": direction_rows,
        "daily_30d": daily,
        "recent_sequence": {"type": sequence_type, "count": sequence_count},
        "best_day": dict(best_day) if best_day else None,
        "worst_day": dict(worst_day) if worst_day else None,
    }


def summarize_period_results(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = Counter(str(row.get("outcome") or "") for row in rows)
    exact_r = [float(row["r_result"]) for row in rows if row.get("r_result") is not None]
    tp = sum(
        count
        for outcome, count in outcomes.items()
        if outcome.startswith("TP") and "BE" not in outcome
    )
    sl = int(outcomes.get("SL", 0))
    return {
        "sample": len(rows),
        "tp": tp,
        "sl": sl,
        "tp_rate": round(tp / len(rows) * 100, 1) if rows else None,
        "sl_rate": round(sl / len(rows) * 100, 1) if rows else None,
        "exact_r_sample": len(exact_r),
        "net_r": round(sum(exact_r), 4) if exact_r else None,
    }


def build_period_comparisons(
    closed_results: list[dict[str, Any]],
    current: datetime,
) -> dict[str, Any]:
    current_ts = int(current.timestamp())
    comparisons: dict[str, Any] = {}
    systems = (("ALL", "Tüm Sistemler"), *SYSTEM_LABELS.items())
    for key, days in (("7D", 7), ("30D", 30)):
        current_start = current_ts - days * 86400
        previous_start = current_ts - days * 2 * 86400
        current_rows = [
            row
            for row in closed_results
            if current_start <= safe_int(row.get("closed_at")) <= current_ts
        ]
        previous_rows = [
            row
            for row in closed_results
            if previous_start <= safe_int(row.get("closed_at")) < current_start
        ]
        rows = []
        for system, label in systems:
            active = (
                current_rows
                if system == "ALL"
                else [row for row in current_rows if row.get("system") == system]
            )
            previous = (
                previous_rows
                if system == "ALL"
                else [row for row in previous_rows if row.get("system") == system]
            )
            active_summary = summarize_period_results(active)
            previous_summary = summarize_period_results(previous)
            current_net = active_summary["net_r"]
            previous_net = previous_summary["net_r"]
            rows.append({
                "system": system,
                "label": label,
                "current": active_summary,
                "previous": previous_summary,
                "sample_delta": active_summary["sample"] - previous_summary["sample"],
                "net_r_delta": (
                    round(float(current_net) - float(previous_net), 4)
                    if current_net is not None and previous_net is not None
                    else None
                ),
            })
        comparisons[key] = {
            "days": days,
            "label": f"Son {days} gün",
            "previous_label": f"Önceki {days} gün",
            "rows": rows,
        }
    return comparisons


def collect_open_trades(root: Path, warnings: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    premium = read_json(root, "open_signals.json", {}, warnings)
    if isinstance(premium, dict):
        for key, record in premium.items():
            if isinstance(record, dict) and not bool(record.get("closed")):
                rows.append(normalize_open_trade("PREMIUM", record, str(key)))

    scalp = read_json(root, "scalp_radar_state.json", {}, warnings)
    scalp_open = scalp.get("open_scalp_signals", {}) if isinstance(scalp, dict) else {}
    if isinstance(scalp_open, dict):
        for key, record in scalp_open.items():
            if isinstance(record, dict) and not bool(record.get("closed")):
                rows.append(normalize_open_trade("SCALP", record, str(key)))

    pump = read_json(root, "pump_radar_state.json", {}, warnings)
    seen_pump: set[str] = set()
    if isinstance(pump, dict):
        for bucket_name in ("open_pump_signals", "open_signals"):
            bucket = pump.get(bucket_name, {})
            if not isinstance(bucket, dict):
                continue
            for key, record in bucket.items():
                if not isinstance(record, dict) or bool(record.get("closed")):
                    continue
                row = normalize_open_trade("PUMP_DUMP", record, str(key))
                if row["id"] not in seen_pump:
                    rows.append(row)
                    seen_pump.add(row["id"])

    listing = read_json(root, "new_listing_performance_ledger.json", {}, warnings)
    for record in iter_records(listing):
        status = str(record.get("status") or "").upper()
        record_type = str(record.get("record_type") or "").upper()
        if status in {"FINAL", "CLOSED", "EXPIRED", "INVALIDATED"}:
            continue
        if record_type not in {"CONFIRMED_TRADE", "REAL_SIGNAL", "TRADE"}:
            continue
        rows.append(normalize_open_trade("NEW_LISTING", record, str(record.get("record_id") or "listing")))

    rows.sort(key=lambda row: (safe_int(row.get("opened_at")), row.get("symbol", "")), reverse=True)
    return rows


def normalize_closed_result(system: str, record: dict[str, Any], fallback_id: str) -> dict[str, Any] | None:
    outcome = normalize_outcome(first_value(record, ("final_result", "trade_outcome", "outcome", "result")))
    if not outcome or outcome == "OPEN":
        return None
    if system in {"SCALP", "PUMP_DUMP"}:
        stage = str(record.get("stage") or "REAL_SIGNAL").upper()
        if stage != "REAL_SIGNAL":
            return None

    return {
        "id": str(first_value(record, ("trade_id", "id", "record_id"), fallback_id)),
        "system": system,
        "system_label": SYSTEM_LABELS[system],
        "symbol": display_symbol(record),
        "direction": str(record.get("direction") or "—").upper(),
        "source": str(first_value(record, ("setup", "setup_name", "source", "alert_type"), "—")),
        "outcome": outcome,
        "r_result": safe_float(first_value(record, ("r_result", "trade_result_r", "result_r"))),
        "entry": safe_float(first_value(record, ("entry", "analysis_entry", "alert_price"))),
        "exit_price": safe_float(first_value(record, ("exit_price", "trade_exit_price", "latest_price"))),
        "tp1": safe_float(record.get("tp1")),
        "tp2": safe_float(record.get("tp2")),
        "tp3": safe_float(record.get("tp3")),
        "sl": safe_float(record.get("sl")),
        "opened_at": record_timestamp(record, "opened_at", "sent_at", "recorded_at", "first_seen_at"),
        "closed_at": record_timestamp(record, "closed_at", "trade_closed_at", "finalized_at", "last_updated_at"),
    }


def collect_closed_results(root: Path, warnings: list[str]) -> list[dict[str, Any]]:
    sources = (
        ("PREMIUM", "trade_ledger.json"),
        ("SCALP", "scalp_performance_ledger.json"),
        ("PUMP_DUMP", "pump_performance_ledger.json"),
        ("NEW_LISTING", "new_listing_performance_ledger.json"),
    )
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for system, filename in sources:
        data = read_json(root, filename, {}, warnings)
        for index, record in enumerate(iter_records(data)):
            row = normalize_closed_result(system, record, f"{filename}:{index}")
            if row is None:
                continue
            identity = (system, row["id"])
            if identity in seen:
                continue
            rows.append(row)
            seen.add(identity)
    rows.sort(key=lambda row: (safe_int(row.get("closed_at")), row.get("symbol", "")), reverse=True)
    return rows


def collect_health(root: Path, warnings: list[str]) -> dict[str, Any]:
    report = read_json(root, "system_control_center_report.json", {}, warnings)
    executive = report.get("executive", {}) if isinstance(report, dict) else {}
    raw_components = report.get("components", {}) if isinstance(report, dict) else {}
    components: list[dict[str, Any]] = []

    if isinstance(raw_components, dict):
        for key, raw in raw_components.items():
            if not isinstance(raw, dict):
                continue
            performance = raw.get("performance", {})
            if not isinstance(performance, dict):
                performance = {}
            reasons = raw.get("health_reasons", [])
            if not isinstance(reasons, list):
                reasons = [str(reasons)]
            components.append({
                "key": str(key),
                "label": str(raw.get("label") or key),
                "kind": str(raw.get("kind") or "OTHER"),
                "health": str(raw.get("health") or "UNKNOWN").upper(),
                "age_hours": safe_float(raw.get("age_hours")),
                "reasons": [str(reason) for reason in reasons[:3]],
                "open_count": safe_int((raw.get("metrics") or {}).get("open_count")) if isinstance(raw.get("metrics"), dict) else 0,
                "decision": str(performance.get("decision_tr") or "KARAR YOK"),
                "confidence": performance.get("confidence"),
                "sample_size": safe_int(performance.get("sample_size")) if performance.get("sample_size") is not None else None,
                "next_action": performance.get("next_action"),
            })

    order = {"LIVE_SIGNAL": 0, "RADAR": 1, "SHADOW": 2, "INTEGRATED_ANALYSIS": 3, "ANALYSIS": 4, "GUARD": 5}
    components.sort(key=lambda row: (order.get(row["kind"], 9), row["label"]))
    counts = executive.get("health_counts", {}) if isinstance(executive.get("health_counts"), dict) else {}
    return {
        "overall": str(executive.get("overall_health") or "UNKNOWN").upper(),
        "counts": {
            "green": safe_int(counts.get("GREEN")),
            "yellow": safe_int(counts.get("YELLOW")),
            "red": safe_int(counts.get("RED")),
        },
        "generated_at": safe_int(report.get("generated_at")) if isinstance(report, dict) else 0,
        "components": components,
    }


def build_dashboard_data(root: Path | str, now: datetime | None = None) -> dict[str, Any]:
    root = Path(root)
    warnings: list[str] = []
    open_trades = collect_open_trades(root, warnings)
    closed_results = collect_closed_results(root, warnings)
    health = collect_health(root, warnings)
    open_risk = build_open_risk_summary(open_trades)

    open_counts = Counter(row["system"] for row in open_trades)
    outcome_counts = Counter(row["outcome"] for row in closed_results)
    exact_r = [row["r_result"] for row in closed_results if row["r_result"] is not None]
    net_r = round(sum(exact_r), 4) if exact_r else None

    tp_count = sum(count for outcome, count in outcome_counts.items() if outcome.startswith("TP") and "BE" not in outcome)
    be_count = sum(count for outcome, count in outcome_counts.items() if outcome == "BE" or "SONRASI_BE" in outcome)
    sl_count = outcome_counts.get("SL", 0)
    expired_count = outcome_counts.get("EXPIRED", 0)

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    result_breakdown = build_result_breakdown(closed_results, current)
    period_comparisons = build_period_comparisons(closed_results, current)
    performance = build_performance_analytics(closed_results)
    performance_windows: dict[str, dict[str, Any]] = {}
    for key, days, label in (
        ("7D", 7, "Son 7 gün"),
        ("30D", 30, "Son 30 gün"),
        ("90D", 90, "Son 90 gün"),
        ("ALL", None, "Tüm kayıtlar"),
    ):
        cutoff = int(current.timestamp()) - days * 86400 if days else 0
        rows = (
            [row for row in closed_results if safe_int(row.get("closed_at")) >= cutoff]
            if days
            else closed_results
        )
        analytics = build_performance_analytics(rows)
        analytics["label"] = label
        analytics["days"] = days
        performance_windows[key] = analytics
    sources = collect_source_freshness(root, current, warnings)

    live_systems = []
    health_by_key = {row["key"]: row for row in health["components"]}
    for key in ("PREMIUM", "SCALP", "PUMP_DUMP", "NEW_LISTING"):
        component = health_by_key.get(key, {})
        live_systems.append({
            "key": key,
            "label": SYSTEM_LABELS[key],
            "open_count": int(open_counts.get(key, 0)),
            "health": component.get("health", "UNKNOWN"),
            "decision": component.get("decision", "KARAR YOK"),
            "sample_size": component.get("sample_size"),
        })

    return {
        "version": VERSION,
        "mode": "READ_ONLY_NO_ORDERS_NO_TELEGRAM_NO_SIGNAL_CHANGE",
        "generated_at": int(current.timestamp()),
        "generated_at_utc": current.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "generated_at_tr": current.astimezone(TR_TIMEZONE).isoformat(timespec="seconds"),
        "summary": {
            "open_total": len(open_trades),
            "closed_total": len(closed_results),
            "tp": tp_count,
            "sl": sl_count,
            "be": be_count,
            "expired": expired_count,
            "net_r": net_r,
            "exact_r_sample": len(exact_r),
        },
        "live_systems": live_systems,
        "open_risk": open_risk,
        "open_trades": open_trades[:120],
        "recent_results": closed_results[:500],
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "performance": performance,
        "performance_windows": performance_windows,
        "result_breakdown": result_breakdown,
        "period_comparisons": period_comparisons,
        "sources": sources,
        "health": health,
        "data_quality": {
            "ok": not warnings,
            "warnings": sorted(set(warnings)),
        },
    }


def _embedded_json(data: dict[str, Any]) -> str:
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_dashboard(
    data: dict[str, Any] | None,
    *,
    live_endpoint: str | None = None,
    market_endpoint: str | None = None,
    refresh_seconds: int = 30,
    script_nonce: str | None = None,
    top_action_html: str = "",
) -> str:
    live_mode = bool(live_endpoint)
    market_mode = bool(live_endpoint and market_endpoint)
    refresh_seconds = max(10, min(int(refresh_seconds), 300))
    nonce_attr = (
        f' nonce="{html.escape(script_nonce, quote=True)}"'
        if script_nonce
        else ""
    )
    if live_mode:
        endpoint_json = json.dumps(str(live_endpoint), ensure_ascii=False)
        market_endpoint_json = (
            json.dumps(str(market_endpoint), ensure_ascii=False)
            if market_mode
            else "null"
        )
        data_declaration = (
            "let DASHBOARD_DATA = null;\n"
            f"    const LIVE_ENDPOINT = {endpoint_json};\n"
            f"    const MARKET_ENDPOINT = {market_endpoint_json};\n"
            "    let marketInitialized = false;\n"
            f"    const LIVE_REFRESH_SECONDS = {refresh_seconds};"
        )
        live_badge = (
            '<span class="badge"><span id="connectionDot" '
            'class="dot LIVE"></span><span id="connectionBadge">'
            "Canlı veri bağlanıyor</span></span>"
        )
        safety_badge = "✓ Anahtar tarayıcıya çıkmaz"
        eyebrow = "Canlı birleşik operasyon görünümü"
        bootstrap_script = """
    function renderAll() {
      const warningBox=document.getElementById("qualityWarning");
      warningBox.classList.remove("show");
      warningBox.textContent="";
      initHeader(); renderKpis(); renderSystems(); renderRisk(); renderPerformance(); renderDirection(); renderComparison(); renderSources(); renderOpen(); renderResults(); renderHealth();
    }

    function setConnection(status, text) {
      const dot=document.getElementById("connectionDot"), label=document.getElementById("connectionBadge");
      if(!dot||!label)return;
      dot.className="dot "+status;
      label.textContent=text;
    }

    async function loadLiveData(initial=false) {
      if(initial)setConnection("LIVE","Canlı veri bağlanıyor");
      try {
        const response=await fetch(LIVE_ENDPOINT,{credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}});
        if(response.status===401){window.location.assign("/login");return;}
        if(!response.ok)throw new Error("HTTP "+response.status);
        DASHBOARD_DATA=await response.json();
        renderAll();
        if(MARKET_ENDPOINT&&!marketInitialized)initMarket();
        setConnection("GREEN","Canlı · "+new Date().toLocaleTimeString("tr-TR",{hour:"2-digit",minute:"2-digit",second:"2-digit"}));
      } catch(error) {
        setConnection("ERROR",DASHBOARD_DATA?"Bağlantı kesildi · son veri gösteriliyor":"Canlı veri alınamadı");
      }
    }

    loadLiveData(true);
    window.setInterval(()=>loadLiveData(false),LIVE_REFRESH_SECONDS*1000);
"""
    else:
        payload = _embedded_json(data or {})
        data_declaration = (
            f"const DASHBOARD_DATA = {payload};\n"
            "    const LIVE_ENDPOINT = null;\n"
            "    const MARKET_ENDPOINT = null;\n"
            "    let marketInitialized = false;"
        )
        live_badge = '<span class="badge">Anlık görüntü</span>'
        safety_badge = "✓ API anahtarı kullanmaz"
        eyebrow = "Birleşik operasyon görünümü"
        bootstrap_script = (
            "    initHeader(); renderKpis(); renderSystems(); "
            "renderRisk(); renderPerformance(); renderDirection(); renderComparison(); renderSources(); renderOpen(); renderResults(); renderHealth();"
        )

    market_nav_link = '<a href="#market">Coin Grafiği</a>' if market_mode else ""
    market_section = ""
    market_script = ""
    if market_mode:
        market_section = '''
    <section class="section" id="market"><div class="section-head"><div><h2>Coin ve İşlem Grafiği</h2><p>Güncel coinleri veya kapanmış işlemleri gerçekleştiği tarih aralığındaki mumlarla incele</p></div><span class="badge">OKX herkese açık veri · API anahtarı yok</span></div><div class="panel chart-panel"><div class="market-controls"><label>Coin<input id="marketSymbol" list="marketSymbols" value="BTCUSDT" maxlength="24" autocomplete="off" spellcheck="false" aria-label="Coin sembolü"></label><datalist id="marketSymbols"></datalist><label>Periyot<select id="marketBar" aria-label="Mum periyodu"><option>1m</option><option>5m</option><option selected>15m</option><option>1H</option><option>4H</option><option>1D</option></select></label><button id="marketLoad" class="market-button" type="button">Güncel grafiği getir</button><span id="marketStatus" class="badge">Hazır</span></div><div class="canvas-wrap market-canvas"><canvas id="marketCanvas" aria-label="Canlı veya geçmiş mum grafiği"></canvas></div><div id="marketLegend" class="chart-legend"><span>Yeşil/kırmızı: fiyat mumu</span><span>İşlem satırındaki coine tıklarsan o işlemin seviyeleri çizilir</span></div></div></section>
'''
        market_script = '''
    let marketPayload=null,selectedMarketTrade=null;
    const normalizeMarketSymbol=value=>String(value||"").toUpperCase().replace(/[^A-Z0-9]/g,"");

    function marketTrade(symbol) {
      if(selectedMarketTrade&&normalizeMarketSymbol(selectedMarketTrade.symbol)===symbol)return selectedMarketTrade;
      return (DASHBOARD_DATA?.open_trades||[]).find(row=>normalizeMarketSymbol(row.symbol)===symbol)||null;
    }

    function setMarketStatus(text,status="LIVE") {
      const badge=document.getElementById("marketStatus");
      if(!badge)return;
      badge.textContent=text;
      badge.style.borderColor=status==="ERROR"?"rgba(255,98,125,.55)":status==="GREEN"?"rgba(66,226,140,.5)":"";
      badge.style.color=status==="ERROR"?"var(--red)":status==="GREEN"?"var(--green)":"";
    }

    async function loadMarket(symbolValue=null,anchorValue=null) {
      const input=document.getElementById("marketSymbol"), bar=document.getElementById("marketBar");
      const symbol=normalizeMarketSymbol(symbolValue||input.value);
      const anchor=Number(anchorValue??selectedMarketTrade?.closed_at)||0;
      input.value=symbol;
      if(!symbol){setMarketStatus("Coin yazın","ERROR");return;}
      setMarketStatus("Yükleniyor…");
      try {
        const anchorQuery=anchor?`&anchor=${encodeURIComponent(anchor)}`:"";
        const url=`${MARKET_ENDPOINT}?symbol=${encodeURIComponent(symbol)}&bar=${encodeURIComponent(bar.value)}${anchorQuery}`;
        const response=await fetch(url,{credentials:"same-origin",cache:"no-store",headers:{Accept:"application/json"}});
        if(response.status===401){window.location.assign("/login");return;}
        const payload=await response.json();
        if(!response.ok)throw new Error(payload.message||payload.error||`HTTP ${response.status}`);
        marketPayload=payload;
        drawMarketChart();
        const mode=payload.anchor?`Geçmiş · ${fmtDate(payload.anchor)}`:"Güncel";
        setMarketStatus(`${mode} · ${payload.symbol} · ${fmtPrice(payload.last_price)}`,"GREEN");
      } catch(error) {
        setMarketStatus(`Grafik alınamadı: ${error.message}`,"ERROR");
      }
    }

    function drawMarketChart() {
      const canvas=document.getElementById("marketCanvas");
      if(!canvas||!marketPayload)return;
      const candles=marketPayload.candles||[], trade=marketTrade(normalizeMarketSymbol(marketPayload.symbol));
      if(!candles.length)return;
      const levels=trade?[["Giriş",trade.entry,"#60a5fa"],["TP1",trade.tp1,"#42e28c"],["TP2",trade.tp2,"#2ce6bf"],["TP3",trade.tp3,"#18bfa1"],["SL",trade.sl,"#ff627d"],["Çıkış",trade.exit_price,"#ffbd59"]].filter(x=>Number.isFinite(Number(x[1]))):[];
      const values=candles.flatMap(c=>[Number(c.high),Number(c.low)]).concat(levels.map(x=>Number(x[1]))).filter(Number.isFinite);
      const low=Math.min(...values), high=Math.max(...values), pad=Math.max((high-low)*.08,Math.abs(high)*.001,1e-10), min=low-pad, max=high+pad;
      const box=canvas.parentElement.getBoundingClientRect(), dpr=Math.min(window.devicePixelRatio||1,2), width=Math.max(320,box.width), height=Math.max(320,box.height||420);
      canvas.width=width*dpr;canvas.height=height*dpr;canvas.style.width=`${width}px`;canvas.style.height=`${height}px`;
      const ctx=canvas.getContext("2d");ctx.scale(dpr,dpr);ctx.clearRect(0,0,width,height);
      const margin={left:14,right:92,top:18,bottom:30}, chartW=width-margin.left-margin.right, chartH=height-margin.top-margin.bottom;
      const y=value=>margin.top+(max-Number(value))/(max-min)*chartH;
      ctx.strokeStyle="rgba(130,162,159,.14)";ctx.lineWidth=1;ctx.fillStyle="#789894";ctx.font="11px system-ui";
      for(let i=0;i<=5;i++){const yy=margin.top+chartH*i/5;ctx.beginPath();ctx.moveTo(margin.left,yy);ctx.lineTo(width-margin.right,yy);ctx.stroke();ctx.fillText(fmtPrice(max-(max-min)*i/5),width-margin.right+8,yy+4);}
      const step=chartW/candles.length, body=Math.max(2,Math.min(9,step*.64));
      candles.forEach((c,index)=>{const x=margin.left+step*(index+.5), open=y(c.open), close=y(c.close), hi=y(c.high), lo=y(c.low), up=Number(c.close)>=Number(c.open);ctx.strokeStyle=up?"#42e28c":"#ff627d";ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(x,hi);ctx.lineTo(x,lo);ctx.stroke();ctx.fillRect(x-body/2,Math.min(open,close),body,Math.max(1,Math.abs(close-open)));});
      ctx.setLineDash([7,5]);levels.forEach(([label,value,color])=>{const yy=y(value);ctx.strokeStyle=color;ctx.fillStyle=color;ctx.beginPath();ctx.moveTo(margin.left,yy);ctx.lineTo(width-margin.right,yy);ctx.stroke();ctx.fillText(`${label} ${fmtPrice(value)}`,width-margin.right+8,yy+4);});ctx.setLineDash([]);
      const first=candles[0], last=candles[candles.length-1];ctx.fillStyle="#789894";ctx.fillText(new Date(first.ts*1000).toLocaleString("tr-TR",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}),margin.left,height-8);const end=new Date(last.ts*1000).toLocaleString("tr-TR",{day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"});ctx.fillText(end,Math.max(margin.left,width-margin.right-110),height-8);
      const legend=document.getElementById("marketLegend"),tradeMode=trade?.closed_at?`Kapanmış · ${e(trade.outcome)}`:"Açık işlem";legend.innerHTML=trade?`<span><b>${e(trade.system_label)}</b> · ${e(trade.direction)} · ${tradeMode}</span><span>${candles.length} mum · ${e(marketPayload.bar)} · OKX public</span>`:`<span>Bu coinde seçili işlem yok; yalnız güncel fiyat gösteriliyor</span><span>${candles.length} mum · ${e(marketPayload.bar)} · OKX public</span>`;
    }

    function initMarket() {
      if(marketInitialized)return;
      marketInitialized=true;
      const suggestions=[...new Set(["BTCUSDT","ETHUSDT","SOLUSDT",...(DASHBOARD_DATA?.open_trades||[]).map(row=>normalizeMarketSymbol(row.symbol))])];
      document.getElementById("marketSymbols").innerHTML=suggestions.map(symbol=>`<option value="${e(symbol)}"></option>`).join("");
      document.getElementById("marketLoad").addEventListener("click",()=>{selectedMarketTrade=null;loadMarket(null,0);});
      document.getElementById("marketSymbol").addEventListener("keydown",event=>{if(event.key==="Enter"){event.preventDefault();selectedMarketTrade=null;loadMarket(null,0);}});
      document.getElementById("marketBar").addEventListener("change",()=>loadMarket());
      document.addEventListener("click",event=>{const button=event.target.closest("[data-market-symbol]");if(!button)return;const rows=button.dataset.marketKind==="closed"?(DASHBOARD_DATA?.recent_results||[]):(DASHBOARD_DATA?.open_trades||[]);selectedMarketTrade=rows.find(row=>String(row.id)===button.dataset.marketTradeId)||null;document.getElementById("marketSymbol").value=button.dataset.marketSymbol;document.getElementById("market").scrollIntoView({behavior:"smooth"});loadMarket(button.dataset.marketSymbol,selectedMarketTrade?.closed_at||0);});
      loadMarket();
    }
'''
    return f'''<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Kripto Kontrol Merkezi</title>
  <style{nonce_attr}>
    :root{{--bg:#061016;--panel:#0a1820;--panel2:#0d2029;--line:#19343f;--text:#eaf7f4;--muted:#82a29f;--teal:#2ce6bf;--teal2:#10a98e;--green:#42e28c;--amber:#ffbd59;--red:#ff627d;--blue:#60a5fa;--radius:18px}}
    *{{box-sizing:border-box}} html{{scroll-behavior:smooth}} body{{margin:0;background:radial-gradient(circle at 85% 0,#10342f 0,transparent 28%),radial-gradient(circle at 0 35%,#0b2533 0,transparent 25%),var(--bg);color:var(--text);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}}
    body:before{{content:"";position:fixed;inset:0;pointer-events:none;opacity:.16;background-image:linear-gradient(rgba(44,230,191,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(44,230,191,.05) 1px,transparent 1px);background-size:38px 38px;mask-image:linear-gradient(to bottom,black,transparent 72%)}}
    a{{color:inherit;text-decoration:none}} button,input,select{{font:inherit}} .shell{{width:min(1480px,calc(100% - 32px));margin:auto;position:relative}}
    .topbar{{height:76px;border-bottom:1px solid rgba(44,230,191,.16);background:rgba(6,16,22,.78);backdrop-filter:blur(18px);position:sticky;top:0;z-index:10}}
    .topbar .shell{{height:100%;display:flex;align-items:center;justify-content:space-between;gap:20px}} .brand{{display:flex;align-items:center;gap:12px;font-weight:800;letter-spacing:.02em}} .brand-mark{{width:39px;height:39px;border-radius:12px;border:1px solid rgba(44,230,191,.5);background:linear-gradient(145deg,rgba(44,230,191,.22),rgba(44,230,191,.03));display:grid;place-items:center;box-shadow:0 0 30px rgba(44,230,191,.12)}} .brand-mark i{{display:block;width:18px;height:18px;border:3px solid var(--teal);border-left-color:transparent;border-radius:50%;transform:rotate(-24deg);position:relative}} .brand-mark i:after{{content:"";position:absolute;width:6px;height:6px;background:var(--teal);border-radius:50%;right:-5px;top:3px;box-shadow:-12px 8px 0 rgba(44,230,191,.48)}} .brand small{{display:block;color:var(--muted);font-size:10px;letter-spacing:.16em;font-weight:700}}
    .top-actions{{display:flex;align-items:center;gap:9px;flex-wrap:wrap;justify-content:flex-end}} .top-actions form{{margin:0}} .top-actions button{{cursor:pointer}} .badge{{display:inline-flex;align-items:center;gap:7px;padding:7px 10px;border:1px solid var(--line);border-radius:999px;background:rgba(10,24,32,.82);color:#b8cfcc;font-size:12px;font-weight:700}} .dot{{width:8px;height:8px;border-radius:50%;background:var(--muted);box-shadow:0 0 12px currentColor}} .dot.LIVE{{background:var(--blue);color:var(--blue)}} .dot.GREEN{{background:var(--green);color:var(--green)}} .dot.YELLOW{{background:var(--amber);color:var(--amber)}} .dot.RED,.dot.ERROR{{background:var(--red);color:var(--red)}}
    main{{padding:42px 0 70px}} .hero{{display:grid;grid-template-columns:1.55fr .75fr;gap:22px;align-items:stretch;margin-bottom:22px}} .hero-copy,.health-hero{{border:1px solid var(--line);border-radius:24px;background:linear-gradient(145deg,rgba(13,32,41,.94),rgba(7,19,26,.9));overflow:hidden;position:relative}} .hero-copy{{padding:34px}} .eyebrow{{color:var(--teal);font-size:12px;font-weight:850;letter-spacing:.14em;text-transform:uppercase}} h1{{font-size:clamp(32px,5vw,62px);line-height:1.02;letter-spacing:-.052em;margin:14px 0 16px;max-width:900px}} h1 span{{color:var(--teal)}} .lead{{color:#a8c1be;font-size:16px;max-width:760px;margin:0}} .safety{{margin-top:24px;display:flex;gap:10px;flex-wrap:wrap}} .safety .badge:first-child{{border-color:rgba(44,230,191,.35);color:var(--teal)}}
    .health-hero{{padding:26px;display:flex;align-items:center;justify-content:center;gap:24px}} .health-ring{{--score:100;width:132px;height:132px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--green) calc(var(--score)*1%),#17313a 0);position:relative;flex:0 0 auto;box-shadow:0 0 44px rgba(66,226,140,.14)}} .health-ring:before{{content:"";position:absolute;inset:9px;background:#091820;border-radius:50%}} .health-ring>div{{z-index:1;text-align:center}} .health-ring strong{{font-size:30px;display:block;line-height:1}} .health-ring span{{font-size:10px;color:var(--muted);font-weight:800;letter-spacing:.12em}} .health-meta strong{{display:block;font-size:18px}} .health-meta p{{color:var(--muted);margin:7px 0 0}}
    .quality-warning{{display:none;border:1px solid rgba(255,189,89,.42);background:rgba(255,189,89,.08);color:#ffe0aa;padding:13px 16px;border-radius:13px;margin-bottom:20px}} .quality-warning.show{{display:block}}
    .quick-nav{{position:sticky;top:84px;z-index:8;display:flex;gap:7px;overflow:auto;margin:0 0 22px;padding:9px;border:1px solid rgba(25,52,63,.9);border-radius:14px;background:rgba(6,16,22,.88);backdrop-filter:blur(14px);scrollbar-width:thin}} .quick-nav a{{white-space:nowrap;border:1px solid var(--line);border-radius:999px;padding:6px 10px;color:#9db6b3;font-size:11px;font-weight:800}} .quick-nav a:hover{{border-color:var(--teal);color:var(--teal)}}
    .kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin-bottom:32px}} .kpi{{border:1px solid var(--line);border-radius:var(--radius);background:rgba(10,24,32,.9);padding:18px;min-height:112px;position:relative;overflow:hidden}} .kpi:after{{content:"";position:absolute;right:-20px;bottom:-35px;width:90px;height:90px;border-radius:50%;background:var(--accent,var(--teal));filter:blur(40px);opacity:.12}} .kpi label{{display:block;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.1em;font-weight:800}} .kpi strong{{font-size:30px;line-height:1.2;display:block;margin-top:10px}} .kpi small{{color:#8eaaa7}} .kpi.open{{--accent:var(--blue)}} .kpi.tp{{--accent:var(--green)}} .kpi.sl{{--accent:var(--red)}} .kpi.be{{--accent:var(--amber)}}
    .section{{margin-top:32px}} .section-head{{display:flex;align-items:end;justify-content:space-between;gap:18px;margin-bottom:14px}} .section-head h2{{margin:0;font-size:22px;letter-spacing:-.025em}} .section-head p{{color:var(--muted);margin:4px 0 0;font-size:13px}} .filters{{display:flex;gap:7px;flex-wrap:wrap}} .filter{{border:1px solid var(--line);color:#91aaa7;background:#091820;border-radius:999px;padding:7px 11px;cursor:pointer;font-weight:750;font-size:12px}} .filter:hover,.filter.active{{color:#04110e;background:var(--teal);border-color:var(--teal)}} .select-filter{{border:1px solid var(--line);color:#c4d8d5;background:#091820;border-radius:10px;padding:8px 10px;outline:none;font-weight:750;font-size:12px}} .select-filter:focus{{border-color:var(--teal)}}
    .system-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px}} .system-card{{border:1px solid var(--line);border-radius:var(--radius);background:linear-gradient(150deg,rgba(13,32,41,.92),rgba(8,21,28,.88));padding:18px}} .system-top{{display:flex;align-items:center;justify-content:space-between}} .system-card h3{{margin:13px 0 5px;font-size:17px}} .system-card .open-number{{font-size:25px;font-weight:850}} .system-card .caption{{color:var(--muted);font-size:12px}} .decision{{margin-top:15px;padding-top:12px;border-top:1px solid var(--line);font-size:12px;color:#b9cecb;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
    .risk-panel{{padding:16px}} .risk-grid{{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}} .risk-card{{border:1px solid var(--line);border-radius:14px;background:rgba(10,24,32,.82);padding:14px;min-height:105px}} .risk-card label{{display:block;color:var(--muted);font-size:10px;font-weight:850;text-transform:uppercase;letter-spacing:.08em}} .risk-card strong{{display:block;font-size:23px;margin-top:9px}} .risk-card small{{color:var(--muted)}} .risk-systems{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}} .risk-system{{border:1px solid rgba(25,52,63,.72);border-radius:11px;padding:9px 11px;display:flex;justify-content:space-between;color:var(--muted);font-size:11px}} .risk-system b{{color:var(--text)}} .risk-note{{margin-top:12px;color:var(--muted);font-size:11px}} .risk-note.attention{{color:var(--amber)}}
    .analytics-grid{{display:grid;grid-template-columns:.92fr 1.58fr;gap:14px}} .perf-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}} .perf-card{{border:1px solid var(--line);border-radius:14px;background:rgba(10,24,32,.82);padding:14px}} .perf-card h3{{margin:0;font-size:13px}} .perf-number{{font-size:23px;font-weight:850;margin:8px 0 2px}} .perf-meta{{display:flex;gap:9px;flex-wrap:wrap;color:var(--muted);font-size:11px}} .canvas-panel{{padding:16px;min-height:300px}} .canvas-head{{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;margin-bottom:10px}} .canvas-head h3{{margin:0}} .canvas-head p{{margin:3px 0 0;color:var(--muted);font-size:11px}} .canvas-wrap{{position:relative;width:100%;min-height:240px}} .canvas-wrap canvas{{display:block;width:100%;height:100%}}
    .direction-layout{{display:grid;grid-template-columns:.78fr 1.42fr;gap:14px}} .direction-cards{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}} .direction-card{{border:1px solid var(--line);border-radius:14px;background:rgba(10,24,32,.82);padding:15px}} .direction-card h3{{display:flex;align-items:center;justify-content:space-between;margin:0;font-size:14px}} .direction-card strong{{display:block;font-size:24px;margin:11px 0 3px}} .direction-card p{{color:var(--muted);font-size:11px;margin:0}} .direction-summary{{grid-column:1/-1;border:1px solid var(--line);border-radius:14px;background:rgba(10,24,32,.82);padding:14px;color:var(--muted);font-size:11px;display:grid;gap:7px}} .direction-summary b{{color:var(--text)}}
    .period-grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px}} .period-card{{border:1px solid var(--line);border-radius:15px;background:rgba(10,24,32,.82);padding:15px;min-width:0}} .period-card.total{{border-color:rgba(44,230,191,.36);background:linear-gradient(145deg,rgba(44,230,191,.1),rgba(10,24,32,.82))}} .period-card h3{{font-size:13px;margin:0 0 11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .period-main{{display:flex;align-items:center;justify-content:space-between;gap:8px}} .period-main strong{{font-size:22px}} .period-delta{{font-size:10px;font-weight:900;border-radius:999px;padding:4px 7px;background:rgba(130,162,159,.12);color:var(--muted);white-space:nowrap}} .period-delta.positive{{background:rgba(66,226,140,.12);color:var(--green)}} .period-delta.negative{{background:rgba(255,98,125,.12);color:var(--red)}} .period-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:13px}} .period-stat{{border:1px solid rgba(25,52,63,.72);border-radius:9px;padding:7px 5px;text-align:center}} .period-stat span{{display:block;color:var(--muted);font-size:9px;font-weight:800;text-transform:uppercase}} .period-stat b{{font-size:11px}} .period-previous{{border-top:1px solid var(--line);margin-top:11px;padding-top:9px;color:var(--muted);font-size:10px}} .period-previous b{{color:#c9ddda}}
    .source-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}} .source-card{{border:1px solid var(--line);border-radius:14px;background:rgba(10,24,32,.78);padding:14px;min-height:120px}} .source-card-top{{display:flex;justify-content:space-between;gap:8px;align-items:start}} .source-card h3{{font-size:12px;margin:0}} .source-file{{color:var(--muted);font-size:10px;margin-top:4px;word-break:break-all}} .source-age{{font-size:18px;font-weight:850;margin-top:14px}} .source-card.FRESH{{border-color:rgba(66,226,140,.28)}} .source-card.STALE{{border-color:rgba(255,189,89,.35)}} .source-card.ERROR{{border-color:rgba(255,98,125,.4)}} .source-status{{font-size:9px;font-weight:900;border-radius:999px;padding:4px 7px}} .source-status.FRESH{{background:rgba(66,226,140,.12);color:var(--green)}} .source-status.STALE{{background:rgba(255,189,89,.12);color:var(--amber)}} .source-status.ERROR{{background:rgba(255,98,125,.12);color:var(--red)}} .source-status.UNKNOWN{{background:rgba(130,162,159,.12);color:var(--muted)}}
    .chart-panel{{padding:16px}} .market-controls{{display:flex;align-items:end;gap:10px;flex-wrap:wrap;margin-bottom:14px}} .market-controls label{{display:grid;gap:5px;color:var(--muted);font-size:10px;font-weight:850;text-transform:uppercase;letter-spacing:.08em}} .market-controls input,.market-controls select{{border:1px solid var(--line);border-radius:10px;background:#061219;color:var(--text);padding:10px 11px;outline:none;min-width:160px}} .market-controls select{{min-width:100px}} .market-controls input:focus,.market-controls select:focus{{border-color:var(--teal)}} .market-button{{border:0;border-radius:10px;background:var(--teal);color:#03110e;font-weight:900;padding:11px 15px;cursor:pointer}} .market-canvas{{height:440px;border:1px solid rgba(25,52,63,.72);border-radius:12px;background:#07151c;overflow:hidden}} .chart-legend{{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;color:var(--muted);font-size:11px;margin-top:10px}} .symbol-button{{border:0;padding:0;background:none;color:var(--teal);font-weight:850;letter-spacing:.02em;cursor:pointer;text-align:left}} .symbol-button:hover{{text-decoration:underline}}
    .panel{{border:1px solid var(--line);border-radius:var(--radius);background:rgba(9,23,31,.9);overflow:hidden}} .toolbar{{padding:12px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:10px}} .result-toolbar{{justify-content:flex-start;flex-wrap:wrap}} .result-toolbar .search{{margin-right:auto}} .export-button{{border:1px solid rgba(44,230,191,.38);border-radius:10px;background:rgba(44,230,191,.08);color:var(--teal);padding:8px 11px;cursor:pointer;font-weight:850;font-size:12px}} .export-button:hover{{background:var(--teal);color:#03110e}} .search{{width:min(300px,100%);border:1px solid var(--line);border-radius:10px;background:#061219;color:var(--text);padding:9px 11px;outline:none}} .search:focus{{border-color:var(--teal)}} .table-wrap{{overflow:auto}} table{{border-collapse:collapse;width:100%;min-width:960px}} th{{text-align:left;color:#789894;font-size:10px;text-transform:uppercase;letter-spacing:.1em;background:#08171e}} th,td{{padding:13px 14px;border-bottom:1px solid rgba(25,52,63,.72)}} tbody tr:hover{{background:rgba(44,230,191,.035)}} tbody tr:last-child td{{border-bottom:0}} .symbol{{font-weight:850;letter-spacing:.02em}} .sub{{display:block;color:var(--muted);font-size:11px;margin-top:2px;max-width:220px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}} .direction,.result-pill,.health-pill{{display:inline-flex;padding:4px 8px;border-radius:999px;font-size:10px;font-weight:850;letter-spacing:.05em}} .direction.LONG{{background:rgba(66,226,140,.12);color:var(--green)}} .direction.SHORT{{background:rgba(255,98,125,.12);color:#ff8ca0}} .result-pill.TP{{background:rgba(66,226,140,.12);color:var(--green)}} .result-pill.SL{{background:rgba(255,98,125,.12);color:var(--red)}} .result-pill.BE,.result-pill.EXPIRED{{background:rgba(255,189,89,.12);color:var(--amber)}} .progress{{display:flex;gap:5px;min-width:170px}} .step{{height:6px;flex:1;border-radius:9px;background:#173039}} .step.hit{{background:var(--teal);box-shadow:0 0 10px rgba(44,230,191,.3)}} .price-stack{{font-variant-numeric:tabular-nums}} .price-stack small{{display:block;color:var(--muted)}}
    .result-layout{{display:grid;grid-template-columns:.72fr 1.28fr;gap:14px}} .distribution{{padding:20px}} .distribution h3{{margin:0 0 18px}} .bar-row{{margin:14px 0}} .bar-label{{display:flex;justify-content:space-between;color:#b8cecb;font-size:12px;margin-bottom:7px}} .bar{{height:8px;background:#173039;border-radius:99px;overflow:hidden}} .bar i{{display:block;height:100%;width:0;border-radius:99px;background:var(--color,var(--teal))}} .distribution-note{{color:var(--muted);font-size:11px;margin-top:18px}} .pagination{{display:flex;align-items:center;justify-content:center;gap:10px;padding:12px;border-top:1px solid var(--line)}} .page-button{{border:1px solid var(--line);border-radius:9px;background:#091820;color:#c4d8d5;padding:7px 11px;cursor:pointer;font-weight:800}} .page-button:hover:not(:disabled){{border-color:var(--teal);color:var(--teal)}} .page-button:disabled{{opacity:.35;cursor:not-allowed}} .page-info{{color:var(--muted);font-size:11px;min-width:110px;text-align:center}}
    .health-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}} .health-card{{border:1px solid var(--line);border-radius:15px;background:rgba(10,24,32,.82);padding:15px;min-height:150px}} .health-card-top{{display:flex;align-items:flex-start;justify-content:space-between;gap:10px}} .health-card h3{{font-size:14px;margin:0}} .kind{{color:var(--muted);font-size:10px;font-weight:800;letter-spacing:.07em;margin-top:3px}} .health-pill.GREEN{{color:var(--green);background:rgba(66,226,140,.12)}} .health-pill.YELLOW{{color:var(--amber);background:rgba(255,189,89,.12)}} .health-pill.RED{{color:var(--red);background:rgba(255,98,125,.12)}} .health-pill.UNKNOWN{{color:var(--muted);background:rgba(130,162,159,.12)}} .health-reason{{color:#9db6b3;font-size:12px;margin:14px 0}} .health-foot{{border-top:1px solid var(--line);padding-top:10px;color:var(--muted);font-size:11px}} .health-foot b{{color:#cce0dd;font-weight:750}} .empty{{padding:48px 20px;text-align:center;color:var(--muted)}}
    footer{{margin-top:42px;padding-top:22px;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:16px;color:var(--muted);font-size:11px}}
    @media(max-width:1050px){{.hero{{grid-template-columns:1fr}}.kpis{{grid-template-columns:repeat(3,1fr)}}.system-grid{{grid-template-columns:repeat(2,1fr)}}.risk-grid{{grid-template-columns:repeat(3,1fr)}}.risk-systems{{grid-template-columns:repeat(2,1fr)}}.source-grid{{grid-template-columns:repeat(2,1fr)}}.health-grid{{grid-template-columns:repeat(2,1fr)}}.period-grid{{grid-template-columns:repeat(2,1fr)}}.result-layout,.analytics-grid,.direction-layout{{grid-template-columns:1fr}}}}
    @media(max-width:680px){{.shell{{width:min(100% - 20px,1480px)}}.topbar{{height:auto;min-height:70px;padding:10px 0}}.brand small,.top-actions .badge:nth-child(2){{display:none}}main{{padding-top:24px}}.quick-nav{{top:76px}}.hero-copy{{padding:24px}}.health-hero{{justify-content:flex-start}}.kpis{{grid-template-columns:repeat(2,1fr)}}.kpi:last-child{{grid-column:1/-1}}.system-grid,.health-grid,.source-grid,.perf-grid,.risk-grid,.risk-systems,.direction-cards,.period-grid{{grid-template-columns:1fr}}.direction-summary{{grid-column:auto}}.section-head{{align-items:flex-start;flex-direction:column}}.toolbar{{align-items:stretch;flex-direction:column}}.search{{width:100%}}.market-controls>*{{width:100%}}.market-controls input,.market-controls select{{width:100%}}.market-canvas{{height:360px}}footer{{flex-direction:column}}}}
  </style>
</head>
<body>
  <header class="topbar"><div class="shell"><a class="brand" href="#top"><span class="brand-mark"><i></i></span><span>Kripto Kontrol<small>GERÇEK VERİ MERKEZİ</small></span></a><div class="top-actions">{live_badge}<span class="badge"><span id="topHealthDot" class="dot"></span><span id="topHealth">Kontrol ediliyor</span></span><span class="badge">Salt okunur</span><span class="badge" id="generatedBadge">—</span>{top_action_html}</div></div></header>
  <main id="top" class="shell">
    <section class="hero"><div class="hero-copy"><div class="eyebrow">{eyebrow}</div><h1>Gerçek sinyaller.<br><span>Tek kontrol ekranı.</span></h1><p class="lead">Premium, Scalp, Pump/Dump ve Yeni Liste işlemleri; TP/SL sonuçları ve System Control sağlığı aynı panelde. Bu ekran veri gösterir, işlem açmaz.</p><div class="safety"><span class="badge">{safety_badge}</span><span class="badge">✓ Telegram göndermez</span><span class="badge">✓ Stratejiye dokunmaz</span></div></div><div class="health-hero"><div id="healthRing" class="health-ring"><div><strong id="healthScore">—</strong><span>SİSTEM SAĞLIĞI</span></div></div><div class="health-meta"><strong id="overallHealth">—</strong><p id="healthBreakdown">—</p><p id="healthAge">—</p></div></div></section>
    <div id="qualityWarning" class="quality-warning"></div>
    <nav class="quick-nav" aria-label="Panel bölümleri"><a href="#systems">Sistemler</a><a href="#risk">Açık Risk</a><a href="#performance">Performans</a><a href="#direction-performance">Yön / Gün</a><a href="#period-comparison">Dönem</a><a href="#sources">Veri</a>{market_nav_link}<a href="#open">Açık İşlemler</a><a href="#results">Geçmiş</a><a href="#health">Sağlık</a></nav>
    <section id="kpis" class="kpis"></section>

    <section class="section" id="systems"><div class="section-head"><div><h2>Canlı Sistemler</h2><p>Gerçek işlem sinyali üreten veya gerçek sinyal takibi yapan katmanlar</p></div></div><div id="systemGrid" class="system-grid"></div></section>

    <section class="section" id="risk"><div class="section-head"><div><h2>Açık Risk Özeti</h2><p>Açık kayıtların yön, stop mesafesi ve hedef/risk görünümü; bilgi amaçlıdır, emir üretmez</p></div></div><div class="panel risk-panel"><div id="riskGrid" class="risk-grid"></div><div id="riskSystems" class="risk-systems"></div><div id="riskNote" class="risk-note"></div></div></section>

    <section class="section" id="performance"><div class="section-head"><div><h2>Performans Analitiği</h2><p>Premium, Scalp, Pump/Dump ve Yeni Liste sonuçları ayrı örneklerle; yalnız kesin R kayıtları özsermaye eğrisine girer</p></div><select id="performanceWindow" class="select-filter" aria-label="Performans dönemi"><option value="7D">Son 7 gün</option><option value="30D">Son 30 gün</option><option value="90D">Son 90 gün</option><option value="ALL" selected>Tüm kayıtlar</option></select></div><div class="analytics-grid"><div id="performanceGrid" class="perf-grid"></div><div class="panel canvas-panel"><div class="canvas-head"><div><h3>Kümülatif Net R</h3><p id="equityCaption">Kapanış sırasına göre gerçek ledger sonuçları</p></div><span id="drawdownBadge" class="badge">Maks. DD: —</span></div><div class="canvas-wrap"><canvas id="equityCanvas" aria-label="Kümülatif Net R grafiği"></canvas></div></div></div></section>

    <section class="section" id="direction-performance"><div class="section-head"><div><h2>Yön ve Gün Analizi</h2><p>LONG ile SHORT sonuçlarını ayrı örneklerle karşılaştır; son 30 günlük Net R hareketini Türkiye tarihine göre izle</p></div></div><div class="direction-layout"><div id="directionCards" class="direction-cards"></div><div class="panel canvas-panel"><div class="canvas-head"><div><h3>Son 30 Günlük Net R</h3><p>Yalnız kesin R değeri bulunan kapanışlar günlük toplamı etkiler</p></div><span id="sequenceBadge" class="badge">Son seri: —</span></div><div class="canvas-wrap"><canvas id="dailyCanvas" aria-label="Son 30 günlük Net R grafiği"></canvas></div></div></div></section>

    <section class="section" id="period-comparison"><div class="section-head"><div><h2>Dönem Karşılaştırması</h2><p>Son dönemi önceki eşit dönemle sistem sistem karşılaştır; karar verirken örnek sayısını Net R ile birlikte değerlendir</p></div><select id="comparisonWindow" class="select-filter" aria-label="Karşılaştırma dönemi"><option value="7D">7 gün / önceki 7 gün</option><option value="30D" selected>30 gün / önceki 30 gün</option></select></div><div id="periodGrid" class="period-grid"></div></section>

    <section class="section" id="sources"><div class="section-head"><div><h2>Veri Güncelliği</h2><p>Her veri kaynağı ayrı kontrol edilir; eski veya zamanı belirsiz kritik kayıtlar uyarı üretir</p></div></div><div id="sourceGrid" class="source-grid"></div></section>

{market_section}

    <section class="section" id="open"><div class="section-head"><div><h2>Açık İşlemler</h2><p>State dosyalarındaki halen açık gerçek kayıtlar</p></div><div id="openFilters" class="filters"></div></div><div class="panel"><div class="toolbar"><input id="searchInput" class="search" type="search" placeholder="Coin ara…" aria-label="Coin ara"><span class="badge" id="openCountBadge">0 kayıt</span></div><div class="table-wrap"><table><thead><tr><th>Sistem / Coin</th><th>Yön</th><th>Giriş / Son</th><th>TP1 / TP2 / TP3</th><th>Stop</th><th>İlerleme</th><th>Açılış</th></tr></thead><tbody id="openRows"></tbody></table><div id="openEmpty" class="empty" hidden>Açık işlem bulunmuyor.</div></div></div></section>

    <section class="section" id="results"><div class="section-head"><div><h2>İşlem İnceleme Merkezi</h2><p>TP/SL geçmişini filtrele; canlı panelde coin adına tıklayarak işlemi gerçekleştiği mumlarda aç</p></div></div><div class="result-layout"><div class="panel distribution"><h3>Filtreli sonuç dağılımı</h3><div id="distribution"></div><p class="distribution-note">Net R yalnız kesin R değeri bulunan kayıtların toplamıdır. Farklı sistemlerin örnekleri ayrı ledger kaynaklarından gelir.</p></div><div class="panel"><div class="toolbar result-toolbar"><input id="resultSearch" class="search" type="search" placeholder="Geçmişte coin ara…" aria-label="Geçmişte coin ara"><select id="resultSystem" class="select-filter" aria-label="Sonuç sistemi"><option value="ALL">Tüm sistemler</option><option value="PREMIUM">Premium</option><option value="SCALP">Scalp</option><option value="PUMP_DUMP">Pump/Dump</option><option value="NEW_LISTING">Yeni Liste</option></select><select id="resultOutcome" class="select-filter" aria-label="İşlem sonucu"><option value="ALL">Tüm sonuçlar</option><option value="TP">TP</option><option value="SL">Stop / SL</option><option value="BE">Break-even</option><option value="EXPIRED">Süresi dolan</option></select><select id="resultWindow" class="select-filter" aria-label="Geçmiş dönemi"><option value="7D">7 gün</option><option value="30D">30 gün</option><option value="90D">90 gün</option><option value="ALL" selected>Tümü</option></select><select id="resultPageSize" class="select-filter" aria-label="Sayfadaki işlem sayısı"><option value="20" selected>20 / sayfa</option><option value="50">50 / sayfa</option></select><button id="exportResults" class="export-button" type="button">CSV indir</button><span id="resultCountBadge" class="badge">0 kayıt</span></div><div class="table-wrap"><table><thead><tr><th>Sistem / Coin</th><th>Yön</th><th>Sonuç</th><th>Net R</th><th>Giriş / Çıkış</th><th>Kapanış</th></tr></thead><tbody id="resultRows"></tbody></table><div id="resultEmpty" class="empty" hidden>Bu filtrelerde kapanmış işlem bulunmuyor.</div></div><div id="resultPagination" class="pagination"><button id="resultPrev" class="page-button" type="button">← Önceki</button><span id="resultPageInfo" class="page-info">1 / 1</span><button id="resultNext" class="page-button" type="button">Sonraki →</button></div></div></div></section>

    <section class="section" id="health"><div class="section-head"><div><h2>System Control Sağlığı</h2><p>Teknik çalışma durumu performans kararından ayrı gösterilir</p></div><div id="healthFilters" class="filters"></div></div><div id="healthGrid" class="health-grid"></div></section>

    <footer><span id="footerVersion"></span><span>Finansal tavsiye değildir · Otomatik emir kapalıdır</span></footer>
  </main>
  <script{nonce_attr}>
    {data_declaration}
    const SYSTEMS = ["ALL","PREMIUM","SCALP","PUMP_DUMP","NEW_LISTING"];
    const state = {{ openSystem:"ALL", healthKind:"ALL", query:"", performanceWindow:"ALL", comparisonWindow:"30D", resultSystem:"ALL", resultOutcome:"ALL", resultWindow:"ALL", resultQuery:"", resultPage:1, resultPageSize:20 }};
    const labels = {{ALL:"Tümü",PREMIUM:"Premium",SCALP:"Scalp",PUMP_DUMP:"Pump/Dump",NEW_LISTING:"Yeni Liste",LIVE_SIGNAL:"Canlı",RADAR:"Radar",SHADOW:"Gölge",ANALYSIS:"Analiz",INTEGRATED_ANALYSIS:"Entegre",GUARD:"Koruma"}};
    const e = value => String(value ?? "—").replace(/[&<>"']/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    const fmtPrice = value => {{ const n=Number(value); if(!Number.isFinite(n)) return "—"; const a=Math.abs(n); const digits=a>=100?2:a>=1?4:a>=.01?6:10; return n.toLocaleString("tr-TR",{{maximumFractionDigits:digits}}); }};
    const fmtR = value => {{ const n=Number(value); return Number.isFinite(n) ? `${{n>=0?"+":""}}${{n.toFixed(3)}}R` : "—"; }};
    const fmtPercent = value => {{ const n=Number(value); return Number.isFinite(n)?`%${{n.toFixed(2)}}`:"—"; }};
    const fmtDate = ts => {{ const n=Number(ts); if(!n) return "—"; return new Intl.DateTimeFormat("tr-TR",{{timeZone:"Europe/Istanbul",day:"2-digit",month:"2-digit",hour:"2-digit",minute:"2-digit"}}).format(new Date(n*1000)); }};
    const ageText = ts => {{ const n=Number(ts); if(!n) return "veri zamanı yok"; const sec=Math.max(0,Date.now()/1000-n); if(sec<3600)return `${{Math.max(1,Math.round(sec/60))}} dk önce`; if(sec<86400)return `${{(sec/3600).toFixed(1)}} sa önce`; return `${{Math.round(sec/86400)}} gün önce`; }};
    const outcomeClass = outcome => outcome==="SL"?"SL":(outcome==="BE"||String(outcome).includes("BE"))?"BE":outcome==="EXPIRED"?"EXPIRED":String(outcome).startsWith("TP")?"TP":"BE";
    const healthScore = counts => {{ const total=counts.green+counts.yellow+counts.red; return total ? Math.round((counts.green+counts.yellow*.5)/total*100) : 0; }};

    function initHeader() {{
      const health=DASHBOARD_DATA.health, score=healthScore(health.counts), overall=health.overall;
      document.getElementById("topHealth").textContent=`System Control: ${{overall}}`;
      document.getElementById("topHealthDot").className="dot "+overall;
      document.getElementById("generatedBadge").textContent=`Panel: ${{fmtDate(DASHBOARD_DATA.generated_at)}}`;
      document.getElementById("healthScore").textContent=score;
      document.getElementById("healthRing").style.setProperty("--score",score);
      document.getElementById("overallHealth").textContent=overall==="GREEN"?"Tüm sistemler normal":overall==="YELLOW"?"Dikkat gereken bileşen var":overall==="RED"?"Kritik teknik uyarı":"Sağlık verisi bekleniyor";
      document.getElementById("healthBreakdown").textContent=`${{health.counts.green}} yeşil · ${{health.counts.yellow}} sarı · ${{health.counts.red}} kırmızı`;
      document.getElementById("healthAge").textContent=`System Control ${{ageText(health.generated_at)}}`;
      document.getElementById("footerVersion").textContent=`${{DASHBOARD_DATA.version}} · ${{fmtDate(DASHBOARD_DATA.generated_at)}}`;
      const q=DASHBOARD_DATA.data_quality; if(!q.ok){{const box=document.getElementById("qualityWarning");box.classList.add("show");box.textContent=`Veri notu: ${{q.warnings.join(" · ")}}`;}}
    }}

    function renderKpis() {{
      const s=DASHBOARD_DATA.summary;
      const items=[
        ["Açık gerçek işlem",s.open_total,"Canlı state kayıtları","open"],
        ["TP kapanışı",s.tp,`${{s.closed_total}} kapanmış kayıt içinde`,"tp"],
        ["Stop / SL",s.sl,"Kesin sonuç kayıtları","sl"],
        ["Break-even",s.be,"TP sonrası BE dahil","be"],
        ["Toplam Net R",fmtR(s.net_r),`${{s.exact_r_sample}} kesin R kaydı`,""]
      ];
      document.getElementById("kpis").innerHTML=items.map(x=>`<article class="kpi ${{x[3]}}"><label>${{e(x[0])}}</label><strong>${{e(x[1])}}</strong><small>${{e(x[2])}}</small></article>`).join("");
    }}

    function renderSystems() {{
      document.getElementById("systemGrid").innerHTML=DASHBOARD_DATA.live_systems.map(s=>`<article class="system-card"><div class="system-top"><span class="health-pill ${{e(s.health)}}">${{e(s.health)}}</span><span class="caption">Örnek: ${{s.sample_size??"—"}}</span></div><h3>${{e(s.label)}}</h3><div class="open-number">${{e(s.open_count)}}</div><div class="caption">açık gerçek işlem</div><div class="decision" title="${{e(s.decision)}}">${{e(s.decision)}}</div></article>`).join("");
    }}

    function renderRisk() {{
      const risk=DASHBOARD_DATA.open_risk||{{total:0,long:0,short:0,systems:[]}};
      const items=[
        ["Yön dengesi",`${{risk.long||0}} LONG / ${{risk.short||0}} SHORT`,`${{risk.total||0}} açık kayıt`],
        ["Ortalama stop",fmtPercent(risk.average_stop_percent),`${{risk.with_stop||0}} kayıtta stop`],
        ["En geniş stop",fmtPercent(risk.max_stop_percent),risk.widest_stop_symbol||"kayıt yok"],
        ["Ortalama TP1 R/R",risk.average_tp1_rr==null?"—":`${{Number(risk.average_tp1_rr).toFixed(2)}}R`,"ilk hedef / stop"],
        ["Ortalama TP3 R/R",risk.average_tp3_rr==null?"—":`${{Number(risk.average_tp3_rr).toFixed(2)}}R`,"üçüncü hedef / stop"]
      ];
      document.getElementById("riskGrid").innerHTML=items.map(item=>`<article class="risk-card"><label>${{e(item[0])}}</label><strong>${{e(item[1])}}</strong><small>${{e(item[2])}}</small></article>`).join("");
      document.getElementById("riskSystems").innerHTML=(risk.systems||[]).map(row=>`<div class="risk-system"><span>${{e(row.label)}}</span><b>${{row.count}} açık</b></div>`).join("");
      const notes=[];if(risk.missing_stop)notes.push(`${{risk.missing_stop}} açık kayıtta geçerli giriş/stop mesafesi hesaplanamadı`);if(risk.wide_stop_count)notes.push(`${{risk.wide_stop_count}} açık kayıtta stop mesafesi %2 üzerinde`);const note=document.getElementById("riskNote");note.textContent=notes.length?notes.join(" · "):risk.total?"Tüm açık kayıtlarda giriş ve stop mesafesi hesaplanabildi.":"Açık işlem olmadığı için risk özeti boş.";note.className="risk-note "+(notes.length?"attention":"");
    }}

    const activePerformance=()=>DASHBOARD_DATA.performance_windows?.[state.performanceWindow]||DASHBOARD_DATA.performance||{{systems:[],equity_curve:[]}};

    function renderPerformance() {{
      const perf=activePerformance();
      document.getElementById("performanceGrid").innerHTML=perf.systems.map(row=>`<article class="perf-card"><h3>${{e(row.label)}}</h3><div class="perf-number">${{fmtR(row.net_r)}}</div><div class="perf-meta"><span>${{row.sample}} kapanış</span><span>TP %${{row.tp_rate??"—"}}</span><span>SL %${{row.sl_rate??"—"}}</span><span>${{row.exact_r_sample}} kesin R</span></div></article>`).join("");
      document.getElementById("drawdownBadge").textContent=`Maks. DD: ${{fmtR(perf.max_drawdown_r==null?null:-Math.abs(perf.max_drawdown_r))}}`;
      document.getElementById("equityCaption").textContent=`${{perf.label||"Tüm kayıtlar"}} · ${{perf.exact_r_sample||0}} kesin R kaydı`;
      drawEquityChart();
    }}

    function setupCanvas(canvas,minHeight=240) {{
      const box=canvas.parentElement.getBoundingClientRect(),dpr=Math.min(window.devicePixelRatio||1,2),width=Math.max(300,box.width),height=Math.max(minHeight,box.height||minHeight);
      canvas.width=width*dpr;canvas.height=height*dpr;canvas.style.width=`${{width}}px`;canvas.style.height=`${{height}}px`;
      const ctx=canvas.getContext("2d");ctx.scale(dpr,dpr);return {{ctx,width,height}};
    }}

    function drawEquityChart() {{
      const canvas=document.getElementById("equityCanvas"),points=activePerformance().equity_curve||[];
      if(!canvas)return;
      const {{ctx,width,height}}=setupCanvas(canvas,240);ctx.clearRect(0,0,width,height);
      const margin={{left:46,right:18,top:16,bottom:28}},chartW=width-margin.left-margin.right,chartH=height-margin.top-margin.bottom;
      const values=[0,...points.map(point=>Number(point.cumulative_r))].filter(Number.isFinite),min=Math.min(...values),max=Math.max(...values),span=Math.max(max-min,.5),low=min-span*.12,high=max+span*.12;
      const x=index=>margin.left+(points.length<=1?chartW/2:index/(points.length-1)*chartW),y=value=>margin.top+(high-value)/(high-low)*chartH;
      ctx.strokeStyle="rgba(130,162,159,.14)";ctx.fillStyle="#789894";ctx.font="11px system-ui";ctx.lineWidth=1;
      for(let i=0;i<=4;i++){{const value=high-(high-low)*i/4,yy=y(value);ctx.beginPath();ctx.moveTo(margin.left,yy);ctx.lineTo(width-margin.right,yy);ctx.stroke();ctx.fillText(`${{value.toFixed(2)}}R`,4,yy+4);}}
      if(low<=0&&high>=0){{ctx.strokeStyle="rgba(255,255,255,.28)";ctx.setLineDash([5,5]);ctx.beginPath();ctx.moveTo(margin.left,y(0));ctx.lineTo(width-margin.right,y(0));ctx.stroke();ctx.setLineDash([]);}}
      if(!points.length){{ctx.fillStyle="#82a29f";ctx.textAlign="center";ctx.fillText("Kesin R sonucu biriktiğinde grafik oluşacak",width/2,height/2);ctx.textAlign="left";return;}}
      const gradient=ctx.createLinearGradient(0,margin.top,0,height-margin.bottom);gradient.addColorStop(0,"rgba(44,230,191,.26)");gradient.addColorStop(1,"rgba(44,230,191,0)");ctx.beginPath();points.forEach((point,index)=>{{const xx=x(index),yy=y(Number(point.cumulative_r));if(index===0)ctx.moveTo(xx,yy);else ctx.lineTo(xx,yy);}});ctx.lineTo(x(points.length-1),height-margin.bottom);ctx.lineTo(x(0),height-margin.bottom);ctx.closePath();ctx.fillStyle=gradient;ctx.fill();
      ctx.beginPath();points.forEach((point,index)=>{{const xx=x(index),yy=y(Number(point.cumulative_r));if(index===0)ctx.moveTo(xx,yy);else ctx.lineTo(xx,yy);}});ctx.strokeStyle="#2ce6bf";ctx.lineWidth=2;ctx.stroke();
      const last=points[points.length-1];ctx.fillStyle="#2ce6bf";ctx.beginPath();ctx.arc(x(points.length-1),y(Number(last.cumulative_r)),4,0,Math.PI*2);ctx.fill();ctx.fillStyle="#789894";ctx.fillText(`${{points.length}} kesin R kaydı`,margin.left,height-7);
    }}

    function renderDirection() {{
      const breakdown=DASHBOARD_DATA.result_breakdown||{{directions:[],daily_30d:[],recent_sequence:{{type:null,count:0}},best_day:null,worst_day:null}};
      const directions=["LONG","SHORT"].map(direction=>(breakdown.directions||[]).find(row=>row.direction===direction)||{{direction,sample:0,tp:0,sl:0,tp_rate:null,sl_rate:null,exact_r_sample:0,net_r:null,average_r:null}});
      const directionHtml=directions.map(row=>`<article class="direction-card"><h3><span>${{e(row.direction)}} sonuçları</span><span class="direction ${{e(row.direction)}}">${{e(row.direction)}}</span></h3><strong>${{row.net_r==null?"—":fmtR(row.net_r)}}</strong><p>${{row.sample}} kapanış · ${{row.exact_r_sample}} kesin R</p><p>TP %${{row.tp_rate??"—"}} · SL %${{row.sl_rate??"—"}} · Ort. ${{row.average_r==null?"—":fmtR(row.average_r)}}</p></article>`).join("");
      const sequence=breakdown.recent_sequence||{{type:null,count:0}},sequenceText=sequence.type?`${{e(sequence.type)}} · ${{sequence.count}} işlem`:"Henüz sonuç yok";
      const dayText=day=>day?`${{e(String(day.date).slice(5).replace("-","/"))}} · ${{fmtR(day.net_r)}}`:"—";
      document.getElementById("directionCards").innerHTML=directionHtml+`<div class="direction-summary"><span><b>Son sonuç serisi:</b> ${{sequenceText}}</span><span><b>30 günün en iyi günü:</b> ${{dayText(breakdown.best_day)}}</span><span><b>30 günün en zayıf günü:</b> ${{dayText(breakdown.worst_day)}}</span></div>`;
      document.getElementById("sequenceBadge").textContent=`Son seri: ${{sequence.type?`${{sequence.type}} · ${{sequence.count}}`:"—"}}`;
      drawDailyChart();
    }}

    function drawDailyChart() {{
      const canvas=document.getElementById("dailyCanvas"),rows=DASHBOARD_DATA.result_breakdown?.daily_30d||[];
      if(!canvas)return;
      const {{ctx,width,height}}=setupCanvas(canvas,240);ctx.clearRect(0,0,width,height);
      const margin={{left:46,right:16,top:16,bottom:32}},chartW=width-margin.left-margin.right,chartH=height-margin.top-margin.bottom;
      const values=rows.map(row=>Number(row.net_r)).filter(Number.isFinite),min=Math.min(0,...values),max=Math.max(0,...values),span=Math.max(max-min,.5),low=min-span*.16,high=max+span*.16;
      const y=value=>margin.top+(high-value)/(high-low)*chartH,x=index=>margin.left+(index+.5)/Math.max(rows.length,1)*chartW;
      ctx.strokeStyle="rgba(130,162,159,.14)";ctx.fillStyle="#789894";ctx.font="11px system-ui";ctx.lineWidth=1;
      for(let i=0;i<=4;i++){{const value=high-(high-low)*i/4,yy=y(value);ctx.beginPath();ctx.moveTo(margin.left,yy);ctx.lineTo(width-margin.right,yy);ctx.stroke();ctx.fillText(`${{value.toFixed(2)}}R`,4,yy+4);}}
      const zeroY=y(0);ctx.strokeStyle="rgba(255,255,255,.3)";ctx.setLineDash([5,5]);ctx.beginPath();ctx.moveTo(margin.left,zeroY);ctx.lineTo(width-margin.right,zeroY);ctx.stroke();ctx.setLineDash([]);
      if(!rows.some(row=>Number(row.exact_r_sample)>0)){{ctx.fillStyle="#82a29f";ctx.textAlign="center";ctx.fillText("Son 30 günde kesin R kaydı bulunmuyor",width/2,height/2);ctx.textAlign="left";return;}}
      const barWidth=Math.max(3,Math.min(16,chartW/Math.max(rows.length,1)*.62));
      rows.forEach((row,index)=>{{const value=Number(row.net_r)||0,yy=y(value),top=Math.min(zeroY,yy),barHeight=Math.max(1,Math.abs(zeroY-yy));ctx.fillStyle=value>0?"#42e28c":value<0?"#ff627d":"#36515a";ctx.fillRect(x(index)-barWidth/2,top,barWidth,barHeight);}});
      [0,7,14,21,29].forEach(index=>{{const row=rows[index];if(!row)return;ctx.fillStyle="#789894";ctx.textAlign=index===0?"left":index===29?"right":"center";ctx.fillText(String(row.date).slice(5).replace("-","/"),x(index),height-8);}});ctx.textAlign="left";
    }}

    function renderComparison() {{
      const comparison=DASHBOARD_DATA.period_comparisons?.[state.comparisonWindow]||{{label:"Son dönem",previous_label:"Önceki dönem",rows:[]}};
      document.getElementById("periodGrid").innerHTML=(comparison.rows||[]).map(row=>{{
        const current=row.current||{{}},previous=row.previous||{{}},delta=Number(row.net_r_delta),hasDelta=row.net_r_delta!=null,deltaClass=hasDelta?(delta>0?"positive":delta<0?"negative":""):"",deltaText=hasDelta?`${{delta>0?"↑ ":delta<0?"↓ ":""}}${{fmtR(delta)}}`:"Karşılaştırma yok";
        const rate=value=>value==null?"—":`%${{Number(value).toFixed(1)}}`;
        return `<article class="period-card ${{row.system==="ALL"?"total":""}}"><h3>${{e(row.label)}}</h3><div class="period-main"><strong>${{current.net_r==null?"—":fmtR(current.net_r)}}</strong><span class="period-delta ${{deltaClass}}">${{e(deltaText)}}</span></div><div class="period-stats"><div class="period-stat"><span>Örnek</span><b>${{current.sample??0}}</b></div><div class="period-stat"><span>TP</span><b>${{rate(current.tp_rate)}}</b></div><div class="period-stat"><span>SL</span><b>${{rate(current.sl_rate)}}</b></div></div><div class="period-previous">${{e(comparison.previous_label)}}: <b>${{previous.net_r==null?"—":fmtR(previous.net_r)}}</b> · ${{previous.sample??0}} örnek</div></article>`;
      }}).join("");
    }}

    function renderSources() {{
      const statusLabels={{FRESH:"GÜNCEL",STALE:"ESKİ",UNKNOWN:"ZAMAN YOK",ERROR:"HATA"}};
      document.getElementById("sourceGrid").innerHTML=(DASHBOARD_DATA.sources||[]).map(row=>`<article class="source-card ${{e(row.status)}}"><div class="source-card-top"><div><h3>${{e(row.label)}}</h3><div class="source-file">${{e(row.filename)}}</div></div><span class="source-status ${{e(row.status)}}">${{e(statusLabels[row.status]||row.status)}}</span></div><div class="source-age">${{row.age_hours==null?"—":row.age_hours<1?`${{Math.max(1,Math.round(row.age_hours*60))}} dk`:`${{row.age_hours.toFixed(1)}} sa`}}</div><span class="sub">Eşik: ${{row.threshold_hours}} sa · ${{row.critical?"kritik":"bilgi"}}</span></article>`).join("");
    }}

    function filterButtons(target, values, current, onClick) {{
      const root=document.getElementById(target); root.innerHTML=values.map(v=>`<button class="filter ${{v===current?"active":""}}" data-value="${{e(v)}}">${{e(labels[v]||v)}}</button>`).join("");
      root.querySelectorAll("button").forEach(btn=>btn.addEventListener("click",()=>onClick(btn.dataset.value)));
    }}

    function renderOpen() {{
      filterButtons("openFilters",SYSTEMS,state.openSystem,value=>{{state.openSystem=value;renderOpen();}});
      const query=state.query.trim().toUpperCase();
      const rows=DASHBOARD_DATA.open_trades.filter(r=>(state.openSystem==="ALL"||r.system===state.openSystem)&&(!query||r.symbol.includes(query)));
      document.getElementById("openCountBadge").textContent=`${{rows.length}} kayıt`;
      const tbody=document.getElementById("openRows"), empty=document.getElementById("openEmpty"); empty.hidden=rows.length>0;
      tbody.innerHTML=rows.map(r=>`<tr><td>${{MARKET_ENDPOINT?`<button type="button" class="symbol-button" data-market-kind="open" data-market-trade-id="${{e(r.id)}}" data-market-symbol="${{e(r.symbol)}}">${{e(r.symbol)}}</button>`:`<span class="symbol">${{e(r.symbol)}}</span>`}}<span class="sub">${{e(r.system_label)}} · ${{e(r.source)}}</span></td><td><span class="direction ${{e(r.direction)}}">${{e(r.direction)}}</span></td><td class="price-stack">${{fmtPrice(r.entry)}}<small>Son: ${{fmtPrice(r.last_price)}}</small></td><td class="price-stack">${{fmtPrice(r.tp1)}} / ${{fmtPrice(r.tp2)}}<small>TP3: ${{fmtPrice(r.tp3)}}</small></td><td class="price-stack">${{fmtPrice(r.sl)}}<small>Mesafe: ${{fmtPercent(r.stop_percent)}}</small></td><td><div class="progress" title="${{e(r.progress)}}"><i class="step ${{r.tp1_hit?"hit":""}}"></i><i class="step ${{r.tp2_hit?"hit":""}}"></i><i class="step ${{r.tp3_hit?"hit":""}}"></i></div><span class="sub">${{e(r.progress)}}</span></td><td>${{fmtDate(r.opened_at)}}<span class="sub">${{ageText(r.opened_at)}}</span></td></tr>`).join("");
    }}

    function filteredResultRows() {{
      const query=state.resultQuery.trim().toUpperCase(),days={{"7D":7,"30D":30,"90D":90}}[state.resultWindow]||0,cutoff=days?Number(DASHBOARD_DATA.generated_at)-days*86400:0;
      const outcomeMatches=(outcome,group)=>group==="ALL"||(group==="TP"&&String(outcome).startsWith("TP")&&!String(outcome).includes("BE"))||(group==="BE"&&(outcome==="BE"||String(outcome).includes("SONRASI_BE")))||outcome===group;
      return DASHBOARD_DATA.recent_results.filter(r=>(state.resultSystem==="ALL"||r.system===state.resultSystem)&&outcomeMatches(r.outcome,state.resultOutcome)&&(!cutoff||Number(r.closed_at)>=cutoff)&&(!query||String(r.symbol).includes(query)));
    }}

    function renderResults() {{
      const filteredRows=filteredResultRows();
      const totalPages=Math.max(1,Math.ceil(filteredRows.length/state.resultPageSize));state.resultPage=Math.min(Math.max(1,state.resultPage),totalPages);const start=(state.resultPage-1)*state.resultPageSize,rows=filteredRows.slice(start,start+state.resultPageSize);
      document.getElementById("resultCountBadge").textContent=filteredRows.length?`${{start+1}}–${{start+rows.length}} / ${{filteredRows.length}} kayıt`:"0 kayıt";
      document.getElementById("resultEmpty").hidden=filteredRows.length>0;
      document.getElementById("resultPageInfo").textContent=`Sayfa ${{state.resultPage}} / ${{totalPages}}`;
      document.getElementById("resultPrev").disabled=state.resultPage<=1;
      document.getElementById("resultNext").disabled=state.resultPage>=totalPages;
      document.getElementById("resultPagination").hidden=filteredRows.length===0;
      document.getElementById("resultRows").innerHTML=rows.map(r=>`<tr><td>${{MARKET_ENDPOINT&&r.closed_at?`<button type="button" class="symbol-button" data-market-kind="closed" data-market-trade-id="${{e(r.id)}}" data-market-symbol="${{e(r.symbol)}}">${{e(r.symbol)}}</button>`:`<span class="symbol">${{e(r.symbol)}}</span>`}}<span class="sub">${{e(r.system_label)}} · ${{e(r.source)}}</span></td><td><span class="direction ${{e(r.direction)}}">${{e(r.direction)}}</span></td><td><span class="result-pill ${{outcomeClass(r.outcome)}}">${{e(r.outcome)}}</span></td><td>${{fmtR(r.r_result)}}</td><td class="price-stack">${{fmtPrice(r.entry)}}<small>Çıkış: ${{fmtPrice(r.exit_price)}}</small></td><td>${{fmtDate(r.closed_at)}}</td></tr>`).join("");
      const counts=filteredRows.reduce((acc,row)=>{{acc[row.outcome]=(acc[row.outcome]||0)+1;return acc;}},{{}}), total=filteredRows.length||1;
      const groups=[
        ["TP",Object.entries(counts).filter(([k])=>k.startsWith("TP")&&!k.includes("BE")).reduce((a,[,v])=>a+v,0),"#42e28c"],
        ["Break-even",Object.entries(counts).filter(([k])=>k==="BE"||k.includes("SONRASI_BE")).reduce((a,[,v])=>a+v,0),"#ffbd59"],
        ["Stop / SL",counts.SL||0,"#ff627d"],
        ["Süresi dolan",counts.EXPIRED||0,"#60a5fa"]
      ];
      document.getElementById("distribution").innerHTML=groups.map(g=>`<div class="bar-row"><div class="bar-label"><span>${{e(g[0])}}</span><b>${{g[1]}}</b></div><div class="bar"><i style="--color:${{g[2]}};width:${{Math.max(g[1]?2:0,g[1]/total*100)}}%"></i></div></div>`).join("");
    }}

    function exportFilteredResults() {{
      const rows=filteredResultRows();
      if(!rows.length)return;
      const csvCell=value=>{{let text=String(value??"");if(/^[-=+@]/.test(text))text="'"+text;return `"${{text.replace(/"/g,'""')}}"`;}};
      const headers=["Sistem","Coin","Yön","Kaynak","Sonuç","Net R","Giriş","Çıkış","Açılış UTC","Kapanış UTC"];
      const lines=[headers,...rows.map(row=>[row.system_label,row.symbol,row.direction,row.source,row.outcome,row.r_result,row.entry,row.exit_price,row.opened_at?new Date(row.opened_at*1000).toISOString():"",row.closed_at?new Date(row.closed_at*1000).toISOString():""])];
      const csv="\ufeff"+lines.map(line=>line.map(csvCell).join(";")).join("\\r\\n"),blob=new Blob([csv],{{type:"text/csv;charset=utf-8"}}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=`kripto-islem-gecmisi-${{new Date().toISOString().slice(0,10)}}.csv`;document.body.appendChild(link);link.click();link.remove();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
    }}

    function renderHealth() {{
      const kinds=["ALL",...new Set(DASHBOARD_DATA.health.components.map(c=>c.kind))];
      filterButtons("healthFilters",kinds,state.healthKind,value=>{{state.healthKind=value;renderHealth();}});
      const rows=DASHBOARD_DATA.health.components.filter(c=>state.healthKind==="ALL"||c.kind===state.healthKind);
      document.getElementById("healthGrid").innerHTML=rows.map(c=>`<article class="health-card"><div class="health-card-top"><div><h3>${{e(c.label)}}</h3><div class="kind">${{e(labels[c.kind]||c.kind)}} · ${{c.age_hours==null?"yaş yok":`${{c.age_hours}} saat`}}</div></div><span class="health-pill ${{e(c.health)}}">${{e(c.health)}}</span></div><p class="health-reason">${{e(c.reasons[0]||"Sağlık açıklaması yok")}}</p><div class="health-foot"><b>${{e(c.decision)}}</b>${{c.next_action?`<span class="sub" title="${{e(c.next_action)}}">${{e(c.next_action)}}</span>`:""}}</div></article>`).join("");
    }}

    document.getElementById("searchInput").addEventListener("input",event=>{{state.query=event.target.value;renderOpen();}});
    document.getElementById("performanceWindow").addEventListener("change",event=>{{state.performanceWindow=event.target.value;renderPerformance();}});
    document.getElementById("comparisonWindow").addEventListener("change",event=>{{state.comparisonWindow=event.target.value;renderComparison();}});
    document.getElementById("resultSearch").addEventListener("input",event=>{{state.resultQuery=event.target.value;state.resultPage=1;renderResults();}});
    document.getElementById("resultSystem").addEventListener("change",event=>{{state.resultSystem=event.target.value;state.resultPage=1;renderResults();}});
    document.getElementById("resultOutcome").addEventListener("change",event=>{{state.resultOutcome=event.target.value;state.resultPage=1;renderResults();}});
    document.getElementById("resultWindow").addEventListener("change",event=>{{state.resultWindow=event.target.value;state.resultPage=1;renderResults();}});
    document.getElementById("resultPageSize").addEventListener("change",event=>{{state.resultPageSize=Number(event.target.value)||20;state.resultPage=1;renderResults();}});
    document.getElementById("exportResults").addEventListener("click",exportFilteredResults);
    document.getElementById("resultPrev").addEventListener("click",()=>{{state.resultPage=Math.max(1,state.resultPage-1);renderResults();document.getElementById("results").scrollIntoView({{behavior:"smooth",block:"start"}});}});
    document.getElementById("resultNext").addEventListener("click",()=>{{state.resultPage+=1;renderResults();document.getElementById("results").scrollIntoView({{behavior:"smooth",block:"start"}});}});
{market_script}
{bootstrap_script}
    let resizeTimer=null;
    window.addEventListener("resize",()=>{{window.clearTimeout(resizeTimer);resizeTimer=window.setTimeout(()=>{{if(DASHBOARD_DATA){{drawEquityChart();drawDailyChart();}}if(typeof drawMarketChart==="function"&&typeof marketPayload!=="undefined"&&marketPayload)drawMarketChart();}},120);}});
  </script>
</body>
</html>'''


def write_dashboard(root: Path | str, output: Path | str) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_dashboard(build_dashboard_data(root)), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Salt-okunur Kripto Kontrol Paneli üretir.")
    parser.add_argument("--root", default=".", help="JSON dosyalarının bulunduğu repo kökü")
    parser.add_argument("--output", default="dashboard_output/index.html", help="Üretilecek HTML dosyası")
    args = parser.parse_args()
    output = write_dashboard(args.root, args.output)
    print(f"Kripto Kontrol Paneli üretildi: {output}")


if __name__ == "__main__":
    main()
