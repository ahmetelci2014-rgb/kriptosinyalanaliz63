"""Kripto Kontrol Paneli V1.8 - ürünleşmiş üyelik merkezi.

Bu katman V1.7 çoklu kullanıcı yönetimini genişletir:
- Üyeler kendi hesap/üyelik durumunu görebilir.
- Yönetici aktif/pasif/süresi dolan kullanıcı özetini görür.
- Dinamik kullanıcılar güvenli onayla tamamen silinebilir.
- Sinyal stratejileri, Telegram ve borsa/emir akışı değişmez.
"""

from __future__ import annotations

import argparse
import html
import math
import os
import secrets
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as v17
from dashboard_builder import render_dashboard
from dashboard_live_app import (
    LOGIN_CSRF_COOKIE,
    ROLE_ADMIN,
    ROLE_MEMBER,
    SESSION_COOKIE,
    LoginRateLimiter,
    OKXMarketDataClient,
    PanelConfig,
    build_service,
    cookie_value,
)

VERSION = "KRIPTO_KONTROL_PANELI_LIVE_V1_8_2026_08_14"


class ProductAccountStore(v17.GitHubAccountStore):
    """V1.7 kullanıcı deposuna ürün ekranlarının ihtiyaç duyduğu işlemleri ekler."""

    def get_user(self, username: str) -> dict[str, Any] | None:
        key = str(username or "").casefold()
        try:
            users = self.list_users()
        except v17.AccountStoreError:
            return None
        return next(
            (row for row in users if str(row.get("username") or "").casefold() == key),
            None,
        )

    def delete_user(self, username: str, *, actor: str) -> None:
        username = v17._normalize_username(username)
        key = username.casefold()
        with self._lock:
            _users, document, sha = self._users_unlocked()
            raw_users = document.get("users", [])
            if not isinstance(raw_users, list):
                raise v17.AccountStoreError("Kullanıcı listesi bozuk.")
            index = next(
                (
                    i
                    for i, row in enumerate(raw_users)
                    if isinstance(row, dict)
                    and str(row.get("username") or "").casefold() == key
                ),
                None,
            )
            if index is None:
                raise ValueError("Kullanıcı bulunamadı.")
            raw_users.pop(index)
            self._save_unlocked(
                document,
                sha,
                actor=actor,
                action=f"delete {username}",
            )


def account_store_from_env(config: PanelConfig) -> ProductAccountStore:
    return ProductAccountStore(
        config.repository,
        os.getenv("GITHUB_PANEL_USERS_TOKEN"),
        ref=os.getenv("PANEL_USERS_REF", v17.USERS_REF_DEFAULT),
        path=os.getenv("PANEL_USERS_PATH", v17.USERS_PATH_DEFAULT),
    )


def account_summary(users: list[dict[str, Any]]) -> dict[str, int]:
    now = int(time.time())
    summary = {
        "total": len(users),
        "active": 0,
        "passive": 0,
        "expired": 0,
        "expiring_7d": 0,
        "admins": 0,
        "members": 0,
    }
    for row in users:
        role = str(row.get("role") or ROLE_MEMBER).upper()
        if role == ROLE_ADMIN:
            summary["admins"] += 1
        else:
            summary["members"] += 1
        if not row.get("active", False):
            summary["passive"] += 1
        elif row.get("expired", False):
            summary["expired"] += 1
        else:
            summary["active"] += 1
            expires_at = row.get("expires_at")
            try:
                expires = int(expires_at) if expires_at else 0
            except (TypeError, ValueError):
                expires = 0
            if expires and 0 < expires - now <= 7 * 86_400:
                summary["expiring_7d"] += 1
    return summary


def membership_remaining(expires_at: Any) -> str:
    if not expires_at:
        return "Süresiz"
    try:
        remaining = int(expires_at) - int(time.time())
    except (TypeError, ValueError):
        return "—"
    if remaining <= 0:
        return "Süresi doldu"
    days = max(1, math.ceil(remaining / 86_400))
    return f"{days} gün kaldı"


def account_profile_page(store: ProductAccountStore, session: dict[str, Any]) -> str:
    username_raw = str(session.get("username") or "üye")
    role_raw = str(session.get("role") or ROLE_MEMBER).upper()
    row = store.get_user(username_raw)

    if row:
        status = v17.account_status(row)
        expires = v17.tr_datetime(row.get("expires_at"))
        remaining = membership_remaining(row.get("expires_at"))
        managed = "Dinamik üyelik hesabı"
        created = v17.tr_datetime(row.get("created_at"))
        updated = v17.tr_datetime(row.get("updated_at"))
        role = str(row.get("role") or role_raw).upper()
    else:
        status = "AKTİF"
        expires = "Süresiz"
        remaining = "Kurucu / ortam hesabı"
        managed = "Kurucu veya uyumluluk hesabı"
        created = "—"
        updated = "—"
        role = role_raw

    username = html.escape(username_raw)
    role_label = "Yönetici" if role == ROLE_ADMIN else "Üye"
    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Kripto Kontrol · Hesabım</title>
  <style>
    :root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#86a5a1;--teal:#2ce6bf}}
    *{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}
    .shell{{width:min(900px,calc(100% - 28px));margin:0 auto;padding:30px 0 60px}}
    .top{{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap}}
    h1{{margin:0;font-size:28px}}p{{color:var(--muted)}}a{{color:var(--teal);font-weight:800;text-decoration:none}}
    .card{{margin-top:20px;background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:22px}}
    .grid{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}}
    .item{{border:1px solid var(--line);border-radius:12px;padding:14px;background:rgba(255,255,255,.015)}}
    .item small{{display:block;color:var(--muted);margin-bottom:4px;text-transform:uppercase;font-size:10px;letter-spacing:.06em}}
    .item strong{{font-size:17px}}.pill{{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:6px 9px}}
    .note{{font-size:12px;color:var(--muted);margin-top:18px}}
    @media(max-width:650px){{.grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
<div class="shell">
  <div class="top">
    <div><h1>Hesabım</h1><p>Kripto Kontrol Merkezi üyelik bilgileri</p></div>
    <a href="/">← Kontrol merkezine dön</a>
  </div>
  <div class="card">
    <div class="grid">
      <div class="item"><small>Kullanıcı</small><strong>{username}</strong></div>
      <div class="item"><small>Rol</small><strong>{html.escape(role_label)}</strong></div>
      <div class="item"><small>Durum</small><span class="pill">{html.escape(status)}</span></div>
      <div class="item"><small>Kalan süre</small><strong>{html.escape(remaining)}</strong></div>
      <div class="item"><small>Bitiş</small><strong>{html.escape(expires)}</strong></div>
      <div class="item"><small>Hesap türü</small><strong>{html.escape(managed)}</strong></div>
      <div class="item"><small>Oluşturulma</small><strong>{html.escape(created)}</strong></div>
      <div class="item"><small>Son yönetim işlemi</small><strong>{html.escape(updated)}</strong></div>
    </div>
    <p class="note">Şifre ve üyelik süresi değişiklikleri yönetici tarafından yapılır. Bu ekran işlem açmaz, sinyal üretmez ve Telegram ayarlarını değiştirmez.</p>
  </div>
</div>
</body>
</html>"""


def enhanced_admin_users_page(
    config: PanelConfig,
    store: ProductAccountStore,
    session: dict[str, Any],
    *,
    message: str | None = None,
    error: str | None = None,
) -> str:
    body = v17.admin_users_page(
        config,
        store,
        session,
        message=message,
        error=error,
    )
    try:
        users = store.list_users()
    except v17.AccountStoreError:
        users = []
    summary = account_summary(users)
    summary_html = f"""
  <div class="card">
    <h2>Üyelik özeti</h2>
    <div class="grid">
      <div><strong>{summary['total']}</strong><p>Toplam dinamik hesap</p></div>
      <div><strong>{summary['active']}</strong><p>Aktif</p></div>
      <div><strong>{summary['passive']}</strong><p>Pasif</p></div>
      <div><strong>{summary['expired']}</strong><p>Süresi dolan</p></div>
    </div>
    <p class="footnote">7 gün içinde süresi bitecek: <strong>{summary['expiring_7d']}</strong> · Yönetici: <strong>{summary['admins']}</strong> · Üye: <strong>{summary['members']}</strong></p>
  </div>
"""
    marker = '  <div class="card">\n    <h2>Depo durumu</h2>'
    if marker in body:
        body = body.replace(marker, summary_html + marker, 1)

    if users:
        csrf = html.escape(str(session.get("csrf") or ""), quote=True)
        options = "".join(
            f'<option value="{html.escape(str(row["username"]), quote=True)}">{html.escape(str(row["username"]))}</option>'
            for row in users
        )
        delete_card = f"""
  <div class="card">
    <h2>Kullanıcıyı tamamen sil</h2>
    <p>Test veya artık kullanılmayan dinamik hesabı kaldırır. İşlem geri alınamaz; kullanıcının açık panel oturumları da kapatılır.</p>
    <form method="post" action="/admin/users/delete" class="inline-forms">
      <input type="hidden" name="csrf" value="{csrf}">
      <select name="username" required>{options}</select>
      <input name="confirm_username" placeholder="Kullanıcı adını tekrar yazın" required>
      <button type="submit">Hesabı tamamen sil</button>
    </form>
  </div>
"""
        closing = "\n</div>\n</body>"
        if closing in body:
            body = body.replace(closing, delete_card + closing, 1)
    return body


def make_v18_handler(
    config: PanelConfig,
    service,
    sessions: v17.SessionStore,
    limiter: LoginRateLimiter,
    store: ProductAccountStore,
    market_client: OKXMarketDataClient | None = None,
):
    market_client = market_client or OKXMarketDataClient()
    BaseHandler = v17.make_v17_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
    )

    class V18Handler(BaseHandler):
        server_version = "KriptoPanel/1.8"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            csrf = html.escape(str(session["csrf"]), quote=True)
            role = str(session.get("role") or ROLE_MEMBER).upper()
            role_label = "Yönetici" if role == ROLE_ADMIN else "Üye"
            username = html.escape(str(session.get("username") or "üye"))
            admin_link = (
                '<a class="badge" href="/admin/users">Kullanıcılar</a>'
                if role == ROLE_ADMIN
                else ""
            )
            profile_link = '<a class="badge" href="/account">Hesabım</a>'
            account_badge = f'<span class="badge">{role_label} · {username}</span>'
            logout = (
                '<form method="post" action="/logout">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                '<button class="badge" type="submit">Çıkış</button>'
                "</form>"
            )
            nonce = secrets.token_urlsafe(18)
            body = render_dashboard(
                None,
                live_endpoint="/api/dashboard",
                market_endpoint="/api/market/candles",
                refresh_seconds=config.refresh_seconds,
                script_nonce=nonce,
                top_action_html=admin_link + profile_link + account_badge + logout,
            )
            self._send(
                HTTPStatus.OK,
                body,
                "text/html; charset=utf-8",
                nonce=nonce,
            )

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            if path == "/account":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                self._send(
                    HTTPStatus.OK,
                    account_profile_page(store, session),
                    "text/html; charset=utf-8",
                )
                return
            if path == "/admin/users":
                session = self._admin_session()
                if not session:
                    self._redirect("/login" if not self._session() else "/")
                    return
                query = urllib.parse.parse_qs(
                    parsed.query,
                    keep_blank_values=False,
                    max_num_fields=4,
                )
                message = (query.get("message") or [None])[0]
                error = (query.get("error") or [None])[0]
                self._send(
                    HTTPStatus.OK,
                    enhanced_admin_users_page(
                        config,
                        store,
                        session,
                        message=message,
                        error=error,
                    ),
                    "text/html; charset=utf-8",
                )
                return
            return super().do_GET()

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/admin/users/delete":
                form = self._form()
                session = self._csrf_admin(form)
                if not session:
                    return
                actor = str(session.get("username") or config.username)
                target = str(form.get("username") or "").strip()
                confirm = str(form.get("confirm_username") or "").strip()
                try:
                    if not target or target.casefold() != confirm.casefold():
                        raise ValueError("Silme onayı için kullanıcı adını aynı şekilde tekrar yazın.")
                    if target.casefold() == actor.casefold():
                        raise ValueError("Kendi hesabınızı bu ekrandan silemezsiniz.")
                    store.delete_user(target, actor=actor)
                    if hasattr(sessions, "delete_username"):
                        sessions.delete_username(target)
                    self._admin_redirect(message="Kullanıcı tamamen silindi; açık oturumları kapatıldı.")
                except (ValueError, v17.AccountStoreError) as exc:
                    self._admin_redirect(error=str(exc))
                return
            return super().do_POST()

    return V18Handler


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kripto Kontrol Paneli V1.8 üyelik merkezi."
    )
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8080")),
    )
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = account_store_from_env(config)
    handler = make_v18_handler(
        config,
        service,
        sessions,
        limiter,
        store,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"{VERSION} http://{args.host}:{args.port} "
        f"users_ref={store.ref} users_store={'on' if store.configured else 'off'}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
