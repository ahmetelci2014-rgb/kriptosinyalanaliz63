"""Kripto Kontrol Merkezi V3.32.8 - hesapla senkron İzleme Listesi.

V3.32.7 hesap güvenliği ve V3.32.6 yüzey paritesi korunur. Yeni davranış yalnız
Premium/Admin yönetilen panel_users hesaplarının İzleme Listesini cihazlar arasında
aynı hesap tercihiyle kullanmasını sağlar. İlk senkronizasyonda mevcut mobil cookie
ve masaüstü localStorage favorileri kaybolmadan hesaba taşınır. Kurucu/env hesapta
cihaz-local fallback korunur.

Trading, strategy/config, radar, Telegram, TP/SL/BE, state ve ledger değişmez.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accountflow_runtime_app as previous
import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_score_app as score
import dashboard_surface_parity_app as parity
import dashboard_watchsync_app as watchsync
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_8_WATCHLIST_SYNC_2026_08_16"
CSS = previous.CSS
SCRIPT = previous.SCRIPT


def make_v3321_handler(
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
    overview = overview_client or market.OKXMarketOverviewClient(cache_seconds=20)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    analysis_service = score.AnalysisScoreService(candle_client, overview, cache_seconds=120)
    BaseHandler = previous.make_v3321_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        candle_client,
        overview,
        history_cache=cache,
    )

    class V3328Handler(BaseHandler):
        server_version = "KriptoPanel/3.32.8"

        def _watch_access(self):
            session = self._session()
            if not session:
                return None, False
            return session, bool(self._is_premium(session))

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                session = self._session()
                if session and self._is_premium(session) and 'id="page-watchlist"' in body:
                    body = watchsync.enhance_desktop_watch_sync(
                        body,
                        csrf=str(session.get("csrf") or ""),
                        nonce=nonce,
                    )
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def _watch_snapshot(self, username: str) -> dict[str, Any]:
            return watchsync.account_watchlist_snapshot(store, username)

        def _serve_synced_mobile_watchlist(self, query: dict[str, list[str]]) -> None:
            session, is_admin, premium_flag, plan, label = self._identity()
            if not session:
                self._redirect("/login")
                return
            if not parity.premium_plan(plan):
                self._redirect("/premium")
                return

            username = str(session.get("username") or "")
            browser_symbols = parity.read_watchlist(self.headers.get("Cookie"))
            snapshot = self._watch_snapshot(username)
            managed = bool(snapshot.get("managed"))
            initialized = bool(snapshot.get("initialized"))
            server_symbols = watchsync.normalize_watchlist(snapshot.get("symbols") or [])
            cookie_to_set = None

            if managed:
                if initialized:
                    symbols = server_symbols
                else:
                    symbols = watchsync.first_sync_list(server_symbols, browser_symbols)
                    try:
                        symbols = watchsync.save_account_watchlist(store, username, symbols, actor=username)
                        initialized = True
                    except (accounts.AccountStoreError, ValueError):
                        managed = False
                        symbols = browser_symbols
                if managed and symbols != browser_symbols:
                    cookie_to_set = parity.watch_cookie(symbols, secure=config.cookie_secure)
            else:
                symbols = browser_symbols

            add = str((query.get("add") or [""])[0] or "")
            remove = str((query.get("remove") or [""])[0] or "")
            updated = parity.update_watchlist(symbols, add=add, remove=remove)
            if updated != symbols:
                symbols = updated
                if managed:
                    try:
                        symbols = watchsync.save_account_watchlist(store, username, symbols, actor=username)
                    except accounts.AccountStoreError:
                        managed = False
                cookie_to_set = parity.watch_cookie(symbols, secure=config.cookie_secure)

            try:
                payload = overview.get_overview(symbols) if symbols else {"items": []}
                items = [row for row in payload.get("items", []) if isinstance(row, dict)]
            except Exception:
                items = []

            score_symbol = str((query.get("tech") or [""])[0] or "").upper()
            technical = None
            if score_symbol and score_symbol in symbols:
                try:
                    technical = analysis_service.get_score(score_symbol)
                except Exception:
                    technical = None

            body = parity.render_watchlist_page(
                session,
                plan=plan,
                plan_label=label,
                symbols=symbols,
                items=items,
                data=self._safe_data(),
                score=technical,
                score_symbol=score_symbol,
            )
            body = watchsync.enhance_mobile_watchlist_notice(body, managed=managed)
            self._send(
                HTTPStatus.OK,
                body,
                "text/html; charset=utf-8",
                cookies=[cookie_to_set] if cookie_to_set else None,
            )

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "classic_runtime_repair": True,
                    "desktop_runtime": "V3.32.1 preserved",
                    "surface_audit": "desktop_mobile_unauth_free_premium_admin",
                    "journey_audit": "visitor_register_free_upgrade_premium_renew_admin",
                    "mobile_runtime": "server_rendered_no_javascript",
                    "mobile_main": "V3.32.3 preserved",
                    "mobile_market": "V3.32.4 preserved",
                    "mobile_chart": "svg_no_javascript",
                    "mobile_account": "server_rendered_no_javascript",
                    "mobile_navigation": "consistent_core",
                    "mobile_filters": "server_rendered",
                    "mobile_opportunities": "server_rendered_existing_analysis",
                    "mobile_free_premium_separated": True,
                    "account_password_change": "current_password_required_all_sessions_revoked",
                    "payment_feedback": "user_visible_fixed_codes",
                    "watchlist_sync": "managed_account_cross_device",
                    "watchlist_first_migration": "browser_favorites_preserved_once",
                    "watchlist_unmanaged_fallback": "device_local_preserved",
                    "membership_backend": "unchanged_except_user_preference_field",
                    "payment_backend": "unchanged",
                    "free_runtime": "separate_preserved",
                    "premium_dashboard_api": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return

            if path == "/api/account/watchlist":
                session, premium = self._watch_access()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "Oturum gerekli."})
                    return
                if not premium:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "Premium erişim gerekli."})
                    return
                snapshot = self._watch_snapshot(str(session.get("username") or ""))
                self._json(HTTPStatus.OK, snapshot)
                return

            if path == "/mobile/watchlist":
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=12)
                self._serve_synced_mobile_watchlist(query)
                return

            return super().do_GET()

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/api/account/watchlist":
                session, premium = self._watch_access()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "Oturum gerekli."})
                    return
                if not premium:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "Premium erişim gerekli."})
                    return
                form = self._form()
                if not commercial._csrf_ok(session, form.get("csrf", "")):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "Oturum doğrulaması geçersiz."})
                    return
                username = str(session.get("username") or "")
                snapshot = self._watch_snapshot(username)
                if not snapshot.get("managed"):
                    self._json(HTTPStatus.OK, {
                        "managed": False,
                        "initialized": False,
                        "symbols": [],
                        "updated_at": 0,
                    })
                    return
                symbols = watchsync.normalize_watchlist(form.get("symbols", ""))
                try:
                    symbols = watchsync.save_account_watchlist(store, username, symbols, actor=username)
                except accounts.AccountStoreError:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "İzleme listesi şu anda kaydedilemedi."})
                    return
                cookie = parity.watch_cookie(symbols, secure=config.cookie_secure)
                payload = json.dumps({
                    "managed": True,
                    "initialized": True,
                    "symbols": symbols,
                }, ensure_ascii=False, separators=(",", ":"))
                self._send(
                    HTTPStatus.OK,
                    payload,
                    "application/json; charset=utf-8",
                    cookies=[cookie],
                )
                return
            return super().do_POST()

    return V3328Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.8 hesaba bağlı İzleme Listesi")
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
    handler = make_v3321_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        candle_client,
        overview_client,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"{VERSION} http://{args.host}:{args.port} watchlist_sync=managed_account "
        "v3327_preserved=1 signal_engine=unchanged"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
