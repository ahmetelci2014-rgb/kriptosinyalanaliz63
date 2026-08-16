"""Kripto Kontrol Merkezi V3.22 - Veri Kaynağı Güven Etiketleri.

V3.21 admin analiz merkezi korunur. Paneldeki önemli ekranlara verinin niteliğini
belirten görünür kaynak etiketleri ekler:
- SİSTEM KAYDI: çalışan canlı sinyal sisteminin state/ledger kayıtları,
- PUBLIC PİYASA: OKX/Binance salt-okunur piyasa verisi,
- HESAPLANAN ANALİZ: gerçek kayıtlardan türetilen panel metriği,
- GÖLGE / SİMÜLASYON: canlı performans olmayan karşılaştırma modeli.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger yazma ve
automatik filtre davranışı değiştirilmez.
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
import dashboard_adminhub_app as adminhub
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_22_DATA_PROVENANCE_2026_08_16"

TYPE_META = {
    "SYSTEM": ("SİSTEM KAYDI", "Çalışan sinyal sisteminin gerçek state / ledger kaydı.", "system"),
    "MARKET": ("PUBLIC PİYASA", "OKX public piyasa verisi; gerektiğinde Binance public yedek kaynak.", "market"),
    "DERIVED": ("HESAPLANAN ANALİZ", "Gerçek sistem/piyasa kayıtlarından panel tarafından türetilen ölçüm.", "derived"),
    "SHADOW": ("GÖLGE / SİMÜLASYON", "Canlı sonuç değildir; alternatif kuralı karşılaştırmak için modellenen veri.", "shadow"),
}

PATH_TYPES = {
    "/": ("SYSTEM", "MARKET", "DERIVED"),
    "/coin-center": ("SYSTEM", "MARKET", "DERIVED"),
    "/early-performance": ("SYSTEM", "MARKET", "DERIVED"),
    "/system-quality": ("SYSTEM", "MARKET", "DERIVED"),
    "/learning-center": ("SYSTEM", "MARKET", "DERIVED"),
    "/performance-intelligence": ("SYSTEM", "DERIVED"),
    "/admin/improvement-center": ("SYSTEM", "DERIVED", "SHADOW"),
    "/admin/improvements": ("SYSTEM", "DERIVED", "SHADOW"),
    "/admin/center": ("SYSTEM", "DERIVED"),
}

SOURCE_FACTS = (
    ("Premium açık/kapanış", "open_signals.json · trade_ledger.json", "SYSTEM"),
    ("Scalp açık/kapanış", "scalp_radar_state.json · scalp_performance_ledger.json", "SYSTEM"),
    ("Pump/Dump açık/kapanış", "pump_radar_state.json · pump_performance_ledger.json", "SYSTEM"),
    ("Yeni Liste", "new_listing_performance_ledger.json", "SYSTEM"),
    ("Sistem sağlığı", "system_control_center_report.json", "SYSTEM"),
    ("Grafik / ilk 15 dakika", "OKX public mumları · Binance public fallback", "MARKET"),
    ("Kalite / öğrenme / performans puanları", "Yukarıdaki gerçek kayıtlardan hesaplanır", "DERIVED"),
    ("İyileştirme gölge modelleri", "post-result shadow / Decision Engine karşılaştırmaları", "SHADOW"),
)

CSS = r'''
.v322-sourcebar{margin:10px 0 13px;border:1px solid rgba(61,91,101,.72);border-radius:12px;background:rgba(7,20,27,.94);padding:9px 10px;display:flex;align-items:center;gap:7px;flex-wrap:wrap}.v322-sourcebar>strong{font-size:8px;color:#77928e;margin-right:2px;text-transform:uppercase;letter-spacing:.06em}.v322-badge{display:inline-flex;align-items:center;gap:5px;border:1px solid currentColor;border-radius:999px;padding:4px 7px;font-size:7px;font-weight:950;line-height:1;white-space:nowrap}.v322-badge:before{content:'';width:5px;height:5px;border-radius:50%;background:currentColor}.v322-system{color:#42e28c}.v322-market{color:#69a9ff}.v322-derived{color:#2ce6bf}.v322-shadow{color:#ffbd59}.v322-help{color:#6f8d89;font-size:8px;margin-left:auto}.v322-card-source{display:inline-flex;margin-top:5px;border:1px solid currentColor;border-radius:999px;padding:3px 6px;font-size:6px;font-weight:950}.v322-sourcefacts{margin:10px 0 14px;border:1px solid rgba(105,169,255,.24);border-radius:13px;background:#081820;padding:11px}.v322-sourcefacts h3{margin:0 0 3px;font-size:12px}.v322-sourcefacts p{margin:0 0 8px;color:#789491;font-size:8px}.v322-sourcefacts-grid{display:grid;grid-template-columns:1fr 1fr;gap:6px}.v322-fact{border:1px solid #1b3943;border-radius:9px;background:#07151c;padding:7px}.v322-fact b{display:block;font-size:8px}.v322-fact small{display:block;color:#698580;font-size:7px;margin-top:2px}@media(max-width:700px){.v322-help{width:100%;margin-left:0}.v322-sourcefacts-grid{grid-template-columns:1fr}}
'''


def _badge(kind: str) -> str:
    label, description, css = TYPE_META[kind]
    return (
        f'<span class="v322-badge v322-{css}" title="{html.escape(description, quote=True)}">'
        f'{html.escape(label)}</span>'
    )


def provenance_bar(path: str) -> str:
    kinds = PATH_TYPES.get(path, ())
    if not kinds:
        return ""
    badges = "".join(_badge(kind) for kind in kinds)
    help_text = (
        "Gerçek hesap P/L değildir; otomatik emir bağlantısı yoktur."
        if "SYSTEM" in kinds else "Veri türü"
    )
    return (
        '<div class="v322-sourcebar" id="v322SourceBar"><strong>Veri niteliği</strong>'
        f'{badges}<span class="v322-help">{html.escape(help_text)}</span></div>'
    )


def source_facts_block() -> str:
    cards = []
    for title, source, kind in SOURCE_FACTS:
        label, _, css = TYPE_META[kind]
        cards.append(
            '<div class="v322-fact">'
            f'<b>{html.escape(title)}</b><small>{html.escape(source)}</small>'
            f'<span class="v322-card-source v322-{css}">{html.escape(label)}</span>'
            '</div>'
        )
    return (
        '<section class="v322-sourcefacts" id="v322SourceFacts"><h3>Veri Kaynakları</h3>'
        '<p>Panelin gösterdiği sayıların hangi veri sınıfından geldiğini özetler.</p>'
        f'<div class="v322-sourcefacts-grid">{"".join(cards)}</div></section>'
    )


def _inject_css(body: str) -> str:
    if ".v322-sourcebar" in body:
        return body
    if "</style>" in body:
        return body.replace("</style>", CSS + "\n</style>", 1)
    return body


def _insert_after_top(body: str, block: str) -> str:
    # Standalone V3.18+ pages use top + hero. Place the source legend before the hero.
    hero = '<section class="hero">'
    if hero in body:
        return body.replace(hero, block + hero, 1)
    # Main/admin pages have different markup; put it immediately after body as a safe fallback.
    if "<body>" in body:
        return body.replace("<body>", "<body>" + block, 1)
    return body


def _annotate_admin_tools(body: str) -> str:
    if 'id="v321AdminAnalysisHub"' not in body:
        return body
    mapping = {
        "/learning-center": ("SYSTEM", "MARKET", "DERIVED"),
        "/system-quality": ("SYSTEM", "MARKET", "DERIVED"),
        "/early-performance": ("SYSTEM", "MARKET", "DERIVED"),
        "/performance-intelligence": ("SYSTEM", "DERIVED"),
        "/admin/improvement-center": ("SYSTEM", "DERIVED", "SHADOW"),
    }
    for href, kinds in mapping.items():
        marker = f'<a class="v321-tool" href="{href}">'
        pos = body.find(marker)
        if pos < 0:
            continue
        end = body.find('</a>', pos)
        if end < 0:
            continue
        segment = body[pos:end]
        if "v322-card-source" in segment:
            continue
        badges = "".join(
            f'<span class="v322-card-source v322-{TYPE_META[kind][2]}">{html.escape(TYPE_META[kind][0])}</span>'
            for kind in kinds
        )
        # Add tags inside the descriptive div, just before its closing div.
        div_end = segment.rfind('</div>')
        if div_end >= 0:
            segment = segment[:div_end] + badges + segment[div_end:]
            body = body[:pos] + segment + body[end:]
    return body


def enhance_page(body: str, path: str) -> str:
    if 'id="v322SourceBar"' in body:
        return body
    body = _inject_css(body)
    body = _annotate_admin_tools(body)
    bar = provenance_bar(path)
    if bar:
        body = _insert_after_top(body, bar)
    if path == "/admin/center" and 'id="v322SourceFacts"' not in body:
        facts = source_facts_block()
        marker = '<section class="v321-admin-hub"'
        if marker in body:
            body = body.replace(marker, facts + marker, 1)
        else:
            body = body.replace("</body>", facts + "</body>", 1)
    return body


def make_v322_handler(
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
    BaseHandler = adminhub.make_v321_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V322Handler(BaseHandler):
        server_version = "KriptoPanel/3.22"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path in PATH_TYPES:
                    session = self._session()
                    if session:
                        body = enhance_page(body, path)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "data_provenance": True,
                    "system_record_label": True,
                    "public_market_label": True,
                    "derived_analysis_label": True,
                    "shadow_simulation_label": True,
                    "real_account_pnl": False,
                    "automatic_filter": False,
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V322Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.22 Veri Kaynağı Güven Etiketleri")
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
    handler = make_v322_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} provenance=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
