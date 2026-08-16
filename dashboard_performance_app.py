"""Kripto Kontrol Merkezi V3.9 - Performans Zekâsı.

Salt-okunur panel katmanıdır. Sinyal üretmez, emir açmaz, Telegram göndermez
ve strategy/config/TP/SL davranışını değiştirmez.
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

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"key": None, "loaded_at": 0.0, "data": {}, "warning": None, "checked_at": 0}


def _number(value: Any, default: float | None = None) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _stamp(value: Any) -> int:
    number = _number(value)
    if number is None or number <= 0:
        return 0
    if number > 10_000_000_000:
        number /= 1000
    return int(number)


def _outcome(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    return {
        "STOP": "SL", "STOPLOSS": "SL", "STOP_LOSS": "SL",
        "BREAKEVEN": "BE", "BREAK_EVEN": "BE",
        "TP1_BE": "TP1_SONRASI_BE", "TP2_BE": "TP2_SONRASI_BE",
    }.get(text, text)


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
    return role == commercial.ROLE_ADMIN or plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}


def _period_row(data: dict[str, Any], key: str, system: str) -> dict[str, Any]:
    comparison = (data.get("period_comparisons") or {}).get(key) or {}
    for row in comparison.get("rows") or []:
        if str(row.get("system") or "") == system:
            return copy.deepcopy(row)
    return {"system": system, "label": SYSTEM_LABELS.get(system, system), "current": {}, "previous": {}, "sample_delta": 0, "net_r_delta": None}


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = Counter(_outcome(row.get("outcome")) for row in rows)
    r_values = [float(row["r_result"]) for row in rows if _number(row.get("r_result")) is not None]
    tp = sum(count for key, count in outcomes.items() if key.startswith("TP") and "BE" not in key)
    sl = int(outcomes.get("SL", 0))
    be = sum(count for key, count in outcomes.items() if key == "BE" or "SONRASI_BE" in key)
    return {
        "sample": len(rows), "tp": tp, "sl": sl, "be": be,
        "expired": int(outcomes.get("EXPIRED", 0)),
        "tp_rate": round(tp / len(rows) * 100, 1) if rows else None,
        "sl_rate": round(sl / len(rows) * 100, 1) if rows else None,
        "exact_r_sample": len(r_values),
        "net_r": round(sum(r_values), 4) if r_values else None,
        "average_r": round(sum(r_values) / len(r_values), 4) if r_values else None,
    }


def _manual_period(data: dict[str, Any], system: str, days: int, now: int) -> dict[str, Any]:
    recent = [row for row in (data.get("recent_results") or []) if isinstance(row, dict)]
    current_start, previous_start = now - days * DAY, now - days * 2 * DAY

    def select(start: int, end: int) -> list[dict[str, Any]]:
        return [
            row for row in recent
            if (system == "ALL" or str(row.get("system") or "") == system)
            and start <= _stamp(row.get("closed_at")) < end
        ]

    current, previous = _summarize_rows(select(current_start, now + 1)), _summarize_rows(select(previous_start, current_start))
    current_net, previous_net = current.get("net_r"), previous.get("net_r")
    earliest = min((_stamp(row.get("closed_at")) for row in recent if _stamp(row.get("closed_at"))), default=0)
    closed_total = int((data.get("summary") or {}).get("closed_total") or len(recent))
    coverage_complete = not (closed_total > len(recent) and earliest and earliest > previous_start)
    return {
        "system": system, "label": SYSTEM_LABELS.get(system, system),
        "current": current, "previous": previous,
        "sample_delta": current["sample"] - previous["sample"],
        "net_r_delta": round(float(current_net) - float(previous_net), 4) if current_net is not None and previous_net is not None else None,
        "coverage_complete": coverage_complete,
    }


def _trend(period7: dict[str, Any], period30: dict[str, Any]) -> dict[str, str]:
    c7, c30 = period7.get("current") or {}, period30.get("current") or {}
    if int(c7.get("exact_r_sample") or 0) < 3 or int(c30.get("exact_r_sample") or 0) < 6:
        return {"code": "INSUFFICIENT", "label": "VERİ YETERSİZ", "tone": "neutral"}
    d7, d30 = _number(period7.get("net_r_delta")), _number(period30.get("net_r_delta"))
    n7, n30 = _number(c7.get("net_r"), 0.0) or 0.0, _number(c30.get("net_r"), 0.0) or 0.0
    if d7 is not None and d7 >= 0.5 and (d30 is None or d30 >= 0):
        return {"code": "IMPROVING", "label": "TOPARLANIYOR ↑", "tone": "positive"}
    if d7 is not None and d7 <= -0.5 and (d30 is None or d30 <= 0):
        return {"code": "WEAKENING", "label": "ZAYIFLIYOR ↓", "tone": "negative"}
    if n7 > 0 and n30 > 0 and (d7 is None or d7 >= -0.25):
        return {"code": "POSITIVE", "label": "POZİTİF / DENGELİ", "tone": "positive"}
    if n7 < 0 and n30 < 0 and (d7 is None or d7 <= 0.25):
        return {"code": "NEGATIVE", "label": "NEGATİF / BASKILI", "tone": "negative"}
    return {"code": "MIXED", "label": "KARIŞIK ↔", "tone": "neutral"}


def build_window_intelligence(data: dict[str, Any], *, now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    systems = []
    for system in SYSTEM_ORDER:
        p7, p14, p30 = _period_row(data, "7D", system), _manual_period(data, system, 14, now), _period_row(data, "30D", system)
        systems.append({"system": system, "label": SYSTEM_LABELS[system], "trend": _trend(p7, p30), "periods": {"7D": p7, "14D": p14, "30D": p30}})
    return {"generated_at": now, "systems": systems, "overall": systems[0] if systems else None, "note": "Trend etiketi geçmiş kapanış performansını özetler; gelecek sonuç tahmini değildir."}


def _event_names(trade: dict[str, Any]) -> set[str]:
    result: set[str] = set()
    for event in trade.get("events") or []:
        if isinstance(event, dict):
            value = _outcome(event.get("event") or event.get("result") or event.get("type"))
            if value:
                result.add(value)
    return result


def analyze_tp_continuation(ledger: dict[str, Any], *, days: int = 30, now: int | None = None) -> dict[str, Any]:
    now, cutoff = int(now or time.time()), int(now or time.time()) - days * DAY
    rows = []
    for trade in _iter_trades(ledger):
        closed_at = _stamp(trade.get("closed_at"))
        if closed_at and closed_at < cutoff:
            continue
        events, final = _event_names(trade), _outcome(trade.get("final_result") or trade.get("result"))
        tp1 = bool(trade.get("tp1_hit")) or bool(events & {"TP1", "TP2", "TP3"}) or final in {"TP1_SONRASI_BE", "TP2_SONRASI_BE", "TP3"}
        if not tp1:
            continue
        rows.append({
            "tp2": bool(trade.get("tp2_hit")) or bool(events & {"TP2", "TP3"}) or final in {"TP2_SONRASI_BE", "TP3"},
            "tp3": bool(trade.get("tp3_hit")) or "TP3" in events or final == "TP3",
            "final": final,
        })
    sample = len(rows)
    tp2, tp3 = sum(1 for row in rows if row["tp2"]), sum(1 for row in rows if row["tp3"])
    be = sum(1 for row in rows if row["final"] in {"BE", "TP1_SONRASI_BE", "TP2_SONRASI_BE"})
    return {"days": days, "tp1_sample": sample, "tp2_after_tp1": tp2, "tp3_after_tp1": tp3, "be_after_tp1": be,
            "tp2_continue_rate": round(tp2 / sample * 100, 1) if sample else None,
            "tp3_continue_rate": round(tp3 / sample * 100, 1) if sample else None,
            "be_rate": round(be / sample * 100, 1) if sample else None}


def analyze_stop_diagnosis(ledger: dict[str, Any], *, days: int = 30, now: int | None = None) -> dict[str, Any]:
    now, cutoff = int(now or time.time()), int(now or time.time()) - days * DAY
    categories, returned_levels = Counter(), Counter()
    total = diagnosed = provisional = returned = no_return = tracking = 0
    for trade in _iter_trades(ledger):
        if _outcome(trade.get("final_result") or trade.get("result")) != "SL":
            continue
        closed_at = _stamp(trade.get("closed_at"))
        if closed_at and closed_at < cutoff:
            continue
        total += 1
        root = trade.get("stop_root_cause") if isinstance(trade.get("stop_root_cause"), dict) else {}
        diag = trade.get("diagnosis") if isinstance(trade.get("diagnosis"), dict) else {}
        label = str(root.get("label") or root.get("primary") or diag.get("primary") or "Veri yetersiz")
        diagnosed += int(bool(root or diag))
        provisional += int(bool(root.get("provisional")) or bool(diag.get("provisional")))
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
    return {"days": days, "sl_total": total, "diagnosed": diagnosed, "provisional": provisional,
            "returned_to_target": returned, "no_tp1_return": no_return, "tracking": tracking, "resolved_follow": resolved,
            "return_rate": round(returned / resolved * 100, 1) if resolved else None,
            "categories": [{"label": label, "count": count} for label, count in categories.most_common(8)],
            "returned_levels": [{"level": level, "count": count} for level, count in returned_levels.most_common()]}


def _load_premium_ledger(config: PanelConfig, *, cache_seconds: int = 30) -> tuple[dict[str, Any], str | None, int]:
    key, mono = f"{config.repository}@{config.ref}:{config.root}", time.monotonic()
    with _CACHE_LOCK:
        if _CACHE.get("key") == key and mono - float(_CACHE.get("loaded_at") or 0) < cache_seconds:
            return copy.deepcopy(_CACHE.get("data") or {}), _CACHE.get("warning"), int(_CACHE.get("checked_at") or 0)
    data: dict[str, Any] = {}
    warning: str | None = None
    try:
        if config.github_token:
            path, ref = urllib.parse.quote("trade_ledger.json", safe="/"), urllib.parse.quote(config.ref, safe="")
            request = urllib.request.Request(
                f"https://api.github.com/repos/{config.repository}/contents/{path}?ref={ref}",
                headers={"Accept": "application/vnd.github.raw+json", "Authorization": f"Bearer {config.github_token}", "User-Agent": "Kripto-Panel-Performance/3.9", "X-GitHub-Api-Version": "2022-11-28"},
            )
            with urllib.request.urlopen(request, timeout=25) as response:
                loaded = json.loads(response.read().decode("utf-8"))
        else:
            with (config.root / "trade_ledger.json").open("r", encoding="utf-8") as handle:
                loaded = json.load(handle)
        data = loaded if isinstance(loaded, dict) else {}
        if not data:
            warning = "Premium ledger boş veya biçimi geçersiz."
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
        warning = f"Premium ledger okunamadı ({type(exc).__name__})."
        with _CACHE_LOCK:
            if _CACHE.get("key") == key and _CACHE.get("data"):
                data = copy.deepcopy(_CACHE["data"])
                warning += " Son geçerli veri kullanıldı."
    checked = int(time.time())
    with _CACHE_LOCK:
        _CACHE.update({"key": key, "loaded_at": mono, "data": copy.deepcopy(data), "warning": warning, "checked_at": checked})
    return data, warning, checked


def build_performance_intelligence(data: dict[str, Any], ledger: dict[str, Any], *, ledger_warning: str | None = None, now: int | None = None) -> dict[str, Any]:
    now = int(now or time.time())
    sources = []
    allowed = {"Premium performans", "Scalp performans", "Pump/Dump performans", "System Control raporu"}
    for row in data.get("sources") or []:
        if isinstance(row, dict) and str(row.get("label") or "") in allowed:
            sources.append({"label": str(row.get("label")), "status": str(row.get("status") or "UNKNOWN"), "age_hours": _number(row.get("age_hours")), "critical": bool(row.get("critical"))})
    return {"version": VERSION, "generated_at": now, "windows": build_window_intelligence(data, now=now),
            "tp_continuation": analyze_tp_continuation(ledger, now=now), "stop_diagnosis": analyze_stop_diagnosis(ledger, now=now),
            "sources": sources, "ledger_warning": ledger_warning,
            "safety": {"read_only": True, "signal_engine": "unchanged", "telegram": "unchanged", "orders": False}}


def _fmt_r(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"{number:+.2f}R"


def _fmt_pct(value: Any) -> str:
    number = _number(value)
    return "—" if number is None else f"%{number:.1f}"


def _tone(value: str) -> str:
    return "good" if value == "positive" else "bad" if value == "negative" else "neutral"


def render_performance_page(payload: dict[str, Any]) -> str:
    windows = payload.get("windows") or {}
    overall = windows.get("overall") or {}
    trend = overall.get("trend") or {}
    periods = overall.get("periods") or {}
    cards = []
    for key, label in (("7D", "Son 7 gün"), ("14D", "Son 14 gün"), ("30D", "Son 30 gün")):
        period = periods.get(key) or {}
        current = period.get("current") or {}
        partial = key == "14D" and not bool(period.get("coverage_complete", True))
        cards.append(f'<div class="kpi"><small>{label}{" · KISMİ" if partial else ""}</small><b>{_fmt_r(current.get("net_r"))}</b><span>{int(current.get("sample") or 0)} kapanış · TP {_fmt_pct(current.get("tp_rate"))} · SL {_fmt_pct(current.get("sl_rate"))}</span></div>')

    rows = []
    for item in windows.get("systems") or []:
        p = item.get("periods") or {}
        p7, p14, p30 = (p.get("7D") or {}).get("current") or {}, (p.get("14D") or {}).get("current") or {}, (p.get("30D") or {}).get("current") or {}
        t = item.get("trend") or {}
        rows.append(f'<tr><td><b>{html.escape(str(item.get("label") or ""))}</b></td><td>{_fmt_r(p7.get("net_r"))}<small>{int(p7.get("sample") or 0)} işlem</small></td><td>{_fmt_r(p14.get("net_r"))}<small>{int(p14.get("sample") or 0)} işlem</small></td><td>{_fmt_r(p30.get("net_r"))}<small>{int(p30.get("sample") or 0)} işlem</small></td><td><span class="pill {_tone(str(t.get("tone") or "neutral"))}">{html.escape(str(t.get("label") or "—"))}</span></td></tr>')

    tp, stop = payload.get("tp_continuation") or {}, payload.get("stop_diagnosis") or {}
    reasons = "".join(f'<div class="line"><span>{html.escape(str(row.get("label") or ""))}</span><b>{int(row.get("count") or 0)}</b></div>' for row in stop.get("categories") or []) or '<div class="empty">Henüz sınıflandırılmış stop yok.</div>'
    source_parts = []
    for row in payload.get("sources") or []:
        age = "—" if row.get("age_hours") is None else f'{float(row.get("age_hours")):.2f} saat'
        status = str(row.get("status") or "UNKNOWN")
        source_parts.append(f'<div class="line"><span>{html.escape(str(row.get("label") or ""))}</span><b class="{status.lower()}">{html.escape(status)}</b><small>{age}</small></div>')
    sources = "".join(source_parts) or '<div class="empty">Kaynak güncelliği bilgisi yok.</div>'
    warning = payload.get("ledger_warning")
    warning_html = f'<div class="warning">{html.escape(str(warning))}</div>' if warning else ""

    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="60"><title>Performans Zekâsı</title><style>
:root{{--bg:#061016;--p:#0b1b23;--l:#1b3943;--t:#edf8f6;--m:#82a09d;--g:#2ce6bf;--r:#ff627d;--a:#ffbd59}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--t);font:13px/1.5 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.shell{{width:min(1180px,calc(100% - 20px));margin:auto;padding:22px 0 60px}}.top,.hero{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}h1{{margin:0;font-size:30px}}h2{{margin:0 0 8px}}.muted,.note{{color:var(--m)}}.btn,.pill{{border:1px solid var(--l);border-radius:999px;padding:7px 10px;font-size:9px;font-weight:900}}.hero,.card,.kpi,table{{border:1px solid var(--l);background:var(--p)}}.hero{{margin:16px 0;border-radius:16px;padding:15px}}.good{{color:var(--g)!important}}.bad{{color:var(--r)!important}}.neutral{{color:var(--a)!important}}.kpis{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}}.kpi,.card{{border-radius:13px;padding:13px}}.kpi small,.kpi span,.card small,.line small,td small{{display:block;color:var(--m);font-size:8px}}.kpi b{{display:block;font-size:23px}}.section{{margin-top:16px}}table{{width:100%;border-collapse:collapse;border-radius:13px;overflow:hidden}}th,td{{padding:10px;border-bottom:1px solid var(--l);text-align:left}}th{{font-size:8px;color:var(--m)}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px}}.stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}}.stat{{border:1px solid var(--l);border-radius:9px;padding:9px}}.stat span,.stat b{{display:block}}.stat span{{color:var(--m);font-size:8px}}.line{{display:grid;grid-template-columns:1fr auto auto;gap:8px;padding:8px 0;border-bottom:1px solid var(--l);align-items:center}}.fresh{{color:var(--g)}}.stale,.error{{color:var(--r)}}.unknown{{color:var(--a)}}.warning{{border:1px solid rgba(255,189,89,.35);padding:10px;border-radius:10px;color:var(--a);margin:12px 0}}.empty{{color:var(--m);padding:8px 0}}@media(max-width:760px){{.kpis,.grid{{grid-template-columns:1fr}}table{{display:block;overflow-x:auto}}.stats{{grid-template-columns:1fr 1fr}}}}
</style></head><body><div class="shell"><div class="top"><div><h1>Performans Zekâsı</h1><div class="muted">Gerçek kapanışlarla 7 / 14 / 30 günlük yönü izle.</div></div><a class="btn" href="/">← Panele Dön</a></div>{warning_html}<div class="hero"><div><small>GENEL EĞİLİM</small><h2>İyiye mi, kötüye mi?</h2><div class="muted">{html.escape(str(windows.get("note") or ""))}</div></div><span class="pill {_tone(str(trend.get("tone") or "neutral"))}">{html.escape(str(trend.get("label") or "VERİ YETERSİZ"))}</span></div><div class="kpis">{''.join(cards)}</div><section class="section"><h2>Sistem karşılaştırması</h2><table><thead><tr><th>Sistem</th><th>7 Gün</th><th>14 Gün</th><th>30 Gün</th><th>Eğilim</th></tr></thead><tbody>{''.join(rows)}</tbody></table></section><section class="section grid"><div class="card"><small>PREMIUM MTF · SON 30 GÜN</small><h2>TP1 sonrası devam</h2><div class="stats"><div class="stat"><span>TP1 örneği</span><b>{int(tp.get("tp1_sample") or 0)}</b></div><div class="stat"><span>TP2'ye devam</span><b>{_fmt_pct(tp.get("tp2_continue_rate"))}</b></div><div class="stat"><span>TP3'e devam</span><b>{_fmt_pct(tp.get("tp3_continue_rate"))}</b></div></div><p class="note">Kâr yönetimini veriye göre değerlendirmek içindir; canlı kuralları otomatik değiştirmez.</p></div><div class="card"><small>PREMIUM MTF · SON 30 GÜN</small><h2>Stop sonrası davranış</h2><div class="stats"><div class="stat"><span>Stop</span><b>{int(stop.get("sl_total") or 0)}</b></div><div class="stat"><span>Hedefe geri dönüş</span><b>{_fmt_pct(stop.get("return_rate"))}</b></div><div class="stat"><span>Takip sürüyor</span><b>{int(stop.get("tracking") or 0)}</b></div></div><p class="note">Geri dönüş oranı dar stop/erken giriş ihtimalini ayırmaya yardım eder.</p></div></section><section class="section grid"><div class="card"><small>STOP KÖK NEDENLERİ</small><h2>Teşhis dağılımı</h2>{reasons}</div><div class="card"><small>VERİ GÜNCELLİĞİ</small><h2>Kaynak sağlığı</h2>{sources}</div></section><p class="note">Salt okunur analizdir. Emir açmaz, Telegram göndermez, strateji/config/TP/SL değerlerini değiştirmez. 60 saniyede bir yenilenir.</p></div></body></html>'''


def enhance_home_shortcut(body: str, payload: dict[str, Any]) -> str:
    if 'id="v39PerformanceShortcut"' in body:
        return body
    overall = payload.get("overall") or {}
    trend = overall.get("trend") or {}
    card = f'<a id="v39PerformanceShortcut" href="/performance-intelligence" style="display:block;width:min(1180px,calc(100% - 24px));margin:10px auto;border:1px solid rgba(44,230,191,.25);border-radius:14px;background:#0a1921;padding:12px 14px;text-decoration:none;color:#edf8f6"><b>◈ Performans Zekâsı</b><span style="display:block;color:#82a09d;font-size:9px">7 / 14 / 30 gün · Net R · TP/SL · stop teşhisi · TP1 devamı · {html.escape(str(trend.get("label") or "VERİ YETERSİZ"))}</span></a>'
    return body.replace("</body>", card + "</body>", 1) if "</body>" in body else body + card


def make_v39_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store: commercial.CommercialAccountStore, market_client=None, overview_client=None):
    BaseHandler = retention.make_v38_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V39Handler(BaseHandler):
        server_version = "KriptoPanel/3.9"

        def _send(self, status: int, body: str | bytes, content_type: str, *, cookies: list[str] | None = None, nonce: str | None = None) -> None:
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html") and urllib.parse.urlsplit(self.path).path == "/":
                session = self._session()
                if session and _has_access(session, self._plan_info(session) or {}):
                    try:
                        body = enhance_home_shortcut(body, build_window_intelligence(service.get_data()))
                    except Exception:
                        pass
            super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def _performance_payload(self) -> dict[str, Any]:
            ledger, warning, checked = _load_premium_ledger(config)
            payload = build_performance_intelligence(service.get_data(), ledger, ledger_warning=warning)
            payload["ledger_checked_at"] = checked
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
                if not _has_access(session, self._plan_info(session) or {}):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "Premium erişim gerekli."})
                    return
                self._json(HTTPStatus.OK, self._performance_payload())
                return
            return super().do_GET()

    return V39Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.9 Performans Zekâsı")
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
    print(f"{VERSION} http://{args.host}:{args.port} windows=7,14,30 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
