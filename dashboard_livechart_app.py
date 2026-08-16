"""Kripto Kontrol Merkezi V3.15 - Canlı Grafik Motoru.

V3.14.2 Coin Merkezi güvenlik ve fallback katmanını korur; kullanıcıya tek, bağımsız ve
otomatik yenilenen canlı grafik yüzeyi sunar. Grafik yalnız public piyasa verisi ve paneldeki
mevcut salt-okunur teknik senaryoları gösterir.

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
import dashboard_chartcoord_app as chartcoord
import dashboard_chartfix_app as chartfix
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_15_LIVE_CHART_2026_08_16"

LIVE_CSS = r'''
#v315LiveChart{display:none;position:absolute;inset:0;width:100%;height:100%;z-index:8;background:#07151c;border-radius:8px;touch-action:none;cursor:crosshair}
#v315ChartHud{display:none;position:absolute;left:12px;top:9px;z-index:10;pointer-events:none;color:#9bb5b1;font-size:9px;line-height:1.45;background:rgba(4,15,20,.68);border:1px solid rgba(66,93,99,.34);border-radius:7px;padding:5px 7px;backdrop-filter:blur(8px)}
#v315LiveBadge{display:none;align-items:center;gap:5px;color:#71bbae;font-size:8px;font-weight:850;white-space:nowrap}
#v315LiveBadge:before{content:"";width:6px;height:6px;border-radius:50%;background:#42e28c;box-shadow:0 0 0 3px rgba(66,226,140,.10)}
body.v315-live-ready #v315LiveChart,body.v315-live-ready #v315ChartHud{display:block}
body.v315-live-ready #v315LiveBadge{display:inline-flex}
body.v315-live-ready #chart,body.v315-live-ready #levelOverlay,body.v315-live-ready #chartRecovery{visibility:hidden!important;pointer-events:none!important}
body.v315-live-ready #chartRecoveryNote{display:none!important}
@media(max-width:680px){#v315ChartHud{left:8px;top:7px;font-size:8px;padding:4px 6px}}
'''

LIVE_SCRIPT = r'''
<script nonce="__NONCE__" id="v315-live-chart-script">
(() => {
  'use strict';
  if (window.__v315LiveChart) return;
  window.__v315LiveChart = true;
  const $ = id => document.getElementById(id);
  const host = $('chart')?.parentElement;
  const canvas = $('v315LiveChart');
  const hud = $('v315ChartHud');
  const info = $('chartInfo');
  const badge = $('v315LiveBadge');
  if (!host || !canvas || !info) return;

  const TICK_MS = 2500;
  const CANDLE_SYNC_MS = 12000;
  const DETAIL_SYNC_MS = 18000;
  const state = {
    symbol:'', bar:'15m', candles:[], summary:null, lastPrice:null, source:'',
    visible:70, offset:0, hover:-1, dragging:false, dragX:0, dragOffset:0,
    lastTickAt:0, token:0, ready:false
  };
  const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};
  const fmt=v=>{const n=num(v);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{maximumFractionDigits:2});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{maximumFractionDigits:5});return n.toLocaleString('tr-TR',{maximumFractionDigits:9})};
  const pct=v=>{const n=num(v);return n===null?'—':`${n>=0?'+':''}${n.toFixed(2)}%`};
  const compact=v=>{const n=num(v);return n===null?'—':Intl.NumberFormat('tr-TR',{notation:'compact',maximumFractionDigits:1}).format(n)};
  const currentSymbol=()=>normalize(new URLSearchParams(location.search).get('symbol')||$('symbolInput')?.value||'BTCUSDT');
  const currentBar=()=>document.querySelector('[data-bar].active')?.dataset?.bar||'15m';
  async function json(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');throw new Error('Oturum gerekli')}if(r.status===403){location.assign('/premium');throw new Error('Premium gerekli')}const p=await r.json();if(!r.ok)throw new Error(p.message||p.error||`HTTP ${r.status}`);return p}

  function updateLiveBadge(){
    if(!badge)return;
    if(!state.lastTickAt){badge.textContent='Canlı bağlantı hazırlanıyor';return}
    const age=Math.max(0,Math.round((Date.now()-state.lastTickAt)/1000));
    badge.textContent=age<=3?'Canlı · şimdi':`Canlı · ${age} sn önce`;
  }

  function latestTrade(){
    const rows=Array.isArray(state.summary?.open_trades)?state.summary.open_trades:[];
    return rows.length?rows[0]:null;
  }

  function levels(){
    const t=latestTrade();if(!t)return [];
    return [
      ['entry','Giriş','#69a9ff'],['tp1','TP1','#42e28c'],['tp2','TP2','#42e28c'],
      ['tp3','TP3','#42e28c'],['sl','SL','#ff627d']
    ].map(([key,label,color])=>({key,label,color,value:num(t[key])})).filter(x=>x.value!==null);
  }

  function updateCurrentCandle(priceValue){
    const p=num(priceValue);if(p===null||!state.candles.length)return;
    const last=state.candles[state.candles.length-1];
    last.close=p;
    last.high=Math.max(num(last.high)??p,p);
    last.low=Math.min(num(last.low)??p,p);
    state.lastPrice=p;
  }

  function visibleCandles(){
    const total=state.candles.length;
    const count=Math.max(20,Math.min(state.visible,total||state.visible));
    const maxOffset=Math.max(0,total-count);
    state.offset=Math.max(0,Math.min(state.offset,maxOffset));
    const end=Math.max(0,total-state.offset);
    const start=Math.max(0,end-count);
    return {rows:state.candles.slice(start,end),start,end,total};
  }

  function draw(){
    if(!state.candles.length)return;
    const rect=host.getBoundingClientRect();
    const w=Math.max(340,Math.round(rect.width||900)),h=Math.max(280,Math.round(rect.height||430));
    const dpr=Math.min(window.devicePixelRatio||1,2);
    canvas.width=Math.round(w*dpr);canvas.height=Math.round(h*dpr);canvas.style.width=`${w}px`;canvas.style.height=`${h}px`;
    const ctx=canvas.getContext('2d');if(!ctx)return;
    ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);ctx.fillStyle='#07151c';ctx.fillRect(0,0,w,h);
    const view=visibleCandles(),rows=view.rows;if(!rows.length)return;
    const m={l:14,r:88,t:25,b:30},cw=w-m.l-m.r,ch=h-m.t-m.b;
    const lows=rows.map(c=>num(c.low)).filter(v=>v!==null),highs=rows.map(c=>num(c.high)).filter(v=>v!==null);
    let lo=Math.min(...lows),hi=Math.max(...highs);
    const lastClose=num(rows[rows.length-1]?.close)??state.lastPrice;
    for(const lv of levels()){
      if(lastClose!==null && Math.abs(lv.value-lastClose)/Math.max(Math.abs(lastClose),1e-12)<=.18){lo=Math.min(lo,lv.value);hi=Math.max(hi,lv.value)}
    }
    let pad=Math.max((hi-lo)*.075,Math.abs(hi)*.0012,1e-10);if(hi===lo)pad=Math.max(Math.abs(hi)*.01,1e-8);lo-=pad;hi+=pad;
    const y=v=>m.t+(hi-Number(v))/(hi-lo)*ch;
    const step=cw/rows.length,body=Math.max(2,Math.min(10,step*.66));

    ctx.font='10px system-ui';ctx.lineWidth=1;
    for(let i=0;i<=5;i++){
      const yy=m.t+ch*i/5,val=hi-(hi-lo)*i/5;
      ctx.strokeStyle='rgba(126,157,153,.13)';ctx.beginPath();ctx.moveTo(m.l,yy);ctx.lineTo(w-m.r,yy);ctx.stroke();
      ctx.fillStyle='#688580';ctx.fillText(fmt(val),w-m.r+7,yy+4);
    }
    const labels=6;
    for(let i=0;i<labels;i++){
      const idx=Math.min(rows.length-1,Math.round((rows.length-1)*i/(labels-1))),x=m.l+step*(idx+.5),ts=num(rows[idx]?.ts);
      if(ts){const d=new Date(ts*1000);const label=state.bar==='1D'?d.toLocaleDateString('tr-TR',{day:'2-digit',month:'2-digit'}):d.toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'});ctx.fillStyle='#5d7975';ctx.fillText(label,Math.max(m.l,x-18),h-8)}
    }

    rows.forEach((c,i)=>{
      const x=m.l+step*(i+.5),o=y(c.open),cl=y(c.close),hh=y(c.high),ll=y(c.low),up=Number(c.close)>=Number(c.open),color=up?'#42e28c':'#ff627d';
      ctx.strokeStyle=color;ctx.fillStyle=color;ctx.beginPath();ctx.moveTo(x,hh);ctx.lineTo(x,ll);ctx.stroke();ctx.fillRect(x-body/2,Math.min(o,cl),body,Math.max(1.2,Math.abs(cl-o)));
    });

    for(const lv of levels()){
      let yy=y(lv.value),edge='';if(yy<m.t){yy=m.t+2;edge=' ↑'}else if(yy>h-m.b){yy=h-m.b-2;edge=' ↓'}
      ctx.save();ctx.setLineDash([6,4]);ctx.strokeStyle=lv.color;ctx.globalAlpha=.9;ctx.beginPath();ctx.moveTo(m.l,yy);ctx.lineTo(w-m.r,yy);ctx.stroke();ctx.restore();
      ctx.font='700 9px system-ui';const label=`${lv.label} ${fmt(lv.value)}${edge}`,tw=Math.min(152,ctx.measureText(label).width+12),bx=w-m.r-tw-3;
      ctx.fillStyle='rgba(4,15,20,.9)';ctx.fillRect(bx,yy-13,tw,16);ctx.fillStyle=lv.color;ctx.fillText(label,bx+6,yy-2);
    }

    const live=num(state.lastPrice??rows[rows.length-1]?.close);
    if(live!==null){
      const yy=y(live);if(yy>=m.t&&yy<=h-m.b){ctx.save();ctx.setLineDash([3,4]);ctx.strokeStyle='#2ce6bf';ctx.globalAlpha=.75;ctx.beginPath();ctx.moveTo(m.l,yy);ctx.lineTo(w-m.r,yy);ctx.stroke();ctx.restore();const text=fmt(live);ctx.font='800 10px system-ui';const tw=Math.max(62,ctx.measureText(text).width+14);ctx.fillStyle='#16b99a';ctx.fillRect(w-m.r+2,yy-10,tw,20);ctx.fillStyle='#03120e';ctx.fillText(text,w-m.r+8,yy+4)}
    }

    if(state.hover>=0&&state.hover<rows.length){
      const c=rows[state.hover],x=m.l+step*(state.hover+.5),cy=y(c.close);
      ctx.save();ctx.setLineDash([3,4]);ctx.strokeStyle='rgba(190,217,212,.35)';ctx.beginPath();ctx.moveTo(x,m.t);ctx.lineTo(x,h-m.b);ctx.moveTo(m.l,cy);ctx.lineTo(w-m.r,cy);ctx.stroke();ctx.restore();
      if(hud){const ts=num(c.ts);const when=ts?new Date(ts*1000).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'';hud.innerHTML=`<b>${when}</b><br>A ${fmt(c.open)} &nbsp; Y ${fmt(c.high)} &nbsp; D ${fmt(c.low)} &nbsp; K ${fmt(c.close)}`}
    }else if(hud){const c=rows[rows.length-1];hud.innerHTML=`<b>${state.symbol} · ${state.bar}</b><br>A ${fmt(c.open)} &nbsp; Y ${fmt(c.high)} &nbsp; D ${fmt(c.low)} &nbsp; K ${fmt(c.close)}`}

    canvas.__v315={m,w,h,step,rows,y};
    if(!state.ready){state.ready=true;document.body.classList.add('v315-live-ready')}
  }

  async function syncCandles(force=false){
    if(document.hidden&&!force)return;
    const symbol=currentSymbol(),bar=currentBar(),mine=++state.token;
    try{
      const p=await json(`/api/market/candles?symbol=${encodeURIComponent(symbol)}&bar=${encodeURIComponent(bar)}&v315=${Date.now()}`);
      if(mine!==state.token||symbol!==currentSymbol()||bar!==currentBar())return;
      const rows=Array.isArray(p.candles)?p.candles:[];if(!rows.length)throw new Error('Mum verisi boş');
      state.symbol=symbol;state.bar=bar;state.candles=rows.map(c=>({...c}));state.source=p.source||'OKX_PUBLIC';
      if(state.lastPrice!==null)updateCurrentCandle(state.lastPrice);
      draw();info.textContent=`Canlı grafik · ${rows.length} mum · ${state.source} · ${bar}`;
    }catch(err){if(!state.ready)info.textContent=`Canlı grafik hazırlanamadı: ${err.message}`}
  }

  function paintOverview(p){
    const item=(p.items||[])[0]||{},last=num(item.last);if(last!==null){updateCurrentCandle(last);$('lastPrice')&&($('lastPrice').textContent=fmt(last))}
    const change=num(item.change_24h_pct);if($('change24')){$('change24').textContent=pct(change);$('change24').className=`change ${change!==null&&change>=0?'up':'down'}`}
    if($('high24'))$('high24').textContent=fmt(item.high_24h);if($('low24'))$('low24').textContent=fmt(item.low_24h);if($('vol24'))$('vol24').textContent=compact(item.volume_24h);
    state.lastTickAt=Date.now();updateLiveBadge();draw();
  }

  async function tick(){
    if(document.hidden)return;
    const symbol=currentSymbol();
    try{const p=await json(`/api/market/overview?symbols=${encodeURIComponent(symbol)}&v315=${Date.now()}`);if(symbol===currentSymbol())paintOverview(p)}catch{}
  }

  function renderScore(p){
    const score=num(p.score);if($('scoreValue'))$('scoreValue').textContent=score===null?'—':Math.round(score);if($('scoreRing'))$('scoreRing').style.setProperty('--score',`${Math.max(0,Math.min(100,score||0))}%`);if($('scoreBand'))$('scoreBand').textContent=p.band||'Skor yok';if($('scoreDirection'))$('scoreDirection').textContent=`${p.direction||'KARIŞIK'} · teknik uyum göstergesi`;const m=p.metrics||{};if($('trend15'))$('trend15').textContent=m.trend_15m||'—';if($('trend1h'))$('trend1h').textContent=m.trend_1h||'—';if($('rsi15'))$('rsi15').textContent=m.rsi_15m??'—';if($('rsi1h'))$('rsi1h').textContent=m.rsi_1h??'—';if($('volumeRatio'))$('volumeRatio').textContent=m.volume_ratio_15m==null?'—':`${m.volume_ratio_15m}x`;if($('momentum'))$('momentum').textContent=pct(m.change_24h_pct)
  }

  function esc(v){return String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]))}
  function renderSummary(p){
    state.summary=p;const perf=p.performance||{};
    if($('sampleCount'))$('sampleCount').textContent=perf.sample??0;if($('perfSample'))$('perfSample').textContent=perf.sample??0;if($('perfTp'))$('perfTp').textContent=perf.tp??0;if($('perfSl'))$('perfSl').textContent=perf.sl??0;if($('perfRate'))$('perfRate').textContent=perf.tp_rate_percent==null?'—':`%${perf.tp_rate_percent}`;if($('perfR'))$('perfR').textContent=perf.net_r==null?'—':`${perf.net_r>=0?'+':''}${Number(perf.net_r).toFixed(2)}R`;
    const opens=Array.isArray(p.open_trades)?p.open_trades:[];if($('coinContext'))$('coinContext').textContent=opens.length?`${opens.length} açık teknik senaryo takipte · ${perf.sample||0} kapanış kaydı`:`Açık teknik senaryo yok · ${perf.sample||0} kapanış kaydı`;
    if($('openTrades'))$('openTrades').innerHTML=opens.map(r=>`<div class="open-card"><div class="open-top"><div><b>${esc(r.system||'Sistem')}</b></div><span class="direction ${String(r.direction||'').toLowerCase()}">${esc(r.direction||'AÇIK')}</span></div><div class="levels"><div class="level"><small>Giriş</small><b>${fmt(r.entry)}</b></div><div class="level"><small>TP1</small><b>${fmt(r.tp1)}</b></div><div class="level"><small>SL</small><b>${fmt(r.sl)}</b></div><div class="level"><small>TP2</small><b>${fmt(r.tp2)}</b></div><div class="level"><small>TP3</small><b>${fmt(r.tp3)}</b></div><div class="level"><small>Skor</small><b>${r.score??r.signal_score??r.quality_score??'—'}</b></div></div></div>`).join('')||'<div class="empty">Bu coin için açık teknik senaryo yok.</div>';
    draw();
  }

  async function detailSync(){
    if(document.hidden)return;
    const symbol=currentSymbol();
    const tasks=[json(`/api/coin-center/summary?symbol=${encodeURIComponent(symbol)}&v315=${Date.now()}`).then(p=>symbol===currentSymbol()&&renderSummary(p)),json(`/api/market/analysis-score?symbol=${encodeURIComponent(symbol)}&v315=${Date.now()}`).then(p=>symbol===currentSymbol()&&renderScore(p))];
    await Promise.allSettled(tasks);
  }

  function resetForSelection(){
    state.symbol=currentSymbol();state.bar=currentBar();state.candles=[];state.summary=null;state.lastPrice=null;state.visible=70;state.offset=0;state.hover=-1;state.ready=false;document.body.classList.remove('v315-live-ready');state.token+=1;
    setTimeout(()=>{syncCandles(true);tick();detailSync()},180);
  }

  canvas.addEventListener('wheel',e=>{e.preventDefault();const delta=e.deltaY>0?8:-8;state.visible=Math.max(24,Math.min(120,state.visible+delta));state.offset=Math.min(state.offset,Math.max(0,state.candles.length-state.visible));draw()},{passive:false});
  canvas.addEventListener('pointerdown',e=>{state.dragging=true;state.dragX=e.clientX;state.dragOffset=state.offset;canvas.setPointerCapture?.(e.pointerId)});
  canvas.addEventListener('pointermove',e=>{const meta=canvas.__v315;if(!meta)return;if(state.dragging){const shift=Math.round((e.clientX-state.dragX)/Math.max(meta.step,1));state.offset=Math.max(0,Math.min(Math.max(0,state.candles.length-state.visible),state.dragOffset+shift));draw();return}const r=canvas.getBoundingClientRect(),x=e.clientX-r.left,idx=Math.floor((x-meta.m.l)/meta.step);state.hover=(idx>=0&&idx<meta.rows.length)?idx:-1;draw()});
  const endDrag=e=>{state.dragging=false;try{canvas.releasePointerCapture?.(e.pointerId)}catch{}};canvas.addEventListener('pointerup',endDrag);canvas.addEventListener('pointercancel',endDrag);canvas.addEventListener('pointerleave',()=>{if(!state.dragging){state.hover=-1;draw()}});
  canvas.addEventListener('dblclick',()=>{state.visible=70;state.offset=0;state.hover=-1;draw()});
  $('bars')?.addEventListener('click',()=>setTimeout(resetForSelection,80));$('loadBtn')?.addEventListener('click',()=>setTimeout(resetForSelection,100));$('symbolInput')?.addEventListener('keydown',e=>{if(e.key==='Enter')setTimeout(resetForSelection,100)});
  new ResizeObserver(()=>draw()).observe(host);window.addEventListener('resize',draw);document.addEventListener('visibilitychange',()=>{if(!document.hidden){tick();syncCandles(true);detailSync()}});

  state.symbol=currentSymbol();state.bar=currentBar();
  syncCandles(true);tick();detailSync();
  setInterval(tick,TICK_MS);setInterval(()=>syncCandles(false),CANDLE_SYNC_MS);setInterval(detailSync,DETAIL_SYNC_MS);setInterval(updateLiveBadge,1000);
})();
</script>
'''


def enhance_live_chart_page(body: str, nonce: str) -> str:
    if 'id="v315-live-chart-script"' in body:
        return body
    if '<div class="chart-wrap">' not in body or 'id="chartInfo"' not in body or "</style>" not in body or "</body>" not in body:
        raise RuntimeError("V3.15 canlı grafik ankrajları bulunamadı.")
    body = body.replace("</style>", LIVE_CSS + "\n</style>", 1)
    body = body.replace('<div class="chart-wrap">', '<div class="chart-wrap"><canvas id="v315LiveChart" aria-label="Canlı mum grafiği"></canvas><div id="v315ChartHud"></div>', 1)
    body = body.replace('<span id="chartInfo">', '<span id="v315LiveBadge">Canlı bağlantı hazırlanıyor</span><span id="chartInfo">', 1)
    script = LIVE_SCRIPT.replace("__NONCE__", html.escape(str(nonce), quote=True))
    return body.replace("</body>", script + "\n</body>", 1)


def make_v315_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = chartcoord.make_v3142_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V315Handler(BaseHandler):
        server_version = "KriptoPanel/3.15"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if (
                status == HTTPStatus.OK
                and isinstance(body, str)
                and content_type.startswith("text/html")
                and urllib.parse.urlsplit(self.path).path == "/coin-center"
                and nonce
            ):
                body = enhance_live_chart_page(body, str(nonce))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            if urllib.parse.urlsplit(self.path).path == "/healthz":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status":"ok",
                        "version":VERSION,
                        "coin_center":True,
                        "live_chart":True,
                        "live_tick_ms":2500,
                        "candle_sync_ms":12000,
                        "detail_sync_ms":18000,
                        "chart_zoom":True,
                        "chart_pan":True,
                        "chart_crosshair":True,
                        "chart_recovery":True,
                        "signal_engine":"unchanged",
                        "telegram":"unchanged",
                    },
                )
                return
            return super().do_GET()

    return V315Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.15 Canlı Grafik Motoru")
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
    market_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=2)
    server = ThreadingHTTPServer((args.host, args.port), make_v315_handler(config, service, sessions, limiter, store, market_client, overview_client))
    print(f"{VERSION} http://{args.host}:{args.port} live_chart=on tick_ms=2500 candle_sync_ms=12000 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
