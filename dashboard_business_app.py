"""Kripto Kontrol Merkezi V3.6 - Premium satış ve işletme kalite katmanı.

V3.5 ürün kalitesi/runtime katmanını koruyarak yalnız panel/ürün yönetimini geliştirir:
- Premium sayfasını profesyonel satış/karar ekranına dönüştürür,
- mevcut manuel ödeme bildirimi ve yönetici onay akışını aynen korur,
- ürünün canlı durumunu yalnız güvenli özet metriklerle gösterir,
- yönetim merkezine müşteri dönüşümü, yeni kullanıcı, süresi yaklaşan Premium ve ödeme önceliği ekler,
- mobil Premium CTA ve yönetim dikkat kuyruğu ekler.

Sinyal üretimi, strategy/config, radarlar, Telegram ve emir akışı değişmez.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
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
import dashboard_market_app as market
import dashboard_quality_runtime_app as runtime
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service, env_bool

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_6_BUSINESS_QUALITY_2026_08_15"


def _safe_rows(value: Any) -> list[dict[str, Any]]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def build_product_proof(data: dict[str, Any]) -> dict[str, Any]:
    """Premium karar ekranı için işlem seviyesi içermeyen güvenli canlı özet üretir."""
    open_rows = _safe_rows(data.get("open_trades"))
    result_rows = _safe_rows(data.get("recent_results"))
    health = data.get("health") if isinstance(data.get("health"), dict) else {}
    quality = data.get("data_quality") if isinstance(data.get("data_quality"), dict) else {}
    overall = str(health.get("overall") or ("GREEN" if quality.get("ok") else "UNKNOWN")).upper()

    outcomes = [str(row.get("outcome") or row.get("result") or "").upper() for row in result_rows]
    tp = sum(1 for value in outcomes if value.startswith("TP") and "BE" not in value)
    sl = sum(1 for value in outcomes if value == "SL" or value.startswith("SL_"))
    be = sum(1 for value in outcomes if "BE" in value)
    closed = tp + sl + be
    tp_rate = round(tp * 100.0 / closed, 1) if closed else None
    return {
        "health": overall,
        "open_count": len(open_rows),
        "recent_count": len(result_rows),
        "tp_count": tp,
        "sl_count": sl,
        "be_count": be,
        "tp_rate_percent": tp_rate,
        "updated_at": int(time.time()),
    }


def _stamp(row: dict[str, Any], key: str) -> int:
    try:
        return max(0, int(float(row.get(key) or 0)))
    except (TypeError, ValueError):
        return 0


def build_business_metrics(
    store: commercial.CommercialAccountStore,
    *,
    now: int | None = None,
) -> dict[str, Any]:
    """Yönetim merkezine gelir uydurmadan operasyonel üyelik metrikleri üretir."""
    now = int(now or time.time())
    try:
        users = list(store.list_commercial_users())
    except accounts.AccountStoreError:
        users = []
    try:
        payments = list(store.list_payments())
    except accounts.AccountStoreError:
        payments = []

    customers = [row for row in users if str(row.get("role") or "").upper() != commercial.ROLE_ADMIN]
    active_customers = [row for row in customers if bool(row.get("active", True))]
    premium_rows = [row for row in active_customers if str(row.get("plan") or "").upper() == commercial.PLAN_PREMIUM]
    free_rows = [row for row in active_customers if str(row.get("plan") or "").upper() == commercial.PLAN_FREE]
    denominator = len(premium_rows) + len(free_rows)
    conversion = round(len(premium_rows) * 100.0 / denominator, 1) if denominator else 0.0

    seven_days = 7 * 24 * 3600
    future_limit = now + seven_days
    expiring = []
    for row in premium_rows:
        expiry = _stamp(row, "expires_at")
        if now < expiry <= future_limit:
            expiring.append(row)
    expiring.sort(key=lambda row: _stamp(row, "expires_at"))

    new_users = [row for row in customers if now - seven_days <= _stamp(row, "created_at") <= now]
    pending = [row for row in payments if str(row.get("status") or "").upper() == commercial.PAYMENT_PENDING]
    approved_7d = [
        row for row in payments
        if str(row.get("status") or "").upper() == commercial.PAYMENT_APPROVED
        and now - seven_days <= (_stamp(row, "decided_at") or _stamp(row, "created_at")) <= now
    ]
    rejected_7d = [
        row for row in payments
        if str(row.get("status") or "").upper() == commercial.PAYMENT_REJECTED
        and now - seven_days <= (_stamp(row, "decided_at") or _stamp(row, "created_at")) <= now
    ]
    pending_created = [_stamp(row, "created_at") for row in pending if _stamp(row, "created_at")]
    oldest_pending_hours = round((now - min(pending_created)) / 3600.0, 1) if pending_created else 0.0

    return {
        "customers": len(customers),
        "active_customers": len(active_customers),
        "premium": len(premium_rows),
        "free": len(free_rows),
        "conversion_percent": conversion,
        "new_users_7d": len(new_users),
        "expiring_7d": len(expiring),
        "expiring_users": [str(row.get("username") or "") for row in expiring[:6]],
        "pending_payments": len(pending),
        "oldest_pending_hours": oldest_pending_hours,
        "approved_7d": len(approved_7d),
        "rejected_7d": len(rejected_7d),
    }


def enhance_premium_sales(
    body: str,
    session: dict[str, Any],
    info: dict[str, Any],
    settings: dict[str, Any],
    proof: dict[str, Any],
) -> str:
    if 'id="v36PremiumSales"' in body:
        return body

    plan = str(info.get("plan") or commercial.PLAN_FREE)
    is_premium = plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}
    health = html.escape(str(proof.get("health") or "UNKNOWN"))
    open_count = int(proof.get("open_count") or 0)
    recent_count = int(proof.get("recent_count") or 0)
    tp_rate = proof.get("tp_rate_percent")
    tp_rate_text = "—" if tp_rate is None else f"%{float(tp_rate):.1f}"
    package_name = html.escape(str(settings.get("package_name") or "Premium"))
    price = html.escape(str(settings.get("price_label") or "—"))
    days = max(1, int(settings.get("days") or 30))

    css = r'''
.v36-hero{margin-top:16px;border:1px solid rgba(44,230,191,.28);border-radius:20px;padding:22px;background:linear-gradient(135deg,rgba(44,230,191,.075),rgba(96,165,250,.035)),#0b1b23}.v36-kicker{display:inline-flex;border:1px solid rgba(44,230,191,.28);border-radius:999px;padding:5px 8px;color:var(--teal);font-size:9px;font-weight:950}.v36-hero-grid{display:grid;grid-template-columns:1.15fr .85fr;gap:18px;align-items:center}.v36-hero h1{font-size:30px;line-height:1.08;margin:10px 0 8px;letter-spacing:-.035em}.v36-hero p{margin:0;color:var(--muted)}.v36-offer{border:1px solid var(--line);border-radius:15px;background:#07151c;padding:16px}.v36-offer small{color:var(--muted)}.v36-offer strong{display:block;font-size:18px;margin-top:3px}.v36-offer .price{font-size:24px;color:var(--teal);font-weight:950;margin-top:7px}.v36-offer .btnlink{display:block;margin-top:12px;border-radius:10px;padding:10px 12px;background:var(--teal);color:#03110e;text-align:center;font-weight:950;text-decoration:none}.v36-proof{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:12px}.v36-proof>div{border:1px solid var(--line);border-radius:12px;padding:11px;background:#08171e}.v36-proof small{display:block;color:var(--muted);font-size:8px;text-transform:uppercase}.v36-proof b{display:block;margin-top:3px;font-size:17px}.v36-proof .good{color:var(--green)}.v36-benefits{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:12px}.v36-benefit{border:1px solid var(--line);border-radius:13px;padding:13px;background:#08171e}.v36-benefit b{display:block;font-size:11px}.v36-benefit span{display:block;color:var(--muted);font-size:10px;margin-top:4px}.v36-flow{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}.v36-step{border:1px solid var(--line);border-radius:13px;padding:13px}.v36-step i{display:grid;place-items:center;width:25px;height:25px;border-radius:8px;background:rgba(44,230,191,.08);color:var(--teal);font-style:normal;font-weight:950}.v36-step b{display:block;margin-top:7px}.v36-step span{display:block;color:var(--muted);font-size:10px;margin-top:3px}.v36-trust{margin-top:12px;border-left:2px solid var(--teal);padding:4px 0 4px 11px;color:var(--muted);font-size:10px}.v36-mobile{display:none}@media(max-width:760px){.v36-hero-grid{grid-template-columns:1fr}.v36-proof{grid-template-columns:1fr 1fr}.v36-benefits,.v36-flow{grid-template-columns:1fr}.v36-mobile{display:flex;position:fixed;left:8px;right:8px;bottom:8px;z-index:990;border:1px solid var(--line);border-radius:13px;padding:7px;background:rgba(6,16,22,.95);box-shadow:0 12px 35px rgba(0,0,0,.4)}.v36-mobile a{width:100%;text-align:center;border-radius:9px;background:var(--teal);color:#03110e;padding:10px;font-weight:950;text-decoration:none}.shell{padding-bottom:92px}}
'''
    if "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)

    hero = f'''
<section class="v36-hero" id="v36PremiumSales"><div class="v36-hero-grid"><div><span class="v36-kicker">PREMIUM · ANALİZ VE TAKİP</span><h1>Tüm sistemi tek ekranda kullan.</h1><p>FREE deneyimden sonra ihtiyacın varsa tüm açık sinyaller, TP2/TP3, Fırsat Merkezi, teknik skorlar, sesli uyarılar ve ayrıntılı takip araçlarını aç.</p><div class="v36-trust">Otomatik emir açılmaz · kullanıcı parası tutulmaz · kazanç garantisi verilmez.</div></div><div class="v36-offer"><small>Aktif paket</small><strong>{package_name}</strong><div class="price">{price}</div><small>{days} gün Premium erişim</small><a class="btnlink" href="#payment">{'Üyeliğim aktif' if is_premium else 'Ödeme / üyelik adımına geç'}</a></div></div>
<div class="v36-proof"><div><small>Sistem durumu</small><b class="good">{health}</b></div><div><small>Açık işlem</small><b>{open_count}</b></div><div><small>Son sonuç kaydı</small><b>{recent_count}</b></div><div><small>Son listedeki TP oranı</small><b>{tp_rate_text}</b></div></div></section>
'''
    marker = '<div class="card"><h1>Premium üyelik</h1>'
    if marker in body:
        body = body.replace(marker, hero + marker, 1)
    else:
        body = body.replace('<div class="shell">', '<div class="shell">' + hero, 1)

    benefits = '''
<div class="card"><h2>Premium ile açılan çalışma alanı</h2><div class="v36-benefits"><div class="v36-benefit"><b>⚡ Tüm açık sinyaller</b><span>FREE tek işlem sınırı kalkar; sistemdeki gerçek açık işlemler görünür.</span></div><div class="v36-benefit"><b>◎ Tam hedef yapısı</b><span>Entry, TP1, TP2, TP3 ve SL seviyelerini tek ekranda takip et.</span></div><div class="v36-benefit"><b>◈ Fırsat Merkezi</b><span>Canlı piyasa grupları, filtreler ve teknik inceleme skorları.</span></div><div class="v36-benefit"><b>🔔 Canlı uyarılar</b><span>Yeni sinyal ve sonuçları renkli/sesli panel uyarılarıyla takip et.</span></div><div class="v36-benefit"><b>★ İzleme Listesi</b><span>Takip ettiğin coinleri ve hızlı analizlerini tek yerde tut.</span></div><div class="v36-benefit"><b>▦ Ayrıntılı performans</b><span>İşlemler, sonuçlar ve sistem görünümünü daha geniş veriyle incele.</span></div></div></div>
<div class="card"><h2>Nasıl Premium olunur?</h2><div class="v36-flow"><div class="v36-step"><i>1</i><b>Paket bilgisini kontrol et</b><span>Fiyat ve süre bu sayfada güncel ayarlardan gelir.</span></div><div class="v36-step"><i>2</i><b>Ödemeyi tamamla</b><span>Aşağıdaki mevcut ödeme talimatını kullan ve bildirimi gönder.</span></div><div class="v36-step"><i>3</i><b>Yönetici onayı</b><span>Onay sonrası Premium süre hesabına tanımlanır; durumunu buradan izlersin.</span></div></div></div>
'''
    pay_marker = '<div class="card"><h2>Ödeme / üyelik işlemi</h2>'
    if pay_marker in body:
        body = body.replace(pay_marker, benefits + '<div class="card" id="payment"><h2>Ödeme / üyelik işlemi</h2>', 1)
    elif 'id="payment"' not in body:
        body = body.replace("</div></body>", benefits + "</div></body>", 1)

    mobile = '<div class="v36-mobile"><a href="#payment">Premium üyelik adımına geç</a></div>'
    return body.replace("</body>", mobile + "</body>", 1)


def enhance_admin_business(body: str, metrics: dict[str, Any]) -> str:
    if 'id="v36BusinessOps"' in body:
        return body

    conversion = float(metrics.get("conversion_percent") or 0.0)
    names = [html.escape(name) for name in metrics.get("expiring_users") or [] if name]
    expiring_text = ", ".join(names) if names else "Yakında bitecek Premium üyelik yok."
    oldest = float(metrics.get("oldest_pending_hours") or 0.0)

    css = r'''
.v36-business{border:1px solid rgba(96,165,250,.22);border-radius:16px;background:linear-gradient(135deg,rgba(96,165,250,.045),rgba(44,230,191,.025)),var(--panel);padding:15px;margin:0 0 14px}.v36-business-head{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;flex-wrap:wrap}.v36-business-head h2{margin:0}.v36-business-head p{margin:3px 0 0;color:var(--muted)}.v36-biz-grid{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin-top:12px}.v36-biz-kpi{border:1px solid var(--line);border-radius:11px;padding:10px;background:#08171e}.v36-biz-kpi small{display:block;color:var(--muted);font-size:8px;text-transform:uppercase}.v36-biz-kpi b{display:block;font-size:19px;margin-top:3px}.v36-biz-kpi.teal b{color:var(--teal)}.v36-biz-kpi.amber b{color:var(--amber)}.v36-attention{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin-top:10px}.v36-attn{border:1px solid var(--line);border-radius:11px;padding:11px}.v36-attn b{display:block}.v36-attn span{display:block;color:var(--muted);font-size:10px;margin-top:3px}.v36-attn a{display:inline-block;margin-top:7px;color:var(--teal);font-weight:850;font-size:10px}@media(max-width:980px){.v36-biz-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:600px){.v36-biz-grid{grid-template-columns:1fr 1fr}.v36-attention{grid-template-columns:1fr}}
'''
    if "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)

    section = f'''
<section class="v36-business" id="v36BusinessOps"><div class="v36-business-head"><div><h2>İşletme özeti</h2><p>Satış tutarı varsaymadan üyelik dönüşümü ve operasyon öncelikleri.</p></div><a class="btn primary" href="/admin/memberships">Üyelik & ödeme yönetimi</a></div><div class="v36-biz-grid"><div class="v36-biz-kpi"><small>Müşteri hesabı</small><b>{int(metrics.get('customers') or 0)}</b></div><div class="v36-biz-kpi teal"><small>Premium dönüşüm</small><b>%{conversion:.1f}</b></div><div class="v36-biz-kpi"><small>Yeni kullanıcı · 7g</small><b>{int(metrics.get('new_users_7d') or 0)}</b></div><div class="v36-biz-kpi amber"><small>Bekleyen ödeme</small><b>{int(metrics.get('pending_payments') or 0)}</b></div><div class="v36-biz-kpi"><small>Bitecek Premium · 7g</small><b>{int(metrics.get('expiring_7d') or 0)}</b></div><div class="v36-biz-kpi"><small>Onaylanan · 7g</small><b>{int(metrics.get('approved_7d') or 0)}</b></div></div><div class="v36-attention"><div class="v36-attn"><b>Ödeme önceliği</b><span>{int(metrics.get('pending_payments') or 0)} bildirim bekliyor · en eski bekleme {oldest:.1f} saat.</span><a href="/admin/memberships">Bekleyenleri incele →</a></div><div class="v36-attn"><b>Yaklaşan üyelik bitişleri</b><span>{expiring_text}</span><a href="/admin/memberships">Üyelik planlarını aç →</a></div></div></section>
'''
    marker = '<div class="kpis">'
    if marker in body:
        body = body.replace(marker, section + marker, 1)
    else:
        body = body.replace('<div class="shell">', '<div class="shell">' + section, 1)
    return body


def make_v36_handler(
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
    BaseHandler = runtime.make_runtime_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V36Handler(BaseHandler):
        server_version = "KriptoPanel/3.6"

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "premium_sales": True,
                    "business_ops": True,
                    "payment_flow": "manual_admin_approval",
                    "signal_engine": "unchanged",
                })
                return
            if path == "/premium":
                session = self._session()
                if not session:
                    self._redirect("/register")
                    return
                info = self._plan_info(session)
                try:
                    proof = build_product_proof(service.get_data())
                except Exception:
                    proof = build_product_proof({})
                body = billing.premium_page_v31(session, info, store, settings, crypto_enabled)
                is_admin = str(session.get("role") or "").upper() == commercial.ROLE_ADMIN
                body = adminux.enhance_standalone(body, session, is_admin=is_admin)
                body = enhance_premium_sales(body, session, info, settings, proof)
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            if path == "/admin/center":
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                body = adminux.admin_center_page(config, store, service, session, settings)
                body = enhance_admin_business(body, build_business_metrics(store))
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            return super().do_GET()

    return V36Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.6 business quality.")
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
    handler = make_v36_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} premium_sales=1 business_ops=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
