"""Kripto Kontrol Merkezi V2.9 - Sayfa Konumu Koruma.

V2.8 panelini değiştirmeden yalnız istemci tarafındaki aktif sekmeyi URL hash içinde
saklar. Kullanıcı F5 / tarayıcı yenileme yaptığında aynı panel sekmesi yeniden açılır.
Sinyal üretimi, strateji, radar, Telegram ve emir akışı bu katmanda yoktur.
"""

from __future__ import annotations

import argparse
import os
import secrets
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_filter_app as filters
import dashboard_market_app as market
import dashboard_product_app as product
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_9_VIEW_PERSISTENCE_2026_08_14"


def route_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = filters.filter_dashboard_page(session, nonce)
    nonce_attr = str(nonce).replace('"', "&quot;")

    script = r'''
<script nonce="__NONCE__">
(() => {
  const HASH_KEY='view';

  function allViews(){
    return [...document.querySelectorAll('.page[id^="page-"]')]
      .map(node=>String(node.id||'').replace(/^page-/,''))
      .filter(Boolean);
  }

  function available(view){
    return Boolean(view && allViews().includes(view) && [...document.querySelectorAll('[data-view]')].some(node=>node.dataset.view===view));
  }

  function readView(){
    const raw=String(location.hash||'').replace(/^#/, '');
    if(!raw)return '';
    if(!raw.includes('='))return decodeURIComponent(raw).trim();
    try{return new URLSearchParams(raw).get(HASH_KEY)||'';}catch{return '';}
  }

  function writeView(view){
    if(!available(view))return;
    const next=new URL(location.href);
    next.hash=`${HASH_KEY}=${encodeURIComponent(view)}`;
    history.replaceState({view},'',next);
  }

  function activate(view){
    if(!available(view))return false;
    const target=[...document.querySelectorAll('[data-view]')].find(node=>node.dataset.view===view);
    if(!target)return false;
    const active=document.querySelector('.page.active');
    if(active?.id===`page-${view}`){writeView(view);return true;}
    target.click();
    writeView(view);
    return true;
  }

  function restore(){
    const requested=readView();
    if(requested && activate(requested))return;
    const current=String(document.querySelector('.page.active')?.id||'').replace(/^page-/,'')||'home';
    if(available(current))writeView(current);
  }

  document.addEventListener('click',event=>{
    const nav=event.target.closest('[data-view]');
    if(!nav)return;
    const view=String(nav.dataset.view||'');
    if(available(view))queueMicrotask(()=>writeView(view));
  },true);

  window.addEventListener('hashchange',()=>{
    const view=readView();
    if(view)activate(view);
  });

  requestAnimationFrame(restore);
})();
</script>
'''.replace("__NONCE__", nonce_attr)
    return body.replace("</body>", script + "\n</body>", 1)


def make_v29_handler(
    config: PanelConfig,
    service,
    sessions,
    limiter: LoginRateLimiter,
    store,
    market_client=None,
    overview_client=None,
):
    BaseHandler = filters.make_v28_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
        overview_client,
    )

    class V29Handler(BaseHandler):
        server_version = "KriptoPanel/2.9"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, route_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            return super().do_GET()

    return V29Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.9 sayfa konumu koruma.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = product.account_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v29_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
        overview_client,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} view_persistence=on signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
