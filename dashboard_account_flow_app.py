"""V3.32.7 hesap güvenliği ve kullanıcı geri bildirimi yardımcıları.

Bu modül trading/sinyal mantığı içermez. Yalnız giriş yapmış kullanıcının mevcut
şifresini doğrulayarak kendi şifresini değiştirmesi, hesap sayfasında güvenlik
bağlantısı ve ödeme bildirimi formunun sabit/kullanıcı-dostu geri bildirimlerini
üretir.
"""
from __future__ import annotations

import html
import time
from typing import Any

import dashboard_accounts_app as accounts
from dashboard_live_app import password_hash, verify_password

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_7_ACCOUNT_FLOW_2026_08_16"


def _esc(value: Any) -> str:
    return html.escape(str(value if value not in (None, "") else "—"))


def managed_account(store, username: str) -> bool:
    """Kullanıcının GitHub panel_users deposunda yönetilen hesap olup olmadığını bulur."""
    try:
        username = accounts._normalize_username(username)
        with store._lock:
            _users, document, _sha = store._users_unlocked()
            store._find_raw_user(document, username)
        return True
    except (ValueError, accounts.AccountStoreError, AttributeError):
        return False


def change_managed_password(
    store,
    username: str,
    current_password: str,
    new_password: str,
    new_password_confirm: str,
) -> None:
    """Mevcut şifreyi doğrular, yeni özeti atomik olarak aynı kullanıcı belgesine yazar."""
    username = accounts._normalize_username(username)
    current_password = str(current_password or "")
    new_password = str(new_password or "")
    new_password_confirm = str(new_password_confirm or "")
    if not current_password:
        raise ValueError("Mevcut şifrenizi girin.")
    if new_password != new_password_confirm:
        raise ValueError("Yeni şifreler eşleşmiyor.")
    new_password = accounts._normalize_password(new_password)

    try:
        with store._lock:
            _users, document, sha = store._users_unlocked()
            raw = store._find_raw_user(document, username)
            encoded = str(raw.get("password_hash") or "")
            if not encoded or not verify_password(current_password, encoded, None):
                raise ValueError("Mevcut şifre doğru değil.")
            if verify_password(new_password, encoded, None):
                raise ValueError("Yeni şifre mevcut şifreyle aynı olmamalıdır.")
            now = int(time.time())
            raw["password_hash"] = password_hash(new_password)
            raw["updated_at"] = now
            raw["updated_by"] = username
            store._save_unlocked(
                document,
                sha,
                actor=username,
                action=f"self-password-change {username}",
            )
    except accounts.AccountStoreError:
        raise


def _extra_css() -> str:
    return r'''
.v3327-security-card{border:1px solid rgba(44,230,191,.24);background:rgba(44,230,191,.035);border-radius:13px;padding:12px;margin:10px 0}.v3327-security-card b{display:block;font-size:12px}.v3327-security-card p{margin:4px 0 9px;color:#819a97;font-size:9px}.v3327-security-card a{display:inline-flex;border:1px solid rgba(44,230,191,.28);border-radius:9px;padding:8px 10px;color:#2ce6bf!important;text-decoration:none;font-size:9px;font-weight:900}.v3327-feedback{width:min(920px,calc(100% - 24px));margin:10px auto;border:1px solid rgba(44,230,191,.28);border-radius:11px;padding:10px 12px;background:rgba(44,230,191,.045);color:#b7d7d1;font-size:10px;font-weight:750}.v3327-feedback.warn{border-color:rgba(255,189,89,.32);background:rgba(255,189,89,.055);color:#d5c59e}.v3327-feedback.err{border-color:rgba(255,98,125,.32);background:rgba(255,98,125,.055);color:#d7aab2}
'''


def _insert_css(body: str) -> str:
    css = _extra_css()
    if ".v3327-security-card" in body:
        return body
    if "</style>" in body:
        return body.replace("</style>", css + "\n</style>", 1)
    return body


def enhance_account_security_link(body: str) -> str:
    if 'id="v3327AccountSecurity"' in body:
        return body
    body = _insert_css(body)
    card = (
        '<div class="v3327-security-card" id="v3327AccountSecurity">'
        '<b>Hesap güvenliği</b>'
        '<p>Mevcut şifreni doğrulayarak panel şifreni değiştirebilirsin. Değişiklikten sonra açık oturumların güvenlik için kapatılır.</p>'
        '<a href="/account/security">Şifremi değiştir</a></div>'
    )
    mobile_marker = '<form class="logout"'
    if mobile_marker in body:
        return body.replace(mobile_marker, card + mobile_marker, 1)
    closing = "</div></body>"
    if closing in body:
        head, tail = body.rsplit(closing, 1)
        return head + card + closing + tail
    return body + card


PAYMENT_FEEDBACK: dict[str, tuple[str, str]] = {
    "sent": ("ok", "Ödeme bildirimin kaydedildi. Yönetici onayından sonra üyelik durumun otomatik olarak güncellenecek."),
    "already_pending": ("warn", "Zaten onay bekleyen bir ödeme bildirimin var. Aynı ödeme için tekrar bildirim göndermene gerek yok."),
    "session": ("err", "Oturum doğrulaması yenilenemedi. Üyelik sayfasını tekrar açıp işlemi yeniden deneyin."),
    "crypto_disabled": ("err", "Kripto ödeme bildirimi şu anda kapalı. Kullanılabilir ödeme yöntemini üyelik sayfasındaki açıklamadan kontrol edin."),
    "store_unavailable": ("err", "Ödeme bildirimi şu anda kaydedilemedi. Daha sonra tekrar deneyin veya yöneticiyle iletişime geçin."),
    "invalid": ("err", "Ödeme bildirimi kaydedilemedi. Bilgileri kontrol edip tekrar deneyin."),
}


def enhance_payment_feedback(body: str, code: str) -> str:
    code = str(code or "").strip().lower()
    item = PAYMENT_FEEDBACK.get(code)
    if not item or 'id="v3327PaymentFeedback"' in body:
        return body
    kind, text = item
    body = _insert_css(body)
    cls = "" if kind == "ok" else " warn" if kind == "warn" else " err"
    block = f'<div class="v3327-feedback{cls}" id="v3327PaymentFeedback">{html.escape(text)}</div>'
    if "<body>" in body:
        return body.replace("<body>", "<body>" + block, 1)
    return block + body


def security_page(
    session: dict[str, Any],
    *,
    managed: bool,
    error: str = "",
    status_text: str = "",
) -> str:
    username = _esc(session.get("username") or "üye")
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    error_html = f'<div class="msg err">{html.escape(error)}</div>' if error else ""
    status_html = f'<div class="msg ok">{html.escape(status_text)}</div>' if status_text else ""
    if managed:
        content = f'''
<form method="post" action="/account/password" class="card">
<h1>Şifremi değiştir</h1><p>Yeni şifreyi kaydetmeden önce mevcut şifren doğrulanır.</p>{status_html}{error_html}
<input type="hidden" name="csrf" value="{csrf}">
<label>Mevcut şifre</label><input type="password" name="current_password" required autocomplete="current-password" maxlength="256">
<label>Yeni şifre</label><input type="password" name="new_password" required autocomplete="new-password" minlength="10" maxlength="256">
<label>Yeni şifre tekrar</label><input type="password" name="new_password_confirm" required autocomplete="new-password" minlength="10" maxlength="256">
<button type="submit">Şifreyi güvenli biçimde değiştir</button>
<div class="note">En az 10 karakter kullan. Başarılı değişiklikten sonra bu kullanıcıya ait tüm açık panel oturumları kapatılır ve yeniden giriş gerekir.</div>
</form>'''
    else:
        content = f'''<div class="card"><h1>Hesap güvenliği</h1>{error_html}<p><strong>{username}</strong> hesabının şifresi panel kullanıcı deposunda yönetilmiyor.</p><div class="msg warn">Kurucu/ortam hesabı şifresi sunucu ortam ayarlarından yönetilir; güvenlik nedeniyle panel içinden değiştirilemez.</div></div>'''
    return f'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="dark"><title>Hesap Güvenliği · Kripto Kontrol</title><style>
:root{{--bg:#071018;--panel:#0c1720;--line:#1d303b;--text:#edf7f5;--muted:#819a97;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:radial-gradient(circle at 85% 0,rgba(44,230,191,.08),transparent 30%),var(--bg);color:var(--text);font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}}.shell{{width:min(560px,calc(100% - 24px));margin:auto;padding:24px 0 60px}}.top{{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:12px}}a{{color:var(--teal);text-decoration:none;font-weight:850}}.user{{color:var(--muted);font-size:9px}}.card{{border:1px solid var(--line);border-radius:17px;background:var(--panel);padding:20px}}h1{{margin:0 0 6px;font-size:25px}}p,.note{{color:var(--muted)}}label{{display:block;margin:12px 0 5px;font-size:10px;font-weight:850}}input{{width:100%;border:1px solid var(--line);border-radius:9px;background:#061219;color:var(--text);padding:11px}}button{{width:100%;border:0;border-radius:10px;background:var(--teal);color:#03110e;padding:12px;margin-top:16px;font-weight:950;cursor:pointer}}.note{{font-size:9px;margin-top:10px}}.msg{{border:1px solid var(--line);border-radius:10px;padding:10px;margin:10px 0;font-size:10px}}.msg.err{{border-color:rgba(255,98,125,.32);background:rgba(255,98,125,.05);color:#d7aab2}}.msg.ok{{border-color:rgba(66,226,140,.28);background:rgba(66,226,140,.04);color:#acd2bb}}.msg.warn{{border-color:rgba(255,189,89,.3);background:rgba(255,189,89,.05);color:#d5c59e}}</style></head><body><div class="shell"><div class="top"><a href="/account">← Hesabım</a><span class="user">{username}</span></div>{content}</div></body></html>'''


def password_changed_page() -> str:
    return '''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark"><title>Şifre değiştirildi</title><style>:root{--bg:#071018;--panel:#0c1720;--line:#1d303b;--text:#edf7f5;--muted:#819a97;--teal:#2ce6bf;--green:#42e28c}*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:var(--bg);color:var(--text);font:14px/1.5 system-ui,sans-serif}.card{width:min(440px,100%);border:1px solid rgba(66,226,140,.3);border-radius:18px;background:var(--panel);padding:24px}.ok{color:var(--green);font-weight:950}.card p{color:var(--muted)}a{display:inline-block;margin-top:8px;border-radius:10px;background:var(--teal);color:#03110e;text-decoration:none;padding:10px 13px;font-weight:950}</style></head><body><div class="card"><div class="ok">✓ Şifre değiştirildi</div><h1>Yeniden giriş yap</h1><p>Güvenlik için bu kullanıcıya ait açık panel oturumları kapatıldı.</p><a href="/login">Giriş ekranına git</a></div></body></html>'''
