"""Kripto Kontrol Merkezi V3.32.5 - JS'siz mobil Hesap ve Premium deneyimi.

Bu modül yalnız sunum yardımcıları içerir. Mevcut üyelik/ödeme backend'i,
/payment/notify POST akışı, admin onayı ve yenileme kuralları değiştirilmez.
Telefon/tablet için Hesap ve Premium sayfaları server-rendered HTML üretir.
"""
from __future__ import annotations

import html
from typing import Any

import dashboard_billing_app as billing
import dashboard_commercial_app as commercial
import dashboard_retention_app as retention

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_5_MOBILE_ACCOUNT_2026_08_16"


def _esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def _status_class(value: Any) -> str:
    status = str(value or "").upper()
    if status == commercial.PAYMENT_APPROVED:
        return "approved"
    if status == commercial.PAYMENT_REJECTED:
        return "rejected"
    return "pending"


def _nav(plan: str, active: str) -> str:
    premium = plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}
    signal_href = "/mobile?view=signals" if premium else "/premium"
    result_href = "/mobile?view=results" if premium else "/premium"
    items = [
        ("home", "/mobile", "⌂", "Ana"),
        ("market", "/mobile/market", "⌁", "Piyasa"),
        ("signal", signal_href, "⚡", "Sinyal" if premium else "Premium"),
        ("result", result_href, "✓", "Sonuç" if premium else "Üyelik"),
        ("account", "/mobile/account", "○", "Hesap"),
    ]
    return '<nav class="bottomnav">' + ''.join(
        f'<a class="{"active" if key == active else ""}" href="{href}"><span>{icon}</span>{label}</a>'
        for key, href, icon, label in items
    ) + '</nav>'


def _shell(*, title: str, subtitle: str, session: dict[str, Any], plan: str, plan_label: str, body: str, active: str = "account") -> str:
    username = _esc(session.get("username") or "üye")
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    nav = _nav(plan, active)
    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="dark"><title>{_esc(title)} · Kripto Kontrol</title><style>
:root{{--bg:#071018;--panel:#0c1720;--panel2:#09141c;--line:#1d303b;--text:#edf7f5;--muted:#819a97;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59}}*{{box-sizing:border-box}}html,body{{margin:0;min-height:100%;background:var(--bg);color:var(--text);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}}body{{padding:0 12px calc(86px + env(safe-area-inset-bottom));overflow-x:hidden}}a{{color:inherit;text-decoration:none;touch-action:manipulation;-webkit-tap-highlight-color:transparent}}button,input,select{{font:inherit}}button{{touch-action:manipulation}}.wrap{{max-width:720px;margin:auto}}header{{position:sticky;top:0;z-index:10;margin:0 -12px;padding:10px 12px;display:flex;align-items:center;gap:9px;background:rgba(7,16,24,.98);border-bottom:1px solid var(--line)}}.back{{border:1px solid var(--line);border-radius:9px;padding:8px 9px;color:#a9bfbc;font-size:9px;font-weight:850}}.head{{flex:1;min-width:0}}.head strong{{display:block;font-size:13px}}.head small{{display:block;color:var(--muted);font-size:8px}}.plan{{border:1px solid #2a4742;border-radius:999px;padding:4px 7px;color:var(--teal);font-size:8px;font-weight:900}}.hero{{padding:16px 0 8px}}.hero h1{{margin:0;font-size:24px;letter-spacing:-.03em}}.hero p{{margin:4px 0 0;color:var(--muted);font-size:10px}}.who{{margin-top:7px;color:#617d79;font-size:8px}}.card{{border:1px solid var(--line);background:var(--panel);border-radius:13px;padding:12px;margin:8px 0}}.card h2{{font-size:13px;margin:0 0 8px}}.card p{{color:var(--muted);font-size:10px;margin:5px 0}}.grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}}.item{{background:var(--panel2);border-radius:9px;padding:9px;min-width:0}}.item small{{display:block;color:var(--muted);font-size:7px}}.item strong{{display:block;font-size:13px;margin-top:3px;overflow-wrap:anywhere}}.status{{border-radius:11px;padding:10px;border:1px solid var(--line);display:grid;gap:3px;margin-top:8px}}.status.pending{{border-color:rgba(255,189,89,.3);background:rgba(255,189,89,.04)}}.status.approved{{border-color:rgba(66,226,140,.3);background:rgba(66,226,140,.04)}}.status.rejected{{border-color:rgba(255,98,125,.3);background:rgba(255,98,125,.04)}}.status b{{font-size:11px}}.status small{{color:var(--muted);font-size:8px}}.actions{{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}}.actions a,.actions button{{flex:1;min-width:120px;border:1px solid var(--line);background:#0b1821;color:#b4c9c6;border-radius:10px;padding:10px;text-align:center;font-size:9px;font-weight:900}}.actions .primary,.pay-form button{{background:#0d2a24;border-color:#275b50;color:var(--teal)}}.notice{{border:1px solid #3d4430;background:#18190f;border-radius:11px;padding:10px;color:#b8b69a;font-size:9px;margin:8px 0}}.notice.good{{border-color:rgba(66,226,140,.28);background:rgba(66,226,140,.04);color:#a8c9b5}}.notice.danger{{border-color:rgba(255,98,125,.28);background:rgba(255,98,125,.04);color:#d1a3ab}}.package{{border:1px solid rgba(44,230,191,.22);background:rgba(44,230,191,.04);border-radius:13px;padding:12px;margin:8px 0}}.package strong{{display:block;font-size:15px}}.package span{{display:block;color:var(--muted);font-size:8px;margin-top:2px}}.package b{{display:block;color:var(--teal);font-size:13px;margin-top:7px}}label{{display:block;color:#a8bfbc;font-size:8px;font-weight:850;margin:9px 0 4px}}input,select{{width:100%;border:1px solid var(--line);border-radius:9px;background:#061219;color:var(--text);padding:10px;font-size:13px}}.pay-form button{{width:100%;border-radius:10px;padding:11px;font-weight:950;margin-top:11px}}details{{border-top:1px solid #152832;margin-top:9px;padding-top:8px}}summary{{cursor:pointer;list-style:none;color:var(--teal);font-size:8px;font-weight:850;touch-action:manipulation}}summary::-webkit-details-marker{{display:none}}.instructions{{white-space:pre-wrap;color:#a9bfbc;font-size:9px;margin-top:8px;background:var(--panel2);border-radius:9px;padding:9px}}.history{{margin-top:7px}}.history-row{{display:flex;justify-content:space-between;gap:8px;padding:9px 1px;border-bottom:1px solid #142630}}.history-row:last-child{{border-bottom:0}}.history-row strong{{font-size:9px}}.history-row small{{display:block;color:var(--muted);font-size:7px}}.badge{{font-size:8px;font-weight:900}}.badge.pending{{color:var(--amber)}}.badge.approved{{color:var(--green)}}.badge.rejected{{color:var(--red)}}.logout{{margin:0}}.logout button{{border:1px solid var(--line);background:#0b1821;color:#a9bfbc;border-radius:9px;padding:8px 10px;font-size:9px;font-weight:850}}.bottomnav{{position:fixed;left:0;right:0;bottom:0;z-index:20;height:66px;padding-bottom:env(safe-area-inset-bottom);display:grid;grid-template-columns:repeat(5,1fr);background:rgba(8,18,25,.99);border-top:1px solid var(--line)}}.bottomnav a{{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;color:#6f8986;font-size:7px;font-weight:800;min-width:0}}.bottomnav a span{{font-size:15px;line-height:1}}.bottomnav a.active{{color:var(--teal)}}@media(min-width:760px){{.bottomnav{{max-width:720px;left:50%;transform:translateX(-50%)}}}}@media(max-width:390px){{.grid{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap"><header><a class="back" href="/mobile">← Kontrol</a><div class="head"><strong>Kripto Kontrol</strong><small>Mobil · üyelik merkezi</small></div><span class="plan">{_esc(plan_label)}</span></header><div class="hero"><h1>{_esc(title)}</h1><p>{_esc(subtitle)}</p><div class="who">{username}</div></div>{body}<form class="logout" method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button type="submit">Oturumu kapat</button></form></div>{nav}</body></html>'''


def _payment_status(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="notice">Henüz ödeme bildirimi yok.</div>'
    row = rows[0]
    status = str(row.get("status") or "").upper()
    cls = _status_class(status)
    note = str(row.get("note") or "").strip()
    note_html = f'<small>Not: {_esc(note)}</small>' if note else ""
    return (
        f'<div class="status {cls}"><b>{_esc(billing._status_label(status))}</b>'
        f'<small>{_esc(row.get("package") or "Premium")} · {_esc(billing._tr_time(row.get("created_at")))}</small>{note_html}</div>'
    )


def _history(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return '<div class="notice">Henüz ödeme geçmişi yok.</div>'
    return '<div class="history">' + ''.join(
        f'<div class="history-row"><div><strong>{_esc(row.get("package") or "Premium")}</strong><small>{_esc(billing._tr_time(row.get("created_at")))} · {_esc(row.get("method") or "—")}</small></div><span class="badge {_status_class(row.get("status"))}">{_esc(billing._status_label(row.get("status")))}</span></div>'
        for row in rows[:8]
    ) + '</div>'


def _payment_form(session: dict[str, Any], settings: dict[str, Any], *, crypto_enabled: bool, button_text: str) -> str:
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    package = html.escape(str(settings.get("package_code") or "PREMIUM_30D"), quote=True)
    crypto_option = '<option value="CRYPTO">Kripto ödeme bildirimi</option>' if crypto_enabled else ""
    return f'''<form class="pay-form" method="post" action="/payment/notify"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="package" value="{package}"><label>Ödeme yöntemi</label><select name="method"><option value="BANK_TRANSFER">Banka / FAST / Havale</option>{crypto_option}</select><label>Not (isteğe bağlı)</label><input name="note" maxlength="180" placeholder="Gönderen adı veya kısa açıklama"><button type="submit">{_esc(button_text)}</button></form>'''


def render_account_page(session: dict[str, Any], info: dict[str, Any], *, plan: str, plan_label: str, store) -> str:
    username = str(session.get("username") or "")
    try:
        rows = billing.user_payments(store, username, 3)
    except Exception:
        rows = []
    expiry = commercial._format_expiry(info.get("expires_at"))
    state = retention.renewal_state(info)
    expiry_item = ""
    if plan == commercial.PLAN_PREMIUM:
        expiry_item = f'<div class="item"><small>Premium bitiş</small><strong>{_esc(expiry)}</strong></div>'
    stage = str(state.get("stage") or "")
    if stage in {"D1", "D3", "D7"}:
        membership_note = f'<div class="notice">Premium bitimine {int(state.get("days") or 0)} gün kaldı. Yenileme için üyelik merkezine geçebilirsin.</div>'
    elif stage == "EXPIRED":
        membership_note = '<div class="notice danger">Premium süren sona erdi. Hesabın FREE olarak açık kalır; istediğinde tekrar Premium başlatabilirsin.</div>'
    elif plan in {commercial.PLAN_PREMIUM, commercial.PLAN_ADMIN}:
        membership_note = '<div class="notice good">Üyeliğin aktif. Açık sinyal ve Coin Merkezi erişimin kullanılabilir.</div>'
    else:
        membership_note = '<div class="notice">FREE plan aktiftir. Public Piyasa Merkezi kullanılabilir; işlem seviyeleri Premium üyeliğe özeldir.</div>'
    admin = '<a href="/admin/center">Yönetim merkezi</a>' if plan == commercial.PLAN_ADMIN else ""
    body = f'''{membership_note}<div class="card"><h2>Hesap özeti</h2><div class="grid"><div class="item"><small>Kullanıcı</small><strong>{_esc(username)}</strong></div><div class="item"><small>Plan</small><strong>{_esc(plan_label)}</strong></div>{expiry_item}</div><div class="actions"><a class="primary" href="/mobile/premium">Üyelik merkezi</a><a href="/mobile/market">Piyasa</a>{admin}</div></div><div class="card"><h2>Son ödeme durumu</h2>{_payment_status(rows)}<details><summary>Ödeme geçmişini göster</summary>{_history(rows)}</details></div>'''
    return _shell(title="Hesabım", subtitle="Plan, üyelik ve ödeme durumu", session=session, plan=plan, plan_label=plan_label, body=body)


def render_premium_page(session: dict[str, Any], info: dict[str, Any], *, plan: str, plan_label: str, store, settings: dict[str, Any], crypto_enabled: bool) -> str:
    username = str(session.get("username") or "")
    try:
        rows = billing.user_payments(store, username, 8)
    except Exception:
        rows = []
    pending = next((row for row in rows if str(row.get("status") or "").upper() == commercial.PAYMENT_PENDING), None)
    state = retention.renewal_state(info)
    stage = str(state.get("stage") or "")
    package = f'<div class="package"><strong>{_esc(settings.get("package_name") or "Premium")}</strong><span>+{int(settings.get("days") or 30)} gün Premium erişim</span><b>{_esc(settings.get("price_label") or "—")}</b></div>'

    if plan == commercial.PLAN_ADMIN:
        action = '<div class="notice good">Yönetici hesabında Premium araçlar aktiftir.</div><div class="actions"><a class="primary" href="/admin/center">Yönetim merkezi</a><a href="/mobile">Panele dön</a></div>'
    elif pending:
        action = '<div class="notice">Ödeme bildirimin alındı ve yönetici onayı bekliyor. Aynı ödeme için ikinci bildirim göndermene gerek yok.</div>'
    elif plan == commercial.PLAN_PREMIUM and stage in {"D1", "D3", "D7"}:
        days = int(state.get("days") or 0)
        action = f'<div class="notice">Premium erişiminin bitmesine {days} gün kaldı. Onaylanan yenileme mevcut bitiş tarihinin üzerine eklenir.</div>' + _payment_form(session, settings, crypto_enabled=crypto_enabled, button_text="Yenileme ödemesi yaptım · Onaya gönder")
    elif plan == commercial.PLAN_PREMIUM:
        action = f'<div class="notice good">Premium üyeliğin aktif. Bitiş: {_esc(commercial._format_expiry(info.get("expires_at")))}</div>'
    else:
        expired = '<div class="notice danger">Önceki Premium süren sona ermiş. FREE kullanım devam ediyor.</div>' if stage == "EXPIRED" else ""
        action = expired + _payment_form(session, settings, crypto_enabled=crypto_enabled, button_text="Ödeme yaptım · Onaya gönder")

    body = f'''{package}<div class="card"><h2>Üyelik işlemi</h2>{action}<details><summary>Ödeme açıklamasını göster</summary><div class="instructions">{_esc(settings.get("instructions") or "")}</div></details></div><div class="card"><h2>Ödeme durumu</h2>{_payment_status(rows)}<details><summary>Geçmiş işlemleri göster</summary>{_history(rows)}</details></div><div class="notice">Ödeme otomatik tahsil edilmez. Bildirim yönetici tarafından kontrol edilir; üyelik yalnız onaydan sonra değişir.</div>'''
    return _shell(title="Üyeliğim", subtitle="Premium plan ve ödeme işlemleri", session=session, plan=plan, plan_label=plan_label, body=body)
