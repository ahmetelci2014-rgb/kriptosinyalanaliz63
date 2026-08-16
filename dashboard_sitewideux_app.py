"""Kripto Kontrol Merkezi V3.28 - Genel Sade UX / Bilgi Hiyerarşisi.

V3.27 ve önceki bütün canlı panel katmanlarını korur. Bu sürüm tek tek kutu
saklamak yerine ana panelin genel bilgi mimarisini düzenler:
- Ana sayfada yalnız günlük özet ve öne çıkan açık sinyaller ilk planda kalır.
- Günlük akış, favoriler, sinyal açıklamaları, veri sağlığı ve veri kaynakları
  tek bir "Daha fazla bilgi" alanına taşınır.
- Eski HTML işaretine bağlı kalmadan Sinyal Rehberi / Veri Sağlığı bloklarının
  gerçek mevcut ana sayfada erişilebilir kalması sağlanır.
- Mobil alt menü en fazla beş ana hedefe sadeleştirilir.
- Mobil sinyal ve işlem kartlarında ikincil seviyeler isteğe bağlı açılır.
- ADMIN tarafındaki tekrarlı doğrudan menüler azaltılır; Yönetim Merkezi korunur.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger yazımı ve
otomatik filtre davranışı değiştirilmez.
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
import dashboard_datahealth_app as datahealth
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_progressive_app as progressive
import dashboard_signalguide_app as signalguide
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_28_SITEWIDE_UX_2026_08_16"

CSS = r'''
/* V3.28 - ana panelde bilgi hiyerarşisi */
.v328-more{margin:14px 0 4px;border:1px solid #1b3943;border-radius:15px;background:#08171f;overflow:hidden}
.v328-more>summary{list-style:none;display:flex;align-items:center;justify-content:space-between;gap:10px;padding:13px 14px;cursor:pointer;user-select:none}
.v328-more>summary::-webkit-details-marker{display:none}.v328-more-title b{display:block;font-size:12px}.v328-more-title small{display:block;color:#789491;font-size:8px;margin-top:2px}.v328-more-chevron{width:27px;height:27px;border:1px solid #29444c;border-radius:9px;display:grid;place-items:center;color:#8ba7a3;transition:transform .18s ease}.v328-more[open] .v328-more-chevron{transform:rotate(180deg)}
.v328-more-body{border-top:1px solid #1b3943;padding:11px}.v328-more-body>*{margin-top:0!important}.v328-more-body>*+*{margin-top:10px!important}
.v328-coin-tool{display:grid;grid-template-columns:1fr auto;gap:7px;border:1px solid #1b3943;border-radius:11px;background:#06131a;padding:9px}.v328-coin-tool input{min-width:0;border:1px solid #29444c;background:#07171e;color:#edf8f6;border-radius:9px;padding:9px 10px;outline:none}.v328-coin-tool input:focus{border-color:#2ce6bf}.v328-coin-tool button{border:1px solid #2ce6bf;background:#2ce6bf;color:#04120f;border-radius:9px;padding:8px 11px;font-size:9px;font-weight:900}
.v328-card-toggle,.v328-level-toggle{display:none;border:1px solid #29444c;background:#081820;color:#9db5b1;border-radius:8px;font-size:8px;font-weight:900;min-height:32px;padding:6px 8px}
.sidebar button.admin-only[data-view="system"],.sidebar a.admin-only[href="/admin/users"]{display:none}
@media(max-width:760px){
  .mobile-nav button[data-view="trades"],.mobile-nav .v32-mobile-admin-nav{display:none!important}
  .mobile-nav button,.mobile-nav a{min-width:0!important;flex:1 1 20%!important}
  .v328-more{border-radius:13px}.v328-more>summary{padding:12px}.v328-more-body{padding:9px}
  .v328-coin-tool{grid-template-columns:1fr}.v328-coin-tool button{min-height:39px}
  #page-signals .row-card{grid-template-columns:minmax(0,1fr) auto auto auto!important;gap:6px!important}
  #page-signals .row-card .data-block{display:none!important}
  #page-signals .row-card .v328-primary-data{display:block!important}
  #page-signals .row-card .v328-secondary-data{display:none!important}
  #page-signals .row-card.v328-expanded .v328-secondary-data{display:flex!important;grid-column:1/-1;align-items:center;justify-content:space-between;border-top:1px solid rgba(29,48,59,.65);padding-top:6px}
  .v328-card-toggle{display:inline-flex;align-items:center;justify-content:center;min-width:34px;padding:5px 7px}
  #page-trades .wide-card .levels{display:none!important}
  #page-trades .wide-card.v328-level-open .levels{display:grid!important}
  .v328-level-toggle{display:inline-flex;align-items:center;justify-content:center;margin-left:auto}
  #page-trades .wide-top{flex-wrap:wrap}
}
@media(max-width:430px){#page-signals .row-card{grid-template-columns:minmax(0,1fr) auto auto!important}#page-signals .row-card>.btn{grid-column:1/2;justify-self:start}.v328-card-toggle{grid-column:3/4;grid-row:1/2}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v328-sitewide-script">
(()=>{'use strict';if(window.__v328Sitewide)return;window.__v328Sitewide=true;
const $=id=>document.getElementById(id);
function cleanSymbol(value){let s=String(value||'').toUpperCase().replace(/[^A-Z0-9]/g,'');if(!s)return'';if(!s.endsWith('USDT'))s+='USDT';return s}
function makeMoreHub(){const page=$('page-home');if(!page)return null;let hub=$('v328More');if(hub)return hub;hub=document.createElement('details');hub.id='v328More';hub.className='v328-more';hub.innerHTML='<summary><span class="v328-more-title"><b>Daha fazla bilgi</b><small>Günlük akış, favoriler, teknik açıklamalar ve sistem durumu</small></span><span class="v328-more-chevron">⌄</span></summary><div class="v328-more-body" id="v328MoreBody"></div>';page.appendChild(hub);return hub}
function installCoinTool(body){if($('v328CoinInput'))return;const wrap=document.createElement('div');wrap.className='v328-coin-tool';wrap.innerHTML='<input id="v328CoinInput" inputmode="text" autocomplete="off" placeholder="Coin incele: BTC, ETH, SOL..." aria-label="Coin sembolü"><button id="v328CoinOpen" type="button">Coin Merkezi</button>';body.prepend(wrap);const open=()=>{const s=cleanSymbol($('v328CoinInput')?.value);if(!s){$('v328CoinInput')?.focus();return}location.assign(`/coin-center?symbol=${encodeURIComponent(s)}`)};$('v328CoinOpen')?.addEventListener('click',open);$('v328CoinInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();open()}})}
function moveOptional(){const hub=makeMoreHub();if(!hub)return;const body=$('v328MoreBody');if(!body)return;installCoinTool(body);const pulse=$('v312DailyPulse');const ids=['homeSmartMetrics','v323MemberFocus','v324SignalGuide','v326DataHealth','v322SourceBar'];for(const id of ids){const el=$(id);if(!el||el.closest('#v328MoreBody'))continue;if(id==='homeSmartMetrics'&&!pulse)continue;body.appendChild(el)}const grid=document.querySelector('#page-home .home-smart-grid');if(grid&&!grid.closest('#v328MoreBody'))body.appendChild(grid)}
function compactMobileNav(){const nav=document.querySelector('.mobile-nav');if(!nav)return;let result=nav.querySelector('[data-view="results"]');if(!result){const account=nav.querySelector('a[href="/account"]');result=document.createElement('button');result.type='button';result.dataset.view='results';result.className='v328-mobile-results';result.innerHTML='<span>✓</span>Sonuç';if(account)nav.insertBefore(result,account);else nav.appendChild(result)}nav.querySelectorAll('.v32-mobile-admin-nav').forEach(el=>el.setAttribute('aria-hidden','true'))}
function decorateSignalCards(){const root=$('signalsList');if(!root)return;const apply=()=>root.querySelectorAll('.row-card').forEach(card=>{if(card.dataset.v328Ready==='1')return;card.dataset.v328Ready='1';const blocks=[...card.querySelectorAll('.data-block')];if(blocks[0])blocks[0].classList.add('v328-primary-data');blocks.slice(1).forEach(el=>el.classList.add('v328-secondary-data'));if(!blocks.length)return;const b=document.createElement('button');b.type='button';b.className='v328-card-toggle';b.textContent='Detay';b.setAttribute('aria-expanded','false');b.addEventListener('click',()=>{const open=card.classList.toggle('v328-expanded');b.textContent=open?'Kapat':'Detay';b.setAttribute('aria-expanded',open?'true':'false')});card.appendChild(b)});apply();new MutationObserver(apply).observe(root,{childList:true,subtree:true})}
function decorateTradeCards(){const root=$('tradesList');if(!root)return;const apply=()=>root.querySelectorAll('.wide-card').forEach(card=>{if(card.dataset.v328Ready==='1')return;card.dataset.v328Ready='1';const head=card.querySelector('.wide-top'),levels=card.querySelector('.levels');if(!head||!levels)return;const b=document.createElement('button');b.type='button';b.className='v328-level-toggle';b.textContent='Seviyeler';b.setAttribute('aria-expanded','false');b.addEventListener('click',()=>{const open=card.classList.toggle('v328-level-open');b.textContent=open?'Gizle':'Seviyeler';b.setAttribute('aria-expanded',open?'true':'false')});head.appendChild(b)});apply();new MutationObserver(apply).observe(root,{childList:true,subtree:true})}
function init(){document.body.classList.add('v328-site-clean');moveOptional();compactMobileNav();decorateSignalCards();decorateTradeCards()}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
</script>
'''


def _ensure_optional_blocks(body: str, premium_access: bool) -> str:
    """Eski homeMetrics işaretine bağlı kalmadan teknik blokların erişilebilir kalmasını sağlar."""
    if not premium_access:
        return body
    additions = []
    if 'id="v324SignalGuide"' not in body:
        additions.append(signalguide.signal_guide_block())
    if 'id="v326DataHealth"' not in body:
        additions.append(datahealth.data_health_block())
    if additions and "</body>" in body:
        body = body.replace("</body>", "".join(additions) + "\n</body>", 1)
    return body


def enhance_sitewide_ui(body: str, nonce: str, *, premium_access: bool) -> str:
    if 'id="v328-sitewide-script"' in body:
        return body
    body = _ensure_optional_blocks(body, premium_access)
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v328_handler(
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
    BaseHandler = progressive.make_v327_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V328Handler(BaseHandler):
        server_version = "KriptoPanel/3.28"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    if session:
                        info = self._plan_info(session)
                        premium_access = str(info.get("plan") or "") != commercial.PLAN_FREE
                        body = enhance_sitewide_ui(body, str(nonce or ""), premium_access=premium_access)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "sitewide_information_hierarchy": True,
                    "home_primary": "daily_pulse_and_featured_signals",
                    "home_secondary": "optional_more_information",
                    "mobile_primary_nav_max": 5,
                    "mobile_signal_details": "on_demand",
                    "mobile_trade_levels": "on_demand",
                    "real_home_marker_guard": True,
                    "member_focus": "consolidated_not_deleted",
                    "signal_guide": "preserved_optional",
                    "data_heartbeat": "preserved_optional",
                    "data_provenance": "preserved_optional",
                    "admin_tools": "preserved_via_admin_center",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V328Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.28 Genel Sade UX")
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
    handler = make_v328_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} sitewide_ux=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
