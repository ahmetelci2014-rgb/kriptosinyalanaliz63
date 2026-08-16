"""Kripto Kontrol Merkezi V3.32 - Piyasa / Coin inceleme akışı.

V3.31 ve önceki bütün panel katmanlarını korur. Bu katman yalnız Piyasa Merkezi,
Coin Merkezi ve bu iki alana giden navigasyonun görevlerini netleştirir:
- Piyasa Merkezi: coin bulma, karşılaştırma ve hızlı grafik kontrolü.
- Coin Merkezi: Premium/Admin için seçilen coinin derin sistem incelemesi.
- /market-center?symbol=... bağlantısı artık istenen coini gerçekten açar.
- Premium/Admin Piyasa Merkezi'nden seçili coinin Coin Merkezi'ne geçebilir.
- Coin Merkezi'nde teknik/performance/geçmiş gibi ikincil analizler isteğe bağlı açılır.
- Ana panelde tekrarlı doğrudan Coin Merkezi yan menü bağlantısı azaltılır; coin arama,
  sinyal kartları ve Piyasa Merkezi üzerinden erişim korunur.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger,
üyelik/ödeme backend'i ve otomatik filtre davranışı değiştirilmez.
"""
from __future__ import annotations

import argparse
import html
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_roleboundary_app as roleux
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_MARKET_COIN_UX_2026_08_16"

CSS = r'''
/* V3.32 - Piyasa = keşif, Coin Merkezi = derin inceleme */
.v332-market-path,.v332-coin-path{display:flex;align-items:center;gap:6px;flex-wrap:wrap;color:#708d89;font-size:9px;font-weight:850;margin:10px 0 0}.v332-market-path a,.v332-coin-path a{color:#9db6b2!important;text-decoration:none!important}.v332-market-path b,.v332-coin-path b{color:#2ce6bf}.v332-path-sep{color:#405e5a}
.v332-market-action{display:inline-flex;align-items:center;justify-content:center;gap:5px;border:1px solid rgba(44,230,191,.32)!important;background:rgba(44,230,191,.07)!important;color:#2ce6bf!important;border-radius:10px;padding:10px 11px;font-weight:900;text-decoration:none!important;white-space:nowrap}.v332-market-action:hover{background:rgba(44,230,191,.12)!important}
.v332-chart-toggle{display:none;border:1px solid #29444c;background:#081820;color:#9db5b1;border-radius:9px;padding:7px 9px;font-size:9px;font-weight:900;margin:0 0 10px;cursor:pointer}
.v332-coin-detail-toggle{display:flex;align-items:center;justify-content:space-between;width:100%;margin:12px 0 0;border:1px solid #29444c;background:#081820;color:#a9bfbc;border-radius:11px;padding:10px 12px;font-size:9px;font-weight:900;cursor:pointer}.v332-coin-detail-toggle span{color:#6f8d89;font-size:8px;font-weight:800}.v332-coin-detail-toggle:hover{border-color:#2ce6bf;color:#2ce6bf}
.v332-secondary-analysis{display:none!important}.v332-show-secondary .v332-secondary-analysis{display:block!important}
.v332-coin-note{display:flex;align-items:center;gap:7px;margin:10px 0 0;color:#6f8b87;font-size:8px}.v332-coin-note b{color:#2ce6bf}
@media(max-width:760px){
 .v332-market-path,.v332-coin-path{margin-top:7px}.v332-market-action{width:100%;min-height:41px}
 .v332-chart-toggle{display:block;width:100%;min-height:38px}
 .v332-market-chart-collapsed .chart-wrap,.v332-market-chart-collapsed .chart-meta{display:none!important}
 .v332-market-chart-collapsed.card.selected{padding-bottom:9px!important}
 .v332-coin-detail-toggle{min-height:42px}
}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v332-marketcoin-script">
(()=>{'use strict';if(window.__v332MarketCoin)return;window.__v332MarketCoin=true;
const PREMIUM=__PREMIUM__;
const clean=value=>{let s=String(value||'').toUpperCase().replace(/[^A-Z0-9]/g,'');if(s&&!s.endsWith('USDT'))s+='USDT';return /^[A-Z0-9]{2,15}USDT$/.test(s)?s:'BTCUSDT'};
function market(){
 const shell=document.querySelector('.shell'),top=shell?.querySelector('.top'),input=document.getElementById('symbolInput'),load=document.getElementById('loadButton');if(!shell||!top||!input)return;
 const h=top.querySelector('h1'),p=top.querySelector('p');if(h)h.textContent='Piyasa Merkezi';if(p)p.textContent='Coin bul, karşılaştır ve hızlı grafiğini kontrol et. Derin analiz Coin Merkezi’nde.';if(load)load.textContent='Grafiği aç';
 if(!document.querySelector('.v332-market-path')){const path=document.createElement('div');path.className='v332-market-path';path.innerHTML='<a href="/">Panel</a><span class="v332-path-sep">›</span><b>Piyasa</b>';top.querySelector('div')?.appendChild(path)}
 const toolbar=shell.querySelector('.toolbar');if(toolbar&&PREMIUM&&!document.getElementById('v332DeepCoin')){const a=document.createElement('a');a.id='v332DeepCoin';a.className='v332-market-action';a.textContent='Detaylı Coin Merkezi';toolbar.insertBefore(a,toolbar.querySelector('.status'));const sync=()=>{a.href=`/coin-center?symbol=${encodeURIComponent(clean(input.value))}`};sync();input.addEventListener('input',sync);document.getElementById('marketRows')?.addEventListener('click',e=>{const row=e.target.closest('[data-symbol]');if(row){input.value=clean(row.dataset.symbol);sync()}});const title=document.getElementById('chartTitle');if(title)new MutationObserver(()=>{const symbol=String(title.textContent||'').split('·')[0].trim();if(symbol){input.value=clean(symbol);sync()}}).observe(title,{childList:true,subtree:true,characterData:true})}
 const chartCard=shell.querySelector('.layout > .card.selected');if(chartCard&&!chartCard.querySelector('.v332-chart-toggle')){const btn=document.createElement('button');btn.type='button';btn.className='v332-chart-toggle';btn.textContent='Hızlı grafiği göster';btn.setAttribute('aria-expanded','false');chartCard.classList.add('v332-market-chart-collapsed');btn.addEventListener('click',()=>{const closed=chartCard.classList.toggle('v332-market-chart-collapsed');btn.textContent=closed?'Hızlı grafiği göster':'Hızlı grafiği gizle';btn.setAttribute('aria-expanded',closed?'false':'true');if(!closed)window.dispatchEvent(new Event('resize'))});chartCard.querySelector('h2')?.insertAdjacentElement('afterend',btn)}
}
function coin(){
 const shell=document.querySelector('.shell'),top=shell?.querySelector('.top'),input=document.getElementById('symbolInput');if(!shell||!top)return;
 const back=top.querySelector('a.back[href="/"]');if(back)back.textContent='← Panel';
 if(!document.getElementById('v332MarketBack')){const a=document.createElement('a');a.id='v332MarketBack';a.className='back';a.textContent='Piyasa';const sync=()=>a.href=`/market-center?symbol=${encodeURIComponent(clean(input?.value))}`;sync();input?.addEventListener('input',sync);const load=document.getElementById('loadBtn');load?.addEventListener('click',()=>setTimeout(sync,0));if(back)back.insertAdjacentElement('afterend',a);else top.prepend(a)}
 const hero=shell.querySelector('.hero');if(hero&&!document.querySelector('.v332-coin-path')){const path=document.createElement('div');path.className='v332-coin-path';path.innerHTML='<a href="/">Panel</a><span class="v332-path-sep">›</span><a id="v332CoinPathMarket" href="/market-center">Piyasa</a><span class="v332-path-sep">›</span><b>Coin Merkezi</b>';hero.insertAdjacentElement('beforebegin',path);const sync=()=>{const a=document.getElementById('v332CoinPathMarket');if(a)a.href=`/market-center?symbol=${encodeURIComponent(clean(input?.value))}`};sync();input?.addEventListener('input',sync)}
 const panels=[...shell.querySelectorAll('.panel')];const secondary=[];for(const panel of panels){const title=String(panel.querySelector('.panel-head h2')?.textContent||panel.querySelector('h2')?.textContent||'').trim();if(/teknik görünüm|performans|geçmiş|sistem bazlı/i.test(title)){panel.classList.add('v332-secondary-analysis');secondary.push(panel)}}
 if(hero&&secondary.length&&!document.getElementById('v332CoinDetails')){const btn=document.createElement('button');btn.id='v332CoinDetails';btn.type='button';btn.className='v332-coin-detail-toggle';btn.innerHTML=`Analiz ayrıntılarını göster <span>${secondary.length} bölüm</span>`;btn.setAttribute('aria-expanded','false');btn.addEventListener('click',()=>{const open=document.body.classList.toggle('v332-show-secondary');btn.innerHTML=`${open?'Analiz ayrıntılarını gizle':'Analiz ayrıntılarını göster'} <span>${secondary.length} bölüm</span>`;btn.setAttribute('aria-expanded',open?'true':'false')});hero.insertAdjacentElement('afterend',btn);const note=document.createElement('div');note.className='v332-coin-note';note.innerHTML='<b>Önce karar bilgisi</b><span>Grafik ve aktif işlem önde; teknik/geçmiş analizler isteğe bağlı.</span>';btn.insertAdjacentElement('afterend',note)}
}
function root(){if(location.pathname!=='/')return;document.querySelectorAll('.sidebar .nav-item[href^="/coin-center"]').forEach(a=>a.remove())}
function init(){root();if(location.pathname==='/market-center')market();else if(location.pathname==='/coin-center')coin()}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
</script>
'''


def _inject(body: str, nonce: str, *, premium_access: bool) -> str:
    if 'id="v332-marketcoin-script"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True)).replace(
        "__PREMIUM__", "true" if premium_access else "false"
    )
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def enhance_market_page(body: str, nonce: str, *, premium_access: bool) -> str:
    """Piyasa sayfasını keşif/karşılaştırma rolüne getirir ve symbol deep-link'i düzeltir."""
    marker = "loadOverview().then(()=>loadChart('BTCUSDT'));"
    if marker in body:
        replacement = (
            "const v332Requested=normalize(new URLSearchParams(location.search).get('symbol')||'BTCUSDT');"
            "const v332Initial=/^[A-Z0-9]{2,15}USDT$/.test(v332Requested)?v332Requested:'BTCUSDT';"
            "loadOverview().then(()=>loadChart(v332Initial));"
        )
        body = body.replace(marker, replacement, 1)
    return _inject(body, nonce, premium_access=premium_access)


def enhance_coin_page(body: str, nonce: str) -> str:
    """Coin Merkezi'nde karar bilgisi/ikincil analiz hiyerarşisi ekler."""
    return _inject(body, nonce, premium_access=True)


def enhance_root_navigation(body: str, nonce: str, *, premium_access: bool) -> str:
    """Coin Merkezi rotasını silmeden tekrarlı yan menü bağlantısını azaltır."""
    return _inject(body, nonce, premium_access=premium_access)


def make_v332_handler(
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
    BaseHandler = roleux.make_v331_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V332Handler(BaseHandler):
        server_version = "KriptoPanel/3.32"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                session = self._session()
                if session:
                    premium_access = bool(self._is_premium(session))
                    if path == "/market-center":
                        body = enhance_market_page(body, str(nonce or ""), premium_access=premium_access)
                    elif path == "/coin-center" and premium_access:
                        body = enhance_coin_page(body, str(nonce or ""))
                    elif path == "/":
                        body = enhance_root_navigation(body, str(nonce or ""), premium_access=premium_access)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "market_role": "discover_compare_quick_chart",
                    "coin_center_role": "premium_deep_single_coin_review",
                    "market_symbol_deeplink": True,
                    "market_mobile_chart": "on_demand",
                    "coin_secondary_analysis": "on_demand",
                    "premium_coin_handoff": True,
                    "free_market_access": "preserved",
                    "coin_center_premium_guard": "preserved",
                    "role_boundary": "preserved",
                    "account_ux": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V332Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32 Piyasa / Coin UX")
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
    handler = make_v332_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} market_coin_ux=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
