"""Kripto Kontrol Merkezi V3.19 - Sistem Kalite Profili.

V3.18 ilk 15 dakika geçmiş analizini korur. Bu katman V3.18 erken davranış
istatistiklerini mevcut kapanış performansıyla birleştirerek sistem bazında salt-okunur
bir gözlemsel kalite profili üretir.

Puan ve etiketler canlı filtre, sinyal, işlem önerisi veya otomatik karar değildir.
Sinyal, strateji, radar, Telegram, emir, TP/SL, BE ve state/ledger yazma mantığı
kesinlikle değiştirilmez.
"""
from __future__ import annotations

import argparse
import html
import math
import os
import secrets
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_earlyperformance_app as earlyperf
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_19_SYSTEM_QUALITY_2026_08_16"

PROFILE_LABELS = {
    "STRONG": "Güçlü gözlem profili",
    "BALANCED": "Dengeli gözlem profili",
    "WATCH": "Yakından izlenmeli",
    "RISKY": "Riskli başlangıç profili",
    "DATA_INSUFFICIENT": "Veri yetersiz",
}
CONFIDENCE_LABELS = {
    "HIGH": "Yüksek veri güveni",
    "MEDIUM": "Orta veri güveni",
    "LOW": "Düşük veri güveni",
    "VERY_LOW": "Çok düşük veri güveni",
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, minimum: float = 0.0, maximum: float = 100.0) -> float:
    return max(minimum, min(maximum, float(value)))


def _weighted(parts: list[tuple[float | None, float]]) -> float | None:
    clean = [(float(value), float(weight)) for value, weight in parts if value is not None and weight > 0]
    if not clean:
        return None
    weight_total = sum(weight for _, weight in clean)
    return round(sum(value * weight for value, weight in clean) / weight_total, 2)


def _r_score(value: Any) -> float | None:
    """Map an observed average R into 0..100 without treating it as probability."""
    number = _number(value)
    if number is None:
        return None
    return round(_clamp(50.0 + number * 35.0), 2)


def _edge_score(tp1_first_rate: Any, sl_first_rate: Any) -> float | None:
    tp = _number(tp1_first_rate)
    sl = _number(sl_first_rate)
    if tp is None and sl is None:
        return None
    tp = 0.0 if tp is None else tp
    sl = 0.0 if sl is None else sl
    return round(_clamp(50.0 + (tp - sl) * 0.5), 2)


def _confidence(final_sample: int, early_sample: int) -> tuple[str, float]:
    final_part = min(max(final_sample, 0) / 20.0, 1.0) * 0.65
    early_part = min(max(early_sample, 0) / 8.0, 1.0) * 0.35
    score = round((final_part + early_part) * 100.0, 1)
    if score >= 80:
        return "HIGH", score
    if score >= 55:
        return "MEDIUM", score
    if score >= 30:
        return "LOW", score
    return "VERY_LOW", score


def _profile_band(score: float | None, final_sample: int, early_sample: int) -> str:
    if score is None or (final_sample < 3 and early_sample < 3):
        return "DATA_INSUFFICIENT"
    if score >= 72:
        return "STRONG"
    if score >= 58:
        return "BALANCED"
    if score >= 45:
        return "WATCH"
    return "RISKY"


def _final_performance_by_system(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    performance = data.get("performance") if isinstance(data.get("performance"), dict) else {}
    rows = performance.get("systems") if isinstance(performance.get("systems"), list) else []
    out: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("system") or "").upper()
        if key in earlyperf.SYSTEM_ORDER:
            out[key] = raw
    return out


def build_system_profile(system: str, final_row: dict[str, Any], early_row: dict[str, Any]) -> dict[str, Any]:
    final_sample = max(0, int(_number(final_row.get("sample")) or 0))
    exact_r_sample = max(0, int(_number(final_row.get("exact_r_sample")) or 0))
    net_r = _number(final_row.get("net_r"))
    average_final_r = round(net_r / exact_r_sample, 4) if net_r is not None and exact_r_sample else None
    final_tp_rate = _number(final_row.get("tp_rate"))
    final_sl_rate = _number(final_row.get("sl_rate"))

    final_score = _weighted([
        (final_tp_rate, 0.40),
        ((100.0 - final_sl_rate) if final_sl_rate is not None else None, 0.25),
        (_r_score(average_final_r), 0.35),
    ])

    early_sample = max(0, int(_number(early_row.get("sample")) or 0))
    early_positive = _number(early_row.get("positive_close_rate"))
    early_edge = _edge_score(early_row.get("tp1_first_rate"), early_row.get("sl_first_rate"))
    early_close_r = _number(early_row.get("average_close_r"))
    early_score = _weighted([
        (early_positive, 0.35),
        (early_edge, 0.35),
        (_r_score(early_close_r), 0.30),
    ])

    if final_score is not None and early_score is not None:
        final_weight = 0.68 if early_sample >= 3 else 0.85
        composite = round(final_score * final_weight + early_score * (1.0 - final_weight), 2)
    else:
        composite = final_score if final_score is not None else early_score

    confidence, confidence_score = _confidence(final_sample, early_sample)
    band = _profile_band(composite, final_sample, early_sample)

    flags: list[str] = []
    strengths: list[str] = []
    if early_sample < 3:
        flags.append("İlk 15 dk örneği az")
    if final_sample < 5:
        flags.append("Kapanış örneği az")
    if early_close_r is not None and early_close_r < 0:
        flags.append("İlk 15 dk ortalama R negatif")
    if (_number(early_row.get("sl_first_rate")) or 0) > (_number(early_row.get("tp1_first_rate")) or 0):
        flags.append("SL ilk teması TP1'den yüksek")
    if final_sl_rate is not None and final_sl_rate >= 50:
        flags.append("Kapanışlarda SL oranı yüksek")

    if early_close_r is not None and early_close_r > 0:
        strengths.append("İlk 15 dk ortalama R pozitif")
    if (_number(early_row.get("tp1_first_rate")) or 0) > (_number(early_row.get("sl_first_rate")) or 0):
        strengths.append("TP1 ilk teması SL'den yüksek")
    if final_tp_rate is not None and final_tp_rate >= 50:
        strengths.append("Kapanışlarda TP oranı %50+ ")
    if average_final_r is not None and average_final_r > 0:
        strengths.append("Ortalama kesin R pozitif")

    return {
        "system": system,
        "label": earlyperf.SYSTEM_LABELS.get(system, system),
        "score": composite,
        "band": band,
        "band_label": PROFILE_LABELS[band],
        "confidence": confidence,
        "confidence_label": CONFIDENCE_LABELS[confidence],
        "confidence_score": confidence_score,
        "final": {
            "sample": final_sample,
            "tp_rate": final_tp_rate,
            "sl_rate": final_sl_rate,
            "exact_r_sample": exact_r_sample,
            "net_r": net_r,
            "average_r": average_final_r,
            "score": final_score,
        },
        "early": {
            "sample": early_sample,
            "tp1_first_rate": _number(early_row.get("tp1_first_rate")),
            "sl_first_rate": _number(early_row.get("sl_first_rate")),
            "positive_close_rate": early_positive,
            "average_mfe_r": _number(early_row.get("average_mfe_r")),
            "average_mae_r": _number(early_row.get("average_mae_r")),
            "average_close_r": early_close_r,
            "score": early_score,
        },
        "strengths": strengths[:4],
        "flags": flags[:4],
        "disclaimer": "Gözlemsel puandır; canlı filtre veya işlem kararı değildir.",
    }


def build_quality_payload(data: dict[str, Any], early_payload: dict[str, Any]) -> dict[str, Any]:
    final_map = _final_performance_by_system(data)
    early_rows = early_payload.get("systems") if isinstance(early_payload.get("systems"), list) else []
    early_map = {
        str(row.get("system") or "").upper(): row
        for row in early_rows
        if isinstance(row, dict)
    }
    profiles = [
        build_system_profile(system, final_map.get(system, {}), early_map.get(system, {}))
        for system in earlyperf.SYSTEM_ORDER
    ]
    scored = [row for row in profiles if row.get("score") is not None and row.get("band") != "DATA_INSUFFICIENT"]
    ranked = sorted(scored, key=lambda row: (float(row["score"]), float(row["confidence_score"])), reverse=True)
    avg_score = round(sum(float(row["score"]) for row in scored) / len(scored), 2) if scored else None
    return {
        "version": VERSION,
        "generated_at": int(time.time()),
        "profiles": profiles,
        "measured_systems": len(scored),
        "average_score": avg_score,
        "best_observed": ranked[0] if ranked else None,
        "note": "V3.19 puanı kapanış performansı ile ilk 15 dakika davranışını birlikte özetleyen gözlemsel panel metriğidir.",
        "safety": {
            "signal_engine": "unchanged",
            "telegram": "unchanged",
            "trade_management": "unchanged",
            "ledger_write": "unchanged",
            "automatic_filter": False,
        },
    }


PAGE = r'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="dark"><title>Sistem Kalite Profili</title><style>
:root{--bg:#061016;--panel:#0a1921;--line:#1b3943;--text:#eef8f6;--muted:#7f9d99;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59;--blue:#69a9ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 84% -10%,rgba(105,169,255,.12),transparent 30%),radial-gradient(circle at 14% 0,rgba(44,230,191,.08),transparent 24%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}.shell{width:min(1280px,calc(100% - 24px));margin:auto;padding:20px 0 70px}.top{display:flex;align-items:center;gap:8px;margin-bottom:16px}.btn{border:1px solid var(--line);background:#091821;color:#9db6b2;border-radius:10px;padding:9px 11px;font-weight:850;text-decoration:none;cursor:pointer}.btn:hover{border-color:var(--teal);color:var(--teal)}.spacer{flex:1}.hero{border:1px solid rgba(105,169,255,.22);background:linear-gradient(135deg,rgba(12,31,43,.98),rgba(7,20,27,.97));border-radius:20px;padding:18px}.eyebrow{font-size:9px;color:var(--blue);font-weight:900;letter-spacing:.08em}.hero h1{margin:4px 0 5px;font-size:28px;letter-spacing:-.04em}.hero p{margin:0;color:var(--muted);max-width:880px}.summary{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:13px}.metric,.card{border:1px solid var(--line);background:rgba(10,25,33,.96);border-radius:14px}.metric{padding:11px}.metric small{display:block;color:#6d8986;font-size:8px;text-transform:uppercase}.metric b{display:block;font-size:17px;margin-top:3px}.profiles{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:13px}.card{padding:13px}.card-top{display:flex;justify-content:space-between;gap:10px;align-items:flex-start}.card h2{margin:0;font-size:14px}.sample{color:var(--muted);font-size:8px}.score{font-size:28px;font-weight:950;letter-spacing:-.05em}.band{font-size:9px;font-weight:900}.strong{color:var(--green)}.balanced{color:var(--teal)}.watch{color:var(--amber)}.risky{color:var(--red)}.insufficient{color:var(--muted)}.track{height:8px;border:1px solid rgba(60,92,100,.65);border-radius:999px;background:#06141b;overflow:hidden;margin:10px 0}.fill{height:100%;background:linear-gradient(90deg,var(--red),var(--amber),var(--green));border-radius:999px}.sections{display:grid;grid-template-columns:1fr 1fr;gap:7px}.box{background:#07151c;border-radius:10px;padding:8px}.box small{display:block;color:#607d79;font-size:7px}.box b{font-size:10px}.list{margin:9px 0 0;padding:0;list-style:none;display:grid;gap:4px}.list li{font-size:8px;color:#829b97;padding-left:10px;position:relative}.list li:before{content:"•";position:absolute;left:0}.list.good li:before{color:var(--green)}.list.bad li:before{color:var(--amber)}.confidence{margin-top:8px;padding-top:8px;border-top:1px solid rgba(27,57,67,.68);display:flex;justify-content:space-between;gap:8px;color:#6f8c88;font-size:8px}.note{margin-top:13px;color:#617f7b;font-size:9px}.empty{padding:28px;text-align:center;color:var(--muted);grid-column:1/-1}@media(max-width:850px){.profiles{grid-template-columns:1fr}}@media(max-width:620px){.shell{width:calc(100% - 14px);padding-top:8px}.top{flex-wrap:wrap}.hero{padding:13px;border-radius:15px}.hero h1{font-size:22px}.summary{display:flex;overflow:auto}.metric{flex:0 0 145px}.sections{grid-template-columns:1fr 1fr}}
</style></head><body><div class="shell"><div class="top"><a class="btn" href="/">← Panel</a><a class="btn" href="/early-performance">15 dk Analizi</a><a class="btn" href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a><div class="spacer"></div><button class="btn" id="refreshBtn">Yenile</button></div><section class="hero"><div class="eyebrow">V3.19 · GÖZLEMSEL KALİTE PROFİLİ</div><h1>Sistem Kalite Profili</h1><p>Kapanış performansı ile ilk 15 dakika davranışını tek profilde birleştirir. Puanlar olasılık, işlem önerisi veya otomatik filtre değildir; örnek sayısı büyüdükçe veri güveni ayrıca yükselir.</p></section><div class="summary"><div class="metric"><small>Ölçülen sistem</small><b id="measured">—</b></div><div class="metric"><small>Ortalama kalite puanı</small><b id="average">—</b></div><div class="metric"><small>En güçlü gözlem</small><b id="best">—</b></div></div><div class="profiles" id="profiles"><div class="empty">Kalite profilleri hazırlanıyor…</div></div><div class="note" id="note">Puanların hiçbiri canlı sinyal motoruna geri yazılmaz.</div></div><script nonce="__NONCE__">(()=>{'use strict';const $=id=>document.getElementById(id);const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};const pct=v=>{const n=num(v);return n===null?'—':`%${n.toFixed(1)}`};const r=v=>{const n=num(v);return n===null?'—':`${n>=0?'+':''}${n.toFixed(2)}R`};const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));const cls=b=>({STRONG:'strong',BALANCED:'balanced',WATCH:'watch',RISKY:'risky',DATA_INSUFFICIENT:'insufficient'})[b]||'insufficient';async function get(){const res=await fetch(`/api/system-quality?v=${Date.now()}`,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(res.status===401){location.assign('/login');throw new Error('Oturum gerekli')}if(res.status===403){location.assign('/premium');throw new Error('Premium gerekli')}const p=await res.json();if(!res.ok)throw new Error(p.message||p.error||`HTTP ${res.status}`);return p}function render(p){$('measured').textContent=p.measured_systems??0;$('average').textContent=p.average_score==null?'—':`${Number(p.average_score).toFixed(1)}/100`;$('best').textContent=p.best_observed?.label||'—';$('note').textContent=p.note||'Puanlar salt okunur gözlemdir.';const rows=p.profiles||[];$('profiles').innerHTML=rows.map(x=>{const f=x.final||{},e=x.early||{},score=num(x.score),strengths=(x.strengths||[]).map(v=>`<li>${esc(v)}</li>`).join(''),flags=(x.flags||[]).map(v=>`<li>${esc(v)}</li>`).join('');return `<section class="card"><div class="card-top"><div><h2>${esc(x.label)}</h2><div class="sample">${f.sample||0} kapanış · ${e.sample||0} ilk-15dk örneği</div></div><div style="text-align:right"><div class="score ${cls(x.band)}">${score===null?'—':score.toFixed(0)}</div><div class="band ${cls(x.band)}">${esc(x.band_label)}</div></div></div><div class="track"><div class="fill" style="width:${score===null?0:Math.max(0,Math.min(100,score))}%"></div></div><div class="sections"><div class="box"><small>Kapanış TP / SL</small><b>${pct(f.tp_rate)} / ${pct(f.sl_rate)}</b></div><div class="box"><small>Ort. kesin R</small><b>${r(f.average_r)}</b></div><div class="box"><small>15dk pozitif kapanış</small><b>${pct(e.positive_close_rate)}</b></div><div class="box"><small>15dk ort. R</small><b>${r(e.average_close_r)}</b></div><div class="box"><small>TP1 önce / SL önce</small><b>${pct(e.tp1_first_rate)} / ${pct(e.sl_first_rate)}</b></div><div class="box"><small>MFE / MAE</small><b>${r(e.average_mfe_r)} / ${r(e.average_mae_r)}</b></div></div>${strengths?`<ul class="list good">${strengths}</ul>`:''}${flags?`<ul class="list bad">${flags}</ul>`:''}<div class="confidence"><span>${esc(x.confidence_label)}</span><span>Veri güveni ${pct(x.confidence_score)}</span></div></section>`}).join('')||'<div class="empty">Henüz kalite profili üretmek için yeterli veri yok.</div>'}async function load(){const b=$('refreshBtn');b.disabled=true;b.textContent='Hesaplanıyor…';try{render(await get())}catch(e){$('profiles').innerHTML=`<div class="empty">Kalite profili alınamadı: ${esc(e.message)}</div>`}finally{b.disabled=false;b.textContent='Yenile'}}$('refreshBtn').addEventListener('click',load);load()})();</script></body></html>'''


def page(nonce: str) -> str:
    return PAGE.replace("__NONCE__", html.escape(str(nonce), quote=True))


def enhance_navigation(body: str, path: str) -> str:
    if 'href="/system-quality"' in body:
        return body
    if path == "/early-performance":
        anchor = '<a class="btn" href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a>'
        if anchor in body:
            return body.replace(anchor, anchor + '<a class="btn" href="/system-quality">Kalite Profili</a>', 1)
    if path == "/coin-center":
        anchor = '<button class="fav" id="favBtn"'
        if anchor in body:
            return body.replace(anchor, '<a class="back" href="/system-quality">Kalite Profili</a>' + anchor, 1)
    if path == "/":
        anchor = '<a class="nav-item" href="/market-center">'
        if anchor in body:
            return body.replace(anchor, '<a class="nav-item" href="/system-quality"><span>◆</span><b>Kalite</b></a>' + anchor, 1)
        anchor = '<a href="/market-center">Piyasayı incele</a>'
        if anchor in body:
            return body.replace(anchor, anchor + '<a href="/system-quality">Sistem Kalitesi</a>', 1)
    return body


def make_v319_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None, history_cache: earlyperf.HistoricalPulseCache | None = None):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = earlyperf.make_v318_handler(config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache)

    class V319Handler(BaseHandler):
        server_version = "KriptoPanel/3.19"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path in {"/", "/coin-center", "/early-performance"}:
                    session = self._session()
                    if session and self._is_premium(session):
                        body = enhance_navigation(body, path)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status":"ok","version":VERSION,"system_quality":True,"early_performance":True,
                    "observational_score":True,"confidence_band":True,"automatic_filter":False,
                    "signal_engine":"unchanged","telegram":"unchanged","trade_management":"unchanged","ledger_write":"unchanged",
                })
                return
            if path == "/system-quality":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                if not self._is_premium(session):
                    self._redirect("/premium")
                    return
                nonce = secrets.token_urlsafe(18)
                self._send(HTTPStatus.OK, page(nonce), "text/html; charset=utf-8", nonce=nonce)
                return
            if path == "/api/system-quality":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error":"authentication_required"})
                    return
                if not self._is_premium(session):
                    self._json(HTTPStatus.FORBIDDEN, {"error":"premium_required","upgrade":"/premium"})
                    return
                try:
                    data = service.get_data()
                    early_payload = earlyperf.build_history_payload(data, candle_client, cache)
                    self._json(HTTPStatus.OK, build_quality_payload(data, early_payload))
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error":"system_quality_unavailable"})
                return
            return super().do_GET()

    return V319Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.19 Sistem Kalite Profili")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root)); config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter(); store = lifecycle.lifecycle_store_from_env(config)
    market_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=2)
    history_cache = earlyperf.HistoricalPulseCache()
    handler = make_v319_handler(config, service, sessions, limiter, store, market_client, overview_client, history_cache)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} system_quality=on automatic_filter=off signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
