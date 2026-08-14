from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

VERSION = "SYSTEM_CONTROL_CENTER_V1_3_SIZE_GUARD_CRITICAL_ALERT_2026_08_14"
MODE = "READ_ONLY_MONITOR_CRITICAL_RED_ALERT_ONLY_NO_ORDERS_NO_SIGNAL_CHANGE_NO_AUTO_APPLY"

REPORT_JSON = "system_control_center_report.json"
REPORT_MD = "system_control_center_report.md"
ALERT_STATE_FILE = "system_control_alert_state.json"

FILE_SIZE_YELLOW_BYTES = 4 * 1024 * 1024
FILE_SIZE_RED_BYTES = 8 * 1024 * 1024
CRITICAL_ALERT_COOLDOWN_SECONDS = 12 * 60 * 60
TELEGRAM_TIMEOUT_SECONDS = 10

FAST_STALE_HOURS = 6.0
HOURLY_STALE_HOURS = 8.0
DAILY_STALE_HOURS = 36.0

# Sağlık/güncellik ölçümünde gelecekte planlanmış zamanlar
# (ör. bir sonraki funding saati) aktivite zamanı değildir.
# Küçük sistem saati farkları için yalnız 5 dakikalık tolerans bırakılır.
MAX_FUTURE_SKEW_SECONDS = 5 * 60

NON_ACTIVITY_TIMESTAMP_KEYS = {
    "funding_next_timestamp",
    "next_funding_timestamp",
    "funding_next_time",
    "next_funding_time",
}

COMPONENTS = {
    "PREMIUM": {
        "label": "Premium MTF",
        "kind": "LIVE_SIGNAL",
        "files": ["open_signals.json", "trade_ledger.json", "performance.json"],
        "workflow": ".github/workflows/main.yml",
        "stale_hours": FAST_STALE_HOURS,
        "open_paths": [("open_signals.json", None)],
    },
    "SCALP": {
        "label": "Scalp Radar",
        "kind": "LIVE_SIGNAL",
        "files": ["scalp_radar_state.json", "scalp_performance_ledger.json"],
        "workflow": ".github/workflows/scalp-radar.yml",
        "stale_hours": FAST_STALE_HOURS,
        "open_paths": [("scalp_radar_state.json", "open_scalp_signals")],
    },
    "PUMP_DUMP": {
        "label": "Pump/Dump Radar",
        "kind": "LIVE_SIGNAL",
        "files": ["pump_radar_state.json", "pump_performance_ledger.json"],
        "workflow": ".github/workflows/pump-radar.yml",
        "stale_hours": FAST_STALE_HOURS,
        "open_paths": [
            ("pump_radar_state.json", "open_signals"),
            ("pump_radar_state.json", "open_pump_signals"),
        ],
    },
    "SWING_V4_SHADOW": {
        "label": "Swing Shadow V4",
        "kind": "SHADOW",
        "files": ["swing_shadow_v4_ledger.json"],
        "workflow": ".github/workflows/swing-shadow-v4.yml",
        "stale_hours": HOURLY_STALE_HOURS,
        "open_paths": [("swing_shadow_v4_ledger.json", "open_positions")],
    },
    "POSITION_TREND": {
        "label": "Ana Trend Pozisyon Radarı",
        "kind": "SHADOW",
        "files": ["position_trend_shadow_state.json", "position_trend_shadow_ledger.json"],
        "workflow": ".github/workflows/position-trend-shadow.yml",
        "stale_hours": HOURLY_STALE_HOURS,
        "open_paths": [("position_trend_shadow_state.json", "open_trades")],
    },
    "ALL_MARKET": {
        "label": "Tüm Piyasa Keşif Radarı",
        "kind": "SHADOW",
        "files": ["all_market_shadow_state.json", "all_market_shadow_ledger.json"],
        "workflow": ".github/workflows/all-market-shadow.yml",
        "stale_hours": HOURLY_STALE_HOURS,
        "open_paths": [],
    },
    "MOMENTUM_SHADOW": {
        "label": "Momentum Shadow",
        "kind": "SHADOW",
        "files": ["momentum_shadow.json"],
        "workflow": None,
        "stale_hours": DAILY_STALE_HOURS,
        "open_paths": [],
    },
    "RANGE_SHADOW": {
        "label": "Range Cycle Shadow",
        "kind": "SHADOW",
        "files": ["range_shadow.json"],
        "workflow": None,
        "stale_hours": DAILY_STALE_HOURS,
        "open_paths": [],
    },
    "PORTFOLIO_RISK": {
        "label": "Portfolio Risk",
        "kind": "GUARD",
        "files": ["portfolio_risk_shadow.json", "portfolio_risk_outcomes.json"],
        "workflow": None,
        "stale_hours": DAILY_STALE_HOURS,
        "open_paths": [],
    },
    "DECISION_ENGINE": {
        "label": "Decision Engine",
        "kind": "ANALYSIS",
        "files": ["decision_report.json"],
        "workflow": ".github/workflows/decision-engine.yml",
        "stale_hours": DAILY_STALE_HOURS,
        "open_paths": [],
    },
    "PRESCRIPTION_ENGINE": {
        "label": "Prescription Engine",
        "kind": "ANALYSIS",
        "files": ["prescription_report.json"],
        "workflow": None,
        "stale_hours": DAILY_STALE_HOURS,
        "open_paths": [],
    },
    "NEW_LISTING": {
        "label": "New Listing Radar",
        "kind": "RADAR",
        "files": ["new_listing_performance_ledger.json"],
        "workflow": ".github/workflows/new-listing-radar.yml",
        "stale_hours": DAILY_STALE_HOURS,
        "open_paths": [],
    },
    "POST_RESULT": {
        "label": "TP Sonrası / Post Result Shadow",
        "kind": "INTEGRATED_ANALYSIS",
        "files": [
            "trade_ledger.json",
            "post_result_shadow_v2_report.json",
            "post_result_shadow_v3_report.json",
        ],
        "workflow": ".github/workflows/main.yml",
        "stale_hours": FAST_STALE_HOURS,
        "open_paths": [],
        "integrated_note": "Premium ledger + V2 ölçüm + V3 karşılaştırmalı yönetim",
    },
}

TIMESTAMP_KEYS = {
    "last_update", "last_run", "updated_at", "generated_at", "recorded_at",
    "outcome_checked_at", "last_checked_at", "last_tracking_at", "closed_at",
    "opened_at", "created_at", "checked_at", "last_deep_scan_at",
    "tp1_hit_at", "tp2_hit_at", "tp3_hit_at",
}

DECISION_MAP = {
    "PREMIUM": "PREMIUM",
    "SCALP": "SCALP",
    "PUMP_DUMP": "PUMP_DUMP",
    "SWING_V4_SHADOW": "SWING_V4_SHADOW",
    "MOMENTUM_SHADOW": "MOMENTUM_SHADOW",
    "RANGE_SHADOW": "RANGE_SHADOW",
    "PORTFOLIO_RISK": "PORTFOLIO_RISK",
    "POST_RESULT": "POST_RESULT_SHADOW",
}

def now_ts():
    return int(time.time())

def utc_text(ts=None):
    value = int(ts if ts is not None else now_ts())
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()

def safe_float(value, default=None):
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except Exception:
        return default

def atomic_write_text(filename, content):
    target = os.path.abspath(filename)
    directory = os.path.dirname(target) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_control_", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass

def atomic_write_json(filename, data):
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    json.loads(content)
    atomic_write_text(filename, content)

def load_json_file(path):
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("JSON kök veri sözlük değil")
    return data

def _possible_timestamp(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        number = int(value)
        if number > 10_000_000_000:
            number //= 1000
        return number if 1_600_000_000 <= number <= now_ts() + MAX_FUTURE_SKEW_SECONDS else 0
    if isinstance(value, str):
        text = value.strip()
        try:
            number = int(float(text))
            if number > 10_000_000_000:
                number //= 1000
            if 1_600_000_000 <= number <= now_ts() + MAX_FUTURE_SKEW_SECONDS:
                return number
        except Exception:
            pass
        normalized = text.replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            number = int(dt.timestamp())
            if 1_600_000_000 <= number <= now_ts() + MAX_FUTURE_SKEW_SECONDS:
                return number
        except Exception:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S UTC", "%Y-%m-%d %H:%M:%S"):
            try:
                return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp())
            except Exception:
                pass
    return 0

def extract_latest_timestamp(data, depth=0, max_depth=12):
    if depth > max_depth:
        return 0
    best = 0
    if isinstance(data, dict):
        for key, value in data.items():
            k = str(key).lower()

            if k in NON_ACTIVITY_TIMESTAMP_KEYS:
                # Örn. funding_next_timestamp gelecekteki planlı saattir;
                # sistemin son çalışma/güncelleme zamanı değildir.
                pass
            elif k in TIMESTAMP_KEYS or k.endswith("_at") or k.endswith("_timestamp") or k.endswith("_time"):
                best = max(best, _possible_timestamp(value))
            if isinstance(value, (dict, list)):
                best = max(best, extract_latest_timestamp(value, depth + 1, max_depth))
    elif isinstance(data, list):
        iterable = data if len(data) <= 1000 else data[:100] + data[-900:]
        for item in iterable:
            best = max(best, extract_latest_timestamp(item, depth + 1, max_depth))
    return best

def file_info(path):
    p = Path(path)
    info = {
        "path": path,
        "exists": p.exists(),
        "bytes": p.stat().st_size if p.exists() else 0,
        "valid_json": False,
        "error": None,
        "latest_timestamp": 0,
    }
    if not p.exists():
        info["error"] = "MISSING"
        return info
    try:
        data = load_json_file(path)
        info["valid_json"] = True
        info["latest_timestamp"] = extract_latest_timestamp(data)
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
    return info

def get_path_dict(data, key):
    if key is None:
        return data if isinstance(data, dict) else {}
    value = data.get(key, {})
    return value if isinstance(value, dict) else {}

def record_is_open(item):
    if not isinstance(item, dict):
        return False
    if item.get("closed") is True:
        return False
    status = str(item.get("status", "")).upper()
    final = str(item.get("final_result") or item.get("result") or "").upper()
    if status in {"CLOSED", "DONE", "RESOLVED"}:
        return False
    if final in {"SL", "TP3", "BE", "EXPIRED", "CLOSED"}:
        return False
    return True

def unique_open_count(cache, open_paths):
    seen = set()
    for filename, key in open_paths:
        records = get_path_dict(cache.get(filename, {}), key)
        for record_id, item in records.items():
            if not record_is_open(item):
                continue
            trade_id = str(item.get("trade_id") or item.get("performance_record_id") or record_id)
            seen.add(trade_id)
    return len(seen)

def all_market_metrics(data):
    summary = data.get("summary", {})
    overall = summary.get("overall", {}) if isinstance(summary, dict) else {}
    if not isinstance(overall, dict):
        overall = {}
    return {
        "total": overall.get("total"),
        "open": overall.get("open"),
        "closed": overall.get("closed"),
        "net_r": overall.get("net_r"),
        "avg_r": overall.get("avg_r"),
        "tp3_rate_percent": overall.get("tp3_rate_percent"),
    }

def position_trend_metrics(state, ledger):
    summary = ledger.get("summary", {})
    if not isinstance(summary, dict):
        summary = {}
    opens = state.get("open_trades", {})
    if not isinstance(opens, dict):
        opens = {}
    names = []
    for trade in opens.values():
        if record_is_open(trade):
            names.append(f"{trade.get('symbol','?')} {trade.get('direction','')}".strip())
    return {
        "open": unique_open_count({"s": {"open_trades": opens}}, [("s", "open_trades")]),
        "open_names": names[:10],
        "closed": summary.get("total_closed"),
        "net_r_after_costs": summary.get("net_r_after_costs"),
        "avg_r_after_costs": summary.get("avg_r_after_costs"),
        "profit_factor": summary.get("profit_factor"),
        "max_drawdown_r": summary.get("max_drawdown_r"),
    }

def decision_components(cache):
    report = cache.get("decision_report.json", {})
    value = report.get("components", {})
    return value if isinstance(value, dict) else {}

def performance_advisory(key, decisions, cache):
    mapped = DECISION_MAP.get(key)
    if mapped and isinstance(decisions.get(mapped), dict):
        item = decisions[mapped]
        return {
            "source": "decision_report.json",
            "decision_code": item.get("decision_code"),
            "decision_tr": item.get("decision_tr"),
            "confidence": item.get("confidence"),
            "sample_size": item.get("sample_size"),
            "next_action": item.get("next_action"),
        }
    if key == "POSITION_TREND":
        closed = int(position_trend_metrics(
            cache.get("position_trend_shadow_state.json", {}),
            cache.get("position_trend_shadow_ledger.json", {})
        ).get("closed") or 0)
        return {
            "source": "position_trend_shadow_ledger.json",
            "decision_code": "VERI_TOPLA",
            "decision_tr": "⚪ VERİ TOPLA",
            "confidence": "DUSUK",
            "sample_size": closed,
            "next_action": "Yeterli kapalı sanal işlem oluşmadan canlı karar verme.",
        }
    if key == "ALL_MARKET":
        metrics = all_market_metrics(cache.get("all_market_shadow_ledger.json", {}))
        closed = int(metrics.get("closed") or 0)
        net_r = safe_float(metrics.get("net_r"), 0.0) or 0.0
        if closed < 30:
            code, text, conf = "VERI_TOPLA", "⚪ VERİ TOPLA", "DUSUK"
        elif net_r > 0:
            code, text, conf = "IZLE_DOGRULA", "🟡 İZLE / DOĞRULA", "ORTA"
        else:
            code, text, conf = "CANLIYA_ALMA", "🔴 CANLIYA ALMA", "ORTA"
        return {
            "source": "all_market_shadow_ledger.json",
            "decision_code": code,
            "decision_tr": text,
            "confidence": conf,
            "sample_size": closed,
            "next_action": "Shadow performans örneğini büyüt.",
        }
    return {
        "source": None,
        "decision_code": "NO_DECISION",
        "decision_tr": "⚪ KARAR YOK",
        "confidence": None,
        "sample_size": None,
        "next_action": None,
    }

def component_metrics(key, cache, cfg):
    metrics = {}
    if cfg.get("open_paths"):
        metrics["open_count"] = unique_open_count(cache, cfg["open_paths"])
    if key == "POSITION_TREND":
        metrics.update(position_trend_metrics(
            cache.get("position_trend_shadow_state.json", {}),
            cache.get("position_trend_shadow_ledger.json", {})
        ))
    elif key == "ALL_MARKET":
        metrics.update(all_market_metrics(cache.get("all_market_shadow_ledger.json", {})))
    elif key in {"SCALP", "PUMP_DUMP"}:
        state_file = "scalp_radar_state.json" if key == "SCALP" else "pump_radar_state.json"
        stats = cache.get(state_file, {}).get("stats", {})
        if isinstance(stats, dict):
            metrics["event_stats"] = stats
    return metrics

def health_status(files, latest_ts, stale_hours, workflow_path):
    reasons = []
    missing = [f["path"] for f in files if not f["exists"]]
    invalid = [f["path"] for f in files if f["exists"] and not f["valid_json"]]
    critical_size = [
        f for f in files
        if f["exists"] and int(f.get("bytes") or 0) >= FILE_SIZE_RED_BYTES
    ]
    warning_size = [
        f for f in files
        if f["exists"]
        and FILE_SIZE_YELLOW_BYTES <= int(f.get("bytes") or 0) < FILE_SIZE_RED_BYTES
    ]

    if missing:
        reasons.append("Eksik dosya: " + ", ".join(missing))
    if invalid:
        reasons.append("Bozuk JSON: " + ", ".join(invalid))
    if critical_size:
        reasons.append(
            "Kritik dosya büyüklüğü: "
            + ", ".join(
                f"{item['path']}={int(item.get('bytes') or 0) / (1024 * 1024):.2f}MB"
                for item in critical_size
            )
        )
    if warning_size:
        reasons.append(
            "Dosya büyüme uyarısı: "
            + ", ".join(
                f"{item['path']}={int(item.get('bytes') or 0) / (1024 * 1024):.2f}MB"
                for item in warning_size
            )
        )
    if workflow_path and not Path(workflow_path).exists():
        reasons.append("Workflow dosyası yok: " + workflow_path)
    if invalid or critical_size:
        return "RED", reasons, None
    if files and sum(1 for f in files if f["exists"]) == 0:
        return "RED", reasons, None

    age_hours = None
    if latest_ts:
        # Küçük saat farkları olsa bile raporda negatif veri yaşı gösterme.
        age_hours = round(max(0.0, (now_ts() - latest_ts) / 3600), 2)
        if age_hours > stale_hours:
            reasons.append(f"Veri eski: {age_hours:.2f}s > {stale_hours:.2f}s")
            return "YELLOW", reasons, age_hours
    else:
        reasons.append("Güncellik timestamp'i bulunamadı")
        return "YELLOW", reasons, None

    if missing or warning_size:
        return "YELLOW", reasons, age_hours
    reasons.append("Dosyalar geçerli, boyut kontrollü ve veri güncel")
    return "GREEN", reasons, age_hours

def build_report(root="."):
    original = os.getcwd()
    os.chdir(root)
    try:
        all_files = sorted({f for cfg in COMPONENTS.values() for f in cfg.get("files", [])})
        cache, infos = {}, {}
        for filename in all_files:
            info = file_info(filename)
            infos[filename] = info
            if info["exists"] and info["valid_json"]:
                cache[filename] = load_json_file(filename)

        decisions = decision_components(cache)
        components = {}
        counts = {"GREEN": 0, "YELLOW": 0, "RED": 0}

        for key, cfg in COMPONENTS.items():
            file_infos = [infos.get(f, file_info(f)) for f in cfg.get("files", [])]
            latest_ts = max([int(i.get("latest_timestamp") or 0) for i in file_infos] or [0])
            health, reasons, age = health_status(
                file_infos, latest_ts, float(cfg.get("stale_hours", DAILY_STALE_HOURS)), cfg.get("workflow")
            )
            counts[health] += 1
            components[key] = {
                "label": cfg["label"],
                "kind": cfg["kind"],
                "health": health,
                "health_reasons": reasons,
                "latest_timestamp": latest_ts or None,
                "latest_utc": utc_text(latest_ts) if latest_ts else None,
                "age_hours": age,
                "workflow": cfg.get("workflow"),
                "workflow_file_exists": Path(cfg["workflow"]).exists() if cfg.get("workflow") else None,
                "files": file_infos,
                "metrics": component_metrics(key, cache, cfg),
                "performance": performance_advisory(key, decisions, cache),
                "integrated_note": cfg.get("integrated_note"),
            }

        critical = [k for k, v in components.items() if v["health"] == "RED"]
        attention = [k for k, v in components.items() if v["health"] == "YELLOW"]
        overall = "RED" if critical else "YELLOW" if attention else "GREEN"

        return {
            "version": VERSION,
            "mode": MODE,
            "auto_apply": False,
            "generated_at": now_ts(),
            "generated_at_utc": utc_text(),
            "executive": {
                "component_count": len(components),
                "health_counts": counts,
                "critical_components": critical,
                "attention_components": attention,
                "overall_health": overall,
                "note": "Sağlık teknik çalışma/veri güncelliğidir; performans kararı ayrı alandadır.",
            },
            "components": components,
        }
    finally:
        os.chdir(original)

def icon(status):
    return {"GREEN": "🟢", "YELLOW": "🟡", "RED": "🔴"}.get(status, "⚪")

def report_markdown(report):
    ex = report["executive"]
    lines = [
        "# Sistem Kontrol Merkezi",
        "",
        f"- Sürüm: `{report['version']}`",
        f"- Mod: `{report['mode']}`",
        f"- Üretim: {report['generated_at_utc']}",
        f"- Genel sağlık: {icon(ex['overall_health'])} **{ex['overall_health']}**",
        "",
        "## Sistemler",
        "",
        "| Sistem | Sağlık | Açık | Performans kararı | Veri yaşı |",
        "|---|---:|---:|---|---:|",
    ]
    for item in report["components"].values():
        metrics = item.get("metrics", {})
        open_count = metrics.get("open_count", metrics.get("open", "-"))
        decision = item.get("performance", {}).get("decision_tr") or "⚪ KARAR YOK"
        age = item.get("age_hours")
        age_text = f"{age:.2f}s" if isinstance(age, (int, float)) else "-"
        lines.append(
            f"| {item['label']} | {icon(item['health'])} {item['health']} | {open_count} | {decision} | {age_text} |"
        )
    lines += [
        "",
        "## Güvenlik",
        "",
        "- `auto_apply = false`",
        "- Telegram yalnız genel sağlık RED olduğunda, aynı hata için 12 saatlik tekrar engeliyle gönderilir.",
        "- Emir açmaz.",
        "- Mevcut bot state/ledger dosyalarına yazmaz.",
        "- Strateji/config/TP/SL değiştirmez.",
        "",
        "- Kritik: " + (", ".join(ex["critical_components"]) if ex["critical_components"] else "Yok"),
        "- Dikkat: " + (", ".join(ex["attention_components"]) if ex["attention_components"] else "Yok"),
        "",
    ]
    return "\n".join(lines)

def critical_alert_fingerprint(report):
    executive = report.get("executive", {}) if isinstance(report, dict) else {}
    if executive.get("overall_health") != "RED":
        return ""

    components = report.get("components", {})
    critical = sorted(executive.get("critical_components") or [])
    payload = {
        key: {
            "health": (components.get(key) or {}).get("health"),
            "reasons": (components.get(key) or {}).get("health_reasons") or [],
        }
        for key in critical
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_critical_alert_message(report):
    executive = report.get("executive", {})
    components = report.get("components", {})
    lines = [
        "🔴 SİSTEM KONTROL KRİTİK",
        f"UTC: {report.get('generated_at_utc') or utc_text()}",
        "Genel sağlık: RED",
    ]
    for key in executive.get("critical_components") or []:
        item = components.get(key) or {}
        reasons = item.get("health_reasons") or []
        reason_text = "; ".join(str(value) for value in reasons[:2]) or "Kritik teknik hata"
        lines.append(f"- {item.get('label') or key}: {reason_text}")
    lines.append("Emir veya strateji değişikliği yapılmadı; yalnız teknik uyarıdır.")
    return "\n".join(lines)


def load_alert_state(filename=ALERT_STATE_FILE):
    try:
        return load_json_file(filename)
    except Exception:
        return {}


def send_critical_telegram(message, token, chat_id):
    if not token or not chat_id:
        return False
    payload = urllib.parse.urlencode({
        "chat_id": str(chat_id),
        "text": str(message),
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=TELEGRAM_TIMEOUT_SECONDS) as response:
            return 200 <= int(response.getcode()) < 300
    except Exception as exc:
        print("Kritik Telegram gönderim hatası:", type(exc).__name__)
        return False


def maybe_send_critical_alert(
    report,
    state_file=ALERT_STATE_FILE,
    token=None,
    chat_id=None,
    current_ts=None,
    sender=send_critical_telegram,
):
    current_ts = int(current_ts if current_ts is not None else now_ts())
    fingerprint = critical_alert_fingerprint(report)
    if not fingerprint:
        return {"sent": False, "reason": "NOT_RED"}

    state = load_alert_state(state_file)
    last_fingerprint = str(state.get("last_fingerprint") or "")
    last_sent_at = int(safe_float(state.get("last_sent_at"), 0) or 0)
    if (
        fingerprint == last_fingerprint
        and current_ts - last_sent_at < CRITICAL_ALERT_COOLDOWN_SECONDS
    ):
        return {"sent": False, "reason": "COOLDOWN"}

    token = token if token is not None else os.getenv("TOKEN")
    chat_id = chat_id if chat_id is not None else os.getenv("CHAT_ID")
    if not token or not chat_id:
        return {"sent": False, "reason": "MISSING_CREDENTIALS"}

    message = build_critical_alert_message(report)
    if not sender(message, token, chat_id):
        return {"sent": False, "reason": "SEND_FAILED"}

    executive = report.get("executive", {})
    atomic_write_json(state_file, {
        "version": "SYSTEM_CONTROL_ALERT_STATE_V1",
        "last_fingerprint": fingerprint,
        "last_sent_at": current_ts,
        "last_sent_at_utc": utc_text(current_ts),
        "critical_components": executive.get("critical_components") or [],
    })
    return {"sent": True, "reason": "SENT"}


def run():
    report = build_report(".")
    atomic_write_json(REPORT_JSON, report)
    atomic_write_text(REPORT_MD, report_markdown(report))
    alert_result = maybe_send_critical_alert(report)
    ex = report["executive"]
    print("=== SISTEM KONTROL MERKEZI ===")
    print("Version:", VERSION)
    print("Mode:", MODE)
    print("Overall:", ex["overall_health"])
    print("Counts:", ex["health_counts"])
    print("Critical:", ex["critical_components"])
    print("Attention:", ex["attention_components"])
    print("Critical alert:", alert_result.get("reason"))
    for key, item in report["components"].items():
        print(icon(item["health"]), key, item["health"], "age_hours=", item["age_hours"], "perf=", item["performance"].get("decision_code"))

if __name__ == "__main__":
    run()
