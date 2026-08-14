"""Kripto Kontrol Paneli V1.7 - çoklu kullanıcı yönetimi.

Bu katman mevcut V1.6 canlı panelini değiştirmeden sarmalar:
- Ortam değişkenindeki yönetici hesabı her zaman acil/kurucu hesap olarak kalır.
- Ek kullanıcılar ayrı bir private GitHub veri dalındaki JSON dosyasında tutulur.
- Yalnız PBKDF2 şifre özetleri saklanır; düz şifre ve token tarayıcıya gönderilmez.
- Yönetici kullanıcı ekleyebilir, devre dışı bırakabilir, süre uzatabilir ve şifre sıfırlayabilir.
- Sinyal stratejileri, Telegram ve borsa/emir akışı değişmez.
"""

from __future__ import annotations

import argparse
import base64
import copy
import html
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from dashboard_builder import render_dashboard
from dashboard_live_app import (
    LOGIN_CSRF_COOKIE,
    ROLE_ADMIN,
    ROLE_MEMBER,
    SESSION_COOKIE,
    LoginRateLimiter,
    OKXMarketDataClient,
    PanelConfig,
    SessionStore,
    authenticate_account,
    build_service,
    cookie_value,
    make_handler,
    password_hash,
    verify_password,
)

VERSION = "KRIPTO_KONTROL_PANELI_LIVE_V1_7_2026_08_14"
USERS_PATH_DEFAULT = "panel_users.json"
USERS_REF_DEFAULT = "panel-users"
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,40}$")
ALLOWED_ROLES = {ROLE_ADMIN, ROLE_MEMBER}
MIN_PASSWORD_LENGTH = 10
MAX_USERS = 500


class AccountStoreError(RuntimeError):
    """Kullanıcı deposu güvenli biçimde okunamadığında/yazılamadığında."""


def _utc_now() -> int:
    return int(time.time())


def _normalize_role(value: str) -> str:
    role = str(value or ROLE_MEMBER).strip().upper()
    if role not in ALLOWED_ROLES:
        raise ValueError("Rol ADMIN veya MEMBER olmalıdır.")
    return role


def _normalize_username(value: str) -> str:
    username = str(value or "").strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "Kullanıcı adı 3-40 karakter olmalı; yalnız harf, rakam, nokta, alt çizgi ve tire kullanılabilir."
        )
    return username


def _normalize_password(value: str) -> str:
    password = str(value or "")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Şifre en az {MIN_PASSWORD_LENGTH} karakter olmalıdır.")
    if len(password) > 256:
        raise ValueError("Şifre çok uzun.")
    return password


def _normalize_expiry_days(value: Any) -> int | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        days = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("Üyelik süresi gün olarak sayı olmalıdır.") from exc
    if days < 1 or days > 3650:
        raise ValueError("Üyelik süresi 1 ile 3650 gün arasında olmalıdır.")
    return days


class GitHubAccountStore:
    """Ek panel hesaplarını private GitHub veri dalında saklar."""

    def __init__(
        self,
        repository: str,
        token: str | None,
        *,
        ref: str = USERS_REF_DEFAULT,
        path: str = USERS_PATH_DEFAULT,
        timeout_seconds: int = 20,
    ):
        self.repository = str(repository or "").strip()
        self.token = str(token or "").strip() or None
        self.ref = str(ref or USERS_REF_DEFAULT).strip() or USERS_REF_DEFAULT
        self.path = str(path or USERS_PATH_DEFAULT).strip() or USERS_PATH_DEFAULT
        self.timeout_seconds = max(5, min(int(timeout_seconds), 30))
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.token and "/" in self.repository)

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise AccountStoreError("Kullanıcı yönetimi tokeni tanımlı değil.")
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "Kripto-Kontrol-Paneli-Accounts/1.7",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }

    def _contents_url(self) -> str:
        path = urllib.parse.quote(self.path, safe="/")
        return f"https://api.github.com/repos/{self.repository}/contents/{path}"

    @staticmethod
    def _empty_document() -> dict[str, Any]:
        return {"version": 1, "users": [], "updated_at": 0}

    def _decode_document(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
        raw_content = payload.get("content")
        if not isinstance(raw_content, str):
            raise AccountStoreError("Kullanıcı dosyası içeriği okunamadı.")
        try:
            decoded = base64.b64decode(raw_content.encode("ascii")).decode("utf-8")
            document = json.loads(decoded)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccountStoreError("Kullanıcı dosyası bozuk.") from exc
        if not isinstance(document, dict):
            raise AccountStoreError("Kullanıcı dosyası nesne biçiminde değil.")
        users = document.get("users")
        if not isinstance(users, list):
            raise AccountStoreError("Kullanıcı listesi bozuk.")
        return document, str(payload.get("sha") or "") or None

    def _load_unlocked(self) -> tuple[dict[str, Any], str | None]:
        if not self.configured:
            return self._empty_document(), None
        query = urllib.parse.urlencode({"ref": self.ref})
        request = urllib.request.Request(
            f"{self._contents_url()}?{query}",
            headers=self._headers(),
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == HTTPStatus.NOT_FOUND:
                return self._empty_document(), None
            raise AccountStoreError(f"Kullanıcı deposu GitHub HTTP {exc.code}.") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccountStoreError(
                f"Kullanıcı deposu okunamadı ({type(exc).__name__})."
            ) from exc
        if not isinstance(payload, dict):
            raise AccountStoreError("Kullanıcı deposu geçersiz yanıt verdi.")
        return self._decode_document(payload)

    def _save_unlocked(
        self,
        document: dict[str, Any],
        sha: str | None,
        *,
        actor: str,
        action: str,
    ) -> None:
        if not self.configured:
            raise AccountStoreError(
                "Kullanıcı yönetimi için GITHUB_PANEL_USERS_TOKEN tanımlanmalıdır."
            )
        document = copy.deepcopy(document)
        document["version"] = 1
        document["updated_at"] = _utc_now()
        body: dict[str, Any] = {
            "message": f"Panel users: {action} by {actor}",
            "content": base64.b64encode(
                (
                    json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)
                    + "\n"
                ).encode("utf-8")
            ).decode("ascii"),
            "branch": self.ref,
        }
        if sha:
            body["sha"] = sha
        request = urllib.request.Request(
            self._contents_url(),
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=self._headers(),
            method="PUT",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise AccountStoreError(f"Kullanıcı deposu GitHub HTTP {exc.code}.") from exc
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AccountStoreError(
                f"Kullanıcı deposu yazılamadı ({type(exc).__name__})."
            ) from exc
        if not isinstance(payload, dict) or not payload.get("commit"):
            raise AccountStoreError("Kullanıcı deposu güncellemesi doğrulanamadı.")

    @staticmethod
    def _normalized_user(row: dict[str, Any]) -> dict[str, Any]:
        username = _normalize_username(row.get("username", ""))
        role = _normalize_role(row.get("role", ROLE_MEMBER))
        encoded = str(row.get("password_hash") or "")
        if not encoded.startswith("pbkdf2_sha256$"):
            raise AccountStoreError(f"{username}: şifre özeti geçersiz.")
        now = _utc_now()
        expires_at_raw = row.get("expires_at")
        try:
            expires_at = int(expires_at_raw) if expires_at_raw not in (None, "") else None
        except (TypeError, ValueError):
            expires_at = None
        active = bool(row.get("active", True))
        expired = bool(expires_at and expires_at <= now)
        return {
            "username": username,
            "role": role,
            "password_hash": encoded,
            "active": active,
            "expired": expired,
            "expires_at": expires_at,
            "created_at": int(row.get("created_at") or 0),
            "updated_at": int(row.get("updated_at") or 0),
            "created_by": str(row.get("created_by") or ""),
            "updated_by": str(row.get("updated_by") or ""),
        }

    def _users_unlocked(self) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
        document, sha = self._load_unlocked()
        users: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in document.get("users", []):
            if not isinstance(raw, dict):
                continue
            user = self._normalized_user(raw)
            key = user["username"].casefold()
            if key in seen:
                raise AccountStoreError("Kullanıcı dosyasında yinelenen kullanıcı adı var.")
            seen.add(key)
            users.append(user)
        if len(users) > MAX_USERS:
            raise AccountStoreError("Kullanıcı sayısı güvenlik sınırını aşıyor.")
        return users, document, sha

    def list_users(self) -> list[dict[str, Any]]:
        with self._lock:
            users, _document, _sha = self._users_unlocked()
        rows: list[dict[str, Any]] = []
        for user in users:
            sanitized = dict(user)
            sanitized.pop("password_hash", None)
            rows.append(sanitized)
        return sorted(rows, key=lambda row: row["username"].casefold())

    def authenticate(self, username: str, password: str) -> dict[str, str] | None:
        if not self.configured:
            return None
        try:
            with self._lock:
                users, _document, _sha = self._users_unlocked()
        except AccountStoreError:
            return None
        candidate_key = str(username or "").casefold()
        user = next(
            (
                row
                for row in users
                if row["username"].casefold() == candidate_key
            ),
            None,
        )
        if not user or not user["active"] or user["expired"]:
            return None
        if not verify_password(password, user["password_hash"], None):
            return None
        return {"username": user["username"], "role": user["role"]}

    def create_user(
        self,
        username: str,
        password: str,
        *,
        role: str,
        expiry_days: Any,
        actor: str,
        reserved_usernames: set[str] | None = None,
    ) -> None:
        username = _normalize_username(username)
        password = _normalize_password(password)
        role = _normalize_role(role)
        days = _normalize_expiry_days(expiry_days)
        reserved = {item.casefold() for item in (reserved_usernames or set())}
        if username.casefold() in reserved:
            raise ValueError("Bu kullanıcı adı kurucu/ortam hesabı tarafından kullanılıyor.")
        with self._lock:
            users, document, sha = self._users_unlocked()
            if len(users) >= MAX_USERS:
                raise ValueError("Maksimum kullanıcı sayısına ulaşıldı.")
            if any(user["username"].casefold() == username.casefold() for user in users):
                raise ValueError("Bu kullanıcı adı zaten var.")
            now = _utc_now()
            expires_at = now + days * 86_400 if days else None
            document.setdefault("users", []).append(
                {
                    "username": username,
                    "password_hash": password_hash(password),
                    "role": role,
                    "active": True,
                    "expires_at": expires_at,
                    "created_at": now,
                    "updated_at": now,
                    "created_by": actor,
                    "updated_by": actor,
                }
            )
            self._save_unlocked(
                document,
                sha,
                actor=actor,
                action=f"create {username}",
            )

    def _find_raw_user(
        self,
        document: dict[str, Any],
        username: str,
    ) -> dict[str, Any]:
        key = _normalize_username(username).casefold()
        for raw in document.get("users", []):
            if isinstance(raw, dict) and str(raw.get("username") or "").casefold() == key:
                return raw
        raise ValueError("Kullanıcı bulunamadı.")

    def set_active(self, username: str, active: bool, *, actor: str) -> None:
        username = _normalize_username(username)
        with self._lock:
            _users, document, sha = self._users_unlocked()
            raw = self._find_raw_user(document, username)
            raw["active"] = bool(active)
            raw["updated_at"] = _utc_now()
            raw["updated_by"] = actor
            self._save_unlocked(
                document,
                sha,
                actor=actor,
                action=("enable " if active else "disable ") + username,
            )

    def reset_password(self, username: str, password: str, *, actor: str) -> None:
        username = _normalize_username(username)
        password = _normalize_password(password)
        with self._lock:
            _users, document, sha = self._users_unlocked()
            raw = self._find_raw_user(document, username)
            raw["password_hash"] = password_hash(password)
            raw["updated_at"] = _utc_now()
            raw["updated_by"] = actor
            self._save_unlocked(
                document,
                sha,
                actor=actor,
                action=f"reset-password {username}",
            )

    def set_expiry(self, username: str, expiry_days: Any, *, actor: str) -> None:
        username = _normalize_username(username)
        days = _normalize_expiry_days(expiry_days)
        with self._lock:
            _users, document, sha = self._users_unlocked()
            raw = self._find_raw_user(document, username)
            raw["expires_at"] = _utc_now() + days * 86_400 if days else None
            raw["updated_at"] = _utc_now()
            raw["updated_by"] = actor
            self._save_unlocked(
                document,
                sha,
                actor=actor,
                action=f"expiry {username}",
            )

    def set_role(self, username: str, role: str, *, actor: str) -> None:
        username = _normalize_username(username)
        role = _normalize_role(role)
        with self._lock:
            _users, document, sha = self._users_unlocked()
            raw = self._find_raw_user(document, username)
            raw["role"] = role
            raw["updated_at"] = _utc_now()
            raw["updated_by"] = actor
            self._save_unlocked(
                document,
                sha,
                actor=actor,
                action=f"role {username}={role}",
            )


def account_store_from_env(config: PanelConfig) -> GitHubAccountStore:
    return GitHubAccountStore(
        config.repository,
        os.getenv("GITHUB_PANEL_USERS_TOKEN"),
        ref=os.getenv("PANEL_USERS_REF", USERS_REF_DEFAULT),
        path=os.getenv("PANEL_USERS_PATH", USERS_PATH_DEFAULT),
    )


class ManagedSessionStore(SessionStore):
    """Hesap yönetimi değişikliklerinde kullanıcı oturumlarını iptal edebilir."""

    def delete_username(self, username: str) -> None:
        key = str(username or "").casefold()
        with self._lock:
            tokens = [
                token
                for token, session in self._sessions.items()
                if str(session.get("username") or "").casefold() == key
            ]
            for token in tokens:
                self._sessions.pop(token, None)


def account_status(row: dict[str, Any]) -> str:
    if not row.get("active", False):
        return "PASİF"
    if row.get("expired", False):
        return "SÜRESİ DOLDU"
    return "AKTİF"


def tr_datetime(timestamp: Any) -> str:
    if not timestamp:
        return "Süresiz"
    try:
        value = int(timestamp) + 3 * 3600
    except (TypeError, ValueError):
        return "—"
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(value))


def admin_users_page(
    config: PanelConfig,
    store: GitHubAccountStore,
    session: dict[str, Any],
    *,
    message: str | None = None,
    error: str | None = None,
) -> str:
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    actor = html.escape(str(session.get("username") or "admin"))
    try:
        users = store.list_users()
        store_error = None
    except AccountStoreError as exc:
        users = []
        store_error = str(exc)
    if store_error and not error:
        error = store_error

    rows: list[str] = []
    for row in users:
        username = html.escape(str(row["username"]))
        username_attr = html.escape(str(row["username"]), quote=True)
        role = html.escape(str(row["role"]))
        status = account_status(row)
        expires = tr_datetime(row.get("expires_at"))
        updated = tr_datetime(row.get("updated_at"))
        toggle_action = "enable" if status == "PASİF" else "disable"
        toggle_label = "Aktifleştir" if toggle_action == "enable" else "Pasifleştir"
        role_target = ROLE_MEMBER if row["role"] == ROLE_ADMIN else ROLE_ADMIN
        role_label = "Üye yap" if role_target == ROLE_MEMBER else "Yönetici yap"
        rows.append(
            f"""
            <tr>
              <td><strong>{username}</strong><small>Son güncelleme: {html.escape(updated)}</small></td>
              <td><span class="pill">{role}</span></td>
              <td><span class="pill">{html.escape(status)}</span></td>
              <td>{html.escape(expires)}</td>
              <td class="actions">
                <form method="post" action="/admin/users/toggle">
                  <input type="hidden" name="csrf" value="{csrf}">
                  <input type="hidden" name="username" value="{username_attr}">
                  <input type="hidden" name="action" value="{toggle_action}">
                  <button type="submit">{toggle_label}</button>
                </form>
                <form method="post" action="/admin/users/role">
                  <input type="hidden" name="csrf" value="{csrf}">
                  <input type="hidden" name="username" value="{username_attr}">
                  <input type="hidden" name="role" value="{role_target}">
                  <button type="submit">{role_label}</button>
                </form>
              </td>
            </tr>
            <tr class="secondary">
              <td colspan="5">
                <div class="inline-forms">
                  <form method="post" action="/admin/users/password">
                    <input type="hidden" name="csrf" value="{csrf}">
                    <input type="hidden" name="username" value="{username_attr}">
                    <input name="password" type="password" minlength="{MIN_PASSWORD_LENGTH}" maxlength="256" placeholder="Yeni şifre" required>
                    <button type="submit">Şifreyi sıfırla</button>
                  </form>
                  <form method="post" action="/admin/users/expiry">
                    <input type="hidden" name="csrf" value="{csrf}">
                    <input type="hidden" name="username" value="{username_attr}">
                    <input name="expiry_days" type="number" min="1" max="3650" placeholder="Gün (boş = süresiz)">
                    <button type="submit">Süreyi güncelle</button>
                  </form>
                </div>
              </td>
            </tr>
            """
        )
    if not rows:
        rows.append(
            '<tr><td colspan="5" class="empty">Henüz dinamik kullanıcı yok. İlk hesabı aşağıdaki formdan ekleyin.</td></tr>'
        )

    notice = ""
    if message:
        notice += f'<div class="notice ok">{html.escape(message)}</div>'
    if error:
        notice += f'<div class="notice error">{html.escape(error)}</div>'

    store_state = (
        "Hazır · private veri dalına güvenli yazma açık"
        if store.configured
        else "Salt-okunur · GITHUB_PANEL_USERS_TOKEN henüz tanımlı değil"
    )
    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Kripto Kontrol · Kullanıcılar</title>
  <style>
    :root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#86a5a1;--teal:#2ce6bf;--red:#ff748c}}
    *{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}
    .shell{{width:min(1180px,calc(100% - 28px));margin:0 auto;padding:28px 0 60px}}
    .top{{display:flex;gap:12px;justify-content:space-between;align-items:center;flex-wrap:wrap;margin-bottom:22px}}
    h1{{margin:0;font-size:28px}} h2{{font-size:18px;margin:0 0 16px}} p{{color:var(--muted)}}
    .card{{background:var(--panel);border:1px solid var(--line);border-radius:18px;padding:20px;margin-top:18px}}
    .badge,.pill{{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:6px 9px;font-size:11px}}
    a{{color:var(--teal);text-decoration:none;font-weight:800}} .top-actions{{display:flex;gap:10px;align-items:center}}
    table{{width:100%;border-collapse:collapse}} th,td{{padding:12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}
    th{{color:var(--muted);font-size:11px;text-transform:uppercase}} td small{{display:block;color:var(--muted);margin-top:4px}}
    .actions,.inline-forms{{display:flex;gap:8px;flex-wrap:wrap}} form{{margin:0}}
    input,select,button{{background:#07151c;border:1px solid var(--line);color:var(--text);border-radius:9px;padding:9px 10px}}
    button{{cursor:pointer;font-weight:800}} button:hover{{border-color:var(--teal)}} .grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}}
    .grid button{{background:var(--teal);color:#04110e;border-color:transparent}}
    .notice{{border-radius:10px;padding:10px 12px;margin:10px 0}} .notice.ok{{border:1px solid rgba(44,230,191,.35);background:rgba(44,230,191,.08)}} .notice.error{{border:1px solid rgba(255,116,140,.35);background:rgba(255,116,140,.08);color:#ffb3c0}}
    .secondary td{{padding-top:0;background:rgba(255,255,255,.01)}} .empty{{color:var(--muted);text-align:center;padding:25px}}
    .footnote{{font-size:12px;color:var(--muted)}} code{{color:#b7fff0}}
    @media(max-width:800px){{.grid{{grid-template-columns:1fr}} table{{display:block;overflow:auto}}}}
  </style>
</head>
<body>
<div class="shell">
  <div class="top">
    <div><h1>Kullanıcı Yönetimi</h1><p>Çoklu hesap · rol · süre · şifre yönetimi</p></div>
    <div class="top-actions"><span class="badge">Yönetici · {actor}</span><a href="/">← Panele dön</a></div>
  </div>
  {notice}
  <div class="card">
    <h2>Depo durumu</h2>
    <p><strong>{html.escape(store_state)}</strong></p>
    <p class="footnote">Hesaplar <code>{html.escape(store.ref)}</code> dalındaki <code>{html.escape(store.path)}</code> dosyasında yalnız PBKDF2 şifre özetiyle tutulur. Sinyal ve strateji dosyaları değiştirilmez.</p>
  </div>
  <div class="card">
    <h2>Kurucu yönetici</h2>
    <p><span class="pill">ADMIN</span> <strong>{html.escape(config.username)}</strong> · ortam değişkeni hesabı. Acil erişim için korunur ve buradan kapatılamaz.</p>
  </div>
  <div class="card">
    <h2>Yeni kullanıcı ekle</h2>
    <form method="post" action="/admin/users/create" class="grid">
      <input type="hidden" name="csrf" value="{csrf}">
      <input name="username" minlength="3" maxlength="40" placeholder="Kullanıcı adı" required>
      <input name="password" type="password" minlength="{MIN_PASSWORD_LENGTH}" maxlength="256" placeholder="Şifre (en az {MIN_PASSWORD_LENGTH})" required>
      <select name="role"><option value="MEMBER">Üye</option><option value="ADMIN">Yönetici</option></select>
      <input name="expiry_days" type="number" min="1" max="3650" placeholder="Süre (gün, boş=süresiz)">
      <button type="submit">Kullanıcı oluştur</button>
    </form>
  </div>
  <div class="card">
    <h2>Dinamik kullanıcılar</h2>
    <table>
      <thead><tr><th>Kullanıcı</th><th>Rol</th><th>Durum</th><th>Bitiş</th><th>İşlemler</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""


def make_v17_handler(
    config: PanelConfig,
    service,
    sessions: SessionStore,
    limiter: LoginRateLimiter,
    store: GitHubAccountStore,
    market_client: OKXMarketDataClient | None = None,
):
    """V1.6 handler'ını çoklu kullanıcı yönetimiyle genişletir."""
    market_client = market_client or OKXMarketDataClient()
    BaseHandler = make_handler(config, service, sessions, limiter, market_client)

    class V17Handler(BaseHandler):
        server_version = "KriptoPanel/1.7"

        def _admin_session(self) -> dict[str, Any] | None:
            session = self._session()
            if not session or str(session.get("role") or "").upper() != ROLE_ADMIN:
                return None
            return session

        def _csrf_admin(self, form: dict[str, str]) -> dict[str, Any] | None:
            session = self._admin_session()
            if not session:
                self._json(HTTPStatus.FORBIDDEN, {"error": "admin_required"})
                return None
            expected = str(session.get("csrf") or "")
            supplied = str(form.get("csrf") or "")
            import hmac
            if not expected or not hmac.compare_digest(expected, supplied):
                self._json(HTTPStatus.FORBIDDEN, {"error": "csrf_failed"})
                return None
            return session

        def _admin_redirect(self, *, message: str | None = None, error: str | None = None):
            params: dict[str, str] = {}
            if message:
                params["message"] = message[:240]
            if error:
                params["error"] = error[:240]
            suffix = "?" + urllib.parse.urlencode(params) if params else ""
            self._redirect("/admin/users" + suffix)

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            csrf = html.escape(str(session["csrf"]), quote=True)
            role = str(session.get("role") or ROLE_MEMBER).upper()
            role_label = "Yönetici" if role == ROLE_ADMIN else "Üye"
            account_label = html.escape(
                f"{role_label} · {session.get('username') or 'üye'}"
            )
            admin_link = (
                '<a class="badge" href="/admin/users">Kullanıcılar</a>'
                if role == ROLE_ADMIN
                else ""
            )
            account_badge = f'<span class="badge">{account_label}</span>'
            logout = (
                '<form method="post" action="/logout">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                '<button class="badge" type="submit">Çıkış</button>'
                "</form>"
            )
            import secrets
            nonce = secrets.token_urlsafe(18)
            body = render_dashboard(
                None,
                live_endpoint="/api/dashboard",
                market_endpoint="/api/market/candles",
                refresh_seconds=config.refresh_seconds,
                script_nonce=nonce,
                top_action_html=admin_link + account_badge + logout,
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
            if path == "/":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                self._render_root_v17(session)
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
                body = admin_users_page(
                    config,
                    store,
                    session,
                    message=message,
                    error=error,
                )
                self._send(
                    HTTPStatus.OK,
                    body,
                    "text/html; charset=utf-8",
                )
                return
            if path == "/api/admin/users":
                session = self._admin_session()
                if not session:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "admin_required"})
                    return
                try:
                    users = store.list_users()
                except AccountStoreError:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": "account_store_unavailable"},
                    )
                    return
                self._json(
                    HTTPStatus.OK,
                    {
                        "version": VERSION,
                        "users": users,
                        "bootstrap_admin": config.username,
                        "store_configured": store.configured,
                    },
                )
                return
            return super().do_GET()

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/login":
                form = self._form()
                identity = self._client_ip()
                if not limiter.allowed(identity):
                    self._serve_login(
                        "Çok fazla hatalı deneme. 15 dakika sonra tekrar deneyin.",
                        HTTPStatus.TOO_MANY_REQUESTS,
                    )
                    return
                csrf_cookie = self._cookie(LOGIN_CSRF_COOKIE) or ""
                csrf_form = form.get("csrf", "")
                import hmac
                csrf_ok = bool(csrf_cookie) and hmac.compare_digest(
                    csrf_cookie,
                    csrf_form,
                )
                username = form.get("username", "")
                password = form.get("password", "")
                account = store.authenticate(username, password)
                if account is None:
                    account = authenticate_account(config, username, password)
                if not (csrf_ok and account):
                    limiter.record_failure(identity)
                    self._serve_login(
                        "Kullanıcı adı veya şifre hatalı ya da üyelik aktif değil.",
                        HTTPStatus.UNAUTHORIZED,
                        username,
                    )
                    return
                limiter.clear(identity)
                token, _session = sessions.create(
                    account["username"],
                    account["role"],
                )
                self._redirect(
                    "/",
                    cookies=[
                        cookie_value(
                            SESSION_COOKIE,
                            token,
                            max_age=config.session_hours * 3600,
                            secure=config.cookie_secure,
                        ),
                        cookie_value(
                            LOGIN_CSRF_COOKIE,
                            "",
                            max_age=0,
                            secure=config.cookie_secure,
                        ),
                    ],
                )
                return

            if not path.startswith("/admin/users/"):
                return super().do_POST()

            form = self._form()
            session = self._csrf_admin(form)
            if not session:
                return
            actor = str(session.get("username") or config.username)
            try:
                if path == "/admin/users/create":
                    reserved = {config.username}
                    if config.member_username:
                        reserved.add(config.member_username)
                    store.create_user(
                        form.get("username", ""),
                        form.get("password", ""),
                        role=form.get("role", ROLE_MEMBER),
                        expiry_days=form.get("expiry_days", ""),
                        actor=actor,
                        reserved_usernames=reserved,
                    )
                    self._admin_redirect(message="Kullanıcı oluşturuldu.")
                    return
                if path == "/admin/users/toggle":
                    action = form.get("action", "")
                    if action not in {"enable", "disable"}:
                        raise ValueError("Geçersiz kullanıcı işlemi.")
                    target = form.get("username", "")
                    if target.casefold() == actor.casefold() and action == "disable":
                        raise ValueError("Kendi oturumunuzu buradan pasifleştiremezsiniz.")
                    store.set_active(
                        target,
                        action == "enable",
                        actor=actor,
                    )
                    if action == "disable" and hasattr(sessions, "delete_username"):
                        sessions.delete_username(target)
                    self._admin_redirect(
                        message="Kullanıcı durumu güncellendi."
                    )
                    return
                if path == "/admin/users/password":
                    target = form.get("username", "")
                    store.reset_password(
                        target,
                        form.get("password", ""),
                        actor=actor,
                    )
                    if hasattr(sessions, "delete_username"):
                        sessions.delete_username(target)
                    self._admin_redirect(message="Kullanıcı şifresi yenilendi; eski oturumları kapatıldı.")
                    return
                if path == "/admin/users/expiry":
                    store.set_expiry(
                        form.get("username", ""),
                        form.get("expiry_days", ""),
                        actor=actor,
                    )
                    self._admin_redirect(message="Üyelik süresi güncellendi.")
                    return
                if path == "/admin/users/role":
                    target = form.get("username", "")
                    if target.casefold() == actor.casefold():
                        raise ValueError("Kendi yönetici rolünüzü bu ekrandan değiştiremezsiniz.")
                    store.set_role(
                        target,
                        form.get("role", ROLE_MEMBER),
                        actor=actor,
                    )
                    if hasattr(sessions, "delete_username"):
                        sessions.delete_username(target)
                    self._admin_redirect(message="Kullanıcı rolü güncellendi; eski oturumları kapatıldı.")
                    return
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            except (ValueError, AccountStoreError) as exc:
                self._admin_redirect(error=str(exc))

    return V17Handler


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Kripto Kontrol Paneli V1.7 çoklu kullanıcı yönetimi."
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
    sessions = ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = account_store_from_env(config)
    handler = make_v17_handler(
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
