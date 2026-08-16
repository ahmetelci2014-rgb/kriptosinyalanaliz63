"""Kripto Kontrol Merkezi V3.32.7 - hesap akışı tamamlama katmanı.

V3.32.6 masaüstü/mobil/plan paritesini aynen korur. Yalnız kod denetiminde
kanıtlanan iki kullanıcı akışı açığını kapatır:
- giriş yapmış, panel_users deposunda yönetilen kullanıcı mevcut şifresini
  doğrulayarak kendi şifresini değiştirebilir; başarıda tüm oturumları kapanır,
- ödeme bildirimi başarısız/başarılı olduğunda kullanıcı sabit ve güvenli bir
  geri bildirim görür; ödeme/üyelik backend kuralları değiştirilmez.

Şifremi unuttum/kurtarma eklenmez; doğrulanmış e-posta/telefon kimliği yoktur.
Trading, strategy/config, radar, Telegram, TP/SL/BE ve state/ledger değişmez.
"""
from __future__ import annotations

import argparse
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

import dashboard_account_flow_app as accountflow
import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_runtimefix_app as runtimefix
from dashboard_live_app import (
    LoginRateLimiter,
    OKXMarketDataClient,
    PanelConfig,
    SESSION_COOKIE,
    build_service,
    cookie_value,
    env_bool,
)

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_7_ACCOUNT_FLOW_2026_08_16"
CSS = runtimefix.CSS
SCRIPT = runtimefix.SCRIPT


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
    """V3.32.6'yı sarmalar; trading dışı hesap kullanıcı akışını tamamlar."""
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview = overview_client or market.OKXMarketOverviewClient(cache_seconds=20)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = runtimefix.make_v3321_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        candle_client,
        overview,
        history_cache=cache,
    )
    crypto_enabled = env_bool("PANEL_CRYPTO_PAYMENT_ENABLED", False)

    class V3327Handler(BaseHandler):
        server_version = "KriptoPanel/3.32.7"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if isinstance(body, str) and content_type.startswith("text/html"):
                parsed = urllib.parse.urlsplit(self.path)
                path = parsed.path
                if path in {"/account", "/mobile/account"} and self._session():
                    body = accountflow.enhance_account_security_link(body)
                if path in {"/premium", "/mobile/premium"}:
                    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=8)
                    code = str((query.get("payment") or [""])[0] or "")
                    body = accountflow.enhance_payment_feedback(body, code)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def _security_page(self, *, error: str = "", status: int = HTTPStatus.OK) -> None:
            session = self._session()
            if not session:
                self._redirect("/login")
                return
            username = str(session.get("username") or "")
            managed = accountflow.managed_account(store, username)
            body = accountflow.security_page(session, managed=managed, error=error)
            self._send(status, body, "text/html; charset=utf-8")

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
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
                    "mobile_coin": "server_rendered_premium_svg",
                    "mobile_chart": "svg_no_javascript",
                    "mobile_account": "server_rendered_no_javascript",
                    "mobile_premium": "server_rendered_existing_billing_backend",
                    "mobile_renewal": "existing_7_3_1_day_rules",
                    "mobile_legacy_spa_bypassed": True,
                    "mobile_progressive_disclosure": True,
                    "mobile_primary_levels": "entry_tp1_sl",
                    "mobile_navigation": "consistent_core",
                    "mobile_filters": "server_rendered",
                    "mobile_watchlist": "server_rendered_cookie_preference",
                    "mobile_opportunities": "server_rendered_existing_analysis",
                    "mobile_sound": "desktop_only_by_design",
                    "mobile_free_premium_separated": True,
                    "account_password_change": "current_password_required_all_sessions_revoked",
                    "password_recovery": "not_enabled_without_verified_identity",
                    "payment_feedback": "user_visible_fixed_codes",
                    "membership_backend": "unchanged",
                    "payment_backend": "unchanged",
                    "free_runtime": "separate_preserved",
                    "premium_dashboard_api": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            if path in {"/account/security", "/mobile/account/security"}:
                self._security_page()
                return
            return super().do_GET()

        def do_POST(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/account/password":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                form = self._form()
                if not commercial._csrf_ok(session, form.get("csrf", "")):
                    self._security_page(
                        error="Oturum doğrulaması geçersiz veya süresi dolmuş. Sayfayı yenileyip tekrar deneyin.",
                        status=HTTPStatus.FORBIDDEN,
                    )
                    return
                username = str(session.get("username") or "")
                if not accountflow.managed_account(store, username):
                    self._security_page(
                        error="Bu hesabın şifresi sunucu ortam ayarlarından yönetiliyor; panel içinden değiştirilemez.",
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                try:
                    accountflow.change_managed_password(
                        store,
                        username,
                        form.get("current_password", ""),
                        form.get("new_password", ""),
                        form.get("new_password_confirm", ""),
                    )
                except ValueError as exc:
                    self._security_page(error=str(exc), status=HTTPStatus.BAD_REQUEST)
                    return
                except accounts.AccountStoreError:
                    self._security_page(
                        error="Şifre şu anda kaydedilemedi. Daha sonra tekrar deneyin.",
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                sessions.delete_username(username)
                self._send(
                    HTTPStatus.OK,
                    accountflow.password_changed_page(),
                    "text/html; charset=utf-8",
                    cookies=[
                        cookie_value(
                            SESSION_COOKIE,
                            "",
                            max_age=0,
                            secure=config.cookie_secure,
                        )
                    ],
                )
                return

            if path == "/payment/notify":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                form = self._form()
                if not commercial._csrf_ok(session, form.get("csrf", "")):
                    self._redirect("/premium?payment=session")
                    return
                method = str(form.get("method") or "BANK_TRANSFER").upper()
                if method == "CRYPTO" and not crypto_enabled:
                    self._redirect("/premium?payment=crypto_disabled")
                    return
                try:
                    store.submit_payment(
                        str(session.get("username") or ""),
                        method=method,
                        package=form.get("package", "PREMIUM_30D"),
                        note=form.get("note", ""),
                    )
                except ValueError as exc:
                    code = "already_pending" if "onay bekleyen" in str(exc).casefold() else "invalid"
                    self._redirect(f"/premium?payment={code}")
                    return
                except accounts.AccountStoreError:
                    self._redirect("/premium?payment=store_unavailable")
                    return
                self._redirect("/premium?payment=sent")
                return
            return super().do_POST()

    return V3327Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.7 hesap akışı tamamlama")
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
        f"{VERSION} http://{args.host}:{args.port} account_password=on "
        "payment_feedback=on v3326_preserved=1 signal_engine=unchanged"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
