"""Kripto Kontrol Merkezi V3.32.4 - JS'siz mobil Piyasa ve Coin Merkezi.

V3.32.3 mobil ana paneli ve masaüstü V3.32.1 runtime aynen korunur. Telefon/tablet
isteklerinde Piyasa Merkezi ve Coin Merkezi de eski tarayıcı-SPA davranışından ayrılır;
HTML/SVG tamamen sunucu tarafında üretilir ve gezinme normal bağlantılarla yapılır.

FREE yalnız public piyasa verisi görür. PREMIUM/ADMIN coin bazlı açık işlem seviyeleri,
coin performans özeti ve sunucu tarafında üretilmiş salt-okunur grafik görür.
Canlı sinyal, strateji, radar, Telegram, TP/SL/BE, state/ledger ve üyelik yazımları değişmez.
"""
from __future__ import annotations

import argparse
import html
import math
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_coin_app as coin
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_mobile_server_app as mobile
import dashboard_runtimefix_app as current
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_4_MOBILE_MARKET_COIN_2026_08_16"
ALLOWED_BARS = ("15m", "1H", "4H", "1D")


def _n(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fmt(value: Any) -> str:
    number = _n(value)
    if number is None:
        return "—"
    absolute = abs(number)
    if absolute >= 1000:
        return f"{number:,.2f}".replace(",", ".")
    if absolute >= 1:
        return f"{number:.5f}".rstrip("0").rstrip(".")
    return f"{number:.9f}".rstrip("0").rstrip(".")


def _esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _change_text(value: Any) -> tuple[str, str]:
    number = _n(value)
    if number is None:
        return "—", ""
    return f"{number:+.2f}%", "up" if number >= 0 else "down"


def _market_sort_key(item: dict[str, Any], selected: str) -> tuple[int, int, float, str]:
    symbol = str(item.get("symbol") or "")
    selected_rank = 0 if symbol == selected else 1
    open_rank = 0 if str(item.get("kind") or "") == "OPEN" else 1
    change = abs(_n(item.get("change_24h_pct")) or 0.0)
    return selected_rank, open_rank, -change, symbol


def _market_card(item: dict[str, Any], *, premium: bool) -> str:
    symbol = str(item.get("symbol") or "—")
    change, change_cls = _change_text(item.get("change_24h_pct"))
    kind = str(item.get("kind") or "") if premium else ""
    direction = str(item.get("direction") or "").upper() if premium else ""
    outcome = str(item.get("outcome") or "").upper() if premium else ""
    context = ""
    if kind == "OPEN":
        context = f'<span class="context {"long" if direction == "LONG" else "short"}">{_esc(direction or "AÇIK")} · açık</span>'
    elif kind == "RECENT" and outcome:
        context = f'<span class="context recent">Son: {_esc(outcome)}</span>'
    href = f'/mobile/coin?symbol={urllib.parse.quote(symbol)}' if premium else '/premium'
    action = "Coini incele" if premium else "Premium detay"
    return f'''
    <article class="market-card">
      <a class="market-main" href="{href}">
        <div><strong>{_esc(symbol)}</strong>{context}</div>
        <div class="price"><b>{_esc(_fmt(item.get("last")))}</b><span class="{change_cls}">{_esc(change)}</span></div>
      </a>
      <details><summary>24s detayları</summary><div class="market-detail">
        <span><small>Yüksek</small><b>{_esc(_fmt(item.get("high_24h")))}</b></span>
        <span><small>Düşük</small><b>{_esc(_fmt(item.get("low_24h")))}</b></span>
        <span><small>Hacim</small><b>{_esc(_fmt(item.get("volume_24h")))}</b></span>
      </div><a class="detail-action" href="{href}">{action} ›</a></details>
    </article>'''


def _bottom_nav(active: str, *, premium: bool) -> str:
    market_class = "active" if active in {"market", "coin"} else ""
    signal_href = "/mobile?view=signals" if premium else "/premium"
    return (
        '<nav class="bottomnav">'
        '<a href="/mobile"><span>⌂</span>Ana</a>'
        f'<a class="{market_class}" href="/mobile/market"><span>⌁</span>Piyasa</a>'
        f'<a href="{signal_href}"><span>⚡</span>Sinyal</a>'
        '<a href="/mobile?view=results"><span>✓</span>Sonuç</a>' if premium else
        '<nav class="bottomnav"><a href="/mobile"><span>⌂</span>Ana</a>'
        f'<a class="{market_class}" href="/mobile/market"><span>⌁</span>Piyasa</a>'
        '<a href="/premium"><span>◆</span>Premium</a>'
        '<a href="/account"><span>○</span>Hesap</a></nav>'
    ) + ('<a href="/account"><span>○</span>Hesap</a></nav>' if premium else '')


def _shell(*, title: str, subtitle: str, plan_label: str, username: str, body: str, nav: str, top_link: str = "/mobile", top_text: str = "Kontrol") -> str:
    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="dark"><title>{_esc(title)} · Kripto Kontrol</title><style>
:root{{--bg:#071018;--panel:#0c1720;--panel2:#09141c;--line:#1d303b;--text:#edf7f5;--muted:#819a97;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59;--blue:#69a9ff}}*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}body{{padding:0 12px calc(86px + env(safe-area-inset-bottom));overflow-x:hidden}}a{{color:inherit;text-decoration:none;touch-action:manipulation;-webkit-tap-highlight-color:transparent}}.wrap{{max-width:720px;margin:auto}}header{{position:sticky;top:0;z-index:10;margin:0 -12px;padding:10px 12px;display:flex;align-items:center;gap:9px;background:rgba(7,16,24,.98);border-bottom:1px solid var(--line)}}.back{{border:1px solid var(--line);border-radius:9px;padding:8px 9px;color:#a9bfbc;font-size:9px;font-weight:850}}.head{{flex:1;min-width:0}}.head strong{{display:block;font-size:13px}}.head small{{display:block;color:var(--muted);font-size:8px}}.plan{{border:1px solid #2a4742;border-radius:999px;padding:4px 7px;color:var(--teal);font-size:8px;font-weight:900}}.hero{{padding:16px 0 8px}}.hero h1{{margin:0;font-size:24px;letter-spacing:-.03em}}.hero p{{margin:4px 0 0;color:var(--muted);font-size:10px}}.hero .who{{margin-top:7px;color:#617d79;font-size:8px}}.search{{display:flex;gap:7px;margin:9px 0 14px}}.search input{{min-width:0;flex:1;border:1px solid var(--line);background:#08151d;color:var(--text);border-radius:10px;padding:11px 12px;font-size:16px;text-transform:uppercase}}.search button{{border:1px solid #245148;background:#0d2a24;color:var(--teal);border-radius:10px;padding:10px 13px;font-weight:900}}.notice{{border:1px solid #3d4430;background:#18190f;border-radius:11px;padding:9px 10px;color:#b8b69a;font-size:9px;margin:8px 0 12px}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin:8px 0 14px}}.metric{{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:10px}}.metric small{{display:block;color:var(--muted);font-size:7px}}.metric b{{display:block;font-size:16px;margin-top:3px}}.up,.green{{color:var(--green)}}.down,.red{{color:var(--red)}}section{{margin:15px 0}}.sectionhead{{display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:7px}}.sectionhead h2{{margin:0;font-size:13px}}.sectionhead span,.sectionhead a{{font-size:8px;color:var(--muted)}}.market-card,.card{{border:1px solid var(--line);background:var(--panel);border-radius:12px;padding:11px;margin:7px 0}}.market-main{{display:flex;justify-content:space-between;align-items:center;gap:10px}}.market-main strong{{font-size:14px}}.price{{text-align:right}}.price b{{display:block;font-size:13px}}.price span{{display:block;font-size:9px;font-weight:900;margin-top:1px}}.context{{display:inline-block;margin-left:6px;border:1px solid var(--line);border-radius:999px;padding:2px 5px;font-size:7px;font-weight:900}}.context.long{{color:var(--green)}}.context.short{{color:var(--red)}}.context.recent{{color:var(--amber)}}details{{border-top:1px solid #152832;margin-top:8px;padding-top:7px}}summary{{cursor:pointer;list-style:none;color:var(--teal);font-size:8px;font-weight:850;touch-action:manipulation}}summary::-webkit-details-marker{{display:none}}.market-detail{{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:7px}}.market-detail span,.level,.mini{{background:var(--panel2);border-radius:8px;padding:7px;min-width:0}}.market-detail small,.level small,.mini small{{display:block;color:var(--muted);font-size:7px}}.market-detail b,.level b,.mini b{{display:block;font-size:9px;overflow-wrap:anywhere;margin-top:2px}}.detail-action{{display:block;margin-top:7px;color:var(--teal);font-size:8px;font-weight:850}}.more-list{{border:1px dashed #1b343d;border-radius:11px;padding:10px;margin:9px 0}}.coin-hero{{border:1px solid rgba(44,230,191,.2);background:linear-gradient(135deg,#0d222b,#08151d);border-radius:15px;padding:13px;margin:8px 0 11px}}.coin-top{{display:flex;justify-content:space-between;gap:9px;align-items:flex-start}}.coin-top h2{{margin:0;font-size:22px}}.coin-price{{text-align:right}}.coin-price b{{display:block;font-size:19px}}.coin-price span{{font-size:9px;font-weight:900}}.signal-box{{border:1px solid rgba(105,169,255,.18);background:rgba(105,169,255,.04);border-radius:12px;padding:10px;margin:10px 0}}.signal-head{{display:flex;justify-content:space-between;gap:8px}}.signal-head strong{{font-size:12px}}.dir{{border:1px solid var(--line);border-radius:999px;padding:3px 7px;font-size:8px;font-weight:950}}.dir.long{{color:var(--green)}}.dir.short{{color:var(--red)}}.levels{{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:8px}}.detailgrid{{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:7px}}.chart-card{{border:1px solid var(--line);background:#08151d;border-radius:13px;padding:9px;overflow:hidden}}.chart-card svg{{display:block;width:100%;height:auto;min-height:180px}}.barlinks{{display:flex;gap:5px;flex-wrap:wrap;margin:7px 0 0}}.barlinks a{{border:1px solid var(--line);border-radius:8px;padding:5px 8px;color:var(--muted);font-size:8px;font-weight:850}}.barlinks a.active{{border-color:#275b50;color:var(--teal);background:#0d2a24}}.chart-note{{color:#607b78;font-size:7px;margin-top:6px}}.result{{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 1px;border-bottom:1px solid #142630}}.result:last-child{{border-bottom:0}}.result strong{{display:block;font-size:11px}}.result small{{display:block;color:var(--muted);font-size:7px}}.out{{border:1px solid var(--line);border-radius:999px;padding:3px 6px;font-size:8px;font-weight:900}}.out.tp{{color:var(--green)}}.out.sl{{color:var(--red)}}.out.be{{color:var(--amber)}}.empty{{border:1px dashed var(--line);border-radius:11px;padding:20px;text-align:center;color:var(--muted);font-size:9px}}.bottomnav{{position:fixed;left:0;right:0;bottom:0;z-index:20;height:66px;padding-bottom:env(safe-area-inset-bottom);display:grid;grid-template-columns:repeat(5,1fr);background:rgba(8,18,25,.99);border-top:1px solid var(--line)}}.bottomnav a{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;color:#6f8986;font-size:7px;font-weight:800;min-width:0}}.bottomnav a span{{font-size:15px;line-height:1}}.bottomnav a.active{{color:var(--teal)}}@media(min-width:760px){{.bottomnav{{max-width:720px;left:50%;transform:translateX(-50%)}}}}@media(max-width:390px){{.metrics{{gap:5px}}.metric{{padding:8px}}.detailgrid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><div class="wrap"><header><a class="back" href="{top_link}">← {top_text}</a><div class="head"><strong>Kripto Kontrol</strong><small>Mobil · sunucu görünümü</small></div><span class="plan">{_esc(plan_label)}</span></header><div class="hero"><h1>{_esc(title)}</h1><p>{_esc(subtitle)}</p><div class="who">{_esc(username)}</div></div>{body}</div>{nav}</body></html>'''


def render_market_page(session: dict[str, Any], *, items: list[dict[str, Any]], plan: str, plan_label: str, selected: str = "", market_error: bool = False) -> str:
    premium = plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}
    selected = selected or "BTCUSDT"
    ordered = sorted(items, key=lambda item: _market_sort_key(item, selected))
    rising = sum(1 for item in ordered if (_n(item.get("change_24h_pct")) or 0) > 0)
    falling = sum(1 for item in ordered if (_n(item.get("change_24h_pct")) or 0) < 0)
    active = sum(1 for item in ordered if str(item.get("kind") or "") == "OPEN") if premium else 0
    first, extra = ordered[:10], ordered[10:30]
    cards = "".join(_market_card(item, premium=premium) for item in first) or '<div class="empty">Piyasa verisi şu anda alınamadı.</div>'
    if extra:
        cards += '<details class="more-list"><summary>Diğer piyasaları göster (' + str(len(extra)) + ')</summary>' + ''.join(_market_card(item, premium=premium) for item in extra) + '</details>'
    notice = '<div class="notice">OKX public piyasa verisi şu anda alınamadı. Ekran son işlem verilerini değiştirmez; daha sonra yeniden deneyin.</div>' if market_error else '<div class="notice">Fiyat ve 24 saat değişim public OKX verisidir. Panel hiçbir emir açmaz.</div>'
    active_metric = f'<div class="metric"><small>Açık sistem</small><b>{active}</b></div>' if premium else '<div class="metric"><small>Erişim</small><b style="font-size:11px">PUBLIC</b></div>'
    body = f'''
    <form class="search" method="get" action="/mobile/market"><input name="symbol" value="{_esc(selected)}" placeholder="BTCUSDT" autocomplete="off"><button type="submit">Bul</button></form>
    {notice}
    <div class="metrics"><div class="metric"><small>Yükselen</small><b class="green">{rising}</b></div><div class="metric"><small>Düşen</small><b class="red">{falling}</b></div>{active_metric}</div>
    <section><div class="sectionhead"><h2>İzlenen piyasa</h2><span>{len(ordered)} parite</span></div>{cards}</section>
    '''
    return _shell(title="Piyasa Merkezi", subtitle="Fiyat · 24s hareket · gerektiğinde detay", plan_label=plan_label, username=str(session.get("username") or "üye"), body=body, nav=_bottom_nav("market", premium=premium))


def _svg_chart(candles: list[dict[str, Any]], levels: dict[str, Any] | None = None) -> str:
    rows = [row for row in candles[-80:] if isinstance(row, dict) and _n(row.get("close")) is not None]
    if len(rows) < 2:
        return '<div class="empty">Grafik verisi şu anda alınamadı.</div>'
    width, height = 640.0, 220.0
    left, right, top, bottom = 10.0, 76.0, 10.0, 22.0
    values = [_n(row.get("close")) for row in rows]
    values = [value for value in values if value is not None]
    level_values: list[tuple[str, float, str]] = []
    for key, label, color in (("entry", "Giriş", "#69a9ff"), ("tp1", "TP1", "#42e28c"), ("sl", "SL", "#ff627d")):
        value = _n((levels or {}).get(key))
        if value is not None:
            level_values.append((label, value, color))
            values.append(value)
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.08, abs(hi) * 0.001, 1e-10)
    lo -= pad; hi += pad
    chart_w, chart_h = width - left - right, height - top - bottom
    def y(value: float) -> float:
        return top + (hi - value) / (hi - lo) * chart_h
    points: list[str] = []
    for index, row in enumerate(rows):
        close = float(row["close"])
        x = left + chart_w * index / max(1, len(rows) - 1)
        points.append(f"{x:.2f},{y(close):.2f}")
    grid: list[str] = []
    for index in range(5):
        yy = top + chart_h * index / 4
        val = hi - (hi - lo) * index / 4
        grid.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{width-right}" y2="{yy:.2f}" stroke="#17303a" stroke-width="1"/><text x="{width-right+6}" y="{yy+3:.2f}" fill="#6f8986" font-size="8">{html.escape(_fmt(val))}</text>')
    level_svg: list[str] = []
    for label, value, color in level_values:
        yy = y(value)
        level_svg.append(f'<line x1="{left}" y1="{yy:.2f}" x2="{width-right}" y2="{yy:.2f}" stroke="{color}" stroke-width="1" stroke-dasharray="5 4"/><text x="{left+4}" y="{yy-3:.2f}" fill="{color}" font-size="8" font-weight="700">{label} {html.escape(_fmt(value))}</text>')
    last = float(rows[-1]["close"])
    return f'<svg viewBox="0 0 640 220" role="img" aria-label="Fiyat grafiği"><rect width="640" height="220" rx="9" fill="#07151c"/>{"".join(grid)}<polyline points="{" ".join(points)}" fill="none" stroke="#2ce6bf" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>{"".join(level_svg)}<circle cx="{left+chart_w:.2f}" cy="{y(last):.2f}" r="3" fill="#2ce6bf"/></svg>'


def _coin_result(row: dict[str, Any]) -> str:
    outcome = str(row.get("outcome") or "KAPALI").upper()
    cls = "tp" if outcome.startswith("TP") else "sl" if outcome.startswith("SL") else "be" if "BE" in outcome else ""
    r_value = None
    for key in ("net_r", "r_multiple", "realized_r"):
        r_value = _n(row.get(key))
        if r_value is not None:
            break
    r_text = f"{r_value:+.2f}R" if r_value is not None else "—"
    return f'<div class="result"><div><strong>{_esc(row.get("system") or "Sistem")}</strong><small>{_esc(row.get("direction") or "İşlem")}</small></div><div><span class="out {cls}">{_esc(outcome)}</span> <b>{_esc(r_text)}</b></div></div>'


def render_coin_page(session: dict[str, Any], *, symbol: str, bar: str, plan_label: str, overview_item: dict[str, Any] | None, summary: dict[str, Any], candles: list[dict[str, Any]], chart_source: str = "PUBLIC", market_error: bool = False) -> str:
    item = overview_item or {}
    change, change_cls = _change_text(item.get("change_24h_pct"))
    open_rows = summary.get("open_trades") if isinstance(summary.get("open_trades"), list) else []
    result_rows = summary.get("results") if isinstance(summary.get("results"), list) else []
    perf = summary.get("performance") if isinstance(summary.get("performance"), dict) else {}
    trade = open_rows[0] if open_rows and isinstance(open_rows[0], dict) else None
    if trade:
        direction = str(trade.get("direction") or "").upper()
        dcls = "long" if direction == "LONG" else "short" if direction == "SHORT" else ""
        signal = f'''
        <div class="signal-box"><div class="signal-head"><div><strong>{_esc(trade.get("system") or "Sistem")}</strong><div style="color:var(--muted);font-size:7px">Açık işlem kaydı</div></div><span class="dir {dcls}">{_esc(direction or "AÇIK")}</span></div>
        <div class="levels"><div class="level"><small>Giriş</small><b>{_esc(_fmt(trade.get("entry")))}</b></div><div class="level"><small>TP1</small><b>{_esc(_fmt(trade.get("tp1")))}</b></div><div class="level"><small>SL</small><b>{_esc(_fmt(trade.get("sl")))}</b></div></div>
        <details><summary>Diğer seviyeler ve skor</summary><div class="detailgrid"><div class="mini"><small>TP2</small><b>{_esc(_fmt(trade.get("tp2")))}</b></div><div class="mini"><small>TP3</small><b>{_esc(_fmt(trade.get("tp3")))}</b></div><div class="mini"><small>Skor</small><b>{_esc(_fmt(trade.get("score") or trade.get("signal_score") or trade.get("quality_score")))}</b></div></div></details></div>'''
    else:
        signal = '<div class="empty">Bu coin için açık işlem kaydı yok.</div>'
    bars = ''.join(f'<a class="{"active" if value == bar else ""}" href="/mobile/coin?symbol={urllib.parse.quote(symbol)}&bar={urllib.parse.quote(value)}">{value}</a>' for value in ALLOWED_BARS)
    chart = _svg_chart(candles, trade)
    tp_rate = "—" if perf.get("tp_rate_percent") is None else f"%{perf.get('tp_rate_percent')}"
    net_r = "—" if perf.get("net_r") is None else f"{float(perf.get('net_r')):+.2f}R"
    first_results = ''.join(_coin_result(row) for row in result_rows[:5] if isinstance(row, dict)) or '<div class="empty">Bu coin için kapanmış sonuç yok.</div>'
    extra_results = [row for row in result_rows[5:25] if isinstance(row, dict)]
    if extra_results:
        first_results += '<details class="more-list"><summary>Daha eski sonuçları göster (' + str(len(extra_results)) + ')</summary>' + ''.join(_coin_result(row) for row in extra_results) + '</details>'
    error_note = '<div class="notice">Public fiyat veya grafik kaynağının bir bölümü şu anda alınamadı. İşlem kayıtları değişmeden gösterilir.</div>' if market_error else ''
    body = f'''
    <form class="search" method="get" action="/mobile/coin"><input name="symbol" value="{_esc(symbol)}" placeholder="BTCUSDT" autocomplete="off"><input type="hidden" name="bar" value="{_esc(bar)}"><button type="submit">Aç</button></form>
    {error_note}
    <div class="coin-hero"><div class="coin-top"><div><h2>{_esc(symbol)}</h2><div style="color:var(--muted);font-size:8px">Public piyasa + sistem geçmişi</div></div><div class="coin-price"><b>{_esc(_fmt(item.get("last")))}</b><span class="{change_cls}">{_esc(change)}</span></div></div></div>
    {signal}
    <section><div class="sectionhead"><h2>Fiyat grafiği</h2><span>{_esc(bar)}</span></div><div class="chart-card">{chart}<div class="barlinks">{bars}</div><div class="chart-note">{_esc(chart_source)} · son {min(len(candles),80)} mum · sunucu SVG · JavaScript yok</div></div></section>
    <section><div class="sectionhead"><h2>Coin performansı</h2><span>panel kayıtları</span></div><div class="metrics"><div class="metric"><small>Örnek</small><b>{int(perf.get('sample') or 0)}</b></div><div class="metric"><small>TP oranı</small><b>{_esc(tp_rate)}</b></div><div class="metric"><small>Net R</small><b>{_esc(net_r)}</b></div></div><details class="more-list"><summary>TP / SL / BE dağılımını göster</summary><div class="detailgrid"><div class="mini"><small>TP</small><b>{int(perf.get('tp') or 0)}</b></div><div class="mini"><small>SL</small><b>{int(perf.get('sl') or 0)}</b></div><div class="mini"><small>BE</small><b>{int(perf.get('be') or 0)}</b></div></div></details></section>
    <section><div class="sectionhead"><h2>Son sonuçlar</h2><span>en güncel</span></div><div class="card">{first_results}</div></section>
    <div class="notice">Coin performansı panelin sinyal/sonuç kayıtlarını özetler; borsa hesabındaki gerçekleşmiş P&L değildir ve gelecek performansı garanti etmez.</div>
    '''
    return _shell(title="Coin Merkezi", subtitle="Önce fiyat ve karar seviyeleri · ayrıntılar isteğe bağlı", plan_label=plan_label, username=str(session.get("username") or "üye"), body=body, nav=_bottom_nav("coin", premium=True), top_link="/mobile/market", top_text="Piyasa")


def make_v3324_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None, history_cache: earlyperf.HistoricalPulseCache | None = None):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview = overview_client or market.OKXMarketOverviewClient(cache_seconds=20)
    BaseHandler = current.make_v3321_handler(config, service, sessions, limiter, store, candle_client, overview, history_cache=history_cache)

    class V3324Handler(BaseHandler):
        server_version = "KriptoPanel/3.32.4"

        def _identity(self):
            session = self._session()
            if not session:
                return None, False, False, commercial.PLAN_FREE, "Ücretsiz"
            is_admin = bool(self._is_admin_session(session))
            is_premium = bool(self._is_premium(session))
            plan, label = mobile._plan(store, session, is_admin=is_admin, is_premium=is_premium)
            return session, is_admin, is_premium, plan, label

        def _safe_data(self) -> dict[str, Any]:
            try:
                data = service.get_data()
            except Exception:
                data = {}
            return data if isinstance(data, dict) else {}

        def _serve_market(self, query: dict[str, list[str]]) -> None:
            session, is_admin, is_premium, plan, label = self._identity()
            if not session:
                self._redirect("/login"); return
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
            body = render_market_page(session, items=items, plan=plan, plan_label=label, selected=selected, market_error=market_error)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")

        def _serve_coin(self, query: dict[str, list[str]]) -> None:
            session, is_admin, is_premium, plan, label = self._identity()
            if not session:
                self._redirect("/login"); return
            if plan not in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}:
                self._redirect("/premium"); return
            raw_symbol = str((query.get("symbol") or ["BTCUSDT"])[0] or "BTCUSDT")
            try:
                symbol = OKXMarketDataClient.normalize_symbol(raw_symbol)
            except ValueError:
                symbol = "BTCUSDT"
            bar = str((query.get("bar") or ["15m"])[0])
            if bar not in ALLOWED_BARS:
                bar = "15m"
            data = self._safe_data()
            try:
                summary = coin.build_coin_summary(data, symbol)
            except Exception:
                summary = {"symbol": symbol, "open_trades": [], "results": [], "performance": {}}
            market_error = False
            overview_item: dict[str, Any] | None = None
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
                chart_source = str(chart_payload.get("source") or "PUBLIC") if isinstance(chart_payload, dict) else "PUBLIC"
            except Exception:
                market_error = True
            body = render_coin_page(session, symbol=symbol, bar=bar, plan_label=label, overview_item=overview_item, summary=summary, candles=candles, chart_source=chart_source, market_error=market_error)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            query = urllib.parse.parse_qs(parsed.query)
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "desktop_runtime": "V3.32.1 preserved",
                    "mobile_runtime": "server_rendered_no_javascript",
                    "mobile_main": "V3.32.3 preserved",
                    "mobile_market": "server_rendered_public_okx",
                    "mobile_coin": "server_rendered_premium_svg",
                    "mobile_chart": "svg_no_javascript",
                    "mobile_free_premium_separated": True,
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            session = self._session()
            force_mobile = path in {"/mobile/market", "/mobile/coin"}
            detected_mobile = bool(session and mobile.mobile_request(self.headers, query))
            if path in {"/mobile/market", "/market-center"} and (force_mobile or detected_mobile):
                self._serve_market(query); return
            if path in {"/mobile/coin", "/coin-center"} and (force_mobile or detected_mobile):
                self._serve_coin(query); return
            return super().do_GET()

    return V3324Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.4 mobil Piyasa/Coin runtime")
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
    handler = make_v3324_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} mobile_market_coin=1 mobile_js=0 desktop_v3321=preserved signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
