"""Kripto Kontrol Merkezi V3.14.2 - Ana/Yedek Grafik Koordinasyonu.

V3.14.1 grafik kurtarma katmanını korur, ancak yedek grafiğin ana canvas yüklenirken
fazla erken görünmesini engeller. Ana grafik hazır olduğunda yedek SVG otomatik çekilir.

Sinyal, strateji, radar, Telegram, emir, TP/SL hesaplama ve state/ledger yazma davranışı değiştirilmez.
Yeni periyodik GitHub Actions işi eklenmez.
"""
from __future__ import annotations

import argparse
import html
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_14_2_CHART_COORDINATION_2026_08_16"

COORD_CSS = r'''
#chartRecovery[data-v3142-wait="1"]{visibility:hidden!important}
#chartRecoveryNote[data-v3142-wait="1"]{display:none!important}
'''

COORD_SCRIPT = r'''
<script nonce="__NONCE__" id="v3142-chart-coordination-script">
(() => {
  'use strict';
  if (window.__v3142ChartCoordination) return;
  window.__v3142ChartCoordination = true;
  const $ = id => document.getElementById(id);
  const canvas = $('chart');
  const recovery = $('chartRecovery');
  const note = $('chartRecoveryNote');
  const overlay = $('levelOverlay');
  const info = $('chartInfo');
  if (!canvas || !recovery || !info) return;

  const GRACE_MS = 6500;
  const POLL_MS = 180;
  let cycle = 0;
  let timer = 0;
  let deadline = 0;

  const currentBar = () => document.querySelector('[data-bar].active')?.dataset?.bar || '15m';

  function markWaiting(waiting) {
    if (waiting) {
      recovery.dataset.v3142Wait = '1';
      if (note) note.dataset.v3142Wait = '1';
    } else {
      delete recovery.dataset.v3142Wait;
      if (note) delete note.dataset.v3142Wait;
    }
  }

  function primaryReady() {
    const meta = canvas.__chart;
    return !!(meta && Array.isArray(meta.candles) && meta.candles.length);
  }

  function preferPrimary() {
    if (!primaryReady()) return false;
    markWaiting(false);
    recovery.style.display = 'none';
    recovery.style.visibility = '';
    if (note) {
      note.classList.remove('on');
      note.style.display = '';
    }
    if (overlay) overlay.style.display = '';
    const count = canvas.__chart?.candles?.length || 0;
    if (/^Yedek grafik/i.test(info.textContent || '')) {
      info.textContent = `Ana grafik · ${count} mum · ${currentBar()}`;
    }
    return true;
  }

  function revealRecoveryIfNeeded() {
    if (preferPrimary()) return;
    markWaiting(false);
    recovery.style.visibility = '';
    if (note) note.style.display = '';
  }

  function startCycle() {
    cycle += 1;
    const mine = cycle;
    deadline = Date.now() + GRACE_MS;
    markWaiting(true);
    if (timer) clearInterval(timer);
    timer = setInterval(() => {
      if (mine !== cycle) return;
      if (preferPrimary()) {
        clearInterval(timer);
        timer = 0;
        return;
      }
      if (Date.now() >= deadline) {
        revealRecoveryIfNeeded();
        clearInterval(timer);
        timer = 0;
      }
    }, POLL_MS);
  }

  const observer = new MutationObserver(() => {
    if (preferPrimary()) return;
    const text = info.textContent || '';
    if (/Grafik alınamadı|verisi alınamadı/i.test(text)) {
      revealRecoveryIfNeeded();
    }
  });
  observer.observe(info, {childList:true, subtree:true, characterData:true});

  $('bars')?.addEventListener('click', startCycle);
  $('loadBtn')?.addEventListener('click', startCycle);
  $('symbolInput')?.addEventListener('keydown', event => {
    if (event.key === 'Enter') startCycle();
  });
  window.addEventListener('resize', () => setTimeout(preferPrimary, 80));

  startCycle();
})();
</script>
'''


def enhance_coordination_page(body: str, nonce: str) -> str:
    if 'id="v3142-chart-coordination-script"' in body:
        return body
    if 'id="chartRecovery"' not in body or 'id="chart"' not in body or "</style>" not in body or "</body>" not in body:
        raise RuntimeError("V3.14.2 grafik koordinasyon ankrajları bulunamadı.")
    body = body.replace("</style>", COORD_CSS + "\n</style>", 1)
    script = COORD_SCRIPT.replace("__NONCE__", html.escape(str(nonce), quote=True))
    return body.replace("</body>", script + "\n</body>", 1)


def make_v3142_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = chartfix.make_v3141_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V3142Handler(BaseHandler):
        server_version = "KriptoPanel/3.14.2"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if (
                status == HTTPStatus.OK
                and isinstance(body, str)
                and content_type.startswith("text/html")
                and urllib.parse.urlsplit(self.path).path == "/coin-center"
                and nonce
            ):
                body = enhance_coordination_page(body, str(nonce))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            if urllib.parse.urlsplit(self.path).path == "/healthz":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "version": VERSION,
                        "coin_center": True,
                        "chart_recovery": True,
                        "chart_coordination": True,
                        "primary_chart_grace_ms": 6500,
                        "secondary_candle_source": True,
                        "signal_engine": "unchanged",
                        "telegram": "unchanged",
                    },
                )
                return
            return super().do_GET()

    return V3142Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.14.2 Ana/Yedek Grafik Koordinasyonu")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = lifecycle.lifecycle_store_from_env(config)
    market_client = chartfix.ResilientMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    server = ThreadingHTTPServer(
        (args.host, args.port),
        make_v3142_handler(config, service, sessions, limiter, store, market_client, overview_client),
    )
    print(
        f"{VERSION} http://{args.host}:{args.port} chart_coordination=on "
        "primary_grace_ms=6500 recovery=on signal_engine=unchanged"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
