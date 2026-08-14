"""Kripto Kontrol Merkezi V2.1 - coin odaklı son web girişi.

Hızlı coin analiz çekmecesini çalıştırır ve Piyasa Merkezi bağlantılarında
`?symbol=...&bar=...` parametrelerini açılışta uygular. Yalnız panel katmanıdır.
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

import dashboard_focus_app as focus
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_1_2026_08_14"
ALLOWED_BARS = {"1m", "5m", "15m", "1H", "4H", "1D"}


def market_page_with_selection(nonce: str, symbol_value: str = "BTCUSDT", bar_value: str = "15m") -> str:
    try:
        symbol = OKXMarketDataClient.normalize_symbol(symbol_value or "BTCUSDT")
    except ValueError:
        symbol = "BTCUSDT"
    bar = bar_value if bar_value in ALLOWED_BARS else "15m"
    body = market.market_center_page(nonce)
    safe_symbol = html.escape(symbol, quote=True)
    body = body.replace('id="symbolInput" value="BTCUSDT"', f'id="symbolInput" value="{safe_symbol}"', 1)
    startup = "loadOverview().then(()=>loadChart('BTCUSDT'));"
    replacement = (
        f"$('barSelect').value={bar!r};"
        f"loadOverview({symbol!r}).then(()=>loadChart({symbol!r}));"
    )
    body = body.replace(startup, replacement, 1)
    return body


def make_handler(config: PanelConfig, service, sessions, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = focus.make_v21_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V21FocusHandler(BaseHandler):
        server_version = "KriptoPanel/2.1"

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            if parsed.path == "/market-center":
                if not self._session():
                    self._redirect("/login")
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=4)
                symbol = (query.get("symbol") or ["BTCUSDT"])[0]
                bar = (query.get("bar") or ["15m"])[0]
                nonce = secrets.token_urlsafe(18)
                self._send(
                    HTTPStatus.OK,
                    market_page_with_selection(nonce, symbol, bar),
                    "text/html; charset=utf-8",
                    nonce=nonce,
                )
                return
            return super().do_GET()

    return V21FocusHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.1 coin odaklı arayüz.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = focus.v2.v19.v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = focus.v2.v19.v18.account_store_from_env(config)
    handler = make_handler(config, service, sessions, limiter, store, OKXMarketDataClient(), market.OKXMarketOverviewClient())
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} quick_coin=on direct_market_selection=on")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
