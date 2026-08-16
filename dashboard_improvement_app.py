"""Kripto Kontrol Merkezi V3.10 - İyileştirme Karar Merkezi.

Bu katman yalnız ADMIN içindir. Canlı performans, Decision Engine ve gölge
karşılaştırma raporlarını bir araya getirip kontrollü geliştirme kuyruğu üretir.
Hiçbir strateji/config/radar/Telegram dosyasını değiştirmez ve otomatik canlı
kural uygulamaz.
"""
from __future__ import annotations

import argparse
import copy
import html
import json
import math
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_adminux_app as adminux
import dashboard_commercial_app as commercial
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
import dashboard_performance_app as performance
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_10_IMPROVEMENT_DECISION_CENTER_2026_08_16"
DAY = 86_400
DECISION_STALE_HOURS = 36.0
SHADOW_STALE_HOURS = 12.0
REPORT_FILES = ("decision_report.json", "post_result_shadow_v3_report.json")

_REPORT_LOCK = threading.Lock()
_REPORT_CACHE: dict[str, Any] = {
    "key": None,
    "loaded_at": 0.0,
    "documents": {},
    "warnings": [],
    "checked_at": 0,
}

MODEL_LABELS = {
    "TP1_DELAY_BE_UNTIL_TP2": "TP1 sonrası BE'yi TP2'ye kadar geciktir",
    "TP1_SOFT_BE_MINUS_0_25R": "TP1 sonrası yumuşak BE (-0.25R)",
    "TP2_SOFT_BE_MINUS_0_25R": "TP2 sonrası yumuşak BE (-0.25R)",
    "TP3_RUNNER_TRAIL_0_5R": "TP3 sonrası 0.5R takipçi runner",
    "TP3_RUNNER_TRAIL_1_0R": "TP3 sonrası 1.0R takipçi runner",
}

STATUS_LABELS = {
    "LIVE_REVIEW_READY": "CANLI İNCELEMEYE HAZIR",
    "PROMOTION_CANDIDATE": "GÜÇLÜ GÖLGE ADAYI",
    "COMPARE_BACKUP": "KARŞILAŞTIRMA YEDEĞİ",
    "SHADOW_CONTINUE": "GÖLGEDE DEVAM",
    "COLLECT_MORE": "VERİ TOPLA",
    "REJECT": "REDDET / CANLIYA ALMA",
    "NEW_SHADOW_TEST": "YENİ GÖLGE TESTİ AÇ",
    "WATCH_PROTECT": "KORU / TEŞHİS ET",
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _stamp(value: Any) -> int:
    number = _number(value)
    if number is None or number <= 0:
        return 0
    if number > 10_000_000_000:
        number /= 1000
    return int(number)


def _age_hours(timestamp: Any, now: int) -> float | None:
    stamp = _stamp(timestamp)
    if not stamp:
        return None
    return round(max(0, now - stamp) / 3600, 2)


def _fmt_r(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:+.3f}R"


def _fmt_pct(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"%{number:.1f}"


def _model_family(name: str) -> str:
    if name.startswith("TP3_RUNNER_TRAIL"):
        return "TP3_RUNNER"
    if name.startswith("TP1_"):
        return "TP1_MANAGEMENT"
    if name.startswith("TP2_"):
        return "TP2_MANAGEMENT"
    return name


def classify_shadow_model(name: str, row: dict[str, Any]) -> dict[str, Any]:
    """Bir gölge modeli canlıya dokunmadan kanıt kapısından geçirir."""
    sample = int(_number(row.get("sample"), 0) or 0)
    gate = str(row.get("evidence_gate") or "").upper()
    net_r = _number(row.get("net_incremental_r"))
    avg_r = _number(row.get("average_incremental_r"))
    negative_rate = _number(row.get("negative_rate"))
    positive_rate = _number(row.get("positive_rate"))

    if gate != "ENOUGH_SAMPLE" or sample < 20:
        status = "COLLECT_MORE"
        reason = "Kanıt eşiği tamamlanmadı; örnek büyümeden canlı değerlendirme yapılmaz."
    elif net_r is None or avg_r is None:
        status = "COLLECT_MORE"
        reason = "Ek R ölçümü eksik; karar için veri yetersiz."
    elif net_r <= 0 or avg_r <= 0:
        status = "REJECT"
        reason = "Mevcut kurala göre ek Net R üretmiyor; canlıya alınmaz."
    elif sample >= 50 and net_r >= 5.0 and avg_r >= 0.05 and (negative_rate is None or negative_rate <= 15.0):
        status = "LIVE_REVIEW_READY"
        reason = "Yüksek örnek, pozitif ek R ve düşük negatif etki birlikte doğrulandı."
    elif sample >= 30 and net_r >= 1.0 and avg_r >= 0.05 and (negative_rate is None or negative_rate <= 20.0):
        status = "PROMOTION_CANDIDATE"
        reason = "Gölge avantajı güçlü; ikinci bağımsız doğrulama penceresi gerekli."
    elif negative_rate is not None and negative_rate > 50.0:
        status = "SHADOW_CONTINUE"
        reason = "Toplam ek R pozitif olsa da işlemlerin çoğunu olumsuz etkiliyor; canlı için riskli."
    else:
        status = "SHADOW_CONTINUE"
        reason = "Pozitif işaret var fakat canlı terfi eşiği henüz tamamlanmadı."

    return {
        "id": name,
        "family": _model_family(name),
        "label": MODEL_LABELS.get(name, name),
        "source": "POST_RESULT_SHADOW_V3",
        "status": status,
        "status_label": STATUS_LABELS[status],
        "sample": sample,
        "evidence_gate": gate or "UNKNOWN",
        "net_incremental_r": net_r,
        "average_incremental_r": avg_r,
        "positive_rate": positive_rate,
        "negative_rate": negative_rate,
        "reason": reason,
        "automatic_apply": False,
    }


def build_shadow_candidates(report: dict[str, Any]) -> list[dict[str, Any]]:
    models = report.get("models") if isinstance(report, dict) else {}
    if not isinstance(models, dict):
        return []
    rows = [
        classify_shadow_model(str(name), raw)
        for name, raw in models.items()
        if isinstance(raw, dict)
    ]

    # Aynı strateji ailesinde birden fazla güçlü varyant varsa yalnız en iyisi öne çıkar.
    by_family: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["status"] in {"LIVE_REVIEW_READY", "PROMOTION_CANDIDATE"}:
            by_family.setdefault(str(row["family"]), []).append(row)
    for family_rows in by_family.values():
        if len(family_rows) < 2:
            continue
        family_rows.sort(key=lambda item: (_number(item.get("net_incremental_r"), -9999.0) or -9999.0), reverse=True)
        for secondary in family_rows[1:]:
            secondary["status"] = "COMPARE_BACKUP"
            secondary["status_label"] = STATUS_LABELS["COMPARE_BACKUP"]
            secondary["reason"] = "Aynı ailenin daha güçlü varyantı bulundu; bu model karşılaştırma yedeği olarak kalır."

    order = {
        "LIVE_REVIEW_READY": 0,
        "PROMOTION_CANDIDATE": 1,
        "NEW_SHADOW_TEST": 2,
        "SHADOW_CONTINUE": 3,
        "COMPARE_BACKUP": 4,
        "COLLECT_MORE": 5,
        "REJECT": 6,
    }
    rows.sort(key=lambda item: (order.get(str(item.get("status")), 9), -int(item.get("sample") or 0)))
    return rows


def build_stop_experiment(performance_payload: dict[str, Any]) -> dict[str, Any] | None:
    stop = performance_payload.get("stop_diagnosis") or {}
    resolved = int(_number(stop.get("resolved_follow"), 0) or 0)
    return_rate = _number(stop.get("return_rate"))
    total = int(_number(stop.get("sl_total"), 0) or 0)
    if total <= 0:
        return None
    if resolved < 20:
        status = "COLLECT_MORE"
        reason = f"Stop sonrası kesinleşmiş takip örneği {resolved}/20; kök neden testi için veri büyütülüyor."
    elif return_rate is not None and return_rate >= 35.0:
        status = "NEW_SHADOW_TEST"
        reason = "Stop sonrası hedefe dönüş oranı yüksek; giriş zamanlaması/dar stop alternatifi ayrı gölgede sınanmalı."
    else:
        status = "WATCH_PROTECT"
        reason = "Stop sonrası dönüş oranı canlı stop kuralını değiştirecek kadar güçlü değil; mevcut yapı korunur."
    return {
        "id": "STOP_ENTRY_TIMING_REVIEW",
        "family": "STOP_ENTRY",
        "label": "Stop / giriş zamanlaması teşhisi",
        "source": "PERFORMANCE_INTELLIGENCE",
        "status": status,
        "status_label": STATUS_LABELS[status],
        "sample": resolved,
        "sl_total": total,
        "return_rate": return_rate,
        "reason": reason,
        "automatic_apply": False,
    }


def build_trend_guard(performance_payload: dict[str, Any]) -> dict[str, Any] | None:
    window = performance_payload.get("window_intelligence") or {}
    systems = window.get("systems") or []
    premium = next((row for row in systems if str(row.get("system")) == "PREMIUM"), None)
    if not isinstance(premium, dict):
        return None
    trend = premium.get("trend") or {}
    code = str(trend.get("code") or "INSUFFICIENT")
    if code not in {"WEAKENING", "NEGATIVE", "INSUFFICIENT"}:
        return None
    if code == "INSUFFICIENT":
        reason = "Premium trend kararı için dönemsel exact-R örneği yetersiz; canlı ana giriş mantığı korunur."
    else:
        reason = "Premium kısa dönem zayıflığı var; ana stratejiyi topluca değiştirmek yerine alt nedenler gölgede ayrıştırılır."
    return {
        "id": "PREMIUM_PROTECTION_GUARD",
        "family": "PREMIUM_CORE",
        "label": "Premium ana strateji koruma kuralı",
        "source": "PERFORMANCE_INTELLIGENCE",
        "status": "WATCH_PROTECT",
        "status_label": STATUS_LABELS["WATCH_PROTECT"],
        "sample": 0,
        "trend": trend,
        "reason": reason,
        "automatic_apply": False,
    }


def _decision_snapshot(report: dict[str, Any], now: int) -> dict[str, Any]:
    generated_at = _stamp(report.get("generated_at")) if isinstance(report, dict) else 0
    age = _age_hours(generated_at, now)
    stale = age is None or age > DECISION_STALE_HOURS
    executive = report.get("executive") if isinstance(report, dict) else {}
    actions = executive.get("top_actions") if isinstance(executive, dict) else []
    rows = []
    for raw in actions or []:
        if not isinstance(raw, dict):
            continue
        rows.append({
            "component": str(raw.get("component") or "UNKNOWN"),
            "decision_code": str(raw.get("decision_code") or "UNKNOWN"),
            "decision": str(raw.get("decision_tr") or "KARAR YOK"),
            "confidence": str(raw.get("confidence") or "DUSUK"),
            "sample_size": int(_number(raw.get("sample_size"), 0) or 0),
            "next_action": str(raw.get("next_action") or ""),
            "priority": int(_number(raw.get("priority"), 99) or 99),
        })
    rows.sort(key=lambda row: (row["priority"], row["component"]))
    return {
        "generated_at": generated_at,
        "age_hours": age,
        "stale": stale,
        "overall": str((executive or {}).get("overall") or "UNKNOWN"),
        "actions": rows,
        "automatic_apply": False,
    }


def _shadow_snapshot(report: dict[str, Any], now: int) -> dict[str, Any]:
    generated_at = _stamp(report.get("generated_at")) if isinstance(report, dict) else 0
    age = _age_hours(generated_at, now)
    return {
        "generated_at": generated_at,
        "age_hours": age,
        "stale": age is None or age > SHADOW_STALE_HOURS,
        "modeled_trades": int(_number(report.get("modeled_trades"), 0) or 0) if isinstance(report, dict) else 0,
        "automatic_rule_change": bool(((report.get("decision") or {}).get("automatic_rule_change"))) if isinstance(report, dict) else False,
    }


def build_improvement_payload(
    performance_payload: dict[str, Any],
    decision_report: dict[str, Any],
    shadow_report: dict[str, Any],
    *,
    report_warnings: list[str] | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    now = int(now or time.time())
    candidates = build_shadow_candidates(shadow_report)
    stop_candidate = build_stop_experiment(performance_payload)
    if stop_candidate:
        candidates.append(stop_candidate)
    trend_guard = build_trend_guard(performance_payload)
    if trend_guard:
        candidates.append(trend_guard)

    status_counts: dict[str, int] = {}
    for row in candidates:
        status = str(row.get("status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    decision_snapshot = _decision_snapshot(decision_report, now)
    shadow_snapshot = _shadow_snapshot(shadow_report, now)
    warnings = list(report_warnings or [])
    if decision_snapshot["stale"]:
        warnings.append("Decision Engine raporu güncel değil; iç kararlar eski snapshot olarak gösteriliyor.")
    if shadow_snapshot["stale"]:
        warnings.append("Post-result gölge raporu güncel değil; canlı terfi kararı verilmez.")

    live_review_allowed = not shadow_snapshot["stale"] and not decision_snapshot["stale"]
    if not live_review_allowed:
        for row in candidates:
            if row.get("status") == "LIVE_REVIEW_READY":
                row["status"] = "PROMOTION_CANDIDATE"
                row["status_label"] = STATUS_LABELS["PROMOTION_CANDIDATE"]
                row["reason"] = str(row.get("reason") or "") + " Güncel Decision Engine doğrulaması bekleniyor."

    return {
        "version": VERSION,
        "generated_at": now,
        "mode": "ADMIN_ANALYSIS_ONLY_NO_AUTO_APPLY",
        "auto_apply": False,
        "live_change_policy": "SHADOW_FIRST_REVIEW_THEN_SEPARATE_REVERSIBLE_PR",
        "promotion_gate": {
            "minimum_sample": 50,
            "minimum_net_incremental_r": 5.0,
            "minimum_average_incremental_r": 0.05,
            "maximum_negative_rate_percent": 15.0,
            "fresh_decision_engine_required": True,
            "fresh_shadow_report_required": True,
        },
        "summary": {
            "total_candidates": len(candidates),
            "live_review_ready": status_counts.get("LIVE_REVIEW_READY", 0),
            "promotion_candidates": status_counts.get("PROMOTION_CANDIDATE", 0),
            "new_shadow_tests": status_counts.get("NEW_SHADOW_TEST", 0),
            "collect_more": status_counts.get("COLLECT_MORE", 0),
            "rejected": status_counts.get("REJECT", 0),
            "live_review_allowed": live_review_allowed,
        },
        "candidates": candidates,
        "decision_engine": decision_snapshot,
        "post_result_shadow": shadow_snapshot,
        "warnings": sorted(set(warnings)),
        "safety": {
            "signal_engine_changed": False,
            "strategy_changed": False,
            "config_changed": False,
            "telegram_changed": False,
            "orders_enabled": False,
        },
    }


def _fetch_report(config: PanelConfig, filename: str) -> dict[str, Any]:
    if config.github_token:
        path = urllib.parse.quote(filename, safe="/")
        ref = urllib.parse.quote(config.ref, safe="")
        request = urllib.request.Request(
            f"https://api.github.com/repos/{config.repository}/contents/{path}?ref={ref}",
            headers={
                "Accept": "application/vnd.github.raw+json",
                "Authorization": f"Bearer {config.github_token}",
                "User-Agent": "Kripto-Panel-Improvement/3.10",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            loaded = json.loads(response.read().decode("utf-8"))
    else:
        with (config.root / filename).open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    return loaded if isinstance(loaded, dict) else {}


def load_reports(config: PanelConfig, *, cache_seconds: int = 30) -> tuple[dict[str, Any], list[str], int]:
    key = f"{config.repository}@{config.ref}:{config.root}"
    mono = time.monotonic()
    with _REPORT_LOCK:
        if _REPORT_CACHE.get("key") == key and mono - float(_REPORT_CACHE.get("loaded_at") or 0) < cache_seconds:
            return copy.deepcopy(_REPORT_CACHE.get("documents") or {}), list(_REPORT_CACHE.get("warnings") or []), int(_REPORT_CACHE.get("checked_at") or 0)

    documents: dict[str, Any] = {}
    warnings: list[str] = []
    for filename in REPORT_FILES:
        try:
            documents[filename] = _fetch_report(config, filename)
            if not documents[filename]:
                warnings.append(f"{filename}: boş veya geçersiz")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
            warnings.append(f"{filename}: okunamadı ({type(exc).__name__})")
            documents[filename] = {}

    checked = int(time.time())
    with _REPORT_LOCK:
        previous = copy.deepcopy(_REPORT_CACHE.get("documents") or {}) if _REPORT_CACHE.get("key") == key else {}
        if warnings and previous:
            for filename in REPORT_FILES:
                if not documents.get(filename) and previous.get(filename):
                    documents[filename] = previous[filename]
                    warnings.append(f"{filename}: son geçerli önbellek kullanıldı")
        _REPORT_CACHE.update({
            "key": key,
            "loaded_at": mono,
            "documents": copy.deepcopy(documents),
            "warnings": list(warnings),
            "checked_at": checked,
        })
    return documents, warnings, checked


def render_improvement_page(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    candidates = payload.get("candidates") or []
    decision = payload.get("decision_engine") or {}
    shadow = payload.get("post_result_shadow") or {}

    cards = []
    for row in candidates:
        status = str(row.get("status") or "UNKNOWN")
        tone = "good" if status in {"LIVE_REVIEW_READY", "PROMOTION_CANDIDATE"} else "bad" if status == "REJECT" else "warn"
        metrics = []
        if row.get("sample") is not None:
            metrics.append(f"Örnek: {int(row.get('sample') or 0)}")
        if row.get("net_incremental_r") is not None:
            metrics.append("Ek Net R: " + _fmt_r(row.get("net_incremental_r")))
        if row.get("average_incremental_r") is not None:
            metrics.append("Ort.: " + _fmt_r(row.get("average_incremental_r")))
        if row.get("negative_rate") is not None:
            metrics.append("Negatif etki: " + _fmt_pct(row.get("negative_rate")))
        if row.get("return_rate") is not None:
            metrics.append("Stop sonrası dönüş: " + _fmt_pct(row.get("return_rate")))
        cards.append(
            '<article class="candidate">'
            f'<div class="candidate-top"><span class="status {tone}">{html.escape(str(row.get("status_label") or status))}</span><small>{html.escape(str(row.get("source") or ""))}</small></div>'
            f'<h3>{html.escape(str(row.get("label") or row.get("id") or "Aday"))}</h3>'
            f'<div class="metrics">{" · ".join(html.escape(item) for item in metrics) or "Ölçüm bekleniyor"}</div>'
            f'<p>{html.escape(str(row.get("reason") or ""))}</p>'
            '<div class="lock">Canlıya otomatik uygulanmaz</div>'
            '</article>'
        )
    if not cards:
        cards.append('<div class="empty">Şu anda değerlendirme kuyruğunda aday yok.</div>')

    decision_rows = []
    for row in decision.get("actions") or []:
        decision_rows.append(
            '<div class="decision-row">'
            f'<b>{html.escape(str(row.get("component") or ""))}</b>'
            f'<span>{html.escape(str(row.get("decision") or ""))}</span>'
            f'<small>{html.escape(str(row.get("next_action") or ""))}</small>'
            f'<em>{int(row.get("sample_size") or 0)} örnek · {html.escape(str(row.get("confidence") or ""))}</em>'
            '</div>'
        )
    if not decision_rows:
        decision_rows.append('<div class="empty">Decision Engine kararı bulunamadı.</div>')

    warning_html = "".join(f'<div class="warning">{html.escape(str(item))}</div>' for item in payload.get("warnings") or [])
    decision_age = "—" if decision.get("age_hours") is None else f"{float(decision.get('age_hours')):.1f} saat"
    shadow_age = "—" if shadow.get("age_hours") is None else f"{float(shadow.get('age_hours')):.1f} saat"

    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>İyileştirme Karar Merkezi</title><style>
:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--amber:#ffbd59;--red:#ff627d;--blue:#60a5fa}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,rgba(44,230,191,.08),transparent 30%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.shell{{width:min(1160px,calc(100% - 20px));margin:auto;padding:22px 0 60px}}.top{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}}h1{{margin:0;font-size:30px}}.top p{{margin:4px 0;color:var(--muted)}}.nav a{{display:inline-block;border:1px solid var(--line);border-radius:9px;padding:8px 10px;margin-left:5px;font-size:9px;font-weight:900}}.guard{{margin:15px 0;border:1px solid rgba(44,230,191,.35);background:rgba(44,230,191,.06);border-radius:13px;padding:12px;display:flex;justify-content:space-between;gap:10px;align-items:center}}.guard b{{color:var(--teal)}}.guard span{{color:var(--muted);font-size:10px}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:14px 0}}.kpi{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px}}.kpi small{{display:block;color:var(--muted);font-size:8px}}.kpi b{{font-size:22px}}.section{{margin-top:18px}}.section h2{{margin:0 0 8px;font-size:18px}}.section>p{{margin:0 0 10px;color:var(--muted);font-size:10px}}.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}}.candidate{{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:13px}}.candidate-top{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.candidate-top small{{color:var(--muted);font-size:8px}}.status{{border:1px solid currentColor;border-radius:999px;padding:3px 7px;font-size:8px;font-weight:950}}.status.good{{color:var(--teal)}}.status.warn{{color:var(--amber)}}.status.bad{{color:var(--red)}}.candidate h3{{margin:9px 0 4px;font-size:15px}}.candidate p{{color:#9bb2af;margin:8px 0;font-size:10px}}.metrics{{color:var(--blue);font-size:9px;font-weight:800}}.lock{{display:inline-block;border:1px solid #29434a;border-radius:8px;padding:5px 7px;color:var(--muted);font-size:8px}}.decision-row{{display:grid;grid-template-columns:150px 210px 1fr 155px;gap:9px;align-items:center;background:var(--panel);border:1px solid var(--line);border-radius:11px;padding:10px;margin-bottom:7px}}.decision-row span{{font-weight:900}}.decision-row small{{color:var(--muted)}}.decision-row em{{font-style:normal;color:var(--blue);font-size:9px;text-align:right}}.sourcebar{{display:grid;grid-template-columns:1fr 1fr;gap:8px}}.source{{border:1px solid var(--line);background:var(--panel);border-radius:11px;padding:10px}}.source b,.source span{{display:block}}.source span{{color:var(--muted);font-size:9px}}.warning{{margin:7px 0;border:1px solid rgba(255,189,89,.32);background:rgba(255,189,89,.05);border-radius:10px;padding:9px;color:#ffd18a;font-size:9px}}.pipeline{{display:grid;grid-template-columns:repeat(4,1fr);gap:7px}}.step{{text-align:center;border:1px solid var(--line);border-radius:10px;padding:10px;background:#091820}}.step b{{display:block;color:var(--teal)}}.step span{{color:var(--muted);font-size:8px}}.empty{{border:1px dashed var(--line);border-radius:12px;padding:22px;color:var(--muted);text-align:center}}@media(max-width:780px){{.kpis{{grid-template-columns:1fr 1fr}}.grid,.sourcebar,.pipeline{{grid-template-columns:1fr}}.decision-row{{grid-template-columns:1fr}}.decision-row em{{text-align:left}}.guard{{align-items:flex-start;flex-direction:column}}}}
</style></head><body><div class="shell"><div class="top"><div><h1>İyileştirme Karar Merkezi</h1><p>Gerçek performans + gölge test + Decision Engine kanıtını tek geliştirme kuyruğunda birleştirir.</p></div><div class="nav"><a href="/performance-intelligence">Performans Zekâsı</a><a href="/admin/center">Yönetim</a><a href="/">Panel</a></div></div><div class="guard"><div><b>OTOMATİK CANLI DEĞİŞİKLİK KAPALI</b><span> · Her canlı değişiklik ayrı, geri alınabilir kod incelemesinden geçer.</span></div><strong>{'CANLI İNCELEME AÇIK' if summary.get('live_review_allowed') else 'KANIT / GÜNCELLİK BEKLENİYOR'}</strong></div>{warning_html}<div class="kpis"><div class="kpi"><small>Toplam aday</small><b>{int(summary.get('total_candidates') or 0)}</b></div><div class="kpi"><small>Canlı incelemeye hazır</small><b>{int(summary.get('live_review_ready') or 0)}</b></div><div class="kpi"><small>Güçlü gölge adayı</small><b>{int(summary.get('promotion_candidates') or 0)}</b></div><div class="kpi"><small>Yeni gölge testi</small><b>{int(summary.get('new_shadow_tests') or 0)}</b></div><div class="kpi"><small>Reddedilen</small><b>{int(summary.get('rejected') or 0)}</b></div></div><section class="section"><h2>Kanıt → Düzeltme kuyruğu</h2><p>Bir sonuç tek başına kural değiştirmez. Kanıt eşiğini geçen fikirler önce bağımsız gölge doğrulamasına gider.</p><div class="grid">{''.join(cards)}</div></section><section class="section"><h2>Terfi hattı</h2><div class="pipeline"><div class="step"><b>1 · VERİ</b><span>Gerçek TP/SL/Net R</span></div><div class="step"><b>2 · GÖLGE</b><span>Canlıyı etkilemeden alternatif</span></div><div class="step"><b>3 · KARŞILAŞTIR</b><span>Ek R + negatif etki + örnek</span></div><div class="step"><b>4 · AYRI PR</b><span>Küçük, ölçülebilir, geri alınabilir</span></div></div></section><section class="section"><h2>Decision Engine iç kararları</h2><p>Rapor yaşı: {html.escape(decision_age)} · Eski rapor canlı terfi kapısını otomatik kapatır.</p>{''.join(decision_rows)}</section><section class="section"><h2>Kaynak güncelliği</h2><div class="sourcebar"><div class="source"><b>Decision Engine</b><span>Yaş: {html.escape(decision_age)} · {'ESKİ' if decision.get('stale') else 'GÜNCEL'}</span></div><div class="source"><b>Post-result Shadow V3</b><span>Yaş: {html.escape(shadow_age)} · {int(shadow.get('modeled_trades') or 0)} modellenmiş işlem · {'ESKİ' if shadow.get('stale') else 'GÜNCEL'}</span></div></div></section></div></body></html>'''


def enhance_admin_shortcut(body: str, payload: dict[str, Any]) -> str:
    if 'id="v310ImprovementShortcut"' in body:
        return body
    summary = payload.get("summary") or {}
    card = (
        '<a id="v310ImprovementShortcut" href="/admin/improvement-center">'
        '<b>⚙ İyileştirme Karar Merkezi</b>'
        f'<span>{int(summary.get("promotion_candidates") or 0)} güçlü gölge adayı · '
        f'{int(summary.get("new_shadow_tests") or 0)} yeni gölge testi · '
        f'{int(summary.get("live_review_ready") or 0)} canlı inceleme</span></a>'
    )
    marker = '<div class="quick">'
    return body.replace(marker, marker + card, 1) if marker in body else body


def make_v310_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    BaseHandler = performance.make_v39_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V310Handler(BaseHandler):
        server_version = "KriptoPanel/3.10"

        def _improvement_payload(self) -> dict[str, Any]:
            perf = self._performance_payload()
            documents, warnings, checked = load_reports(config)
            payload = build_improvement_payload(
                perf,
                documents.get("decision_report.json") or {},
                documents.get("post_result_shadow_v3_report.json") or {},
                report_warnings=warnings,
            )
            payload["reports_checked_at"] = checked
            return payload

        def _send(self, status: int, body: str | bytes, content_type: str, *, cookies: list[str] | None = None, nonce: str | None = None) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html") and path == "/admin/center":
                if self._admin_session():
                    try:
                        body = enhance_admin_shortcut(body, self._improvement_payload())
                    except Exception:
                        pass
            super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "improvement_decision_center": True,
                    "admin_only": True,
                    "shadow_first": True,
                    "auto_apply": False,
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                })
                return
            if path in {"/admin/improvement-center", "/admin/improvements"}:
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                try:
                    body = render_improvement_page(self._improvement_payload())
                    body = adminux.enhance_standalone(body, session, is_admin=True)
                    self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                except Exception as exc:
                    self._send(HTTPStatus.INTERNAL_SERVER_ERROR, f"İyileştirme Karar Merkezi hazırlanamadı: {html.escape(type(exc).__name__)}", "text/plain; charset=utf-8")
                return
            if path == "/api/admin/improvement-decisions":
                if not self._admin_session():
                    self._json(HTTPStatus.FORBIDDEN, {"error": "Yönetici erişimi gerekli."})
                    return
                self._json(HTTPStatus.OK, self._improvement_payload())
                return
            return super().do_GET()

    return V310Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.10 İyileştirme Karar Merkezi")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = lifecycle.lifecycle_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v310_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} admin_only=1 auto_apply=0 shadow_first=1")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
