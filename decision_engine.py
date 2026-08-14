#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kripto Sinyal Sistemi - Karar Motoru v1

Amaç:
- Mevcut JSON ledger/state dosyalarını salt-okunur biçimde analiz eder.
- Premium, Scalp, Pump/Dump, Swing, Range, Momentum, Portföy Risk ve
  Premium TP/BE sonrası takip verilerini tek raporda toplar.
- KORU / İZLE / GÖLGE TEST / CANLI DURDUR / CANLIYA ALMA kararları üretir.
- decision_report.json dosyasına kanıt, örnek sayısı ve güven seviyesi yazar.

GÜVENLİK:
- Telegram mesajı göndermez.
- Sinyal üretmez.
- OKX'e bağlanmaz.
- Emir açmaz.
- Hiçbir strateji/config/state dosyasını değiştirmez.
- Kararları otomatik uygulamaz. auto_apply her zaman False'tur.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


VERSION = "DECISION_ENGINE_V2_2026_08_14"
MODE = "ANALYSIS_ONLY_NO_SIGNAL_CHANGE_NO_TELEGRAM_NO_ORDERS"
DEFAULT_OUTPUT = "decision_report.json"

FILES = {
    "premium_performance": "performance.json",
    "premium_trade_ledger": "trade_ledger.json",
    "scalp_state": "scalp_radar_state.json",
    "scalp_ledger": "scalp_performance_ledger.json",
    "pump_state": "pump_radar_state.json",
    "pump_ledger": "pump_performance_ledger.json",
    "swing_state": "swing_radar_state.json",
    "swing_ledger": "swing_performance_ledger.json",
    "swing_v4_ledger": "swing_shadow_v4_ledger.json",
    "momentum_shadow": "momentum_shadow.json",
    "range_shadow": "range_shadow.json",
    "portfolio_risk_outcomes": "portfolio_risk_outcomes.json",
    "post_result_v2": "post_result_shadow_v2_report.json",
    "post_result_v3": "post_result_shadow_v3_report.json",
}

THRESHOLDS = {
    "confidence": {
        "medium_sample": 10,
        "high_sample": 30,
    },
    "swing": {
        "live_stop_min_sample": 30,
        "live_stop_min_stop_rate": 0.45,
        "live_stop_max_tp3_rate": 0.15,
    },
    "scalp": {
        "split_test_min_sample": 20,
        "split_test_min_stop_rate": 0.33,
        "split_test_max_tp3_rate": 0.20,
    },
    "pump": {
        "clean_min_sample": 10,
        "clean_max_stop_rate": 0.30,
        "clean_min_tp3_rate": 0.25,
    },
    "range": {
        "reject_min_sample": 100,
    },
    "momentum": {
        "min_resolved": 10,
    },
    "portfolio": {
        "min_records_for_rule_review": 50,
        "favorable_margin": 0.10,
    },
    "post_result": {
        "min_completed_for_rule_review": 20,
        "tp1_be_tp2_reach_rate_for_shadow_test": 0.35,
        "tp1_be_tp3_reach_rate_for_shadow_test": 0.20,
        "tp2_be_tp3_reach_rate_for_shadow_test": 0.30,
        "tp3_extra_mfe_r_for_runner_shadow_test": 0.50,
    },
}

FINAL_ALIASES = {
    "STOP": "SL",
    "STOPLOSS": "SL",
    "STOP_LOSS": "SL",
    "BREAKEVEN": "BE",
    "BREAK_EVEN": "BE",
    "TP1_BE": "TP1_SONRASI_BE",
    "TP2_BE": "TP2_SONRASI_BE",
    "TP1_AFTER_BE": "TP1_SONRASI_BE",
    "TP2_AFTER_BE": "TP2_SONRASI_BE",
}

CLOSED_RESULTS = {
    "TP3",
    "TP2_SONRASI_BE",
    "TP1_SONRASI_BE",
    "BE",
    "SL",
    "EXPIRED",
    "TIMEOUT",
}


def now_ts() -> int:
    return int(time.time())


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    number = safe_float(value, None)
    return int(number) if number is not None else default


def pct(numerator: Any, denominator: Any) -> Optional[float]:
    n = safe_float(numerator, None)
    d = safe_float(denominator, None)
    if n is None or d is None or d <= 0:
        return None
    return round(n / d * 100.0, 2)


def ratio(numerator: Any, denominator: Any) -> Optional[float]:
    value = pct(numerator, denominator)
    return None if value is None else value / 100.0


def round_or_none(value: Any, digits: int = 4) -> Optional[float]:
    number = safe_float(value, None)
    return None if number is None else round(number, digits)


def mean(values: Iterable[Any]) -> Optional[float]:
    clean = [safe_float(v, None) for v in values]
    clean = [v for v in clean if v is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 4)


def median(values: Iterable[Any]) -> Optional[float]:
    clean = [safe_float(v, None) for v in values]
    clean = [v for v in clean if v is not None]
    if not clean:
        return None
    return round(float(statistics.median(clean)), 4)


def confidence(sample_size: int) -> str:
    if sample_size >= THRESHOLDS["confidence"]["high_sample"]:
        return "YUKSEK"
    if sample_size >= THRESHOLDS["confidence"]["medium_sample"]:
        return "ORTA"
    return "DUSUK"


def normalize_outcome(value: Any) -> str:
    text = str(value or "").upper().strip()
    text = (
        text.replace("İ", "I")
        .replace("Ş", "S")
        .replace("Ğ", "G")
        .replace("Ü", "U")
        .replace("Ö", "O")
        .replace("Ç", "C")
        .replace("-", "_")
        .replace(" ", "_")
    )
    if text in FINAL_ALIASES:
        return FINAL_ALIASES[text]
    if "TP2" in text and "BE" in text:
        return "TP2_SONRASI_BE"
    if "TP1" in text and "BE" in text:
        return "TP1_SONRASI_BE"
    if text.startswith("TP3"):
        return "TP3"
    if text in {"SL", "STOP", "STOPPED"}:
        return "SL"
    if text in {"BE", "BREAKEVEN"}:
        return "BE"
    if "EXPIRE" in text:
        return "EXPIRED"
    return text


def load_json(path: Path) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    meta = {
        "path": str(path),
        "exists": path.exists(),
        "bytes": 0,
        "valid_json": False,
        "error": None,
    }
    if not path.exists():
        meta["error"] = "FILE_NOT_FOUND"
        return {}, meta
    try:
        meta["bytes"] = path.stat().st_size
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            meta["error"] = "EMPTY_FILE"
            return {}, meta
        data = json.loads(text)
        if not isinstance(data, dict):
            meta["error"] = "ROOT_NOT_OBJECT"
            return {}, meta
        meta["valid_json"] = True
        return data, meta
    except Exception as exc:
        meta["error"] = f"JSON_ERROR: {str(exc)[:200]}"
        return {}, meta


def save_json_atomically(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(temp_path, "r", encoding="utf-8") as verify:
            checked = json.load(verify)
        if not isinstance(checked, dict):
            raise ValueError("Rapor JSON kökü object değil.")

        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def extract_last_update(data: Dict[str, Any]) -> int:
    candidates = [
        data.get("last_update"),
        data.get("updated_at"),
        data.get("last_checked_at"),
    ]
    summary = data.get("summary")
    if isinstance(summary, dict):
        candidates.extend([summary.get("last_update"), summary.get("updated_at")])
    return max([safe_int(v, 0) for v in candidates] + [0])


def enrich_source_meta(meta: Dict[str, Any], data: Dict[str, Any], current_ts: int) -> Dict[str, Any]:
    result = dict(meta)
    last_update = extract_last_update(data)
    result["last_update"] = last_update
    result["age_hours"] = (
        round(max(0, current_ts - last_update) / 3600.0, 2)
        if last_update > 0
        else None
    )
    result["stale_over_48h"] = bool(
        result["age_hours"] is not None and result["age_hours"] > 48
    )
    return result


def iter_records(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    for key in ("records", "trades", "closed_positions"):
        value = data.get(key)
        if isinstance(value, dict):
            return [v for v in value.values() if isinstance(v, dict)]
        if isinstance(value, list):
            return [v for v in value if isinstance(v, dict)]
    return []


def record_outcome(record: Dict[str, Any]) -> str:
    for key in ("final_result", "trade_outcome", "outcome", "result"):
        value = record.get(key)
        if value not in (None, ""):
            return normalize_outcome(value)
    return ""


def record_timestamp(record: Dict[str, Any]) -> int:
    for key in ("closed_at", "trade_closed_at", "finalized_at", "opened_at", "sent_at"):
        value = safe_int(record.get(key), 0)
        if value > 0:
            return value
    return 0


def recent_records(records: Sequence[Dict[str, Any]], current_ts: int, days: int = 14) -> List[Dict[str, Any]]:
    cutoff = current_ts - days * 24 * 3600
    with_time = [r for r in records if record_timestamp(r) > 0]
    if not with_time:
        return list(records)
    recent = [r for r in with_time if record_timestamp(r) >= cutoff]
    return recent if recent else list(with_time)


def outcome_counts(records: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    counter: Counter[str] = Counter()
    for record in records:
        outcome = record_outcome(record)
        if outcome:
            counter[outcome] += 1
    return dict(counter)


def closed_only(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for record in records:
        outcome = record_outcome(record)
        if outcome in CLOSED_RESULTS:
            result.append(record)
            continue
        if safe_int(record.get("closed_at"), 0) > 0 and outcome:
            result.append(record)
    return result


def metrics_from_counts(counts: Dict[str, int], total: Optional[int] = None) -> Dict[str, Any]:
    tp3 = safe_int(counts.get("TP3"), 0)
    be = (
        safe_int(counts.get("BE"), 0)
        + safe_int(counts.get("TP1_SONRASI_BE"), 0)
        + safe_int(counts.get("TP2_SONRASI_BE"), 0)
    )
    sl = safe_int(counts.get("SL"), 0)
    expired = safe_int(counts.get("EXPIRED"), 0) + safe_int(counts.get("TIMEOUT"), 0)
    finalized = total if total is not None else tp3 + be + sl + expired
    measurable = tp3 + be + sl
    return {
        "sample_size": int(finalized or 0),
        "measurable_closed": measurable,
        "tp3": tp3,
        "be": be,
        "sl": sl,
        "expired": expired,
        "tp3_rate_percent": pct(tp3, measurable),
        "be_rate_percent": pct(be, measurable),
        "stop_rate_percent": pct(sl, measurable),
        "positive_close_rate_percent": pct(tp3 + be, measurable),
    }


def exact_r_metrics(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    values = []
    for record in records:
        for key in ("r_result", "net_r", "realized_r"):
            value = safe_float(record.get(key), None)
            if value is not None:
                values.append(value)
                break
    return {
        "r_records": len(values),
        "r_coverage_percent": pct(len(values), len(records)),
        "net_r": round(sum(values), 4) if values else None,
        "avg_r": round(sum(values) / len(values), 4) if values else None,
    }


def decision_entry(
    code: str,
    text: str,
    sample_size: int,
    reasons: Sequence[str],
    next_action: str,
    metrics: Optional[Dict[str, Any]] = None,
    confidence_override: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "decision_code": code,
        "decision_tr": text,
        "confidence": confidence_override or confidence(sample_size),
        "sample_size": int(sample_size),
        "auto_apply": False,
        "reasons": [str(r) for r in reasons if r],
        "next_action": next_action,
        "metrics": metrics or {},
    }


def summarize_premium(
    performance: Dict[str, Any],
    trade_ledger: Dict[str, Any],
    current_ts: int,
) -> Dict[str, Any]:
    records = closed_only(recent_records(iter_records(trade_ledger), current_ts, 14))

    if len(records) >= 10:
        counts = outcome_counts(records)
        metrics = metrics_from_counts(counts, total=len(records))
        metrics.update(exact_r_metrics(records))
        metrics["data_basis"] = "trade_ledger_recent_14d"
        sources = Counter(str(r.get("source") or "UNKNOWN") for r in records)
        directions = Counter(str(r.get("direction") or "UNKNOWN").upper() for r in records)
        metrics["source_breakdown"] = dict(sources)
        metrics["direction_breakdown"] = dict(directions)
    else:
        days = performance.get("days")
        day_items: List[Tuple[str, Dict[str, Any]]] = []
        if isinstance(days, dict):
            for day, item in sorted(days.items())[-7:]:
                if isinstance(item, dict):
                    day_items.append((day, item))

        opened = sum(safe_int(v.get("opened"), 0) for _, v in day_items)
        tp1 = sum(safe_int(v.get("tp1"), 0) for _, v in day_items)
        tp2 = sum(safe_int(v.get("tp2"), 0) for _, v in day_items)
        tp3 = sum(safe_int(v.get("tp3"), 0) for _, v in day_items)
        sl = sum(safe_int(v.get("sl"), 0) for _, v in day_items)
        be = sum(safe_int(v.get("be"), 0) for _, v in day_items)
        expired = sum(safe_int(v.get("expired"), 0) for _, v in day_items)
        measurable = tp3 + be + sl
        metrics = {
            "data_basis": "performance_recent_7_days_fallback",
            "days": [day for day, _ in day_items],
            "opened_events": opened,
            "tp1_events": tp1,
            "tp2_events": tp2,
            "tp3": tp3,
            "be": be,
            "sl": sl,
            "expired": expired,
            "sample_size": measurable,
            "measurable_closed": measurable,
            "tp1_per_opened_percent": pct(tp1, opened),
            "tp2_per_opened_percent": pct(tp2, opened),
            "tp3_rate_percent": pct(tp3, measurable),
            "be_rate_percent": pct(be, measurable),
            "stop_rate_percent": pct(sl, measurable),
            "positive_close_rate_percent": pct(tp3 + be, measurable),
            # Bu yalnız geçmiş ana bot raporlama standardıyla kıyas için kullanılır.
            # TP2 sonrası BE ayrımı yoksa gerçek R değildir.
            "legacy_estimated_net_r": round(tp3 * 1.075 + be * 0.275 - sl, 4),
        }

    n = safe_int(metrics.get("measurable_closed"), 0)
    stop_rate = (safe_float(metrics.get("stop_rate_percent"), 0.0) or 0.0) / 100.0
    be_rate = (safe_float(metrics.get("be_rate_percent"), 0.0) or 0.0) / 100.0
    tp3_rate = (safe_float(metrics.get("tp3_rate_percent"), 0.0) or 0.0) / 100.0
    avg_r = safe_float(metrics.get("avg_r"), None)

    if n < 10:
        return decision_entry(
            "IZLE",
            "🟡 İZLE",
            n,
            ["Premium için ölçülebilir güncel kapanış örneği henüz düşük."],
            "Canlı kural değiştirme; veri toplamaya devam et.",
            metrics,
        )

    if avg_r is not None and avg_r < -0.05 and n >= 30:
        code = "GOLGE_TEST_DEGISIKLIK"
        text = "🟠 DEĞİŞİKLİĞİ GÖLGE TESTE AL"
        reasons = [f"Güncel exact-R ortalaması negatif: {avg_r:.3f}R."]
        action = "Giriş/çıkış değişikliğini doğrudan canlıya alma; kaybeden segmenti ayrı gölge test et."
    elif stop_rate >= 0.45 and n >= 30:
        code = "GOLGE_TEST_DEGISIKLIK"
        text = "🟠 DEĞİŞİKLİĞİ GÖLGE TESTE AL"
        reasons = [f"Stop oranı yüksek: %{stop_rate*100:.1f}."]
        action = "Kaybeden setup/ADX/hacim segmentini ayırıp gölge filtre test et."
    else:
        code = "KORU"
        text = "🟢 KORU"
        reasons = [
            f"Ölçülebilir kapanış: {n}.",
            f"Stop oranı: %{stop_rate*100:.1f}; pozitif kapanış oranı: %{(1-stop_rate)*100:.1f}.",
        ]
        if avg_r is not None:
            reasons.append(f"Exact-R ortalaması: {avg_r:+.3f}R.")
        action = "Ana giriş mantığını değiştirme."

    if be_rate >= 0.45 and tp3_rate <= 0.20:
        reasons.append(
            f"BE oranı %{be_rate*100:.1f}, TP3 oranı %{tp3_rate*100:.1f}; odak girişten çok işlem yönetimi olmalı."
        )
        action += " TP/BE sonrası gölge takip verisini karar için kullan."

    return decision_entry(code, text, n, reasons, action, metrics)


def summary_to_counts(summary: Dict[str, Any]) -> Dict[str, int]:
    return {
        "TP3": safe_int(summary.get("tp3"), 0),
        "BE": safe_int(summary.get("breakeven"), 0),
        "SL": safe_int(summary.get("stop"), 0),
        "EXPIRED": safe_int(summary.get("expired"), 0),
    }


def summarize_setup_breakdown(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        source = str(record.get("source") or "").strip()
        setup = str(record.get("setup") or "").strip()
        key = source or setup or "UNKNOWN"
        groups[key].append(record)

    output: Dict[str, Any] = {}
    for key, items in sorted(groups.items(), key=lambda pair: (-len(pair[1]), pair[0])):
        counts = outcome_counts(items)
        metrics = metrics_from_counts(counts, total=len(items))
        if len(items) >= 3:
            output[key] = metrics
    return output


def summarize_scalp(state: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    records = iter_records(ledger)
    real = [
        r for r in records
        if str(r.get("stage") or "").upper() in {"REAL_SIGNAL", "REAL", "TRADE"}
    ]
    real_closed = closed_only(real)

    if real_closed:
        counts = outcome_counts(real_closed)
        metrics = metrics_from_counts(counts, total=len(real_closed))
        metrics["data_basis"] = "scalp_ledger_real_signal"
        metrics["setup_breakdown"] = summarize_setup_breakdown(real_closed)
        n = len(real_closed)
    else:
        summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
        if safe_int(summary.get("real_signal"), 0) > 0:
            n = safe_int(summary.get("real_signal"), 0)
            counts = summary_to_counts(summary)
            metrics = metrics_from_counts(counts, total=n)
            metrics["data_basis"] = "scalp_ledger_summary"
            metrics["rolling_total_records"] = safe_int(summary.get("total"), 0)
            metrics["prewatch_records"] = safe_int(summary.get("prewatch"), 0)
            metrics["early_records"] = safe_int(summary.get("early"), 0)
        else:
            stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
            n = safe_int(stats.get("signals"), 0)
            counts = {
                "TP3": safe_int(stats.get("tp3"), 0),
                "BE": safe_int(stats.get("breakeven"), 0),
                "SL": safe_int(stats.get("stop"), 0),
                "EXPIRED": safe_int(stats.get("expired"), 0),
            }
            metrics = metrics_from_counts(counts, total=n)
            metrics["data_basis"] = "scalp_state_stats_fallback"

    stop_rate = (safe_float(metrics.get("stop_rate_percent"), 0.0) or 0.0) / 100.0
    tp3_rate = (safe_float(metrics.get("tp3_rate_percent"), 0.0) or 0.0) / 100.0

    if (
        n >= THRESHOLDS["scalp"]["split_test_min_sample"]
        and stop_rate >= THRESHOLDS["scalp"]["split_test_min_stop_rate"]
        and tp3_rate <= THRESHOLDS["scalp"]["split_test_max_tp3_rate"]
    ):
        return decision_entry(
            "SETUPLARI_AYIR_GOLGE_TEST",
            "🟠 SETUPLARI AYIR / GÖLGE TEST",
            n,
            [
                f"Gerçek Scalp örneği: {n}.",
                f"Stop oranı %{stop_rate*100:.1f}, TP3 oranı %{tp3_rate*100:.1f}.",
            ],
            "ATAK_SCALP / TEPKİ_SCALP ve diğer source/setup gruplarını ayrı performansla karşılaştır; zayıf grubu önce gölgede filtrele.",
            metrics,
        )

    if n >= 10 and stop_rate <= 0.25 and tp3_rate >= 0.25:
        return decision_entry(
            "KORU",
            "🟢 KORU",
            n,
            [f"Stop oranı %{stop_rate*100:.1f}, TP3 oranı %{tp3_rate*100:.1f}."],
            "Canlı Scalp mantığını değiştirme; setup kırılımını izlemeye devam et.",
            metrics,
        )

    return decision_entry(
        "IZLE",
        "🟡 İZLE",
        n,
        [f"Gerçek Scalp örneği: {n}."],
        "Canlı filtreyi değiştirmeden setup bazlı ayrıştırmayı sürdür.",
        metrics,
    )


def summarize_pump(state: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    records = closed_only(iter_records(ledger))
    if records:
        n = len(records)
        metrics = metrics_from_counts(outcome_counts(records), total=n)
        metrics["data_basis"] = "pump_ledger"
    else:
        summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
        if safe_int(summary.get("total"), 0) > 0:
            n = safe_int(summary.get("total"), 0)
            metrics = metrics_from_counts(summary_to_counts(summary), total=n)
            metrics["data_basis"] = "pump_ledger_summary"
            for key in (
                "direction_correct", "direction_wrong", "direction_mixed",
                "diagnosis_success", "diagnosis_momentum_faded",
                "diagnosis_no_continuation",
            ):
                if key in summary:
                    metrics[key] = safe_int(summary.get(key), 0)
        else:
            stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
            n = safe_int(stats.get("signals"), 0)
            metrics = metrics_from_counts({
                "TP3": safe_int(stats.get("tp3"), 0),
                "BE": safe_int(stats.get("breakeven"), 0),
                "SL": safe_int(stats.get("stop"), 0),
                "EXPIRED": safe_int(stats.get("expired"), 0),
            }, total=n)
            metrics["data_basis"] = "pump_state_stats_fallback"

    stop_rate = (safe_float(metrics.get("stop_rate_percent"), 0.0) or 0.0) / 100.0
    tp3_rate = (safe_float(metrics.get("tp3_rate_percent"), 0.0) or 0.0) / 100.0

    if (
        n >= THRESHOLDS["pump"]["clean_min_sample"]
        and stop_rate <= THRESHOLDS["pump"]["clean_max_stop_rate"]
        and tp3_rate >= THRESHOLDS["pump"]["clean_min_tp3_rate"]
    ):
        return decision_entry(
            "KORU_IZLE",
            "🟢 KORU / İZLE",
            n,
            [
                f"Pump/Dump örneği {n}.",
                f"Stop oranı %{stop_rate*100:.1f}, TP3 oranı %{tp3_rate*100:.1f}.",
            ],
            "Ayarları değiştirme; örnek sayısını büyüt.",
            metrics,
            confidence_override="ORTA" if n < 30 else "YUKSEK",
        )

    return decision_entry(
        "IZLE",
        "🟡 İZLE",
        n,
        [f"Pump/Dump örneği {n}; sert karar için henüz sınırlı."],
        "Canlı kural değiştirme; sonuç toplamaya devam et.",
        metrics,
    )


def summarize_swing(state: Dict[str, Any], ledger: Dict[str, Any]) -> Dict[str, Any]:
    records = closed_only(iter_records(ledger))
    summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}

    if records:
        n = len(records)
        metrics = metrics_from_counts(outcome_counts(records), total=n)
        metrics["data_basis"] = "swing_ledger_records"
    elif safe_int(summary.get("total"), 0) > 0:
        n = safe_int(summary.get("total"), 0)
        metrics = metrics_from_counts(summary_to_counts(summary), total=n)
        metrics["data_basis"] = "swing_ledger_summary"
    else:
        stats = state.get("stats") if isinstance(state.get("stats"), dict) else {}
        n = safe_int(stats.get("signals"), 0)
        metrics = metrics_from_counts({
            "TP3": safe_int(stats.get("tp3"), 0),
            "BE": safe_int(stats.get("breakeven"), 0),
            "SL": safe_int(stats.get("stop"), 0),
            "EXPIRED": safe_int(stats.get("expired"), 0),
        }, total=n)
        metrics["data_basis"] = "swing_state_stats_fallback"

    for key in (
        "direction_correct", "direction_wrong", "direction_mixed",
        "early_15m", "confirmed_1h", "long", "short",
        "diagnosis_success", "diagnosis_delayed_direction",
        "diagnosis_weak_trend", "diagnosis_setup_failed",
    ):
        if key in summary:
            metrics[key] = safe_int(summary.get(key), 0)

    stop_rate = (safe_float(metrics.get("stop_rate_percent"), 0.0) or 0.0) / 100.0
    tp3_rate = (safe_float(metrics.get("tp3_rate_percent"), 0.0) or 0.0) / 100.0
    direction_wrong = safe_int(metrics.get("direction_wrong"), 0)
    direction_correct = safe_int(metrics.get("direction_correct"), 0)
    short_count = safe_int(metrics.get("short"), 0)
    long_count = safe_int(metrics.get("long"), 0)

    if (
        n >= THRESHOLDS["swing"]["live_stop_min_sample"]
        and stop_rate >= THRESHOLDS["swing"]["live_stop_min_stop_rate"]
        and tp3_rate <= THRESHOLDS["swing"]["live_stop_max_tp3_rate"]
    ):
        reasons = [
            f"Swing örneği {n}; stop oranı %{stop_rate*100:.1f}.",
            f"TP3 oranı yalnız %{tp3_rate*100:.1f}.",
        ]
        if direction_wrong or direction_correct:
            reasons.append(f"Yön doğru/yanlış: {direction_correct}/{direction_wrong}.")
        if short_count and long_count == 0:
            reasons.append(f"Yön dağılımı tek taraflı: {short_count} SHORT / 0 LONG.")
        return decision_entry(
            "CANLI_DURDUR",
            "🔴 CANLI İŞLEM KAYNAĞINI DURDUR",
            n,
            reasons,
            "Swing veri toplamaya devam etsin fakat gerçek para kaynağı olmasın; 1D/4H rejim ve 1H giriş zamanlaması yeniden tasarlansın.",
            metrics,
            confidence_override="YUKSEK",
        )

    return decision_entry(
        "IZLE",
        "🟡 İZLE",
        n,
        [f"Swing örneği {n}; mevcut eşikler CANLI_DURDUR koşulunu tamamlamadı."],
        "Canlı değişiklik yapmadan izlemeyi sürdür.",
        metrics,
    )


def summarize_swing_v4(ledger: Dict[str, Any]) -> Dict[str, Any]:
    summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
    closed = safe_int(summary.get("closed"), 0)
    metrics = dict(summary)
    live_candidate = bool(summary.get("live_candidate"))

    if live_candidate:
        return decision_entry(
            "CANLI_ADAYI_GOLGE_DOGRULANDI",
            "🟠 SWING V4 CANLI ADAYI / SON İNCELEME",
            closed,
            [
                f"Stop %{safe_float(summary.get('stop_rate_percent')):.1f}, "
                f"TP3 %{safe_float(summary.get('tp3_rate_percent')):.1f}.",
                f"Pozitif kapanış %{safe_float(summary.get('positive_close_rate_percent')):.1f}, "
                f"maksimum yön payı %{safe_float(summary.get('max_direction_share_percent')):.1f}.",
            ],
            "Otomatik canlıya alma; maliyet ve ileri dönem doğrulamasını incele.",
            metrics,
            confidence_override="YUKSEK" if closed >= 50 else "ORTA",
        )

    if closed < 30:
        return decision_entry(
            "VERI_TOPLA",
            "⚪ SWING V4 GÖLGE VERİ TOPLA",
            closed,
            [f"Kapanmış Swing V4 sanal işlemi: {closed}/30."],
            "Telegram ve emir kapalı kalsın; 30 kapanışa kadar veri topla.",
            metrics,
        )

    failed = [
        name for name, passed in (summary.get("gates") or {}).items()
        if name != "minimum_sample" and not passed
    ]
    return decision_entry(
        "GOLGEDE_YENIDEN_AYARLA",
        "🟠 SWING V4 GÖLGEDE AYARLA",
        closed,
        [f"Başarısız doğrulama kapıları: {', '.join(failed) or 'bilinmiyor'}."],
        "Canlıya alma; yalnız başarısız kapıları küçük gölge değişikliklerle düzelt.",
        metrics,
    )


def summarize_range(data: Dict[str, Any]) -> Dict[str, Any]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    n = safe_int(summary.get("total_closed"), 0)
    net_r = safe_float(summary.get("net_r"), None)
    gross_r = safe_float(summary.get("gross_r"), None)
    avg_cost = safe_float(summary.get("average_cost_r_per_closed"), None)
    metrics = {
        "total_opened": safe_int(summary.get("total_opened"), 0),
        "total_closed": n,
        "wins": safe_int(summary.get("completed_cycle_legs"), 0),
        "sl": safe_int(summary.get("sl_count"), 0),
        "win_rate_percent": round_or_none(summary.get("win_rate_percent"), 2),
        "gross_r": round_or_none(gross_r),
        "net_r": round_or_none(net_r),
        "gross_to_net_cost_r": round_or_none(summary.get("gross_to_net_cost_r")),
        "average_cost_r_per_closed": round_or_none(avg_cost),
        "close_reason_counts": summary.get("close_reason_counts") if isinstance(summary.get("close_reason_counts"), dict) else {},
        "risk_band_stats": summary.get("risk_band_stats") if isinstance(summary.get("risk_band_stats"), dict) else {},
        "direction_stats": summary.get("direction_stats") if isinstance(summary.get("direction_stats"), dict) else {},
    }

    if n >= THRESHOLDS["range"]["reject_min_sample"] and net_r is not None and net_r < 0:
        reasons = [
            f"{n} kapanmış gölge işlemde net sonuç {net_r:+.2f}R.",
        ]
        if avg_cost is not None:
            reasons.append(f"Ortalama maliyet etkisi işlem başına {avg_cost:.3f}R.")
        return decision_entry(
            "CANLIYA_ALMA_YENIDEN_TASARLA",
            "🔴 CANLIYA ALMA / YENİDEN TASARLA",
            n,
            reasons,
            "Mevcut Range V3'ü canlı aday olarak bırak; yeni mimari ancak ayrı gölge sürüm olarak denensin.",
            metrics,
            confidence_override="YUKSEK",
        )

    return decision_entry(
        "GOLGEDE_TUT",
        "🟡 GÖLGEDE TUT",
        n,
        [f"Range kapanmış örnek: {n}."],
        "Canlıya alma; gölge değerlendirmeyi sürdür.",
        metrics,
    )


def summarize_momentum(data: Dict[str, Any]) -> Dict[str, Any]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    n = safe_int(summary.get("resolved_records"), 0)
    blocked_winners = safe_int(summary.get("blocked_winners"), 0)
    blocked_losers = safe_int(summary.get("blocked_losers"), 0)
    passed_winners = safe_int(summary.get("passed_winners"), 0)
    passed_losers = safe_int(summary.get("passed_losers"), 0)
    metrics = {
        "total_records": safe_int(summary.get("total_records"), 0),
        "resolved_records": n,
        "pass_records": safe_int(summary.get("pass_records"), 0),
        "caution_records": safe_int(summary.get("caution_records"), 0),
        "would_block_records": safe_int(summary.get("would_block_records"), 0),
        "blocked_winners": blocked_winners,
        "blocked_losers": blocked_losers,
        "passed_winners": passed_winners,
        "passed_losers": passed_losers,
    }

    if n >= THRESHOLDS["momentum"]["min_resolved"] and blocked_winners > blocked_losers:
        return decision_entry(
            "GOLGEDE_TUT_CANLIYA_ALMA",
            "🟠 GÖLGEDE TUT / CANLIYA ALMA",
            n,
            [
                f"Momentum filtresi engellemek istediği işlemlerde {blocked_winners} kazanan / {blocked_losers} kaybeden gördü.",
                "Filtre şu aşamada kazanan işlemleri de kesiyor.",
            ],
            "Filtreyi canlı sinyal engeline dönüştürme; gölge veri toplamaya devam et.",
            metrics,
        )

    return decision_entry(
        "IZLE",
        "🟡 İZLE",
        n,
        [f"Momentum çözülmüş örnek: {n}."],
        "Canlıya almadan gölgede izlemeye devam et.",
        metrics,
    )


def event_pair(stats: Dict[str, Any], threshold_key: str = "0_5") -> Tuple[int, int, int, int]:
    fav = safe_int(stats.get(f"first_{threshold_key}_favorable"), 0)
    adv = safe_int(stats.get(f"first_{threshold_key}_adverse"), 0)
    amb = safe_int(stats.get(f"first_{threshold_key}_ambiguous"), 0)
    none = safe_int(stats.get(f"first_{threshold_key}_none"), 0)
    return fav, adv, amb, none


def portfolio_group_metric(stats: Dict[str, Any]) -> Dict[str, Any]:
    records = safe_int(stats.get("records"), 0)
    fav, adv, amb, none = event_pair(stats, "0_5")
    decisive = fav + adv
    return {
        "records": records,
        "first_0_5_favorable": fav,
        "first_0_5_adverse": adv,
        "first_0_5_ambiguous": amb,
        "first_0_5_none": none,
        "favorable_first_rate_percent": pct(fav, decisive),
        "adverse_first_rate_percent": pct(adv, decisive),
        "avg_return_60m_percent": round_or_none(stats.get("avg_return_60m_percent")),
        "avg_return_240m_percent": round_or_none(stats.get("avg_return_240m_percent")),
        "avg_return_720m_percent": round_or_none(stats.get("avg_return_720m_percent")),
        "avg_return_1440m_percent": round_or_none(stats.get("avg_return_1440m_percent")),
        "avg_mfe_240m_percent": round_or_none(stats.get("avg_mfe_240m_percent")),
        "avg_mae_240m_percent": round_or_none(stats.get("avg_mae_240m_percent")),
    }


def summarize_portfolio(data: Dict[str, Any]) -> Dict[str, Any]:
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    tracked = safe_int(summary.get("tracked_records"), 0)
    completed = safe_int(summary.get("completed_records"), 0)
    by_decision = summary.get("by_decision") if isinstance(summary.get("by_decision"), dict) else {}
    by_code = summary.get("by_block_code") if isinstance(summary.get("by_block_code"), dict) else {}

    block_stats = by_decision.get("BLOCK") if isinstance(by_decision.get("BLOCK"), dict) else {}
    allow_stats = by_decision.get("ALLOW") if isinstance(by_decision.get("ALLOW"), dict) else {}
    block_metric = portfolio_group_metric(block_stats)
    allow_metric = portfolio_group_metric(allow_stats)

    code_findings: Dict[str, Any] = {}
    for code, stats in by_code.items():
        if not isinstance(stats, dict):
            continue
        metric = portfolio_group_metric(stats)
        fav_rate = safe_float(metric.get("favorable_first_rate_percent"), None)
        adv_rate = safe_float(metric.get("adverse_first_rate_percent"), None)
        finding = "IZLE"
        if metric["records"] >= 20 and fav_rate is not None and adv_rate is not None:
            if fav_rate >= adv_rate + 10:
                finding = "GEREKSIZ_ENGEL_ADAYI"
            elif adv_rate >= fav_rate + 10:
                finding = "ENGEL_FAYDALI_ADAYI"
        metric["finding"] = finding
        code_findings[str(code)] = metric

    metrics = {
        "sample_unit": "portfolio_decision_records_not_unique_trades",
        "source_records": safe_int(summary.get("source_records"), tracked),
        "tracked_records": tracked,
        "completed_records": completed,
        "tracking_records": safe_int(summary.get("tracking_records"), 0),
        "data_error_records": safe_int(summary.get("data_error_records"), 0),
        "block": block_metric,
        "allow": allow_metric,
        "by_block_code": code_findings,
    }

    block_records = safe_int(block_metric.get("records"), 0)
    fav_rate = safe_float(block_metric.get("favorable_first_rate_percent"), None)
    adv_rate = safe_float(block_metric.get("adverse_first_rate_percent"), None)

    if (
        block_records >= THRESHOLDS["portfolio"]["min_records_for_rule_review"]
        and fav_rate is not None
        and adv_rate is not None
    ):
        margin_pct = THRESHOLDS["portfolio"]["favorable_margin"] * 100.0
        if fav_rate >= adv_rate + margin_pct:
            return decision_entry(
                "LIMIT_GEVSETME_GOLGE_TEST",
                "🟠 LİMİT GEVŞETMEYİ GÖLGE TEST ET",
                tracked,
                [
                    f"BLOCK kararlarında ilk %0.5 olumlu hareket oranı %{fav_rate:.1f}, olumsuz %{adv_rate:.1f}.",
                    "Engellenen adayların anlamlı kısmı sonradan doğru yönde hareket ediyor olabilir.",
                ],
                "Canlı portföy limitini hemen değiştirme; block_code bazında daha gevşek limitleri paralel gölge test et.",
                metrics,
                confidence_override="YUKSEK" if completed >= 100 else "ORTA",
            )
        if adv_rate >= fav_rate + margin_pct:
            return decision_entry(
                "KORU",
                "🟢 PORTFÖY ENGELİNİ KORU",
                tracked,
                [
                    f"BLOCK kararlarında ilk %0.5 olumsuz hareket oranı %{adv_rate:.1f}, olumlu %{fav_rate:.1f}.",
                ],
                "Mevcut portföy engelini koru; block_code bazında izlemeyi sürdür.",
                metrics,
                confidence_override="YUKSEK" if completed >= 100 else "ORTA",
            )

    return decision_entry(
        "IZLE",
        "🟡 PORTFÖY RİSKİNİ İZLE",
        tracked,
        [
            f"Takip edilen portföy karar kaydı: {tracked}; tamamlanan: {completed}.",
            "Bu kayıtların bir kısmı aynı adayın farklı zamanlardaki kararlarıdır; benzersiz trade sayısı değildir.",
        ],
        "Canlı limit değiştirmeden BLOCK/ALLOW ve block_code sonuçlarını toplamaya devam et.",
        metrics,
    )


def summarize_post_result(
    trade_ledger: Dict[str, Any],
    v2_report: Optional[Dict[str, Any]] = None,
    v3_report: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    records = iter_records(trade_ledger)
    tracked: List[Dict[str, Any]] = []
    for trade in records:
        follow = trade.get("post_result_shadow")
        if isinstance(follow, dict):
            tracked.append(trade)

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for trade in tracked:
        final = normalize_outcome(
            (trade.get("post_result_shadow") or {}).get("final_result")
            or trade.get("final_result")
        )
        groups[final].append(trade)

    group_metrics: Dict[str, Any] = {}
    completed_total = 0

    for final, items in groups.items():
        completed = [
            trade for trade in items
            if str((trade.get("post_result_shadow") or {}).get("status") or "").upper() == "COMPLETED"
        ]
        completed_total += len(completed)

        tp1_reached = tp2_reached = tp3_reached = 0
        mfe_r = []
        mae_r = []
        directional_240_r = []

        for trade in completed:
            follow = trade.get("post_result_shadow") or {}
            reached = follow.get("reached_levels") if isinstance(follow.get("reached_levels"), dict) else {}
            tp1_reached += int("TP1" in reached)
            tp2_reached += int("TP2" in reached)
            tp3_reached += int("TP3" in reached)
            mfe_r.append(follow.get("max_favorable_r"))
            mae_r.append(follow.get("max_adverse_r"))
            checkpoints = follow.get("checkpoints") if isinstance(follow.get("checkpoints"), dict) else {}
            cp240 = checkpoints.get("240") if isinstance(checkpoints.get("240"), dict) else {}
            if cp240:
                directional_240_r.append(cp240.get("directional_r_from_reference"))

        group_metrics[final] = {
            "tracked": len(items),
            "completed": len(completed),
            "tp1_reached_after_close": tp1_reached,
            "tp2_reached_after_close": tp2_reached,
            "tp3_reached_after_close": tp3_reached,
            "tp1_reach_rate_percent": pct(tp1_reached, len(completed)),
            "tp2_reach_rate_percent": pct(tp2_reached, len(completed)),
            "tp3_reach_rate_percent": pct(tp3_reached, len(completed)),
            "avg_max_favorable_r": mean(mfe_r),
            "median_max_favorable_r": median(mfe_r),
            "avg_max_adverse_r": mean(mae_r),
            "median_240m_directional_r": median(directional_240_r),
        }

    metrics = {
        "tracked_total": len(tracked),
        "completed_total": completed_total,
        "groups": group_metrics,
        "v2_report": v2_report if isinstance(v2_report, dict) else {},
        "v3_report": v3_report if isinstance(v3_report, dict) else {},
    }

    if completed_total < THRESHOLDS["post_result"]["min_completed_for_rule_review"]:
        return decision_entry(
            "IZLE",
            "🟡 TP/BE SONRASI TAKİBİ SÜRDÜR",
            completed_total,
            [f"Tamamlanmış post-result örneği {completed_total}; kural değişikliği için henüz düşük."],
            "BE/TP kurallarını değiştirme; 15/30/60/120/240 dakika verisini biriktir.",
            metrics,
        )

    reasons: List[str] = []
    shadow_test = False

    v3_models = (v3_report or {}).get("models") if isinstance(v3_report, dict) else {}
    if isinstance(v3_models, dict):
        for model_name, model in sorted(v3_models.items()):
            if not isinstance(model, dict):
                continue
            model_sample = safe_int(model.get("sample"), 0)
            model_avg = safe_float(model.get("average_incremental_r"), None)
            if model_sample >= THRESHOLDS["post_result"]["min_completed_for_rule_review"] and model_avg is not None:
                reasons.append(
                    f"V3 {model_name}: {model_sample} örnek, ortalama ek sonuç {model_avg:+.3f}R."
                )
                shadow_test = True

    tp1 = group_metrics.get("TP1_SONRASI_BE", {})
    tp1_n = safe_int(tp1.get("completed"), 0)
    tp1_tp2 = safe_float(tp1.get("tp2_reach_rate_percent"), None)
    tp1_tp3 = safe_float(tp1.get("tp3_reach_rate_percent"), None)
    if tp1_n >= 10:
        reasons.append(
            f"TP1→BE tamamlanmış {tp1_n}; sonradan TP2 %{tp1_tp2 or 0:.1f}, TP3 %{tp1_tp3 or 0:.1f}."
        )
        if (
            (tp1_tp2 is not None and tp1_tp2 >= THRESHOLDS["post_result"]["tp1_be_tp2_reach_rate_for_shadow_test"] * 100)
            or (tp1_tp3 is not None and tp1_tp3 >= THRESHOLDS["post_result"]["tp1_be_tp3_reach_rate_for_shadow_test"] * 100)
        ):
            shadow_test = True

    tp2 = group_metrics.get("TP2_SONRASI_BE", {})
    tp2_n = safe_int(tp2.get("completed"), 0)
    tp2_tp3 = safe_float(tp2.get("tp3_reach_rate_percent"), None)
    if tp2_n >= 10:
        reasons.append(
            f"TP2→BE tamamlanmış {tp2_n}; sonradan TP3 %{tp2_tp3 or 0:.1f}."
        )
        if (
            tp2_tp3 is not None
            and tp2_tp3 >= THRESHOLDS["post_result"]["tp2_be_tp3_reach_rate_for_shadow_test"] * 100
        ):
            shadow_test = True

    tp3 = group_metrics.get("TP3", {})
    tp3_n = safe_int(tp3.get("completed"), 0)
    tp3_mfe = safe_float(tp3.get("avg_max_favorable_r"), None)
    if tp3_n >= 10 and tp3_mfe is not None:
        reasons.append(f"TP3 sonrası ortalama ek MFE: {tp3_mfe:.3f}R.")
        if tp3_mfe >= THRESHOLDS["post_result"]["tp3_extra_mfe_r_for_runner_shadow_test"]:
            shadow_test = True

    if shadow_test:
        return decision_entry(
            "YONETIM_ALTERNATIFI_GOLGE_TEST",
            "🟠 İŞLEM YÖNETİMİ ALTERNATİFİNİ GÖLGE TEST ET",
            completed_total,
            reasons or ["Post-result verisi alternatif yönetim testini destekliyor."],
            "V3 modellerini karşılaştır; yalnız yeterli örnek ve pozitif net ek R doğrulanırsa canlı kural önerisi üret.",
            metrics,
        )

    return decision_entry(
        "MEVCUT_BE_TP_KURALINI_KORU",
        "🟢 MEVCUT BE/TP KURALINI KORU",
        completed_total,
        reasons or ["Post-result örnekleri mevcut kuralı değiştirecek kadar güçlü devam sinyali göstermiyor."],
        "Canlı BE/TP kuralını koru; gölge takibi sürdür.",
        metrics,
    )


PRIORITY = {
    "CANLI_DURDUR": 1,
    "CANLIYA_ALMA_YENIDEN_TASARLA": 2,
    "GOLGEDE_TUT_CANLIYA_ALMA": 3,
    "GOLGE_TEST_DEGISIKLIK": 4,
    "SETUPLARI_AYIR_GOLGE_TEST": 4,
    "LIMIT_GEVSETME_GOLGE_TEST": 4,
    "YONETIM_ALTERNATIFI_GOLGE_TEST": 4,
    "CANLI_ADAYI_GOLGE_DOGRULANDI": 4,
    "GOLGEDE_YENIDEN_AYARLA": 5,
    "VERI_TOPLA": 9,
    "GOLGEDE_TUT": 5,
    "IZLE": 6,
    "KORU_IZLE": 7,
    "KORU": 8,
    "MEVCUT_BE_TP_KURALINI_KORU": 8,
}


def executive_summary(components: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    actions = []
    for name, result in components.items():
        code = str(result.get("decision_code") or "IZLE")
        actions.append({
            "priority": PRIORITY.get(code, 9),
            "component": name,
            "decision_code": code,
            "decision_tr": result.get("decision_tr"),
            "confidence": result.get("confidence"),
            "sample_size": result.get("sample_size"),
            "next_action": result.get("next_action"),
        })

    actions.sort(key=lambda item: (item["priority"], -safe_int(item.get("sample_size"), 0), item["component"]))
    urgent = [a for a in actions if a["priority"] <= 4]
    return {
        "overall": "ACTION_REQUIRED" if urgent else "STABLE_OBSERVATION",
        "top_actions": actions[:8],
        "urgent_action_count": len(urgent),
        "auto_apply": False,
    }


def build_report(base_dir: str = ".", current_ts: Optional[int] = None) -> Dict[str, Any]:
    current_ts = int(current_ts or now_ts())
    root = Path(base_dir)

    loaded: Dict[str, Dict[str, Any]] = {}
    source_meta: Dict[str, Dict[str, Any]] = {}
    for logical_name, filename in FILES.items():
        data, meta = load_json(root / filename)
        loaded[logical_name] = data
        source_meta[logical_name] = enrich_source_meta(meta, data, current_ts)

    components = {
        "PREMIUM": summarize_premium(
            loaded["premium_performance"],
            loaded["premium_trade_ledger"],
            current_ts,
        ),
        "SCALP": summarize_scalp(
            loaded["scalp_state"],
            loaded["scalp_ledger"],
        ),
        "PUMP_DUMP": summarize_pump(
            loaded["pump_state"],
            loaded["pump_ledger"],
        ),
        "SWING": summarize_swing(
            loaded["swing_state"],
            loaded["swing_ledger"],
        ),
        "SWING_V4_SHADOW": summarize_swing_v4(
            loaded["swing_v4_ledger"],
        ),
        "RANGE_SHADOW": summarize_range(
            loaded["range_shadow"],
        ),
        "MOMENTUM_SHADOW": summarize_momentum(
            loaded["momentum_shadow"],
        ),
        "PORTFOLIO_RISK": summarize_portfolio(
            loaded["portfolio_risk_outcomes"],
        ),
        "POST_RESULT_SHADOW": summarize_post_result(
            loaded["premium_trade_ledger"],
            loaded["post_result_v2"],
            loaded["post_result_v3"],
        ),
    }

    data_warnings = []
    for name, meta in source_meta.items():
        if not meta.get("exists"):
            data_warnings.append(f"{name}: dosya yok")
        elif not meta.get("valid_json"):
            data_warnings.append(f"{name}: {meta.get('error')}")
        elif meta.get("stale_over_48h"):
            data_warnings.append(f"{name}: veri 48 saatten eski")

    report = {
        "version": VERSION,
        "mode": MODE,
        "generated_at": current_ts,
        "generated_at_utc": datetime.fromtimestamp(current_ts, tz=timezone.utc).isoformat(),
        "auto_apply": False,
        "thresholds": THRESHOLDS,
        "data_quality": {
            "warnings": data_warnings,
            "source_count": len(source_meta),
            "valid_source_count": sum(1 for m in source_meta.values() if m.get("valid_json")),
            "sources": source_meta,
        },
        "executive": executive_summary(components),
        "components": components,
        "notes": [
            "Bu rapor tavsiye/analiz katmanıdır; hiçbir canlı strateji kuralını otomatik değiştirmez.",
            "Portföy risk kayıtları benzersiz trade değil, karar gözlemleridir.",
            "Exact R mevcutsa kullanılır; yoksa kararlar ağırlıklı olarak sonuç oranlarına dayanır.",
            "Tekil işlem sonucu nedeniyle canlı filtre değişikliği önerilmez.",
        ],
    }
    return report


def print_report(report: Dict[str, Any]) -> None:
    print("=" * 72)
    print("KARAR MOTORU")
    print(report.get("version"))
    print("=" * 72)
    for item in report.get("executive", {}).get("top_actions", []):
        print(
            f"{item.get('component', ''):20} "
            f"{item.get('decision_tr', ''):36} "
            f"n={item.get('sample_size', 0):>4} "
            f"guven={item.get('confidence', '')}"
        )
    warnings = report.get("data_quality", {}).get("warnings", [])
    if warnings:
        print("-" * 72)
        print("VERI UYARILARI:")
        for warning in warnings:
            print(" -", warning)
    print("=" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto bot Karar Motoru")
    parser.add_argument("--base-dir", default=".", help="JSON dosyalarının bulunduğu klasör")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Çıktı JSON dosyası")
    parser.add_argument("--print-only", action="store_true", help="Dosyaya yazmadan raporu ekrana bas")
    args = parser.parse_args()

    report = build_report(base_dir=args.base_dir)
    print_report(report)

    if not args.print_only:
        output = Path(args.output)
        if not output.is_absolute():
            output = Path(args.base_dir) / output
        save_json_atomically(output, report)
        print("Rapor kaydedildi:", output)


if __name__ == "__main__":
    main()
