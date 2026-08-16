"""Kripto Kontrol Merkezi V3.34 - mobil kullanılabilirlik onarımı.

V3.33 ve önceki katmanları korur. Bu katman yalnız mobil sunumu düzeltir:
- Alt menüde Ana / Sinyal / Piyasa / Sonuç / Hesap tekrar görünür.
- Mobil dokunma alanları büyür ve iOS/Android form yakınlaştırma sorunları azalır.
- İçerik alt menünün altında kalmaz; yatay taşmalar sınırlandırılır.
- CANLI / sesli bildirim şeridi telefonda daha kompakt hale gelir.
- Sinyal kartları ve ana içerik küçük ekranlarda daha rahat okunur.

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
import dashboard_simplevoice_app as voice
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_34_MOBILE_REPAIR_2026_08_16"

CSS = r'''
/* V3.34 - telefonda gerçek kullanılabilirlik onarımı */
@media(max-width:760px){
  html,body{max-width:100%;overflow-x:hidden!important}
  body{padding-bottom:calc(86px + env(safe-area-inset-bottom))!important}
  .content{width:100%!important;max-width:100%!important;overflow-x:hidden!important}
  .topbar{height:54px!important;padding:0 10px!important;gap:6px!important}
  .top-title{min-width:0!important;max-width:110px!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}
  main{width:calc(100% - 16px)!important;max-width:100%!important;padding:14px 0 28px!important}
  .page,.panel,.panel-body,.table-list,.row-card,.wide-card,.grid-2,.summary{min-width:0!important;max-width:100%!important}
  .page-head{margin-bottom:13px!important;gap:8px!important}.page-head h1{font-size:21px!important}.page-head p{font-size:10px!important;line-height:1.45!important}

  /* V3.33'te gizlenen Piyasa'yı geri getir. */
  body .mobile-nav a[href="/market-center"]{display:flex!important}
  .mobile-nav{display:flex!important;height:74px!important;min-height:74px!important;align-items:stretch!important;padding:2px 4px env(safe-area-inset-bottom)!important;gap:0!important;background:rgba(8,18,25,.985)!important;backdrop-filter:blur(14px)!important}
  .mobile-nav button,.mobile-nav a{display:flex!important;flex:1 1 20%!important;min-width:0!important;min-height:58px!important;padding:6px 2px!important;justify-content:center!important;gap:3px!important;font-size:8px!important;line-height:1.1!important;touch-action:manipulation!important}
  .mobile-nav span{font-size:18px!important;line-height:1!important}.mobile-nav .active{color:var(--teal)!important}

  /* CANLI + ses şeridi küçük ekranda yer kaplamasın. */
  #v333Status{min-height:44px!important;padding:5px 9px!important;gap:6px!important;display:grid!important;grid-template-columns:minmax(0,1fr) auto!important}
  #v333Status .v333-live{min-width:0!important}.v333-live-copy b{font-size:9px!important}.v333-live-copy small{font-size:7px!important;max-width:46vw!important}
  #v333Status .v333-last{display:none!important}
  #v333VoiceToggle{min-height:34px!important;max-width:104px!important;padding:5px 8px!important;font-size:7.5px!important;line-height:1.15!important;white-space:normal!important;text-align:center!important}

  /* Küçük ekranda dokunma ve form kullanılabilirliği. */
  button,a.btn,.v328-card-toggle,.v328-level-toggle,.v329-coin-link{touch-action:manipulation}
  .toolbar{gap:6px!important}.toolbar input,.toolbar select,.v328-coin-tool input{font-size:16px!important;min-height:42px!important}
  .toolbar input{min-width:100%!important;width:100%!important}.toolbar select{flex:1 1 calc(50% - 4px)!important;min-width:0!important}
  .btn,.v328-card-toggle,.v328-level-toggle,.v329-coin-link{min-height:38px!important;display:inline-flex!important;align-items:center!important;justify-content:center!important}

  /* Sinyal kartları: coin + yön + giriş ilk bakışta, taşma yok. */
  #page-signals .row-card{width:100%!important;grid-template-columns:minmax(0,1fr) auto auto!important;padding:11px 10px!important;gap:7px!important;overflow:hidden!important}
  #page-signals .row-card .coin{min-width:0!important}#page-signals .row-card .coin strong{font-size:13px!important}#page-signals .row-card .coin small{font-size:8px!important;white-space:nowrap!important;overflow:hidden!important;text-overflow:ellipsis!important}
  #page-signals .row-card .tag{font-size:9px!important;padding:5px 8px!important}
  #page-signals .row-card>.btn{grid-column:1/2!important;justify-self:start!important;max-width:100%!important}
  #page-signals .v328-card-toggle{grid-column:3/4!important;grid-row:1/2!important}

  #page-trades .wide-card{padding:11px!important}.wide-top{gap:7px!important}.levels{gap:5px!important}.level{padding:7px!important}
  #page-results .result-item{padding:10px 2px!important}.result-main strong{font-size:11px!important}.result-main div{font-size:8px!important}

  /* Ana sayfa mevcut kartlarını daha telefon gibi göster; yeni kutu eklemez. */
  #page-home .summary,#homeSmartMetrics{gap:7px!important}.metric{min-height:76px!important;padding:11px!important;border-radius:13px!important}.metric strong{font-size:20px!important}.metric small{font-size:8px!important}
  #page-home .panel{border-radius:13px!important}.panel-head{padding:12px!important}.panel-body{padding:9px!important}
}
@media(max-width:390px){
  .top-title{max-width:86px!important}
  #v333VoiceToggle{max-width:88px!important;font-size:7px!important}
  .mobile-nav button,.mobile-nav a{font-size:7.5px!important;padding-inline:1px!important}
  .mobile-nav span{font-size:17px!important}
}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v334-mobile-script">
(()=>{'use strict';if(window.__v334MobileRepair)return;window.__v334MobileRepair=true;
function init(){
  if(location.pathname!=='/')return;
  document.body.classList.add('v334-mobile-repair');
  const market=document.querySelector('.mobile-nav a[href="/market-center"]');
  if(market){market.removeAttribute('aria-hidden');market.style.removeProperty('display')}
  const nav=document.querySelector('.mobile-nav');
  if(nav){
    const wanted=[
      ['[data-view="home"]','Ana'],['[data-view="signals"]','Sinyal'],['a[href="/market-center"]','Piyasa'],['[data-view="results"]','Sonuç'],['a[href="/account"]','Hesap']
    ];
    wanted.forEach(([selector,label])=>{const el=nav.querySelector(selector);if(el)el.setAttribute('aria-label',label)});
  }
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
</script>
'''


def enhance_mobile_ui(body: str, nonce: str) -> str:
    if 'id="v334-mobile-script"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v334_handler(
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
    BaseHandler = voice.make_v333_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V334Handler(BaseHandler):
        server_version = "KriptoPanel/3.34"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/" and self._session():
                    body = enhance_mobile_ui(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "mobile_navigation": ["Ana", "Sinyal", "Piyasa", "Sonuç", "Hesap"],
                    "mobile_market_visible": True,
                    "mobile_touch_targets": "improved",
                    "mobile_horizontal_overflow_guard": True,
                    "mobile_bottom_content_guard": True,
                    "voice_strip": "compact_mobile",
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

    return V334Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.34 Mobil Kullanılabilirlik")
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
    handler = make_v334_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} mobile_repair=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
