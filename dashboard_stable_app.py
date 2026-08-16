"""Kripto Kontrol Merkezi V3.25 - Birleşik Stabil Panel.

Amaç: ardışık panel katmanlarında özelliklerin rol nedeniyle görünmez hale gelmesini
önlemek. V3.24 zinciri korunur; ADMIN ana ekranında üye ürün deneyimi de görünür
olur. Böylece yönetici hem ürünü kullanıcı gözüyle test eder hem de admin araçlarına
erişmeye devam eder.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger yazımı ve
otomatik filtre davranışı değiştirilmez.
"""
from __future__ import annotations

import argparse
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
import dashboard_memberfocus_app as memberfocus
import dashboard_signalguide_app as signalguide
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_25_CUMULATIVE_STABLE_2026_08_16"


def enhance_admin_product_view(body: str, nonce: str) -> str:
    """ADMIN ana ekranında MEMBER ürün UX'ini kaybetmeden görünür tutar."""
    body = memberfocus.enhance_member_home(body, nonce)
    body = signalguide.enhance_signal_guide(body, nonce)
    body = body.replace("ÜYE · HIZLI KULLANIM", "YÖNETİCİ · ÜRÜN GÖRÜNÜMÜ", 1)
    body = body.replace(
        "Şimdi ne yapmak istiyorsun?",
        "Üye panelini ve canlı sinyalleri tek bakışta kontrol et",
        1,
    )
    return body


def make_v325_handler(
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
    BaseHandler = signalguide.make_v324_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        candle_client,
        overview_client,
        history_cache=cache,
    )

    class V325Handler(BaseHandler):
        server_version = "KriptoPanel/3.25"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    is_admin = bool(session) and str(session.get("role") or "").upper() == commercial.ROLE_ADMIN
                    if is_admin:
                        body = enhance_admin_product_view(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "cumulative_ui": True,
                    "regression_guard": True,
                    "member_focus_visible_to_admin": True,
                    "signal_guide_visible_to_admin": True,
                    "admin_tools": "preserved",
                    "data_provenance": "preserved",
                    "learning_center": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V325Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.25 Birleşik Stabil Panel")
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
    handler = make_v325_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} cumulative_ui=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
