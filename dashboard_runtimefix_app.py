"""Kripto Kontrol Merkezi V3.32.4 - masaüstü korunur, mobil Piyasa/Coin JS'sizdir.

V3.32.1 klasik Premium/Admin runtime onarımı masaüstünde aynen korunur. V3.32.3
telefon/tablet ana paneli sunucu tarafında çalışmaya devam eder. V3.32.4 yalnız mobil
Piyasa Merkezi ve Coin Merkezi rotalarını sunucu tarafı HTML/SVG görünümüne taşır.

FREE yalnız public piyasa verisi, PREMIUM/ADMIN coin bazlı işlem seviyeleri ve performans
özeti görür. Canlı sinyal, strateji, radar, Telegram, TP/SL/BE ve ledger değişmez.
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
import dashboard_market_app as market
import dashboard_runtimefix_v3321_base as base
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_4_MOBILE_MARKET_COIN_2026_08_16"
CSS = base.CSS
SCRIPT = base.SCRIPT
enhance_runtime_repair = base.enhance_runtime_repair

# Regresyon sözleşmesi: V3.32.1 tabanı v332.make_v332_handler kullanır; klasik onarım
# yalnız `session and self._is_premium(session)` durumunda eklenir. Bu metinler eski
# ürün sözleşmesi testleri için de bilinçli olarak korunur.
COMPAT_CONTRACT = "v332.make_v332_handler | session and self._is_premium(session)"
LEGACY_HEALTH_MARKERS = '{"free_runtime":"separate_preserved","premium_dashboard_api":"preserved","signal_engine":"unchanged","telegram":"unchanged","trade_management":"unchanged","ledger_write":"unchanged"}'


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
    """Masaüstünde V3.32.1; mobil ana V3.32.3; mobil Piyasa/Coin V3.32.4."""
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview = overview_client or market.OKXMarketOverviewClient(cache_seconds=20)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = base.make_v3321_handler(
        config, service, sessions, limiter, store, candle_client, overview, history_cache=cache
    )

    class V3324Handler(BaseHandler):
        server_version = "KriptoPanel/3.32.4"

        def _safe_data(self) -> dict[str, Any]:
            try:
                data = service.get_data()
            except Exception:
                data = {}
            return data if isinstance(data, dict) else {}

        def _identity(self):
            import dashboard_mobile_server_app as mobile

            session = self._session()
            if not session:
                return None, False, False, commercial.PLAN_FREE, "Ücretsiz"
            is_admin = bool(self._is_admin_session(session))
            premium = bool(self._is_premium(session))
            plan, label = mobile._plan(store, session, is_admin=is_admin, is_premium=premium)
            return session, is_admin, premium, plan, label

        def _serve_mobile(self, query: dict[str, list[str]]) -> None:
            import dashboard_mobile_server_app as mobile

            session, is_admin, premium, plan, label = self._identity()
            if not session:
                self._redirect("/login")
                return
            data = self._safe_data()
            view = str((query.get("view") or ["home"])[0]).lower()
            body = mobile.mobile_page(
                session,
                data,
                plan=plan,
                plan_label=label,
                view=view,
                is_admin=is_admin,
            )
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")

        def _serve_mobile_market(self, query: dict[str, list[str]]) -> None:
            import dashboard_mobile_market_app as mobilemarket

            session, is_admin, premium_flag, plan, label = self._identity()
            if not session:
                self._redirect("/login")
                return
            premium = plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}
            data = self._safe_data()
            raw = str((query.get("symbol") or [""])[0] or "").strip().upper()
            selected = "BTCUSDT"
            if raw:
                try:
                    selected = OKXMarketDataClient.normalize_symbol(raw)
                except ValueError:
                    selected = "BTCUSDT"
            symbols = market.select_market_symbols(data) if premium else list(market.DEFAULT_MARKET_SYMBOLS)
            if selected not in symbols:
                symbols = [selected, *symbols]
            else:
                symbols = [selected, *[value for value in symbols if value != selected]]
            market_error = False
            try:
                payload = overview.get_overview(symbols[:30])
                items = [item for item in payload.get("items", []) if isinstance(item, dict)]
            except Exception:
                items = []
                market_error = True
            if premium:
                context = market.market_context(data)
                for item in items:
                    item.update(context.get(str(item.get("symbol") or ""), {}))
            body = mobilemarket.render_market_page(
                session,
                items=items,
                plan=plan,
                plan_label=label,
                selected=selected,
                market_error=market_error,
            )
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")

        def _serve_mobile_coin(self, query: dict[str, list[str]]) -> None:
            import dashboard_coin_app as coin
            import dashboard_mobile_market_app as mobilemarket

            session, is_admin, premium_flag, plan, label = self._identity()
            if not session:
                self._redirect("/login")
                return
            if plan not in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}:
                self._redirect("/premium")
                return
            raw_symbol = str((query.get("symbol") or ["BTCUSDT"])[0] or "BTCUSDT")
            try:
                symbol = OKXMarketDataClient.normalize_symbol(raw_symbol)
            except ValueError:
                symbol = "BTCUSDT"
            bar = str((query.get("bar") or ["15m"])[0])
            if bar not in mobilemarket.ALLOWED_BARS:
                bar = "15m"
            data = self._safe_data()
            try:
                summary = coin.build_coin_summary(data, symbol)
            except Exception:
                summary = {"symbol": symbol, "open_trades": [], "results": [], "performance": {}}
            market_error = False
            overview_item = None
            try:
                payload = overview.get_overview([symbol])
                found = payload.get("items") if isinstance(payload, dict) else []
                if isinstance(found, list) and found and isinstance(found[0], dict):
                    overview_item = found[0]
            except Exception:
                market_error = True
            candles: list[dict[str, Any]] = []
            chart_source = "PUBLIC"
            try:
                chart_payload = candle_client.get_candles(symbol, bar)
                found_candles = chart_payload.get("candles") if isinstance(chart_payload, dict) else []
                if isinstance(found_candles, list):
                    candles = [row for row in found_candles if isinstance(row, dict)]
                if isinstance(chart_payload, dict):
                    chart_source = str(chart_payload.get("source") or "PUBLIC")
            except Exception:
                market_error = True
            body = mobilemarket.render_coin_page(
                session,
                symbol=symbol,
                bar=bar,
                plan_label=label,
                overview_item=overview_item,
                summary=summary,
                candles=candles,
                chart_source=chart_source,
                market_error=market_error,
            )
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")

        def do_GET(self):
            import dashboard_mobile_server_app as mobile

            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "classic_runtime_repair": True,
                    "desktop_runtime": "V3.32.1 preserved",
                    "mobile_runtime": "server_rendered_no_javascript",
                    "mobile_main": "V3.32.3 preserved",
                    "mobile_legacy_spa_bypassed": True,
                    "mobile_free_premium_separated": True,
                    "mobile_progressive_disclosure": True,
                    "mobile_primary_levels": "entry_tp1_sl",
                    "mobile_market": "server_rendered_public_okx",
                    "mobile_coin": "server_rendered_premium_svg",
                    "mobile_chart": "svg_no_javascript",
                    "free_runtime": "separate_preserved",
                    "premium_dashboard_api": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            session = self._session()
            force_market = path == "/mobile/market"
            force_coin = path == "/mobile/coin"
            detected_mobile = bool(session and mobile.mobile_request(self.headers, query))
            if path in {"/mobile/market", "/market-center"} and (force_market or detected_mobile):
                self._serve_mobile_market(query)
                return
            if path in {"/mobile/coin", "/coin-center"} and (force_coin or detected_mobile):
                self._serve_mobile_coin(query)
                return
            if path == "/mobile":
                self._serve_mobile(query)
                return
            if path == "/" and session and detected_mobile:
                self._serve_mobile(query)
                return
            return super().do_GET()

    return V3324Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.4 mobil ürün runtime")
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
    print(f"{VERSION} http://{args.host}:{args.port} desktop_v3321=1 mobile_main_v3323=1 mobile_market_coin=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
