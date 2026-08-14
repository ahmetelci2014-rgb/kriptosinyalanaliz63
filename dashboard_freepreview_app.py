"""Kripto Kontrol Merkezi V3.3 - FREE gerçek ürün deneyimi.

V3.2 yönetim/oturum katmanını bozmadan FREE üyeye sınırlı ama gerçek değer sunar:
- aynı anda en yeni 1 gerçek açık sinyal: coin, yön, Entry, TP1 ve SL,
- son 5 gerçek kapanış sonucu,
- 6 büyük coin için canlı temel piyasa görünümü,
- diğer açık sinyaller, TP2/TP3, Fırsatlar, skor, sesli uyarı ve tam geçmiş Premium kalır.

FREE sinyal seçiminde sonuç bilgisi kullanılmaz; yalnız mevcut açık işlemler arasından en yeni kayıt seçilir.
Sinyal üretimi, strateji, radar, Telegram ve emir akışına dokunmaz.
"""

from __future__ import annotations

import argparse
import html
import math
import os
import re
import secrets
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_adminux_app as adminux
import dashboard_billing_app as billing
import dashboard_commercial_app as commercial
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_3_FREE_PREVIEW_2026_08_14"
FREE_MARKET_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT")
SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,18}USDT$")


def _safe_symbol(value: Any) -> str:
    symbol = str(value or "").upper().replace("-", "").replace("_", "")[:24]
    return symbol if SYMBOL_RE.fullmatch(symbol) else ""


def _safe_direction(value: Any) -> str:
    direction = str(value or "").upper()
    return direction if direction in {"LONG", "SHORT"} else ""


def _safe_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _row_timestamp(row: dict[str, Any]) -> int:
    for key in ("closed_at", "finalized_at", "opened_at", "sent_at", "created_at", "timestamp", "updated_at"):
        value = commercial._int(row.get(key), 0)
        if value > 0:
            return value
    return 0


def _system_label(row: dict[str, Any]) -> str:
    return commercial._safe_text(
        row.get("system_label") or row.get("system") or row.get("source") or "Sistem",
        60,
    )


def _free_signal_from_open(open_rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Sonuca bakmadan en yeni, seviyeleri okunabilir açık sinyali seçer."""
    candidates: list[dict[str, Any]] = []
    for row in open_rows:
        if not isinstance(row, dict):
            continue
        symbol = _safe_symbol(row.get("symbol"))
        direction = _safe_direction(row.get("direction"))
        entry = _safe_number(row.get("entry"))
        tp1 = _safe_number(row.get("tp1"))
        sl = _safe_number(row.get("sl"))
        if not symbol or not direction or entry is None or tp1 is None or sl is None:
            continue
        candidates.append(row)
    if not candidates:
        return None
    row = max(candidates, key=_row_timestamp)
    return {
        "symbol": _safe_symbol(row.get("symbol")),
        "direction": _safe_direction(row.get("direction")),
        "entry": _safe_number(row.get("entry")),
        "tp1": _safe_number(row.get("tp1")),
        "sl": _safe_number(row.get("sl")),
        "system": _system_label(row),
        "opened_at": _row_timestamp(row) or None,
    }


def build_free_preview(data: dict[str, Any]) -> dict[str, Any]:
    """FREE tarayıcıya yalnız izin verilen sınırlı işlem alanlarını verir."""
    open_rows = [
        row for row in (data.get("open_trades") if isinstance(data.get("open_trades"), list) else [])
        if isinstance(row, dict)
    ]
    free_signal = _free_signal_from_open(open_rows)

    result_source = [
        row for row in (data.get("recent_results") if isinstance(data.get("recent_results"), list) else [])
        if isinstance(row, dict)
    ]
    result_source.sort(key=_row_timestamp, reverse=True)
    results: list[dict[str, Any]] = []
    for row in result_source[:5]:
        symbol = _safe_symbol(row.get("symbol"))
        outcome = commercial._safe_text(commercial._result_outcome(row), 24)
        if not symbol or not outcome:
            continue
        results.append({
            "symbol": symbol,
            "direction": _safe_direction(row.get("direction")),
            "outcome": outcome,
            "system": _system_label(row),
            "closed_at": _row_timestamp(row) or None,
        })

    summary = commercial.build_public_summary(data)
    return {
        "version": VERSION,
        "plan": commercial.PLAN_FREE,
        "open_count": len(open_rows),
        "locked_open_count": max(0, len(open_rows) - (1 if free_signal else 0)),
        "free_signal": free_signal,
        "recent_results": results,
        "summary": {
            "tp_count": summary.get("tp_count", 0),
            "sl_count": summary.get("sl_count", 0),
            "be_count": summary.get("be_count", 0),
            "tp_rate_percent": summary.get("tp_rate_percent"),
            "health": summary.get("health", "UNKNOWN"),
        },
        "updated_at": int(time.time()),
        "limits": {
            "visible_open_signals": 1,
            "visible_results": 5,
            "tp2_tp3": False,
            "opportunity_center": False,
            "analysis_score": False,
            "audio_alerts": False,
        },
        "disclaimer": "FREE önizleme gerçek sistem kayıtlarını gösterir; kazanç garantisi değildir. Giriş seviyesi geçmiş olabilir.",
    }


def free_preview_page(session: dict[str, Any], nonce: str) -> str:
    username = html.escape(str(session.get("username") or "üye"))
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    nonce_attr = html.escape(nonce, quote=True)
    market_symbols = ",".join(FREE_MARKET_SYMBOLS)
    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kripto Kontrol · FREE</title>
<style>
:root{{--bg:#061016;--panel:#0b1b23;--panel2:#091820;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--blue:#69aef8;--amber:#ffbd59;--green:#42e28c;--red:#ff627d}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,rgba(44,230,191,.07),transparent 30%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}button{{font:inherit}}.shell{{width:min(1120px,calc(100% - 24px));margin:auto;padding:18px 0 54px}}.top{{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}}.brand{{font-weight:950;font-size:17px}}.actions{{display:flex;gap:7px;align-items:center;flex-wrap:wrap}}.btn,.actions button{{border:1px solid var(--line);border-radius:9px;padding:8px 11px;background:#0a1820;color:#c4d7d4;font-weight:850;font-size:10px;cursor:pointer}}.btn.primary{{background:var(--teal);color:#03110e;border-color:transparent}}form{{margin:0}}.badge{{display:inline-flex;padding:5px 8px;border:1px solid rgba(255,189,89,.28);color:var(--amber);border-radius:999px;font-weight:950;font-size:9px}}
.hero{{margin-top:18px;border:1px solid var(--line);border-radius:18px;padding:20px;background:linear-gradient(135deg,rgba(44,230,191,.055),rgba(105,174,248,.025)),var(--panel)}}h1{{font-size:29px;margin:9px 0 5px;letter-spacing:-.035em}}h2{{margin:0 0 5px;font-size:16px}}p{{color:var(--muted)}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:15px}}.metric{{border:1px solid var(--line);background:var(--panel2);border-radius:12px;padding:12px}}.metric small{{display:block;color:#6c8582;font-size:8px;text-transform:uppercase}}.metric strong{{display:block;font-size:21px;margin-top:3px}}.metric.green strong{{color:var(--green)}}.metric.red strong{{color:var(--red)}}.metric.blue strong{{color:var(--blue)}}
.grid{{display:grid;grid-template-columns:1.12fr .88fr;gap:11px;margin-top:11px}}.card{{border:1px solid var(--line);border-radius:15px;background:var(--panel);padding:16px}}.signal{{border-color:rgba(44,230,191,.28);background:linear-gradient(135deg,rgba(44,230,191,.055),rgba(9,24,32,.9))}}.signal-head{{display:flex;justify-content:space-between;align-items:center;gap:8px}}.signal-symbol{{font-size:23px;font-weight:950;letter-spacing:-.025em}}.direction{{display:inline-flex;border-radius:999px;padding:5px 8px;font-weight:950;font-size:9px}}.direction.long{{color:var(--green);border:1px solid rgba(66,226,140,.3);background:rgba(66,226,140,.06)}}.direction.short{{color:var(--red);border:1px solid rgba(255,98,125,.3);background:rgba(255,98,125,.06)}}.levels{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:12px}}.level{{border:1px solid var(--line);border-radius:10px;padding:10px;background:#07151c}}.level small{{display:block;color:#6c8582;font-size:8px;text-transform:uppercase}}.level b{{display:block;margin-top:3px;font-size:13px}}.free-note{{margin-top:10px;border:1px solid rgba(255,189,89,.23);border-radius:10px;padding:10px;color:#bfa77d;background:rgba(255,189,89,.04);font-size:10px}}
.results{{display:flex;flex-direction:column;gap:6px;margin-top:10px}}.result{{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;border-bottom:1px solid rgba(27,57,67,.6);padding:8px 2px}}.result:last-child{{border-bottom:0}}.result small{{color:var(--muted)}}.outcome{{font-weight:950;font-size:9px}}.outcome.tp{{color:var(--green)}}.outcome.sl{{color:var(--red)}}.outcome.be{{color:var(--amber)}}
.market{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}}.coin{{border:1px solid var(--line);border-radius:10px;padding:10px;background:#08161d}}.coin b{{display:block}}.coin small{{color:var(--muted)}}.locked{{position:relative;overflow:hidden}}.locked:after{{content:'PREMIUM';position:absolute;right:12px;top:12px;color:var(--amber);font-size:8px;font-weight:950;border:1px solid rgba(255,189,89,.25);border-radius:999px;padding:3px 6px}}.feature{{padding:8px 0;border-bottom:1px solid rgba(27,57,67,.6);color:#9db3b0}}.feature:last-child{{border-bottom:0}}.empty{{padding:18px 0;color:var(--muted);text-align:center}}
@media(max-width:800px){{.grid{{grid-template-columns:1fr}}.metrics{{grid-template-columns:1fr 1fr}}}}@media(max-width:520px){{.shell{{width:calc(100% - 16px)}}.actions .badge{{display:none}}.levels{{grid-template-columns:1fr 1fr 1fr}}.market{{grid-template-columns:1fr 1fr}}.result{{grid-template-columns:1fr auto}}.result small.time{{display:none}}}}
</style></head><body><div class="shell">
<header class="top"><div class="brand">Kripto Kontrol Merkezi</div><div class="actions"><span class="badge">FREE · {username}</span><a class="btn primary" href="/premium">Premium'a geç</a><a class="btn" href="/account">Hesabım</a><form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Çıkış</button></form></div></header>
<section class="hero"><span class="badge">FREE GERÇEK DENEYİM</span><h1>Sistemi gerçek verilerle dene.</h1><p>FREE üyelikte aynı anda 1 gerçek açık sinyal, son sonuçlar ve temel canlı piyasa görünümü açıktır. Premium üyelik bütün sinyalleri ve gelişmiş araçları açar.</p><div class="metrics"><div class="metric blue"><small>Toplam açık sinyal</small><strong id="fOpen">—</strong></div><div class="metric green"><small>Son TP</small><strong id="fTp">—</strong></div><div class="metric red"><small>Son SL</small><strong id="fSl">—</strong></div><div class="metric"><small>TP oranı</small><strong id="fRate">—</strong></div></div></section>
<div class="grid"><div>
<section class="card signal"><div class="signal-head"><div><h2>Ücretsiz canlı işlem</h2><p id="signalMeta">Sistemdeki en yeni gerçek açık işlemden 1 tanesi.</p></div><span class="badge">1 AÇIK SİNYAL</span></div><div id="freeSignal"><div class="empty">Canlı sinyal yükleniyor…</div></div></section>
<section class="card" style="margin-top:11px"><h2>Son gerçek sonuçlar</h2><p>Yalnız kazananlar seçilmez; sistemin son kapanan kayıtları sırayla gösterilir.</p><div class="results" id="freeResults"><div class="empty">Sonuçlar yükleniyor…</div></div></section>
</div><div>
<section class="card"><h2>Canlı piyasa</h2><p>FREE üyede 6 temel USDT perpetual coin.</p><div class="market" id="freeMarket"><div class="coin"><b>BTCUSDT</b><small>yükleniyor…</small></div></div><p><a class="btn" href="/market-center">Piyasa Merkezi</a></p></section>
<section class="card locked" style="margin-top:11px"><h2>Premium'da açılanlar</h2><div class="feature">🔒 Tüm gerçek açık sinyaller</div><div class="feature">🔒 TP2 / TP3 ve tam işlem detayları</div><div class="feature">🔒 Fırsat Merkezi + 80+ filtreleri</div><div class="feature">🔒 Teknik İnceleme Skoru</div><div class="feature">🔒 Sesli / renkli canlı uyarılar</div><div class="feature">🔒 İzleme Listesi ve ayrıntılı performans</div><p><a class="btn primary" href="/premium">Premium üyeliği incele</a></p></section>
</div></div>
<div class="free-note">FREE'de gösterilen işlem gerçek sistem kaydıdır; yatırım tavsiyesi veya kazanç garantisi değildir. Sinyal daha önce oluşmuş olabileceği için güncel fiyat giriş seviyesinden uzaklaşmış olabilir.</div>
</div>
<script nonce="{nonce_attr}">
(()=>{{
const esc=v=>String(v??'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));
const price=v=>{{const n=Number(v);if(!Number.isFinite(n))return '—';return Math.abs(n)>=1?n.toLocaleString('tr-TR',{{maximumFractionDigits:6}}):n.toLocaleString('tr-TR',{{maximumFractionDigits:9}});}};
const date=v=>{{const n=Number(v);return Number.isFinite(n)&&n>0?new Date(n*1000).toLocaleString('tr-TR',{{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}}):'—';}};
function renderSignal(d){{const s=d.free_signal,box=document.getElementById('freeSignal');if(!s){{box.innerHTML='<div class="empty">Şu anda seviyeleri gösterilebilir açık sinyal yok. Yeni sinyal oluştuğunda burada görünecek.</div>';signalMeta.textContent='FREE üyelik en yeni uygun açık sinyali otomatik gösterir.';return;}}const kind=s.direction==='LONG'?'long':'short';box.innerHTML=`<div class="signal-head"><div><div class="signal-symbol">${{esc(s.symbol)}}</div><small>${{esc(s.system)}} · ${{date(s.opened_at)}}</small></div><span class="direction ${{kind}}">${{esc(s.direction)}}</span></div><div class="levels"><div class="level"><small>Entry</small><b>${{price(s.entry)}}</b></div><div class="level"><small>TP1</small><b>${{price(s.tp1)}}</b></div><div class="level"><small>SL</small><b>${{price(s.sl)}}</b></div></div>`;signalMeta.textContent=d.locked_open_count>0?`${{d.locked_open_count}} diğer açık sinyal Premium'da.`:'Şu anda gösterilen işlem sistemdeki tek uygun açık sinyal.';}}
function renderResults(rows){{const box=document.getElementById('freeResults');if(!Array.isArray(rows)||!rows.length){{box.innerHTML='<div class="empty">Henüz gösterilecek kapanış sonucu yok.</div>';return;}}box.innerHTML=rows.map(r=>{{const o=String(r.outcome||'').toUpperCase(),kind=o.startsWith('TP')?'tp':o==='SL'||o.startsWith('SL_')?'sl':o.includes('BE')?'be':'';return `<div class="result"><div><b>${{esc(r.symbol)}}</b><small>${{esc(r.direction||'')}} · ${{esc(r.system||'Sistem')}}</small></div><span class="outcome ${{kind}}">${{esc(o)}}</span><small class="time">${{date(r.closed_at)}}</small></div>`;}}).join('');}}
async function preview(){{try{{const r=await fetch('/api/free/preview',{{credentials:'same-origin',cache:'no-store'}});if(r.status===401){{location.assign('/login');return;}}const d=await r.json();if(!r.ok)return;fOpen.textContent=d.open_count??'—';fTp.textContent=d.summary?.tp_count??'—';fSl.textContent=d.summary?.sl_count??'—';fRate.textContent=d.summary?.tp_rate_percent==null?'—':d.summary.tp_rate_percent+'%';renderSignal(d);renderResults(d.recent_results);}}catch{{}}}}
async function loadMarket(){{try{{const r=await fetch('/api/market/overview?symbols={market_symbols}',{{credentials:'same-origin',cache:'no-store'}}),d=await r.json();if(!r.ok)return;freeMarket.innerHTML=(d.items||[]).map(i=>`<div class="coin"><b>${{esc(i.symbol)}}</b><small>${{price(i.last)}} · ${{Number(i.change_24h_pct||0)>=0?'+':''}}${{Number(i.change_24h_pct||0).toFixed(2)}}%</small></div>`).join('');}}catch{{}}}}
preview();loadMarket();setInterval(preview,30000);setInterval(loadMarket,30000);
}})();
</script></body></html>'''


def make_v33_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    BaseHandler = adminux.make_v32_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V33Handler(BaseHandler):
        server_version = "KriptoPanel/3.3"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            info = self._plan_info(session)
            if str(info.get("plan")) != commercial.PLAN_FREE:
                return super()._render_root_v17(session)
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, free_preview_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "free_real_signal_preview": True,
                    "free_visible_open_signals": 1,
                    "free_visible_results": 5,
                    "signal_engine": "unchanged",
                })
                return
            if path == "/api/free/preview":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "login_required"})
                    return
                info = self._plan_info(session)
                if str(info.get("plan")) != commercial.PLAN_FREE:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "free_preview_only"})
                    return
                try:
                    payload = build_free_preview(service.get_data())
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "free_preview_unavailable"})
                    return
                self._json(HTTPStatus.OK, payload)
                return
            return super().do_GET()

    return V33Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.3 FREE gerçek ürün deneyimi.")
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
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v33_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} free_preview=1_open_signal+5_results signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
