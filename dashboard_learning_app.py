"""Kripto Kontrol Merkezi V3.20 - Sistem Öğrenme Merkezi.

Yalnız ADMIN geliştirme ekranıdır. V3.18 ilk-15-dakika davranışını, V3.19 sistem
kalite profilini ve V3.9/V3.10 performans teşhislerini tek salt-okunur karar destek
ekranında birleştirir.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger ve otomatik
filtre mantığı değiştirilmez. Bu modül yalnız mevcut verileri okur ve gözlemsel
araştırma başlıkları üretir.
"""
from __future__ import annotations

import argparse
import html
import math
import os
import secrets
import time
import urllib.parse
from collections import Counter
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_improvement_app as improvement
import dashboard_market_app as market
import dashboard_performance_app as performance
import dashboard_systemquality_app as quality
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_20_SYSTEM_LEARNING_2026_08_16"


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _outcome(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "_")
    return {
        "STOP": "SL", "STOPLOSS": "SL", "STOP_LOSS": "SL",
        "BREAKEVEN": "BE", "BREAK_EVEN": "BE",
    }.get(text, text)


def _is_tp(value: Any) -> bool:
    text = _outcome(value)
    return text.startswith("TP") and "BE" not in text


def _summarize_early(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [row for row in rows if row.get("status") == "ok"]
    events = Counter(str(row.get("first_event") or "NONE") for row in ok)

    def avg(key: str) -> float | None:
        values = [_number(row.get(key)) for row in ok]
        clean = [v for v in values if v is not None]
        return round(sum(clean) / len(clean), 4) if clean else None

    positive = sum(1 for row in ok if (_number(row.get("window_close_r")) or 0) > 0)
    return {
        "sample": len(ok),
        "tp1_first_rate": round(events.get("TP1_FIRST", 0) * 100 / len(ok), 1) if ok else None,
        "sl_first_rate": round(events.get("SL_FIRST", 0) * 100 / len(ok), 1) if ok else None,
        "positive_close_rate": round(positive * 100 / len(ok), 1) if ok else None,
        "average_mfe_r": avg("mfe_r"),
        "average_mae_r": avg("mae_r"),
        "average_close_r": avg("window_close_r"),
    }


def build_early_cohorts(early_payload: dict[str, Any]) -> dict[str, Any]:
    samples = [row for row in (early_payload.get("samples") or []) if isinstance(row, dict)]
    winners = [row for row in samples if _is_tp(row.get("outcome"))]
    stops = [row for row in samples if _outcome(row.get("outcome")) == "SL"]
    neutral = [row for row in samples if row not in winners and row not in stops]
    win = _summarize_early(winners)
    stop = _summarize_early(stops)
    comparison = {
        "close_r_gap": round(float(win["average_close_r"]) - float(stop["average_close_r"]), 4)
        if win.get("average_close_r") is not None and stop.get("average_close_r") is not None else None,
        "mfe_gap": round(float(win["average_mfe_r"]) - float(stop["average_mfe_r"]), 4)
        if win.get("average_mfe_r") is not None and stop.get("average_mfe_r") is not None else None,
        "mae_gap": round(float(win["average_mae_r"]) - float(stop["average_mae_r"]), 4)
        if win.get("average_mae_r") is not None and stop.get("average_mae_r") is not None else None,
    }
    return {"tp": win, "sl": stop, "other": _summarize_early(neutral), "comparison": comparison}


def _result_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    outcomes = Counter(_outcome(row.get("outcome")) for row in rows)
    tp = sum(count for name, count in outcomes.items() if name.startswith("TP") and "BE" not in name)
    sl = outcomes.get("SL", 0)
    exact = [_number(row.get("r_result")) for row in rows]
    exact = [value for value in exact if value is not None]
    return {
        "sample": len(rows),
        "tp": int(tp),
        "sl": int(sl),
        "tp_rate": round(tp * 100 / len(rows), 1) if rows else None,
        "sl_rate": round(sl * 100 / len(rows), 1) if rows else None,
        "average_r": round(sum(exact) / len(exact), 4) if exact else None,
        "exact_r_sample": len(exact),
    }


def build_direction_learning(data: dict[str, Any]) -> list[dict[str, Any]]:
    recent = [row for row in (data.get("recent_results") or []) if isinstance(row, dict)]
    rows: list[dict[str, Any]] = []
    for system in earlyperf.SYSTEM_ORDER:
        system_rows = [row for row in recent if str(row.get("system") or "").upper() == system]
        if not system_rows:
            continue
        directions = {}
        for direction in ("LONG", "SHORT"):
            directions[direction] = _result_summary([
                row for row in system_rows if str(row.get("direction") or "").upper() == direction
            ])
        long_sl = directions["LONG"].get("sl_rate")
        short_sl = directions["SHORT"].get("sl_rate")
        gap = abs(float(long_sl) - float(short_sl)) if long_sl is not None and short_sl is not None else None
        weaker = None
        if gap is not None and gap >= 20 and min(directions["LONG"]["sample"], directions["SHORT"]["sample"]) >= 3:
            weaker = "LONG" if float(long_sl) > float(short_sl) else "SHORT"
        rows.append({
            "system": system,
            "label": earlyperf.SYSTEM_LABELS.get(system, system),
            "long": directions["LONG"],
            "short": directions["SHORT"],
            "sl_rate_gap": round(gap, 1) if gap is not None else None,
            "weaker_direction": weaker,
        })
    return rows


def build_learning_actions(
    cohorts: dict[str, Any],
    directions: list[dict[str, Any]],
    quality_payload: dict[str, Any],
    stop_diagnosis: dict[str, Any],
    stop_experiment: dict[str, Any] | None,
    trend_guard: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    win, stop = cohorts.get("tp") or {}, cohorts.get("sl") or {}
    win_n, stop_n = int(win.get("sample") or 0), int(stop.get("sample") or 0)

    if win_n >= 3 and stop_n >= 3:
        win_mae, stop_mae = _number(win.get("average_mae_r")), _number(stop.get("average_mae_r"))
        stop_close = _number(stop.get("average_close_r"))
        stop_mfe = _number(stop.get("average_mfe_r"))
        if stop_close is not None and stop_close <= -0.30:
            actions.append({"type":"RESEARCH","title":"STOP işlemlerinde erken ters hareketi araştır","reason":f"STOP grubunun ilk 15 dk ortalama kapanışı {stop_close:+.2f}R. Giriş zamanlaması / yön teyidi gölgede ayrıştırılmalı.","priority":1})
        if win_mae is not None and win_mae <= -0.65:
            actions.append({"type":"PROTECT","title":"Stopu körlemesine daraltma","reason":f"Kazanan işlemler de ilk 15 dk ortalama {win_mae:+.2f}R MAE görüyor. Daha dar stop başarılı profilleri kesebilir.","priority":1})
        if stop_mfe is not None and stop_mfe >= 0.70:
            actions.append({"type":"RESEARCH","title":"Önce kâra gidip dönen STOP profilini incele","reason":f"STOP grubunda ortalama MFE {stop_mfe:+.2f}R. Bazı kayıplar girişten sonra lehimize hareket edip dönüyor olabilir.","priority":2})
        if win_mae is not None and stop_mae is not None and abs(win_mae - stop_mae) < 0.15:
            actions.append({"type":"COLLECT","title":"MAE tek başına ayırıcı değil","reason":"Kazanan ve STOP gruplarının erken ters hareketi birbirine yakın. Tek başına MAE filtresi eklemek için kanıt zayıf.","priority":3})
    else:
        actions.append({"type":"COLLECT","title":"İlk 15 dakika örneğini büyüt","reason":f"TP={win_n}, STOP={stop_n}. Kazanan/kaybeden ayrımı için her iki grupta da en az 3 ölçüm bekleniyor.","priority":2})

    for row in directions:
        if row.get("weaker_direction"):
            actions.append({
                "type":"RESEARCH",
                "title":f"{row['label']} · {row['weaker_direction']} yönünü ayrı incele",
                "reason":f"LONG/SHORT SL oranı farkı %{row.get('sl_rate_gap'):.1f}. Canlı blok değil; yön koşulları gölgede karşılaştırılmalı.",
                "priority":2,
            })

    profiles = quality_payload.get("profiles") if isinstance(quality_payload.get("profiles"), list) else []
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        if profile.get("band") == "STRONG" and float(profile.get("confidence_score") or 0) >= 30:
            actions.append({"type":"PROTECT","title":f"{profile.get('label')} başarılı profilini koru","reason":"Kapanış ve erken davranış birlikte güçlü gözleniyor. Yeni filtre bu profil üzerinde kayıp üretmemeli.","priority":1})

    resolved = int(stop_diagnosis.get("resolved_follow") or 0)
    return_rate = _number(stop_diagnosis.get("return_rate"))
    if resolved and return_rate is not None:
        actions.append({"type":"INFO","title":"Stop sonrası dönüş ölçümü","reason":f"Kesinleşmiş {resolved} STOP takibinde hedefe dönüş oranı %{return_rate:.1f}.","priority":3})
    if stop_experiment:
        actions.append({"type":"EXISTING","title":str(stop_experiment.get("label") or "Stop teşhisi"),"reason":str(stop_experiment.get("reason") or ""),"priority":2,"status":stop_experiment.get("status_label")})
    if trend_guard:
        actions.append({"type":"EXISTING","title":str(trend_guard.get("label") or "Premium koruma"),"reason":str(trend_guard.get("reason") or ""),"priority":2,"status":trend_guard.get("status_label")})

    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for action in sorted(actions, key=lambda item: (int(item.get("priority") or 9), str(item.get("title") or ""))):
        key = str(action.get("title") or "")
        if key and key not in seen:
            unique.append(action)
            seen.add(key)
    return unique[:12]


def build_learning_payload(
    data: dict[str, Any],
    early_payload: dict[str, Any],
    quality_payload: dict[str, Any],
    premium_ledger: dict[str, Any],
    *,
    ledger_warning: str | None = None,
    ledger_checked_at: int = 0,
) -> dict[str, Any]:
    cohorts = build_early_cohorts(early_payload)
    directions = build_direction_learning(data)
    window = performance.build_window_intelligence(data)
    stop_diag = performance.analyze_stop_diagnosis(premium_ledger, days=30)
    tp_continue = performance.analyze_tp_continuation(premium_ledger, days=30)
    perf_payload = {"window_intelligence": window, "stop_diagnosis": stop_diag}
    stop_experiment = improvement.build_stop_experiment(perf_payload)
    trend_guard = improvement.build_trend_guard(perf_payload)
    actions = build_learning_actions(cohorts, directions, quality_payload, stop_diag, stop_experiment, trend_guard)
    return {
        "version": VERSION,
        "generated_at": int(time.time()),
        "early_cohorts": cohorts,
        "directions": directions,
        "system_quality": quality_payload.get("profiles") or [],
        "window_intelligence": window,
        "stop_diagnosis": stop_diag,
        "tp_continuation": tp_continue,
        "existing_decisions": {"stop_experiment": stop_experiment, "trend_guard": trend_guard},
        "actions": actions,
        "ledger": {"warning": ledger_warning, "checked_at": ledger_checked_at},
        "note": "Bu ekran geliştirme kararı için kanıt toplar; hiçbir öneriyi canlı sisteme otomatik uygulamaz.",
        "safety": {
            "admin_only": True,
            "signal_engine": "unchanged",
            "telegram": "unchanged",
            "trade_management": "unchanged",
            "ledger_write": "unchanged",
            "automatic_filter": False,
        },
    }


PAGE = r'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="dark"><title>Sistem Öğrenme Merkezi</title><style>
:root{--bg:#061016;--panel:#0a1921;--line:#1b3943;--text:#eef8f6;--muted:#7f9d99;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59;--blue:#69a9ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% -10%,rgba(105,169,255,.13),transparent 28%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}.shell{width:min(1320px,calc(100% - 24px));margin:auto;padding:20px 0 70px}.top{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:15px}.btn{border:1px solid var(--line);background:#091821;color:#9db6b2;border-radius:10px;padding:9px 11px;font-weight:850;text-decoration:none;cursor:pointer}.btn:hover{border-color:var(--teal);color:var(--teal)}.spacer{flex:1}.hero,.card,.panel{border:1px solid var(--line);background:rgba(10,25,33,.96);border-radius:16px}.hero{padding:18px;border-color:rgba(105,169,255,.24);background:linear-gradient(135deg,#0d202a,#07141b)}.eyebrow{font-size:9px;color:var(--blue);font-weight:950;letter-spacing:.08em}.hero h1{margin:4px 0 5px;font-size:28px;letter-spacing:-.04em}.hero p{margin:0;color:var(--muted);max-width:920px}.cohorts{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.card{padding:13px}.card h2,.panel h2{margin:0;font-size:14px}.sub{color:var(--muted);font-size:9px}.metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:9px}.metric{background:#07151c;border-radius:9px;padding:8px}.metric small{display:block;color:#607d79;font-size:7px}.metric b{font-size:11px}.tp{border-color:rgba(66,226,140,.28)}.sl{border-color:rgba(255,98,125,.28)}.pos{color:var(--green)}.neg{color:var(--red)}.amber{color:var(--amber)}.panel{margin-top:12px;padding:13px}.actions{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:9px}.action{background:#07151c;border:1px solid rgba(27,57,67,.72);border-radius:11px;padding:10px}.action b{font-size:11px}.action p{margin:4px 0 0;color:#809995;font-size:9px}.tag{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 6px;font-size:7px;font-weight:900;margin-bottom:5px}.PROTECT{color:var(--green)}.RESEARCH{color:var(--amber)}.COLLECT{color:var(--blue)}.EXISTING{color:var(--teal)}.INFO{color:var(--muted)}.direction{display:grid;grid-template-columns:1.2fr repeat(4,.8fr);gap:7px;align-items:center;padding:8px 0;border-bottom:1px solid rgba(27,57,67,.65)}.direction:last-child{border:0}.direction small{color:var(--muted)}.note{margin-top:12px;color:#617f7b;font-size:9px}.empty{padding:18px;text-align:center;color:var(--muted)}@media(max-width:850px){.cohorts,.actions{grid-template-columns:1fr}.direction{grid-template-columns:1fr 1fr 1fr}.hide-small{display:none}}@media(max-width:620px){.shell{width:calc(100% - 14px);padding-top:8px}.hero{padding:13px}.hero h1{font-size:22px}.metrics{grid-template-columns:1fr 1fr}.direction{grid-template-columns:1fr 1fr}}
</style></head><body><div class="shell"><div class="top"><a class="btn" href="/">← Panel</a><a class="btn" href="/system-quality">Kalite Profili</a><a class="btn" href="/early-performance">15 dk Analizi</a><div class="spacer"></div><button class="btn" id="refreshBtn">Yenile</button></div><section class="hero"><div class="eyebrow">V3.20 · ADMIN · KARAR DESTEK</div><h1>Sistem Öğrenme Merkezi</h1><p>TP ve STOP profillerini, ilk 15 dakika davranışını, LONG/SHORT farklarını ve mevcut performans teşhislerini tek yerde toplar. Amaç yeni filtre eklemek değil; hangi değişikliğin gerçekten test edilmeye değer olduğunu bulmaktır.</p></section><div class="cohorts"><section class="card tp"><h2>TP ile kapananlar · ilk 15 dakika</h2><div class="sub" id="tpSample">—</div><div class="metrics" id="tpMetrics"></div></section><section class="card sl"><h2>STOP ile kapananlar · ilk 15 dakika</h2><div class="sub" id="slSample">—</div><div class="metrics" id="slMetrics"></div></section></div><section class="panel"><h2>Şimdi neyi araştırmalıyız?</h2><div class="sub">Otomatik uygulama yok. Bunlar yalnız kanıta dayalı geliştirme başlıklarıdır.</div><div class="actions" id="actions"><div class="empty">Öğrenme çıktıları hazırlanıyor…</div></div></section><section class="panel"><h2>LONG / SHORT farkı</h2><div class="sub">Yönlerden biri belirgin daha çok STOP oluyorsa yalnız araştırma adayı olarak işaretlenir.</div><div id="directions"></div></section><section class="panel"><h2>Premium işlem yönetimi gözlemi</h2><div class="metrics"><div class="metric"><small>TP1 sonrası TP2 devam</small><b id="tp2Continue">—</b></div><div class="metric"><small>TP1 sonrası TP3 devam</small><b id="tp3Continue">—</b></div><div class="metric"><small>STOP sonrası hedefe dönüş</small><b id="stopReturn">—</b></div></div></section><div class="note" id="note">Canlı sisteme hiçbir kural geri yazılmaz.</div></div><script nonce="__NONCE__">(()=>{'use strict';const $=id=>document.getElementById(id);const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};const pct=v=>{const n=num(v);return n===null?'—':`%${n.toFixed(1)}`};const rr=v=>{const n=num(v);return n===null?'—':`${n>=0?'+':''}${n.toFixed(2)}R`};const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));function metrics(x){return `<div class="metric"><small>Pozitif 15dk</small><b>${pct(x.positive_close_rate)}</b></div><div class="metric"><small>Ort. 15dk R</small><b>${rr(x.average_close_r)}</b></div><div class="metric"><small>MFE</small><b>${rr(x.average_mfe_r)}</b></div><div class="metric"><small>MAE</small><b>${rr(x.average_mae_r)}</b></div><div class="metric"><small>TP1 önce</small><b>${pct(x.tp1_first_rate)}</b></div><div class="metric"><small>SL önce</small><b>${pct(x.sl_first_rate)}</b></div>`}async function get(){const r=await fetch(`/api/learning-center?v=${Date.now()}`,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');throw new Error('Oturum gerekli')}if(r.status===403){location.assign('/');throw new Error('Yalnız yönetici erişebilir')}const p=await r.json();if(!r.ok)throw new Error(p.message||p.error||`HTTP ${r.status}`);return p}function render(p){const c=p.early_cohorts||{},tp=c.tp||{},sl=c.sl||{};$('tpSample').textContent=`${tp.sample||0} ölçülen TP örneği`;$('slSample').textContent=`${sl.sample||0} ölçülen STOP örneği`;$('tpMetrics').innerHTML=metrics(tp);$('slMetrics').innerHTML=metrics(sl);$('actions').innerHTML=(p.actions||[]).map(a=>`<div class="action"><span class="tag ${esc(a.type)}">${esc(a.type)}</span><b>${esc(a.title)}</b>${a.status?`<div class="sub">${esc(a.status)}</div>`:''}<p>${esc(a.reason)}</p></div>`).join('')||'<div class="empty">Henüz güvenilir araştırma başlığı yok; veri birikiyor.</div>';$('directions').innerHTML=(p.directions||[]).map(d=>`<div class="direction"><div><b>${esc(d.label)}</b><small>${d.weaker_direction?` · ${esc(d.weaker_direction)} araştır`:''}</small></div><div><small>LONG TP/SL</small><b>${pct(d.long?.tp_rate)} / ${pct(d.long?.sl_rate)}</b></div><div><small>SHORT TP/SL</small><b>${pct(d.short?.tp_rate)} / ${pct(d.short?.sl_rate)}</b></div><div class="hide-small"><small>LONG örnek</small><b>${d.long?.sample||0}</b></div><div class="hide-small"><small>SHORT örnek</small><b>${d.short?.sample||0}</b></div></div>`).join('')||'<div class="empty">Yön karşılaştırması için veri yok.</div>';const t=p.tp_continuation||{},s=p.stop_diagnosis||{};$('tp2Continue').textContent=`${pct(t.tp2_continue_rate)} · n=${t.tp1_sample||0}`;$('tp3Continue').textContent=`${pct(t.tp3_continue_rate)} · n=${t.tp1_sample||0}`;$('stopReturn').textContent=`${pct(s.return_rate)} · n=${s.resolved_follow||0}`;$('note').textContent=p.note||'Salt okunur gözlemdir.'}async function load(){const b=$('refreshBtn');b.disabled=true;b.textContent='Analiz ediliyor…';try{render(await get())}catch(e){$('actions').innerHTML=`<div class="empty">Öğrenme Merkezi alınamadı: ${esc(e.message)}</div>`}finally{b.disabled=false;b.textContent='Yenile'}}$('refreshBtn').addEventListener('click',load);load()})();</script></body></html>'''


def page(nonce: str) -> str:
    return PAGE.replace("__NONCE__", html.escape(str(nonce), quote=True))


def enhance_admin_navigation(body: str, path: str) -> str:
    if 'href="/learning-center"' in body:
        return body
    if path == "/system-quality":
        anchor = '<a class="btn" href="/early-performance">15 dk Analizi</a>'
        if anchor in body:
            return body.replace(anchor, anchor + '<a class="btn" href="/learning-center">Öğrenme Merkezi</a>', 1)
    if path == "/":
        anchor = '<a class="nav-item" href="/system-quality">'
        if anchor in body:
            return body.replace(anchor, '<a class="nav-item" href="/learning-center"><span>◎</span><b>Öğrenme</b></a>' + anchor, 1)
    return body


def make_v320_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None, history_cache: earlyperf.HistoricalPulseCache | None = None):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = quality.make_v319_handler(config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache)

    class V320Handler(BaseHandler):
        server_version = "KriptoPanel/3.20"

        @staticmethod
        def _is_admin_session(session: dict[str, Any] | None) -> bool:
            return bool(session) and str(session.get("role") or "").upper() == commercial.ROLE_ADMIN

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path in {"/", "/system-quality"}:
                    session = self._session()
                    if self._is_admin_session(session):
                        body = enhance_admin_navigation(body, path)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status":"ok","version":VERSION,"learning_center":True,"admin_only":True,
                    "winner_loser_cohorts":True,"direction_learning":True,"existing_diagnostics":True,
                    "automatic_filter":False,"signal_engine":"unchanged","telegram":"unchanged",
                    "trade_management":"unchanged","ledger_write":"unchanged",
                })
                return
            if path == "/learning-center":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                if not self._is_admin_session(session):
                    self._redirect("/")
                    return
                nonce = secrets.token_urlsafe(18)
                self._send(HTTPStatus.OK, page(nonce), "text/html; charset=utf-8", nonce=nonce)
                return
            if path == "/api/learning-center":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error":"authentication_required"})
                    return
                if not self._is_admin_session(session):
                    self._json(HTTPStatus.FORBIDDEN, {"error":"admin_required"})
                    return
                try:
                    data = service.get_data()
                    early_payload = earlyperf.build_history_payload(data, candle_client, cache)
                    quality_payload = quality.build_quality_payload(data, early_payload)
                    ledger, warning, checked_at = performance._load_premium_ledger(config)
                    payload = build_learning_payload(
                        data, early_payload, quality_payload, ledger,
                        ledger_warning=warning, ledger_checked_at=checked_at,
                    )
                    self._json(HTTPStatus.OK, payload)
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error":"learning_center_unavailable"})
                return
            return super().do_GET()

    return V320Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.20 Sistem Öğrenme Merkezi")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = commercial.commercial_store_from_env(config)
    candle_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v320_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} admin_only=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
