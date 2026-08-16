"""Kripto Kontrol Merkezi V3.35 - mobil dokunma acil koruması.

V3.34 ve önceki katmanları korur. Bu katman yalnız mobil etkileşim sorununu hedefler:
- Kapalı Focus ve Bildirim çekmece/overlay katmanları kesinlikle dokunma yakalayamaz.
- Sayfa açılışında yanlışlıkla açık kalmış overlay/çekmece sınıfları temizlenir.
- Body overflow kilidi, gerçekten açık bir çekmece yoksa kaldırılır.
- Mobil alt menü ve ana içerik açıkça pointer-events:auto olarak korunur.
- Sayfa görünür olduğunda ve bfcache dönüşünde koruma yeniden uygulanır.

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
import dashboard_mobileux_app as mobile
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_35_TOUCH_GUARD_2026_08_16"

CSS = r'''
/* V3.35 - görünmez sabit katmanlar mobil dokunmayı asla yutmasın */
.focus-overlay:not(.open),.notify-overlay:not(.open){
  opacity:0!important;visibility:hidden!important;pointer-events:none!important;
}
.focus-drawer:not(.open),.notify-drawer:not(.open){
  visibility:hidden!important;pointer-events:none!important;
}
.focus-overlay.open,.notify-overlay.open{visibility:visible!important;pointer-events:auto!important}
.focus-drawer.open,.notify-drawer.open{visibility:visible!important;pointer-events:auto!important}
@media(max-width:760px){
  body,.app,.content,main,.page,.panel,.panel-body{pointer-events:auto!important}
  .mobile-nav{pointer-events:auto!important;z-index:70!important}
  .mobile-nav button,.mobile-nav a{pointer-events:auto!important;touch-action:manipulation!important}
  #v333Status{pointer-events:auto!important}
  #v333Status .v333-live{pointer-events:none!important}
  #v333VoiceToggle{pointer-events:auto!important}
  .alert-stack{pointer-events:none!important}
  .alert-toast{pointer-events:auto!important}
}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v335-touchguard-script">
(()=>{'use strict';if(window.__v335TouchGuard)return;window.__v335TouchGuard=true;
const $=id=>document.getElementById(id);
function isOpen(el){return !!el&&el.classList.contains('open')}
function normalizeClosedLayer(overlayId,drawerId){
 const overlay=$(overlayId),drawer=$(drawerId);
 const actuallyOpen=isOpen(overlay)&&isOpen(drawer);
 if(!actuallyOpen){
   overlay?.classList.remove('open');drawer?.classList.remove('open');
   drawer?.setAttribute('aria-hidden','true');
 }
 return actuallyOpen;
}
function repair(){
 if(location.pathname!=='/')return;
 const focusOpen=normalizeClosedLayer('focusOverlay','focusDrawer');
 const notifyOpen=normalizeClosedLayer('notifyOverlay','notifyDrawer');
 if(!focusOpen&&!notifyOpen&&document.body.style.overflow==='hidden')document.body.style.overflow='';
 document.querySelectorAll('.mobile-nav button,.mobile-nav a').forEach(el=>{
   el.style.pointerEvents='auto';el.style.touchAction='manipulation';
 });
}
function install(){
 repair();
 document.addEventListener('visibilitychange',()=>{if(!document.hidden)repair()});
 window.addEventListener('pageshow',repair);
 window.addEventListener('orientationchange',()=>setTimeout(repair,50));
 setTimeout(repair,250);
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',install,{once:true});else install();
})();
</script>
'''


def enhance_touch_guard(body: str, nonce: str) -> str:
    if 'id="v335-touchguard-script"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v335_handler(
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
    BaseHandler = mobile.make_v334_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V335Handler(BaseHandler):
        server_version = "KriptoPanel/3.35"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/" and self._session():
                    body = enhance_touch_guard(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "mobile_touch_guard": True,
                    "closed_overlays_pointer_events": "none",
                    "closed_drawers_pointer_events": "none",
                    "body_overflow_recovery": True,
                    "mobile_navigation_clickable": True,
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

    return V335Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.35 Mobil Dokunma Koruması")
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
    handler = make_v335_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} touch_guard=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
