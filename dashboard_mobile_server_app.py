"""Kripto Kontrol Merkezi V3.32.2 - mobil sunucu arayüzü.

Masaüstünde çalışan V3.32.1 runtime aynen korunur. Telefon/tablet isteklerinde ana panel
JavaScript SPA yerine sunucu tarafında üretilir. FREE yalnız güvenli özet görür;
PREMIUM/ADMIN gerçek açık sinyal, işlem ve sonuç verisini görür. Mobil HTML JavaScript içermez.

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
import dashboard_runtimefix_app as desktop
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_2_MOBILE_SERVER_2026_08_16"


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


def tag(text: str, cls: str = "") -> str:
    return f'<span class="tag {html.escape(cls, quote=True)}">{esc(text)}</span>'


def signal_card(row: dict[str, Any], *, trade: bool = False) -> str:
    d = direction(row)
    cls = "long" if d == "LONG" else "short" if d == "SHORT" else ""
    sym = symbol(row)
    progress = str(row.get("progress") or ("TP3" if row.get("tp3_hit") else "TP2" if row.get("tp2_hit") else "TP1" if row.get("tp1_hit") else "AÇIK"))
    third_label = "SL" if trade else "TP1"
    third_value = row.get("sl") if trade else row.get("tp1")
    footer_right = esc(progress) if trade else f'<a href="/coin-center?symbol={urllib.parse.quote(sym)}">İncele</a>'
    return (
        '<article class="card">'
        f'<div class="cardtop"><strong>{esc(sym)}</strong>{tag(d or "AÇIK", cls)}</div>'
        '<div class="levels">'
        f'<div><small>Giriş</small><b>{esc(fmt(row.get("entry")))}</b></div>'
        f'<div><small>TP1</small><b>{esc(fmt(row.get("tp1")))}</b></div>'
        f'<div><small>{third_label}</small><b>{esc(fmt(third_value))}</b></div>'
        '</div>'
        f'<div class="foot"><span>{esc(system_name(row))}</span><strong>{footer_right}</strong></div>'
        '</article>'
    )


def result_card(row: dict[str, Any]) -> str:
    o = outcome(row)
    cls = "tp" if o.startswith("TP") and "BE" not in o else "sl" if o == "SL" or o.startswith("SL_") else "be" if "BE" in o else ""
    rv = num(row.get("r_result"))
    rtext = f"{rv:+.2f}R" if rv is not None else ""
    return (
        '<article class="card">'
        f'<div class="cardtop"><strong>{esc(symbol(row))}</strong>{tag(o, cls)}</div>'
        f'<div class="foot"><span>{esc(system_name(row))} · {esc(direction(row) or "İşlem")}</span><strong>{esc(rtext)}</strong></div>'
        '</article>'
    )


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


def mobile_page(session: dict[str, Any], data: dict[str, Any], *, plan: str, plan_label: str, view: str, is_admin: bool) -> str:
    premium = plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}
    view = view if view in {"home", "signals", "trades", "results"} else "home"
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    username = esc(session.get("username") or "üye")
    open_rows = [r for r in (data.get("open_trades") or []) if isinstance(r, dict)] if premium else []
    results = [r for r in (data.get("recent_results") or []) if isinstance(r, dict)] if premium else []
    public = commercial.build_public_summary(data) if not premium else None

    if not premium:
        title = "Ücretsiz Hesap"
        subtitle = "Piyasa özeti ve ürün görünümü"
        tp_rate = "—" if public.get("tp_rate_percent") is None else f"%{public.get('tp_rate_percent')}"
        content = f'''
        <div class="notice"><b>FREE</b><span>Giriş, TP/SL seviyeleri ve canlı işlem listesi Premium üyeliğe özeldir.</span></div>
        <div class="metrics"><div><small>Açık sistem kaydı</small><strong>{int(public.get('open_count') or 0)}</strong></div><div><small>Son TP oranı</small><strong>{esc(tp_rate)}</strong></div><div><small>Sistem durumu</small><strong>{esc(public.get('health') or '—')}</strong></div></div>
        <section><h2>FREE erişim</h2><div class="card"><p>Coin detay seviyeleri gösterilmez. Piyasa Merkezi herkese açık keşif için kullanılabilir.</p><div class="actions"><a href="/market-center">Piyasa Merkezi</a><a class="primary" href="/premium">Premium'u İncele</a></div></div></section>
        '''
        nav = '<nav><a class="active" href="/mobile">Ana</a><a href="/market-center">Piyasa</a><a href="/premium">Premium</a><a href="/account">Hesap</a></nav>'
    else:
        if view == "signals":
            title, subtitle = "Sinyaller", "Açık sinyaller · karar bilgisi"
            content = "".join(signal_card(r) for r in open_rows[:40]) or '<div class="empty">Şu anda açık sinyal yok.</div>'
        elif view == "trades":
            title, subtitle = "İşlemler", "Takipteki açık işlemler"
            content = "".join(signal_card(r, trade=True) for r in open_rows[:40]) or '<div class="empty">Takipte açık işlem yok.</div>'
        elif view == "results":
            title, subtitle = "Sonuçlar", "Son TP / SL / BE kayıtları"
            content = "".join(result_card(r) for r in results[:50]) or '<div class="empty">Henüz sonuç kaydı yok.</div>'
        else:
            title, subtitle = "Kontrol Merkezi", "Premium canlı sistem görünümü" if not is_admin else "Yönetici ürün görünümü"
            tp = sum(1 for r in results[:30] if outcome(r).startswith("TP") and "BE" not in outcome(r))
            sl = sum(1 for r in results[:30] if outcome(r) == "SL" or outcome(r).startswith("SL_"))
            strong = "".join(signal_card(r) for r in open_rows[:4]) or '<div class="empty">Şu anda açık sinyal yok.</div>'
            recent = "".join(result_card(r) for r in results[:4]) or '<div class="empty">Henüz sonuç kaydı yok.</div>'
            admin_link = '<a href="/admin/center">Yönetim Merkezi</a>' if is_admin else ''
            content = f'''
            <div class="metrics"><div><small>Açık işlem</small><strong>{len(open_rows)}</strong></div><div><small>Son TP</small><strong class="green">{tp}</strong></div><div><small>Son SL</small><strong class="red">{sl}</strong></div></div>
            <div class="actions"><a href="/market-center">Piyasa</a><a href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a>{admin_link}</div>
            <section><div class="sectionhead"><h2>Öne çıkan sinyaller</h2><a href="/mobile?view=signals">Tümü</a></div>{strong}</section>
            <section><div class="sectionhead"><h2>Son sonuçlar</h2><a href="/mobile?view=results">Tümü</a></div>{recent}</section>
            '''
        nav = '<nav>' + ''.join([
            f'<a class="{"active" if view == "home" else ""}" href="/mobile">Ana</a>',
            f'<a class="{"active" if view == "signals" else ""}" href="/mobile?view=signals">Sinyal</a>',
            f'<a class="{"active" if view == "trades" else ""}" href="/mobile?view=trades">İşlem</a>',
            f'<a class="{"active" if view == "results" else ""}" href="/mobile?view=results">Sonuç</a>',
            '<a href="/account">Hesap</a>',
        ]) + '</nav>'

    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><title>Kripto Kontrol</title><style>
:root{{--bg:#071018;--panel:#0c1720;--line:#1d303b;--text:#edf7f5;--muted:#819a97;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d}}
*{{box-sizing:border-box}}html,body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}body{{padding:12px;overflow-x:hidden}}a{{color:inherit;text-decoration:none;touch-action:manipulation;-webkit-tap-highlight-color:transparent}}.wrap{{max-width:720px;margin:auto}}header{{display:flex;align-items:center;gap:10px;padding:7px 0 13px;border-bottom:1px solid var(--line)}}.logo{{width:38px;height:38px;display:grid;place-items:center;border:1px solid #245148;border-radius:12px;color:var(--teal);font-weight:950}}.head{{flex:1;min-width:0}}.head strong{{display:block}}.head small{{display:block;color:var(--muted);font-size:9px}}.plan{{border:1px solid #2a4742;border-radius:999px;padding:5px 8px;color:var(--teal);font-size:8px;font-weight:900}}.hero{{padding:18px 0 10px}}.hero h1{{font-size:24px;margin:0}}.hero p{{margin:4px 0;color:var(--muted);font-size:11px}}.user{{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:9px;color:var(--muted);font-size:10px}}button,.btn{{border:1px solid var(--line);background:#0b1821;color:#a9bfbc;border-radius:9px;padding:8px 10px;font:800 10px system-ui}}nav{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin:10px 0 16px}}nav a{{border:1px solid var(--line);background:#0b1821;border-radius:10px;padding:11px 4px;text-align:center;font-size:9px;font-weight:850}}nav a.active{{border-color:#2b776a;color:var(--teal)}}.metrics{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin:8px 0 16px}}.metrics div,.card{{border:1px solid var(--line);background:var(--panel);border-radius:13px;padding:12px}}.metrics small,.levels small{{display:block;color:var(--muted);font-size:8px}}.metrics strong{{display:block;font-size:19px;margin-top:4px}}.green{{color:var(--green)}}.red{{color:var(--red)}}section{{margin:16px 0}}section h2,.sectionhead h2{{font-size:14px;margin:0 0 8px}}.sectionhead{{display:flex;align-items:center;justify-content:space-between}}.sectionhead a{{color:var(--teal);font-size:10px;font-weight:850}}.card{{margin:8px 0}}.card p{{color:var(--muted);font-size:11px;margin:0}}.cardtop,.foot{{display:flex;align-items:center;justify-content:space-between;gap:8px}}.cardtop strong{{font-size:15px}}.tag{{border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:9px;font-weight:900}}.tag.long,.tag.tp{{color:var(--green)}}.tag.short,.tag.sl{{color:var(--red)}}.levels{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-top:10px}}.levels div{{background:#08131a;border-radius:8px;padding:8px;min-width:0}}.levels b{{font-size:10px;overflow-wrap:anywhere}}.foot{{margin-top:9px;color:var(--muted);font-size:9px}}.foot strong{{color:var(--text)}}.foot a{{color:var(--teal)}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}}.actions a{{flex:1;min-width:120px;border:1px solid var(--line);background:#0b1821;border-radius:10px;padding:11px;text-align:center;font-size:10px;font-weight:850}}.actions .primary{{background:#0d2a24;color:var(--teal);border-color:#275b50}}.notice{{border:1px solid #3d4430;background:#18190f;border-radius:12px;padding:11px;display:flex;gap:8px;align-items:flex-start;margin:8px 0 14px}}.notice b{{color:#ffbd59}}.notice span{{color:#b8b69a;font-size:10px}}.empty{{border:1px dashed var(--line);border-radius:12px;padding:24px;text-align:center;color:var(--muted);font-size:11px}}@media(max-width:420px){{body{{padding:10px}}nav{{gap:4px}}nav a{{font-size:8px;padding:10px 2px}}.metrics{{gap:6px}}.metrics div{{padding:9px}}}}
</style></head><body><div class="wrap"><header><div class="logo">K</div><div class="head"><strong>Kripto Kontrol</strong><small>Mobil · sunucu görünümü</small></div><span class="plan">{esc(plan_label)}</span></header><div class="hero"><h1>{esc(title)}</h1><p>{esc(subtitle)}</p><div class="user"><span>{username}</span><div><a class="btn" href="/mobile">Yenile</a> <form style="display:inline" method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Çıkış</button></form></div></div></div>{nav}{content}</div></body></html>'''


def make_v3322_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None, history_cache: earlyperf.HistoricalPulseCache | None = None):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = desktop.make_v3321_handler(config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache)

    class V3322Handler(BaseHandler):
        server_version = "KriptoPanel/3.32.2"

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
                self._json(HTTPStatus.OK, {"status":"ok","version":VERSION,"desktop_runtime":"V3.32.1 preserved","mobile_runtime":"server_rendered_no_javascript","mobile_free_premium_separated":True,"mobile_legacy_spa_bypassed":True,"signal_engine":"unchanged","telegram":"unchanged","trade_management":"unchanged","ledger_write":"unchanged"})
                return
            session = self._session()
            if path == "/mobile":
                self._mobile_response(query)
                return
            if path == "/" and session and mobile_request(self.headers, query):
                self._mobile_response(query)
                return
            return super().do_GET()

    return V3322Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.2 mobil sunucu arayüzü")
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
    print(f"{VERSION} http://{args.host}:{args.port} mobile_server=1 desktop_v3321=preserved")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
