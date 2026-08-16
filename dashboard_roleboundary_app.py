"""Kripto Kontrol Merkezi V3.31 - Üye / Yönetici rol sınırları.

V3.30 ve önceki bütün panel katmanlarını korur. Bu sürüm yalnız erişim ve
sunum sınırlarını netleştirir:
- /advanced yalnız ADMIN hesabına açıktır.
- Normal üyede Gelişmiş Görünüm bağlantısı ana menüden kaldırılır.
- ADMIN için bağlantı "Teknik Görünüm" olarak adlandırılır.
- Yönetim Merkezi'nde günlük yönetim alanları önde kalır; Analiz & Geliştirme
  araçları varsayılan olarak kapalı, isteğe bağlı açılır.
- Teknik görünümde Yönetim Merkezi'ne geri dönüş ve ADMIN sınırı görünürdür.

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

import dashboard_accountux_app as accountux
import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_31_ROLE_BOUNDARY_2026_08_16"

CSS = r'''
/* V3.31 - üye / admin sınırı */
html[data-admin="false"] .nav-item[href="/advanced"]{display:none!important}
.v331-admin-hub-ready #v321AdminAnalysisHub.v331-collapsed .v321-tools{display:none!important}
.v331-admin-hub-ready #v321AdminAnalysisHub.v331-collapsed{padding-bottom:10px!important}
.v331-admin-toggle{border:1px solid #29444c;background:#081820;color:#9db5b1;border-radius:9px;padding:7px 9px;font-size:8px;font-weight:900;cursor:pointer;white-space:nowrap}
.v331-admin-toggle:hover{border-color:#2ce6bf;color:#2ce6bf}
.v331-admin-boundary{border:1px solid rgba(255,189,89,.22);background:rgba(255,189,89,.045);border-radius:11px;padding:8px 10px;color:#a9bebb;font-size:9px;margin:0 0 12px;display:flex;align-items:center;gap:7px}
.v331-admin-boundary b{color:#ffbd59;font-size:8px;letter-spacing:.05em}
.v331-advanced-bar{position:sticky;top:0;z-index:999;display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:9px 12px;border-bottom:1px solid #263e47;background:rgba(6,16,22,.96);backdrop-filter:blur(14px);color:#a9bfbc;font:800 10px/1.3 Inter,system-ui,sans-serif}
.v331-advanced-bar strong{color:#ffbd59;margin-right:auto}.v331-advanced-bar a{border:1px solid #29444c;border-radius:8px;padding:7px 9px;background:#081820;color:#a9bfbc!important;text-decoration:none!important}.v331-advanced-bar a:hover{border-color:#2ce6bf;color:#2ce6bf!important}
@media(max-width:760px){.v331-admin-toggle{min-height:34px}.v331-admin-boundary{align-items:flex-start}.v331-advanced-bar strong{width:100%;margin-right:0}.v331-advanced-bar a{flex:1;text-align:center;min-height:36px;display:grid;place-items:center}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v331-role-script">
(()=>{'use strict';if(window.__v331RoleBoundary)return;window.__v331RoleBoundary=true;
const IS_ADMIN=__IS_ADMIN__;
function rootNav(){const link=document.querySelector('.nav-item[href="/advanced"]');if(!link)return;if(!IS_ADMIN){link.remove();return}const b=link.querySelector('b');if(b)b.textContent='Teknik Görünüm';link.setAttribute('title','Yalnız yönetici teknik görünümü')}
function adminHub(){if(location.pathname!=='/admin/center'||!IS_ADMIN)return;document.body.classList.add('v331-admin-hub-ready');const hub=document.getElementById('v321AdminAnalysisHub');if(!hub||hub.dataset.v331Ready==='1')return;hub.dataset.v331Ready='1';hub.classList.add('v331-collapsed');const head=hub.querySelector('.v321-head');if(head){const btn=document.createElement('button');btn.type='button';btn.className='v331-admin-toggle';btn.textContent='Analiz araçlarını göster';btn.setAttribute('aria-expanded','false');btn.addEventListener('click',()=>{const open=hub.classList.toggle('v331-collapsed')===false;btn.textContent=open?'Analiz araçlarını gizle':'Analiz araçlarını göster';btn.setAttribute('aria-expanded',open?'true':'false')});head.appendChild(btn)}const note=document.createElement('div');note.className='v331-admin-boundary';note.innerHTML='<b>YALNIZ ADMIN</b><span>Üyelik, kullanıcı ve sistem yönetimi burada; analiz araçları yalnız gerektiğinde açılır.</span>';hub.insertAdjacentElement('afterend',note)}
function init(){rootNav();adminHub()}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
</script>
'''


def _inject_style(body: str) -> str:
    if "</style>" in body:
        return body.replace("</style>", CSS + "\n</style>", 1)
    if "</head>" in body:
        return body.replace("</head>", f"<style>{CSS}</style>\n</head>", 1)
    return body


def enhance_role_ui(body: str, nonce: str, *, is_admin: bool) -> str:
    """Ana panel ve admin merkezine salt sunum rol sınırı ekler."""
    if 'id="v331-role-script"' in body:
        return body
    body = _inject_style(body)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True)).replace(
        "__IS_ADMIN__", "true" if is_admin else "false"
    )
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def enhance_advanced_admin(body: str) -> str:
    """ADMIN teknik görünümüne görünür yönetim sınırı ve geri dönüş ekler."""
    if 'class="v331-advanced-bar"' in body:
        return body
    bar = (
        '<div class="v331-advanced-bar">'
        '<strong>ADMIN · TEKNİK GÖRÜNÜM</strong>'
        '<span>Üyelere gösterilmez</span>'
        '<a href="/admin/center">Yönetim Merkezi</a>'
        '<a href="/">Ürün Paneli</a>'
        '</div>'
    )
    body = _inject_style(body)
    if "<body>" in body:
        return body.replace("<body>", "<body>" + bar, 1)
    if '<body class="' in body:
        pos = body.find('> ', body.find('<body class="'))
        if pos >= 0:
            return body[:pos + 1] + bar + body[pos + 1:]
    return body


def make_v331_handler(
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
    BaseHandler = accountux.make_v330_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V331Handler(BaseHandler):
        server_version = "KriptoPanel/3.31"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                session = self._session()
                is_admin = bool(session and self._is_admin_session(session))
                if path in {"/", "/admin/center"} and session:
                    body = enhance_role_ui(body, str(nonce or ""), is_admin=is_admin)
                elif path == "/advanced" and is_admin:
                    body = enhance_advanced_admin(body)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "advanced_admin_only": True,
                    "member_advanced_nav_hidden": True,
                    "admin_analysis_default": "collapsed",
                    "admin_center": "preserved",
                    "account_ux": "preserved",
                    "membership_backend": "unchanged",
                    "payment_backend": "unchanged",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            if path == "/advanced":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                if not self._is_admin_session(session):
                    self._redirect("/")
                    return
            return super().do_GET()

    return V331Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.31 Rol Sınırları")
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
    handler = make_v331_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} advanced_admin_only=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
