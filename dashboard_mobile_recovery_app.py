"""Kripto Kontrol Merkezi V3.36 - acil mobil etkileşim kurtarma modu.

V3.35 ve önceki katmanları korur. Bu katman yalnız mobilde yaşanan
"sayfa açılıyor ama hiçbir yere basılmıyor" sorununu izole eder:
- Eski Focus ve Bildirim drawer/overlay katmanlarını mobilde tamamen devre dışı bırakır.
- Mobil alt menüyü capture aşamasında doğrudan çalıştırarak eski JS katmanlarından bağımsız hale getirir.
- Sayfa/bfcache/orientation dönüşlerinde body overflow ve görünmez katman durumunu sıfırlar.
- Desktop deneyimini ve bütün canlı sinyal/Telegram/TP-SL/ledger davranışını değiştirmez.

Bu acil modda mobil hızlı Focus çekmecesi ve Bildirim çekmecesi geçici olarak kapalıdır;
Piyasa, Coin Merkezi ve ana ürün rotaları çalışmaya devam eder.
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
import dashboard_touchguard_app as touch
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_36_MOBILE_RECOVERY_2026_08_16"

CSS = r'''
/* V3.36 - mobil tıklama kurtarma: görünmez çekmeceler DOM'da olsa bile hit-test dışı */
@media (max-width:900px), (hover:none) and (pointer:coarse){
  .focus-overlay,.focus-drawer,.notify-overlay,.notify-drawer{
    display:none!important;
    visibility:hidden!important;
    opacity:0!important;
    pointer-events:none!important;
  }
  #notifyTrigger,.coin-quick{display:none!important}
  html,body,.app,.content,main,.page,.panel,.panel-body{pointer-events:auto!important}
  .mobile-nav{pointer-events:auto!important;z-index:120!important;isolation:isolate!important}
  .mobile-nav button,.mobile-nav a{pointer-events:auto!important;touch-action:manipulation!important;-webkit-tap-highlight-color:rgba(44,230,191,.12)}
}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v336-mobile-recovery-script">
(()=>{'use strict';if(window.__v336MobileRecovery)return;window.__v336MobileRecovery=true;
const MOBILE=()=>window.matchMedia('(max-width:900px), (hover:none) and (pointer:coarse)').matches;
const $=id=>document.getElementById(id);
const layerIds=['focusOverlay','focusDrawer','notifyOverlay','notifyDrawer'];
function hardReset(){
  if(location.pathname!=='/'||!MOBILE())return;
  document.body.dataset.mobileRecovery='1';
  layerIds.forEach(id=>{const el=$(id);if(!el)return;el.classList.remove('open');el.setAttribute('aria-hidden','true');el.style.pointerEvents='none';el.style.visibility='hidden';});
  document.body.style.overflow='';
}
function switchMobileView(view){
  const target=$(`page-${view}`);if(!target)return false;
  document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el===target));
  document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
  const titles={home:'Ana Sayfa',signals:'Sinyaller',trades:'İşlemler',results:'Sonuçlar',system:'Sistem'};
  const title=$('topTitle');if(title)title.textContent=titles[view]||'Kripto Kontrol';
  window.scrollTo(0,0);return true;
}
function captureMobileNavigation(event){
  if(location.pathname!=='/'||!MOBILE())return;
  const viewEl=event.target.closest('.mobile-nav [data-view]');
  if(viewEl){
    const view=String(viewEl.dataset.view||'');
    if(switchMobileView(view)){event.preventDefault();event.stopImmediatePropagation();hardReset();}
    return;
  }
  const link=event.target.closest('.mobile-nav a[href]');
  if(link){
    const href=link.getAttribute('href');
    if(href){event.preventDefault();event.stopImmediatePropagation();hardReset();location.assign(href);}
  }
}
function install(){
  if(location.pathname!=='/'||!MOBILE())return;
  hardReset();
  document.addEventListener('click',captureMobileNavigation,true);
  document.addEventListener('touchend',hardReset,{passive:true,capture:true});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)hardReset()});
  window.addEventListener('pageshow',hardReset);
  window.addEventListener('orientationchange',()=>setTimeout(hardReset,80));
  setTimeout(hardReset,100);setTimeout(hardReset,700);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
</script>
'''


def enhance_mobile_recovery(body: str, nonce: str) -> str:
    if 'id="v336-mobile-recovery-script"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v336_handler(
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
    BaseHandler = touch.make_v335_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V336Handler(BaseHandler):
        server_version = "KriptoPanel/3.36"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/" and self._session():
                    body = enhance_mobile_recovery(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "mobile_emergency_recovery": True,
                    "mobile_focus_drawer": "temporarily_disabled",
                    "mobile_notification_drawer": "temporarily_disabled",
                    "mobile_navigation": "capture_direct",
                    "mobile_body_lock_reset": True,
                    "touch_guard": "preserved",
                    "mobile_repair": "preserved",
                    "simple_voice": "preserved",
                    "market_coin_ux": "preserved",
                    "role_boundary": "preserved",
                    "account_ux": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V336Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.36 Mobil Etkileşim Kurtarma")
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
    handler = make_v336_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} mobile_recovery=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
