"""Kripto Kontrol Merkezi V3.9 - performans zekâsı katmanı.

V3.8 üzerine salt-okunur analiz ekler:
- 7 / 14 / 30 günlük gerçek kapanış performansı,
- önceki dönemle Net R ve TP/SL eğilim karşılaştırması,
- Premium MTF TP1 sonrası TP2/TP3 devam analizi,
- Premium MTF stop kök neden ve stop-sonrası dönüş özeti,
- veri kaynağı güncelliği.

Sinyal üretimi, strategy/config, radarlar, Telegram ve emir akışı değişmez.
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
from collections import Counter
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_adminux_app as adminux
import dashboard_commercial_app as commercial
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
import dashboard_retention_app as retention
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_9_PERFORMANCE_INTELLIGENCE_2026_08_16"
DAY = 86_400
SYSTEM_ORDER = ("ALL", "PREMIUM", "SCALP", "PUMP_DUMP", "NEW_LISTING")
SYSTEM_LABELS = {
    "ALL": "Tüm Sistemler",
    "PREMIUM": "Premium MTF",
    "SCALP": "Scalp Radar",
    "PUMP_DUMP": "Pump / Dump",
    "NEW_LISTING": "Yeni Liste",
}

_LEDGER_CACHE_LOCK = threading.Lock()
_LEDGER_CACHE: dict[str, Any] = {
    "key": None,
    "loaded_at": 0.0,
    "data": {},
    "warning": None,
    "checked_at": 0,
}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _stamp(value: Any) -> int:
    number = _number(value)
    if number is None or number <= 0:
        return 0
    if number > 10_000_000_000:
        number /= 1000
    return int(number)


def _outcome(value: Any) -> str:
    raw = str(value or "").strip().upper().replace(" ", "_")
    aliases = {
        "STOP": "SL",
        "STOPLOSS": "SL",
        "STOP_LOSS": "SL",
        "BREAKEVEN": "BE",
        "BREAK_EVEN": "BE",
        "TP1_BE": "TP1_SONRASI_BE",
        "TP2_BE": "TP2_SONRASI_BE",
    }
    return aliases.get(raw, raw)


def _iter_trades(ledger: Any) -> list[dict[str, Any]]:
    if not isinstance(ledger, dict):
        return []
    trades = ledger.get("trades", {})
    if isinstance(trades, dict):
        return [row for row in trades.values() if isinstance(row, dict)]
    if isinstance(trades, list):
        return [row for row in trades if isinstance(row, dict)]
    return []


def _has_access(session: dict[str, Any] | None, info: dict[str, Any] | None) -> bool:
    if not session:
        return False
    role = str(session.get("role") or "").upper()
    plan = str((info or {}).get("plan") or "").upper()
    return role == commercial.ROLE_ADMIN or plan in {
        commercial.PLAN_PREMIUM,
        commercial.PLAN_ADMIN,
    }


def _period_row(data: dict[str, Any], key: str, system: str) -> dict[str, Any]:
    comparison = (data.get("period_comparisons") or {}).get(key) or {}
    for row in comparison.get("rows") or []:
        if str(row.get("system") or "") == system:
            return copy.deepcopy(row)
    return {
        "system": system,
        "label": SYSTEM_LABELS.get(system, system),
        "current": {},
        "previous": {},
        "sample_delta": 0,
        "net_r_delta": None,
    }


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = Counter(_outcome(row.get("outcome")) for row in rows)
    r_values = [float(row["r_result"]) for row in rows if _number(row.get("r_result")) is not None]
    tp = sum(count for key, count in outcomes.items() if key.startswith("TP") and "BE" not in key)
    sl = int(outcomes.get("SL", 0))
    be = sum(count for key, count in outcomes.items() if key == "BE" or "SONRASI_BE" in key)
    expired = int(outcomes.get("EXPIRED", 0))
    return {
        "sample": len(rows),
        "tp": tp,
        "sl": sl,
        "be": be,
        "expired": expired,
        "tp_rate": round(tp / len(rows) * 100, 1) if rows else None,
        "sl_rate": round(sl / len(rows) * 100, 1) if rows else None,
        "exact_r_sample": len(r_values),
        "net_r": round(sum(r_values), 4) if r_values else None,
        "average_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
    }


def _manual_period(data: dict[str, Any], system: str, days: int, now: int) -> dict[str, Any]:
    recent = [row for row in (data.get("recent_results") or []) if isinstance(row, dict)]
    current_start = now - days * DAY
    previous_start = now - days * 2 * DAY

    def matching(row: dict[str, Any], start: int, end: int) -> bool:
        if system != "ALL" and str(row.get("system") or "") != system:
            return False
        stamp = _stamp(row.get("closed_at"))
        return start <= stamp < end

    current_rows = [row for row in recent if matching(row, current_start, now + 1)]
    previous_rows = [row for row in recent if matching(row, previous_start, current_start)]
    current = _summarize_rows(current_rows)
    previous = _summarize_rows(previous_rows)
    current_net = current.get("net_r")
    previous_net = previous.get("net_r")

    earliest = min((_stamp(row.get("closed_at")) for row in recent if _stamp(row.get("closed_at"))), default=0)
    closed_total = int((data.get("summary") or {}).get("closed_total") or len(recent))
    capped = closed_total > len(recent)
    coverage_complete = not (capped and earliest and earliest > previous_start)

    return {
        "system": system,
        "label": SYSTEM_LABELS.get(system, system),
        "current": current,
        "previous": previous,
        "sample_delta": current["sample"] - previous["sample"],
        "net_r_delta": (
            round(float(current_net) - float(previous_net), 4)
            if current_net is not None and previous_net is not None
            else None
        ),
        "coverage_complete": coverage_complete,
    }


def _trend(period7: dict[str, Any], period30: dict[str, Any]) -> dict[str, str]:
    current7 = period7.get("current") or {}
    current30 = period30.get("current") or {}
    sample7 = int(current7.get("exact_r_sample") or 0)
    sample30 = int(current30.get("exact_r_sample") or 0)
    if sample7 < 3 or sample30 < 6:
        return {"code": "INSUFFICIENT", "label": "VERİ YETERSİZ", "tone": "neutral"}

    delta7 = _number(period7.get("net_r_delta"))
    delta30 = _number(period30.get("net_r_delta"))
    net7 = _number(current7.get("net_r"), 0.0) or 0.0
    net30 = _number(current30.get("net_r"), 0.0) or 0.0

    if delta7 is not None and delta7 >= 0.5 and (delta30 is None or delta30 >= 0):
        return {"code": "IMPROVING", "label": "TOPARLANIYOR ↑", "tone": "positive"}
    if delta7 is not None and delta7 <= -0.5 and (delta30 is None or delta30 <= 0):
        return {"code": "WEAKENING", "label": "ZAYIFLIYOR ↓", "tone": "negative"}
    if net7 > 0 and net30 > 0 and (delta7 is None or delta7 >= -0.25):
        return {"code": "POSITIVE", "label": "POZİTİF / DENGELİ", "tone": "positive"}
    if net7 < 0 and net30 < 0 and (delta7 is None or delta7 <= 0.25):
        return {"code": "NEGATIVE", "label": "NEGATİF / BASKILI", "tone": "negative"}
    return {"code": "MIXED", "label": "KARIŞIK ↔", "tone": "neutral"}


def build_window_intelligence(data: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    systems: list[dict[str, Any]] = []
    for system in SYSTEM_ORDER:
        p7 = _period_row(data, "7D", system)
        p14 = _manual_period(data, system, 14, now)
        p30 = _period_row(data, "30D", system)
        systems.append({
            "system": system,
            "label": SYSTEM_LABELS[system],
            "trend": _trend(p7, p30),
            "periods": {"7D": p7, "14D": p14, "30D": p30},
        })
    return {
        "generated_at": now,
        "systems": systems,
        "overall": systems[0] if systems else None,
        "note": "Trend etiketi geçmiş kapanış performansını özetler; gelecek sonuç tahmini değildir.",
    }


def _event_names(trade: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for event in trade.get("events") or []:
        if not isinstance(event, dict):
            continue
        value = event.get("event") or event.get("result") or event.get("type")
        text = _outcome(value)
        if text:
            names.add(text)
    return names


def analyze_tp_continuation(ledger: dict[str, Any], *, days: int = 30, now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    cutoff = now - days * DAY
    eligible: list[dict[str, Any]] = []
    for trade in _iter_trades(ledger):
        closed_at = _stamp(trade.get("closed_at"))
        if closed_at and closed_at < cutoff:
            continue
        events = _event_names(trade)
        final = _outcome(trade.get("final_result") or trade.get("result"))
        tp1 = bool(trade.get("tp1_hit")) or bool(events & {"TP1", "TP2", "TP3"}) or final in {"TP1_SONRASI_BE", "TP2_SONRASI_BE", "TP3"}
        if not tp1:
            continue
        tp2 = bool(trade.get("tp2_hit")) or bool(events & {"TP2", "TP3"}) or final in {"TP2_SONRASI_BE", "TP3"}
        tp3 = bool(trade.get("tp3_hit")) or "TP3" in events or final == "TP3"
        eligible.append({"tp2": tp2, "tp3": tp3, "final": final})

    sample = len(eligible)
    tp2_count = sum(1 for row in eligible if row["tp2"])
    tp3_count = sum(1 for row in eligible if row["tp3"])
    be_count = sum(1 for row in eligible if row["final"] in {"BE", "TP1_SONRASI_BE", "TP2_SONRASI_BE"})
    return {
        "days": days,
        "tp1_sample": sample,
        "tp2_after_tp1": tp2_count,
        "tp3_after_tp1": tp3_count,
        "be_after_tp1": be_count,
        "tp2_continue_rate": round(tp2_count / sample * 100, 1) if sample else None,
        "tp3_continue_rate": round(tp3_count / sample * 100, 1) if sample else None,
        "be_rate": round(be_count / sample * 100, 1) if sample else None,
    }


def analyze_stop_diagnosis(ledger: dict[str, Any], *, days: int = 30, now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    cutoff = now - days * DAY
    categories: Counter[str] = Counter()
    returned_levels: Counter[str] = Counter()
    total = diagnosed = provisional = returned = no_return = tracking = 0

    for trade in _iter_trades(ledger):
        if _outcome(trade.get("final_result") or trade.get("result")) != "SL":
            continue
        closed_at = _stamp(trade.get("closed_at"))
        if closed_at and closed_at < cutoff:
            continue
        total += 1
        root = trade.get("stop_root_cause") if isinstance(trade.get("stop_root_cause"), dict) else {}
        diagnosis = trade.get("diagnosis") if isinstance(trade.get("diagnosis"), dict) else {}
        label = str(root.get("label") or root.get("primary") or diagnosis.get("primary") or "Veri yetersiz")
        if root or diagnosis:
            diagnosed += 1
        if bool(root.get("provisional")) or bool(diagnosis.get("provisional")):
            provisional += 1
        categories[label] += 1

        follow = trade.get("post_stop_follow") if isinstance(trade.get("post_stop_follow"), dict) else {}
        status = str(follow.get("status") or "").upper()
        if status == "RETURNED_TO_TARGET":
            returned += 1
            returned_levels[str(follow.get("returned_level") or "TP1").upper()] += 1
        elif status == "NO_TP1_RETURN":
            no_return += 1
        else:
            tracking += 1

    resolved = returned + no_return
    return {
        "days": days,
        "sl_total": total,
        "diagnosed": diagnosed,
        "provisional": provisional,
        "returned_to_target": returned,
        "no_tp1_return": no_return,
        "tracking": tracking,
        "resolved_follow": resolved,
        "return_rate": round(returned / resolved * 100, 1) if resolved else None,
        "categories": [{"label": label, "count": count} for label, count in categories.most_common(8)],
        "returned_levels": [{"level": level, "count": count} for level, count in returned_levels.most_common()],
    }


def _load_premium_ledger(config: PanelConfig, *, cache_seconds: int = 30) -> tuple[dict[str, Any], str | None, int]:
    cache_key = f"{config.repository}@{config.ref}:{config.root}"
    now_mono = time.monotonic()
    with _LEDGER_CACHE_LOCK:
        if _LEDGER_CACHE.get("key") == cache_key and now_mono - float(_LEDGER_CACHE.get("loaded_at") or 0) < cache_seconds:
            return copy.deepcopy(_LEDGER_CACHE.get("data") or {}), _LEDGER_CACHE.get("warning"), int(_LEDGER_CACHE.get("checked_at") or 0)

    warning: str | None = None
    data: dict[str, Any] = {}
    try:
        if config.github_token:
            path = urllib.parse.quote("trade_ledger.json", safe="/")
            ref = urllib.parse.quote(config.ref, safe="")
            request = urllib.request.Request(
                f"https://api.github.com/repos/{config.repository}/contents/{path}?ref={ref}",
                headers={
                    "Accept": "application/vnd.github.raw+json",
                    "Authorization": f"Bearer {config.github_token}",
                    "User-Agent": "Kripto-Panel-Performance/3.9",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                loaded = json.loads(response.read().decode("utf-8"))
        else:
            with (config.root / "trade_ledger.json").open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        if isinstance(loaded, dict):
            data = loaded
        else:
            warning = "Premium ledger biçimi geçersiz."
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
        warning = f"Premium ledger okunamadı ({type(exc).__name__})."
        with _LEDGER_CACHE_LOCK:
            if _LEDGER_CACHE.get("key") == cache_key and _LEDGER_CACHE.get("data"):
                data = copy.deepcopy(_LEDGER_CACHE["data"])
                warning += " Son geçerli veri kullanıldı."

    checked_at = int(time.time())
    with _LEDGER_CACHE_LOCK:
        _LEDGER_CACHE.update({"key": cache_key, "loaded_at": now_mono, "data": copy.deepcopy(data), "warning": warning, "checked_at": checked_at})
    return data, warning, checked_at


def build_performance_intelligence(data: dict[str, Any], ledger: dict[str, Any], *, ledger_warning: str | None = None, now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    windows = build_window_intelligence(data, now=now)
    sources = []
    for row in data.get("sources") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("label") or "") not in {"Premium performans", "Scalp performans", "Pump/Dump performans", "System Control raporu"}:
            continue
        sources.append({
            "label": str(row.get("label") or "Veri kaynağı"),
            "status": str(row.get("status") or "UNKNOWN"),
            "age_hours": _number(row.get("age_hours")),
            "critical": bool(row.get("critical")),
        })
    return {
        "version": VERSION,
        "generated_at": now,
        "windows": windows,
        "tp_continuation": analyze_tp_continuation(ledger, days=30, now=now),
        "stop_diagnosis": analyze_stop_diagnosis(ledger, days=30, now=now),
        "sources": sources,
        "ledger_warning": ledger_warning,
        "safety": {
            "read_only": True,
            "signal_engine": "unchanged",
            "telegram": "unchanged",
            "orders": False,
        },
    }


def _fmt_r(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "—"
    return f"{number:+.2f}R"


def _fmt_pct(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"%{number:.1f}"


def _tone_class(tone: str) -> str:
    return "good" if tone == "positive" else "bad" if tone == "negative" else "neutral"


def render_performance_page(payload: dict[str, Any]) -> str:
    windows = payload["windows"]
    overall = windows.get("overall") or {}
    overall_trend = overall.get("trend") or {}
    overall_periods = overall.get("periods") or {}

    top_cards = []
    for key, label in (("7D", "Son 7 gün"), ("14D", "Son 14 gün"), ("30D", "Son 30 gün")):
        period = overall_periods.get(key) or {}
        current = period.get("current") or {}
        partial = key == "14D" and not bool(period.get("coverage_complete", True))
        top_cards.append(
            f'<div class="kpi"><small>{label}{" · KISMİ" if partial else ""}</small><b class="r">{html.escape(_fmt_r(current.get("net_r")))}</b><span>{int(current.get("sample") or 0)} kapanış · TP {_fmt_pct(current.get("tp_rate"))} · SL {_fmt_pct(current.get("sl_rate"))}</span></div>'
        )

    system_rows = []
    for item in windows.get("systems") or []:
        periods = item.get("periods") or {}
        p7 = (periods.get("7D") or {}).get("current") or {}
        p14 = (periods.get("14D") or {}).get("current") or {}
        p30 = (periods.get("30D") or {}).get("current") or {}
        trend = item.get("trend") or {}
        system_rows.append(
            f'<tr><td><b>{html.escape(str(item.get("label") or ""))}</b></td><td>{_fmt_r(p7.get("net_r"))}<small>{int(p7.get("sample") or 0)} işlem</small></td><td>{_fmt_r(p14.get("net_r"))}<small>{int(p14.get("sample") or 0)} işlem</small></td><td>{_fmt_r(p30.get("net_r"))}<small>{int(p30.get("sample") or 0)} işlem</small></td><td><span class="trend {_tone_class(str(trend.get("tone") or "neutral"))}">{html.escape(str(trend.get("label") or "—"))}</span></td></tr>'
        )

    tp = payload.get("tp_continuation") or {}
    stop = payload.get("stop_diagnosis") or {}
    stop_rows = "".join(
        f'<div class="reason"><span>{html.escape(str(row.get("label") or ""))}</span><b>{int(row.get("count") or 0)}</b></div>'
        for row in stop.get("categories") or []
    ) or '<div class="empty">Henüz sınıflandırılmış stop örneği yok.</div>'

    source_rows = "".join(
        f'<div class="source"><span>{html.escape(str(row.get("label") or ""))}</span><b class="{str(row.get("status") or "").lower()}">{html.escape(str(row.get("status") or "UNKNOWN"))}</b><small>{"—" if row.get("age_hours") is None else f"{float(row.get("age_hours")):.2f} saat"}</small></div>'
        for row in payload.get("sources") or []
    ) or '<div class="empty">Kaynak güncelliği bilgisi yok.</div>'

    warning = payload.get("ledger_warning")
    warning_html = f'<div class="warning">{html.escape(str(warning))}</div>' if warning else ""
    trend_class = _tone_class(str(overall_trend.get("tone") or "neutral"))

    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>Performans Zekâsı</title><style>
:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--red:#ff627d;--amber:#ffbd59;--blue:#60a5fa}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,rgba(44,230,191,.07),transparent 28%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.shell{{width:min(1180px,calc(100% - 20px));margin:auto;padding:22px 0 60px}}.top{{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;flex-wrap:wrap}}h1{{margin:0;font-size:30px}}.sub{{color:var(--muted);margin:4px 0 0}}.btn{{display:inline-block;border:1px solid var(--line);border-radius:10px;padding:8px 11px;background:#091820;font-size:9px;font-weight:900}}.hero{{margin:16px 0;border:1px solid rgba(44,230,191,.25);border-radius:18px;background:linear-gradient(135deg,rgba(44,230,191,.07),transparent 45%),var(--panel);padding:17px;display:flex;justify-content:space-between;gap:15px;align-items:center}}.hero small{{color:var(--muted);font-size:9px}}.hero h2{{margin:3px 0;font-size:24px}}.hero p{{margin:0;color:var(--muted);max-width:730px}}.trend{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:9px;font-weight:950}}.trend.good{{color:var(--teal);border-color:rgba(44,230,191,.3)}}.trend.bad{{color:var(--red);border-color:rgba(255,98,125,.35)}}.trend.neutral{{color:var(--amber)}}.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}}.kpi,.card{{border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:13px}}.kpi small,.card small{{color:var(--muted);font-size:8px}}.kpi b{{display:block;font-size:23px;margin:2px 0}}.kpi span{{color:var(--muted);font-size:9px}}.section{{margin-top:16px}}.section h2{{margin:0 0 8px;font-size:17px}}table{{width:100%;border-collapse:collapse;border:1px solid var(--line);background:var(--panel);border-radius:14px;overflow:hidden}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left}}th{{font-size:8px;color:var(--muted)}}td small{{display:block;color:var(--muted);font-size:8px}}.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.big{{font-size:25px;font-weight:950}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:9px}}.stat{{border:1px solid var(--line);border-radius:10px;padding:9px}}.stat b,.stat span{{display:block}}.stat span{{color:var(--muted);font-size:8px}}.reason,.source{{display:grid;grid-template-columns:1fr auto auto;gap:9px;align-items:center;padding:8px 0;border-bottom:1px solid rgba(27,57,67,.65)}}.source small{{color:var(--muted);min-width:70px;text-align:right}}.fresh{{color:var(--teal)}}.stale,.error{{color:var(--red)}}.unknown{{color:var(--amber)}}.warning{{margin:10px 0;border:1px solid rgba(255,189,89,.3);border-radius:11px;background:rgba(255,189,89,.05);padding:10px;color:var(--amber)}}.note{{margin-top:14px;color:var(--muted);font-size:9px}}.empty{{color:var(--muted);padding:8px 0}}@media(max-width:760px){{.hero{{align-items:flex-start;flex-direction:column}}.kpis,.grid2{{grid-template-columns:1fr}}table{{display:block;overflow-x:auto}}.stats{{grid-template-columns:1fr 1fr}}}}
</style></head><body><div class="shell"><div class="top"><div><h1>Performans Zekâsı</h1><p class="sub">Gerçek kapanış kayıtlarıyla sistemin iyiye mi kötüye mi gittiğini izle.</p></div><div><a class="btn" href="/">← Panele Dön</a></div></div>{warning_html}<section class="hero"><div><small>GENEL EĞİLİM</small><h2>Geçmiş performansın yönü</h2><p>{html.escape(str(windows.get("note") or ""))}</p></div><span class="trend {trend_class}">{html.escape(str(overall_trend.get("label") or "—"))}</span></section><div class="kpis">{''.join(top_cards)}</div><section class="section"><h2>Sistem karşılaştırması</h2><table><thead><tr><th>Sistem</th><th>7 Gün</th><th>14 Gün</th><th>30 Gün</th><th>Eğilim</th></tr></thead><tbody>{''.join(system_rows)}</tbody></table></section><section class="section grid2"><div class="card"><small>PREMIUM MTF · SON 30 GÜN</small><h2>TP1 sonrası devam</h2><div class="big">{int(tp.get("tp1_sample") or 0)} TP1 örneği</div><div class="stats"><div class="stat"><span>TP2'ye devam</span><b>{_fmt_pct(tp.get("tp2_continue_rate"))}</b></div><div class="stat"><span>TP3'e devam</span><b>{_fmt_pct(tp.get("tp3_continue_rate"))}</b></div><div class="stat"><span>BE ile kapanan</span><b>{_fmt_pct(tp.get("be_rate"))}</b></div></div><p class="note">Bu alan TP1 sonrası kâr yönetimini ileride veriye göre değerlendirmek içindir; canlı TP/SL kuralını otomatik değiştirmez.</p></div><div class="card"><small>PREMIUM MTF · SON 30 GÜN</small><h2>Stop sonrası davranış</h2><div class="big">{int(stop.get("sl_total") or 0)} stop</div><div class="stats"><div class="stat"><span>Hedefe geri döndü</span><b>{_fmt_pct(stop.get("return_rate"))}</b></div><div class="stat"><span>TP1'e dönmedi</span><b>{int(stop.get("no_tp1_return") or 0)}</b></div><div class="stat"><span>Takip sürüyor</span><b>{int(stop.get("tracking") or 0)}</b></div></div><p class="note">Stop sonrası geri dönüş yüksekse dar stop / erken giriş ihtimali güçlenir; düşükse yön veya kurulum kalitesi daha fazla incelenir.</p></div></section><section class="section grid2"><div class="card"><small>STOP KÖK NEDENLERİ</small><h2>Teşhis dağılımı</h2>{stop_rows}</div><div class="card"><small>VERİ GÜNCELLİĞİ</small><h2>Kaynak sağlığı</h2>{source_rows}</div></section><div class="note">Salt okunur analizdir. Emir açmaz, Telegram göndermez, strateji/config/TP/SL değerlerini değiştirmez. Sayfa 60 saniyede bir yenilenir.</div></div></body></html>'''


def enhance_home_shortcut(body: str, payload: dict[str, Any]) -> str:
    if 'id="v39PerformanceShortcut"' in body:
        return body
    overall = (payload.get("overall") or {})
    trend = overall.get("trend") or {}
    tone = _tone_class(str(trend.get("tone") or "neutral"))
    card = f'''<a id="v39PerformanceShortcut" href="/performance-intelligence" style="display:block;width:min(1180px,calc(100% - 24px));margin:10px auto;border:1px solid rgba(44,230,191,.25);border-radius:14px;background:#0a1921;padding:12px 14px;text-decoration:none;color:#edf8f6"><div style="display:flex;justify-content:space-between;align-items:center;gap:12px"><div><b style="display:block;font-size:12px">◈ Performans Zekâsı</b><span style="display:block;color:#82a09d;font-size:9px;margin-top:2px">7 / 14 / 30 gün · Net R · TP/SL · stop teşhisi · TP1 devamı</span></div><span class="trend {tone}" style="white-space:nowrap">{html.escape(str(trend.get("label") or "VERİ YETERSİZ"))}</span></div></a>'''
    if "</body>" in body:
        return body.replace("</body>", card + "</body>", 1)
    return body + card


def make_v39_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    BaseHandler = retention.make_v38_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V39Handler(BaseHandler):
        server_version = "KriptoPanel/3.9"

        def _send(self, status: int, body: str | bytes, content_type: str, *, cookies: list[str] | None = None, nonce: str | None = None) -> None:
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    if session:
                        info = self._plan_info(session) or {}
                        if _has_access(session, info):
                            try:
                                body = enhance_home_shortcut(body, build_window_intelligence(service.get_data()))
                            except Exception:
                                pass
            super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def _performance_payload(self) -> dict[str, Any]:
            data = service.get_data()
            ledger, warning, checked_at = _load_premium_ledger(config)
            payload = build_performance_intelligence(data, ledger, ledger_warning=warning)
            payload["ledger_checked_at"] = checked_at
            return payload

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION, "performance_intelligence": True, "windows": [7, 14, 30], "tp_continuation": True, "stop_diagnosis": True, "signal_engine": "unchanged", "telegram": "unchanged"})
                return
            if path in {"/performance", "/performance-intelligence"}:
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                info = self._plan_info(session) or {}
                if not _has_access(session, info):
                    self._redirect("/premium")
                    return
                try:
                    body = render_performance_page(self._performance_payload())
                except Exception as exc:
                    body = render_performance_page(build_performance_intelligence(service.get_data(), {}, ledger_warning=f"Performans verisi hazırlanamadı ({type(exc).__name__})."))
                body = adminux.enhance_standalone(body, session, is_admin=str(session.get("role") or "").upper() == commercial.ROLE_ADMIN)
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            if path == "/api/performance/intelligence":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "Oturum gerekli."})
                    return
                info = self._plan_info(session) or {}
                if not _has_access(session, info):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "Premium erişim gerekli."})
                    return
                self._json(HTTPStatus.OK, self._performance_payload())
                return
            return super().do_GET()

    return V39Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.9 Performans Zekâsı.")
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
    handler = make_v39_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} windows=7,14,30 performance_intelligence=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
