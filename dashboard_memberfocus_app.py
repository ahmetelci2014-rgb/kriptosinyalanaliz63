"""Kripto Kontrol Merkezi V3.23 - Üye Odaklı Ana Ekran.

V3.22 veri kaynağı güven etiketlerini korur. Yalnız sunum/erişim katmanında:
- Üye ana ekranına görev odaklı hızlı işlem merkezi ekler.
- Coin'i doğrudan Coin Merkezi'nde açan arama kutusu ekler.
- Mobil alt menüye Sonuçlar kısayolu ekler.
- Admin iç geliştirme araçlarını ve canlı kripto çekirdeğini değiştirmez.

Sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger yazımı ve
otomatik filtre davranışı değiştirilmez.
"""
from __future__ import annotations

import argparse
import html
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_provenance_app as provenance
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_23_MEMBER_FOCUS_2026_08_16"

CSS = r'''
.v323-focus{margin:0 0 16px;border:1px solid rgba(44,230,191,.22);border-radius:16px;background:linear-gradient(135deg,rgba(12,31,40,.97),rgba(7,19,26,.98));padding:14px;box-shadow:0 12px 34px rgba(0,0,0,.12)}
.v323-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.v323-head span{display:block;color:#2ce6bf;font-size:8px;font-weight:950;letter-spacing:.08em}.v323-head h2{margin:2px 0 2px;font-size:17px}.v323-head p{margin:0;color:#789491;font-size:9px}.v323-safe{border:1px solid rgba(105,169,255,.25);border-radius:999px;padding:5px 8px;color:#69a9ff;font-size:7px;font-weight:950;white-space:nowrap}
.v323-actions{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:11px}.v323-action{border:1px solid #1b3943;background:#07151c;color:#dbe9e7;border-radius:11px;padding:10px;text-align:left;cursor:pointer;min-height:70px}.v323-action:hover{border-color:rgba(44,230,191,.42)}.v323-action b{display:block;font-size:11px}.v323-action small{display:block;color:#718b87;font-size:8px;margin-top:3px}.v323-action span{display:inline-grid;place-items:center;width:23px;height:23px;border-radius:7px;background:rgba(44,230,191,.08);color:#2ce6bf;margin-bottom:6px;font-weight:950}
.v323-coin{display:grid;grid-template-columns:1fr auto;gap:7px;margin-top:8px}.v323-coin input{min-width:0;border:1px solid #1b3943;background:#06131a;color:#eef8f6;border-radius:10px;padding:10px 11px;outline:none;font-size:11px}.v323-coin input:focus{border-color:#2ce6bf}.v323-coin button{border:1px solid #2ce6bf;background:#2ce6bf;color:#04120f;border-radius:10px;padding:9px 12px;font-size:10px;font-weight:900;cursor:pointer}.v323-flow{margin-top:8px;color:#68837f;font-size:8px}
@media(max-width:760px){.v323-focus{padding:12px;border-radius:14px;margin-bottom:12px}.v323-head{flex-direction:column}.v323-safe{align-self:flex-start}.v323-actions{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;padding-bottom:3px}.v323-action{flex:0 0 145px;scroll-snap-align:start}.v323-coin{grid-template-columns:1fr auto}.mobile-nav .v323-mobile-results{display:flex}}
@media(max-width:430px){.v323-coin{grid-template-columns:1fr}.v323-coin button{min-height:40px}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v323-member-focus-script">
(()=>{'use strict';if(window.__v323MemberFocus)return;window.__v323MemberFocus=true;
const cleanSymbol=value=>{let s=String(value||'').toUpperCase().replace(/[^A-Z0-9]/g,'');if(!s)return '';if(!s.endsWith('USDT'))s+='USDT';return s};
function switchView(view){const target=[...document.querySelectorAll('[data-view]')].find(el=>el.dataset.view===view);if(target)target.click()}
document.addEventListener('click',event=>{const btn=event.target.closest('[data-v323-view]');if(!btn)return;event.preventDefault();switchView(btn.dataset.v323View)});
function openCoin(){const input=document.getElementById('v323CoinInput');const symbol=cleanSymbol(input?.value);if(!symbol){input?.focus();return}location.assign(`/coin-center?symbol=${encodeURIComponent(symbol)}`)}
document.getElementById('v323CoinOpen')?.addEventListener('click',openCoin);
document.getElementById('v323CoinInput')?.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();openCoin()}});
})();
</script>
'''


def member_focus_block() -> str:
    return '''<section class="v323-focus" id="v323MemberFocus"><div class="v323-head"><div><span>ÜYE · HIZLI KULLANIM</span><h2>Şimdi ne yapmak istiyorsun?</h2><p>Sinyali bul, seviyeleri incele ve sonucu aynı akıştan takip et.</p></div><div class="v323-safe">ANALİZ & TAKİP</div></div><div class="v323-actions"><button class="v323-action" type="button" data-v323-view="signals"><span>⚡</span><b>Açık sinyalleri gör</b><small>Aktif Premium / Scalp / radar kayıtlarını incele.</small></button><button class="v323-action" type="button" data-v323-view="trades"><span>↕</span><b>Seviyeleri kontrol et</b><small>Giriş, TP ve SL seviyelerini tek listede gör.</small></button><button class="v323-action" type="button" data-v323-view="results"><span>✓</span><b>Sonuçları incele</b><small>TP / SL / BE geçmişini ve R sonuçlarını kontrol et.</small></button></div><div class="v323-coin"><input id="v323CoinInput" inputmode="text" autocomplete="off" placeholder="Coin yaz: BTC, ETH, SOL..." aria-label="Coin sembolü"><button id="v323CoinOpen" type="button">Coin Merkezi'nde aç</button></div><div class="v323-flow">Önerilen akış: Sinyal → Coin Merkezi / Grafik → Giriş-TP-SL → Sonuç takibi. Panel gerçek hesapta otomatik emir açmaz.</div></section>'''


def enhance_member_home(body: str, nonce: str) -> str:
    if 'id="v323MemberFocus"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    marker = '<div class="summary" id="homeMetrics"></div>'
    if marker in body:
        body = body.replace(marker, member_focus_block() + marker, 1)
    mobile_anchor = '<a href="/account"><span>○</span>Hesap</a>'
    if mobile_anchor in body and 'v323-mobile-results' not in body:
        body = body.replace(mobile_anchor, '<button class="v323-mobile-results" data-view="results"><span>✓</span>Sonuç</button>' + mobile_anchor, 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v323_handler(
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
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = provenance.make_v322_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V323Handler(BaseHandler):
        server_version = "KriptoPanel/3.23"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    is_admin = bool(session) and str(session.get("role") or "").upper() == commercial.ROLE_ADMIN
                    if session and not is_admin:
                        body = enhance_member_home(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status":"ok","version":VERSION,"member_focus":True,"mobile_results_nav":True,
                    "coin_center_shortcut":True,"admin_tools":"unchanged","signal_engine":"unchanged",
                    "telegram":"unchanged","trade_management":"unchanged","ledger_write":"unchanged",
                })
                return
            return super().do_GET()

    return V323Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.23 Üye Odaklı Ana Ekran")
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
    handler = make_v323_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} member_focus=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
