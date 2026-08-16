"""Kripto Kontrol Merkezi V3.26 - Canlı Veri Tazeliği.

V3.25 birleşik stabil paneli korur. Panelde sinyal oluşma zamanını sistemin
çalışma/kayıt tazeliği ile karıştırmamak için kaynak bazlı heartbeat üretir.
Gölge/simülasyon zamanları canlı Pump/Dump heartbeat hesabına dahil edilmez.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger yazımı ve
otomatik filtre davranışı değiştirilmez.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import urllib.parse
from datetime import datetime, timezone
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
import dashboard_signalguide_app as signalguide
import dashboard_stable_app as stable
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_26_DATA_HEALTH_2026_08_16"

# 5 dakikalık botlar için GitHub zamanlayıcı gecikmesine pay bırakılır.
FAST_FRESH_MINUTES = 20
FAST_DELAYED_MINUTES = 45
CONTROL_FRESH_MINUTES = 90
CONTROL_DELAYED_MINUTES = 150


def _document(root: Path, filename: str) -> dict[str, Any]:
    try:
        value = json.loads((root / filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _timestamp(value: Any) -> int:
    return builder.parse_timestamp(value)


def _top_timestamp(document: dict[str, Any], *keys: str) -> int:
    return max((_timestamp(document.get(key)) for key in keys), default=0)


def _container_timestamp(document: dict[str, Any], *keys: str) -> int:
    latest = 0
    for key in keys:
        value = document.get(key)
        if isinstance(value, dict):
            for child in value.values():
                if not isinstance(child, (dict, list)):
                    latest = max(latest, _timestamp(child))
    return latest


def _bucket_record_timestamp(document: dict[str, Any], bucket: str, *fields: str) -> int:
    value = document.get(bucket)
    rows = value.values() if isinstance(value, dict) else value if isinstance(value, list) else []
    latest = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field in fields:
            latest = max(latest, _timestamp(row.get(field)))
    return latest


def _premium_heartbeat(root: Path) -> int:
    ledger = _document(root, "trade_ledger.json")
    state = _document(root, "open_signals.json")
    state_rows = state.values() if isinstance(state, dict) else []
    latest_state = 0
    for row in state_rows:
        if not isinstance(row, dict):
            continue
        latest_state = max(
            latest_state,
            _top_timestamp(row, "last_checked_at", "last_tracking_at", "updated_at", "opened_at"),
        )
    return max(_top_timestamp(ledger, "last_update", "updated_at"), latest_state)


def _scalp_heartbeat(root: Path) -> int:
    ledger = _document(root, "scalp_performance_ledger.json")
    state = _document(root, "scalp_radar_state.json")
    return max(
        _top_timestamp(ledger, "updated_at", "last_update"),
        _container_timestamp(state, "last_sent", "early_last_sent", "prewatch_last_sent"),
        _bucket_record_timestamp(state, "open_scalp_signals", "last_checked_at", "last_tracking_at"),
    )


def _pump_heartbeat(root: Path) -> int:
    ledger = _document(root, "pump_performance_ledger.json")
    state = _document(root, "pump_radar_state.json")
    # Bilerek shadow_moves / recorded_at okunmaz. Gölge veri canlı radar heartbeat'i değildir.
    return max(
        _top_timestamp(ledger, "updated_at", "last_update"),
        _top_timestamp(state, "last_run", "updated_at"),
        _container_timestamp(state, "last_sent"),
        _bucket_record_timestamp(state, "open_pump_signals", "last_checked_at", "last_tracking_at"),
        _bucket_record_timestamp(state, "open_signals", "last_checked_at", "last_tracking_at"),
    )


def _new_listing_heartbeat(root: Path) -> int:
    ledger = _document(root, "new_listing_performance_ledger.json")
    return _top_timestamp(ledger, "updated_at", "last_update", "generated_at")


def _control_heartbeat(root: Path) -> int:
    report = _document(root, "system_control_center_report.json")
    return _top_timestamp(report, "generated_at", "updated_at", "last_update")


def _status(heartbeat: int, now_ts: int, fresh_minutes: int, delayed_minutes: int) -> tuple[str, float | None]:
    if heartbeat <= 0:
        return "UNKNOWN", None
    age = max(0.0, (now_ts - heartbeat) / 60.0)
    if age <= fresh_minutes:
        return "FRESH", round(age, 1)
    if age <= delayed_minutes:
        return "DELAYED", round(age, 1)
    return "STALE", round(age, 1)


def build_system_freshness(root: Path | str, now: datetime | None = None) -> dict[str, Any]:
    root_path = Path(root)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    now_ts = int(current.timestamp())

    specs = (
        ("PREMIUM", "Premium MTF", _premium_heartbeat(root_path), 5, FAST_FRESH_MINUTES, FAST_DELAYED_MINUTES),
        ("SCALP", "Scalp Radar", _scalp_heartbeat(root_path), 5, FAST_FRESH_MINUTES, FAST_DELAYED_MINUTES),
        ("PUMP_DUMP", "Pump / Dump", _pump_heartbeat(root_path), 5, FAST_FRESH_MINUTES, FAST_DELAYED_MINUTES),
        ("NEW_LISTING", "Yeni Liste", _new_listing_heartbeat(root_path), 5, FAST_FRESH_MINUTES, FAST_DELAYED_MINUTES),
        ("SYSTEM_CONTROL", "Sistem Kontrol", _control_heartbeat(root_path), 60, CONTROL_FRESH_MINUTES, CONTROL_DELAYED_MINUTES),
    )

    rows = []
    for key, label, heartbeat, cadence, fresh_limit, delayed_limit in specs:
        status, age_minutes = _status(heartbeat, now_ts, fresh_limit, delayed_limit)
        rows.append({
            "key": key,
            "label": label,
            "status": status,
            "heartbeat_at": heartbeat or None,
            "age_minutes": age_minutes,
            "cadence_minutes": cadence,
            "fresh_limit_minutes": fresh_limit,
            "delayed_limit_minutes": delayed_limit,
        })

    live_rows = [row for row in rows if row["key"] != "SYSTEM_CONTROL"]
    if any(row["status"] == "STALE" for row in live_rows):
        overall = "ATTENTION"
    elif any(row["status"] in {"DELAYED", "UNKNOWN"} for row in live_rows):
        overall = "WATCH"
    else:
        overall = "FRESH"
    return {
        "overall": overall,
        "rows": rows,
        "note": "Sinyal adedi değil, sistem kayıtlarının tazeliği ölçülür; gölge kayıtlar canlı heartbeat sayılmaz.",
    }


def build_dashboard_data_v326(root: Path | str, now=None) -> dict[str, Any]:
    data = signalguide.build_dashboard_data_v324(root, now=now)
    data["system_freshness"] = build_system_freshness(root, now=now)
    return data


CSS = r'''
.v326-health{margin:0 0 12px;border:1px solid rgba(44,230,191,.22);border-radius:14px;background:#07171e;padding:11px}.v326-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:8px}.v326-head span{display:block;color:#2ce6bf;font-size:8px;font-weight:950;letter-spacing:.07em}.v326-head h3{margin:2px 0;font-size:13px}.v326-head p{margin:0;color:#718d89;font-size:8px}.v326-overall{border:1px solid #29444c;border-radius:999px;padding:5px 8px;font-size:7px;font-weight:950;white-space:nowrap}.v326-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:6px}.v326-item{border:1px solid #1b3943;border-radius:9px;background:#06131a;padding:7px}.v326-item b{display:block;font-size:8px}.v326-item small{display:block;margin-top:3px;color:#718985;font-size:7px}.v326-state{display:inline-block;margin-top:5px;border:1px solid currentColor;border-radius:999px;padding:3px 5px;font-size:6px;font-weight:950}.v326-state.FRESH{color:#42e28c}.v326-state.DELAYED{color:#ffbd59}.v326-state.STALE{color:#ff7189}.v326-state.UNKNOWN{color:#879997}@media(max-width:800px){.v326-grid{grid-template-columns:1fr 1fr}.v326-head{flex-direction:column}.v326-overall{align-self:flex-start}}@media(max-width:430px){.v326-grid{grid-template-columns:1fr}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v326-data-health-script">
(()=>{'use strict';if(window.__v326DataHealth)return;window.__v326DataHealth=true;
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const labels={FRESH:'GÜNCEL',DELAYED:'GECİKMELİ',STALE:'ESKİ',UNKNOWN:'BİLİNMİYOR'};
function age(row){if(row.age_minutes===null||row.age_minutes===undefined)return 'zaman kaydı yok';const n=Number(row.age_minutes);return n<1?'1 dk altında':`${n.toFixed(n<10?1:0)} dk önce`}
function draw(data){const root=document.getElementById('v326HealthGrid'),overall=document.getElementById('v326HealthOverall');if(!root)return;const h=data?.system_freshness||{},rows=Array.isArray(h.rows)?h.rows:[];if(overall)overall.textContent=h.overall==='FRESH'?'VERİ AKIŞI GÜNCEL':h.overall==='WATCH'?'VERİ AKIŞINI İZLE':'VERİ AKIŞINI KONTROL ET';root.innerHTML=rows.length?rows.map(r=>`<div class="v326-item"><b>${esc(r.label)}</b><small>${esc(age(r))} · beklenen ~${esc(r.cadence_minutes)} dk</small><span class="v326-state ${esc(r.status)}">${esc(labels[r.status]||r.status)}</span></div>`).join(''):'<div class="v326-item"><b>Veri durumu</b><small>Heartbeat bilgisi hazırlanıyor.</small></div>'}
async function load(){try{const r=await fetch('/api/dashboard',{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');return}const d=await r.json();if(r.ok)draw(d)}catch(e){}}
setTimeout(load,500);setInterval(load,30000);
})();
</script>
'''


def data_health_block() -> str:
    return '<section class="v326-health" id="v326DataHealth"><div class="v326-head"><div><span>CANLI VERİ KONTROLÜ</span><h3>Veri akışı gerçekten güncel mi?</h3><p>Son sinyal zamanını değil, sistemin kayıt heartbeat\'ini izler.</p></div><div class="v326-overall" id="v326HealthOverall">KONTROL EDİLİYOR</div></div><div class="v326-grid" id="v326HealthGrid"><div class="v326-item"><b>Veri tazeliği</b><small>Kontrol ediliyor…</small></div></div></section>'


def enhance_data_health(body: str, nonce: str) -> str:
    if 'id="v326DataHealth"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    marker = '<div class="summary" id="homeMetrics"></div>'
    if marker in body:
        body = body.replace(marker, data_health_block() + marker, 1)
    elif "<body>" in body:
        body = body.replace("<body>", "<body>" + data_health_block(), 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v326_handler(
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
    BaseHandler = stable.make_v325_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )
    # LiveDashboardService fonksiyonu çağrı anında dashboard_live_app globalini çözer.
    # Yalnız panel payload'ına system_freshness eklenir.
    live.build_dashboard_data = build_dashboard_data_v326

    class V326Handler(BaseHandler):
        server_version = "KriptoPanel/3.26"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    if session:
                        info = self._plan_info(session)
                        if str(info.get("plan") or "") != commercial.PLAN_FREE:
                            body = enhance_data_health(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "cumulative_ui": True,
                    "data_heartbeat": True,
                    "shadow_excluded_from_live_heartbeat": True,
                    "member_focus": "preserved",
                    "signal_guide": "preserved",
                    "admin_tools": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V326Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.26 Canlı Veri Tazeliği")
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
    handler = make_v326_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} data_heartbeat=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
