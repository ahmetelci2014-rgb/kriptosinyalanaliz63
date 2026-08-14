"""Kripto Kontrol Merkezi V2.6 - Piyasa Fırsat Merkezi.

V2.5 izleme listesi ve V2.4 sesli/renkli uyarı katmanlarını korur. Bu dosya yalnız
panel tarafında OKX public piyasa verisini sade gruplara ayırır:
- 24 saat yükselen momentum,
- 24 saat düşen momentum,
- yaklaşık işlem hacmi liderleri,
- mevcut sistemde aktif sinyali bulunan coinler.

Bu ekran yeni sinyal üretmez, stratejiyi değiştirmez ve emir açmaz.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_market_app as market
import dashboard_watchlist_app as watch
from dashboard_live_app import (
    LoginRateLimiter,
    MarketDataError,
    OKXMarketDataClient,
    PanelConfig,
    build_service,
    dashboard_for_session,
)

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_6_OPPORTUNITY_2026_08_14"


def _turnover(item: dict[str, Any]) -> float | None:
    try:
        last = float(item.get("last"))
        volume = float(item.get("volume_24h"))
    except (TypeError, ValueError):
        return None
    value = last * volume
    return value if value > 0 else None


def build_opportunity_payload(
    overview_client: market.OKXMarketOverviewClient,
    data: dict[str, Any],
    *,
    liquid_limit: int = 120,
    per_group: int = 8,
) -> dict[str, Any]:
    parsed: dict[str, dict[str, Any]] = {}
    for raw in overview_client._all_tickers():
        parsed_row = overview_client._parse_row(raw)
        if parsed_row is None:
            continue
        symbol, item = parsed_row
        item = dict(item)
        item["turnover_24h_estimate"] = _turnover(item)
        parsed[symbol] = item

    context = market.market_context(data)
    for symbol, item in parsed.items():
        item.update(context.get(symbol, {}))

    liquid = [item for item in parsed.values() if item.get("turnover_24h_estimate") is not None]
    liquid.sort(key=lambda item: float(item.get("turnover_24h_estimate") or 0), reverse=True)
    liquid = liquid[: max(20, min(int(liquid_limit), 200))]

    def change(item: dict[str, Any]) -> float:
        try:
            return float(item.get("change_24h_pct"))
        except (TypeError, ValueError):
            return 0.0

    rising = sorted((item for item in liquid if change(item) > 0), key=change, reverse=True)[:per_group]
    falling = sorted((item for item in liquid if change(item) < 0), key=change)[:per_group]
    volume = liquid[:per_group]

    open_rows = data.get("open_trades") if isinstance(data.get("open_trades"), list) else []
    active: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in open_rows:
        if not isinstance(row, dict):
            continue
        try:
            symbol = OKXMarketDataClient.normalize_symbol(str(row.get("symbol") or ""))
        except ValueError:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        item = parsed.get(symbol)
        if item is None:
            continue
        active.append(item)
        if len(active) >= 12:
            break

    changes = [change(item) for item in liquid]
    up_count = sum(1 for value in changes if value > 0)
    down_count = sum(1 for value in changes if value < 0)
    flat_count = len(changes) - up_count - down_count
    avg_change = sum(changes) / len(changes) if changes else 0.0

    return {
        "summary": {
            "universe": len(liquid),
            "up": up_count,
            "down": down_count,
            "flat": flat_count,
            "avg_change_24h_pct": round(avg_change, 3),
            "active_signals": len(active),
        },
        "groups": {
            "rising": rising,
            "falling": falling,
            "volume": volume,
            "active": active,
        },
        "fetched_at": int(time.time()),
        "source": "OKX_PUBLIC_NO_API_KEY",
        "note": "Gruplar analiz amaçlı piyasa görünümüdür; işlem sinyali değildir.",
        "version": VERSION,
    }


def opportunity_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = watch.watchlist_dashboard_page(session, nonce)
    nonce_attr = html.escape(nonce, quote=True)

    css = r'''
    /* V2.6: piyasa fırsatlarını sade, renk kodlu gruplar halinde göster. */
    .nav-item[data-view="opportunities"].active{background:rgba(96,165,250,.10);color:#7db8ff;border-color:rgba(96,165,250,.23)}
    #page-opportunities .panel{border-color:rgba(96,165,250,.15)}#page-opportunities .panel-head{background:linear-gradient(90deg,rgba(96,165,250,.055),transparent 40%)}
    .opp-summary{display:grid;grid-template-columns:repeat(5,1fr);gap:9px;margin-bottom:14px}.opp-metric{border:1px solid var(--line);background:#0a171f;border-radius:12px;padding:11px 12px}.opp-metric small{display:block;color:#698581;font-size:8px;text-transform:uppercase;letter-spacing:.06em}.opp-metric strong{display:block;margin-top:4px;font-size:19px}.opp-metric.up strong{color:var(--green)}.opp-metric.down strong{color:var(--red)}.opp-metric.blue strong{color:var(--blue)}.opp-metric.amber strong{color:var(--amber)}
    .opp-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.opp-group{border:1px solid var(--line);background:#091720;border-radius:14px;overflow:hidden}.opp-group-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 13px;border-bottom:1px solid var(--line)}.opp-group-head h2{font-size:13px;margin:0}.opp-group-head small{font-size:8px;color:var(--muted)}.opp-group.rising{border-color:rgba(66,226,140,.18)}.opp-group.falling{border-color:rgba(255,98,125,.18)}.opp-group.volume{border-color:rgba(255,189,89,.18)}.opp-group.active{border-color:rgba(96,165,250,.20)}
    .opp-list{padding:6px}.opp-card{display:grid;grid-template-columns:36px minmax(95px,1fr) auto auto;gap:9px;align-items:center;padding:9px 7px;border-bottom:1px solid rgba(29,48,59,.65);cursor:pointer}.opp-card:last-child{border-bottom:0}.opp-card:hover{background:rgba(255,255,255,.025);border-radius:9px}.opp-mark{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:#10232d;border:1px solid #1e3d49;color:#a8c5c1;font-size:8px;font-weight:950}.opp-main{min-width:0}.opp-main strong{display:block;font-size:11px}.opp-main small{display:block;color:var(--muted);font-size:8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.opp-price{text-align:right}.opp-price b{display:block;font-size:10px}.opp-price small{font-size:8px;color:var(--muted)}.opp-change{min-width:56px;text-align:right;font-size:10px;font-weight:900}.opp-change.up{color:var(--green)}.opp-change.down{color:var(--red)}.opp-change.flat{color:var(--muted)}.opp-signal{display:inline-flex;margin-top:3px;border:1px solid var(--line);border-radius:999px;padding:2px 5px;font-size:7px;font-weight:900}.opp-signal.long{color:var(--green);border-color:rgba(66,226,140,.25)}.opp-signal.short{color:var(--red);border-color:rgba(255,98,125,.25)}
    .opp-empty{padding:24px 12px;text-align:center;color:var(--muted);font-size:10px}.opp-note{margin-top:10px;color:#66817e;font-size:9px}.opp-actions{display:flex;gap:7px;flex-wrap:wrap}
    @media(max-width:1000px){.opp-summary{grid-template-columns:repeat(3,1fr)}.opp-grid{grid-template-columns:1fr}}
    @media(max-width:620px){.opp-summary{grid-template-columns:1fr 1fr}.opp-card{grid-template-columns:34px 1fr auto}.opp-price{display:none}}
    '''
    body = body.replace("  </style>", css + "\n  </style>", 1)

    watch_nav = '<button class="nav-item" data-view="watchlist"><span>★</span><b>İzleme Listesi</b></button>'
    opp_nav = '<button class="nav-item" data-view="opportunities"><span>◈</span><b>Fırsatlar</b></button>'
    if watch_nav not in body:
        raise RuntimeError("V2.5 İzleme Listesi menüsü bulunamadı.")
    body = body.replace(watch_nav, watch_nav + "\n      " + opp_nav, 1)

    mobile_market = '<a href="/market-center"><span>⌁</span>Piyasa</a>'
    if mobile_market in body:
        body = body.replace(mobile_market, '<button data-view="opportunities"><span>◈</span>Fırsat</button>', 1)

    title_anchor = "const titles={home:'Ana Sayfa',signals:'Sinyaller',trades:'İşlemler',results:'Sonuçlar',watchlist:'İzleme Listesi',system:'Sistem'};"
    if title_anchor not in body:
        raise RuntimeError("V2.5 görünüm başlık haritası bulunamadı.")
    body = body.replace(
        title_anchor,
        "const titles={home:'Ana Sayfa',signals:'Sinyaller',trades:'İşlemler',results:'Sonuçlar',watchlist:'İzleme Listesi',opportunities:'Piyasa Fırsatları',system:'Sistem'};",
        1,
    )

    section = r'''
      <section class="page" id="page-opportunities">
        <div class="page-head">
          <div><h1>Piyasa Fırsat Merkezi</h1><p>Likit USDT perpetual coinleri 24 saat hareketi, yaklaşık hacim ve sistem durumuna göre hızlı tara.</p></div>
          <div class="opp-actions"><button class="btn primary" id="oppRefreshBtn" type="button">Piyasayı yenile</button><a class="btn" href="/market-center">Tüm Piyasa / Grafik</a></div>
        </div>
        <div class="opp-summary" id="oppSummary"></div>
        <div class="opp-grid">
          <section class="opp-group rising"><div class="opp-group-head"><div><h2>↗ 24s Yükselen Momentum</h2><small>Likit evrende en güçlü pozitif hareketler</small></div></div><div class="opp-list" id="oppRising"></div></section>
          <section class="opp-group falling"><div class="opp-group-head"><div><h2>↘ 24s Düşen Momentum</h2><small>Likit evrende en güçlü negatif hareketler</small></div></div><div class="opp-list" id="oppFalling"></div></section>
          <section class="opp-group volume"><div class="opp-group-head"><div><h2>◆ Yaklaşık Hacim Liderleri</h2><small>OKX 24s hacim × son fiyat yaklaşımı</small></div></div><div class="opp-list" id="oppVolume"></div></section>
          <section class="opp-group active"><div class="opp-group-head"><div><h2>⚡ Bizim Sistemde Aktif</h2><small>Şu an panelde açık sinyali bulunan coinler</small></div></div><div class="opp-list" id="oppActive"></div></section>
        </div>
        <div class="opp-note">Bu gruplar yalnız piyasa taramasıdır. Yeni işlem sinyali üretmez, mevcut bot filtrelerini değiştirmez ve emir açmaz. Bir coine tıklayınca Hızlı Coin Analizi açılır.</div>
      </section>
'''
    watch_anchor = '      <section class="page" id="page-watchlist">'
    if watch_anchor not in body:
        raise RuntimeError("V2.5 İzleme Listesi bölümü bulunamadı.")
    body = body.replace(watch_anchor, section + "\n" + watch_anchor, 1)

    script = r'''
<script nonce="__NONCE__">
(() => {
  const $=id=>document.getElementById(id);let loading=false,lastPayload=null;
  const esc=v=>String(v??'').replace(/[&<>\"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch]));
  const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null;};
  const fmt=v=>{const n=num(v);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{maximumFractionDigits:2});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{maximumFractionDigits:5});return n.toLocaleString('tr-TR',{maximumFractionDigits:9});};
  const pct=v=>{const n=num(v);return n===null?'—':`${n>=0?'+':''}${n.toFixed(2)}%`;};
  const compact=v=>{const n=num(v);if(n===null)return '—';return new Intl.NumberFormat('tr-TR',{notation:'compact',maximumFractionDigits:1}).format(n);};
  const cls=v=>{const n=num(v);return n===null||n===0?'flat':n>0?'up':'down';};
  const base=s=>String(s||'').replace(/USDT$/,'').slice(0,6);
  function metric(label,value,kind=''){return `<div class="opp-metric ${kind}"><small>${esc(label)}</small><strong>${esc(value)}</strong></div>`;}
  function card(item,showVolume=false){const change=num(item?.change_24h_pct),kind=String(item?.kind||''),dir=String(item?.direction||'').toUpperCase(),signal=kind==='OPEN'?`<span class="opp-signal ${dir==='SHORT'?'short':'long'}">${esc(dir||'AÇIK')} · ${esc(item.system_label||'Sistem')}</span>`:'';return `<div class="opp-card" data-focus-symbol="${esc(item?.symbol||'')}"><div class="opp-mark">${esc(base(item?.symbol))}</div><div class="opp-main"><strong>${esc(item?.symbol||'—')}</strong><small>${showVolume?`≈ ${compact(item?.turnover_24h_estimate)} USDT`:'Hızlı analiz için tıkla'}</small>${signal}</div><div class="opp-price"><b>${fmt(item?.last)}</b><small>USDT</small></div><div class="opp-change ${cls(change)}">${pct(change)}</div></div>`;}
  function renderList(id,items,volume=false){$(id).innerHTML=(Array.isArray(items)?items:[]).map(item=>card(item,volume)).join('')||'<div class="opp-empty">Bu grupta gösterilecek coin yok.</div>';}
  function render(payload){lastPayload=payload;const s=payload?.summary||{};const avg=num(s.avg_change_24h_pct);$('oppSummary').innerHTML=[metric('Likit evren',s.universe??0,'blue'),metric('Yükselen',s.up??0,'up'),metric('Düşen',s.down??0,'down'),metric('Ortalama 24s',avg===null?'—':pct(avg),avg!==null&&avg>=0?'up':'down'),metric('Aktif sinyal',s.active_signals??0,'amber')].join('');const g=payload?.groups||{};renderList('oppRising',g.rising);renderList('oppFalling',g.falling);renderList('oppVolume',g.volume,true);renderList('oppActive',g.active);}
  async function load(force=false){if(loading)return;loading=true;$('oppRefreshBtn').textContent='Güncelleniyor…';try{const r=await fetch('/api/market/opportunities'+(force?'?refresh=1':''),{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');return;}const p=await r.json();if(!r.ok)throw new Error(p.message||p.error||`HTTP ${r.status}`);render(p);}catch(err){if(!lastPayload){['oppRising','oppFalling','oppVolume','oppActive'].forEach(id=>$(id).innerHTML=`<div class="opp-empty">Piyasa taraması alınamadı.</div>`);}}finally{loading=false;$('oppRefreshBtn').textContent='Piyasayı yenile';}}
  $('oppRefreshBtn')?.addEventListener('click',()=>load(true));
  document.addEventListener('click',event=>{const btn=event.target.closest('[data-view="opportunities"]');if(btn)setTimeout(()=>load(false),20);});
  load(false);setInterval(()=>load(false),60000);
})();
</script>
'''.replace("__NONCE__", nonce_attr)
    return body.replace("</body>", script + "\n</body>", 1)


def make_v26_handler(config: PanelConfig, service, sessions, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    overview_client = overview_client or market.OKXMarketOverviewClient()
    BaseHandler = watch.make_v25_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V26Handler(BaseHandler):
        server_version = "KriptoPanel/2.6"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, opportunity_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            if parsed.path == "/api/market/opportunities":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication_required"})
                    return
                try:
                    filtered_data = dashboard_for_session(service.get_data(), session)
                    payload = build_opportunity_payload(overview_client, filtered_data)
                    self._json(HTTPStatus.OK, payload)
                except MarketDataError:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": "market_data_unavailable"})
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "market_opportunities_unavailable"})
                return
            return super().do_GET()

    return V26Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.6 piyasa fırsat merkezi.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = watch.alert.notify.home.v21.focus.v2.v19.v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = watch.alert.notify.home.v21.focus.v2.v19.v18.account_store_from_env(config)
    overview_client = market.OKXMarketOverviewClient()
    handler = make_v26_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        OKXMarketDataClient(),
        overview_client,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} opportunities=on watchlist=on sound_alert=optional")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
