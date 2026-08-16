"""Kripto Kontrol Merkezi V3.24 - Anlaşılır Sinyal Rehberi.

V3.23 üye odaklı ana ekranını korur. Yalnız panel katmanında çalışan sistemin
mevcut açık sinyal kayıtlarından güvenli bir açıklama özeti üretir:
- sinyalin kaydedilmiş trend/onay/giriş gerekçesi,
- kalite ve dikkat notu,
- mevcut fiyatın girişe göre salt-okunur konumu,
- SL mesafesi ve gönderim anındaki giriş yakınlığı.

Üyeye iç risk motoru, market guard, gölge kurallar ve sürüm/metaveri ayrıntıları
açılmaz. Sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger yazımı
ve otomatik filtre davranışı değiştirilmez.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_builder as builder
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_live_app as live
import dashboard_market_app as market
import dashboard_memberfocus_app as memberfocus
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_24_SIGNAL_GUIDE_2026_08_16"

SAFE_DETAIL_KEYS = (
    "quality",
    "quality_note",
    "trend_reason",
    "confirm_reason",
    "entry_reason",
    "ideal_entry",
    "zone_name",
    "zone_distance_percent",
    "entry_distance_at_send_percent",
    "risk_percent",
    "leverage",
)


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json_document(root: Path, filename: str) -> dict[str, Any]:
    try:
        value = json.loads((root / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _raw_open_rows(root: Path) -> list[tuple[str, dict[str, Any]]]:
    rows: list[tuple[str, dict[str, Any]]] = []

    premium = _json_document(root, "open_signals.json")
    for record in premium.values():
        if isinstance(record, dict) and not bool(record.get("closed")):
            rows.append(("PREMIUM", record))

    scalp = _json_document(root, "scalp_radar_state.json")
    scalp_open = scalp.get("open_scalp_signals") if isinstance(scalp, dict) else None
    if isinstance(scalp_open, dict):
        for record in scalp_open.values():
            if isinstance(record, dict) and not bool(record.get("closed")):
                rows.append(("SCALP", record))

    pump = _json_document(root, "pump_radar_state.json")
    if isinstance(pump, dict):
        seen: set[str] = set()
        for bucket_name in ("open_pump_signals", "open_signals"):
            bucket = pump.get(bucket_name)
            if not isinstance(bucket, dict):
                continue
            for key, record in bucket.items():
                if not isinstance(record, dict) or bool(record.get("closed")):
                    continue
                identity = str(record.get("trade_id") or record.get("id") or key)
                if identity in seen:
                    continue
                seen.add(identity)
                rows.append(("PUMP_DUMP", record))
    return rows


def _raw_id(record: dict[str, Any]) -> str:
    for key in ("trade_id", "performance_record_id", "id", "record_id"):
        value = record.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _raw_symbol(record: dict[str, Any]) -> str:
    value = str(record.get("display_symbol") or record.get("symbol") or "").upper()
    return value.replace("/USDT:USDT", "USDT").replace("/", "")


def _safe_details(record: dict[str, Any]) -> dict[str, Any]:
    details: dict[str, Any] = {}
    for key in SAFE_DETAIL_KEYS:
        value = record.get(key)
        if isinstance(value, (str, int, float, bool)) and value not in (None, ""):
            details[key] = value
    return details


def enrich_open_trade_details(data: dict[str, Any], root: Path | str) -> dict[str, Any]:
    """Whitelisted signal context is copied into panel rows; internal engines stay hidden."""
    root_path = Path(root)
    raw_rows = _raw_open_rows(root_path)
    by_id: dict[tuple[str, str], dict[str, Any]] = {}
    by_symbol: dict[tuple[str, str, str], dict[str, Any]] = {}
    for system, record in raw_rows:
        identity = _raw_id(record)
        if identity:
            by_id[(system, identity)] = record
        symbol = _raw_symbol(record)
        direction = str(record.get("direction") or "").upper()
        if symbol and direction:
            by_symbol[(system, symbol, direction)] = record

    open_rows = data.get("open_trades") if isinstance(data.get("open_trades"), list) else []
    for row in open_rows:
        if not isinstance(row, dict):
            continue
        system = str(row.get("system") or "").upper()
        record = by_id.get((system, str(row.get("id") or "")))
        if record is None:
            record = by_symbol.get((
                system,
                str(row.get("symbol") or "").upper(),
                str(row.get("direction") or "").upper(),
            ))
        if record is None:
            continue
        row.update(_safe_details(record))
    return data


def build_dashboard_data_v324(root: Path | str, now=None) -> dict[str, Any]:
    data = builder.build_dashboard_data(root, now=now)
    return enrich_open_trade_details(data, root)


# LiveDashboardService resolves this global at call time. V3.24 replaces only the
# panel data builder; the trading programs and their files are untouched.
live.build_dashboard_data = build_dashboard_data_v324


def _reason_lines(row: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("trend_reason", "confirm_reason", "entry_reason"):
        text = str(row.get(key) or "").strip()
        if text and text not in values:
            values.append(text)
    if not values:
        source = str(row.get("source") or "").strip()
        if source and source not in {"—", "Canlı Sinyal"}:
            values.append(f"{source} koşullarıyla oluşan sistem sinyali")
        else:
            values.append("Çalışan sistemin koşullarını geçen canlı sinyal kaydı")
    return values[:3]


def _timing(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("tp3_hit"):
        return {"label": "TP3 görüldü", "tone": "done", "detail": "Teknik senaryo üçüncü hedefe kadar ilerlemiş."}
    if row.get("tp2_hit"):
        return {"label": "TP2 görüldü", "tone": "done", "detail": "Teknik senaryo ikinci hedefe kadar ilerlemiş."}
    if row.get("tp1_hit"):
        return {"label": "TP1 görüldü", "tone": "done", "detail": "İlk hedef görülmüş; bu kart yeni giriş çağrısı değildir."}

    entry = _number(row.get("entry"))
    last = _number(row.get("last_price"))
    sl = _number(row.get("sl"))
    direction = str(row.get("direction") or "").upper()
    if entry in (None, 0) or last is None or sl is None or direction not in {"LONG", "SHORT"}:
        return {"label": "Konum ölçülemiyor", "tone": "neutral", "detail": "Güncel fiyat / giriş bilgisi yeterli değil."}

    risk = abs(entry - sl)
    if risk <= 0:
        return {"label": "Konum ölçülemiyor", "tone": "neutral", "detail": "Giriş-SL risk mesafesi bulunamadı."}
    favorable = (last - entry) if direction == "LONG" else (entry - last)
    live_r = favorable / risk
    gap_pct = abs(last - entry) / abs(entry) * 100

    if live_r >= 0.60:
        label, tone = "Girişten uzaklaşmış", "warn"
        detail = f"Fiyat olumlu yönde yaklaşık {live_r:.2f}R ileride; giriş mesafesi %{gap_pct:.2f}."
    elif live_r >= 0.20:
        label, tone = "Hareket başlamış", "watch"
        detail = f"Fiyat girişten olumlu yönde yaklaşık {live_r:.2f}R ileride; mesafe %{gap_pct:.2f}."
    elif live_r <= -0.45:
        label, tone = "Ters hareket var", "risk"
        detail = f"Fiyat girişten ters yönde yaklaşık {abs(live_r):.2f}R; mesafe %{gap_pct:.2f}."
    else:
        label, tone = "Giriş bölgesine yakın", "good"
        detail = f"Fiyat giriş çevresinde; girişe mutlak mesafe yaklaşık %{gap_pct:.2f}."
    return {"label": label, "tone": tone, "detail": detail, "live_r": round(live_r, 3), "gap_percent": round(gap_pct, 3)}


def _sent_timing(row: dict[str, Any]) -> str | None:
    distance = _number(row.get("entry_distance_at_send_percent"))
    if distance is None:
        distance = _number(row.get("zone_distance_percent"))
    if distance is None:
        return None
    distance = abs(distance)
    if distance <= 0.25:
        return f"Gönderimde girişe çok yakındı (%{distance:.2f})."
    if distance <= 0.75:
        return f"Gönderimde giriş bölgesine yakındı (%{distance:.2f})."
    return f"Gönderimde girişten belirgin uzaktı (%{distance:.2f})."


def _risk_distance(row: dict[str, Any]) -> dict[str, Any]:
    stop = _number(row.get("stop_percent"))
    if stop is None:
        stop = _number(row.get("risk_percent"))
    if stop is None:
        return {"label": "SL mesafesi bilinmiyor", "tone": "neutral", "value": None}
    stop = abs(stop)
    if stop <= 1.0:
        label, tone = "SL mesafesi dar", "good"
    elif stop <= 1.8:
        label, tone = "SL mesafesi orta", "watch"
    else:
        label, tone = "SL mesafesi geniş", "warn"
    return {"label": label, "tone": tone, "value": round(stop, 3)}


def build_signal_guidance(data: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    rows = data.get("open_trades") if isinstance(data.get("open_trades"), list) else []
    output: list[dict[str, Any]] = []
    for raw in rows[: max(1, int(limit))]:
        if not isinstance(raw, dict):
            continue
        score = _number(raw.get("score"))
        quality = str(raw.get("quality") or "").strip()
        quality_label = quality or (f"Skor {score:.0f}" if score is not None else "Kalite kaydı yok")
        output.append({
            "id": str(raw.get("id") or ""),
            "symbol": str(raw.get("symbol") or "—"),
            "direction": str(raw.get("direction") or "—").upper(),
            "system": str(raw.get("system_label") or raw.get("system") or "Sistem"),
            "quality": quality_label,
            "score": score,
            "reasons": _reason_lines(raw),
            "timing": _timing(raw),
            "sent_timing": _sent_timing(raw),
            "risk": _risk_distance(raw),
            "note": str(raw.get("quality_note") or "").strip() or None,
            "leverage": str(raw.get("leverage") or "").strip() or None,
        })
    return output


CSS = r'''
.v324-guide{margin:0 0 16px;border:1px solid rgba(105,169,255,.22);border-radius:16px;background:#08171f;padding:14px}.v324-guide-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;margin-bottom:9px}.v324-guide-head span{display:block;color:#69a9ff;font-size:8px;font-weight:950;letter-spacing:.08em}.v324-guide-head h2{margin:2px 0;font-size:16px}.v324-guide-head p{margin:0;color:#76908d;font-size:9px}.v324-guide-note{border:1px solid #24414b;border-radius:999px;padding:5px 8px;color:#84a19d;font-size:7px;font-weight:900;white-space:nowrap}.v324-list{display:grid;grid-template-columns:1fr 1fr;gap:8px}.v324-card{border:1px solid #1b3943;border-radius:12px;background:#06141b;padding:10px}.v324-top{display:flex;justify-content:space-between;gap:8px;align-items:center}.v324-coin b{font-size:12px}.v324-coin small{display:block;color:#6f8986;font-size:8px}.v324-dir{border:1px solid currentColor;border-radius:999px;padding:3px 6px;font-size:7px;font-weight:950}.v324-dir.long{color:#42e28c}.v324-dir.short{color:#ff7189}.v324-badges{display:flex;gap:5px;flex-wrap:wrap;margin:8px 0}.v324-pill{border:1px solid #27434b;border-radius:999px;padding:4px 6px;color:#8eaaa6;font-size:7px;font-weight:850}.v324-pill.good,.v324-pill.done{color:#42e28c;border-color:rgba(66,226,140,.28)}.v324-pill.watch{color:#69a9ff;border-color:rgba(105,169,255,.28)}.v324-pill.warn{color:#ffbd59;border-color:rgba(255,189,89,.28)}.v324-pill.risk{color:#ff7189;border-color:rgba(255,113,137,.28)}.v324-why{border-top:1px solid rgba(27,57,67,.7);padding-top:7px;margin-top:4px}.v324-why strong{display:block;font-size:8px;color:#8ba6a2;margin-bottom:3px}.v324-why div{color:#b6c9c6;font-size:8px;margin:2px 0}.v324-status{margin-top:7px;color:#7f9995;font-size:8px;line-height:1.45}.v324-caution{margin-top:7px;border:1px solid rgba(255,189,89,.18);background:rgba(255,189,89,.035);border-radius:8px;padding:6px;color:#cbb181;font-size:7px}.v324-empty{grid-column:1/-1;border:1px dashed #24404a;border-radius:11px;padding:18px;text-align:center;color:#708b87;font-size:9px}@media(max-width:760px){.v324-list{grid-template-columns:1fr}.v324-guide{padding:12px;border-radius:14px}.v324-guide-head{flex-direction:column}.v324-guide-note{align-self:flex-start}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v324-signal-guide-script">
(()=>{'use strict';if(window.__v324SignalGuide)return;window.__v324SignalGuide=true;
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};
const first=(r,keys)=>{for(const k of keys){if(r?.[k]!==undefined&&r?.[k]!==null&&r?.[k]!=='')return r[k]}return null};
function timing(r){if(r.tp3_hit)return['TP3 görüldü','done','Teknik senaryo üçüncü hedefe kadar ilerlemiş.'];if(r.tp2_hit)return['TP2 görüldü','done','Teknik senaryo ikinci hedefe kadar ilerlemiş.'];if(r.tp1_hit)return['TP1 görüldü','done','İlk hedef görülmüş; bu kart yeni giriş çağrısı değildir.'];const e=num(r.entry),p=num(r.last_price),s=num(r.sl),d=String(r.direction||'').toUpperCase();if(!e||p===null||s===null||!['LONG','SHORT'].includes(d))return['Konum ölçülemiyor','neutral','Güncel fiyat / giriş bilgisi yeterli değil.'];const risk=Math.abs(e-s);if(!risk)return['Konum ölçülemiyor','neutral','Giriş-SL mesafesi bulunamadı.'];const rr=(d==='LONG'?p-e:e-p)/risk,gap=Math.abs(p-e)/Math.abs(e)*100;if(rr>=.6)return['Girişten uzaklaşmış','warn',`Fiyat olumlu yönde yaklaşık ${rr.toFixed(2)}R ileride; giriş mesafesi %${gap.toFixed(2)}.`];if(rr>=.2)return['Hareket başlamış','watch',`Fiyat girişten olumlu yönde yaklaşık ${rr.toFixed(2)}R ileride; mesafe %${gap.toFixed(2)}.`];if(rr<=-.45)return['Ters hareket var','risk',`Fiyat girişten ters yönde yaklaşık ${Math.abs(rr).toFixed(2)}R; mesafe %${gap.toFixed(2)}.`];return['Giriş bölgesine yakın','good',`Fiyat giriş çevresinde; girişe mutlak mesafe yaklaşık %${gap.toFixed(2)}.`]}
function risk(r){const v=Math.abs(num(first(r,['stop_percent','risk_percent']))??NaN);if(!Number.isFinite(v))return['SL mesafesi bilinmiyor','neutral',''];if(v<=1)return['SL mesafesi dar','good',`%${v.toFixed(2)}`];if(v<=1.8)return['SL mesafesi orta','watch',`%${v.toFixed(2)}`];return['SL mesafesi geniş','warn',`%${v.toFixed(2)}`]}
function sent(r){const n=num(first(r,['entry_distance_at_send_percent','zone_distance_percent']));if(n===null)return'';const d=Math.abs(n);return d<=.25?`Gönderimde girişe çok yakındı (%${d.toFixed(2)}).`:d<=.75?`Gönderimde giriş bölgesine yakındı (%${d.toFixed(2)}).`:`Gönderimde girişten belirgin uzaktı (%${d.toFixed(2)}).`}
function reasons(r){const a=['trend_reason','confirm_reason','entry_reason'].map(k=>String(r?.[k]||'').trim()).filter(Boolean);if(a.length)return[...new Set(a)].slice(0,3);const source=String(r?.source||'').trim();return[source&&source!=='Canlı Sinyal'?`${source} koşullarıyla oluşan sistem sinyali`:'Çalışan sistemin koşullarını geçen canlı sinyal kaydı']}
function card(r){const [tl,tt,td]=timing(r),[rl,rt,rv]=risk(r),dir=String(r.direction||'—').toUpperCase(),q=String(r.quality||'').trim()||(num(r.score)!==null?`Skor ${Math.round(num(r.score))}`:'Kalite kaydı yok'),why=reasons(r).map(x=>`<div>• ${esc(x)}</div>`).join(''),st=sent(r),note=String(r.quality_note||'').trim();return `<article class="v324-card"><div class="v324-top"><div class="v324-coin"><b>${esc(r.symbol||'—')}</b><small>${esc(r.system_label||r.system||'Sistem')}</small></div><span class="v324-dir ${dir==='LONG'?'long':dir==='SHORT'?'short':''}">${esc(dir)}</span></div><div class="v324-badges"><span class="v324-pill">${esc(q)}</span><span class="v324-pill ${tt}">${esc(tl)}</span><span class="v324-pill ${rt}">${esc(rl)}${rv?' · '+esc(rv):''}</span></div><div class="v324-why"><strong>NEDEN GELDİ?</strong>${why}</div><div class="v324-status">${esc(td)}${st?' '+esc(st):''}</div>${note?`<div class="v324-caution">Dikkat notu: ${esc(note)}</div>`:''}</article>`}
async function load(){const root=document.getElementById('v324GuideList');if(!root)return;try{const r=await fetch('/api/dashboard',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');return}const data=await r.json();if(!r.ok)throw new Error('HTTP '+r.status);const rows=Array.isArray(data.open_trades)?data.open_trades.slice(0,5):[];root.innerHTML=rows.length?rows.map(card).join(''):'<div class="v324-empty">Şu anda açık sinyal yok. Yeni canlı sinyal geldiğinde açıklaması burada görünecek.</div>'}catch(e){root.innerHTML='<div class="v324-empty">Sinyal açıklaması geçici olarak alınamadı; mevcut ana panel verileri kullanılmaya devam ediyor.</div>'}}
setTimeout(load,900);setInterval(load,30000);
})();
</script>
'''


def signal_guide_block() -> str:
    return '<section class="v324-guide" id="v324SignalGuide"><div class="v324-guide-head"><div><span>CANLI SİNYALİ ANLA</span><h2>Sinyal Rehberi</h2><p>Teknik kaydı daha sade dille açıklar; otomatik işlem veya kesin kazanç önerisi değildir.</p></div><div class="v324-guide-note">SİSTEM KAYDI + HESAPLANAN KONUM</div></div><div class="v324-list" id="v324GuideList"><div class="v324-empty">Sinyal açıklamaları hazırlanıyor…</div></div></section>'


def enhance_signal_guide(body: str, nonce: str) -> str:
    if 'id="v324SignalGuide"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    marker = '<div class="summary" id="homeMetrics"></div>'
    if marker in body:
        body = body.replace(marker, signal_guide_block() + marker, 1)
    elif 'id="v323MemberFocus"' in body:
        end = body.find('</section>', body.find('id="v323MemberFocus"'))
        if end >= 0:
            end += len('</section>')
            body = body[:end] + signal_guide_block() + body[end:]
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v324_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store,
    market_client=None,
    overview_client=None,
    history_cache: earlyperf.HistoricalPulseCache | None = None,
):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = memberfocus.make_v323_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V324Handler(BaseHandler):
        server_version = "KriptoPanel/3.24"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    is_admin = bool(session) and str(session.get("role") or "").upper() == commercial.ROLE_ADMIN
                    if session and not is_admin:
                        body = enhance_signal_guide(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status":"ok","version":VERSION,"signal_guide":True,"safe_signal_context":True,
                    "internal_strategy_details_exposed":False,"automatic_filter":False,
                    "signal_engine":"unchanged","telegram":"unchanged","trade_management":"unchanged",
                    "ledger_write":"unchanged",
                })
                return
            return super().do_GET()

    return V324Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.24 Anlaşılır Sinyal Rehberi")
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
    handler = make_v324_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} signal_guide=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
