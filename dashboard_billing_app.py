"""Kripto Kontrol Merkezi V3.1 - profesyonel üyelik ve ödeme durum merkezi.

V3.0 ticari üyelik katmanını değiştirmeden genişletir:
- Premium paket adı / süre / görünen fiyat etiketi ortam ayarlarından yönetilir,
- kullanıcı son ödeme bildiriminin Bekliyor / Onaylandı / Reddedildi durumunu görür,
- onay bekleyen bildirim varken ikinci bildirim formu gösterilmez,
- admin panelinde ödeme sayaçları ve ödeme geçmişi görünür,
- ana yönetici panelinde bekleyen ödeme sayısı rozet olarak gösterilir.

Ödeme tahsilatı yapmaz. Sinyal, strateji, radar, Telegram ve emir akışına dokunmaz.
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
import dashboard_commercial_app as commercial
import dashboard_market_app as market
import dashboard_memory_app as memory
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service, env_bool

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_1_BILLING_2026_08_14"


def _settings() -> dict[str, Any]:
    days = max(1, min(commercial._int(os.getenv("PANEL_PREMIUM_DAYS"), 30), 3650))
    package_name = commercial._safe_text(
        os.getenv("PANEL_PREMIUM_PACKAGE_NAME") or f"Premium {days} Gün",
        80,
    )
    price_label = commercial._safe_text(
        os.getenv("PANEL_PREMIUM_PRICE_LABEL") or "Fiyat için yöneticiyle iletişime geçin",
        80,
    )
    package_code = commercial._safe_text(
        os.getenv("PANEL_PREMIUM_PACKAGE_CODE") or f"PREMIUM_{days}D",
        60,
    )
    instructions = commercial._safe_text(
        os.getenv("PANEL_PAYMENT_INSTRUCTIONS")
        or "Ödeme bilgisini yöneticiden alın. Ödeme tamamlandıktan sonra 'Ödeme yaptım' bildirimi bırakın.",
        1000,
    )
    return {
        "days": days,
        "package_name": package_name,
        "price_label": price_label,
        "package_code": package_code,
        "instructions": instructions,
    }


def _status_label(value: Any) -> str:
    status = str(value or "").upper()
    return {
        commercial.PAYMENT_PENDING: "Onay bekliyor",
        commercial.PAYMENT_APPROVED: "Onaylandı",
        commercial.PAYMENT_REJECTED: "Reddedildi",
    }.get(status, "Bilinmiyor")


def _status_class(value: Any) -> str:
    status = str(value or "").upper()
    return {
        commercial.PAYMENT_PENDING: "pending",
        commercial.PAYMENT_APPROVED: "approved",
        commercial.PAYMENT_REJECTED: "rejected",
    }.get(status, "")


def _tr_time(value: Any) -> str:
    stamp = commercial._int(value, 0)
    if not stamp:
        return "—"
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(stamp + 3 * 3600))


def user_payments(store: commercial.CommercialAccountStore, username: str, limit: int = 8) -> list[dict[str, Any]]:
    key = str(username or "").casefold()
    return [
        row for row in store.list_payments()
        if str(row.get("username") or "").casefold() == key
    ][:max(1, min(int(limit), 20))]


def payment_counts(store: commercial.CommercialAccountStore) -> dict[str, int]:
    counts = {"total": 0, "pending": 0, "approved": 0, "rejected": 0}
    for row in store.list_payments():
        counts["total"] += 1
        status = str(row.get("status") or "").upper()
        if status == commercial.PAYMENT_PENDING:
            counts["pending"] += 1
        elif status == commercial.PAYMENT_APPROVED:
            counts["approved"] += 1
        elif status == commercial.PAYMENT_REJECTED:
            counts["rejected"] += 1
    return counts


def _latest_payment_card(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="payment-empty">Henüz ödeme bildiriminiz yok.</div>'
    row = rows[0]
    status = str(row.get("status") or "").upper()
    note = html.escape(str(row.get("note") or ""))
    decision = ""
    if status in {commercial.PAYMENT_APPROVED, commercial.PAYMENT_REJECTED}:
        decision = f'<small>Sonuç: {_tr_time(row.get("decided_at"))}</small>'
    note_html = f'<small>Not: {note}</small>' if note else ""
    return (
        f'<div class="payment-state {_status_class(status)}">'
        f'<div><span class="state-dot"></span><strong>{html.escape(_status_label(status))}</strong></div>'
        f'<b>{html.escape(str(row.get("package") or "Premium"))}</b>'
        f'<small>Bildirim: {_tr_time(row.get("created_at"))}</small>{decision}{note_html}'
        f'</div>'
    )


def premium_page_v31(
    session: dict[str, Any],
    info: dict[str, Any],
    store: commercial.CommercialAccountStore,
    settings: dict[str, Any],
    crypto_enabled: bool,
) -> str:
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    username_raw = str(session.get("username") or "üye")
    username = html.escape(username_raw)
    plan = str(info.get("plan") or commercial.PLAN_FREE)
    expiry = commercial._format_expiry(info.get("expires_at"))
    try:
        rows = user_payments(store, username_raw)
    except accounts.AccountStoreError:
        rows = []
    latest = rows[0] if rows else None
    pending = bool(latest and str(latest.get("status") or "").upper() == commercial.PAYMENT_PENDING)

    if plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}:
        action = (
            f'<div class="active-plan"><strong>{html.escape(commercial._plan_label(plan))} aktif</strong>'
            f'<span>Bitiş: {html.escape(expiry)}</span></div>'
        )
    elif pending:
        action = '<div class="wait-box"><strong>Ödeme bildirimin alındı.</strong><span>Yönetici onayı bekleniyor. Aynı ödeme için tekrar bildirim göndermene gerek yok.</span></div>'
    else:
        crypto_option = '<option value="CRYPTO">Kripto ödeme bildirimi</option>' if crypto_enabled else ""
        action = f"""
        <form method="post" action="/payment/notify" class="pay-form">
          <input type="hidden" name="csrf" value="{csrf}">
          <input type="hidden" name="package" value="{html.escape(str(settings['package_code']), quote=True)}">
          <label>Ödeme yöntemi</label>
          <select name="method"><option value="BANK_TRANSFER">Banka / FAST / Havale</option>{crypto_option}</select>
          <label>Not (isteğe bağlı)</label>
          <input name="note" maxlength="180" placeholder="Örn. gönderen adı veya kısa açıklama">
          <button type="submit">Ödeme yaptım · Onaya gönder</button>
        </form>"""

    crypto_note = (
        "Kripto ödeme bildirimi yönetici tarafından etkinleştirilmiştir."
        if crypto_enabled
        else "Kripto ödeme seçeneği şu anda kapalıdır."
    )
    history = "".join(
        f'<tr><td>{_tr_time(row.get("created_at"))}</td><td>{html.escape(str(row.get("package") or ""))}</td>'
        f'<td>{html.escape(str(row.get("method") or ""))}</td><td><span class="status {_status_class(row.get("status"))}">{html.escape(_status_label(row.get("status")))}</span></td></tr>'
        for row in rows[:5]
    ) or '<tr><td colspan="4" class="muted">Henüz ödeme kaydı yok.</td></tr>'

    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Premium Üyelik</title>
<style>
:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--amber:#ffbd59;--green:#42e28c;--red:#ff627d}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 85% 0,rgba(44,230,191,.09),transparent 30%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}.shell{{width:min(920px,calc(100% - 28px));margin:auto;padding:28px 0 55px}}a{{color:var(--teal);font-weight:850;text-decoration:none}}.card{{border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:22px;margin-top:16px}}h1,h2{{margin-top:0}}p,.muted{{color:var(--muted)}}.package{{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:center;border:1px solid rgba(44,230,191,.25);background:rgba(44,230,191,.04);border-radius:15px;padding:16px;margin-top:15px}}.package b{{font-size:20px}}.price{{font-size:19px;color:var(--teal);font-weight:950;text-align:right}}.price small{{display:block;color:var(--muted);font-size:10px}}.instructions{{white-space:pre-wrap;border:1px dashed #2b4a53;border-radius:10px;padding:12px;color:#a7bebb;background:#07141a}}label{{display:block;margin:12px 0 5px;font-size:11px;font-weight:850}}input,select{{width:100%;border:1px solid var(--line);border-radius:9px;background:#061219;color:var(--text);padding:10px}}button{{margin-top:16px;width:100%;border:0;border-radius:10px;background:var(--teal);color:#03110e;padding:11px;font-weight:950;cursor:pointer}}.payment-state,.wait-box,.active-plan{{border-radius:12px;padding:13px;margin-top:12px;display:grid;gap:4px}}.payment-state.pending,.wait-box{{border:1px solid rgba(255,189,89,.32);background:rgba(255,189,89,.06)}}.payment-state.approved,.active-plan{{border:1px solid rgba(66,226,140,.3);background:rgba(66,226,140,.06)}}.payment-state.rejected{{border:1px solid rgba(255,98,125,.3);background:rgba(255,98,125,.06)}}.payment-state small,.wait-box span,.active-plan span{{color:var(--muted)}}.state-dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--amber);margin-right:7px}}.approved .state-dot{{background:var(--green)}}.rejected .state-dot{{background:var(--red)}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:9px;border-bottom:1px solid var(--line);font-size:11px}}th{{color:var(--muted)}}.status{{font-weight:900}}.status.pending{{color:var(--amber)}}.status.approved{{color:var(--green)}}.status.rejected{{color:var(--red)}}@media(max-width:650px){{.package{{grid-template-columns:1fr}}.price{{text-align:left}}table{{display:block;overflow:auto}}}}
</style></head><body><div class="shell"><a href="/">← Panele dön</a>
<div class="card"><h1>Premium üyelik</h1><p>{username}, üyelik ve ödeme durumunu buradan takip edebilirsin.</p><div class="package"><div><b>{html.escape(str(settings['package_name']))}</b><p>{int(settings['days'])} gün Premium erişim · canlı sinyal ayrıntıları ve gelişmiş panel araçları</p></div><div class="price">{html.escape(str(settings['price_label']))}<small>Görünen paket fiyatı</small></div></div>{_latest_payment_card(rows)}</div>
<div class="card"><h2>Ödeme / üyelik işlemi</h2><p>Ödeme otomatik tahsil edilmez. Bildirim yönetici tarafından kontrol edilir ve onaylanır.</p><div class="instructions">{html.escape(str(settings['instructions']))}</div><p>{html.escape(crypto_note)}</p>{action}</div>
<div class="card"><h2>Ödeme geçmişim</h2><table><thead><tr><th>Tarih</th><th>Paket</th><th>Yöntem</th><th>Durum</th></tr></thead><tbody>{history}</tbody></table></div>
</div></body></html>"""


def account_page_v31(session: dict[str, Any], info: dict[str, Any], store: commercial.CommercialAccountStore) -> str:
    username_raw = str(session.get("username") or "üye")
    username = html.escape(username_raw)
    plan = str(info.get("plan") or commercial.PLAN_FREE)
    expiry = commercial._format_expiry(info.get("expires_at"))
    try:
        rows = user_payments(store, username_raw, 3)
    except accounts.AccountStoreError:
        rows = []
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hesabım</title><style>:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--amber:#ffbd59;--green:#42e28c;--red:#ff627d}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}.shell{{width:min(780px,calc(100% - 28px));margin:auto;padding:30px 0}}.card{{border:1px solid var(--line);border-radius:17px;background:var(--panel);padding:20px;margin-top:16px}}a{{color:var(--teal);font-weight:850;text-decoration:none}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}.item{{border:1px solid var(--line);border-radius:10px;padding:12px}}small{{display:block;color:var(--muted)}}strong{{font-size:17px}}.payment-state{{border:1px solid var(--line);border-radius:11px;padding:12px;margin-top:10px;display:grid;gap:3px}}.payment-state.pending{{border-color:rgba(255,189,89,.35)}}.payment-state.approved{{border-color:rgba(66,226,140,.35)}}.payment-state.rejected{{border-color:rgba(255,98,125,.35)}}.state-dot{{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--amber);margin-right:7px}}.approved .state-dot{{background:var(--green)}}.rejected .state-dot{{background:var(--red)}}.payment-empty{{color:var(--muted)}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><div class="shell"><a href="/">← Geri dön</a><div class="card"><h1>Hesabım</h1><div class="grid"><div class="item"><small>Kullanıcı</small><strong>{username}</strong></div><div class="item"><small>Plan</small><strong>{html.escape(commercial._plan_label(plan))}</strong></div><div class="item"><small>Premium bitiş</small><strong>{html.escape(expiry)}</strong></div><div class="item"><small>Durum</small><strong>Aktif</strong></div></div></div><div class="card"><h2>Son ödeme durumu</h2>{_latest_payment_card(rows)}<p><a href="/premium">Premium üyelik ve ödeme merkezi →</a></p></div></div></body></html>"""


def admin_billing_page(
    store: commercial.CommercialAccountStore,
    session: dict[str, Any],
    settings: dict[str, Any],
    *,
    message: str = "",
    error: str = "",
) -> str:
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    users = store.list_commercial_users()
    payments = store.list_payments()
    counts = payment_counts(store)
    notices = ""
    if message:
        notices += f'<div class="notice ok">{html.escape(message)}</div>'
    if error:
        notices += f'<div class="notice err">{html.escape(error)}</div>'

    pending_rows: list[str] = []
    for row in payments:
        if str(row.get("status") or "").upper() != commercial.PAYMENT_PENDING:
            continue
        pid = html.escape(str(row.get("id") or ""), quote=True)
        pending_rows.append(
            f'<tr><td><strong>{html.escape(str(row.get("username") or ""))}</strong><small>{html.escape(str(row.get("id") or ""))}</small></td>'
            f'<td>{_tr_time(row.get("created_at"))}</td><td>{html.escape(str(row.get("method") or ""))}</td><td>{html.escape(str(row.get("package") or ""))}<small>{html.escape(str(row.get("note") or ""))}</small></td>'
            f'<td class="acts"><form method="post" action="/admin/payments/decision"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="payment_id" value="{pid}"><input type="hidden" name="decision" value="approve"><button class="approve">Onayla +{int(settings["days"])} gün</button></form>'
            f'<form method="post" action="/admin/payments/decision"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="payment_id" value="{pid}"><input type="hidden" name="decision" value="reject"><button>Reddet</button></form></td></tr>'
        )
    if not pending_rows:
        pending_rows.append('<tr><td colspan="5" class="empty">Onay bekleyen ödeme bildirimi yok.</td></tr>')

    history_rows = "".join(
        f'<tr><td>{_tr_time(row.get("created_at"))}</td><td>{html.escape(str(row.get("username") or ""))}</td><td>{html.escape(str(row.get("package") or ""))}</td><td>{html.escape(str(row.get("method") or ""))}</td><td><span class="status {_status_class(row.get("status"))}">{html.escape(_status_label(row.get("status")))}</span></td><td>{_tr_time(row.get("decided_at"))}</td></tr>'
        for row in payments[:100]
    ) or '<tr><td colspan="6" class="empty">Henüz ödeme geçmişi yok.</td></tr>'

    user_rows: list[str] = []
    for row in users:
        username_raw = str(row.get("username") or "")
        username = html.escape(username_raw)
        username_attr = html.escape(username_raw, quote=True)
        plan = str(row.get("plan") or commercial.PLAN_FREE)
        if str(row.get("role") or "").upper() == commercial.ROLE_ADMIN:
            actions = "Kurucu / yönetici"
        else:
            actions = (
                f'<form method="post" action="/admin/memberships/plan"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="username" value="{username_attr}"><input type="hidden" name="plan" value="PREMIUM"><input type="hidden" name="days" value="{int(settings["days"])}"><button class="approve">Premium +{int(settings["days"])} gün</button></form>'
                f'<form method="post" action="/admin/memberships/plan"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="username" value="{username_attr}"><input type="hidden" name="plan" value="FREE"><input type="hidden" name="days" value="{int(settings["days"])}"><button>FREE yap</button></form>'
            )
        user_rows.append(f'<tr><td><strong>{username}</strong></td><td>{html.escape(commercial._plan_label(plan))}</td><td>{html.escape(commercial._format_expiry(row.get("expires_at")))}</td><td class="acts">{actions}</td></tr>')

    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Üyelik ve Ödemeler</title>
<style>:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--red:#ff748c;--amber:#ffbd59;--green:#42e28c}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}.shell{{width:min(1220px,calc(100% - 28px));margin:auto;padding:28px 0}}a{{color:var(--teal);font-weight:850;text-decoration:none}}.top{{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}}.card{{border:1px solid var(--line);border-radius:17px;background:var(--panel);padding:18px;margin-top:16px}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:14px}}.stat{{border:1px solid var(--line);border-radius:12px;padding:13px}}.stat small{{color:var(--muted)}}.stat strong{{display:block;font-size:25px}}.stat.pending strong{{color:var(--amber)}}.stat.approved strong{{color:var(--green)}}.stat.rejected strong{{color:var(--red)}}.package{{display:flex;gap:14px;justify-content:space-between;align-items:center;flex-wrap:wrap;border:1px solid rgba(44,230,191,.22);border-radius:12px;padding:13px;margin-top:12px}}.package strong{{font-size:17px}}.package b{{color:var(--teal)}}table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}th{{color:var(--muted);font-size:9px;text-transform:uppercase}}small{{display:block;color:var(--muted)}}.acts{{display:flex;gap:6px;flex-wrap:wrap}}form{{margin:0}}button{{border:1px solid var(--line);border-radius:8px;padding:7px 9px;background:#08161d;color:#c3d7d4;cursor:pointer;font-weight:800}}button.approve{{border-color:rgba(44,230,191,.3);color:var(--teal)}}.notice{{padding:9px;border-radius:9px;margin-top:10px}}.ok{{border:1px solid rgba(44,230,191,.3)}}.err{{border:1px solid rgba(255,98,125,.3)}}.empty{{text-align:center;color:var(--muted);padding:20px}}.status{{font-weight:900}}.status.pending{{color:var(--amber)}}.status.approved{{color:var(--green)}}.status.rejected{{color:var(--red)}}@media(max-width:760px){{.stats{{grid-template-columns:1fr 1fr}}table{{display:block;overflow:auto}}}}</style></head><body><div class="shell"><div class="top"><div><h1>Üyelik ve Ödeme Yönetimi</h1><p>Ödeme bildirimleri, planlar ve üyelik geçmişi</p></div><div><a href="/admin/users">Kullanıcılar</a> · <a href="/">Panele dön</a></div></div>{notices}
<div class="stats"><div class="stat"><small>Toplam bildirim</small><strong>{counts['total']}</strong></div><div class="stat pending"><small>Onay bekliyor</small><strong>{counts['pending']}</strong></div><div class="stat approved"><small>Onaylandı</small><strong>{counts['approved']}</strong></div><div class="stat rejected"><small>Reddedildi</small><strong>{counts['rejected']}</strong></div></div>
<div class="card"><h2>Aktif paket ayarı</h2><div class="package"><div><strong>{html.escape(str(settings['package_name']))}</strong><small>Kod: {html.escape(str(settings['package_code']))} · Süre: {int(settings['days'])} gün</small></div><b>{html.escape(str(settings['price_label']))}</b></div><p><small>Bu değerler Render ortam ayarlarından değiştirilebilir; kod güncellemesi gerekmez.</small></p></div>
<div class="card"><h2>Onay bekleyen ödemeler</h2><table><thead><tr><th>Kullanıcı</th><th>Bildirim</th><th>Yöntem</th><th>Paket / Not</th><th>İşlem</th></tr></thead><tbody>{''.join(pending_rows)}</tbody></table></div>
<div class="card"><h2>Ödeme geçmişi</h2><table><thead><tr><th>Bildirim</th><th>Kullanıcı</th><th>Paket</th><th>Yöntem</th><th>Durum</th><th>Karar</th></tr></thead><tbody>{history_rows}</tbody></table></div>
<div class="card"><h2>Üyelik planları</h2><table><thead><tr><th>Kullanıcı</th><th>Plan</th><th>Bitiş</th><th>İşlem</th></tr></thead><tbody>{''.join(user_rows)}</tbody></table></div>
</div></body></html>"""


def make_v31_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    settings = _settings()
    crypto_enabled = env_bool("PANEL_CRYPTO_PAYMENT_ENABLED", False)
    BaseHandler = commercial.make_v3_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V31Handler(BaseHandler):
        server_version = "KriptoPanel/3.1"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            info = self._plan_info(session)
            nonce = secrets.token_urlsafe(18)
            if str(info.get("plan")) == commercial.PLAN_FREE:
                self._send(HTTPStatus.OK, commercial.free_member_page(session, info, nonce), "text/html; charset=utf-8", nonce=nonce)
                return
            body = memory.memory_dashboard_page(session, nonce)
            if str(info.get("plan")) == commercial.PLAN_ADMIN:
                try:
                    pending_count = payment_counts(store)["pending"]
                except accounts.AccountStoreError:
                    pending_count = 0
                label = f"Üyelikler / Ödemeler · {pending_count}" if pending_count else "Üyelikler / Ödemeler"
                plan_link = f'<a class="badge" href="/admin/memberships">{html.escape(label)}</a>'
            else:
                plan_link = '<a class="badge" href="/premium">Premium</a>'
            marker = '<a class="badge" href="/account">Hesabım</a>'
            if marker in body:
                body = body.replace(marker, plan_link + marker, 1)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION, "billing_status": True, "package_code": settings["package_code"], "premium_days": settings["days"], "crypto_payment_enabled": crypto_enabled})
                return
            if path == "/premium":
                session = self._session()
                if not session:
                    self._redirect("/register")
                    return
                self._send(HTTPStatus.OK, premium_page_v31(session, self._plan_info(session), store, settings, crypto_enabled), "text/html; charset=utf-8")
                return
            if path == "/account":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                self._send(HTTPStatus.OK, account_page_v31(session, self._plan_info(session), store), "text/html; charset=utf-8")
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
                    body = admin_billing_page(store, session, settings, message=message, error=error)
                except accounts.AccountStoreError as exc:
                    body = admin_billing_page(store, session, settings, error=str(exc))
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            return super().do_GET()

    return V31Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.1 ödeme durum ve üyelik merkezi.")
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
    handler = make_v31_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    settings = _settings()
    print(f"{VERSION} http://{args.host}:{args.port} billing_status=on package={settings['package_code']} premium_days={settings['days']} signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
