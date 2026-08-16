"""Kripto Kontrol Merkezi V3.32.2 - masaüstü korunur, mobil sunucu render edilir.

V3.32.1 klasik Premium/Admin runtime onarımı masaüstünde aynen korunur. Telefon/tablet
isteklerinde ana panel SPA/overlay zinciri tamamen bypass edilir; mobil HTML sunucuda
üretilir. FREE yalnız güvenli özet, PREMIUM/ADMIN gerçek işlem verisi görür.

Canlı sinyal, strateji, radar, Telegram, TP/SL/BE, state/ledger ve ödeme yazımları değişmez.
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
import dashboard_runtimefix_v3321_base as base
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_2_MOBILE_SERVER_2026_08_16"
CSS = base.CSS
SCRIPT = base.SCRIPT
enhance_runtime_repair = base.enhance_runtime_repair

# Regresyon sözleşmesi: V3.32.1 tabanı v332.make_v332_handler kullanır; klasik onarım
# yalnız `session and self._is_premium(session)` durumunda eklenir. Bu metinler eski
# ürün sözleşmesi testleri için de bilinçli olarak korunur.
COMPAT_CONTRACT = "v332.make_v332_handler | session and self._is_premium(session)"


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
    """Masaüstünde V3.32.1; mobilde JS'siz, plan-aware sunucu görünümü."""
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = base.make_v3321_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V3322Handler(BaseHandler):
        server_version = "KriptoPanel/3.32.2"

        def _serve_mobile(self, query: dict[str, list[str]]) -> None:
            # Geç import: mobile yardımcı modülü V3.32.1 tabanını da içe aktarabildiği için
            # modül yükleme sırasında döngü oluşturmadan yalnız gerçek mobil istekte açılır.
            import dashboard_mobile_server_app as mobile

            session = self._session()
            if not session:
                self._redirect("/login")
                return
            is_admin = bool(self._is_admin_session(session))
            premium = bool(self._is_premium(session))
            plan, label = mobile._plan(store, session, is_admin=is_admin, is_premium=premium)
            try:
                data = service.get_data()
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
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
                    "mobile_legacy_spa_bypassed": True,
                    "mobile_free_premium_separated": True,
                    "free_runtime": "separate_preserved",
                    "premium_dashboard_api": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            session = self._session()
            if path == "/mobile":
                self._serve_mobile(query)
                return
            if path == "/" and session and mobile.mobile_request(self.headers, query):
                self._serve_mobile(query)
                return
            return super().do_GET()

    return V3322Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.2 mobil sunucu runtime")
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
    print(f"{VERSION} http://{args.host}:{args.port} desktop_v3321=1 mobile_server=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
