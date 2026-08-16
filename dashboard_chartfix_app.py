"""Kripto Kontrol Merkezi V3.14.1 - Coin grafik onarımı.

V3.14 panel davranışını korur; Coin Merkezi grafiğini iki katmanla sağlamlaştırır:
- OKX mum verisi alınamazsa salt-okunur Binance public mum verisine düşer.
- Tarayıcı canvas grafiği oluşmazsa aynı API sözleşmesinden SVG yedek grafik çizer.

Sinyal, strateji, radar, Telegram, emir, TP/SL hesaplama ve state/ledger yazma davranışı değiştirilmez.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_chartlevel_app as chartlevel
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import (
    LoginRateLimiter,
    MarketDataError,
    OKXMarketDataClient,
    PanelConfig,
    build_service,
)

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_14_1_CHART_REPAIR_2026_08_16"

_BINANCE_BAR = {"1m": "1m", "5m": "5m", "15m": "15m", "1H": "1h", "4H": "4h", "1D": "1d"}
_BAR_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}


class ResilientMarketDataClient(OKXMarketDataClient):
    """OKX birincil, Binance public salt-okunur mum verisi ikincil kaynak."""

    @staticmethod
    def _request_binance(url: str) -> list[list[Any]]:
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "Kripto-Kontrol-Paneli-Chart-Fallback/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, urllib.error.HTTPError) as exc:
            raise MarketDataError(f"Yedek mum kaynağına bağlanılamadı ({type(exc).__name__}).") from exc
        if not isinstance(payload, list) or not payload:
            raise MarketDataError("Yedek mum kaynağı geçerli veri döndürmedi.")
        return [row for row in payload if isinstance(row, list) and len(row) >= 6]

    def _binance_candles(self, symbol: str, bar: str, anchor: int) -> dict[str, Any]:
        interval = _BINANCE_BAR[bar]
        params: dict[str, str] = {"symbol": symbol, "interval": interval, "limit": "120"}
        if anchor:
            params["endTime"] = str((anchor + _BAR_SECONDS[bar] * 40) * 1000)
        query = urllib.parse.urlencode(params)
        attempts = (
            (f"https://fapi.binance.com/fapi/v1/klines?{query}", "BINANCE_FUTURES_PUBLIC_FALLBACK", "FUTURES"),
            (f"https://data-api.binance.vision/api/v3/klines?{query}", "BINANCE_SPOT_PUBLIC_FALLBACK", "SPOT"),
        )
        last_error: MarketDataError | None = None
        for url, source, market_type in attempts:
            try:
                rows = self._request_binance(url)
            except MarketDataError as exc:
                last_error = exc
                continue
            candles: list[dict[str, Any]] = []
            for row in rows:
                try:
                    ts = int(float(row[0]) / 1000)
                    values = [float(row[index]) for index in range(1, 6)]
                except (TypeError, ValueError, IndexError):
                    continue
                if ts <= 0 or not all(math.isfinite(value) for value in values):
                    continue
                candles.append({
                    "ts": ts,
                    "open": values[0],
                    "high": values[1],
                    "low": values[2],
                    "close": values[3],
                    "volume": values[4],
                    "confirmed": None,
                })
            if candles:
                candles.sort(key=lambda item: int(item["ts"]))
                return {
                    "symbol": symbol,
                    "inst_id": symbol,
                    "market_type": market_type,
                    "bar": bar,
                    "candles": candles,
                    "last_price": candles[-1]["close"],
                    "fetched_at": int(time.time()),
                    "anchor": anchor or None,
                    "source": source,
                    "fallback": True,
                }
        raise last_error or MarketDataError("Yedek mum verisi bulunamadı.")

    def get_candles(self, symbol_value: str, bar_value: str, anchor_value: Any = None) -> dict[str, Any]:
        try:
            return super().get_candles(symbol_value, bar_value, anchor_value)
        except MarketDataError as primary_error:
            symbol = self.normalize_symbol(symbol_value)
            bar = self.validate_bar(bar_value)
            anchor = self.normalize_anchor(anchor_value)
            try:
                result = self._binance_candles(symbol, bar, anchor)
            except MarketDataError:
                raise primary_error
            result["primary_source_error"] = "OKX_UNAVAILABLE"
            return result


RECOVERY_CSS = r'''
#chartRecovery{display:none;position:absolute;inset:0;width:100%;height:100%;z-index:2;background:#07151c;border-radius:8px}
#chartRecovery text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.v3141-chart-note{display:none;margin-top:7px;padding:7px 9px;border:1px solid rgba(255,189,89,.22);background:rgba(255,189,89,.045);border-radius:9px;color:#a99168;font-size:8px}
.v3141-chart-note.on{display:block}
'''

RECOVERY_SCRIPT = r'''
<script nonce="__NONCE__" id="v3141-chart-recovery-script">
(() => {
  'use strict';
  if (window.__v3141ChartRecovery) return;
  window.__v3141ChartRecovery = true;
  const $ = id => document.getElementById(id);
  const canvas = $('chart'), svg = $('chartRecovery'), info = $('chartInfo'), note = $('chartRecoveryNote');
  if (!canvas || !svg || !info) return;
  const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const currentSymbol=()=>normalize(new URLSearchParams(location.search).get('symbol')||$('symbolInput')?.value||'BTCUSDT');
  const currentBar=()=>document.querySelector('[data-bar].active')?.dataset?.bar||'15m';
  const fmt=v=>{const n=Number(v);if(!Number.isFinite(n))return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{maximumFractionDigits:2});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{maximumFractionDigits:5});return n.toLocaleString('tr-TR',{maximumFractionDigits:9})};
  const safe=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  let token=0,lastPayload=null,lastSummary=null;
  async function json(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});const p=await r.json();if(!r.ok)throw new Error(p.message||p.error||`HTTP ${r.status}`);return p}
  function setRecovery(on,message=''){
    svg.style.display=on?'block':'none';
    if ($('levelOverlay')) $('levelOverlay').style.display=on?'none':'';
    if (note){note.classList.toggle('on',on);note.textContent=message||'Tarayıcı ana grafiği oluşturamadığı için güvenli yedek grafik gösteriliyor.'}
  }
  function render(payload,summary){
    const candles=Array.isArray(payload?.candles)?payload.candles:[];
    if(!candles.length)throw new Error('Mum verisi boş');
    const wrap=canvas.parentElement, w=Math.max(360,Math.round(wrap.clientWidth||900)), h=Math.max(300,Math.round(wrap.clientHeight||430));
    svg.setAttribute('viewBox',`0 0 ${w} ${h}`);svg.setAttribute('preserveAspectRatio','none');
    const m={l:12,r:88,t:14,b:26}, cw=w-m.l-m.r, ch=h-m.t-m.b;
    const lows=candles.map(c=>Number(c.low)).filter(Number.isFinite), highs=candles.map(c=>Number(c.high)).filter(Number.isFinite);
    let lo=Math.min(...lows),hi=Math.max(...highs);const pad=Math.max((hi-lo)*.065,Math.abs(hi)*.001,1e-10);lo-=pad;hi+=pad;
    const y=v=>m.t+(hi-Number(v))/(hi-lo)*ch, step=cw/candles.length, body=Math.max(2,Math.min(8,step*.62));
    const out=[];out.push(`<rect width="${w}" height="${h}" fill="#07151c"/>`);
    for(let i=0;i<=5;i++){const yy=m.t+ch*i/5,val=hi-(hi-lo)*i/5;out.push(`<line x1="${m.l}" y1="${yy}" x2="${w-m.r}" y2="${yy}" stroke="rgba(126,157,153,.14)"/>`);out.push(`<text x="${w-m.r+6}" y="${yy+4}" fill="#6f8d89" font-size="10">${safe(fmt(val))}</text>`)}
    candles.forEach((c,i)=>{const x=m.l+step*(i+.5),o=y(c.open),cl=y(c.close),hh=y(c.high),ll=y(c.low),up=Number(c.close)>=Number(c.open),color=up?'#42e28c':'#ff627d';out.push(`<line x1="${x}" y1="${hh}" x2="${x}" y2="${ll}" stroke="${color}"/>`);out.push(`<rect x="${x-body/2}" y="${Math.min(o,cl)}" width="${body}" height="${Math.max(1,Math.abs(cl-o))}" fill="${color}"/>`)});
    const trade=Array.isArray(summary?.open_trades)&&summary.open_trades.length?summary.open_trades[0]:null;
    if(trade){const defs=[['entry','Giriş','#69a9ff'],['tp1','TP1','#42e28c'],['tp2','TP2','#42e28c'],['tp3','TP3','#42e28c'],['sl','SL','#ff627d']];for(const [key,label,color] of defs){const value=Number(trade[key]);if(!Number.isFinite(value))continue;let yy=y(value),suffix='';if(yy<m.t){yy=m.t+3;suffix=' ↑'}else if(yy>h-m.b){yy=h-m.b-3;suffix=' ↓'}out.push(`<line x1="${m.l}" y1="${yy}" x2="${w-m.r}" y2="${yy}" stroke="${color}" stroke-width="1.25" stroke-dasharray="6 4"/>`);out.push(`<rect x="${Math.max(m.l+4,w-m.r-142)}" y="${yy-13}" width="138" height="15" rx="4" fill="rgba(4,15,20,.9)"/>`);out.push(`<text x="${Math.max(m.l+8,w-m.r-137)}" y="${yy-3}" fill="${color}" font-size="9" font-weight="700">${label} ${safe(fmt(value))}${suffix}</text>`)}}
    svg.innerHTML=out.join('');setRecovery(true);
    info.textContent=`Yedek grafik · ${candles.length} mum · ${payload.source||'PUBLIC'} · ${currentBar()}`;
  }
  async function recover(force=false){
    if(!force && canvas.__chart){setRecovery(false);return}
    const mine=++token,symbol=currentSymbol(),bar=currentBar();
    try{const [payload,summary]=await Promise.all([json(`/api/market/candles?symbol=${encodeURIComponent(symbol)}&bar=${encodeURIComponent(bar)}`),json(`/api/coin-center/summary?symbol=${encodeURIComponent(symbol)}`)]);if(mine!==token)return;lastPayload=payload;lastSummary=summary;render(payload,summary)}catch(err){if(mine!==token)return;setRecovery(true,'Grafik verisi şu anda alınamadı; otomatik yeniden deneme açık.');svg.setAttribute('viewBox','0 0 800 360');svg.innerHTML='<rect width="800" height="360" fill="#07151c"/><text x="400" y="180" text-anchor="middle" fill="#7f9d99" font-size="13">Grafik verisi alınamadı · yeniden deneniyor…</text>';info.textContent=`Grafik alınamadı: ${err.message}`;setTimeout(()=>recover(true),5000)}
  }
  function verify(){if(canvas.__chart){setRecovery(false);return}recover(true)}
  setTimeout(verify,2200);
  $('bars')?.addEventListener('click',()=>setTimeout(verify,900));
  $('loadBtn')?.addEventListener('click',()=>setTimeout(verify,1100));
  $('symbolInput')?.addEventListener('keydown',e=>{if(e.key==='Enter')setTimeout(verify,1100)});
  window.addEventListener('resize',()=>{if(svg.style.display==='block'&&lastPayload)render(lastPayload,lastSummary)});
  const observer=new MutationObserver(()=>{if(/alınamadı|yükleniyor/i.test(info.textContent||''))setTimeout(verify,700)});observer.observe(info,{childList:true,subtree:true,characterData:true});
})();
</script>
'''


def enhance_recovery_page(body: str, nonce: str) -> str:
    if 'id="chartRecovery"' in body:
        return body
    canvas_anchor = '<canvas class="v314-level-overlay" id="levelOverlay"></canvas>'
    legend_anchor = '<div class="v314-level-legend" id="levelLegend">'
    if canvas_anchor not in body or legend_anchor not in body or "</style>" not in body or "</body>" not in body:
        raise RuntimeError("V3.14.1 grafik kurtarma ankrajları bulunamadı.")
    body = body.replace("</style>", RECOVERY_CSS + "\n</style>", 1)
    body = body.replace(canvas_anchor, canvas_anchor + '<svg id="chartRecovery" role="img" aria-label="Yedek mum grafiği"></svg>', 1)
    note = '<div class="v3141-chart-note" id="chartRecoveryNote"></div>'
    body = body.replace(legend_anchor, note + legend_anchor, 1)
    script = RECOVERY_SCRIPT.replace("__NONCE__", html.escape(str(nonce), quote=True))
    return body.replace("</body>", script + "\n</body>", 1)


def coin_center_page_v3141(nonce: str, initial_symbol: str) -> str:
    return enhance_recovery_page(chartlevel.coin_center_page_v314(nonce, initial_symbol), nonce)


def make_v3141_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = chartlevel.make_v314_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V3141Handler(BaseHandler):
        server_version = "KriptoPanel/3.14.1"

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/healthz":
                self._json(HTTPStatus.OK, {"status":"ok","version":VERSION,"coin_center":True,"chart_recovery":True,"secondary_candle_source":True,"signal_engine":"unchanged","telegram":"unchanged"})
                return
            if parsed.path == "/coin-center":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                if not self._is_premium(session):
                    self._redirect("/premium")
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
                symbol = (query.get("symbol") or ["BTCUSDT"])[0]
                nonce = secrets.token_urlsafe(18)
                self._send(HTTPStatus.OK, coin_center_page_v3141(nonce, symbol), "text/html; charset=utf-8", nonce=nonce)
                return
            return super().do_GET()

    return V3141Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.14.1 Coin Grafik Onarımı")
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
    market_client = ResilientMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    server = ThreadingHTTPServer((args.host, args.port), make_v3141_handler(config, service, sessions, limiter, store, market_client, overview_client))
    print(f"{VERSION} http://{args.host}:{args.port} chart_recovery=on secondary_candle_source=on signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
