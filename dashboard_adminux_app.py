"""Kripto Kontrol Merkezi V3.2 - yönetim merkezi ve oturum UX.

V3.1 üzerine yalnız panel/yönetim katmanında eklenir:
- masaüstü ve mobil ana panelde görünür Çıkış,
- mobil ADMIN için Yönetim Merkezi kısayolu,
- masaüstü ADMIN için Yönetim Merkezi kısayolu,
- kullanıcı / üyelik / ödeme / sistem özetlerini birleştiren detaylı admin merkezi,
- Hesabım, Premium, Kullanıcılar ve Üyelik/Ödemeler sayfalarında görünür oturum kontrolleri.

Sinyal üretimi, strateji, radar, Telegram ve emir akışına dokunmaz.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_billing_app as billing
import dashboard_commercial_app as commercial
import dashboard_market_app as market
import dashboard_memory_app as memory
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service, env_bool

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_2_ADMIN_UX_2026_08_14"


def _csrf(session: dict[str, Any]) -> str:
    return html.escape(str(session.get("csrf") or ""), quote=True)


def _logout_form(session: dict[str, Any], *, css_class: str = "") -> str:
    cls = f' class="{html.escape(css_class, quote=True)}"' if css_class else ""
    return (
        f'<form{cls} method="post" action="/logout">'
        f'<input type="hidden" name="csrf" value="{_csrf(session)}">'
        '<button type="submit" aria-label="Çıkış yap" title="Çıkış yap"><span>↪</span><b>Çıkış</b></button>'
        '</form>'
    )


def _payment_badge(store: commercial.CommercialAccountStore) -> tuple[int, str]:
    try:
        pending = billing.payment_counts(store)["pending"]
    except accounts.AccountStoreError:
        pending = 0
    label = f"Üyelikler / Ödemeler · {pending}" if pending else "Üyelikler / Ödemeler"
    return pending, label


def enhance_dashboard(
    body: str,
    session: dict[str, Any],
    *,
    is_admin: bool,
    pending_count: int = 0,
) -> str:
    """Mevcut SPA'yı bozmadan oturum/yönetim kontrollerini görünür hale getirir."""
    css = r'''
    /* V3.2: görünür oturum ve admin erişimi */
    .v32-top-tools{display:flex;align-items:center;gap:7px}.v32-top-tools form{margin:0}.v32-top-tools a,.v32-top-tools button{height:32px;border:1px solid var(--line);border-radius:999px;background:#0b1720;color:#9eb5b2;padding:0 10px;font:800 10px/1 Inter,system-ui,sans-serif;display:inline-flex;align-items:center;gap:5px;text-decoration:none;cursor:pointer}.v32-top-tools a:hover,.v32-top-tools button:hover{border-color:rgba(44,230,191,.5);color:var(--teal)}.v32-top-tools button span,.v32-top-tools a span{font-size:13px}.v32-admin-count{min-width:17px;height:17px;border-radius:999px;background:rgba(255,189,89,.14);color:var(--amber);display:inline-grid;place-items:center;font-size:8px;font-weight:950}.mobile-nav .v32-mobile-admin-nav{color:var(--amber);min-width:58px}.sidebar .v32-admin-side{color:var(--amber)}
    @media(max-width:760px){.v32-top-tools{gap:4px}.v32-top-tools a,.v32-top-tools button{width:32px;height:32px;padding:0;justify-content:center;border-radius:10px}.v32-top-tools b{display:none}.v32-top-tools .v32-admin-count{position:absolute;transform:translate(10px,-11px);min-width:14px;height:14px;font-size:7px}.mobile-nav{overflow-x:auto}.mobile-nav .v32-mobile-admin-nav{flex:0 0 62px}}
    '''
    body = body.replace("  </style>", css + "\n  </style>", 1)

    admin_top = ""
    if is_admin:
        count = f'<i class="v32-admin-count">{pending_count}</i>' if pending_count else ""
        admin_top = f'<a href="/admin/center" aria-label="Yönetim Merkezi" title="Yönetim Merkezi"><span>◆</span><b>Yönetim</b>{count}</a>'
    top_tools = f'<div class="v32-top-tools">{admin_top}{_logout_form(session)}</div>'
    if "</header>" in body:
        body = body.replace("</header>", top_tools + "</header>", 1)
    else:
        body = body.replace("<body>", "<body>" + top_tools, 1)

    if is_admin:
        account_marker = '<a class="nav-item" href="/account"><span>○</span><b>Hesabım</b></a>'
        if account_marker in body and "/admin/center" not in body[: body.find(account_marker) + len(account_marker)]:
            side = '<a class="nav-item v32-admin-side" href="/admin/center"><span>◆</span><b>Yönetim Merkezi</b></a>'
            body = body.replace(account_marker, side + account_marker, 1)

        nav_start = body.find('<nav class="mobile-nav">')
        if nav_start >= 0:
            nav_end = body.find("</nav>", nav_start)
            if nav_end >= 0 and 'v32-mobile-admin-nav' not in body[nav_start:nav_end]:
                mobile_admin = '<a class="v32-mobile-admin-nav" href="/admin/center"><span>◆</span>Admin</a>'
                body = body[:nav_end] + mobile_admin + body[nav_end:]
    return body


def enhance_standalone(body: str, session: dict[str, Any], *, is_admin: bool) -> str:
    """Tekil üyelik/admin sayfalarında da çıkışı her ekranda görünür tutar."""
    css = r'''
    .v32-session-float{position:fixed;right:13px;bottom:13px;z-index:999;display:flex;gap:7px;align-items:center}.v32-session-float form{margin:0}.v32-session-float a,.v32-session-float button{border:1px solid #24414b;border-radius:10px;background:#091820;color:#b5cbc8;padding:9px 11px;font:850 10px/1 Inter,system-ui,sans-serif;display:inline-flex;align-items:center;gap:5px;text-decoration:none;box-shadow:0 8px 24px rgba(0,0,0,.28);cursor:pointer}.v32-session-float a:hover,.v32-session-float button:hover{border-color:#2ce6bf;color:#2ce6bf}.v32-session-float button span{font-size:13px}@media(max-width:600px){.v32-session-float{right:9px;bottom:9px}.v32-session-float a,.v32-session-float button{padding:9px}.v32-session-float b{display:none}}
    '''
    if "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)
    admin_link = '<a href="/admin/center">◆ <b>Yönetim</b></a>' if is_admin else ""
    controls = f'<div class="v32-session-float">{admin_link}<a href="/">⌂ <b>Panel</b></a>{_logout_form(session)}</div>'
    return body.replace("<body>", "<body>" + controls, 1)


def admin_snapshot(
    config: PanelConfig,
    store: commercial.CommercialAccountStore,
    service,
) -> dict[str, Any]:
    try:
        users = store.list_commercial_users()
    except accounts.AccountStoreError:
        users = []
    try:
        payments = store.list_payments()
        pay_counts = billing.payment_counts(store)
    except accounts.AccountStoreError:
        payments = []
        pay_counts = {"total": 0, "pending": 0, "approved": 0, "rejected": 0}

    dynamic_admins = sum(1 for row in users if str(row.get("role") or "").upper() == commercial.ROLE_ADMIN)
    free = sum(1 for row in users if str(row.get("plan") or "").upper() == commercial.PLAN_FREE)
    premium = sum(1 for row in users if str(row.get("plan") or "").upper() == commercial.PLAN_PREMIUM)
    active = sum(1 for row in users if bool(row.get("active", True)))
    passive = len(users) - active

    try:
        data = service.get_data()
    except Exception:
        data = {}
    open_rows = data.get("open_trades") if isinstance(data.get("open_trades"), list) else []
    result_rows = data.get("recent_results") if isinstance(data.get("recent_results"), list) else []
    health = data.get("health") if isinstance(data.get("health"), dict) else {}
    quality = data.get("data_quality") if isinstance(data.get("data_quality"), dict) else {}
    health_overall = str(health.get("overall") or ("GREEN" if quality.get("ok") else "UNKNOWN")).upper()
    outcomes = [str((row or {}).get("outcome") or (row or {}).get("result") or "").upper() for row in result_rows if isinstance(row, dict)]
    tp = sum(1 for value in outcomes if value.startswith("TP") and "BE" not in value)
    sl = sum(1 for value in outcomes if value == "SL" or value.startswith("SL_"))

    users_recent = sorted(users, key=lambda row: int(row.get("updated_at") or 0), reverse=True)[:12]
    return {
        "users": users,
        "dynamic_users": len(users),
        "admins": dynamic_admins + 1,
        "free": free,
        "premium": premium,
        "active": active,
        "passive": passive,
        "payments": payments[:12],
        "payment_counts": pay_counts,
        "users_recent": users_recent,
        "open_count": len(open_rows),
        "recent_results": len(result_rows),
        "tp": tp,
        "sl": sl,
        "health": health_overall,
        "data_ok": bool(quality.get("ok", False)),
        "bootstrap_admin": config.username,
    }


def admin_center_page(
    config: PanelConfig,
    store: commercial.CommercialAccountStore,
    service,
    session: dict[str, Any],
    settings: dict[str, Any],
) -> str:
    snap = admin_snapshot(config, store, service)
    csrf = _csrf(session)
    actor = html.escape(str(session.get("username") or config.username))
    pay = snap["payment_counts"]

    user_rows = "".join(
        f'<tr><td><strong>{html.escape(str(row.get("username") or ""))}</strong></td>'
        f'<td>{html.escape(commercial._plan_label(str(row.get("plan") or commercial.PLAN_FREE)))}</td>'
        f'<td>{"Aktif" if row.get("active", True) else "Pasif"}</td>'
        f'<td>{html.escape(commercial._format_expiry(row.get("expires_at")))}</td></tr>'
        for row in snap["users_recent"]
    ) or '<tr><td colspan="4" class="empty">Dinamik kullanıcı kaydı yok.</td></tr>'

    payment_rows = "".join(
        f'<tr><td>{billing._tr_time(row.get("created_at"))}</td>'
        f'<td><strong>{html.escape(str(row.get("username") or ""))}</strong></td>'
        f'<td>{html.escape(str(row.get("package") or ""))}</td>'
        f'<td><span class="status {billing._status_class(row.get("status"))}">{html.escape(billing._status_label(row.get("status")))}</span></td></tr>'
        for row in snap["payments"]
    ) or '<tr><td colspan="4" class="empty">Ödeme kaydı yok.</td></tr>'

    health_class = "good" if snap["health"] == "GREEN" else "warn"
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kripto Kontrol · Yönetim Merkezi</title>
<style>
:root{{--bg:#061016;--panel:#0b1b23;--panel2:#0d2029;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59;--blue:#60a5fa}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,rgba(44,230,191,.08),transparent 30%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}button{{font:inherit}}.shell{{width:min(1240px,calc(100% - 26px));margin:auto;padding:24px 0 54px}}.top{{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}}.top h1{{margin:0;font-size:30px;letter-spacing:-.035em}}.top p{{margin:4px 0 0;color:var(--muted)}}.top-actions,.quick{{display:flex;gap:8px;flex-wrap:wrap}}.btn,.top-actions button{{border:1px solid var(--line);border-radius:10px;background:#0a1820;color:#b2c8c5;padding:9px 11px;font-weight:850;font-size:10px;cursor:pointer}}.btn.primary{{border-color:rgba(44,230,191,.35);color:var(--teal)}}.top-actions form{{margin:0}}.top-actions button{{color:#ff9bad}}
.kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:18px 0}}.kpi{{border:1px solid var(--line);background:var(--panel);border-radius:14px;padding:14px;min-height:88px}}.kpi small{{color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.07em}}.kpi strong{{display:block;font-size:25px;margin-top:5px}}.kpi.teal strong{{color:var(--teal)}}.kpi.green strong{{color:var(--green)}}.kpi.amber strong{{color:var(--amber)}}.kpi.red strong{{color:var(--red)}}.kpi.blue strong{{color:var(--blue)}}
.quick{{margin-bottom:16px}}.quick a{{flex:1;min-width:180px;border:1px solid var(--line);background:var(--panel2);border-radius:13px;padding:14px}}.quick a b{{display:block;font-size:13px}}.quick a span{{display:block;color:var(--muted);font-size:10px;margin-top:3px}}.quick a:hover{{border-color:rgba(44,230,191,.4)}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.card{{border:1px solid var(--line);border-radius:15px;background:var(--panel);overflow:hidden;margin-bottom:12px}}.card-head{{padding:13px 15px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px;align-items:center}}.card-head h2{{font-size:14px;margin:0}}.card-head a{{color:var(--teal);font-size:10px;font-weight:850}}.card-body{{padding:14px}}.system-line{{display:grid;grid-template-columns:1fr auto;gap:8px;padding:9px 0;border-bottom:1px solid rgba(27,57,67,.7)}}.system-line:last-child{{border:0}}.system-line span{{color:var(--muted)}}.health{{font-weight:950}}.health.good{{color:var(--green)}}.health.warn{{color:var(--amber)}}.package{{border:1px solid rgba(44,230,191,.2);border-radius:11px;padding:12px;background:rgba(44,230,191,.035)}}.package strong{{display:block;font-size:16px}}.package span{{color:var(--muted);font-size:10px}}.package b{{display:block;color:var(--teal);margin-top:5px}}
table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:9px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-size:8px;text-transform:uppercase}}td{{font-size:10px}}.status{{font-weight:900}}.status.pending{{color:var(--amber)}}.status.approved{{color:var(--green)}}.status.rejected{{color:var(--red)}}.empty{{text-align:center;color:var(--muted);padding:18px}}
@media(max-width:900px){{.kpis{{grid-template-columns:1fr 1fr}}.grid{{grid-template-columns:1fr}}}}@media(max-width:560px){{.shell{{width:calc(100% - 18px);padding-top:16px}}.top h1{{font-size:24px}}.kpis{{grid-template-columns:1fr 1fr;gap:7px}}.kpi{{padding:11px;min-height:76px}}.kpi strong{{font-size:21px}}.quick{{display:grid;grid-template-columns:1fr 1fr}}.quick a{{min-width:0;padding:11px}}table{{display:block;overflow:auto}}.top-actions{{width:100%}}.top-actions .btn,.top-actions form,.top-actions button{{flex:1}}.top-actions form button{{width:100%}}}}
</style></head><body>
<div class="shell"><div class="top"><div><h1>Yönetim Merkezi</h1><p>Üyeler, ödemeler ve sistem sağlığı tek ekranda · Yönetici {actor}</p></div><div class="top-actions"><a class="btn" href="/">← Panel</a>{_logout_form(session)}</div></div>
<div class="kpis">
<div class="kpi blue"><small>Dinamik kullanıcı</small><strong>{snap['dynamic_users']}</strong></div>
<div class="kpi teal"><small>Premium</small><strong>{snap['premium']}</strong></div>
<div class="kpi"><small>FREE</small><strong>{snap['free']}</strong></div>
<div class="kpi green"><small>Aktif hesap</small><strong>{snap['active']}</strong></div>
<div class="kpi amber"><small>Bekleyen ödeme</small><strong>{pay['pending']}</strong></div>
<div class="kpi"><small>Onaylanan ödeme</small><strong>{pay['approved']}</strong></div>
<div class="kpi blue"><small>Açık işlem</small><strong>{snap['open_count']}</strong></div>
<div class="kpi {health_class}"><small>Sistem sağlığı</small><strong>{html.escape(str(snap['health']))}</strong></div>
</div>
<div class="quick">
<a href="/admin/users"><b>♙ Kullanıcı Yönetimi</b><span>Hesap aç, kapat, rol/süre/şifre yönet</span></a>
<a href="/admin/memberships"><b>◆ Üyelik & Ödemeler</b><span>{pay['pending']} ödeme onay bekliyor</span></a>
<a href="/advanced"><b>◉ Teknik Sistem</b><span>Kaynaklar, sağlık ve ayrıntılı kontrol</span></a>
<a href="/"><b>⌂ Canlı Panel</b><span>Sinyaller, fırsatlar ve sonuçlara dön</span></a>
</div>
<div class="grid"><div>
<div class="card"><div class="card-head"><h2>Sistem özeti</h2><a href="/advanced">Ayrıntı →</a></div><div class="card-body"><div class="system-line"><span>Genel durum</span><b class="health {health_class}">{html.escape(str(snap['health']))}</b></div><div class="system-line"><span>Veri kalitesi</span><b>{'Normal' if snap['data_ok'] else 'Kontrol gerekli'}</b></div><div class="system-line"><span>Açık işlem</span><b>{snap['open_count']}</b></div><div class="system-line"><span>Son sonuç kaydı</span><b>{snap['recent_results']}</b></div><div class="system-line"><span>TP / SL (son liste)</span><b>{snap['tp']} / {snap['sl']}</b></div><div class="system-line"><span>Yönetici hesabı</span><b>{html.escape(str(snap['bootstrap_admin']))}</b></div></div></div>
<div class="card"><div class="card-head"><h2>Aktif Premium paketi</h2><a href="/admin/memberships">Yönet →</a></div><div class="card-body"><div class="package"><strong>{html.escape(str(settings['package_name']))}</strong><span>Kod: {html.escape(str(settings['package_code']))} · {int(settings['days'])} gün</span><b>{html.escape(str(settings['price_label']))}</b></div></div></div>
</div><div>
<div class="card"><div class="card-head"><h2>Son kullanıcı güncellemeleri</h2><a href="/admin/users">Tümü →</a></div><div class="card-body" style="padding:0"><table><thead><tr><th>Kullanıcı</th><th>Plan</th><th>Durum</th><th>Bitiş</th></tr></thead><tbody>{user_rows}</tbody></table></div></div>
<div class="card"><div class="card-head"><h2>Son ödeme bildirimleri</h2><a href="/admin/memberships">Tümü →</a></div><div class="card-body" style="padding:0"><table><thead><tr><th>Tarih</th><th>Kullanıcı</th><th>Paket</th><th>Durum</th></tr></thead><tbody>{payment_rows}</tbody></table></div></div>
</div></div></div>
</body></html>"""


def make_v32_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    settings = billing._settings()
    crypto_enabled = env_bool("PANEL_CRYPTO_PAYMENT_ENABLED", False)
    BaseHandler = billing.make_v31_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V32Handler(BaseHandler):
        server_version = "KriptoPanel/3.2"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            info = self._plan_info(session)
            nonce = secrets.token_urlsafe(18)
            is_admin = str(info.get("plan")) == commercial.PLAN_ADMIN
            if str(info.get("plan")) == commercial.PLAN_FREE:
                body = commercial.free_member_page(session, info, nonce)
                body = enhance_standalone(body, session, is_admin=False)
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)
                return

            body = memory.memory_dashboard_page(session, nonce)
            pending_count = 0
            if is_admin:
                pending_count, label = _payment_badge(store)
                plan_link = f'<a class="badge" href="/admin/memberships">{html.escape(label)}</a>'
            else:
                plan_link = '<a class="badge" href="/premium">Premium</a>'
            marker = '<a class="badge" href="/account">Hesabım</a>'
            if marker in body:
                body = body.replace(marker, plan_link + marker, 1)
            body = enhance_dashboard(body, session, is_admin=is_admin, pending_count=pending_count)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION, "admin_center": True, "mobile_admin": True, "visible_logout": True, "signal_engine": "unchanged"})
                return
            if path == "/admin/center":
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                self._send(HTTPStatus.OK, admin_center_page(config, store, service, session, settings), "text/html; charset=utf-8")
                return
            if path == "/premium":
                session = self._session()
                if not session:
                    self._redirect("/register")
                    return
                body = billing.premium_page_v31(session, self._plan_info(session), store, settings, crypto_enabled)
                self._send(HTTPStatus.OK, enhance_standalone(body, session, is_admin=str(session.get("role") or "").upper() == commercial.ROLE_ADMIN), "text/html; charset=utf-8")
                return
            if path == "/account":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                body = billing.account_page_v31(session, self._plan_info(session), store)
                self._send(HTTPStatus.OK, enhance_standalone(body, session, is_admin=str(session.get("role") or "").upper() == commercial.ROLE_ADMIN), "text/html; charset=utf-8")
                return
            if path == "/admin/memberships":
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=4)
                message = (query.get("message") or [""])[0]
                error = (query.get("error") or [""])[0]
                try:
                    body = billing.admin_billing_page(store, session, settings, message=message, error=error)
                except accounts.AccountStoreError as exc:
                    body = billing.admin_billing_page(store, session, settings, error=str(exc))
                self._send(HTTPStatus.OK, enhance_standalone(body, session, is_admin=True), "text/html; charset=utf-8")
                return
            if path == "/admin/users":
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=4)
                message = (query.get("message") or [None])[0]
                error = (query.get("error") or [None])[0]
                body = accounts.admin_users_page(config, store, session, message=message, error=error)
                self._send(HTTPStatus.OK, enhance_standalone(body, session, is_admin=True), "text/html; charset=utf-8")
                return
            return super().do_GET()

    return V32Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.2 admin merkezi ve görünür çıkış.")
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
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v32_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} admin_center=on mobile_admin=on visible_logout=on signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
