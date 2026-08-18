"""NEXORA Control V3.33.0 - marka ve çok dilli sunum katmanı.

V3.32.9 paylaşım kartı ve mobil görünürlük davranışını korur.
Bu sürüm yalnızca HTML sunum katmanına NEXORA marka kabuğu ve TR/EN/AR/DE
dil seçimi ekler. Trading, strategy/config, radar, Telegram, TP/SL/BE,
üyelik/ödeme backend'i ve state/ledger yazımları değişmez.
"""
from __future__ import annotations

import argparse
import os
import secrets
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accountflow_runtime_app as base
import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_sharecard_app as cards
import dashboard_shareui_app as shareui
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "NEXORA_CONTROL_V3_33_0_BRAND_I18N_2026_08_18"
CSS = base.CSS
SCRIPT = base.SCRIPT

LANGS = {
    "tr": {
        "name": "Türkçe",
        "tagline": "Veri. Zamanlama. Disiplin.",
        "trust": "Ölçülen sinyaller. Risk odaklı karar desteği.",
        "nav": {
            "Ana Sayfa": "Ana Sayfa",
            "Sinyaller": "Sinyaller",
            "İşlemler": "İşlemler",
            "Sonuçlar": "Sonuçlar",
            "Piyasa": "Piyasa",
            "Risk": "Risk",
            "Performans": "Performans",
            "Hesap": "Hesap",
            "İzleme Listesi": "İzleme Listesi",
            "Ödeme": "Ödeme",
            "Şifre": "Şifre",
            "Giriş": "Giriş",
            "Çıkış": "Çıkış",
        },
    },
    "en": {
        "name": "English",
        "tagline": "Data. Timing. Discipline.",
        "trust": "Measured signals. Risk-aware decision support.",
        "nav": {
            "Ana Sayfa": "Home",
            "Sinyaller": "Signals",
            "İşlemler": "Trades",
            "Sonuçlar": "Results",
            "Piyasa": "Market",
            "Risk": "Risk",
            "Performans": "Performance",
            "Hesap": "Account",
            "İzleme Listesi": "Watchlist",
            "Ödeme": "Payment",
            "Şifre": "Password",
            "Giriş": "Sign in",
            "Çıkış": "Sign out",
        },
    },
    "ar": {
        "name": "العربية",
        "tagline": "البيانات. التوقيت. الانضباط.",
        "trust": "إشارات مقاسة. دعم قرار يضع المخاطر أولاً.",
        "nav": {
            "Ana Sayfa": "الرئيسية",
            "Sinyaller": "الإشارات",
            "İşlemler": "الصفقات",
            "Sonuçlar": "النتائج",
            "Piyasa": "السوق",
            "Risk": "المخاطر",
            "Performans": "الأداء",
            "Hesap": "الحساب",
            "İzleme Listesi": "قائمة المراقبة",
            "Ödeme": "الدفع",
            "Şifre": "كلمة المرور",
            "Giriş": "تسجيل الدخول",
            "Çıkış": "تسجيل الخروج",
        },
    },
    "de": {
        "name": "Deutsch",
        "tagline": "Daten. Timing. Disziplin.",
        "trust": "Messbare Signale. Risikobewusste Entscheidungsunterstützung.",
        "nav": {
            "Ana Sayfa": "Startseite",
            "Sinyaller": "Signale",
            "İşlemler": "Trades",
            "Sonuçlar": "Ergebnisse",
            "Piyasa": "Markt",
            "Risk": "Risiko",
            "Performans": "Performance",
            "Hesap": "Konto",
            "İzleme Listesi": "Watchlist",
            "Ödeme": "Zahlung",
            "Şifre": "Passwort",
            "Giriş": "Anmelden",
            "Çıkış": "Abmelden",
        },
    },
}


def _language(path: str) -> str:
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=20)
    lang = str((query.get("lang") or ["tr"])[0] or "tr").lower()
    return lang if lang in LANGS else "tr"


def _lang_url(path: str, lang: str) -> str:
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=20)
    query["lang"] = [lang]
    flat = []
    for key, values in query.items():
        for value in values:
            flat.append((key, value))
    return urllib.parse.urlunsplit(("", "", parsed.path or "/", urllib.parse.urlencode(flat), parsed.fragment))


def _brand_shell(path: str, lang: str, nonce: str = "") -> str:
    copy = LANGS[lang]
    links = []
    for code in ("tr", "en", "ar", "de"):
        active = " nexora-lang-active" if code == lang else ""
        links.append(
            f'<a class="nexora-lang{active}" href="{_lang_url(path, code)}" '
            f'aria-label="{LANGS[code]["name"]}">{code.upper()}</a>'
        )
    direction = "rtl" if lang == "ar" else "ltr"
    nonce_attr = f' nonce="{nonce}"' if nonce else ""
    script = f"""<script{nonce_attr}>
(function(){{
  try {{
    var selected={lang!r};
    var url=new URL(window.location.href);
    var explicit=url.searchParams.get("lang");
    var saved=window.localStorage.getItem("nexora_lang");
    if (!explicit && saved && ["tr","en","ar","de"].indexOf(saved)>=0 && saved!==selected) {{
      url.searchParams.set("lang", saved);
      window.location.replace(url.toString());
      return;
    }}
    if (explicit) window.localStorage.setItem("nexora_lang", selected);
    document.addEventListener("DOMContentLoaded", function(){{
      document.querySelectorAll('a[href^="/"]').forEach(function(a){{
        try {{
          var u=new URL(a.getAttribute("href"), window.location.origin);
          if (!u.searchParams.has("lang")) u.searchParams.set("lang", selected);
          a.setAttribute("href", u.pathname + u.search + u.hash);
        }} catch (e) {{}}
      }});
    }});
  }} catch (e) {{}}
}})();
</script>"""
    return f"""
<style>
.nexora-brand-shell{{box-sizing:border-box;width:100%;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 18px;margin:0 0 12px;background:linear-gradient(135deg,rgba(7,24,34,.97),rgba(5,42,47,.94));border-bottom:1px solid rgba(63,226,190,.22);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;direction:{direction}}}
.nexora-brand-main{{display:flex;align-items:center;gap:12px;min-width:0}}
.nexora-mark{{width:38px;height:38px;border:1px solid rgba(82,245,211,.62);border-radius:11px;display:grid;place-items:center;color:#73f5d8;font-weight:900;font-size:18px;letter-spacing:-1px;box-shadow:inset 0 0 18px rgba(35,230,190,.08)}}
.nexora-wordmark{{color:#f4fbfa;font-size:19px;font-weight:850;letter-spacing:2.2px;line-height:1}}
.nexora-tagline{{color:#8faeaa;font-size:11px;margin-top:5px;letter-spacing:.35px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.nexora-trust{{color:#a7bfbb;font-size:11px;text-align:end;max-width:320px}}
.nexora-langs{{display:flex;gap:5px;align-items:center;flex-shrink:0}}
.nexora-lang{{display:inline-flex;align-items:center;justify-content:center;min-width:34px;height:30px;padding:0 7px;border:1px solid rgba(160,196,190,.22);border-radius:8px;color:#9fb9b5;text-decoration:none;font-size:10px;font-weight:800;letter-spacing:.5px}}
.nexora-lang:hover,.nexora-lang-active{{color:#061516;background:#62e8cb;border-color:#62e8cb}}
@media(max-width:680px){{.nexora-brand-shell{{padding:10px 12px;gap:9px;flex-wrap:wrap}}.nexora-mark{{width:34px;height:34px}}.nexora-wordmark{{font-size:17px}}.nexora-tagline{{font-size:10px;max-width:180px}}.nexora-trust{{display:none}}.nexora-langs{{margin-inline-start:auto}}}}
</style>
<div class="nexora-brand-shell" data-nexora-brand="v1" data-lang="{lang}">
  <div class="nexora-brand-main">
    <div class="nexora-mark" aria-hidden="true">N</div>
    <div>
      <div class="nexora-wordmark">NEXORA</div>
      <div class="nexora-tagline">{copy["tagline"]}</div>
    </div>
  </div>
  <div class="nexora-trust">{copy["trust"]}</div>
  <nav class="nexora-langs" aria-label="Language">{''.join(links)}</nav>
</div>
{script}
"""


def _apply_brand_i18n(body: str, path: str, nonce: str = "") -> str:
    if 'data-nexora-brand="v1"' in body:
        return body
    lang = _language(path)
    copy = LANGS[lang]

    body = body.replace("Kripto Kontrol Merkezi", "NEXORA Control")
    if "<title>" in body and "NEXORA" not in body.split("</title>", 1)[0]:
        body = body.replace("<title>", "<title>NEXORA · ", 1)

    for source, translated in copy["nav"].items():
        body = body.replace(f">{source}<", f">{translated}<")

    if lang == "ar":
        body = body.replace('<html lang="tr"', '<html lang="ar" dir="rtl"', 1)
    else:
        body = body.replace('<html lang="tr"', f'<html lang="{lang}"', 1)

    marker = "<body"
    pos = body.find(marker)
    if pos < 0:
        return body
    end = body.find(">", pos)
    if end < 0:
        return body
    return body[: end + 1] + _brand_shell(path, lang, nonce) + body[end + 1 :]


def _anchor(row: dict[str, Any]) -> int:
    for key in ("closed_at", "close_time", "ended_at", "opened_at", "open_time", "entry_time", "created_at", "created_ts", "timestamp"):
        value = row.get(key)
        if value in (None, ""):
            continue
        try:
            number = int(float(value))
            if number > 10_000_000_000:
                number //= 1000
            if 1_262_304_000 <= number <= 4_102_444_800:
                return number
        except (TypeError, ValueError, OverflowError):
            pass
    return 0


def make_v3321_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store,
    market_client=None,
    overview_client=None,
    history_cache: earlyperf.HistoricalPulseCache | None = None,
):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview = overview_client or market.OKXMarketOverviewClient(cache_seconds=20)
    BaseHandler = base.make_v3321_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        candle_client,
        overview,
        history_cache=history_cache,
    )

    class V3330NexoraHandler(BaseHandler):
        server_version = "NEXORA/3.33.0-brand-i18n"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                session = self._session()
                if session and self._is_premium(session):
                    parsed = urllib.parse.urlsplit(self.path)
                    path = parsed.path
                    if 'id="page-home"' in body:
                        body = shareui.enhance_desktop(body, str(nonce or ""))
                    if path in {"/", "/mobile"} and shareui.is_mobile_server_page(body):
                        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=12)
                        view = str((query.get("view") or ["home"])[0] or "home")
                        body = shareui.enhance_mobile(body, self._safe_data(), view=view)
                body = _apply_brand_i18n(body, self.path, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def _share_record(self, parsed):
            session = self._session()
            if not session:
                self._redirect("/login")
                return None
            if not self._is_premium(session):
                self._redirect("/premium")
                return None
            query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False, max_num_fields=16)
            found = cards.find_record(self._safe_data(), query)
            if not found:
                self._send(
                    HTTPStatus.NOT_FOUND,
                    '<!doctype html><html lang="tr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="background:#061018;color:#eef6f4;font-family:system-ui;padding:24px"><h2>İşlem kaydı bulunamadı</h2><p>İşlem listesi yenilenmiş olabilir. Sinyaller veya Sonuçlar ekranından tekrar Paylaş seçin.</p><a style="color:#2ce6bf" href="/">NEXORA Control</a></body></html>',
                    "text/html; charset=utf-8",
                )
                return None
            kind, row = found
            stage = str((query.get("stage") or ["result" if kind == "result" else "signal"])[0] or "signal")
            if stage not in {"signal", "tracking", "result"}:
                stage = "result" if kind == "result" else "signal"
            return kind, stage, row

        def _share_candles(self, row: dict[str, Any]):
            try:
                payload = candle_client.get_candles(cards.symbol(row), "15m", _anchor(row) or None)
                candles = payload.get("candles") if isinstance(payload, dict) else []
                source = str(payload.get("source") or "PUBLIC") if isinstance(payload, dict) else "PUBLIC"
                return [item for item in (candles or []) if isinstance(item, dict)], source
            except Exception:
                try:
                    payload = candle_client.get_candles(cards.symbol(row), "15m")
                    candles = payload.get("candles") if isinstance(payload, dict) else []
                    source = str(payload.get("source") or "PUBLIC") if isinstance(payload, dict) else "PUBLIC"
                    return [item for item in (candles or []) if isinstance(item, dict)], source
                except Exception:
                    return [], "PUBLIC_DATA_UNAVAILABLE"

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "brand": "NEXORA",
                    "languages": ["tr", "en", "ar", "de"],
                    "brand_layer": "html_presentation_only",
                    "base_runtime": "V3.32.9 preserved",
                    "share_cards": "premium_admin_real_trade_data",
                    "share_buttons": "signals_trades_results_desktop_mobile",
                    "mobile_share_injection": "structural_server_mobile_detection",
                    "share_chart": "public_15m_candles_server_svg",
                    "share_png": "browser_export_web_share_download_fallback",
                    "share_results": "tp_sl_be_supported",
                    "share_free": "blocked",
                    "share_user_identity": "not_rendered",
                    "mobile_runtime": "server_rendered_core_preserved",
                    "free_runtime": "separate_preserved",
                    "membership_backend": "unchanged",
                    "payment_backend": "unchanged",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            if path in {"/share/trade", "/share/card.svg"}:
                selected = self._share_record(parsed)
                if not selected:
                    return
                kind, stage, row = selected
                candles, source = self._share_candles(row)
                if path == "/share/card.svg":
                    self._send(
                        HTTPStatus.OK,
                        cards.render_svg(row, kind=kind, stage=stage, candles=candles, source=source),
                        "image/svg+xml; charset=utf-8",
                    )
                    return
                nonce = secrets.token_urlsafe(18)
                self._send(
                    HTTPStatus.OK,
                    cards.render_page(row, kind=kind, stage=stage, candles=candles, source=source, nonce=nonce),
                    "text/html; charset=utf-8",
                    nonce=nonce,
                )
                return
            return super().do_GET()

    return V3330NexoraHandler


def main() -> None:
    parser = argparse.ArgumentParser(description="NEXORA Control V3.33.0 marka ve çok dilli panel katmanı")
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
    candle_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v3321_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} brand=NEXORA i18n=tr,en,ar,de signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
