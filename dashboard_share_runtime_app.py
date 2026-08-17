"""Kripto Kontrol Merkezi V3.32.9 - mobil Paylaş görünürlük hotfix'i.

V3.32.9 paylaşım kartı özelliğini korur. Mobil sunucu sayfasını eski metin kopyasına
bağlamak yerine yapısal olarak tanır; böylece Sinyal/İşlem/Sonuç ekranlarında Paylaş
bağlantısı güncel JS'siz mobil çekirdeğe eklenir.
Trading, strategy/config, radar, Telegram, TP/SL/BE ve state/ledger yazımları değişmez.
"""
from __future__ import annotations

import argparse
import os
import secrets
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accountflow_runtime_app as base
import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_sharecard_app as cards
import dashboard_shareui_app as shareui
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_9_SHARE_CARDS_MOBILE_FIX_2026_08_17"
CSS = base.CSS
SCRIPT = base.SCRIPT


def _anchor(row: dict[str, Any]) -> int:
    for key in ("closed_at", "close_time", "ended_at", "opened_at", "open_time", "entry_time", "created_at", "created_ts", "timestamp"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            number = int(float(value))
            if number > 10_000_000_000:
                number //= 1000
            if 1_262_304_000 <= number <= 4_102_444_800:
                return number
        except (TypeError, ValueError, OverflowError):
            pass
    return 0


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
    BaseHandler = base.make_v3321_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        candle_client,
        overview,
        history_cache=history_cache,
    )

    class V3329MobileFixHandler(BaseHandler):
        server_version = "KriptoPanel/3.32.9-mobile-fix"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                session = self._session()
                if session and self._is_premium(session):
                    parsed = urllib.parse.urlsplit(self.path)
                    path = parsed.path
                    if 'id="page-home"' in body:
                        body = shareui.enhance_desktop(body, str(nonce or ""))
                    if path in {"/", "/mobile"} and shareui.is_mobile_server_page(body):
                        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=12)
                        view = str((query.get("view") or ["home"])[0] or "home")
                        body = shareui.enhance_mobile(body, self._safe_data(), view=view)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def _share_record(self, parsed):
            session = self._session()
            if not session:
                self._redirect("/login")
                return None
            if not self._is_premium(session):
                self._redirect("/premium")
                return None
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=16)
            found = cards.find_record(self._safe_data(), query)
            if not found:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    '<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="background:#061018;color:#eef6f4;font-family:system-ui;padding:24px"><h2>İşlem kaydı bulunamadı</h2><p>İşlem listesi yenilenmiş olabilir. Sinyaller veya Sonuçlar ekranından tekrar Paylaş seçin.</p><a style="color:#2ce6bf" href="/">Kontrol Merkezine dön</a></body></html>',
                    "text/html; charset=utf-8",
                )
                return None
            kind, row = found
            stage = str((query.get("stage") or ["result" if kind == "result" else "signal"])[0] or "signal")
            if stage not in {"signal", "tracking", "result"}:
                stage = "result" if kind == "result" else "signal"
            return kind, stage, row

        def _share_candles(self, row: dict[str, Any]):
            try:
                payload = candle_client.get_candles(cards.symbol(row), "15m", _anchor(row) or None)
                candles = payload.get("candles") if isinstance(payload, dict) else []
                source = str(payload.get("source") or "PUBLIC") if isinstance(payload, dict) else "PUBLIC"
                return [item for item in (candles or []) if isinstance(item, dict)], source
            except Exception:
                try:
                    payload = candle_client.get_candles(cards.symbol(row), "15m")
                    candles = payload.get("candles") if isinstance(payload, dict) else []
                    source = str(payload.get("source") or "PUBLIC") if isinstance(payload, dict) else "PUBLIC"
                    return [item for item in (candles or []) if isinstance(item, dict)], source
                except Exception:
                    return [], "PUBLIC_DATA_UNAVAILABLE"

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "base_runtime": "V3.32.8 preserved",
                    "share_cards": "premium_admin_real_trade_data",
                    "share_buttons": "signals_trades_results_desktop_mobile",
                    "mobile_share_injection": "structural_server_mobile_detection",
                    "share_chart": "public_15m_candles_server_svg",
                    "share_png": "browser_export_web_share_download_fallback",
                    "share_results": "tp_sl_be_supported",
                    "share_free": "blocked",
                    "share_user_identity": "not_rendered",
                    "mobile_runtime": "server_rendered_core_preserved",
                    "free_runtime": "separate_preserved",
                    "membership_backend": "V3.32.8 preserved",
                    "payment_backend": "unchanged",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            if path in {"/share/trade", "/share/card.svg"}:
                selected = self._share_record(parsed)
                if not selected:
                    return
                kind, stage, row = selected
                candles, source = self._share_candles(row)
                if path == "/share/card.svg":
                    self._send(
                        HTTPStatus.OK,
                        cards.render_svg(row, kind=kind, stage=stage, candles=candles, source=source),
                        "image/svg+xml; charset=utf-8",
                    )
                    return
                nonce = secrets.token_urlsafe(18)
                self._send(
                    HTTPStatus.OK,
                    cards.render_page(row, kind=kind, stage=stage, candles=candles, source=source, nonce=nonce),
                    "text/html; charset=utf-8",
                    nonce=nonce,
                )
                return
            return super().do_GET()

    return V3329MobileFixHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.9 mobil Paylaş görünürlük hotfix'i")
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
    handler = make_v3321_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} share_cards=on mobile_share=structural signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
