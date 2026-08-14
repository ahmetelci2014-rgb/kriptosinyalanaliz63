"""Kripto Kontrol Merkezi V3.5 runtime polish.

V3.5 ürün kalitesi katmanını korur ve V3.4 takip kartı nedeniyle FREE kota bandının
kaçırılabildiği DOM yerleşimini daha sağlam bir işaretleyiciyle tamamlar.
Sinyal/Telegram çekirdeğine dokunmaz.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_commercial_app as commercial
import dashboard_freepreview_app as freepreview
import dashboard_market_app as market
import dashboard_quality_app as quality
import dashboard_transparency_app as transparency
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_5_PRODUCT_QUALITY_R1_2026_08_15"


def ensure_free_quota_bar(body: str, nonce: str) -> str:
    if 'id="v35FreeQuotaLocked"' in body:
        return body
    bar = '''
<div class="v35-freebar v35-freebar-runtime" id="v35FreeQuotaBar">
  <div class="v35-freeitem"><small>FREE hakkın</small><b>1 gerçek sinyal</b></div>
  <div class="v35-freeitem"><small>Sonuç geçmişi</small><b>Son 5 kayıt</b></div>
  <div class="v35-freeitem"><small>Piyasa</small><b>6 canlı coin</b></div>
  <div class="v35-freeitem premium"><small>Premium kilitli</small><b id="v35FreeQuotaLocked">—</b></div>
</div>
'''
    marker = '<div class="grid"><div>'
    if marker in body:
        body = body.replace(marker, bar + marker, 1)
    else:
        marker = '<section class="card signal">'
        if marker in body:
            body = body.replace(marker, bar + marker, 1)

    nonce_attr = html.escape(nonce, quote=True)
    listener = f'''<script nonce="{nonce_attr}">(()=>{{
window.addEventListener('kripto-free-preview',e=>{{const d=e.detail||{{}},el=document.getElementById('v35FreeQuotaLocked');if(el)el.textContent=Number(d.locked_open_count||0)>0?String(d.locked_open_count)+' ek sinyal':'0 ek sinyal';}});
}})();</script>'''
    script_marker = '<script nonce="'
    pos = body.find(script_marker)
    if pos >= 0:
        body = body[:pos] + listener + "\n" + body[pos:]
    else:
        body = body.replace("</body>", listener + "\n</body>", 1)
    return body


def make_runtime_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    BaseHandler = quality.make_v35_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class RuntimeHandler(BaseHandler):
        server_version = "KriptoPanel/3.5-r1"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            info = self._plan_info(session)
            if str(info.get("plan")) != commercial.PLAN_FREE:
                return super()._render_root_v17(session)
            nonce = secrets.token_urlsafe(18)
            body = freepreview.free_preview_page(session, nonce)
            body = transparency.enhance_free_page(body, nonce)
            body = quality.enhance_free_quality(body, nonce)
            body = ensure_free_quota_bar(body, nonce)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "product_quality": True,
                    "free_quota_bar": True,
                    "signal_engine": "unchanged",
                })
                return
            return super().do_GET()

    return RuntimeHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.5 runtime polish.")
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
    handler = make_runtime_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} free_quota_bar=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
