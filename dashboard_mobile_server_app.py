"""Kripto Kontrol Merkezi V3.32.3 - mobil ürün deneyimi.

Masaüstünde çalışan V3.32.1 runtime aynen korunur. Telefon/tablet isteklerinde ana panel
JavaScript SPA yerine sunucu tarafında üretilir. FREE yalnız güvenli özet görür;
PREMIUM/ADMIN gerçek açık sinyal, işlem ve sonuç verisini görür. Mobil HTML JavaScript içermez.

V3.32.3 yalnız mobil sunum katmanını iyileştirir: karar bilgisi ilk bakışta, teknik ve
uzun içerik isteğe bağlı detaylarda gösterilir. Canlı sinyal, strateji, radar, Telegram,
TP/SL/BE, state/ledger ve üyelik backend'i değişmez.
"""
from __future__ import annotations

import argparse
import html
import os
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_runtimefix_app as desktop
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_3_MOBILE_PRODUCT_UX_2026_08_16"


def mobile_request(headers, query: dict[str, list[str]] | None = None) -> bool:
    query = query or {}
    if str((query.get("desktop") or [""])[0]).lower() in {"1", "true", "yes"}:
        return False
    if str((query.get("mobile") or [""])[0]).lower() in {"1", "true", "yes"}:
        return True
    if str(headers.get("Sec-CH-UA-Mobile") or "").strip() == "?1":
        return True
    ua = str(headers.get("User-Agent") or "").lower()
    return any(token in ua for token in ("android", "iphone", "ipad", "ipod", "mobile"))


def esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt(value: Any) -> str:
    n = num(value)
    if n is None:
        return "—"
    a = abs(n)
    if a >= 1000:
        return f"{n:,.2f}".replace(",", ".")
    if a >= 1:
        return f"{n:.5f}".rstrip("0").rstrip(".")
    return f"{n:.9f}".rstrip("0").rstrip(".")


def symbol(row: dict[str, Any]) -> str:
    return str(row.get("symbol") or row.get("display_symbol") or "—").replace("/USDT:USDT", "USDT").replace("/", "")


def direction(row: dict[str, Any]) -> str:
    return str(row.get("direction") or "").upper()


def system_name(row: dict[str, Any]) -> str:
    return str(row.get("system_label") or row.get("system") or row.get("source") or "Sistem")


def outcome(row: dict[str, Any]) -> str:
    return str(row.get("outcome") or row.get("result") or row.get("final_result") or "KAPALI").upper()


def score(row: dict[str, Any]) -> str:
    for key in ("score", "signal_score", "confidence", "quality_score", "strength"):
        value = num(row.get(key))
        if value is not None:
            return f"{value:.0f}" if value.is_integer() else f"{value:.1f}"
    return "—"


def progress(row: dict[str, Any]) -> str:
    explicit = str(row.get("progress") or "").strip().upper()
    if explicit:
        return explicit
    if row.get("tp3_hit"):
        return "TP3"
    if row.get("tp2_hit"):
        return "TP2"
    if row.get("tp1_hit"):
        return "TP1"
    return "AÇIK"


def tag(text: str, cls: str = "") -> str:
    return f'<span class="tag {html.escape(cls, quote=True)}">{esc(text)}</span>'


def signal_card(row: dict[str, Any], *, trade: bool = False) -> str:
    """İlk bakışta karar bilgisi; ikincil seviyeler HTML details içinde."""
    d = direction(row)
    cls = "long" if d == "LONG" else "short" if d == "SHORT" else ""
    sym = symbol(row)
    p = progress(row)
    details = (
        '<details class="more"><summary>Detayları göster</summary>'
        '<div class="detailgrid">'
        f'<div><small>TP2</small><b>{esc(fmt(row.get("tp2")))}</b></div>'
        f'<div><small>TP3</small><b>{esc(fmt(row.get("tp3")))}</b></div>'
        f'<div><small>Skor</small><b>{esc(score(row))}</b></div>'
        f'<div><small>Durum</small><b>{esc(p)}</b></div>'
        '</div>'
        f'<a class="detail-link" href="/coin-center?symbol={urllib.parse.quote(sym)}">Coini incele ›</a>'
        '</details>'
    )
    footer_label = "Takip" if trade else "Sinyal"
    return (
        '<article class="card">'
        f'<div class="cardtop"><div><strong>{esc(sym)}</strong><small class="system">{esc(system_name(row))}</small></div>{tag(d or "AÇIK", cls)}</div>'
        '<div class="levels">'
        f'<div><small>Giriş</small><b>{esc(fmt(row.get("entry")))}</b></div>'
        f'<div><small>TP1</small><b>{esc(fmt(row.get("tp1")))}</b></div>'
        f'<div><small>SL</small><b>{esc(fmt(row.get("sl")))}</b></div>'
        '</div>'
        f'<div class="foot"><span>{footer_label}</span><strong>{esc(p)}</strong></div>'
        f'{details}'
        '</article>'
    )


def result_card(row: dict[str, Any]) -> str:
    o = outcome(row)
    cls = "tp" if o.startswith("TP") and "BE" not in o else "sl" if o == "SL" or o.startswith("SL_") else "be" if "BE" in o else ""
    rv = num(row.get("r_result"))
    rtext = f"{rv:+.2f}R" if rv is not None else "—"
    return (
        '<article class="resultcard">'
        f'<div class="resultmain"><strong>{esc(symbol(row))}</strong><small>{esc(system_name(row))} · {esc(direction(row) or "İşlem")}</small></div>'
        f'<div class="resultright">{tag(o, cls)}<b>{esc(rtext)}</b></div>'
        '</article>'
    )


def compact_rows(
    rows: list[dict[str, Any]],
    render: Callable[[dict[str, Any]], str],
    *,
    first: int,
    maximum: int,
    summary: str,
) -> str:
    visible = rows[:first]
    extra = rows[first:maximum]
    body = "".join(render(row) for row in visible)
    if extra:
        body += (
            f'<details class="more-list"><summary>{esc(summary)} ({len(extra)})</summary>'
            + "".join(render(row) for row in extra)
            + '</details>'
        )
    if len(rows) > maximum:
        body += f'<div class="listnote">En güncel {maximum} kayıt gösteriliyor.</div>'
    return body


def _plan(store, session: dict[str, Any], *, is_admin: bool, is_premium: bool) -> tuple[str, str]:
    username = str(session.get("username") or "")
    info = None
    try:
        info = store.plan_info(username) if store else None
    except Exception:
        info = None
    if isinstance(info, dict):
        plan = str(info.get("plan") or "").upper()
        label = str(info.get("plan_label") or plan.title())
        if plan in commercial.ALLOWED_PLANS:
            return plan, label
    if is_admin:
        return commercial.PLAN_ADMIN, "Yönetici"
    if is_premium:
        return commercial.PLAN_PREMIUM, "Premium"
    return commercial.PLAN_FREE, "Ücretsiz"


def _data_label(data: dict[str, Any]) -> str:
    quality = data.get("data_quality") if isinstance(data.get("data_quality"), dict) else {}
    if quality.get("ok") is False:
        return "Son geçerli kayıt"
    if quality.get("ok") is True:
        return "Canlı kayıt"
    return "Sistem kaydı"


def _refresh_time() -> str:
    try:
        return datetime.now(ZoneInfo("Europe/Istanbul")).strftime("%H:%M")
    except Exception:
        return datetime.now().strftime("%H:%M")


def mobile_page(session: dict[str, Any], data: dict[str, Any], *, plan: str, plan_label: str, view: str, is_admin: bool) -> str:
    premium = plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}
    view = view if view in {"home", "signals", "trades", "results"} else "home"
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    username = esc(session.get("username") or "üye")
    open_rows = [r for r in (data.get("open_trades") or []) if isinstance(r, dict)] if premium else []
    results = [r for r in (data.get("recent_results") or []) if isinstance(r, dict)] if premium else []
    public = commercial.build_public_summary(data) if not premium else None
    refreshed = _refresh_time()

    if not premium:
        title = "Ücretsiz Hesap"
        subtitle = "Piyasa özeti · detaylı işlem seviyeleri kapalı"
        tp_rate = "—" if public.get("tp_rate_percent") is None else f"%{public.get('tp_rate_percent')}"
        content = f'''
        <div class="notice"><b>FREE</b><span>Giriş, TP/SL seviyeleri, coin bazlı canlı sinyaller ve işlem takibi Premium üyeliğe özeldir.</span></div>
        <div class="metrics"><div><small>Açık sistem kaydı</small><strong>{int(public.get('open_count') or 0)}</strong></div><div><small>Son TP oranı</small><strong>{esc(tp_rate)}</strong></div><div><small>Sistem</small><strong class="metricword">{esc(public.get('health') or '—')}</strong></div></div>
        <section><div class="sectionhead"><h2>FREE erişim</h2><span>Dahil olanlar</span></div><div class="card info-card"><p>Piyasa keşfi ve güvenli performans özeti açıktır. Coin, giriş ve risk seviyeleri gösterilmez.</p><div class="actions"><a href="/market-center">Piyasa Merkezi</a><a class="primary" href="/premium">Premium'u İncele</a></div></div></section>
        <details class="more info-more"><summary>Premium'da neler açılır?</summary><div class="featureline">Açık sinyaller · Giriş/TP/SL · İşlem takibi · Coin Merkezi · Sonuç detayları</div></details>
        '''
        nav = '<nav class="bottomnav nav4"><a class="active" href="/mobile"><span>⌂</span>Ana</a><a href="/market-center"><span>⌁</span>Piyasa</a><a href="/premium"><span>◆</span>Premium</a><a href="/account"><span>○</span>Hesap</a></nav>'
        refresh_href = "/mobile"
    else:
        if view == "signals":
            title, subtitle = "Sinyaller", "Karar bilgisi önce · teknik seviyeler isteğe bağlı"
            content = compact_rows(open_rows, signal_card, first=12, maximum=40, summary="Diğer açık sinyalleri göster") or '<div class="empty">Şu anda açık sinyal yok.</div>'
        elif view == "trades":
            title, subtitle = "İşlemler", "Takipteki açık işlemler · Giriş / TP1 / SL"
            content = compact_rows(open_rows, lambda row: signal_card(row, trade=True), first=12, maximum=40, summary="Diğer işlemleri göster") or '<div class="empty">Takipte açık işlem yok.</div>'
        elif view == "results":
            title, subtitle = "Sonuçlar", "En güncel TP / SL / BE kayıtları"
            content = compact_rows(results, result_card, first=12, maximum=50, summary="Daha eski sonuçları göster") or '<div class="empty">Henüz sonuç kaydı yok.</div>'
        else:
            title, subtitle = "Kontrol Merkezi", "Premium canlı sistem görünümü" if not is_admin else "Yönetici ürün görünümü"
            recent30 = results[:30]
            tp = sum(1 for r in recent30 if outcome(r).startswith("TP") and "BE" not in outcome(r))
            sl = sum(1 for r in recent30 if outcome(r) == "SL" or outcome(r).startswith("SL_"))
            longs = sum(1 for r in open_rows if direction(r) == "LONG")
            shorts = sum(1 for r in open_rows if direction(r) == "SHORT")
            strong = "".join(signal_card(r) for r in open_rows[:3]) or '<div class="empty">Şu anda açık sinyal yok.</div>'
            recent = "".join(result_card(r) for r in results[:3]) or '<div class="empty">Henüz sonuç kaydı yok.</div>'
            admin_link = '<a href="/admin/center">Yönetim</a>' if is_admin else ''
            content = f'''
            <div class="statusline"><span>● {esc(_data_label(data))}</span><small>Ekran {esc(refreshed)} yenilendi</small></div>
            <div class="metrics"><div><small>Açık işlem</small><strong>{len(open_rows)}</strong><em>{longs} LONG · {shorts} SHORT</em></div><div><small>Son 30 TP</small><strong class="green">{tp}</strong></div><div><small>Son 30 SL</small><strong class="red">{sl}</strong></div></div>
            <div class="actions homeactions"><a href="/market-center">Piyasa</a><a href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a>{admin_link}</div>
            <section><div class="sectionhead"><h2>Öne çıkan sinyaller</h2><a href="/mobile?view=signals">Tümü ›</a></div>{strong}</section>
            <section><div class="sectionhead"><h2>Son sonuçlar</h2><a href="/mobile?view=results">Tümü ›</a></div>{recent}</section>
            '''
        nav = '<nav class="bottomnav nav5">' + ''.join([
            f'<a class="{"active" if view == "home" else ""}" href="/mobile"><span>⌂</span>Ana</a>',
            f'<a class="{"active" if view == "signals" else ""}" href="/mobile?view=signals"><span>⚡</span>Sinyal</a>',
            f'<a class="{"active" if view == "trades" else ""}" href="/mobile?view=trades"><span>↕</span>İşlem</a>',
            f'<a class="{"active" if view == "results" else ""}" href="/mobile?view=results"><span>✓</span>Sonuç</a>',
            '<a href="/account"><span>○</span>Hesap</a>',
        ]) + '</nav>'
        refresh_href = "/mobile" if view == "home" else f"/mobile?view={urllib.parse.quote(view)}"

    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Kripto Kontrol</title><style>
:root{{--bg:#071018;--panel:#0c1720;--panel2:#09141c;--line:#1d303b;--text:#edf7f5;--muted:#819a97;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59}}
*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}body{{padding:0 12px calc(88px + env(safe-area-inset-bottom));overflow-x:hidden}}a{{color:inherit;text-decoration:none;touch-action:manipulation;-webkit-tap-highlight-color:transparent}}button{{touch-action:manipulation}}.wrap{{max-width:720px;margin:auto}}header{{position:sticky;top:0;z-index:5;margin:0 -12px;padding:11px 14px;display:flex;align-items:center;gap:10px;background:rgba(7,16,24,.97);border-bottom:1px solid var(--line)}}.logo{{width:38px;height:38px;display:grid;place-items:center;border:1px solid #245148;border-radius:12px;color:var(--teal);font-weight:950}}.head{{flex:1;min-width:0}}.head strong{{display:block;font-size:14px}}.head small{{display:block;color:var(--muted);font-size:9px}}.plan{{border:1px solid #2a4742;border-radius:999px;padding:5px 8px;color:var(--teal);font-size:8px;font-weight:900}}.hero{{padding:17px 0 9px}}.hero h1{{font-size:24px;margin:0;letter-spacing:-.025em}}.hero p{{margin:4px 0;color:var(--muted);font-size:11px}}.user{{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:9px;color:var(--muted);font-size:10px}}.user-actions{{display:flex;align-items:center;gap:6px}}.user-actions form{{margin:0}}button,.btn{{display:inline-block;border:1px solid var(--line);background:#0b1821;color:#a9bfbc;border-radius:9px;padding:8px 10px;font:800 10px system-ui}}.statusline{{display:flex;align-items:center;justify-content:space-between;gap:8px;border:1px solid #17352f;background:#091a17;border-radius:10px;padding:8px 10px;margin:5px 0 10px}}.statusline span{{color:var(--green);font-size:9px;font-weight:850}}.statusline small{{color:var(--muted);font-size:8px}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:8px 0 14px}}.metrics div,.card{{border:1px solid var(--line);background:var(--panel);border-radius:13px;padding:12px}}.metrics small,.levels small,.detailgrid small{{display:block;color:var(--muted);font-size:8px}}.metrics strong{{display:block;font-size:19px;margin-top:4px}}.metrics em{{display:block;color:var(--muted);font-size:7px;font-style:normal;margin-top:2px}}.metricword{{font-size:12px!important;margin-top:8px!important}}.green{{color:var(--green)}}.red{{color:var(--red)}}section{{margin:17px 0}}section h2,.sectionhead h2{{font-size:14px;margin:0}}.sectionhead{{display:flex;align-items:center;justify-content:space-between;margin-bottom:8px}}.sectionhead>a{{color:var(--teal);font-size:10px;font-weight:850}}.sectionhead>span{{color:var(--muted);font-size:9px}}.card{{margin:8px 0}}.card p{{color:var(--muted);font-size:11px;margin:0}}.cardtop,.foot{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.cardtop>div{{min-width:0}}.cardtop strong{{display:block;font-size:15px;overflow-wrap:anywhere}}.system{{display:block;color:var(--muted);font-size:8px;margin-top:2px}}.tag{{border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:9px;font-weight:900;white-space:nowrap}}.tag.long,.tag.tp{{color:var(--green);border-color:rgba(66,226,140,.25)}}.tag.short,.tag.sl{{color:var(--red);border-color:rgba(255,98,125,.25)}}.tag.be{{color:var(--amber)}}.levels{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}}.levels div{{background:var(--panel2);border-radius:8px;padding:8px;min-width:0}}.levels b{{display:block;font-size:10px;overflow-wrap:anywhere}}.foot{{margin-top:9px;color:var(--muted);font-size:9px}}.foot strong{{color:var(--text)}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}.actions a{{flex:1;min-width:105px;border:1px solid var(--line);background:#0b1821;border-radius:10px;padding:11px;text-align:center;font-size:10px;font-weight:850}}.actions .primary{{background:#0d2a24;color:var(--teal);border-color:#275b50}}.homeactions{{margin-top:0}}.notice{{border:1px solid #3d4430;background:#18190f;border-radius:12px;padding:11px;display:flex;gap:8px;align-items:flex-start;margin:8px 0 14px}}.notice b{{color:var(--amber)}}.notice span{{color:#b8b69a;font-size:10px}}.empty{{border:1px dashed var(--line);border-radius:12px;padding:24px;text-align:center;color:var(--muted);font-size:11px}}details.more{{border-top:1px solid #152832;margin-top:10px;padding-top:8px}}details summary{{cursor:pointer;list-style:none;color:var(--teal);font-size:9px;font-weight:850;touch-action:manipulation}}details summary::-webkit-details-marker{{display:none}}.detailgrid{{display:grid;grid-template-columns:repeat(4,1fr);gap:5px;margin-top:8px}}.detailgrid div{{background:#08131a;border-radius:7px;padding:7px;min-width:0}}.detailgrid b{{font-size:9px;overflow-wrap:anywhere}}.detail-link{{display:block;margin-top:8px;color:var(--teal);font-size:9px;font-weight:850}}.more-list{{border:1px dashed #1b343d;border-radius:12px;padding:11px;margin:10px 0}}.more-list>summary{{font-size:10px}}.more-list[open]>summary{{margin-bottom:10px}}.listnote{{color:var(--muted);font-size:9px;text-align:center;margin:9px 0}}.resultcard{{display:flex;align-items:center;gap:10px;border-bottom:1px solid #142630;padding:11px 2px}}.resultmain{{flex:1;min-width:0}}.resultmain strong{{display:block;font-size:13px}}.resultmain small{{display:block;color:var(--muted);font-size:8px;margin-top:2px}}.resultright{{display:flex;align-items:center;gap:7px}}.resultright b{{font-size:9px;min-width:48px;text-align:right}}.info-card{{margin-top:0}}.info-more{{border:1px solid var(--line);background:#09141c;border-radius:12px;padding:11px}}.featureline{{color:var(--muted);font-size:10px;margin-top:8px}}.bottomnav{{position:fixed;left:0;right:0;bottom:0;z-index:20;height:68px;padding-bottom:env(safe-area-inset-bottom);display:grid;background:rgba(8,18,25,.99);border-top:1px solid var(--line)}}.bottomnav.nav5{{grid-template-columns:repeat(5,1fr)}}.bottomnav.nav4{{grid-template-columns:repeat(4,1fr)}}.bottomnav a{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;color:#6f8986;font-size:8px;font-weight:800;min-width:0}}.bottomnav a span{{font-size:16px;line-height:1}}.bottomnav a.active{{color:var(--teal)}}@media(min-width:760px){{.bottomnav{{max-width:720px;left:50%;transform:translateX(-50%);border-left:1px solid var(--line);border-right:1px solid var(--line)}}}}@media(max-width:420px){{body{{padding-left:10px;padding-right:10px}}header{{margin-left:-10px;margin-right:-10px}}.metrics{{gap:6px}}.metrics div{{padding:9px}}.detailgrid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap"><header><div class="logo">K</div><div class="head"><strong>Kripto Kontrol</strong><small>Mobil · sade görünüm</small></div><span class="plan">{esc(plan_label)}</span></header><div class="hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p><div class="user"><span>{username}</span><div class="user-actions"><a class="btn" href="{refresh_href}">Yenile</a><form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Çıkış</button></form></div></div></div>{content}</div>{nav}</body></html>'''


def make_v3322_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None, history_cache: earlyperf.HistoricalPulseCache | None = None):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = desktop.make_v3321_handler(config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache)

    class V3323Handler(BaseHandler):
        server_version = "KriptoPanel/3.32.3"

        def _mobile_response(self, query: dict[str, list[str]]) -> None:
            session = self._session()
            if not session:
                self._redirect("/login")
                return
            is_admin = bool(self._is_admin_session(session))
            premium = bool(self._is_premium(session))
            plan, label = _plan(store, session, is_admin=is_admin, is_premium=premium)
            try:
                data = service.get_data()
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            view = str((query.get("view") or ["home"])[0]).lower()
            body = mobile_page(session, data, plan=plan, plan_label=label, view=view, is_admin=is_admin)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status":"ok","version":VERSION,"desktop_runtime":"V3.32.1 preserved","mobile_runtime":"server_rendered_no_javascript","mobile_free_premium_separated":True,"mobile_legacy_spa_bypassed":True,"mobile_progressive_disclosure":True,"mobile_primary_levels":"entry_tp1_sl","signal_engine":"unchanged","telegram":"unchanged","trade_management":"unchanged","ledger_write":"unchanged"})
                return
            session = self._session()
            if path == "/mobile":
                self._mobile_response(query)
                return
            if path == "/" and session and mobile_request(self.headers, query):
                self._mobile_response(query)
                return
            return super().do_GET()

    return V3323Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.3 mobil ürün deneyimi")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root)); config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = commercial.commercial_store_from_env(config)
    candle_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v3322_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} mobile_product_ux=1 desktop_v3321=preserved")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
