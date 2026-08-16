"""Kripto Kontrol Merkezi V3.20 R1 - Öğrenme Merkezi görünürlük düzeltmesi.

V3.20 Sistem Öğrenme Merkezi korunur. Bu katman yalnız ADMIN ana panelinde
Öğrenme Merkezi bağlantısının, V3.19 navigasyon enjeksiyonundan önce de güvenilir
biçimde görünmesini sağlar.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger ve otomatik
filtre mantığı değiştirilmez.
"""
from __future__ import annotations

import argparse
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_learning_app as learning
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_20_R1_LEARNING_NAV_FIX_2026_08_16"


def ensure_learning_navigation(body: str, path: str) -> str:
    """Inject the admin learning link using anchors that exist before V3.19 decoration."""
    if 'href="/learning-center"' in body:
        return body
    if path == "/":
        item = '<a class="nav-item" href="/learning-center"><span>◎</span><b>Öğrenme</b></a>'
        for anchor in (
            '<a class="nav-item" href="/market-center">',
            '<a class="nav-item" href="/performance">',
        ):
            if anchor in body:
                return body.replace(anchor, item + anchor, 1)
        anchor = '<a href="/market-center">Piyasayı incele</a>'
        if anchor in body:
            return body.replace(anchor, anchor + '<a href="/learning-center">Öğrenme Merkezi</a>', 1)
    return body


def make_v320r1_handler(
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
    BaseHandler = learning.make_v320_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V320R1Handler(BaseHandler):
        server_version = "KriptoPanel/3.20-r1"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    if self._is_admin_session(session):
                        body = ensure_learning_navigation(body, path)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "learning_center": True,
                    "admin_only": True,
                    "learning_navigation_fix": True,
                    "automatic_filter": False,
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V320R1Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.20 R1 Öğrenme görünürlük düzeltmesi")
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
    handler = make_v320r1_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} admin_only=1 learning_nav_fix=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
