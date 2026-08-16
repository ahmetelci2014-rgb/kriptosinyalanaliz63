"""Kripto Kontrol Merkezi V3.14 - Akıllı Coin Geçişi ve Grafik Seviyeleri.

V3.13 Coin İnceleme Merkezi'ni bozmadan sunum katmanını geliştirir:
- Premium/Admin panel kartlarındaki coinleri doğrudan Coin Merkezi'ne bağlar.
- Coin grafiğinde en güncel açık senaryonun Entry/TP1/TP2/TP3/SL seviyelerini salt-okunur overlay olarak gösterir.
- Seviye görünümü kullanıcı tarafından açılıp kapatılabilir ve tarayıcıda hatırlanır.

Sinyal, strateji, radar, Telegram, emir, TP/SL hesaplama ve state/ledger yazma davranışı değiştirilmez.
Yeni periyodik GitHub Actions işi eklenmez.
"""
from __future__ import annotations

import argparse
import html
import os
import secrets
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path

import dashboard_accounts_app as accounts
import dashboard_coin_app as coin
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_14_SMART_LINKS_LEVELS_2026_08_16"


SMART_LINK_CSS = r'''
<style id="v314-smart-link-css">
.v314-coin-clickable{cursor:pointer!important}
.v314-coin-clickable:hover{outline-color:rgba(44,230,191,.24)}
.v314-coin-clickable[data-v314-ready="1"]{position:relative}
.v314-coin-clickable[data-v314-ready="1"]:not(button):not(a)::after{content:"Coin Merkezi";position:absolute;right:7px;top:7px;z-index:2;opacity:0;pointer-events:none;border:1px solid rgba(44,230,191,.24);background:rgba(4,18,23,.88);color:#74cbbb;border-radius:999px;padding:3px 6px;font-size:7px;font-weight:900;transition:opacity .16s ease}
.v314-coin-clickable[data-v314-ready="1"]:hover::after{opacity:1}
@media(max-width:760px){.v314-coin-clickable[data-v314-ready="1"]::after{display:none}}
</style>
'''


SMART_LINK_SCRIPT = r'''
<script nonce="__NONCE__" id="v314-smart-link-script">
(() => {
  'use strict';
  if (window.__v314SmartLinks) return;
  window.__v314SmartLinks = true;
  const normalize = value => String(value || '').toUpperCase().replace(/[^A-Z0-9]/g, '').replace(/USDTUSDT$/, 'USDT');
  const symbolOf = node => normalize(node?.dataset?.focusSymbol || node?.dataset?.symbol || '');
  const valid = symbol => /^[A-Z0-9]{2,15}USDT$/.test(symbol);
  function decorate(root = document) {
    root.querySelectorAll('[data-focus-symbol],[data-symbol]').forEach(node => {
      const symbol = symbolOf(node);
      if (!valid(symbol)) return;
      node.classList.add('v314-coin-clickable');
      node.dataset.v314Ready = '1';
      if (!node.title) node.title = `${symbol} · Coin Merkezi'nde incele`;
    });
  }
  document.addEventListener('click', event => {
    const node = event.target.closest('[data-focus-symbol],[data-symbol]');
    if (!node) return;
    if (event.target.closest('a,button,input,select,textarea,label,.score-chip,[role="button"]')) return;
    const symbol = symbolOf(node);
    if (!valid(symbol)) return;
    event.preventDefault();
    location.assign(`/coin-center?symbol=${encodeURIComponent(symbol)}`);
  }, true);
  const observer = new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) if (node.nodeType === 1) decorate(node);
  });
  observer.observe(document.documentElement, {childList:true, subtree:true});
  decorate();
})();
</script>
'''


LEVEL_CSS = r'''
.v314-level-overlay{position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:3}
.v314-level-legend{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:8px;padding:8px 9px;border:1px solid rgba(42,75,84,.65);background:rgba(5,17,23,.52);border-radius:10px;color:#789591;font-size:8px}
.v314-level-legend strong{color:#a9c4bf}.v314-level-key{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}.v314-level-dot{width:7px;height:2px;border-radius:4px;display:inline-block}.v314-level-dot.entry{background:#69a9ff}.v314-level-dot.tp{background:#42e28c}.v314-level-dot.sl{background:#ff627d}
#levelToggle.on{color:var(--teal);border-color:rgba(44,230,191,.42);background:rgba(44,230,191,.07)}
@media(max-width:680px){.v314-level-legend{overflow:auto;flex-wrap:nowrap}.v314-level-legend span{flex:0 0 auto}}
'''


LEVEL_SCRIPT = r'''
<script nonce="__NONCE__" id="v314-level-script">
(() => {
  'use strict';
  if (window.__v314Levels) return;
  window.__v314Levels = true;
  const $ = id => document.getElementById(id);
  const base = $('chart'), overlay = $('levelOverlay'), toggle = $('levelToggle'), legend = $('levelLegend');
  if (!base || !overlay || !toggle || !legend) return;
  const price = value => {
    const n = Number(value); if (!Number.isFinite(n)) return '—';
    if (Math.abs(n) >= 1000) return n.toLocaleString('tr-TR',{maximumFractionDigits:2});
    if (Math.abs(n) >= 1) return n.toLocaleString('tr-TR',{maximumFractionDigits:5});
    return n.toLocaleString('tr-TR',{maximumFractionDigits:9});
  };
  const normalize = value => String(value || '').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const currentSymbol = () => normalize(new URLSearchParams(location.search).get('symbol') || $('symbolInput')?.value || 'BTCUSDT');
  const state = {trade:null, symbol:currentSymbol(), enabled:localStorage.getItem('kripto_v314_levels') !== '0', request:0};
  const colors = {entry:'#69a9ff', tp1:'#42e28c', tp2:'#42e28c', tp3:'#42e28c', sl:'#ff627d'};
  const labels = {entry:'Giriş', tp1:'TP1', tp2:'TP2', tp3:'TP3', sl:'SL'};
  function paintToggle(){toggle.classList.toggle('on',state.enabled);toggle.textContent=state.enabled?'Seviyeler açık':'Seviyeler kapalı'}
  function paintLegend(){
    if (!state.trade) { legend.innerHTML='<strong>Grafik seviyeleri:</strong><span>Bu coin için açık teknik senaryo yok.</span>'; return; }
    const t=state.trade;
    legend.innerHTML=`<strong>${String(t.system||'Sistem').replace(/[&<>"']/g,'')} · ${String(t.direction||'')}</strong><span class="v314-level-key"><i class="v314-level-dot entry"></i>Giriş ${price(t.entry)}</span><span class="v314-level-key"><i class="v314-level-dot tp"></i>TP1 ${price(t.tp1)}</span><span class="v314-level-key"><i class="v314-level-dot tp"></i>TP2 ${price(t.tp2)}</span><span class="v314-level-key"><i class="v314-level-dot tp"></i>TP3 ${price(t.tp3)}</span><span class="v314-level-key"><i class="v314-level-dot sl"></i>SL ${price(t.sl)}</span>`;
  }
  function draw(){
    const meta=base.__chart, rect=base.getBoundingClientRect(), dpr=Math.min(devicePixelRatio||1,2);
    overlay.width=Math.max(1,Math.round(rect.width*dpr)); overlay.height=Math.max(1,Math.round(rect.height*dpr)); overlay.style.width=`${rect.width}px`; overlay.style.height=`${rect.height}px`;
    const ctx=overlay.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,rect.width,rect.height);
    if (!state.enabled || !state.trade || !meta) return;
    const top=meta.m.top, bottom=meta.h-meta.m.bottom, left=meta.m.left, right=meta.w-meta.m.right;
    for (const key of ['entry','tp1','tp2','tp3','sl']) {
      const value=Number(state.trade[key]); if (!Number.isFinite(value)) continue;
      let yy=meta.y(value), suffix='';
      if (yy < top) { yy=top+3; suffix=' ↑'; }
      else if (yy > bottom) { yy=bottom-3; suffix=' ↓'; }
      ctx.save(); ctx.strokeStyle=colors[key]; ctx.globalAlpha=key==='entry'?0.95:0.78; ctx.lineWidth=key==='entry'?1.35:1;
      if (key!=='entry') ctx.setLineDash(key==='sl'?[4,4]:[6,4]);
      ctx.beginPath(); ctx.moveTo(left,yy); ctx.lineTo(right,yy); ctx.stroke(); ctx.setLineDash([]);
      const text=`${labels[key]} ${price(value)}${suffix}`; ctx.font='bold 9px system-ui'; const tw=ctx.measureText(text).width; const tx=Math.max(left+5,right-tw-6);
      ctx.fillStyle='rgba(4,15,20,.86)'; ctx.globalAlpha=1; ctx.fillRect(tx-4,yy-12,tw+8,14); ctx.fillStyle=colors[key]; ctx.fillText(text,tx,yy-2); ctx.restore();
    }
  }
  function schedule(){for (const ms of [0,120,380,850]) setTimeout(draw,ms)}
  async function loadLevels(){
    const symbol=currentSymbol(); state.symbol=symbol; const request=++state.request;
    try {
      const r=await fetch(`/api/coin-center/summary?symbol=${encodeURIComponent(symbol)}`,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
      if (!r.ok) throw new Error(`HTTP ${r.status}`); const p=await r.json(); if (request!==state.request) return;
      state.trade=Array.isArray(p.open_trades)&&p.open_trades.length?p.open_trades[0]:null; paintLegend(); schedule();
    } catch { if (request===state.request){state.trade=null;paintLegend();schedule();} }
  }
  toggle.addEventListener('click',()=>{state.enabled=!state.enabled;localStorage.setItem('kripto_v314_levels',state.enabled?'1':'0');paintToggle();schedule()});
  $('bars')?.addEventListener('click',()=>schedule());
  $('loadBtn')?.addEventListener('click',()=>setTimeout(loadLevels,40));
  $('symbolInput')?.addEventListener('keydown',event=>{if(event.key==='Enter')setTimeout(loadLevels,40)});
  window.addEventListener('resize',schedule);
  const originalReplace=history.replaceState.bind(history);
  history.replaceState=function(...args){const before=location.href;const result=originalReplace(...args);if(location.href!==before)setTimeout(loadLevels,0);return result};
  paintToggle(); paintLegend(); loadLevels(); schedule();
})();
</script>
'''


def enhance_dashboard_smart_links(body: str, nonce: str) -> str:
    if 'id="v314-smart-link-script"' in body:
        return body
    if "</head>" not in body or "</body>" not in body:
        raise RuntimeError("V3.14 dashboard HTML ankrajları bulunamadı.")
    script = SMART_LINK_SCRIPT.replace("__NONCE__", html.escape(str(nonce), quote=True))
    body = body.replace("</head>", SMART_LINK_CSS + "\n</head>", 1)
    return body.replace("</body>", script + "\n</body>", 1)


def enhance_coin_page(body: str, nonce: str) -> str:
    if 'id="levelOverlay"' in body:
        return body
    canvas_anchor = '<canvas id="chart"></canvas><div class="chart-tip" id="chartTip"></div>'
    bar_anchor = '<div class="bars" id="bars">'
    foot_anchor = '<div class="chart-foot"><span id="chartInfo">'
    if canvas_anchor not in body or bar_anchor not in body or foot_anchor not in body or "</style>" not in body or "</body>" not in body:
        raise RuntimeError("V3.14 Coin Merkezi HTML ankrajları bulunamadı.")
    body = body.replace("</style>", LEVEL_CSS + "\n</style>", 1)
    body = body.replace(canvas_anchor, '<canvas id="chart"></canvas><canvas class="v314-level-overlay" id="levelOverlay"></canvas><div class="chart-tip" id="chartTip"></div>', 1)
    body = body.replace(bar_anchor, bar_anchor + '<button class="bar-btn on" id="levelToggle" type="button">Seviyeler açık</button>', 1)
    chart_foot_start = '<div class="chart-foot"><span id="chartInfo">'
    legend = '<div class="v314-level-legend" id="levelLegend"><strong>Grafik seviyeleri:</strong><span>Yükleniyor…</span></div>'
    # Legend, chart-foot div kapanışından hemen sonra; benzersiz salt-okunur metin ankrajını kullan.
    foot_full = '<span>Salt okunur · emir açmaz</span></div>'
    if foot_full not in body:
        raise RuntimeError("V3.14 grafik alt bilgi ankrajı bulunamadı.")
    body = body.replace(foot_full, foot_full + legend, 1)
    script = LEVEL_SCRIPT.replace("__NONCE__", html.escape(str(nonce), quote=True))
    return body.replace("</body>", script + "\n</body>", 1)


def coin_center_page_v314(nonce: str, initial_symbol: str) -> str:
    return enhance_coin_page(coin.coin_center_page(nonce, initial_symbol), nonce)


def make_v314_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = coin.make_v313_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V314Handler(BaseHandler):
        server_version = "KriptoPanel/3.14"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html") and urllib.parse.urlsplit(self.path).path == "/":
                session = self._session()
                if session and self._is_premium(session) and nonce:
                    body = enhance_dashboard_smart_links(body, str(nonce))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status":"ok","version":VERSION,"coin_center":True,"smart_coin_links":True,"chart_levels":True,"level_source":"LATEST_OPEN_SCENARIO","premium_only":True,"new_api_schedule":False,"signal_engine":"unchanged","telegram":"unchanged"})
                return
            if path == "/coin-center":
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
                self._send(HTTPStatus.OK, coin_center_page_v314(nonce, symbol), "text/html; charset=utf-8", nonce=nonce)
                return
            return super().do_GET()

    return V314Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.14 Akıllı Coin Geçişi ve Grafik Seviyeleri")
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
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    server = ThreadingHTTPServer((args.host, args.port), make_v314_handler(config, service, sessions, limiter, store, market_client, overview_client))
    print(f"{VERSION} http://{args.host}:{args.port} smart_coin_links=on chart_levels=on level_source=latest_open signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
