"""Kripto Kontrol Merkezi V3.37 - sunucu taraflı mobil güvenli görünüm.

Mobilde giriş sonrası ana SPA katmanında yaşanan dokunma kilidini tamamen bypass eder.
Telefon isteği /mobile-safe rotasına yönlendirilir ve Ana/Sinyaller/İşlemler/Sonuçlar
sunucu tarafında, JavaScript kullanmadan render edilir. Masaüstü V3.36 aynen korunur.

Canlı sinyal, strateji, radar, Telegram, TP/SL/BE, state/ledger ve üyelik backend'i değişmez.
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
import dashboard_market_app as market
import dashboard_mobile_recovery_app as recovery
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_37_MOBILE_SAFE_2026_08_16"


def _mobile_request(headers, query: dict[str, list[str]] | None = None) -> bool:
    query = query or {}
    if str((query.get("classic") or [""])[0]).lower() in {"1", "true", "yes"}:
        return False
    if str((query.get("mobile") or [""])[0]).lower() in {"1", "true", "yes"}:
        return True
    ch_mobile = str(headers.get("Sec-CH-UA-Mobile") or "").strip()
    if ch_mobile == "?1":
        return True
    ua = str(headers.get("User-Agent") or "").lower()
    return any(token in ua for token in ("android", "iphone", "ipad", "ipod", "mobile"))


def _esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _fmt(value: Any) -> str:
    number = _float(value)
    if number is None:
        return "—"
    absolute = abs(number)
    if absolute >= 1000:
        return f"{number:,.2f}".replace(",", ".")
    if absolute >= 1:
        return f"{number:.5f}".rstrip("0").rstrip(".")
    return f"{number:.9f}".rstrip("0").rstrip(".")


def _direction(row: dict[str, Any]) -> str:
    return str(row.get("direction") or "").upper()


def _system(row: dict[str, Any]) -> str:
    return str(row.get("system_label") or row.get("system") or row.get("source") or "Sistem")


def _outcome(row: dict[str, Any]) -> str:
    return str(row.get("outcome") or row.get("result") or "KAPALI").upper()


def _symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("display_symbol") or "—").replace("/USDT:USDT", "USDT").replace("/", "")


def _score(row: dict[str, Any]) -> str:
    for key in ("score", "signal_score", "confidence", "quality_score", "strength"):
        value = _float(row.get(key))
        if value is not None:
            return f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return "—"


def _tag(text: str, css_class: str = "") -> str:
    return f'<span class="tag {html.escape(css_class, quote=True)}">{_esc(text)}</span>'


def _open_card(row: dict[str, Any], *, trade_view: bool = False) -> str:
    direction = _direction(row)
    direction_class = "long" if direction == "LONG" else "short" if direction == "SHORT" else ""
    symbol = _symbol(row)
    system = _system(row)
    if trade_view:
        progress = str(row.get("progress") or ("TP3" if row.get("tp3_hit") else "TP2" if row.get("tp2_hit") else "TP1" if row.get("tp1_hit") else "AÇIK"))
        detail = (
            f'<div class="levels"><div><small>Giriş</small><b>{_esc(_fmt(row.get("entry")))}</b></div>'
            f'<div><small>TP1</small><b>{_esc(_fmt(row.get("tp1")))}</b></div>'
            f'<div><small>SL</small><b>{_esc(_fmt(row.get("sl")))}</b></div></div>'
            f'<div class="foot"><span>{_esc(system)}</span><strong>{_esc(progress)}</strong></div>'
        )
    else:
        detail = (
            f'<div class="levels"><div><small>Giriş</small><b>{_esc(_fmt(row.get("entry")))}</b></div>'
            f'<div><small>Skor</small><b>{_esc(_score(row))}</b></div>'
            f'<div><small>TP1</small><b>{_esc(_fmt(row.get("tp1")))}</b></div></div>'
            f'<div class="foot"><span>{_esc(system)}</span><a href="/coin-center?symbol={urllib.parse.quote(symbol)}">İncele ›</a></div>'
        )
    return f'<article class="card"><div class="cardtop"><strong>{_esc(symbol)}</strong>{_tag(direction or "AÇIK", direction_class)}</div>{detail}</article>'


def _result_card(row: dict[str, Any]) -> str:
    outcome = _outcome(row)
    cls = "tp" if outcome.startswith("TP") and "BE" not in outcome else "sl" if outcome == "SL" or outcome.startswith("SL_") else "be" if "BE" in outcome else ""
    r_value = row.get("r_result")
    r_text = f"{_float(r_value):+.2f}R" if _float(r_value) is not None else ""
    return (
        '<article class="card">'
        f'<div class="cardtop"><strong>{_esc(_symbol(row))}</strong>{_tag(outcome, cls)}</div>'
        f'<div class="foot"><span>{_esc(_system(row))} · {_esc(_direction(row) or "İşlem")}</span><strong>{_esc(r_text)}</strong></div>'
        '</article>'
    )


def mobile_safe_page(session: dict[str, Any], data: dict[str, Any], view: str = "home") -> str:
    open_rows = data.get("open_trades") if isinstance(data.get("open_trades"), list) else []
    results = data.get("recent_results") if isinstance(data.get("recent_results"), list) else []
    view = view if view in {"home", "signals", "trades", "results"} else "home"
    decided = [row for row in results if isinstance(row, dict)]
    tp_count = sum(1 for row in decided if _outcome(row).startswith("TP") and "BE" not in _outcome(row))
    sl_count = sum(1 for row in decided if _outcome(row) == "SL" or _outcome(row).startswith("SL_"))
    username = _esc(session.get("username") or "üye")
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)

    if view == "signals":
        title, subtitle = "Sinyaller", "Açık sinyaller · karar bilgisi önce"
        content = "".join(_open_card(row) for row in open_rows[:30] if isinstance(row, dict)) or '<div class="empty">Şu anda açık sinyal yok.</div>'
    elif view == "trades":
        title, subtitle = "İşlemler", "Takipteki işlemler · Giriş / TP1 / SL"
        content = "".join(_open_card(row, trade_view=True) for row in open_rows[:30] if isinstance(row, dict)) or '<div class="empty">Şu anda takipte işlem yok.</div>'
    elif view == "results":
        title, subtitle = "Sonuçlar", "Son kapanan TP / SL / BE kayıtları"
        content = "".join(_result_card(row) for row in results[:40] if isinstance(row, dict)) or '<div class="empty">Henüz sonuç kaydı yok.</div>'
    else:
        title, subtitle = "Kontrol Merkezi", "Hızlı, sade ve dokunmatik güvenli görünüm"
        strong = "".join(_open_card(row) for row in open_rows[:4] if isinstance(row, dict)) or '<div class="empty">Şu anda açık sinyal yok.</div>'
        recent = "".join(_result_card(row) for row in results[:4] if isinstance(row, dict)) or '<div class="empty">Henüz sonuç kaydı yok.</div>'
        content = f'''
        <div class="metrics">
          <div><small>Açık işlem</small><strong>{len(open_rows)}</strong></div>
          <div><small>Son TP</small><strong class="green">{tp_count}</strong></div>
          <div><small>Son SL</small><strong class="red">{sl_count}</strong></div>
        </div>
        <div class="quick"><a href="/market-center">⌁ Piyasa</a><a href="/coin-center?symbol=BTCUSDT">◈ Coin Merkezi</a></div>
        <section><div class="sectionhead"><h2>Öne çıkan açıklar</h2><a href="/mobile-safe?view=signals">Tümü ›</a></div>{strong}</section>
        <section><div class="sectionhead"><h2>Son sonuçlar</h2><a href="/mobile-safe?view=results">Tümü ›</a></div>{recent}</section>
        '''

    nav = (
        '<nav class="bottom">'
        f'<a class="{"active" if view == "home" else ""}" href="/mobile-safe"><span>⌂</span>Ana</a>'
        f'<a class="{"active" if view == "signals" else ""}" href="/mobile-safe?view=signals"><span>⚡</span>Sinyal</a>'
        f'<a class="{"active" if view == "trades" else ""}" href="/mobile-safe?view=trades"><span>↕</span>İşlem</a>'
        f'<a class="{"active" if view == "results" else ""}" href="/mobile-safe?view=results"><span>✓</span>Sonuç</a>'
        '<a href="/account"><span>○</span>Hesap</a>'
        '</nav>'
    )

    return f'''<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Kripto Kontrol · Mobil</title>
<style>
:root{{--bg:#071018;--panel:#0c1720;--line:#1d303b;--text:#edf7f5;--muted:#7f9b98;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59}}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}a{{color:inherit;text-decoration:none;-webkit-tap-highlight-color:transparent;touch-action:manipulation}}button{{touch-action:manipulation}}body{{padding:0 12px calc(88px + env(safe-area-inset-bottom));overflow-x:hidden}}.wrap{{max-width:720px;margin:auto}}header{{position:sticky;top:0;z-index:5;background:rgba(7,16,24,.97);border-bottom:1px solid var(--line);margin:0 -12px;padding:12px 14px;display:flex;align-items:center;gap:10px}}.logo{{width:36px;height:36px;border:1px solid rgba(44,230,191,.35);border-radius:11px;display:grid;place-items:center;color:var(--teal);font-weight:950}}.headmain{{min-width:0;flex:1}}.headmain strong{{display:block;font-size:14px}}.headmain small{{display:block;color:var(--muted);font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.live{{border:1px solid rgba(66,226,140,.25);color:var(--green);border-radius:999px;padding:6px 8px;font-size:9px;font-weight:900}}.hero{{padding:18px 2px 12px}}.hero h1{{font-size:24px;margin:0;letter-spacing:-.03em}}.hero p{{margin:4px 0 0;color:var(--muted);font-size:11px}}.userline{{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-top:10px}}.userline span{{font-size:10px;color:var(--muted)}}.userline form{{margin:0}}.userline button,.classic{{border:1px solid var(--line);background:#0b1720;color:#a9bfbc;border-radius:9px;padding:8px 10px;font-weight:800;font-size:10px}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:4px 0 12px}}.metrics div{{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:11px}}.metrics small{{display:block;color:var(--muted);font-size:8px;text-transform:uppercase}}.metrics strong{{display:block;font-size:21px;margin-top:4px}}.green{{color:var(--green)}}.red{{color:var(--red)}}.quick{{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px}}.quick a{{border:1px solid var(--line);background:#0b1821;border-radius:12px;padding:13px;font-weight:850;text-align:center}}section{{margin:16px 0}}.sectionhead{{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}}.sectionhead h2{{font-size:14px;margin:0}}.sectionhead a{{color:var(--teal);font-size:10px;font-weight:850}}.card{{border:1px solid var(--line);background:var(--panel);border-radius:13px;padding:12px;margin:8px 0;overflow:hidden}}.cardtop{{display:flex;justify-content:space-between;align-items:center;gap:8px}}.cardtop strong{{font-size:15px;overflow-wrap:anywhere}}.tag{{border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:9px;font-weight:900}}.tag.long,.tag.tp{{color:var(--green);border-color:rgba(66,226,140,.28)}}.tag.short,.tag.sl{{color:var(--red);border-color:rgba(255,98,125,.28)}}.tag.be{{color:var(--amber)}}.levels{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}}.levels div{{background:#08131a;border-radius:8px;padding:8px;min-width:0}}.levels small{{display:block;color:var(--muted);font-size:8px}}.levels b{{display:block;font-size:10px;overflow-wrap:anywhere}}.foot{{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-top:9px;color:var(--muted);font-size:9px}}.foot a{{color:var(--teal);font-weight:900}}.foot strong{{color:var(--text)}}.empty{{border:1px dashed var(--line);border-radius:12px;padding:24px;text-align:center;color:var(--muted);font-size:11px}}.bottom{{position:fixed;left:0;right:0;bottom:0;height:68px;padding-bottom:env(safe-area-inset-bottom);background:rgba(8,18,25,.99);border-top:1px solid var(--line);z-index:20;display:flex}}.bottom a{{flex:1;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;color:#69827f;font-size:8px;font-weight:800;min-width:0}}.bottom a span{{font-size:17px}}.bottom a.active{{color:var(--teal)}}@media(min-width:760px){{.bottom{{max-width:720px;left:50%;transform:translateX(-50%);border-left:1px solid var(--line);border-right:1px solid var(--line)}}}}
</style></head><body><div class="wrap">
<header><div class="logo">K</div><div class="headmain"><strong>Kripto Kontrol</strong><small>Mobil güvenli görünüm</small></div><span class="live">● CANLI</span></header>
<div class="hero"><h1>{_esc(title)}</h1><p>{_esc(subtitle)}</p><div class="userline"><span>{username}</span><div style="display:flex;gap:6px"><a class="classic" href="/?classic=1">Klasik</a><form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Çıkış</button></form></div></div></div>
{content}
</div>{nav}</body></html>'''


def make_v337_handler(
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
    BaseHandler = recovery.make_v336_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V337Handler(BaseHandler):
        server_version = "KriptoPanel/3.37"

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "mobile_safe_shell": True,
                    "mobile_safe_route": "/mobile-safe",
                    "mobile_default_bypass_legacy_spa": True,
                    "mobile_safe_javascript": False,
                    "desktop_runtime": "V3.36 preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return

            session = self._session()
            if path == "/" and session and _mobile_request(self.headers, query):
                self._redirect("/mobile-safe")
                return

            if path == "/mobile-safe":
                if not session:
                    self._redirect("/login")
                    return
                view = str((query.get("view") or ["home"])[0]).lower()
                try:
                    data = service.get_data()
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                body = mobile_safe_page(session, data, view)
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return

            return super().do_GET()

    return V337Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.37 Mobil Güvenli Görünüm")
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
    handler = make_v337_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} mobile_safe=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
