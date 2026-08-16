"""Kripto Kontrol Merkezi V3.8 - yenileme ve müşteri tutundurma katmanı.

V3.7 üzerine yalnız ürün/üyelik deneyiminde eklenir:
- Premium bitişine 7 / 3 / 1 gün kala görünür yenileme uyarısı,
- süresi biten hesabın güvenli biçimde FREE devam ettiğini açıklayan ekran,
- aktif Premium'un son 7 gününde mevcut ödeme bildirimi üzerinden yenileme akışı,
- bekleyen ödeme varsa yinelenen bildirim/form engeli,
- admin için yenileme kuyruğu ve yönetim merkezi kısayolu.

Sinyal üretimi, strategy/config, radarlar, Telegram ve emir akışı değişmez.
"""

from __future__ import annotations

import argparse
import html
import math
import os
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_adminux_app as adminux
import dashboard_billing_app as billing
import dashboard_commercial_app as commercial
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service, env_bool

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_8_RETENTION_2026_08_15"
DAY = 86_400


def _stamp(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def renewal_state(info: dict[str, Any] | None, *, now: int | None = None) -> dict[str, Any]:
    """Kullanıcının yenileme durumunu planı değiştirmeden hesaplar."""
    now = int(now or time.time())
    info = info if isinstance(info, dict) else {}
    plan = str(info.get("plan") or commercial.PLAN_FREE).upper()
    role = str(info.get("role") or "").upper()
    expiry = _stamp(info.get("expires_at"))
    expired = bool(info.get("expired")) or bool(expiry and expiry <= now)

    if role == commercial.ROLE_ADMIN or plan == commercial.PLAN_ADMIN:
        return {"stage": "ADMIN", "show": False, "days": None, "expires_at": expiry or None, "expired": False}

    if expired:
        return {"stage": "EXPIRED", "show": True, "days": 0, "expires_at": expiry or None, "expired": True}

    if plan != commercial.PLAN_PREMIUM or not expiry:
        return {"stage": "FREE" if plan == commercial.PLAN_FREE else "ACTIVE", "show": False, "days": None, "expires_at": expiry or None, "expired": False}

    remaining = max(0, expiry - now)
    days = max(1, int(math.ceil(remaining / DAY)))
    if days <= 1:
        stage = "D1"
    elif days <= 3:
        stage = "D3"
    elif days <= 7:
        stage = "D7"
    else:
        stage = "ACTIVE"
    return {"stage": stage, "show": stage != "ACTIVE", "days": days, "expires_at": expiry, "expired": False}


def _pending_payment(store: commercial.CommercialAccountStore, username: str) -> dict[str, Any] | None:
    key = str(username or "").casefold()
    try:
        rows = store.list_payments()
    except accounts.AccountStoreError:
        return None
    pending = [
        row for row in rows
        if isinstance(row, dict)
        and str(row.get("username") or "").casefold() == key
        and str(row.get("status") or "").upper() == commercial.PAYMENT_PENDING
    ]
    pending.sort(key=lambda row: _stamp(row.get("created_at")), reverse=True)
    return pending[0] if pending else None


def _expiry_text(value: Any) -> str:
    stamp = _stamp(value)
    if not stamp:
        return "—"
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(stamp + 3 * 3600))


def _insert_css(body: str, css: str) -> str:
    if css in body:
        return body
    if "</style>" in body:
        return body.replace("</style>", css + "\n</style>", 1)
    return body


def _retention_banner(
    session: dict[str, Any],
    state: dict[str, Any],
    *,
    pending: bool,
) -> str:
    username = html.escape(str(session.get("username") or "üye"))
    stage = str(state.get("stage") or "")
    expiry = html.escape(_expiry_text(state.get("expires_at")))

    if stage == "EXPIRED":
        title = "Premium süren sona erdi"
        text = f"{username}, hesabın kapanmadı. FREE planla kullanmaya devam edebilirsin; Premium araçlarını tekrar açmak için üyeliğini yenile."
        badge = "FREE DEVAM"
        cls = "expired"
    else:
        days = int(state.get("days") or 0)
        title = "Premium üyeliğini kesintisiz sürdür"
        text = f"Premium erişiminin bitmesine {days} gün kaldı. Bitiş: {expiry}. Yenileme onaylanınca yeni süre mevcut bitiş tarihinin üzerine eklenir."
        badge = f"{days} GÜN KALDI"
        cls = "urgent" if days <= 1 else "warn" if days <= 3 else "notice"

    if pending:
        cta = '<a class="v38-cta pending" href="/premium">Ödeme bildirimi onay bekliyor</a>'
    else:
        cta = '<a class="v38-cta" href="/renew">Üyeliğimi Yenile</a>'

    return (
        f'<section class="v38-retention {cls}" id="v38RetentionBanner">'
        f'<div class="v38-copy"><span class="v38-badge">{html.escape(badge)}</span><div><b>{html.escape(title)}</b><p>{html.escape(text)}</p></div></div>'
        f'{cta}</section>'
    )


def enhance_retention_banner(
    body: str,
    session: dict[str, Any],
    info: dict[str, Any],
    store: commercial.CommercialAccountStore,
    *,
    now: int | None = None,
) -> str:
    if 'id="v38RetentionBanner"' in body:
        return body
    state = renewal_state(info, now=now)
    if not state.get("show"):
        return body
    pending = bool(_pending_payment(store, str(session.get("username") or "")))
    css = r'''
.v38-retention{width:min(1180px,calc(100% - 24px));margin:10px auto 4px;border:1px solid rgba(96,165,250,.28);border-radius:15px;background:linear-gradient(135deg,rgba(96,165,250,.08),rgba(44,230,191,.025)),#0a1921;padding:12px 14px;display:flex;align-items:center;justify-content:space-between;gap:12px;position:relative;z-index:80;box-shadow:0 10px 30px rgba(0,0,0,.16)}.v38-retention.warn{border-color:rgba(255,189,89,.38);background:linear-gradient(135deg,rgba(255,189,89,.08),rgba(255,189,89,.015)),#0a1921}.v38-retention.urgent,.v38-retention.expired{border-color:rgba(255,98,125,.38);background:linear-gradient(135deg,rgba(255,98,125,.08),rgba(255,98,125,.015)),#0a1921}.v38-copy{display:flex;align-items:flex-start;gap:10px;min-width:0}.v38-badge{flex:0 0 auto;border:1px solid currentColor;border-radius:999px;padding:4px 7px;color:#60a5fa;font-size:8px;font-weight:950;letter-spacing:.04em}.v38-warn .v38-badge,.v38-retention.warn .v38-badge{color:#ffbd59}.v38-retention.urgent .v38-badge,.v38-retention.expired .v38-badge{color:#ff748c}.v38-copy b{display:block;font-size:12px}.v38-copy p{margin:2px 0 0;color:#8fa8a5;font-size:10px}.v38-cta{flex:0 0 auto;border-radius:10px;padding:9px 12px;background:#2ce6bf;color:#03110e!important;text-decoration:none;font-size:10px;font-weight:950}.v38-cta.pending{background:#172a31;color:#ffbd59!important;border:1px solid rgba(255,189,89,.25)}@media(max-width:720px){.v38-retention{align-items:stretch;flex-direction:column;margin-top:7px}.v38-copy{flex-direction:column;gap:6px}.v38-cta{text-align:center;width:100%}}
'''
    body = _insert_css(body, css)
    banner = _retention_banner(session, state, pending=pending)
    if "<body>" in body:
        return body.replace("<body>", "<body>" + banner, 1)
    return banner + body


def enhance_premium_renewal(
    body: str,
    session: dict[str, Any],
    info: dict[str, Any],
    store: commercial.CommercialAccountStore,
    settings: dict[str, Any],
    crypto_enabled: bool,
    *,
    now: int | None = None,
) -> str:
    if 'id="v38RenewalCard"' in body:
        return body
    state = renewal_state(info, now=now)
    if str(state.get("stage")) not in {"D1", "D3", "D7"}:
        return body

    username = str(session.get("username") or "")
    pending = _pending_payment(store, username)
    days = int(state.get("days") or 0)
    expiry = html.escape(_expiry_text(state.get("expires_at")))
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    package_code = html.escape(str(settings.get("package_code") or "PREMIUM_30D"), quote=True)
    package_name = html.escape(str(settings.get("package_name") or "Premium"))
    package_days = max(1, int(settings.get("days") or 30))
    price = html.escape(str(settings.get("price_label") or "—"))

    if pending:
        inner = (
            '<div class="v38-renew-wait"><b>Yenileme bildirimin alındı.</b>'
            '<span>Yönetici onayladığında yeni Premium süren mevcut bitiş tarihinin üzerine eklenecek. İkinci bildirim gönderemezsin.</span></div>'
        )
    else:
        crypto_option = '<option value="CRYPTO">Kripto ödeme bildirimi</option>' if crypto_enabled else ""
        inner = f'''
<form method="post" action="/payment/notify" class="v38-renew-form">
<input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="package" value="{package_code}">
<div class="v38-renew-grid"><div><label>Yenileme yöntemi</label><select name="method"><option value="BANK_TRANSFER">Banka / FAST / Havale</option>{crypto_option}</select></div><div><label>Not (isteğe bağlı)</label><input name="note" maxlength="180" placeholder="Gönderen adı / kısa açıklama"></div></div>
<button type="submit">Yenileme ödemesi yaptım · Onaya gönder</button></form>'''

    card = f'''
<div class="card v38-renew-card" id="v38RenewalCard"><div class="v38-renew-head"><div><small>YENİLEME PENCERESİ</small><h2>Premium'u kesintisiz sürdür</h2><p>Mevcut bitiş: {expiry} · {days} gün kaldı</p></div><div class="v38-renew-package"><b>{package_name}</b><span>+{package_days} gün</span><strong>{price}</strong></div></div>{inner}<p class="v38-renew-note">Onaylanan yenileme mevcut Premium bitiş tarihinin üzerine eklenir; kalan günlerin yanmaz.</p></div>
'''
    css = r'''
.v38-renew-card{border-color:rgba(44,230,191,.32)!important}.v38-renew-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.v38-renew-head small{color:#2ce6bf;font-size:8px;font-weight:950;letter-spacing:.08em}.v38-renew-head h2{margin:3px 0}.v38-renew-head p{margin:0;color:#82a09d}.v38-renew-package{text-align:right;border:1px solid #1b3943;border-radius:11px;padding:9px 11px;min-width:135px}.v38-renew-package b,.v38-renew-package span,.v38-renew-package strong{display:block}.v38-renew-package span{color:#82a09d;font-size:9px}.v38-renew-package strong{color:#2ce6bf;margin-top:3px}.v38-renew-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.v38-renew-wait{border:1px solid rgba(255,189,89,.32);border-radius:11px;padding:12px;background:rgba(255,189,89,.05);display:grid;gap:3px}.v38-renew-wait span,.v38-renew-note{color:#82a09d;font-size:10px}.v38-renew-form button{background:#2ce6bf!important;color:#03110e!important}.v38-renew-note{margin-bottom:0}@media(max-width:650px){.v38-renew-head{flex-direction:column}.v38-renew-package{text-align:left;width:100%}.v38-renew-grid{grid-template-columns:1fr}}
'''
    body = _insert_css(body, css)
    marker = '<div class="card"><h2>Ödeme geçmişim</h2>'
    if marker in body:
        return body.replace(marker, card + marker, 1)
    if "</div></body>" in body:
        return body.replace("</div></body>", card + "</div></body>", 1)
    return body + card


def renewal_queue_rows(store: commercial.CommercialAccountStore, *, now: int | None = None) -> list[dict[str, Any]]:
    now = int(now or time.time())
    rows = lifecycle.build_lifecycle_rows(store, now=now)
    selected = [row for row in rows if row.get("pending_payment") or row.get("expired") or row.get("expiring7")]

    def priority(row: dict[str, Any]) -> tuple[int, int, str]:
        if row.get("pending_payment"):
            p = 0
        elif row.get("expired"):
            p = 1
        else:
            days = int(row.get("days_remaining") or 99)
            p = 2 if days <= 1 else 3 if days <= 3 else 4
        return (p, _stamp(row.get("expires_at")) or 9_999_999_999, str(row.get("username") or "").casefold())

    selected.sort(key=priority)
    return selected


def renewal_queue_summary(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "pending": sum(1 for row in rows if row.get("pending_payment")),
        "expired": sum(1 for row in rows if row.get("expired")),
        "d1": sum(1 for row in rows if row.get("premium") and int(row.get("days_remaining") or 99) <= 1),
        "d3": sum(1 for row in rows if row.get("premium") and int(row.get("days_remaining") or 99) <= 3),
        "d7": sum(1 for row in rows if row.get("premium") and int(row.get("days_remaining") or 99) <= 7),
    }


def admin_renewal_page(store: commercial.CommercialAccountStore, session: dict[str, Any]) -> str:
    rows = renewal_queue_rows(store)[:200]
    summary = renewal_queue_summary(rows)
    cards: list[str] = []
    for row in rows:
        username = html.escape(str(row.get("username") or ""))
        if row.get("pending_payment"):
            status = "ÖDEME ONAYI BEKLİYOR"
            cls = "pending"
            action = '<a class="action primary" href="/admin/memberships">Ödemeyi incele</a>'
        elif row.get("expired"):
            status = "PREMIUM SÜRESİ BİTTİ"
            cls = "expired"
            action = '<a class="action" href="/admin/lifecycle?segment=expired">Üyeliği yönet</a>'
        else:
            days = int(row.get("days_remaining") or 0)
            status = f"{days} GÜN KALDI"
            cls = "urgent" if days <= 1 else "warn"
            action = '<a class="action" href="/admin/lifecycle?segment=expiring7">Üyeliği yönet</a>'
        cards.append(
            f'<div class="customer"><div><b>{username}</b><span>{html.escape(str(row.get("plan") or "FREE"))} · Bitiş: {html.escape(_expiry_text(row.get("expires_at")))}</span></div>'
            f'<strong class="status {cls}">{html.escape(status)}</strong>{action}</div>'
        )
    if not cards:
        cards.append('<div class="empty">Şu anda yenileme aksiyonu bekleyen kullanıcı yok.</div>')

    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Yenileme Merkezi</title><style>
:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--amber:#ffbd59;--red:#ff627d;--blue:#60a5fa}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,rgba(44,230,191,.07),transparent 28%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.shell{{width:min(1050px,calc(100% - 20px));margin:auto;padding:22px 0 55px}}.top{{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}}.top h1{{margin:0;font-size:29px}}.top p{{color:var(--muted);margin:4px 0}}.btn,.action{{display:inline-block;border:1px solid var(--line);border-radius:9px;padding:8px 10px;background:#091820;font-size:9px;font-weight:900}}.primary{{color:var(--teal)!important;border-color:rgba(44,230,191,.3)!important}}.kpis{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:16px 0}}.kpi{{border:1px solid var(--line);border-radius:12px;background:var(--panel);padding:12px}}.kpi small{{display:block;color:var(--muted);font-size:8px}}.kpi b{{display:block;font-size:22px;margin-top:2px}}.kpi.red b{{color:var(--red)}}.kpi.amber b{{color:var(--amber)}}.list{{display:grid;gap:8px}}.customer{{display:grid;grid-template-columns:1fr 190px 130px;gap:10px;align-items:center;border:1px solid var(--line);border-radius:13px;background:var(--panel);padding:12px}}.customer b,.customer span{{display:block}}.customer span{{color:var(--muted);font-size:9px;margin-top:2px}}.status{{font-size:9px}}.status.pending,.status.warn{{color:var(--amber)}}.status.expired,.status.urgent{{color:var(--red)}}.empty{{border:1px dashed var(--line);border-radius:13px;color:var(--muted);padding:25px;text-align:center}}.foot{{color:var(--muted);font-size:9px;margin-top:12px}}@media(max-width:720px){{.kpis{{grid-template-columns:1fr 1fr}}.customer{{grid-template-columns:1fr}}.status{{margin-top:3px}}}}
</style></head><body><div class="shell"><div class="top"><div><h1>Yenileme Merkezi</h1><p>Premium süre sonu ve ödeme aksiyonlarını öncelik sırasıyla takip et.</p></div><div><a class="btn" href="/admin/lifecycle">Yaşam Döngüsü</a> <a class="btn primary" href="/admin/center">← Yönetim</a></div></div><div class="kpis"><div class="kpi"><small>Yenileme kuyruğu</small><b>{summary['total']}</b></div><div class="kpi amber"><small>Ödeme bekliyor</small><b>{summary['pending']}</b></div><div class="kpi red"><small>Süresi bitti</small><b>{summary['expired']}</b></div><div class="kpi red"><small>≤1 gün</small><b>{summary['d1']}</b></div><div class="kpi amber"><small>≤7 gün</small><b>{summary['d7']}</b></div></div><div class="list">{''.join(cards)}</div><div class="foot">Öncelik: ödeme bekleyen → süresi biten → ≤1 gün → ≤3 gün → ≤7 gün. Hızlı süre işlemleri Müşteri Yaşam Döngüsü ekranından yapılır.</div></div></body></html>'''


def enhance_admin_center_renewals(body: str, summary: dict[str, int]) -> str:
    if 'id="v38RenewalShortcut"' in body:
        return body
    link = (
        f'<a id="v38RenewalShortcut" href="/admin/renewals"><b>⟳ Yenileme Merkezi</b>'
        f'<span>{int(summary.get("total") or 0)} yenileme aksiyonu · {int(summary.get("pending") or 0)} ödeme · {int(summary.get("d1") or 0)} kritik</span></a>'
    )
    marker = '<div class="quick">'
    if marker in body:
        return body.replace(marker, marker + link, 1)
    return body


def make_v38_handler(
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
    BaseHandler = lifecycle.make_v37_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V38Handler(BaseHandler):
        server_version = "KriptoPanel/3.8"

        def _send(self, status: int, body: str | bytes, content_type: str, *, cookies: list[str] | None = None, nonce: str | None = None) -> None:
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path in {"/", "/premium", "/account"}:
                    session = self._session()
                    if session:
                        info = self._plan_info(session) or {}
                        body = enhance_retention_banner(body, session, info, store)
                        if path == "/premium":
                            body = enhance_premium_renewal(body, session, info, store, settings, crypto_enabled)
                elif path == "/admin/center":
                    try:
                        summary = renewal_queue_summary(renewal_queue_rows(store))
                        body = enhance_admin_center_renewals(body, summary)
                    except Exception:
                        pass
            super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION, "renewal_alerts": True, "renewal_window_days": [7, 3, 1], "expired_to_free": True, "admin_renewal_queue": True, "signal_engine": "unchanged"})
                return
            if path == "/renew":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                info = self._plan_info(session) or {}
                if str(info.get("plan") or "").upper() == commercial.PLAN_ADMIN:
                    self._redirect("/admin/center")
                    return
                pending = _pending_payment(store, str(session.get("username") or ""))
                if pending:
                    self._redirect("/premium#payment")
                    return
                state = renewal_state(info)
                if str(state.get("stage")) in {"D1", "D3", "D7"}:
                    self._redirect("/premium?renew=1#v38RenewalCard")
                else:
                    self._redirect("/premium#payment")
                return
            if path == "/admin/renewals":
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                body = admin_renewal_page(store, session)
                body = adminux.enhance_standalone(body, session, is_admin=True)
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            return super().do_GET()

    return V38Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.8 yenileme ve müşteri tutundurma.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = lifecycle.lifecycle_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v38_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} renewal_alerts=7,3,1 expired_to_free=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
