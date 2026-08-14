"""Kripto Kontrol Merkezi V3.0 - herkese açık vitrin ve ticari üyelik katmanı.

Bu katman V2.10 panelini değiştirmeden ürünleştirir:
- giriş zorunluluğu olmayan herkese açık güvenli vitrin / istatistik ekranı,
- FREE / PREMIUM / ADMIN plan ayrımı,
- ücretsiz kullanıcı kaydı,
- Premium süresi dolunca hesabı kapatmak yerine FREE plana düşürme,
- Premium API uçlarını sunucu tarafında plan kontrolüyle koruma,
- kullanıcının "Ödeme yaptım" bildirimi bırakabilmesi,
- yöneticinin ödeme bildirimini onaylayıp Premium süre başlatabilmesi.

Kripto ödeme seçeneği altyapıda vardır ancak varsayılan olarak KAPALIDIR.
PANEL_CRYPTO_PAYMENT_ENABLED=1 olmadan kullanıcıya kripto ödeme seçeneği sunulmaz.

Sinyal üretimi, strateji, radar, Telegram ve emir akışı bu dosyada yoktur.
"""

from __future__ import annotations

import argparse
import copy
import html
import hmac
import os
import secrets
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_market_app as market
import dashboard_memory_app as memory
import dashboard_product_app as product
from dashboard_live_app import (
    LOGIN_CSRF_COOKIE,
    ROLE_ADMIN,
    ROLE_MEMBER,
    SESSION_COOKIE,
    LoginRateLimiter,
    OKXMarketDataClient,
    PanelConfig,
    authenticate_account,
    build_service,
    cookie_value,
    env_bool,
    password_hash,
    verify_password,
)

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_0_COMMERCIAL_2026_08_14"
PLAN_FREE = "FREE"
PLAN_PREMIUM = "PREMIUM"
PLAN_ADMIN = "ADMIN"
ALLOWED_PLANS = {PLAN_FREE, PLAN_PREMIUM, PLAN_ADMIN}
PAYMENT_PENDING = "PENDING"
PAYMENT_APPROVED = "APPROVED"
PAYMENT_REJECTED = "REJECTED"
MAX_PAYMENT_REQUESTS = 300
PUBLIC_RESULT_WINDOW = 20


def _now() -> int:
    return int(time.time())


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_text(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _csrf_ok(session: dict[str, Any] | None, supplied: str) -> bool:
    expected = str((session or {}).get("csrf") or "")
    return bool(expected) and hmac.compare_digest(expected, str(supplied or ""))


def _plan_from_raw(raw: dict[str, Any], now: int | None = None) -> str:
    """Eski MEMBER kayıtlarını geriye dönük güvenli biçimde Premium kabul eder."""
    now = _now() if now is None else int(now)
    role = str(raw.get("role") or ROLE_MEMBER).upper()
    if role == ROLE_ADMIN:
        return PLAN_ADMIN
    explicit = str(raw.get("plan") or "").upper()
    expires_at = _int(raw.get("expires_at"), 0)
    expired = bool(expires_at and expires_at <= now)
    if explicit == PLAN_FREE:
        return PLAN_FREE
    if explicit == PLAN_PREMIUM:
        return PLAN_FREE if expired else PLAN_PREMIUM
    # Legacy migration: geçmişte MEMBER = tam paneldi. Süresi dolmamış eski üyeyi bozma.
    return PLAN_FREE if expired else PLAN_PREMIUM


def _plan_label(plan: str) -> str:
    return {PLAN_ADMIN: "Yönetici", PLAN_PREMIUM: "Premium", PLAN_FREE: "Ücretsiz"}.get(plan, "Ücretsiz")


def _format_expiry(timestamp: Any) -> str:
    value = _int(timestamp, 0)
    if not value:
        return "—"
    return time.strftime("%d.%m.%Y %H:%M", time.gmtime(value + 3 * 3600))


def _result_outcome(row: dict[str, Any]) -> str:
    return str(row.get("outcome") or row.get("result") or row.get("final_result") or "").upper()


def build_public_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Tarayıcıya sinyal seviyesi/coin detayı vermeyen halka açık özet."""
    open_rows = data.get("open_trades") if isinstance(data.get("open_trades"), list) else []
    result_rows = data.get("recent_results") if isinstance(data.get("recent_results"), list) else []
    result_rows = [row for row in result_rows if isinstance(row, dict)][:PUBLIC_RESULT_WINDOW]
    outcomes = [_result_outcome(row) for row in result_rows]
    tp = sum(1 for value in outcomes if value.startswith("TP"))
    sl = sum(1 for value in outcomes if value == "SL" or value.startswith("SL_"))
    be = sum(1 for value in outcomes if "BE" in value)
    decided = tp + sl
    tp_rate = round(tp * 100 / decided, 1) if decided else None
    health = data.get("health") if isinstance(data.get("health"), dict) else {}
    overall = str(health.get("overall") or "UNKNOWN").upper()
    if overall not in {"GREEN", "YELLOW", "RED"}:
        overall = "UNKNOWN"
    last_outcome = outcomes[0] if outcomes else None
    return {
        "open_count": len(open_rows),
        "recent_count": len(result_rows),
        "tp_count": tp,
        "sl_count": sl,
        "be_count": be,
        "tp_rate_percent": tp_rate,
        "health": overall,
        "last_outcome": last_outcome,
        "updated_at": _now(),
        "disclaimer": "İstatistikler geçmiş sistem kayıtlarıdır; gelecek performansı garanti etmez.",
        "version": VERSION,
    }


class CommercialAccountStore(product.ProductAccountStore):
    """Mevcut panel_users.json belgesini plan ve ödeme bilgisiyle geriye uyumlu genişletir."""

    @staticmethod
    def _payment_list(document: dict[str, Any]) -> list[dict[str, Any]]:
        rows = document.get("payment_requests")
        if not isinstance(rows, list):
            rows = []
            document["payment_requests"] = rows
        return rows

    def _raw_user_unlocked(self, document: dict[str, Any], username: str) -> dict[str, Any]:
        return self._find_raw_user(document, username)

    def authenticate(self, username: str, password: str) -> dict[str, str] | None:
        """Süresi biten Premium kullanıcı giriş yapabilir; planı FREE olarak çözülür."""
        if not self.configured:
            return None
        try:
            with self._lock:
                _users, document, _sha = self._users_unlocked()
                raw = self._raw_user_unlocked(document, username)
                normalized = self._normalized_user(raw)
        except (accounts.AccountStoreError, ValueError):
            return None
        if not normalized.get("active", False):
            return None
        if not verify_password(password, str(normalized.get("password_hash") or ""), None):
            return None
        return {"username": str(normalized["username"]), "role": str(normalized["role"])}

    def create_free_user(
        self,
        username: str,
        password: str,
        *,
        actor: str = "self-register",
        reserved_usernames: set[str] | None = None,
    ) -> None:
        username = accounts._normalize_username(username)
        password = accounts._normalize_password(password)
        reserved = {item.casefold() for item in (reserved_usernames or set())}
        if username.casefold() in reserved:
            raise ValueError("Bu kullanıcı adı kullanılamaz.")
        with self._lock:
            users, document, sha = self._users_unlocked()
            if len(users) >= accounts.MAX_USERS:
                raise ValueError("Yeni kayıt sınırına ulaşıldı.")
            if any(row["username"].casefold() == username.casefold() for row in users):
                raise ValueError("Bu kullanıcı adı zaten var.")
            now = _now()
            document.setdefault("users", []).append({
                "username": username,
                "password_hash": password_hash(password),
                "role": ROLE_MEMBER,
                "plan": PLAN_FREE,
                "active": True,
                "expires_at": None,
                "created_at": now,
                "updated_at": now,
                "created_by": actor,
                "updated_by": actor,
            })
            self._save_unlocked(document, sha, actor=actor, action=f"free-register {username}")

    def plan_info(self, username: str) -> dict[str, Any] | None:
        try:
            with self._lock:
                _users, document, _sha = self._users_unlocked()
                raw = copy.deepcopy(self._raw_user_unlocked(document, username))
        except (accounts.AccountStoreError, ValueError):
            return None
        raw.pop("password_hash", None)
        plan = _plan_from_raw(raw)
        return {
            "username": str(raw.get("username") or username),
            "role": str(raw.get("role") or ROLE_MEMBER).upper(),
            "plan": plan,
            "plan_label": _plan_label(plan),
            "active": bool(raw.get("active", True)),
            "expires_at": _int(raw.get("expires_at"), 0) or None,
            "expired": bool(_int(raw.get("expires_at"), 0) and _int(raw.get("expires_at"), 0) <= _now()),
        }

    def list_commercial_users(self) -> list[dict[str, Any]]:
        with self._lock:
            _users, document, _sha = self._users_unlocked()
            raw_users = copy.deepcopy(document.get("users", []))
        rows: list[dict[str, Any]] = []
        for raw in raw_users:
            if not isinstance(raw, dict):
                continue
            raw.pop("password_hash", None)
            plan = _plan_from_raw(raw)
            rows.append({
                "username": str(raw.get("username") or ""),
                "role": str(raw.get("role") or ROLE_MEMBER).upper(),
                "plan": plan,
                "active": bool(raw.get("active", True)),
                "expires_at": _int(raw.get("expires_at"), 0) or None,
                "updated_at": _int(raw.get("updated_at"), 0),
            })
        return sorted(rows, key=lambda row: str(row["username"]).casefold())

    def set_plan(self, username: str, plan: str, *, days: int, actor: str) -> None:
        plan = str(plan or "").upper()
        if plan not in {PLAN_FREE, PLAN_PREMIUM}:
            raise ValueError("Plan FREE veya PREMIUM olmalıdır.")
        days = max(1, min(int(days), 3650))
        with self._lock:
            _users, document, sha = self._users_unlocked()
            raw = self._raw_user_unlocked(document, username)
            if str(raw.get("role") or ROLE_MEMBER).upper() == ROLE_ADMIN:
                raise ValueError("Yönetici planı bu ekrandan değiştirilemez.")
            now = _now()
            raw["plan"] = plan
            if plan == PLAN_PREMIUM:
                current = _int(raw.get("expires_at"), 0)
                base = current if current > now else now
                raw["expires_at"] = base + days * 86_400
            else:
                raw["expires_at"] = None
            raw["updated_at"] = now
            raw["updated_by"] = actor
            self._save_unlocked(document, sha, actor=actor, action=f"plan {username}={plan}")

    def submit_payment(self, username: str, *, method: str, package: str, note: str = "") -> str:
        method = str(method or "BANK_TRANSFER").upper()
        if method not in {"BANK_TRANSFER", "CRYPTO"}:
            raise ValueError("Geçersiz ödeme yöntemi.")
        package = _safe_text(package, 60) or "PREMIUM_30D"
        note = _safe_text(note, 180)
        with self._lock:
            _users, document, sha = self._users_unlocked()
            raw = self._raw_user_unlocked(document, username)
            if not bool(raw.get("active", True)):
                raise ValueError("Pasif hesap ödeme bildirimi bırakamaz.")
            payments = self._payment_list(document)
            for row in payments:
                if (
                    isinstance(row, dict)
                    and str(row.get("username") or "").casefold() == username.casefold()
                    and str(row.get("status") or "").upper() == PAYMENT_PENDING
                ):
                    raise ValueError("Zaten onay bekleyen bir ödeme bildiriminiz var.")
            now = _now()
            payment_id = f"PAY-{now}-{secrets.token_hex(3).upper()}"
            payments.append({
                "id": payment_id,
                "username": str(raw.get("username") or username),
                "method": method,
                "package": package,
                "note": note,
                "status": PAYMENT_PENDING,
                "created_at": now,
                "decided_at": 0,
                "decided_by": "",
            })
            if len(payments) > MAX_PAYMENT_REQUESTS:
                del payments[:-MAX_PAYMENT_REQUESTS]
            self._save_unlocked(document, sha, actor=username, action=f"payment-notify {payment_id}")
            return payment_id

    def list_payments(self) -> list[dict[str, Any]]:
        with self._lock:
            _users, document, _sha = self._users_unlocked()
            payments = copy.deepcopy(self._payment_list(document))
        rows = [row for row in payments if isinstance(row, dict)]
        rows.sort(key=lambda row: _int(row.get("created_at"), 0), reverse=True)
        return rows[:MAX_PAYMENT_REQUESTS]

    def decide_payment(
        self,
        payment_id: str,
        *,
        approve: bool,
        actor: str,
        premium_days: int,
    ) -> None:
        payment_id = _safe_text(payment_id, 80)
        premium_days = max(1, min(int(premium_days), 3650))
        with self._lock:
            _users, document, sha = self._users_unlocked()
            payments = self._payment_list(document)
            payment = next(
                (row for row in payments if isinstance(row, dict) and str(row.get("id") or "") == payment_id),
                None,
            )
            if not payment:
                raise ValueError("Ödeme bildirimi bulunamadı.")
            if str(payment.get("status") or "").upper() != PAYMENT_PENDING:
                raise ValueError("Bu ödeme bildirimi daha önce sonuçlandırılmış.")
            now = _now()
            if approve:
                username = str(payment.get("username") or "")
                raw = self._raw_user_unlocked(document, username)
                if str(raw.get("role") or ROLE_MEMBER).upper() == ROLE_ADMIN:
                    raise ValueError("Yönetici hesabına ödeme üzerinden plan atanamaz.")
                current = _int(raw.get("expires_at"), 0)
                base = current if current > now else now
                raw["plan"] = PLAN_PREMIUM
                raw["expires_at"] = base + premium_days * 86_400
                raw["updated_at"] = now
                raw["updated_by"] = actor
                payment["status"] = PAYMENT_APPROVED
            else:
                payment["status"] = PAYMENT_REJECTED
            payment["decided_at"] = now
            payment["decided_by"] = actor
            self._save_unlocked(document, sha, actor=actor, action=f"payment-decision {payment_id}")


def commercial_store_from_env(config: PanelConfig) -> CommercialAccountStore:
    return CommercialAccountStore(
        config.repository,
        os.getenv("GITHUB_PANEL_USERS_TOKEN"),
        ref=os.getenv("PANEL_USERS_REF", accounts.USERS_REF_DEFAULT),
        path=os.getenv("PANEL_USERS_PATH", accounts.USERS_PATH_DEFAULT),
    )


class RegistrationLimiter:
    def __init__(self, limit: int = 3, window_seconds: int = 3600):
        self.limit = limit
        self.window_seconds = window_seconds
        self._rows: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allowed(self, identity: str) -> bool:
        now = time.time()
        cutoff = now - self.window_seconds
        with self._lock:
            rows = [value for value in self._rows.get(identity, []) if value >= cutoff]
            self._rows[identity] = rows
            return len(rows) < self.limit

    def record(self, identity: str) -> None:
        with self._lock:
            self._rows.setdefault(identity, []).append(time.time())


def public_home_page(nonce: str) -> str:
    nonce_attr = html.escape(nonce, quote=True)
    return f"""<!doctype html>
<html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kripto Kontrol Merkezi</title>
<style>
:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--blue:#69aef8;--green:#42e28c;--red:#ff627d;--amber:#ffbd59}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 80% -10%,rgba(44,230,191,.12),transparent 32%),radial-gradient(circle at 5% 18%,rgba(105,174,248,.08),transparent 28%),var(--bg);color:var(--text);font:14px/1.55 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}button{{font:inherit}}
.wrap{{width:min(1180px,calc(100% - 28px));margin:auto}}.top{{height:72px;display:flex;align-items:center;justify-content:space-between;gap:12px}}.brand{{display:flex;align-items:center;gap:10px;font-weight:950;letter-spacing:-.02em}}.logo{{width:37px;height:37px;border:1px solid rgba(44,230,191,.38);border-radius:12px;display:grid;place-items:center;color:var(--teal);background:rgba(44,230,191,.07)}}.actions{{display:flex;gap:8px;align-items:center;flex-wrap:wrap}}.btn{{display:inline-flex;align-items:center;justify-content:center;border:1px solid var(--line);border-radius:10px;padding:9px 13px;background:#0a1820;color:#c6d8d5;font-weight:850;font-size:12px}}.btn.primary{{background:var(--teal);border-color:transparent;color:#04110e}}.hero{{padding:68px 0 42px;display:grid;grid-template-columns:1.15fr .85fr;gap:32px;align-items:center}}.eyebrow{{color:var(--teal);font-size:11px;font-weight:950;letter-spacing:.13em;text-transform:uppercase}}h1{{font-size:clamp(38px,6vw,72px);line-height:.98;letter-spacing:-.055em;margin:14px 0 18px;max-width:760px}}.lead{{color:#a5bcba;font-size:17px;max-width:690px}}.hero-actions{{display:flex;gap:9px;flex-wrap:wrap;margin-top:25px}}.screen{{border:1px solid #1c3b46;background:linear-gradient(160deg,#0c1d26,#071219);border-radius:22px;padding:17px;box-shadow:0 30px 90px rgba(0,0,0,.32)}}.screen-top{{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;color:#78928f;font-size:10px}}.pulse{{display:inline-flex;gap:6px;align-items:center;color:var(--green);font-weight:900}}.pulse:before{{content:'';width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 14px var(--green)}}.metrics{{display:grid;grid-template-columns:repeat(2,1fr);gap:9px}}.metric{{border:1px solid var(--line);border-radius:13px;padding:13px;background:#091820}}.metric small{{color:#69827f;font-size:9px;text-transform:uppercase}}.metric strong{{display:block;font-size:24px;margin-top:3px}}.metric.green strong{{color:var(--green)}}.metric.red strong{{color:var(--red)}}.metric.blue strong{{color:var(--blue)}}.metric.amber strong{{color:var(--amber)}}.sample{{margin-top:10px;border:1px dashed #28444c;border-radius:13px;padding:14px;color:#8fa8a5}}.sample b{{color:#dcecea}}.lock{{float:right;color:var(--amber);font-weight:900}}.section{{padding:40px 0}}.section h2{{font-size:29px;letter-spacing:-.035em;margin:0 0 8px}}.section>p{{color:var(--muted);margin:0 0 20px}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}.card{{border:1px solid var(--line);background:rgba(10,25,33,.86);border-radius:16px;padding:18px}}.card span{{font-size:22px}}.card h3{{margin:10px 0 6px}}.card p{{color:var(--muted);font-size:12px}}.plans{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}}.plan{{border:1px solid var(--line);border-radius:18px;padding:22px;background:#0a1921}}.plan.premium{{border-color:rgba(44,230,191,.32);box-shadow:0 0 0 1px rgba(44,230,191,.06) inset}}.plan h3{{font-size:22px;margin:0 0 6px}}.plan ul{{padding-left:18px;color:#9fb5b2}}.foot{{padding:35px 0 50px;border-top:1px solid rgba(27,57,67,.6);color:#6f8986;font-size:11px;margin-top:34px}}@media(max-width:850px){{.hero{{grid-template-columns:1fr;padding-top:38px}}.cards{{grid-template-columns:1fr}}.plans{{grid-template-columns:1fr}}}}@media(max-width:520px){{.top{{height:auto;padding:15px 0;align-items:flex-start}}.actions .btn:first-child{{display:none}}.hero{{padding-top:30px}}.metrics{{grid-template-columns:1fr 1fr}}}}
</style></head><body>
<div class="wrap"><header class="top"><div class="brand"><div class="logo">K</div>Kripto Kontrol Merkezi</div><div class="actions"><a class="btn" href="/register">Ücretsiz üye ol</a><a class="btn primary" href="/login">Giriş yap</a></div></header>
<section class="hero"><div><div class="eyebrow">Canlı analiz ve takip platformu</div><h1>Piyasayı tek ekrandan takip et.</h1><p class="lead">Gerçek sistem kayıtlarından istatistik, canlı piyasa görünümü ve üyelik bazlı analiz araçları. Otomatik emir açmaz; kullanıcı parasını veya borsa hesabını yönetmez.</p><div class="hero-actions"><a class="btn primary" href="/register">Ücretsiz başla</a><a class="btn" href="#plans">Üyelikleri karşılaştır</a></div></div>
<div class="screen"><div class="screen-top"><span>GENEL SİSTEM ÖZETİ</span><span class="pulse" id="healthLabel">Canlı veri</span></div><div class="metrics"><div class="metric blue"><small>Açık takip</small><strong id="openCount">—</strong></div><div class="metric green"><small>Son kayıt TP</small><strong id="tpCount">—</strong></div><div class="metric red"><small>Son kayıt SL</small><strong id="slCount">—</strong></div><div class="metric amber"><small>Sonuç TP oranı</small><strong id="tpRate">—</strong></div></div><div class="sample"><span class="lock">🔒 Premium</span><b>Canlı işlem detayları</b><br>Coin, Entry, TP1/TP2/TP3, SL ve teknik analiz üyelik seviyesine göre açılır.</div></div></section>
<section class="section"><h2>Önce sistemi gör, sonra karar ver.</h2><p>Siteyi incelemek için giriş zorunlu değil. Hassas canlı sinyal seviyeleri ve gelişmiş araçlar Premium alanında kalır.</p><div class="cards"><div class="card"><span>◈</span><h3>Canlı istatistik</h3><p>Genel sistem sonuçlarını ve çalışma durumunu üyelik almadan görebilirsin.</p></div><div class="card"><span>⌁</span><h3>Piyasa araçları</h3><p>Ücretsiz üyelikle temel piyasa görünümünü kullan; Premium'da fırsat ve teknik skor katmanlarını aç.</p></div><div class="card"><span>🔔</span><h3>Premium uyarılar</h3><p>Canlı sinyal detayları, sesli-renkli uyarılar, izleme ve gelişmiş analiz Premium kullanıcıya açılır.</p></div></div></section>
<section class="section" id="plans"><h2>Basit üyelik yapısı</h2><p>Başlangıçta iki kullanıcı paketi: FREE ve PREMIUM.</p><div class="plans"><div class="plan"><h3>FREE</h3><p>Ürünü tanımak ve temel piyasa görünümünü kullanmak için.</p><ul><li>Genel canlı istatistik</li><li>Temel piyasa görünümü</li><li>Sınırlı ürün deneyimi</li><li>Premium özelliklerin önizlemesi</li></ul><a class="btn" href="/register">Ücretsiz hesap aç</a></div><div class="plan premium"><h3>PREMIUM</h3><p>Canlı sinyal ve gelişmiş analiz araçlarının tamamı.</p><ul><li>Canlı sinyal Entry / TP / SL seviyeleri</li><li>Fırsat Merkezi ve İnceleme Skoru</li><li>Sesli ve renkli yeni sinyal uyarısı</li><li>İzleme Listesi ve gelişmiş coin analizi</li><li>Geçmiş sonuç ve performans araçları</li></ul><a class="btn primary" href="/login">Premium alana giriş</a></div></div></section>
<footer class="foot">Kripto Kontrol Merkezi bir analiz ve takip yazılımıdır. Gösterilen istatistikler geçmiş kayıtlardır; kazanç garantisi değildir.</footer></div>
<script nonce="{nonce_attr}">(()=>{{async function load(){{try{{const r=await fetch('/api/public/summary',{{cache:'no-store'}});const d=await r.json();if(!r.ok)return;document.getElementById('openCount').textContent=d.open_count??'—';document.getElementById('tpCount').textContent=d.tp_count??'—';document.getElementById('slCount').textContent=d.sl_count??'—';document.getElementById('tpRate').textContent=d.tp_rate_percent==null?'—':d.tp_rate_percent+'%';const h=document.getElementById('healthLabel');h.textContent=d.health==='GREEN'?'Sistem çalışıyor':d.health==='YELLOW'?'Kontrol ediliyor':d.health==='RED'?'Veri uyarısı':'Canlı veri';}}catch{{}}}}load();setInterval(load,30000);}})();</script>
</body></html>"""


def free_member_page(session: dict[str, Any], plan_info: dict[str, Any] | None, nonce: str) -> str:
    username = html.escape(str(session.get("username") or "üye"))
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    nonce_attr = html.escape(nonce, quote=True)
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kripto Kontrol · FREE</title><style>
:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--blue:#69aef8;--amber:#ffbd59;--green:#42e28c;--red:#ff627d}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}a{{color:inherit;text-decoration:none}}.shell{{width:min(1050px,calc(100% - 26px));margin:auto;padding:20px 0 50px}}.top{{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}}.brand{{font-weight:950;font-size:17px}}.buttons{{display:flex;gap:7px;align-items:center;flex-wrap:wrap}}.btn{{border:1px solid var(--line);border-radius:9px;padding:8px 11px;background:#0a1820;color:#c4d7d4;font-weight:850;font-size:11px}}.btn.primary{{background:var(--teal);color:#03110e;border-color:transparent}}form{{margin:0}}button.btn{{cursor:pointer}}.hero{{margin-top:22px;border:1px solid var(--line);border-radius:18px;padding:22px;background:linear-gradient(135deg,rgba(44,230,191,.06),rgba(105,174,248,.025)),var(--panel)}}.badge{{display:inline-flex;padding:5px 8px;border:1px solid rgba(255,189,89,.25);color:var(--amber);border-radius:999px;font-weight:900;font-size:9px}}h1{{font-size:29px;margin:10px 0 6px}}p{{color:var(--muted)}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin:16px 0}}.metric{{border:1px solid var(--line);background:#091820;border-radius:12px;padding:13px}}.metric small{{display:block;color:#6c8582;font-size:8px;text-transform:uppercase}}.metric strong{{display:block;font-size:21px;margin-top:4px}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.card{{border:1px solid var(--line);border-radius:15px;background:var(--panel);padding:17px}}.market{{display:grid;grid-template-columns:repeat(3,1fr);gap:7px}}.coin{{border:1px solid var(--line);border-radius:10px;padding:10px;background:#08161d}}.coin b{{display:block}}.coin small{{color:var(--muted)}}.locked{{position:relative;overflow:hidden}}.locked:after{{content:'PREMIUM';position:absolute;right:12px;top:12px;color:var(--amber);font-size:8px;font-weight:950;border:1px solid rgba(255,189,89,.23);border-radius:999px;padding:3px 6px}}.feature{{padding:9px 0;border-bottom:1px solid rgba(27,57,67,.6);color:#9db3b0}}.feature:last-child{{border-bottom:0}}@media(max-width:760px){{.metrics{{grid-template-columns:1fr 1fr}}.grid{{grid-template-columns:1fr}}.market{{grid-template-columns:1fr 1fr 1fr}}}}
</style></head><body><div class="shell"><header class="top"><div class="brand">Kripto Kontrol Merkezi</div><div class="buttons"><span class="badge">FREE · {username}</span><a class="btn primary" href="/premium">Premium'a geç</a><a class="btn" href="/account">Hesabım</a><form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button class="btn" type="submit">Çıkış</button></form></div></header><section class="hero"><span class="badge">ÜCRETSİZ ÜYELİK</span><h1>Temel görünüm açık.</h1><p>Piyasa ve genel performansı takip edebilirsin. Canlı sinyal seviyeleri ve gelişmiş analizler Premium üyelikte açılır.</p><div class="metrics"><div class="metric"><small>Açık takip</small><strong id="fOpen">—</strong></div><div class="metric"><small>Son TP</small><strong id="fTp">—</strong></div><div class="metric"><small>Son SL</small><strong id="fSl">—</strong></div><div class="metric"><small>TP oranı</small><strong id="fRate">—</strong></div></div></section><div class="grid"><section class="card"><h2>Canlı piyasa</h2><p>FREE üyede temel coin görünümü.</p><div class="market" id="freeMarket"><div class="coin"><b>BTCUSDT</b><small>yükleniyor…</small></div><div class="coin"><b>ETHUSDT</b><small>yükleniyor…</small></div><div class="coin"><b>SOLUSDT</b><small>yükleniyor…</small></div></div><p><a class="btn" href="/market-center">Piyasa Merkezi</a></p></section><section class="card locked"><h2>Premium araçlar</h2><div class="feature">🔒 Canlı sinyal Entry / TP1 / TP2 / TP3 / SL</div><div class="feature">🔒 Piyasa Fırsat Merkezi ve 80+ filtreleri</div><div class="feature">🔒 Teknik İnceleme Skoru</div><div class="feature">🔒 Sesli ve renkli sinyal uyarıları</div><div class="feature">🔒 İzleme Listesi ve gelişmiş performans</div><p><a class="btn primary" href="/premium">Premium üyeliği incele</a></p></section></div></div>
<script nonce="{nonce_attr}">(()=>{{const esc=v=>String(v??'');async function stats(){{try{{const r=await fetch('/api/public/summary',{{cache:'no-store'}}),d=await r.json();if(!r.ok)return;fOpen.textContent=d.open_count??'—';fTp.textContent=d.tp_count??'—';fSl.textContent=d.sl_count??'—';fRate.textContent=d.tp_rate_percent==null?'—':d.tp_rate_percent+'%';}}catch{{}}}}async function market(){{try{{const r=await fetch('/api/market/overview?symbols=BTCUSDT,ETHUSDT,SOLUSDT',{{credentials:'same-origin',cache:'no-store'}}),d=await r.json();if(!r.ok)return;freeMarket.innerHTML=(d.items||[]).map(i=>`<div class="coin"><b>${{esc(i.symbol)}}</b><small>${{Number(i.last||0).toLocaleString('tr-TR',{{maximumFractionDigits:8}})}} · ${{Number(i.change_24h_pct||0)>=0?'+':''}}${{Number(i.change_24h_pct||0).toFixed(2)}}%</small></div>`).join('');}}catch{{}}}}stats();market();setInterval(stats,30000);setInterval(market,30000);}})();</script></body></html>"""


def register_page(csrf: str, error: str | None = None) -> str:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Ücretsiz Üyelik</title><style>:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--red:#ff748c}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:radial-gradient(circle at 80% 0,#10352f,transparent 35%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}.card{{width:min(440px,100%);border:1px solid var(--line);border-radius:20px;background:var(--panel);padding:28px}}h1{{margin:0 0 5px}}p{{color:var(--muted)}}label{{display:block;margin:13px 0 5px;font-weight:800;font-size:11px}}input{{width:100%;border:1px solid var(--line);border-radius:10px;background:#061219;color:var(--text);padding:11px;outline:none}}button{{width:100%;margin-top:18px;border:0;border-radius:10px;padding:12px;background:var(--teal);color:#03110e;font-weight:950;cursor:pointer}}a{{color:var(--teal);font-weight:800;text-decoration:none}}.error{{border:1px solid rgba(255,116,140,.4);background:rgba(255,116,140,.07);color:#ffb3c0;border-radius:9px;padding:9px;margin:12px 0}}</style></head><body><form class="card" method="post" action="/register"><h1>Ücretsiz hesap aç</h1><p>FREE üyelikle temel piyasa ve istatistik görünümünü kullan. Premium'a daha sonra geçebilirsin.</p>{error_html}<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}"><label>Kullanıcı adı</label><input name="username" minlength="3" maxlength="40" pattern="[A-Za-z0-9_.-]+" required autocomplete="username"><label>Şifre</label><input name="password" type="password" minlength="10" maxlength="256" required autocomplete="new-password"><label>Şifre tekrar</label><input name="password_confirm" type="password" minlength="10" maxlength="256" required autocomplete="new-password"><button type="submit">FREE hesabı oluştur</button><p><a href="/">← Ana sayfa</a> · <a href="/login">Zaten hesabım var</a></p></form></body></html>"""


def login_page_v3(username: str, csrf: str, error: str | None = None) -> str:
    error_html = f'<div class="error">{html.escape(error)}</div>' if error else ""
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Kripto Kontrol · Giriş</title><style>:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:radial-gradient(circle at 82% 0,#10352f,transparent 36%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}.card{{width:min(430px,100%);border:1px solid var(--line);border-radius:21px;background:var(--panel);padding:29px}}h1{{margin:0 0 5px}}p{{color:var(--muted)}}label{{display:block;margin:13px 0 5px;font-weight:800;font-size:11px}}input{{width:100%;border:1px solid var(--line);border-radius:10px;background:#061219;color:var(--text);padding:11px}}button{{width:100%;margin-top:18px;border:0;border-radius:10px;padding:12px;background:var(--teal);color:#03110e;font-weight:950}}a{{color:var(--teal);font-weight:800;text-decoration:none}}.error{{border:1px solid #67333e;border-radius:9px;padding:9px;color:#ffb3c0;background:#25131a}}</style></head><body><form class="card" method="post" action="/login"><h1>Üye girişi</h1><p>FREE, PREMIUM veya yönetici hesabınla giriş yap.</p>{error_html}<input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}"><label>Kullanıcı adı</label><input name="username" value="{html.escape(username, quote=True)}" required autocomplete="username"><label>Şifre</label><input name="password" type="password" required autocomplete="current-password"><button type="submit">Giriş yap</button><p><a href="/">← Ana sayfa</a> · <a href="/register">Ücretsiz hesap aç</a></p></form></body></html>"""


def premium_page(session: dict[str, Any], info: dict[str, Any], payment_instructions: str, crypto_enabled: bool) -> str:
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    username = html.escape(str(session.get("username") or "üye"))
    plan = str(info.get("plan") or PLAN_FREE)
    expiry = _format_expiry(info.get("expires_at"))
    if plan in {PLAN_PREMIUM, PLAN_ADMIN}:
        action = f'<div class="ok">Aktif plan: <strong>{html.escape(_plan_label(plan))}</strong> · Bitiş: {html.escape(expiry)}</div>'
    else:
        crypto_option = '<option value="CRYPTO">Kripto ödeme bildirimi</option>' if crypto_enabled else ""
        action = f"""<form method="post" action="/payment/notify"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="package" value="PREMIUM_30D"><label>Ödeme yöntemi</label><select name="method"><option value="BANK_TRANSFER">Banka / FAST / Havale</option>{crypto_option}</select><label>Not (isteğe bağlı)</label><input name="note" maxlength="180" placeholder="Örn. gönderen adı veya kısa açıklama"><button type="submit">Ödeme yaptım · Yöneticiye bildir</button></form>"""
    crypto_note = "Kripto ödeme seçeneği yönetici tarafından etkinleştirilmiştir." if crypto_enabled else "Kripto ödeme seçeneği şu anda kapalıdır; hukuki/operasyonel onay sonrası tek ayarla açılabilir."
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Premium Üyelik</title><style>:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--amber:#ffbd59}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}.shell{{width:min(820px,calc(100% - 28px));margin:auto;padding:28px 0}}a{{color:var(--teal);font-weight:850;text-decoration:none}}.card{{border:1px solid var(--line);border-radius:18px;background:var(--panel);padding:22px;margin-top:16px}}h1{{margin:0}}p{{color:var(--muted)}}.plan{{display:inline-flex;border:1px solid rgba(255,189,89,.28);color:var(--amber);border-radius:999px;padding:5px 8px;font-size:9px;font-weight:950}}ul{{color:#a1b8b5}}label{{display:block;margin:12px 0 5px;font-size:11px;font-weight:850}}input,select{{width:100%;border:1px solid var(--line);border-radius:9px;background:#061219;color:var(--text);padding:10px}}button{{margin-top:16px;width:100%;border:0;border-radius:10px;background:var(--teal);color:#03110e;padding:11px;font-weight:950;cursor:pointer}}.instructions{{white-space:pre-wrap;border:1px dashed #2b4a53;border-radius:10px;padding:12px;color:#a7bebb;background:#07141a}}.ok{{border:1px solid rgba(44,230,191,.3);background:rgba(44,230,191,.06);border-radius:11px;padding:12px}}</style></head><body><div class="shell"><a href="/">← Panele dön</a><div class="card"><span class="plan">{html.escape(_plan_label(plan))}</span><h1>Premium üyelik</h1><p>{username}, Premium ile canlı sinyal seviyeleri ve gelişmiş analiz araçlarının tamamı açılır.</p><ul><li>Entry / TP1 / TP2 / TP3 / SL</li><li>Fırsat Merkezi, teknik skor ve filtreler</li><li>Sesli-renkli canlı bildirim</li><li>İzleme Listesi ve gelişmiş coin analizi</li><li>Detaylı geçmiş sonuç ve performans</li></ul></div><div class="card"><h2>Ödeme bildirimi</h2><p>Ödeme otomatik tahsil edilmez. Ödeme yaptıktan sonra aşağıdaki butonla yöneticiye bildirim bırakılır; onaydan sonra Premium süre açılır.</p><div class="instructions">{html.escape(payment_instructions)}</div><p>{html.escape(crypto_note)}</p>{action}</div></div></body></html>"""


def account_page_v3(session: dict[str, Any], info: dict[str, Any]) -> str:
    username = html.escape(str(session.get("username") or "üye"))
    plan = str(info.get("plan") or PLAN_FREE)
    expiry = _format_expiry(info.get("expires_at"))
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Hesabım</title><style>:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}.shell{{width:min(760px,calc(100% - 28px));margin:auto;padding:30px 0}}.card{{border:1px solid var(--line);border-radius:17px;background:var(--panel);padding:20px;margin-top:16px}}a{{color:var(--teal);font-weight:850;text-decoration:none}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:9px}}.item{{border:1px solid var(--line);border-radius:10px;padding:12px}}small{{display:block;color:var(--muted)}}strong{{font-size:17px}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><div class="shell"><a href="/">← Geri dön</a><div class="card"><h1>Hesabım</h1><div class="grid"><div class="item"><small>Kullanıcı</small><strong>{username}</strong></div><div class="item"><small>Plan</small><strong>{html.escape(_plan_label(plan))}</strong></div><div class="item"><small>Premium bitiş</small><strong>{html.escape(expiry)}</strong></div><div class="item"><small>Durum</small><strong>Aktif</strong></div></div><p><a href="/premium">Premium üyelik / ödeme</a></p></div></div></body></html>"""


def admin_memberships_page(store: CommercialAccountStore, session: dict[str, Any], premium_days: int, message: str = "", error: str = "") -> str:
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    users = store.list_commercial_users()
    payments = store.list_payments()
    pending = [row for row in payments if str(row.get("status") or "").upper() == PAYMENT_PENDING]
    notices = ""
    if message:
        notices += f'<div class="notice ok">{html.escape(message)}</div>'
    if error:
        notices += f'<div class="notice err">{html.escape(error)}</div>'
    user_rows = []
    for row in users:
        username = html.escape(str(row.get("username") or ""))
        username_attr = html.escape(str(row.get("username") or ""), quote=True)
        plan = str(row.get("plan") or PLAN_FREE)
        if str(row.get("role") or "").upper() == ROLE_ADMIN:
            actions = "Kurucu / yönetici"
        else:
            actions = f"""<form method="post" action="/admin/memberships/plan"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="username" value="{username_attr}"><input type="hidden" name="plan" value="PREMIUM"><input type="hidden" name="days" value="{premium_days}"><button>Premium +{premium_days} gün</button></form><form method="post" action="/admin/memberships/plan"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="username" value="{username_attr}"><input type="hidden" name="plan" value="FREE"><input type="hidden" name="days" value="{premium_days}"><button>FREE yap</button></form>"""
        user_rows.append(f'<tr><td><strong>{username}</strong></td><td>{html.escape(_plan_label(plan))}</td><td>{html.escape(_format_expiry(row.get("expires_at")))}</td><td class="acts">{actions}</td></tr>')
    payment_rows = []
    for row in pending:
        pid = html.escape(str(row.get("id") or ""), quote=True)
        payment_rows.append(f"""<tr><td><strong>{html.escape(str(row.get('username') or ''))}</strong><small>{html.escape(str(row.get('id') or ''))}</small></td><td>{html.escape(str(row.get('method') or ''))}</td><td>{html.escape(str(row.get('package') or ''))}<small>{html.escape(str(row.get('note') or ''))}</small></td><td class="acts"><form method="post" action="/admin/payments/decision"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="payment_id" value="{pid}"><input type="hidden" name="decision" value="approve"><button>Onayla +{premium_days} gün</button></form><form method="post" action="/admin/payments/decision"><input type="hidden" name="csrf" value="{csrf}"><input type="hidden" name="payment_id" value="{pid}"><input type="hidden" name="decision" value="reject"><button>Reddet</button></form></td></tr>""")
    if not payment_rows:
        payment_rows.append('<tr><td colspan="4" class="empty">Onay bekleyen ödeme bildirimi yok.</td></tr>')
    return f"""<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Üyelik ve Ödemeler</title><style>:root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#82a09d;--teal:#2ce6bf;--red:#ff748c;--amber:#ffbd59}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,sans-serif}}.shell{{width:min(1180px,calc(100% - 28px));margin:auto;padding:28px 0}}a{{color:var(--teal);font-weight:850;text-decoration:none}}.top{{display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap}}.card{{border:1px solid var(--line);border-radius:17px;background:var(--panel);padding:18px;margin-top:16px}}table{{width:100%;border-collapse:collapse}}th,td{{border-bottom:1px solid var(--line);padding:10px;text-align:left;vertical-align:top}}th{{color:var(--muted);font-size:9px;text-transform:uppercase}}small{{display:block;color:var(--muted)}}.acts{{display:flex;gap:6px;flex-wrap:wrap}}form{{margin:0}}button{{border:1px solid var(--line);border-radius:8px;padding:7px 9px;background:#08161d;color:#c3d7d4;cursor:pointer;font-weight:800}}button:hover{{border-color:var(--teal)}}.notice{{padding:9px;border-radius:9px;margin-top:10px}}.ok{{border:1px solid rgba(44,230,191,.3);background:rgba(44,230,191,.06)}}.err{{border:1px solid rgba(255,98,125,.3);background:rgba(255,98,125,.06)}}.pending{{color:var(--amber)}}.empty{{text-align:center;color:var(--muted);padding:20px}}@media(max-width:700px){{table{{display:block;overflow:auto}}}}</style></head><body><div class="shell"><div class="top"><div><h1>Üyelik ve Ödeme Yönetimi</h1><p>FREE / PREMIUM planları ve kullanıcı ödeme bildirimleri</p></div><div><a href="/admin/users">Kullanıcılar</a> · <a href="/">Panele dön</a></div></div>{notices}<div class="card"><h2><span class="pending">{len(pending)}</span> onay bekleyen ödeme</h2><table><thead><tr><th>Kullanıcı</th><th>Yöntem</th><th>Paket / Not</th><th>İşlem</th></tr></thead><tbody>{''.join(payment_rows)}</tbody></table></div><div class="card"><h2>Üyelik planları</h2><table><thead><tr><th>Kullanıcı</th><th>Plan</th><th>Bitiş</th><th>İşlem</th></tr></thead><tbody>{''.join(user_rows)}</tbody></table></div></div></body></html>"""


def make_v3_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    market_client = market_client or OKXMarketDataClient(cache_seconds=30)
    overview_client = overview_client or market.OKXMarketOverviewClient(cache_seconds=20)
    BaseHandler = memory.make_v210_handler(config, service, sessions, limiter, store, market_client, overview_client)
    register_limiter = RegistrationLimiter()
    premium_days = max(1, min(_int(os.getenv("PANEL_PREMIUM_DAYS"), 30), 3650))
    payment_instructions = _safe_text(os.getenv("PANEL_PAYMENT_INSTRUCTIONS") or "Ödeme bilgisini yöneticiden alın. Ödeme tamamlandıktan sonra bu sayfadaki 'Ödeme yaptım' butonunu kullanın.", 1000)
    crypto_enabled = env_bool("PANEL_CRYPTO_PAYMENT_ENABLED", False)
    premium_paths = {
        "/api/dashboard",
        "/api/market/opportunities",
        "/api/market/analysis-score",
        "/advanced",
    }

    class V3Handler(BaseHandler):
        server_version = "KriptoPanel/3.0"

        def _serve_login(self, error: str | None = None, status: int = HTTPStatus.OK, username: str = "") -> None:
            csrf = secrets.token_urlsafe(24)
            self._send(status, login_page_v3(username, csrf, error), "text/html; charset=utf-8", cookies=[cookie_value(LOGIN_CSRF_COOKIE, csrf, max_age=600, secure=config.cookie_secure)])

        def _serve_register(self, error: str | None = None, status: int = HTTPStatus.OK) -> None:
            csrf = secrets.token_urlsafe(24)
            self._send(status, register_page(csrf, error), "text/html; charset=utf-8", cookies=[cookie_value(LOGIN_CSRF_COOKIE, csrf, max_age=600, secure=config.cookie_secure)])

        def _plan_info(self, session: dict[str, Any] | None) -> dict[str, Any]:
            if not session:
                return {"plan": "VISITOR", "plan_label": "Ziyaretçi", "expires_at": None}
            role = str(session.get("role") or ROLE_MEMBER).upper()
            username = str(session.get("username") or "")
            if role == ROLE_ADMIN:
                return {"plan": PLAN_ADMIN, "plan_label": "Yönetici", "expires_at": None}
            dynamic = store.plan_info(username)
            if dynamic:
                return dynamic
            # Ortam değişkeniyle tanımlanmış eski MEMBER hesabı geriye uyum için Premium kalır.
            if config.member_username and username.casefold() == config.member_username.casefold():
                return {"plan": PLAN_PREMIUM, "plan_label": "Premium", "expires_at": None}
            return {"plan": PLAN_FREE, "plan_label": "Ücretsiz", "expires_at": None}

        def _is_premium(self, session: dict[str, Any] | None) -> bool:
            return str(self._plan_info(session).get("plan") or "") in {PLAN_PREMIUM, PLAN_ADMIN}

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            info = self._plan_info(session)
            nonce = secrets.token_urlsafe(18)
            if str(info.get("plan")) == PLAN_FREE:
                self._send(HTTPStatus.OK, free_member_page(session, info, nonce), "text/html; charset=utf-8", nonce=nonce)
                return
            body = memory.memory_dashboard_page(session, nonce)
            plan_link = '<a class="badge" href="/admin/memberships">Üyelikler / Ödemeler</a>' if str(info.get("plan")) == PLAN_ADMIN else '<a class="badge" href="/premium">Premium</a>'
            marker = '<a class="badge" href="/account">Hesabım</a>'
            if marker in body:
                body = body.replace(marker, plan_link + marker, 1)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION, "public_home": True, "plans": [PLAN_FREE, PLAN_PREMIUM, PLAN_ADMIN], "crypto_payment_enabled": crypto_enabled})
                return
            if path == "/api/public/summary":
                try:
                    self._json(HTTPStatus.OK, build_public_summary(service.get_data()))
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "public_summary_unavailable"})
                return
            if path == "/":
                session = self._session()
                if session:
                    self._render_root_v17(session)
                else:
                    nonce = secrets.token_urlsafe(18)
                    self._send(HTTPStatus.OK, public_home_page(nonce), "text/html; charset=utf-8", nonce=nonce)
                return
            if path == "/login":
                if self._session():
                    self._redirect("/")
                else:
                    self._serve_login()
                return
            if path == "/register":
                if self._session():
                    self._redirect("/")
                else:
                    self._serve_register()
                return
            if path == "/account":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                self._send(HTTPStatus.OK, account_page_v3(session, self._plan_info(session)), "text/html; charset=utf-8")
                return
            if path == "/premium":
                session = self._session()
                if not session:
                    self._redirect("/register")
                    return
                self._send(HTTPStatus.OK, premium_page(session, self._plan_info(session), payment_instructions, crypto_enabled), "text/html; charset=utf-8")
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
                    body = admin_memberships_page(store, session, premium_days, message, error)
                except accounts.AccountStoreError as exc:
                    body = admin_memberships_page(store, session, premium_days, "", str(exc))
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8")
                return
            if path in premium_paths:
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication_required"}) if path.startswith("/api/") else self._redirect("/login")
                    return
                if not self._is_premium(session):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "premium_required", "upgrade": "/premium"}) if path.startswith("/api/") else self._redirect("/premium")
                    return
            return super().do_GET()

        def _membership_redirect(self, *, message: str = "", error: str = "") -> None:
            params = {}
            if message:
                params["message"] = message[:200]
            if error:
                params["error"] = error[:200]
            suffix = "?" + urllib.parse.urlencode(params) if params else ""
            self._redirect("/admin/memberships" + suffix)

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/register":
                identity = self._client_ip()
                if not register_limiter.allowed(identity):
                    self._serve_register("Bu bağlantıdan çok fazla yeni hesap açıldı. Daha sonra tekrar deneyin.", HTTPStatus.TOO_MANY_REQUESTS)
                    return
                form = self._form()
                csrf_cookie = self._cookie(LOGIN_CSRF_COOKIE) or ""
                csrf_form = form.get("csrf", "")
                if not csrf_cookie or not hmac.compare_digest(csrf_cookie, csrf_form):
                    self._serve_register("Kayıt formunun süresi doldu. Tekrar deneyin.", HTTPStatus.FORBIDDEN)
                    return
                username = form.get("username", "")
                password = form.get("password", "")
                if password != form.get("password_confirm", ""):
                    self._serve_register("Şifreler eşleşmiyor.", HTTPStatus.BAD_REQUEST)
                    return
                reserved = {config.username}
                if config.member_username:
                    reserved.add(config.member_username)
                try:
                    store.create_free_user(username, password, reserved_usernames=reserved)
                except (ValueError, accounts.AccountStoreError) as exc:
                    self._serve_register(str(exc), HTTPStatus.BAD_REQUEST)
                    return
                register_limiter.record(identity)
                token, _session = sessions.create(accounts._normalize_username(username), ROLE_MEMBER)
                self._redirect("/", cookies=[cookie_value(SESSION_COOKIE, token, max_age=config.session_hours * 3600, secure=config.cookie_secure), cookie_value(LOGIN_CSRF_COOKIE, "", max_age=0, secure=config.cookie_secure)])
                return
            if path == "/payment/notify":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                form = self._form()
                if not _csrf_ok(session, form.get("csrf", "")):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "csrf_failed"})
                    return
                method = str(form.get("method") or "BANK_TRANSFER").upper()
                if method == "CRYPTO" and not crypto_enabled:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "crypto_payment_disabled"})
                    return
                try:
                    store.submit_payment(str(session.get("username") or ""), method=method, package=form.get("package", "PREMIUM_30D"), note=form.get("note", ""))
                except (ValueError, accounts.AccountStoreError):
                    self._redirect("/premium")
                    return
                self._redirect("/premium")
                return
            if path == "/admin/memberships/plan":
                form = self._form()
                session = self._csrf_admin(form)
                if not session:
                    return
                try:
                    store.set_plan(form.get("username", ""), form.get("plan", ""), days=max(1, min(_int(form.get("days"), premium_days), 3650)), actor=str(session.get("username") or config.username))
                except (ValueError, accounts.AccountStoreError) as exc:
                    self._membership_redirect(error=str(exc))
                else:
                    if hasattr(sessions, "delete_username"):
                        sessions.delete_username(form.get("username", ""))
                    self._membership_redirect(message="Üyelik planı güncellendi.")
                return
            if path == "/admin/payments/decision":
                form = self._form()
                session = self._csrf_admin(form)
                if not session:
                    return
                decision = str(form.get("decision") or "").lower()
                if decision not in {"approve", "reject"}:
                    self._membership_redirect(error="Geçersiz ödeme kararı.")
                    return
                try:
                    store.decide_payment(form.get("payment_id", ""), approve=decision == "approve", actor=str(session.get("username") or config.username), premium_days=premium_days)
                except (ValueError, accounts.AccountStoreError) as exc:
                    self._membership_redirect(error=str(exc))
                else:
                    self._membership_redirect(message="Ödeme bildirimi sonuçlandırıldı.")
                return
            return super().do_POST()

    return V3Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.0 herkese açık vitrin ve ticari üyelik sistemi.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = commercial_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v3_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} public_home=on plans=FREE,PREMIUM,ADMIN crypto_payment={'on' if env_bool('PANEL_CRYPTO_PAYMENT_ENABLED', False) else 'off'} signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
