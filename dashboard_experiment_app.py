"""Kripto Kontrol Merkezi V3.11 - Deney Takip ve Terfi Hazırlığı.

ADMIN-only panel layer. It turns V3.10 candidates into measurable evidence gates.
No strategy/config/radar/Telegram/order/TP-SL rule is changed or auto-applied.
"""
from __future__ import annotations

import argparse
import html
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_commercial_app as commercial
import dashboard_improvement_app as improvement
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_11_EXPERIMENT_READINESS_2026_08_16"
TARGET_SAMPLE = 50
TARGET_NET_R = 5.0
TARGET_AVG_R = 0.05
MAX_NEGATIVE_RATE = 15.0
STOP_TARGET = 40
SAFE_PREMIUM = {"KORU", "KORU_IZLE"}


def _num(value: Any, default: float | None = None) -> float | None:
    return improvement._number(value, default)


def build_core_guard(payload: dict[str, Any]) -> dict[str, Any]:
    decision = payload.get("decision_engine") or {}
    premium = next((r for r in decision.get("actions") or [] if isinstance(r, dict) and str(r.get("component") or "").upper() == "PREMIUM"), {})
    code = str(premium.get("decision_code") or "UNKNOWN").upper()
    fresh = bool((payload.get("summary") or {}).get("live_review_allowed"))
    premium_ok = code in SAFE_PREMIUM
    trend_ok = not any(isinstance(r, dict) and r.get("id") == "PREMIUM_PROTECTION_GUARD" for r in payload.get("candidates") or [])
    reasons = []
    if not fresh: reasons.append("Decision Engine veya gölge raporu güncel değil.")
    if not premium_ok: reasons.append(f"Premium koruma kararı uygun değil ({code}).")
    if not trend_ok: reasons.append("Premium kısa dönem koruma kilidi aktif.")
    return {"freshness_pass": fresh, "premium_decision_pass": premium_ok, "premium_trend_pass": trend_ok,
            "premium_decision_code": code, "overall_pass": fresh and premium_ok and trend_ok,
            "reasons": reasons, "automatic_apply": False}


def model_readiness(row: dict[str, Any], guard: dict[str, Any]) -> dict[str, Any]:
    sample = int(_num(row.get("sample"), 0) or 0)
    net_r, avg_r, negative = _num(row.get("net_incremental_r")), _num(row.get("average_incremental_r")), _num(row.get("negative_rate"))
    gates = {"sample": sample >= TARGET_SAMPLE, "net_r": net_r is not None and net_r >= TARGET_NET_R,
             "average_r": avg_r is not None and avg_r >= TARGET_AVG_R,
             "negative_rate": negative is not None and negative <= MAX_NEGATIVE_RATE,
             "core_guard": bool(guard.get("overall_pass"))}
    gap_sample = max(0, TARGET_SAMPLE - sample)
    gap_r = None if net_r is None else round(max(0.0, TARGET_NET_R - net_r), 4)
    missing = []
    if not gates["sample"]: missing.append(f"{gap_sample} ek kapanış")
    if not gates["net_r"]: missing.append("ek Net R ≥ +5.0R" if gap_r is None else f"+{gap_r:.2f}R ek kanıt")
    if not gates["average_r"]: missing.append("ortalama ek R ≥ +0.05R")
    if not gates["negative_rate"]: missing.append("negatif etki ≤ %15")
    if not gates["core_guard"]: missing.append("Premium/rapor koruma geçidi")
    status = str(row.get("status") or "")
    if status == "REJECT": stage = "REJECTED"
    elif status == "LIVE_REVIEW_READY" and all(gates.values()): stage = "REVIEW_READY"
    elif status in {"LIVE_REVIEW_READY", "PROMOTION_CANDIDATE"}: stage = "SECOND_VALIDATION"
    elif status == "COMPARE_BACKUP": stage = "BACKUP"
    else: stage = "SHADOW_VALIDATION"
    milestone = "Ayrı, küçük ve geri alınabilir canlı PR incelemesi hazırlanabilir." if stage == "REVIEW_READY" else ("Canlı aday listesinden çıkar; kanıt arşivinde tut." if stage == "REJECTED" else (" · ".join(missing) or "Gölge doğrulamasını sürdür."))
    return {"stage": stage, "score": int(round(sum(gates.values()) / len(gates) * 100)), "gates": gates,
            "sample_target": TARGET_SAMPLE, "sample_gap": gap_sample, "net_r_target": TARGET_NET_R,
            "net_r_gap": gap_r, "average_r_target": TARGET_AVG_R, "max_negative_rate": MAX_NEGATIVE_RATE,
            "next_milestone": milestone, "ready_for_live_review": stage == "REVIEW_READY"}


def stop_readiness(row: dict[str, Any]) -> dict[str, Any]:
    sample, rate = int(_num(row.get("sample"), 0) or 0), _num(row.get("return_rate"))
    sample_ok, rate_ok = sample >= STOP_TARGET, rate is not None and rate >= 35.0
    if str(row.get("status") or "") == "NEW_SHADOW_TEST": stage = "SHADOW_READY" if sample_ok else "DESIGN_SHADOW"
    elif str(row.get("status") or "") == "WATCH_PROTECT": stage = "PROTECT"
    else: stage = "COLLECT"
    missing = []
    if not sample_ok: missing.append(f"{max(0, STOP_TARGET-sample)} ek stop-sonrası takip")
    if not rate_ok: missing.append("hedefe dönüş oranı ≥ %35")
    return {"stage": stage, "score": int(round((int(sample_ok)+int(rate_ok))/2*100)),
            "gates": {"resolved_sample": sample_ok, "return_rate": rate_ok}, "sample_target": STOP_TARGET,
            "sample_gap": max(0, STOP_TARGET-sample), "next_milestone": " · ".join(missing) or "Ayrı stop/giriş gölge testi tasarla.",
            "ready_for_live_review": False}


def build_experiment_registry(payload: dict[str, Any]) -> dict[str, Any]:
    guard, rows = build_core_guard(payload), []
    for raw in payload.get("candidates") or []:
        if not isinstance(raw, dict): continue
        row = dict(raw)
        if str(row.get("source") or "") == "POST_RESULT_SHADOW_V3": ready = model_readiness(row, guard)
        elif row.get("id") == "STOP_ENTRY_TIMING_REVIEW": ready = stop_readiness(row)
        else: ready = {"stage": "PROTECT" if row.get("status") == "WATCH_PROTECT" else "OBSERVE", "score": 0, "gates": {}, "next_milestone": str(row.get("reason") or "İzle"), "ready_for_live_review": False}
        row["readiness"] = ready; rows.append(row)
    order = {"REVIEW_READY":0,"SECOND_VALIDATION":1,"SHADOW_READY":2,"DESIGN_SHADOW":3,"SHADOW_VALIDATION":4,"BACKUP":5,"COLLECT":6,"PROTECT":7,"OBSERVE":8,"REJECTED":9}
    rows.sort(key=lambda r:(order.get(str((r.get("readiness") or {}).get("stage")),99),-int((r.get("readiness") or {}).get("score") or 0)))
    packets = []
    for row in rows:
        rd = row.get("readiness") or {}
        if rd.get("ready_for_live_review") and guard.get("overall_pass"):
            packets.append({"candidate_id": row.get("id"), "label": row.get("label"), "family": row.get("family"),
                            "evidence": {"sample": int(_num(row.get("sample"),0) or 0), "net_incremental_r": _num(row.get("net_incremental_r")), "average_incremental_r": _num(row.get("average_incremental_r")), "negative_rate": _num(row.get("negative_rate"))},
                            "policy": "Ayrı branch/PR; tek davranış; yeniden gölge doğrulaması; geri alınabilir değişiklik.",
                            "minimum_live_review_sample": 20, "automatic_apply": False, "automatic_rollback": False})
    counts = {}
    for row in rows:
        stage = str((row.get("readiness") or {}).get("stage") or "UNKNOWN"); counts[stage] = counts.get(stage,0)+1
    return {"version": VERSION, "core_guard": guard,
            "summary": {"tracked":len(rows),"review_ready":counts.get("REVIEW_READY",0),"second_validation":counts.get("SECOND_VALIDATION",0),"shadow_design":counts.get("DESIGN_SHADOW",0)+counts.get("SHADOW_READY",0),"rejected":counts.get("REJECTED",0),"promotion_packets":len(packets)},
            "experiments": rows, "promotion_packets": packets, "auto_apply": False,
            "note": "Kanıt hedefi üretir; canlı kural uygulamaz."}


def render_experiment_page(payload: dict[str, Any]) -> str:
    summary, guard = payload.get("summary") or {}, payload.get("core_guard") or {}
    cards = []
    for row in payload.get("experiments") or []:
        rd, label = row.get("readiness") or {}, html.escape(str(row.get("label") or row.get("id") or "Aday"))
        stage, score = html.escape(str(rd.get("stage") or "OBSERVE")), int(rd.get("score") or 0)
        cards.append(f'<article><b>{stage} · {score}%</b><h3>{label}</h3><p>{html.escape(str(row.get("reason") or ""))}</p><div class="bar"><i style="width:{max(0,min(100,score))}%"></i></div><small>Sonraki eşik: {html.escape(str(rd.get("next_milestone") or "İzle"))}</small></article>')
    packets = ''.join(f'<div class="packet"><b>{html.escape(str(p.get("label") or ""))}</b><span>{int((p.get("evidence") or {}).get("sample") or 0)} örnek · {(p.get("evidence") or {}).get("net_incremental_r"):+.3f}R</span></div>' for p in payload.get("promotion_packets") or []) or '<div class="empty">Henüz canlı inceleme paketi yok.</div>'
    reason = " · ".join(guard.get("reasons") or []) or "Raporlar güncel, Premium koruma kararı uygun ve trend kilidi kapalı."
    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Deney Takip ve Terfi</title><style>body{{margin:0;background:#061016;color:#edf8f6;font:13px/1.5 system-ui}}a{{color:inherit}}.shell{{width:min(1160px,calc(100% - 20px));margin:auto;padding:22px 0}}.top,.kpis{{display:flex;gap:8px;justify-content:space-between;flex-wrap:wrap}}.kpi,article,.packet,.guard,.empty{{background:#0b1b23;border:1px solid #1b3943;border-radius:12px;padding:12px}}.kpi{{flex:1;min-width:130px}}.kpi small,article small,p,.guard span{{color:#82a09d}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}.bar{{height:6px;background:#12272f;border-radius:20px;overflow:hidden;margin:10px 0}}.bar i{{display:block;height:100%;background:#2ce6bf}}.guard{{margin:14px 0}}.guard b{{color:#2ce6bf}}.packet{{margin:7px 0}}@media(max-width:760px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><div class="shell"><div class="top"><div><h1>Deney Takip ve Terfi Hazırlığı</h1><p>Kaç örnek, kaç R ve hangi koruma geçidi eksik?</p></div><nav><a href="/admin/improvement-center">Karar Merkezi</a> · <a href="/performance-intelligence">Performans</a> · <a href="/admin/center">Yönetim</a></nav></div><div class="guard"><b>{'TERFİ KORUMA GEÇİDİ AÇIK' if guard.get('overall_pass') else 'TERFİ KORUMA GEÇİDİ KAPALI'}</b><span> · {html.escape(reason)}</span></div><div class="kpis"><div class="kpi"><small>Takip</small><b>{summary.get('tracked',0)}</b></div><div class="kpi"><small>Hazır</small><b>{summary.get('review_ready',0)}</b></div><div class="kpi"><small>2. doğrulama</small><b>{summary.get('second_validation',0)}</b></div><div class="kpi"><small>Gölge tasarımı</small><b>{summary.get('shadow_design',0)}</b></div><div class="kpi"><small>Red</small><b>{summary.get('rejected',0)}</b></div></div><h2>Deney ilerleme panosu</h2><p>Yüzde yalnız kanıt eşiklerinin tamamlanmasını gösterir; kazanç olasılığı değildir.</p><div class="grid">{''.join(cards) or '<div class="empty">Aday yok.</div>'}</div><h2>Canlı inceleme paketleri</h2>{packets}<h2>Güvenlik</h2><div class="guard">Otomatik canlı değişiklik kapalıdır. Ayrı branch/PR ve geri alma planı gerekir. Yeni periyodik GitHub Actions takvimi eklenmedi; ekstra periyodik Actions maliyeti yoktur.</div></div></body></html>'''


def enhance_admin_shortcut(body: str, payload: dict[str, Any]) -> str:
    if 'id="v311ExperimentShortcut"' in body: return body
    s = payload.get("summary") or {}
    card = f'<a id="v311ExperimentShortcut" href="/admin/experiment-center"><b>🧪 Deney Takip ve Terfi</b><span>{s.get("second_validation",0)} doğrulama · {s.get("review_ready",0)} hazır · {s.get("shadow_design",0)} gölge</span></a>'
    marker = '<div class="quick">'
    return body.replace(marker, marker + card, 1) if marker in body else body


def make_v311_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store: commercial.CommercialAccountStore, market_client=None, overview_client=None):
    Base = improvement.make_v310_handler(config, service, sessions, limiter, store, market_client, overview_client)
    class Handler(Base):
        server_version = "KriptoPanel/3.11"
        def _experiment_payload(self): return build_experiment_registry(self._improvement_payload())
        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body,str) and content_type.startswith("text/html") and urllib.parse.urlsplit(self.path).path == "/admin/center" and self._admin_session():
                try: body = enhance_admin_shortcut(body, self._experiment_payload())
                except Exception: pass
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)
        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK,{"status":"ok","version":VERSION,"experiment_readiness":True,"promotion_packets":True,"admin_only":True,"auto_apply":False,"extra_scheduled_actions":False,"signal_engine":"unchanged","telegram":"unchanged"}); return
            if path in {"/admin/experiment-center","/admin/experiments"}:
                if not self._admin_session(): self._redirect("/login" if not self._session() else "/"); return
                self._send(HTTPStatus.OK,render_experiment_page(self._experiment_payload()),"text/html; charset=utf-8"); return
            if path == "/api/admin/experiment-readiness":
                if not self._admin_session(): self._json(HTTPStatus.FORBIDDEN,{"error":"ADMIN erişimi gerekli."}); return
                self._json(HTTPStatus.OK,self._experiment_payload()); return
            return super().do_GET()
    return Handler


def main():
    p=argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.11 Deney Takip ve Terfi"); p.add_argument("--host",default=os.getenv("HOST","127.0.0.1")); p.add_argument("--port",type=int,default=int(os.getenv("PORT","8080"))); p.add_argument("--root",default="."); a=p.parse_args()
    config=PanelConfig.from_env(Path(a.root)); config.validate(); service=build_service(config); sessions=accounts.ManagedSessionStore(config.session_hours*3600); limiter=LoginRateLimiter(); store=lifecycle.lifecycle_store_from_env(config); market_client=OKXMarketDataClient(cache_seconds=30); overview_client=market.OKXMarketOverviewClient(cache_seconds=20)
    server=ThreadingHTTPServer((a.host,a.port),make_v311_handler(config,service,sessions,limiter,store,market_client,overview_client)); print(f"{VERSION} http://{a.host}:{a.port} admin_only=1 auto_apply=0 extra_schedule=0")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__": main()
