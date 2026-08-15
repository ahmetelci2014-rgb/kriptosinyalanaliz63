"""Kripto Kontrol Merkezi V3.7 - müşteri yaşam döngüsü ve yenileme merkezi.

V3.6 üzerine yalnız ürün/üyelik yönetimi katmanında eklenir:
- FREE / Premium / süresi yaklaşan / süresi biten / ödeme bekleyen müşteri segmentleri,
- kullanıcı arama, filtreleme ve öncelik sıralaması,
- ödeme beklemeyen üyelerde güvenli +30 / +90 gün hızlı Premium uzatma,
- bekleyen ödeme varken çift süre verme riskini engelleme,
- yönetim merkezinden yaşam döngüsü ekranına görünür kısayol,
- V3.6 yeni kullanıcı metriği için güvenli created_at metadata düzeltmesi.

Sinyal üretimi, strategy/config, radarlar, Telegram ve emir akışı değişmez.
"""

from __future__ import annotations

import argparse
import copy
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
import dashboard_business_app as business
import dashboard_commercial_app as commercial
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_7_LIFECYCLE_2026_08_15"
DAY = 86_400
VALID_SEGMENTS = {"all", "action", "free", "premium", "expiring3", "expiring7", "expiring30", "expired", "pending", "inactive"}
VALID_SORTS = {"attention", "expiry", "newest", "username"}


def _stamp(value: Any) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _merge_created_metadata(public_rows: list[dict[str, Any]], raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Parola özeti taşımadan yalnız güvenli oluşturma/güncelleme zamanını birleştirir."""
    meta: dict[str, dict[str, int]] = {}
    for raw in raw_rows:
        if not isinstance(raw, dict):
            continue
        username = str(raw.get("username") or "").casefold()
        if not username:
            continue
        meta[username] = {
            "created_at": _stamp(raw.get("created_at")),
            "updated_at": _stamp(raw.get("updated_at")),
        }
    result: list[dict[str, Any]] = []
    for row in public_rows:
        clean = copy.deepcopy(row)
        username = str(clean.get("username") or "").casefold()
        values = meta.get(username, {})
        clean["created_at"] = _stamp(clean.get("created_at")) or values.get("created_at", 0)
        clean["updated_at"] = _stamp(clean.get("updated_at")) or values.get("updated_at", 0)
        clean.pop("password_hash", None)
        result.append(clean)
    return result


class LifecycleAccountStore(commercial.CommercialAccountStore):
    """V3.0 store davranışını korur; listeye yalnız created_at metadata ekler."""

    def list_commercial_users(self) -> list[dict[str, Any]]:
        rows = super().list_commercial_users()
        try:
            with self._lock:
                _users, document, _sha = self._users_unlocked()
                raw_rows = copy.deepcopy(document.get("users", [])) if isinstance(document, dict) else []
        except Exception:
            raw_rows = []
        return _merge_created_metadata(rows, raw_rows)


def lifecycle_store_from_env(config: PanelConfig) -> LifecycleAccountStore:
    return LifecycleAccountStore(
        config.repository,
        os.getenv("GITHUB_PANEL_USERS_TOKEN"),
        ref=os.getenv("PANEL_USERS_REF", accounts.USERS_REF_DEFAULT),
        path=os.getenv("PANEL_USERS_PATH", accounts.USERS_PATH_DEFAULT),
    )


def _pending_map(payments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in payments:
        if not isinstance(row, dict) or str(row.get("status") or "").upper() != commercial.PAYMENT_PENDING:
            continue
        key = str(row.get("username") or "").casefold()
        if key and (key not in result or _stamp(row.get("created_at")) > _stamp(result[key].get("created_at"))):
            result[key] = row
    return result


def build_lifecycle_rows(store: commercial.CommercialAccountStore, *, now: int | None = None) -> list[dict[str, Any]]:
    now = int(now or time.time())
    try:
        users = list(store.list_commercial_users())
    except accounts.AccountStoreError:
        users = []
    try:
        payments = list(store.list_payments())
    except accounts.AccountStoreError:
        payments = []
    pending = _pending_map(payments)
    rows: list[dict[str, Any]] = []
    for user in users:
        if not isinstance(user, dict) or str(user.get("role") or "").upper() == commercial.ROLE_ADMIN:
            continue
        username = str(user.get("username") or "").strip()
        if not username:
            continue
        plan = str(user.get("plan") or commercial.PLAN_FREE).upper()
        active = bool(user.get("active", True))
        expiry = _stamp(user.get("expires_at"))
        created = _stamp(user.get("created_at"))
        updated = _stamp(user.get("updated_at"))
        expired = bool(expiry and expiry <= now)
        premium = plan == commercial.PLAN_PREMIUM and not expired
        normal_free = plan == commercial.PLAN_FREE and not expired
        remaining_seconds = max(0, expiry - now) if expiry else 0
        days_remaining = int(math.ceil(remaining_seconds / DAY)) if premium and expiry else None
        expiring3 = bool(premium and days_remaining is not None and days_remaining <= 3)
        expiring7 = bool(premium and days_remaining is not None and days_remaining <= 7)
        expiring30 = bool(premium and days_remaining is not None and days_remaining <= 30)
        pay = pending.get(username.casefold())
        pending_payment = bool(pay)
        if pending_payment:
            status = "ÖDEME BEKLİYOR"
            priority = 0
        elif not active:
            status = "PASİF"
            priority = 1
        elif expired:
            status = "SÜRESİ BİTTİ"
            priority = 2
        elif expiring3:
            status = "3 GÜN İÇİNDE"
            priority = 3
        elif expiring7:
            status = "7 GÜN İÇİNDE"
            priority = 4
        elif expiring30:
            status = "30 GÜN İÇİNDE"
            priority = 5
        elif premium:
            status = "PREMIUM"
            priority = 6
        else:
            status = "FREE"
            priority = 7
        rows.append({
            "username": username,
            "plan": commercial.PLAN_PREMIUM if premium else commercial.PLAN_FREE,
            "active": active,
            "expires_at": expiry or None,
            "created_at": created,
            "updated_at": updated,
            "expired": expired,
            "premium": premium,
            "free": normal_free,
            "days_remaining": days_remaining,
            "expiring3": expiring3,
            "expiring7": expiring7,
            "expiring30": expiring30,
            "pending_payment": pending_payment,
            "payment_id": str((pay or {}).get("id") or ""),
            "payment_created_at": _stamp((pay or {}).get("created_at")),
            "status": status,
            "priority": priority,
            "action_needed": bool(pending_payment or not active or expired or expiring7),
        })
    return rows


def lifecycle_summary(rows: list[dict[str, Any]], *, now: int | None = None) -> dict[str, int]:
    now = int(now or time.time())
    return {
        "total": len(rows),
        "premium": sum(1 for row in rows if row.get("premium")),
        "free": sum(1 for row in rows if row.get("free")),
        "expiring3": sum(1 for row in rows if row.get("expiring3")),
        "expiring7": sum(1 for row in rows if row.get("expiring7")),
        "expiring30": sum(1 for row in rows if row.get("expiring30")),
        "expired": sum(1 for row in rows if row.get("expired")),
        "pending": sum(1 for row in rows if row.get("pending_payment")),
        "inactive": sum(1 for row in rows if not row.get("active")),
        "action": sum(1 for row in rows if row.get("action_needed")),
        "new7": sum(1 for row in rows if _stamp(row.get("created_at")) and now - 7 * DAY <= _stamp(row.get("created_at")) <= now),
    }


def filter_lifecycle_rows(rows: list[dict[str, Any]], *, segment: str = "action", query: str = "", sort: str = "attention") -> list[dict[str, Any]]:
    segment = segment if segment in VALID_SEGMENTS else "action"
    sort = sort if sort in VALID_SORTS else "attention"
    q = str(query or "").strip().casefold()[:60]

    def matches(row: dict[str, Any]) -> bool:
        if q and q not in str(row.get("username") or "").casefold():
            return False
        if segment == "all":
            return True
        if segment == "action":
            return bool(row.get("action_needed"))
        if segment == "free":
            return bool(row.get("free"))
        if segment == "premium":
            return bool(row.get("premium"))
        if segment in {"expiring3", "expiring7", "expiring30"}:
            return bool(row.get(segment))
        if segment == "expired":
            return bool(row.get("expired"))
        if segment == "pending":
            return bool(row.get("pending_payment"))
        if segment == "inactive":
            return not bool(row.get("active"))
        return True

    selected = [row for row in rows if matches(row)]
    if sort == "expiry":
        selected.sort(key=lambda row: (_stamp(row.get("expires_at")) or 9_999_999_999, str(row.get("username") or "").casefold()))
    elif sort == "newest":
        selected.sort(key=lambda row: (_stamp(row.get("created_at")), str(row.get("username") or "").casefold()), reverse=True)
    elif sort == "username":
        selected.sort(key=lambda row: str(row.get("username") or "").casefold())
    else:
        selected.sort(key=lambda row: (int(row.get("priority", 99)), _stamp(row.get("expires_at")) or 9_999_999_999, str(row.get("username") or "").casefold()))
    return selected


def _tr_time(value: Any) -> str:
    stamp = _stamp(value)
    if not stamp:
        return "—"
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(stamp + 3 * 3600))


def _segment_label(segment: str) -> str:
    return {
        "all": "Tümü", "action": "Aksiyon", "free": "FREE", "premium": "Premium",
        "expiring3": "≤3 gün", "expiring7": "≤7 gün", "expiring30": "≤30 gün",
        "expired": "Süresi bitti", "pending": "Ödeme bekliyor", "inactive": "Pasif",
    }.get(segment, "Aksiyon")


def lifecycle_page(
    store: commercial.CommercialAccountStore,
    session: dict[str, Any],
    settings: dict[str, Any],
    *,
    segment: str = "action",
    query: str = "",
    sort: str = "attention",
    message: str = "",
    error: str = "",
) -> str:
    now = int(time.time())
    all_rows = build_lifecycle_rows(store, now=now)
    summary = lifecycle_summary(all_rows, now=now)
    segment = segment if segment in VALID_SEGMENTS else "action"
    sort = sort if sort in VALID_SORTS else "attention"
    query = str(query or "").strip()[:60]
    rows = filter_lifecycle_rows(all_rows, segment=segment, query=query, sort=sort)[:250]
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)

    chips = []
    chip_counts = {
        "action": summary["action"], "all": summary["total"], "premium": summary["premium"], "free": summary["free"],
        "expiring3": summary["expiring3"], "expiring7": summary["expiring7"], "expiring30": summary["expiring30"],
        "expired": summary["expired"], "pending": summary["pending"], "inactive": summary["inactive"],
    }
    for key in ("action", "all", "premium", "free", "expiring3", "expiring7", "expiring30", "expired", "pending", "inactive"):
        cls = " active" if key == segment else ""
        params = urllib.parse.urlencode({"segment": key, "q": query, "sort": sort})
        chips.append(f'<a class="chip{cls}" href="/admin/lifecycle?{params}">{html.escape(_segment_label(key))}<b>{chip_counts[key]}</b></a>')

    cards = []
    for row in rows:
        username = html.escape(str(row["username"]))
        username_attr = html.escape(str(row["username"]), quote=True)
        status = str(row.get("status") or "")
        status_cls = "pending" if row.get("pending_payment") else "expired" if row.get("expired") else "warn" if row.get("expiring7") else "off" if not row.get("active") else "premium" if row.get("premium") else "free"
        remaining = "—"
        if row.get("expired"):
            remaining = "Süresi doldu"
        elif row.get("premium") and row.get("days_remaining") is None:
            remaining = "Süre sınırı yok"
        elif row.get("days_remaining") is not None:
            remaining = f'{int(row["days_remaining"])} gün'
        elif row.get("free"):
            remaining = "FREE"

        if row.get("pending_payment"):
            actions = '<a class="action primary" href="/admin/memberships">Ödemeyi incele</a><span class="action-note">Ödeme kararı verilmeden hızlı süre ekleme kapalı.</span>'
        elif not row.get("active"):
            actions = '<a class="action" href="/admin/users">Hesabı yönet</a>'
        else:
            forms = []
            for days in (30, 90):
                forms.append(
                    f'<form method="post" action="/admin/lifecycle/plan"><input type="hidden" name="csrf" value="{csrf}">'
                    f'<input type="hidden" name="username" value="{username_attr}"><input type="hidden" name="plan" value="PREMIUM">'
                    f'<input type="hidden" name="days" value="{days}"><input type="hidden" name="segment" value="{html.escape(segment, quote=True)}">'
                    f'<input type="hidden" name="q" value="{html.escape(query, quote=True)}"><input type="hidden" name="sort" value="{html.escape(sort, quote=True)}">'
                    f'<button class="action primary">+{days} gün</button></form>'
                )
            if row.get("premium"):
                forms.append(
                    f'<form method="post" action="/admin/lifecycle/plan"><input type="hidden" name="csrf" value="{csrf}">'
                    f'<input type="hidden" name="username" value="{username_attr}"><input type="hidden" name="plan" value="FREE">'
                    f'<input type="hidden" name="days" value="{int(settings.get("days") or 30)}"><input type="hidden" name="segment" value="{html.escape(segment, quote=True)}">'
                    f'<input type="hidden" name="q" value="{html.escape(query, quote=True)}"><input type="hidden" name="sort" value="{html.escape(sort, quote=True)}">'
                    '<button class="action danger">FREE yap</button></form>'
                )
            actions = ''.join(forms)

        created = _tr_time(row.get("created_at"))
        expiry = _tr_time(row.get("expires_at"))
        cards.append(f'''
<div class="customer"><div class="customer-main"><div class="avatar">{html.escape(str(row['username'])[:1].upper())}</div><div><strong>{username}</strong><span>{html.escape(commercial._plan_label(str(row.get('plan') or commercial.PLAN_FREE)))}</span></div></div>
<div class="status {status_cls}">{html.escape(status)}</div>
<div class="meta"><div><small>Kalan</small><b>{html.escape(remaining)}</b></div><div><small>Bitiş</small><b>{html.escape(expiry)}</b></div><div><small>Kayıt</small><b>{html.escape(created)}</b></div></div>
<div class="actions">{actions}</div></div>''')

    if not cards:
        cards.append('<div class="empty">Bu filtreye uyan kullanıcı yok.</div>')

    notices = ""
    if message:
        notices += f'<div class="notice ok">{html.escape(message[:240])}</div>'
    if error:
        notices += f'<div class="notice err">{html.escape(error[:240])}</div>'

    sort_opts = ''.join(
        f'<option value="{key}"{" selected" if key == sort else ""}>{label}</option>'
        for key, label in (("attention", "Öncelik"), ("expiry", "Bitiş tarihi"), ("newest", "Yeni kayıt"), ("username", "Kullanıcı adı"))
    )

    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Müşteri Yaşam Döngüsü</title>
<style>
:root{{--bg:#061016;--panel:#0b1b23;--panel2:#0d2029;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--green:#42e28c;--amber:#ffbd59;--red:#ff627d;--blue:#60a5fa}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 90% 0,rgba(44,230,191,.07),transparent 28%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}button,input,select{{font:inherit}}.shell{{width:min(1260px,calc(100% - 24px));margin:auto;padding:22px 0 60px}}.top{{display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap}}.top h1{{margin:0;font-size:29px;letter-spacing:-.035em}}.top p{{margin:4px 0;color:var(--muted)}}.top-actions{{display:flex;gap:7px;flex-wrap:wrap}}.btn{{border:1px solid var(--line);border-radius:10px;padding:9px 11px;background:#091820;font-weight:850;font-size:10px}}.btn.primary{{color:var(--teal);border-color:rgba(44,230,191,.3)}}
.kpis{{display:grid;grid-template-columns:repeat(6,1fr);gap:8px;margin:17px 0}}.kpi{{border:1px solid var(--line);border-radius:13px;background:var(--panel);padding:12px}}.kpi small{{display:block;color:var(--muted);font-size:8px;text-transform:uppercase}}.kpi strong{{display:block;font-size:22px;margin-top:3px}}.kpi.teal strong{{color:var(--teal)}}.kpi.amber strong{{color:var(--amber)}}.kpi.red strong{{color:var(--red)}}.kpi.blue strong{{color:var(--blue)}}
.toolbar{{border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:12px;margin-bottom:11px}}.chips{{display:flex;gap:6px;flex-wrap:wrap}}.chip{{border:1px solid var(--line);border-radius:999px;padding:6px 9px;color:#9db4b1;font-size:9px;font-weight:900}}.chip b{{margin-left:5px;color:var(--muted)}}.chip.active{{border-color:rgba(44,230,191,.42);color:var(--teal);background:rgba(44,230,191,.055)}}.search{{display:grid;grid-template-columns:1fr 180px auto;gap:7px;margin-top:10px}}input,select{{border:1px solid var(--line);border-radius:9px;background:#07151c;color:var(--text);padding:9px}}.search button{{border:0;border-radius:9px;background:var(--teal);color:#03110e;padding:9px 13px;font-weight:950;cursor:pointer}}
.notice{{border-radius:10px;padding:9px 11px;margin-bottom:9px}}.notice.ok{{border:1px solid rgba(66,226,140,.3);color:var(--green)}}.notice.err{{border:1px solid rgba(255,98,125,.3);color:#ff9bad}}.list{{display:grid;gap:8px}}.customer{{display:grid;grid-template-columns:minmax(190px,1.1fr) 120px minmax(300px,1.35fr) minmax(250px,1.1fr);gap:10px;align-items:center;border:1px solid var(--line);border-radius:14px;background:var(--panel);padding:11px}}.customer-main{{display:flex;gap:9px;align-items:center}}.avatar{{width:35px;height:35px;border-radius:10px;display:grid;place-items:center;border:1px solid #24424c;background:#07161d;color:var(--teal);font-weight:950}}.customer-main strong{{display:block}}.customer-main span{{display:block;color:var(--muted);font-size:9px}}.status{{justify-self:start;border:1px solid var(--line);border-radius:999px;padding:5px 7px;font-size:8px;font-weight:950}}.status.pending{{color:var(--amber);border-color:rgba(255,189,89,.35)}}.status.expired,.status.off{{color:var(--red);border-color:rgba(255,98,125,.3)}}.status.warn{{color:var(--amber)}}.status.premium{{color:var(--teal)}}.status.free{{color:#a1b4b1}}.meta{{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}}.meta div{{border-left:1px solid var(--line);padding-left:8px}}.meta small{{display:block;color:var(--muted);font-size:7px;text-transform:uppercase}}.meta b{{display:block;font-size:9px;margin-top:2px}}.actions{{display:flex;gap:5px;align-items:center;justify-content:flex-end;flex-wrap:wrap}}.actions form{{margin:0}}.action{{border:1px solid var(--line);border-radius:8px;background:#08171e;color:#b7cbc8;padding:7px 8px;font-size:8px;font-weight:900;cursor:pointer;display:inline-block}}.action.primary{{color:var(--teal);border-color:rgba(44,230,191,.3)}}.action.danger{{color:#ff9bad;border-color:rgba(255,98,125,.25)}}.action-note{{color:var(--muted);font-size:8px;max-width:165px}}.empty{{border:1px dashed var(--line);border-radius:14px;padding:28px;text-align:center;color:var(--muted)}}.footnote{{color:var(--muted);font-size:9px;margin-top:12px}}
@media(max-width:1050px){{.kpis{{grid-template-columns:repeat(3,1fr)}}.customer{{grid-template-columns:1fr 110px 1.2fr}}.actions{{grid-column:1/-1;justify-content:flex-start;border-top:1px solid var(--line);padding-top:8px}}}}@media(max-width:680px){{.shell{{width:calc(100% - 16px);padding-top:14px}}.top h1{{font-size:24px}}.kpis{{grid-template-columns:1fr 1fr}}.search{{grid-template-columns:1fr}}.customer{{grid-template-columns:1fr auto}}.meta{{grid-column:1/-1}}.actions{{grid-column:1/-1}}.chips{{flex-wrap:nowrap;overflow:auto;padding-bottom:3px}}.chip{{flex:0 0 auto}}}}
</style></head><body><div class="shell"><div class="top"><div><h1>Müşteri Yaşam Döngüsü</h1><p>Yenileme, ödeme ve üyelik aksiyonlarını tek ekrandan yönet.</p></div><div class="top-actions"><a class="btn" href="/admin/users">Kullanıcılar</a><a class="btn" href="/admin/memberships">Ödemeler</a><a class="btn primary" href="/admin/center">← Yönetim Merkezi</a></div></div>
<div class="kpis"><div class="kpi blue"><small>Müşteri</small><strong>{summary['total']}</strong></div><div class="kpi teal"><small>Premium</small><strong>{summary['premium']}</strong></div><div class="kpi"><small>FREE</small><strong>{summary['free']}</strong></div><div class="kpi amber"><small>≤7 gün</small><strong>{summary['expiring7']}</strong></div><div class="kpi red"><small>Süresi bitti</small><strong>{summary['expired']}</strong></div><div class="kpi amber"><small>Ödeme bekliyor</small><strong>{summary['pending']}</strong></div></div>
{notices}<div class="toolbar"><div class="chips">{''.join(chips)}</div><form class="search" method="get" action="/admin/lifecycle"><input type="hidden" name="segment" value="{html.escape(segment, quote=True)}"><input name="q" maxlength="60" value="{html.escape(query, quote=True)}" placeholder="Kullanıcı ara..."><select name="sort">{sort_opts}</select><button>Ara / Uygula</button></form></div>
<div class="list">{''.join(cards)}</div><div class="footnote">Yeni kayıt (7 gün): {summary['new7']} · 30 gün içinde bitecek Premium: {summary['expiring30']} · Aksiyon bekleyen: {summary['action']}. Bekleyen ödeme varsa süre manuel eklenmez; önce ödeme kararı verilir.</div></div></body></html>'''


def enhance_admin_center_lifecycle(body: str, summary: dict[str, int]) -> str:
    if 'id="v37LifecycleShortcut"' in body:
        return body
    link = (
        f'<a id="v37LifecycleShortcut" href="/admin/lifecycle"><b>↻ Müşteri Yaşam Döngüsü</b>'
        f'<span>{int(summary.get("action") or 0)} aksiyon · {int(summary.get("expiring7") or 0)} yakında bitecek · {int(summary.get("pending") or 0)} ödeme bekliyor</span></a>'
    )
    marker = '<div class="quick">'
    if marker in body:
        return body.replace(marker, marker + link, 1)
    return body


def _safe_return_params(form: dict[str, str]) -> str:
    segment = form.get("segment", "action") if form.get("segment", "action") in VALID_SEGMENTS else "action"
    sort = form.get("sort", "attention") if form.get("sort", "attention") in VALID_SORTS else "attention"
    query = str(form.get("q") or "").strip()[:60]
    return urllib.parse.urlencode({"segment": segment, "q": query, "sort": sort})


def _has_pending_payment(store: commercial.CommercialAccountStore, username: str) -> bool:
    key = str(username or "").casefold()
    try:
        return any(
            isinstance(row, dict)
            and str(row.get("username") or "").casefold() == key
            and str(row.get("status") or "").upper() == commercial.PAYMENT_PENDING
            for row in store.list_payments()
        )
    except accounts.AccountStoreError:
        return False


def make_v37_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    settings = billing._settings()
    BaseHandler = business.make_v36_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V37Handler(BaseHandler):
        server_version = "KriptoPanel/3.7"

        def _lifecycle_redirect(self, form: dict[str, str], *, message: str = "", error: str = "") -> None:
            params = urllib.parse.parse_qs(_safe_return_params(form))
            flat = {key: values[0] for key, values in params.items() if values}
            if message:
                flat["message"] = message[:220]
            if error:
                flat["error"] = error[:220]
            self._redirect("/admin/lifecycle?" + urllib.parse.urlencode(flat))

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION, "lifecycle_center": True, "renewal_actions": True, "created_at_fix": True, "signal_engine": "unchanged"})
                return
            if path == "/admin/lifecycle":
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                query_data = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=8)
                segment = (query_data.get("segment") or ["action"])[0]
                q = (query_data.get("q") or [""])[0]
                sort = (query_data.get("sort") or ["attention"])[0]
                message = (query_data.get("message") or [""])[0]
                error = (query_data.get("error") or [""])[0]
                body = lifecycle_page(store, session, settings, segment=segment, query=q, sort=sort, message=message, error=error)
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            if path == "/admin/center":
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                base = adminux.admin_center_page(config, store, service, session, settings)
                base = business.enhance_admin_business(base, business.build_business_metrics(store))
                rows = build_lifecycle_rows(store)
                body = enhance_admin_center_lifecycle(base, lifecycle_summary(rows))
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            return super().do_GET()

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/admin/lifecycle/plan":
                form = self._form()
                session = self._csrf_admin(form)
                if not session:
                    return
                username = str(form.get("username") or "")
                if _has_pending_payment(store, username):
                    self._lifecycle_redirect(form, error="Bu kullanıcıda bekleyen ödeme var. Önce ödeme bildirimini sonuçlandırın.")
                    return
                plan = str(form.get("plan") or "").upper()
                try:
                    days = max(1, min(int(form.get("days") or settings["days"]), 3650))
                    store.set_plan(username, plan, days=days, actor=str(session.get("username") or config.username))
                except (ValueError, TypeError, accounts.AccountStoreError) as exc:
                    self._lifecycle_redirect(form, error=str(exc))
                    return
                if hasattr(sessions, "delete_username"):
                    sessions.delete_username(username)
                label = f"{username}: Premium +{days} gün" if plan == commercial.PLAN_PREMIUM else f"{username}: FREE"
                self._lifecycle_redirect(form, message=label + " olarak güncellendi.")
                return
            return super().do_POST()

    return V37Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.7 müşteri yaşam döngüsü.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = lifecycle_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v37_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} lifecycle=1 renewal=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
