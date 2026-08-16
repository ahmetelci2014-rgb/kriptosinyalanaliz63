"""Kripto Kontrol Merkezi V3.21 - Admin Analiz & Geliştirme Merkezi.

Mevcut Yönetim Merkezi korunur. Yeni teknik analiz ekranlarını normal üye akışına
yaymak yerine yalnız ADMIN için tek bir yönetim bölümünde toplar.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger ve otomatik
filtre mantığı değiştirilmez.
"""
from __future__ import annotations

import argparse
import html
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
import dashboard_learningfix_app as learningfix
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_21_ADMIN_ANALYSIS_HUB_2026_08_16"


ADMIN_TOOLS = (
    ("Öğrenme Merkezi", "/learning-center", "TP–STOP, LONG–SHORT ve giriş davranışlarından geliştirme başlıkları çıkarır.", "◎"),
    ("Sistem Kalite Profili", "/system-quality", "Sistemlerin kapanış ve ilk 15 dakika davranışını tek gözlemsel profilde toplar.", "◆"),
    ("İlk 15 dk Analizi", "/early-performance", "Geçmiş işlemlerin açılış sonrası ilk 15 dakikalık MFE/MAE ve TP1–SL davranışını ölçer.", "15"),
    ("Performans Zekâsı", "/performance-intelligence", "7/14/30 günlük eğilim, TP devamı ve STOP teşhislerini inceler.", "↗"),
    ("İyileştirme Karar Merkezi", "/admin/improvement-center", "Gölge test ve Decision Engine kanıtlarını kontrollü geliştirme kuyruğunda toplar.", "⚙"),
)


def admin_analysis_hub() -> str:
    cards = "".join(
        '<a class="v321-tool" href="{href}"><span class="v321-icon">{icon}</span><div><b>{title}</b><small>{desc}</small></div><em>→</em></a>'.format(
            href=html.escape(href, quote=True),
            icon=html.escape(icon),
            title=html.escape(title),
            desc=html.escape(desc),
        )
        for title, href, desc, icon in ADMIN_TOOLS
    )
    return (
        '<section class="v321-admin-hub" id="v321AdminAnalysisHub">'
        '<div class="v321-head"><div><span>ADMIN · GELİŞTİRME</span><h2>Analiz & Geliştirme</h2>'
        '<p>Üyeye gösterilmeyen iç analiz, performans ve geliştirme araçları.</p></div>'
        '<div class="v321-lock">CANLI KURAL YAZMAZ</div></div>'
        f'<div class="v321-tools">{cards}</div>'
        '</section>'
    )


def enhance_admin_center(body: str) -> str:
    if 'id="v321AdminAnalysisHub"' in body:
        return body
    css = r'''
.v321-admin-hub{margin:0 0 16px;border:1px solid rgba(96,165,250,.28);border-radius:16px;background:linear-gradient(135deg,rgba(12,30,41,.98),rgba(8,22,29,.98));padding:14px}.v321-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;margin-bottom:10px}.v321-head span{display:block;color:#60a5fa;font-size:8px;font-weight:950;letter-spacing:.08em}.v321-head h2{margin:2px 0 2px;font-size:17px}.v321-head p{margin:0;color:#82a09d;font-size:9px}.v321-lock{border:1px solid rgba(44,230,191,.28);border-radius:999px;padding:5px 8px;color:#2ce6bf;font-size:7px;font-weight:950;white-space:nowrap}.v321-tools{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.v321-tool{display:grid;grid-template-columns:34px 1fr auto;gap:9px;align-items:center;border:1px solid #1b3943;border-radius:12px;padding:10px;background:#08171e;text-decoration:none;color:#edf8f6}.v321-tool:hover{border-color:rgba(44,230,191,.4)}.v321-icon{width:34px;height:34px;border-radius:10px;display:grid;place-items:center;background:rgba(96,165,250,.08);color:#60a5fa;font-weight:950}.v321-tool b{display:block;font-size:11px}.v321-tool small{display:block;color:#789491;font-size:8px;margin-top:2px}.v321-tool em{font-style:normal;color:#58736f;font-size:15px}@media(max-width:760px){.v321-tools{grid-template-columns:1fr}.v321-head{flex-direction:column}.v321-lock{align-self:flex-start}}
'''
    if "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)
    hub = admin_analysis_hub()
    marker = '<div class="quick">'
    if marker in body:
        pos = body.find(marker)
        end = body.find('</div>', pos)
        if end >= 0:
            # Insert before quick links so internal admin tools are clearly separated.
            return body[:pos] + hub + body[pos:]
    marker = '<div class="grid">'
    if marker in body:
        return body.replace(marker, hub + marker, 1)
    return body.replace("</body>", hub + "</body>", 1)


def make_v321_handler(
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
    BaseHandler = learningfix.make_v320r1_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V321Handler(BaseHandler):
        server_version = "KriptoPanel/3.21"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/admin/center":
                    session = self._session()
                    if self._is_admin_session(session):
                        body = enhance_admin_center(body)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "admin_analysis_hub": True,
                    "admin_only": True,
                    "learning_center": True,
                    "automatic_filter": False,
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V321Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.21 Admin Analiz & Geliştirme Merkezi")
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
    handler = make_v321_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} admin_analysis_hub=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
