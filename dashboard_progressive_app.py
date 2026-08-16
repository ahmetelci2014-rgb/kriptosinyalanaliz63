"""Kripto Kontrol Merkezi V3.27 - Sade Görünüm / İsteğe Bağlı Detay.

V3.26 canlı veri heartbeat ve V3.25 birleşik stabil panel korunur. Bu katman
yalnız sunum davranışını sadeleştirir:
- Canlı Veri Kontrolü ayrıntıları varsayılan olarak kapalıdır.
- Sinyal Rehberi ayrıntıları varsayılan olarak kapalıdır.
- Kullanıcı isterse tek dokunuşla ayrıntıları açabilir.
- Veri akışında dikkat gerektiren durum varsa sağlık ayrıntıları otomatik açılır.

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
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_27_PROGRESSIVE_DISCLOSURE_2026_08_16"

CSS = r'''
/* V3.27 progressive disclosure: önce özet, sonra isteğe bağlı detay */
.v327-optional{position:relative}
.v327-toggle{border:1px solid #29444c;background:#081820;color:#9bb4b0;border-radius:999px;padding:6px 9px;font-size:8px;font-weight:900;cursor:pointer;white-space:nowrap;min-height:30px}
.v327-toggle:hover{border-color:rgba(44,230,191,.46);color:#d8ebe8}.v327-toggle:focus-visible{outline:2px solid #2ce6bf;outline-offset:2px}
#v326DataHealth.v327-collapsed .v326-grid{display:none}
#v326DataHealth.v327-collapsed{padding-bottom:9px}
#v324SignalGuide.v327-collapsed .v324-list{display:none}
#v324SignalGuide.v327-collapsed{padding-bottom:10px}
.v327-inline-note{margin-top:5px;color:#68837f;font-size:7px;line-height:1.4}
.v327-open .v327-inline-note{display:none}
@media(max-width:760px){.v327-toggle{min-height:34px;padding:7px 10px}.v326-head .v327-toggle,.v324-guide-head .v327-toggle{align-self:flex-start}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v327-progressive-script">
(()=>{'use strict';if(window.__v327Progressive)return;window.__v327Progressive=true;
function button(labelOpen,labelClose){const b=document.createElement('button');b.type='button';b.className='v327-toggle';b.dataset.openLabel=labelOpen;b.dataset.closeLabel=labelClose;b.setAttribute('aria-expanded','false');b.textContent=labelOpen;return b}
function setOpen(section,btn,open){section.classList.toggle('v327-collapsed',!open);section.classList.toggle('v327-open',open);btn.setAttribute('aria-expanded',open?'true':'false');btn.textContent=open?btn.dataset.closeLabel:btn.dataset.openLabel}
function install(sectionId,headSelector,openLabel,closeLabel,note){const section=document.getElementById(sectionId);if(!section||section.dataset.v327Ready==='1')return null;section.dataset.v327Ready='1';section.classList.add('v327-optional','v327-collapsed');const head=section.querySelector(headSelector)||section.firstElementChild;if(!head)return null;const b=button(openLabel,closeLabel);b.setAttribute('aria-controls',sectionId);b.addEventListener('click',()=>setOpen(section,b,section.classList.contains('v327-collapsed')));head.appendChild(b);if(note){const n=document.createElement('div');n.className='v327-inline-note';n.textContent=note;section.appendChild(n)}return {section,button:b}}
function installAll(){
 const health=install('v326DataHealth','.v326-head','Sistem ayrıntılarını göster','Sistem ayrıntılarını gizle','Sistem sağlıklıysa ayrıntıları açmana gerek yok.');
 const guide=install('v324SignalGuide','.v324-guide-head','Sinyal açıklamalarını göster','Sinyal açıklamalarını gizle','Coin, yön ve temel sinyal bilgileri ana ekranda kalır; teknik açıklama isteğe bağlıdır.');
 if(health){const badge=document.getElementById('v326HealthOverall');const inspect=()=>{const t=String(badge?.textContent||'').toUpperCase();if(t.includes('KONTROL ET')||t.includes('İZLE'))setOpen(health.section,health.button,true)};inspect();if(badge)new MutationObserver(inspect).observe(badge,{childList:true,characterData:true,subtree:true})}
 if(guide){const list=document.getElementById('v324GuideList');const update=()=>{const count=list?.querySelectorAll('.v324-card').length||0;guide.button.dataset.openLabel=count?`Sinyal açıklamalarını göster (${count})`:'Sinyal açıklamalarını göster';if(guide.section.classList.contains('v327-collapsed'))guide.button.textContent=guide.button.dataset.openLabel};update();if(list)new MutationObserver(update).observe(list,{childList:true,subtree:true})}
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',installAll,{once:true});else installAll();
})();
</script>
'''


def enhance_progressive_ui(body: str, nonce: str) -> str:
    """V3.26 ve V3.24 ayrıntılarını kaybetmeden varsayılan görünümü sadeleştirir."""
    if 'id="v327-progressive-script"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v327_handler(
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
    BaseHandler = datahealth.make_v326_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V327Handler(BaseHandler):
        server_version = "KriptoPanel/3.27"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    if session:
                        info = self._plan_info(session)
                        if str(info.get("plan") or "") != commercial.PLAN_FREE:
                            body = enhance_progressive_ui(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "cumulative_ui": True,
                    "progressive_disclosure": True,
                    "data_health_collapsible": True,
                    "signal_guide_collapsible": True,
                    "health_auto_opens_on_attention": True,
                    "member_focus": "preserved",
                    "data_heartbeat": "preserved",
                    "signal_guide": "preserved",
                    "admin_tools": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V327Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.27 Sade Görünüm")
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
    handler = make_v327_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} progressive_disclosure=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
