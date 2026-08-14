"""Şifreli ve canlı Kripto Kontrol Paneli web sunucusu.

GÜVENLİK SINIRLARI
- Yalnız JSON kayıtlarını okur.
- Borsaya bağlanmaz, emir açmaz ve kullanıcı varlığı tutmaz.
- Telegram göndermez ve canlı strateji dosyalarını değiştirmez.
- Private GitHub erişim anahtarını hiçbir zaman tarayıcıya göndermez.

Harici web framework kullanmaz; düşük trafikli kapalı beta için Python 3.11
standart kütüphanesiyle çalışır. HTTPS sonlandırması barındırma sağlayıcısında
yapılmalıdır.
"""

from __future__ import annotations

import argparse
import copy
import getpass
import hashlib
import hmac
import html
import json
import math
import os
import re
import secrets
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from dashboard_builder import build_dashboard_data, render_dashboard


VERSION = "KRIPTO_KONTROL_PANELI_LIVE_V1_5_2026_08_14"
PASSWORD_ITERATIONS = 310_000
SESSION_COOKIE = "panel_session"
LOGIN_CSRF_COOKIE = "panel_login_csrf"

DATA_FILES = (
    "open_signals.json",
    "scalp_radar_state.json",
    "pump_radar_state.json",
    "new_listing_performance_ledger.json",
    "trade_ledger.json",
    "scalp_performance_ledger.json",
    "pump_performance_ledger.json",
    "system_control_center_report.json",
)

MARKET_BARS = {"1m", "5m", "15m", "1H", "4H", "1D"}
MARKET_BAR_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1H": 3600,
    "4H": 14_400,
    "1D": 86_400,
}
MARKET_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{2,15}USDT$")


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def password_hash(password: str, iterations: int = PASSWORD_ITERATIONS) -> str:
    if not password:
        raise ValueError("Şifre boş olamaz.")
    salt = secrets.token_bytes(18)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return "$".join(
        ("pbkdf2_sha256", str(iterations), salt.hex(), digest.hex())
    )


def verify_password(candidate: str, configured_hash: str | None, configured_plain: str | None) -> bool:
    if configured_hash:
        try:
            algorithm, iterations_text, salt_hex, digest_hex = configured_hash.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            iterations = int(iterations_text)
            if iterations < 100_000 or iterations > 2_000_000:
                return False
            expected = bytes.fromhex(digest_hex)
            actual = hashlib.pbkdf2_hmac(
                "sha256",
                candidate.encode("utf-8"),
                bytes.fromhex(salt_hex),
                iterations,
            )
            return hmac.compare_digest(actual, expected)
        except (TypeError, ValueError):
            return False
    if configured_plain is None:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), configured_plain.encode("utf-8"))


@dataclass(frozen=True)
class PanelConfig:
    username: str
    password: str | None
    password_hash_value: str | None
    repository: str
    ref: str
    github_token: str | None
    root: Path
    refresh_seconds: int
    cookie_secure: bool
    trust_proxy: bool
    session_hours: int

    @classmethod
    def from_env(cls, root: Path | str = ".") -> "PanelConfig":
        return cls(
            username=os.getenv("PANEL_USERNAME", "ahmet").strip() or "ahmet",
            password=os.getenv("PANEL_PASSWORD"),
            password_hash_value=os.getenv("PANEL_PASSWORD_HASH"),
            repository=os.getenv(
                "GITHUB_REPOSITORY",
                "ahmetelci2014-rgb/kriptosinyalanaliz63",
            ).strip(),
            ref=os.getenv("GITHUB_REF_NAME", "main").strip() or "main",
            github_token=os.getenv("GITHUB_PANEL_TOKEN"),
            root=Path(root),
            refresh_seconds=max(
                10,
                min(int(os.getenv("PANEL_REFRESH_SECONDS", "30")), 300),
            ),
            cookie_secure=env_bool("PANEL_COOKIE_SECURE", True),
            trust_proxy=env_bool("PANEL_TRUST_PROXY", False),
            session_hours=max(
                1,
                min(int(os.getenv("PANEL_SESSION_HOURS", "12")), 72),
            ),
        )

    def validate(self) -> None:
        if not self.password and not self.password_hash_value:
            raise RuntimeError(
                "PANEL_PASSWORD veya PANEL_PASSWORD_HASH tanımlanmalıdır."
            )
        if self.github_token:
            if "/" not in self.repository:
                raise RuntimeError("GITHUB_REPOSITORY owner/repo biçiminde olmalıdır.")
        elif not (self.root / "open_signals.json").exists():
            raise RuntimeError(
                "Canlı veri için GITHUB_PANEL_TOKEN tanımlayın veya JSON "
                "dosyalarının bulunduğu repo kökünü --root ile verin."
            )


class SessionStore:
    def __init__(self, lifetime_seconds: int):
        self.lifetime_seconds = lifetime_seconds
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, username: str) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(32)
        session = {
            "username": username,
            "csrf": secrets.token_urlsafe(24),
            "expires_at": time.time() + self.lifetime_seconds,
        }
        with self._lock:
            self._purge_locked()
            self._sessions[token] = session
        return token, session

    def get(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._lock:
            self._purge_locked()
            session = self._sessions.get(token)
            return dict(session) if session is not None else None

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def _purge_locked(self) -> None:
        now = time.time()
        expired = [
            token
            for token, session in self._sessions.items()
            if float(session.get("expires_at", 0)) <= now
        ]
        for token in expired:
            self._sessions.pop(token, None)


class LoginRateLimiter:
    def __init__(self, max_failures: int = 5, window_seconds: int = 15 * 60):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allowed(self, identity: str) -> bool:
        with self._lock:
            self._trim_locked(identity)
            return len(self._failures.get(identity, [])) < self.max_failures

    def record_failure(self, identity: str) -> None:
        with self._lock:
            self._trim_locked(identity)
            self._failures.setdefault(identity, []).append(time.time())

    def clear(self, identity: str) -> None:
        with self._lock:
            self._failures.pop(identity, None)

    def _trim_locked(self, identity: str) -> None:
        cutoff = time.time() - self.window_seconds
        recent = [
            timestamp
            for timestamp in self._failures.get(identity, [])
            if timestamp >= cutoff
        ]
        if recent:
            self._failures[identity] = recent
        else:
            self._failures.pop(identity, None)


class GitHubJsonSource:
    def __init__(self, repository: str, ref: str, token: str):
        self.repository = repository
        self.ref = ref
        self.token = token
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_lock = threading.Lock()

    def _fetch_one(self, filename: str) -> tuple[str, Any, str | None]:
        path = urllib.parse.quote(filename, safe="/")
        ref = urllib.parse.quote(self.ref, safe="")
        url = (
            "https://api.github.com/repos/"
            f"{self.repository}/contents/{path}?ref={ref}"
        )
        headers = {
            "Accept": "application/vnd.github.raw+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "Kripto-Kontrol-Paneli-Live",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        with self._cache_lock:
            cached = self._cache.get(filename)
        if cached and cached.get("etag"):
            headers["If-None-Match"] = str(cached["etag"])

        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                document = json.loads(response.read().decode("utf-8"))
                etag = response.headers.get("ETag")
                with self._cache_lock:
                    self._cache[filename] = {
                        "etag": etag,
                        "document": document,
                    }
                return filename, document, None
        except urllib.error.HTTPError as exc:
            if exc.code == HTTPStatus.NOT_MODIFIED and cached:
                return filename, cached["document"], None
            if cached:
                return (
                    filename,
                    cached["document"],
                    f"{filename}: GitHub HTTP {exc.code}; son geçerli veri kullanıldı",
                )
            return filename, {}, f"{filename}: GitHub HTTP {exc.code}"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            if cached:
                return (
                    filename,
                    cached["document"],
                    f"{filename}: {type(exc).__name__}; son geçerli veri kullanıldı",
                )
            return filename, {}, f"{filename}: {type(exc).__name__}"

    def snapshot(self) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
        documents: dict[str, Any] = {}
        warnings: list[str] = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(self._fetch_one, filename): filename
                for filename in DATA_FILES
            }
            for future in as_completed(futures):
                filename = futures[future]
                try:
                    name, document, warning = future.result()
                except Exception as exc:
                    name, document = filename, {}
                    warning = f"{filename}: beklenmeyen {type(exc).__name__}"
                documents[name] = document
                if warning:
                    warnings.append(warning)
        return documents, warnings, {
            "mode": "GITHUB_PRIVATE_SERVER_SIDE",
            "repository": self.repository,
            "ref": self.ref,
            "checked_at": int(time.time()),
        }


class LocalJsonSource:
    def __init__(self, root: Path):
        self.root = root

    def snapshot(self) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
        documents: dict[str, Any] = {}
        warnings: list[str] = []
        for filename in DATA_FILES:
            path = self.root / filename
            try:
                with path.open("r", encoding="utf-8") as handle:
                    documents[filename] = json.load(handle)
            except (OSError, json.JSONDecodeError) as exc:
                documents[filename] = {}
                warnings.append(f"{filename}: {type(exc).__name__}")
        return documents, warnings, {
            "mode": "LOCAL_REPOSITORY_FILES",
            "repository": None,
            "ref": None,
            "checked_at": int(time.time()),
        }


class MarketDataError(RuntimeError):
    """Herkese açık piyasa verisi güvenli biçimde alınamadığında kullanılır."""


class OKXMarketDataClient:
    """API anahtarı kullanmadan OKX public mum verisini okur ve kısa süre saklar."""

    def __init__(self, cache_seconds: int = 20):
        self.cache_seconds = max(5, min(int(cache_seconds), 60))
        self._cache: dict[tuple[str, str, int], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def normalize_symbol(value: str) -> str:
        symbol = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
        if not MARKET_SYMBOL_PATTERN.fullmatch(symbol):
            raise ValueError("Sembol BTCUSDT biçiminde bir USDT paritesi olmalıdır.")
        return symbol

    @staticmethod
    def validate_bar(value: str) -> str:
        if value not in MARKET_BARS:
            raise ValueError("Desteklenmeyen mum periyodu.")
        return value

    @staticmethod
    def normalize_anchor(value: Any) -> int:
        if value in (None, "", 0, "0"):
            return 0
        try:
            anchor = int(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError("Geçersiz işlem zamanı.") from exc
        if anchor < 1_262_304_000 or anchor > int(time.time()) + 86_400:
            raise ValueError("İşlem zamanı desteklenen aralığın dışında.")
        return anchor

    def _request_candles(self, inst_id: str, bar: str, anchor: int = 0) -> list[list[Any]]:
        params = {
            "instId": inst_id,
            "bar": bar,
            "limit": "120",
        }
        endpoint = "candles"
        if anchor:
            endpoint = "history-candles"
            params["after"] = str(
                (anchor + MARKET_BAR_SECONDS[bar] * 40) * 1000
            )
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"https://www.okx.com/api/v5/market/{endpoint}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "Kripto-Kontrol-Paneli-Market/1.1",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            urllib.error.HTTPError,
        ) as exc:
            raise MarketDataError(f"OKX bağlantısı kurulamadı ({type(exc).__name__}).") from exc
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise MarketDataError("OKX geçerli mum verisi döndürmedi.")
        rows = payload.get("data")
        if not isinstance(rows, list) or not rows:
            raise MarketDataError("Bu parite için mum verisi bulunamadı.")
        return [row for row in rows if isinstance(row, list) and len(row) >= 6]

    def get_candles(
        self,
        symbol_value: str,
        bar_value: str,
        anchor_value: Any = None,
    ) -> dict[str, Any]:
        symbol = self.normalize_symbol(symbol_value)
        bar = self.validate_bar(bar_value)
        anchor = self.normalize_anchor(anchor_value)
        cache_key = (symbol, bar, anchor)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.cache_seconds:
                return copy.deepcopy(cached[1])

        base = symbol[:-4]
        attempts = (
            (f"{base}-USDT-SWAP", "SWAP"),
            (f"{base}-USDT", "SPOT"),
        )
        last_error: MarketDataError | None = None
        rows: list[list[Any]] = []
        inst_id = ""
        market_type = ""
        for candidate, candidate_type in attempts:
            try:
                rows = self._request_candles(candidate, bar, anchor)
                inst_id = candidate
                market_type = candidate_type
                break
            except MarketDataError as exc:
                last_error = exc
        if not rows:
            raise last_error or MarketDataError("Piyasa verisi bulunamadı.")

        candles: list[dict[str, Any]] = []
        for row in reversed(rows):
            try:
                values = [float(row[index]) for index in range(1, 6)]
                timestamp = int(float(row[0]) / 1000)
            except (TypeError, ValueError, IndexError):
                continue
            if not all(math.isfinite(value) for value in values) or timestamp <= 0:
                continue
            candles.append({
                "ts": timestamp,
                "open": values[0],
                "high": values[1],
                "low": values[2],
                "close": values[3],
                "volume": values[4],
                "confirmed": str(row[8]) == "1" if len(row) > 8 else None,
            })
        if not candles:
            raise MarketDataError("Mum verisi çözümlenemedi.")
        result = {
            "symbol": symbol,
            "inst_id": inst_id,
            "market_type": market_type,
            "bar": bar,
            "candles": candles,
            "last_price": candles[-1]["close"],
            "fetched_at": int(time.time()),
            "anchor": anchor or None,
            "source": "OKX_PUBLIC_NO_API_KEY",
        }
        with self._lock:
            self._cache[cache_key] = (time.monotonic(), result)
        return copy.deepcopy(result)


class LiveDashboardService:
    def __init__(self, source: GitHubJsonSource | LocalJsonSource, cache_seconds: int):
        self.source = source
        self.cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._last_data: dict[str, Any] | None = None
        self._last_refresh = 0.0

    def get_data(self, force: bool = False) -> dict[str, Any]:
        if (
            not force
            and self._last_data is not None
            and time.monotonic() - self._last_refresh < self.cache_seconds
        ):
            return copy.deepcopy(self._last_data)

        with self._lock:
            if (
                not force
                and self._last_data is not None
                and time.monotonic() - self._last_refresh < self.cache_seconds
            ):
                return copy.deepcopy(self._last_data)
            try:
                documents, warnings, source_meta = self.source.snapshot()
                with tempfile.TemporaryDirectory(prefix="kripto-panel-") as directory:
                    root = Path(directory)
                    for filename in DATA_FILES:
                        (root / filename).write_text(
                            json.dumps(
                                documents.get(filename, {}),
                                ensure_ascii=False,
                            ),
                            encoding="utf-8",
                        )
                    data = build_dashboard_data(root)
                data["version"] = VERSION
                data["live_source"] = source_meta
                if warnings:
                    quality = data.setdefault(
                        "data_quality",
                        {"ok": True, "warnings": []},
                    )
                    quality["ok"] = False
                    quality["warnings"] = sorted(
                        set(list(quality.get("warnings", [])) + warnings)
                    )
                self._last_data = data
                self._last_refresh = time.monotonic()
                return copy.deepcopy(data)
            except Exception as exc:
                if self._last_data is None:
                    raise
                stale = copy.deepcopy(self._last_data)
                quality = stale.setdefault(
                    "data_quality",
                    {"ok": False, "warnings": []},
                )
                quality["ok"] = False
                quality["warnings"] = sorted(
                    set(
                        list(quality.get("warnings", []))
                        + [f"Canlı kaynak yenilenemedi: {type(exc).__name__}"]
                    )
                )
                stale.setdefault("live_source", {})["stale"] = True
                return stale


def cookie_value(
    name: str,
    value: str,
    *,
    max_age: int,
    secure: bool,
    http_only: bool = True,
) -> str:
    parts = [
        f"{name}={value}",
        "Path=/",
        f"Max-Age={max_age}",
        "SameSite=Strict",
    ]
    if http_only:
        parts.append("HttpOnly")
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def login_page(username: str, csrf: str, error: str | None = None) -> str:
    error_html = (
        f'<div class="error">{html.escape(error)}</div>'
        if error
        else ""
    )
    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Kripto Kontrol · Giriş</title>
  <style>
    :root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#86a5a1;--teal:#2ce6bf;--red:#ff748c}}
    *{{box-sizing:border-box}} body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:20px;background:radial-gradient(circle at 80% 0,#10352f,transparent 35%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}
    .card{{width:min(430px,100%);border:1px solid var(--line);border-radius:22px;padding:30px;background:rgba(11,27,35,.96);box-shadow:0 30px 80px rgba(0,0,0,.35)}} .mark{{width:48px;height:48px;border:1px solid rgba(44,230,191,.5);border-radius:15px;background:rgba(44,230,191,.1);display:grid;place-items:center;color:var(--teal);font-size:22px;font-weight:900}} h1{{margin:20px 0 5px;font-size:28px;letter-spacing:-.04em}} p{{color:var(--muted);margin:0 0 22px}} label{{display:block;margin:14px 0 6px;color:#bcd2cf;font-size:12px;font-weight:800}} input{{width:100%;border:1px solid var(--line);border-radius:11px;background:#061219;color:var(--text);padding:12px;outline:none}} input:focus{{border-color:var(--teal)}} button{{width:100%;margin-top:20px;border:0;border-radius:11px;padding:12px;background:var(--teal);color:#03110e;font-weight:900;cursor:pointer}} .error{{border:1px solid rgba(255,116,140,.4);border-radius:10px;background:rgba(255,116,140,.08);color:#ffb1bf;padding:10px;margin-bottom:14px}} .foot{{margin-top:18px;color:var(--muted);font-size:11px;text-align:center}}
  </style>
</head>
<body>
  <form class="card" method="post" action="/login" autocomplete="on">
    <div class="mark">K</div>
    <h1>Kripto Kontrol</h1>
    <p>Canlı ve salt-okunur özel yönetim paneli</p>
    {error_html}
    <input type="hidden" name="csrf" value="{html.escape(csrf, quote=True)}">
    <label for="username">Kullanıcı adı</label>
    <input id="username" name="username" value="{html.escape(username, quote=True)}" required autocomplete="username">
    <label for="password">Şifre</label>
    <input id="password" type="password" name="password" required autocomplete="current-password">
    <button type="submit">Güvenli giriş</button>
    <div class="foot">Emir açmaz · Para tutmaz · Borsa hesabı yönetmez</div>
  </form>
</body>
</html>"""


def make_handler(
    config: PanelConfig,
    service: LiveDashboardService,
    sessions: SessionStore,
    limiter: LoginRateLimiter,
    market_client: OKXMarketDataClient | None = None,
) -> type[BaseHTTPRequestHandler]:
    market_client = market_client or OKXMarketDataClient()

    class PanelHandler(BaseHTTPRequestHandler):
        server_version = "KriptoPanel/1.0"
        sys_version = ""

        def log_message(self, format_text: str, *args: Any) -> None:
            path = urllib.parse.urlsplit(self.path).path
            print(f"{self.client_address[0]} {self.command} {path}")

        def _client_ip(self) -> str:
            if config.trust_proxy:
                forwarded = self.headers.get("X-Forwarded-For", "")
                if forwarded:
                    return forwarded.split(",", 1)[0].strip()
            return str(self.client_address[0])

        def _cookies(self) -> SimpleCookie:
            cookies = SimpleCookie()
            try:
                cookies.load(self.headers.get("Cookie", ""))
            except Exception:
                return SimpleCookie()
            return cookies

        def _cookie(self, name: str) -> str | None:
            morsel = self._cookies().get(name)
            return morsel.value if morsel else None

        def _session(self) -> dict[str, Any] | None:
            return sessions.get(self._cookie(SESSION_COOKIE))

        def _security_headers(self, nonce: str | None = None) -> None:
            if nonce:
                csp = (
                    "default-src 'none'; "
                    f"script-src 'nonce-{nonce}'; "
                    "style-src 'unsafe-inline'; connect-src 'self'; "
                    "img-src 'self' data:; font-src 'self'; "
                    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
                )
            else:
                csp = (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "img-src data:; form-action 'self'; "
                    "frame-ancestors 'none'; base-uri 'none'"
                )
            self.send_header("Content-Security-Policy", csp)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Permissions-Policy",
                "camera=(), microphone=(), geolocation=(), payment=()",
            )
            self.send_header("Cache-Control", "no-store, max-age=0")
            if config.cookie_secure:
                self.send_header(
                    "Strict-Transport-Security",
                    "max-age=31536000; includeSubDomains",
                )

        def _send(
            self,
            status: int,
            body: str | bytes,
            content_type: str,
            *,
            cookies: list[str] | None = None,
            nonce: str | None = None,
        ) -> None:
            raw = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self._security_headers(nonce)
            for value in cookies or []:
                self.send_header("Set-Cookie", value)
            self.end_headers()
            self.wfile.write(raw)

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            self._send(
                status,
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                "application/json; charset=utf-8",
            )

        def _redirect(self, location: str, *, cookies: list[str] | None = None) -> None:
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", location)
            self._security_headers()
            for value in cookies or []:
                self.send_header("Set-Cookie", value)
            self.end_headers()

        def _serve_login(
            self,
            error: str | None = None,
            status: int = HTTPStatus.OK,
        ) -> None:
            csrf = secrets.token_urlsafe(24)
            self._send(
                status,
                login_page(config.username, csrf, error),
                "text/html; charset=utf-8",
                cookies=[
                    cookie_value(
                        LOGIN_CSRF_COOKIE,
                        csrf,
                        max_age=600,
                        secure=config.cookie_secure,
                    )
                ],
            )

        def do_GET(self) -> None:
            parsed_url = urllib.parse.urlsplit(self.path)
            path = parsed_url.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            if path == "/login":
                if self._session():
                    self._redirect("/")
                else:
                    self._serve_login()
                return
            if path == "/api/dashboard":
                if not self._session():
                    self._json(
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "authentication_required"},
                    )
                    return
                try:
                    self._json(HTTPStatus.OK, service.get_data())
                except Exception:
                    self._json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"error": "live_data_unavailable"},
                    )
                return
            if path == "/api/market/candles":
                if not self._session():
                    self._json(
                        HTTPStatus.UNAUTHORIZED,
                        {"error": "authentication_required"},
                    )
                    return
                query = urllib.parse.parse_qs(
                    parsed_url.query,
                    keep_blank_values=True,
                    max_num_fields=4,
                )
                symbol = (query.get("symbol") or [""])[0]
                bar = (query.get("bar") or ["15m"])[0]
                anchor = (query.get("anchor") or [""])[0]
                try:
                    payload = market_client.get_candles(symbol, bar, anchor)
                except ValueError as exc:
                    self._json(
                        HTTPStatus.BAD_REQUEST,
                        {"error": "invalid_market_request", "message": str(exc)},
                    )
                except MarketDataError:
                    self._json(
                        HTTPStatus.BAD_GATEWAY,
                        {"error": "market_data_unavailable"},
                    )
                else:
                    self._json(HTTPStatus.OK, payload)
                return
            if path == "/":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                csrf = html.escape(str(session["csrf"]), quote=True)
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
                    top_action_html=logout,
                )
                self._send(
                    HTTPStatus.OK,
                    body,
                    "text/html; charset=utf-8",
                    nonce=nonce,
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def _form(self) -> dict[str, str]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = 0
            if length <= 0 or length > 8192:
                return {}
            raw = self.rfile.read(length).decode("utf-8", errors="replace")
            values = urllib.parse.parse_qs(
                raw,
                keep_blank_values=True,
                max_num_fields=10,
            )
            return {
                key: rows[0]
                for key, rows in values.items()
                if rows
            }

        def do_POST(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            form = self._form()
            if path == "/login":
                identity = self._client_ip()
                if not limiter.allowed(identity):
                    self._serve_login(
                        "Çok fazla hatalı deneme. 15 dakika sonra tekrar deneyin.",
                        HTTPStatus.TOO_MANY_REQUESTS,
                    )
                    return
                csrf_cookie = self._cookie(LOGIN_CSRF_COOKIE) or ""
                csrf_form = form.get("csrf", "")
                csrf_ok = bool(csrf_cookie) and hmac.compare_digest(
                    csrf_cookie,
                    csrf_form,
                )
                username_ok = hmac.compare_digest(
                    form.get("username", "").encode("utf-8"),
                    config.username.encode("utf-8"),
                )
                password_ok = verify_password(
                    form.get("password", ""),
                    config.password_hash_value,
                    config.password,
                )
                if not (csrf_ok and username_ok and password_ok):
                    limiter.record_failure(identity)
                    self._serve_login(
                        "Kullanıcı adı veya şifre hatalı.",
                        HTTPStatus.UNAUTHORIZED,
                    )
                    return
                limiter.clear(identity)
                token, _session = sessions.create(config.username)
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
            if path == "/logout":
                token = self._cookie(SESSION_COOKIE)
                session = sessions.get(token)
                csrf_ok = bool(session) and hmac.compare_digest(
                    str(session.get("csrf", "")),
                    form.get("csrf", ""),
                )
                if not csrf_ok:
                    self._json(HTTPStatus.FORBIDDEN, {"error": "csrf_failed"})
                    return
                sessions.delete(token)
                self._redirect(
                    "/login",
                    cookies=[
                        cookie_value(
                            SESSION_COOKIE,
                            "",
                            max_age=0,
                            secure=config.cookie_secure,
                        )
                    ],
                )
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    return PanelHandler


def build_service(config: PanelConfig) -> LiveDashboardService:
    if config.github_token:
        source: GitHubJsonSource | LocalJsonSource = GitHubJsonSource(
            config.repository,
            config.ref,
            config.github_token,
        )
    else:
        source = LocalJsonSource(config.root)
    return LiveDashboardService(source, config.refresh_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Şifreli ve canlı Kripto Kontrol Paneli sunucusu."
    )
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("PORT", "8080")),
    )
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--hash-password",
        action="store_true",
        help="Güvenli PANEL_PASSWORD_HASH üretip çıkar.",
    )
    args = parser.parse_args()

    if args.hash_password:
        first = getpass.getpass("Yeni panel şifresi: ")
        second = getpass.getpass("Şifreyi tekrar yazın: ")
        if first != second:
            raise SystemExit("Şifreler eşleşmiyor.")
        print(password_hash(first))
        return

    config = PanelConfig.from_env(args.root)
    config.validate()
    service = build_service(config)
    sessions = SessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    handler = make_handler(config, service, sessions, limiter)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"Kripto Kontrol Paneli hazır: http://{args.host}:{args.port} "
        f"| kaynak={'GitHub private' if config.github_token else 'yerel JSON'}"
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
