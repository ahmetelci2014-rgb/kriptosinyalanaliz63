"""Kripto Kontrol Merkezi V3.16 - Canlı İşlem İlerleme Merkezi.

V3.15 canlı grafik motorunu korur. Coin Merkezi'ndeki en güncel açık teknik senaryoyu
canlı piyasa fiyatına göre salt-okunur biçimde izler: girişe göre hareket, canlı R,
sonraki hedef/SL mesafesi ve SL->hedef koridorundaki konum.

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
import dashboard_livechart_app as livechart
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_16_TRADE_PROGRESS_2026_08_16"

PROGRESS_CSS = r'''
#v316TradeProgress{display:none;margin-top:13px;border:1px solid rgba(105,169,255,.22);background:linear-gradient(135deg,rgba(9,27,37,.98),rgba(7,20,27,.97));border-radius:16px;overflow:hidden;box-shadow:0 12px 38px rgba(0,0,0,.12)}
body.v316-has-trade #v316TradeProgress{display:block}.v316-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid rgba(42,73,84,.65)}.v316-title{display:flex;align-items:center;gap:9px}.v316-title strong{font-size:13px}.v316-title small{display:block;color:#708b87;font-size:8px}.v316-dir{border-radius:999px;padding:4px 8px;font-size:8px;font-weight:950;border:1px solid rgba(105,169,255,.25)}.v316-dir.long{color:#42e28c;border-color:rgba(66,226,140,.28)}.v316-dir.short{color:#ff627d;border-color:rgba(255,98,125,.28)}.v316-body{padding:12px 14px}.v316-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px}.v316-metric{background:rgba(4,17,23,.58);border:1px solid rgba(35,65,76,.7);border-radius:10px;padding:9px}.v316-metric small{display:block;color:#617f7b;font-size:7px;text-transform:uppercase;letter-spacing:.05em;font-weight:850}.v316-metric b{display:block;font-size:13px;margin-top:3px}.v316-metric em{display:block;color:#708c88;font-size:7px;font-style:normal;margin-top:2px}.v316-pos{color:#42e28c}.v316-neg{color:#ff627d}.v316-neutral{color:#e9f5f2}.v316-track-wrap{margin-top:11px}.v316-track-labels{display:flex;justify-content:space-between;gap:8px;color:#698581;font-size:8px;margin-bottom:5px}.v316-track{height:12px;position:relative;border-radius:999px;background:linear-gradient(90deg,rgba(255,98,125,.18),rgba(105,169,255,.11) 38%,rgba(66,226,140,.18));border:1px solid rgba(54,84,94,.7);overflow:visible}.v316-fill{position:absolute;inset:1px auto 1px 1px;border-radius:999px;background:linear-gradient(90deg,rgba(255,98,125,.65),rgba(44,230,191,.88));width:0;transition:width .28s ease}.v316-entry{position:absolute;top:-4px;width:2px;height:18px;background:#69a9ff;box-shadow:0 0 0 2px rgba(105,169,255,.12);transition:left .28s ease}.v316-now{position:absolute;top:-5px;width:8px;height:20px;margin-left:-4px;border-radius:5px;background:#eef8f6;border:2px solid #2ce6bf;box-shadow:0 0 0 3px rgba(44,230,191,.12);transition:left .28s ease}.v316-foot{display:flex;justify-content:space-between;gap:10px;margin-top:7px;color:#6e8b87;font-size:8px}.v316-stage{font-weight:900;color:#b7cfcb}.v316-empty{display:none}.v316-stale{color:#ffbd59!important}
@media(max-width:900px){.v316-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:620px){.v316-head{align-items:flex-start}.v316-grid{grid-template-columns:1fr 1fr}.v316-metric:last-child{grid-column:1/-1}.v316-body{padding:10px}.v316-foot{flex-direction:column;gap:3px}}
'''

PROGRESS_HTML = r'''
<section id="v316TradeProgress" aria-live="polite">
  <div class="v316-head">
    <div class="v316-title"><div><strong>Canlı Teknik Senaryo Takibi</strong><small id="v316System">Açık senaryo hazırlanıyor</small></div></div>
    <span id="v316Direction" class="v316-dir">AÇIK</span>
  </div>
  <div class="v316-body">
    <div class="v316-grid">
      <div class="v316-metric"><small>Girişe göre hareket</small><b id="v316Move">—</b><em>Gerçek hesap P/L değildir</em></div>
      <div class="v316-metric"><small>Canlı R</small><b id="v316R">—</b><em>Giriş-SL riskine göre</em></div>
      <div class="v316-metric"><small id="v316TargetTitle">Sonraki hedef</small><b id="v316TargetDistance">—</b><em id="v316TargetPrice">—</em></div>
      <div class="v316-metric"><small>SL mesafesi</small><b id="v316SlDistance">—</b><em id="v316SlPrice">—</em></div>
      <div class="v316-metric"><small>Senaryo durumu</small><b id="v316Stage">—</b><em id="v316PriceAge">Canlı fiyat bekleniyor</em></div>
    </div>
    <div class="v316-track-wrap">
      <div class="v316-track-labels"><span id="v316TrackLeft">SL</span><span>Giriş</span><span id="v316TrackRight">Hedef</span></div>
      <div class="v316-track"><div id="v316Fill" class="v316-fill"></div><div id="v316Entry" class="v316-entry"></div><div id="v316Now" class="v316-now"></div></div>
      <div class="v316-foot"><span id="v316Corridor">SL → hedef koridoru</span><span class="v316-stage" id="v316Note">Salt okunur · emir açmaz</span></div>
    </div>
  </div>
</section>
'''

PROGRESS_SCRIPT = r'''
<script nonce="__NONCE__" id="v316-trade-progress-script">
(() => {
  'use strict';
  if (window.__v316TradeProgress) return;
  window.__v316TradeProgress = true;
  const $ = id => document.getElementById(id);
  const root = $('v316TradeProgress');
  const priceNode = $('lastPrice');
  if (!root || !priceNode) return;

  const state = { trade:null, symbol:'', lastPrice:null, lastPriceAt:0, summaryAt:0 };
  const num = v => { const n=Number(v); return Number.isFinite(n)?n:null; };
  const clamp = (v,a=0,b=1) => Math.max(a,Math.min(b,v));
  const normalize = v => String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const symbol = () => normalize(new URLSearchParams(location.search).get('symbol') || $('symbolInput')?.value || 'BTCUSDT');
  const fmt = v => { const n=num(v); if(n===null)return '—'; if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{maximumFractionDigits:6}); return n.toLocaleString('tr-TR',{maximumFractionDigits:9}); };
  const fmtPct = v => { const n=num(v); return n===null?'—':`${n>=0?'+':''}${n.toFixed(2)}%`; };
  const parseUiPrice = raw => {
    let s=String(raw||'').trim().replace(/\s/g,''); if(!s || s==='—')return null;
    s=s.replace(/[^0-9,.-]/g,'');
    if(s.includes(',')) s=s.replace(/\./g,'').replace(',','.'); else if((s.match(/\./g)||[]).length>=1) s=s.replace(/\./g,'');
    return num(s);
  };
  async function json(url){
    const r=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
    if(r.status===401){location.assign('/login');throw new Error('Oturum gerekli')}
    if(r.status===403){location.assign('/premium');throw new Error('Premium gerekli')}
    const p=await r.json(); if(!r.ok)throw new Error(p.message||p.error||`HTTP ${r.status}`); return p;
  }
  function directionSign(t){ return String(t?.direction||'').toUpperCase()==='SHORT' ? -1 : 1; }
  function reachedTarget(price, level, sign){ return level!==null && sign*(price-level)>=0; }
  function reachedSl(price, sl, sign){ return sl!==null && sign*(price-sl)<=0; }
  function targetState(t,p){
    const sign=directionSign(t), tp1=num(t.tp1),tp2=num(t.tp2),tp3=num(t.tp3);
    if(tp3!==null && reachedTarget(p,tp3,sign))return {label:'TP3 bölgesi',key:'TP3',value:tp3,reached:true};
    if(tp2!==null && reachedTarget(p,tp2,sign))return {label:'TP2 geçti',key:'TP3',value:tp3??tp2,reached:false};
    if(tp1!==null && reachedTarget(p,tp1,sign))return {label:'TP1 geçti',key:'TP2',value:tp2??tp3??tp1,reached:false};
    const next=tp1??tp2??tp3; return {label:'TP1 yolunda',key:tp1!==null?'TP1':tp2!==null?'TP2':'TP3',value:next,reached:false};
  }
  function colorize(node,value){
    if(!node)return; node.classList.remove('v316-pos','v316-neg','v316-neutral');
    node.classList.add(value>0?'v316-pos':value<0?'v316-neg':'v316-neutral');
  }
  function corridor(t,p){
    const sign=directionSign(t), entry=num(t.entry),sl=num(t.sl),target=num(t.tp3??t.tp2??t.tp1);
    if(entry===null||sl===null||target===null)return null;
    const denom=sign===1?(target-sl):(sl-target); if(!(denom>0))return null;
    const pos=sign===1?(p-sl)/denom:(sl-p)/denom;
    const entryPos=sign===1?(entry-sl)/denom:(sl-entry)/denom;
    return {pos:clamp(pos),entryPos:clamp(entryPos),target};
  }
  function render(){
    const t=state.trade,p=num(state.lastPrice);
    if(!t || p===null){document.body.classList.remove('v316-has-trade');return;}
    document.body.classList.add('v316-has-trade');
    const sign=directionSign(t),entry=num(t.entry),sl=num(t.sl),risk=(entry!==null&&sl!==null)?Math.abs(entry-sl):null;
    const move=entry!==null&&entry!==0?sign*(p-entry)/Math.abs(entry)*100:null;
    const liveR=risk&&risk>0&&entry!==null?sign*(p-entry)/risk:null;
    const target=targetState(t,p),targetValue=num(target.value);
    const targetDistance=targetValue!==null?Math.max(0,sign*(targetValue-p)/Math.max(Math.abs(p),1e-12)*100):null;
    const slDistance=sl!==null?Math.max(0,sign*(p-sl)/Math.max(Math.abs(p),1e-12)*100):null;
    const slHit=reachedSl(p,sl,sign);
    let stage=target.label;
    if(slHit)stage='SL bölgesi'; else if(target.reached)stage='TP3 bölgesi'; else if(move!==null&&move<0)stage='Risk tarafında'; else if(String(target.label).includes('geçti'))stage=target.label; else stage='Hedef yolunda';

    $('v316System').textContent=`${t.system||'Sistem'} · ${fmt(entry)} giriş · canlı ${fmt(p)}`;
    const dir=String(t.direction||'AÇIK').toUpperCase(); $('v316Direction').textContent=dir; $('v316Direction').className=`v316-dir ${dir.toLowerCase()}`;
    $('v316Move').textContent=fmtPct(move); colorize($('v316Move'),move??0);
    $('v316R').textContent=liveR===null?'—':`${liveR>=0?'+':''}${liveR.toFixed(2)}R`; colorize($('v316R'),liveR??0);
    $('v316TargetTitle').textContent=`${target.key} mesafesi`; $('v316TargetDistance').textContent=target.reached?'Hedefte':targetDistance===null?'—':`${targetDistance.toFixed(2)}%`; $('v316TargetPrice').textContent=targetValue===null?'Hedef seviyesi yok':`${target.key} ${fmt(targetValue)}`;
    $('v316SlDistance').textContent=slHit?'SL bölgesi':slDistance===null?'—':`${slDistance.toFixed(2)}%`; $('v316SlPrice').textContent=sl===null?'SL seviyesi yok':`SL ${fmt(sl)}`;
    $('v316Stage').textContent=stage; colorize($('v316Stage'),slHit?-1:(move??0));
    const age=state.lastPriceAt?Math.max(0,Math.round((Date.now()-state.lastPriceAt)/1000)):null; $('v316PriceAge').textContent=age===null?'Canlı fiyat bekleniyor':age<=4?'Fiyat canlı':`Fiyat ${age} sn önce`;
    $('v316PriceAge').classList.toggle('v316-stale',age!==null&&age>8);

    const c=corridor(t,p); if(c){$('v316Fill').style.width=`${(c.pos*100).toFixed(2)}%`;$('v316Now').style.left=`${(c.pos*100).toFixed(2)}%`;$('v316Entry').style.left=`${(c.entryPos*100).toFixed(2)}%`;$('v316TrackLeft').textContent=`SL ${fmt(sl)}`;$('v316TrackRight').textContent=`Hedef ${fmt(c.target)}`;$('v316Corridor').textContent=`Konum ${(c.pos*100).toFixed(0)}% · giriş işareti ${(c.entryPos*100).toFixed(0)}%`;}
    else {$('v316Fill').style.width='0%';$('v316Now').style.left='0%';$('v316Entry').style.left='0%';$('v316Corridor').textContent='Koridor için seviye verisi yetersiz';}
  }
  function readLivePrice(){
    const p=parseUiPrice(priceNode.textContent); if(p!==null && p!==state.lastPrice){state.lastPrice=p;state.lastPriceAt=Date.now();render();}
  }
  async function syncTrade(){
    const s=symbol(); state.symbol=s;
    try{const p=await json(`/api/coin-center/summary?symbol=${encodeURIComponent(s)}&v316=${Date.now()}`);if(s!==symbol())return;const rows=Array.isArray(p.open_trades)?p.open_trades:[];state.trade=rows.length?rows[0]:null;state.summaryAt=Date.now();render();}
    catch{state.trade=null;render();}
  }
  function reset(){state.trade=null;state.lastPrice=null;state.lastPriceAt=0;document.body.classList.remove('v316-has-trade');setTimeout(()=>{readLivePrice();syncTrade()},220);}

  new MutationObserver(readLivePrice).observe(priceNode,{childList:true,subtree:true,characterData:true});
  $('bars')?.addEventListener('click',()=>setTimeout(render,100)); $('loadBtn')?.addEventListener('click',()=>setTimeout(reset,120)); $('symbolInput')?.addEventListener('keydown',e=>{if(e.key==='Enter')setTimeout(reset,120)});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden){readLivePrice();syncTrade();}});
  readLivePrice(); syncTrade(); setInterval(readLivePrice,500); setInterval(syncTrade,18000); setInterval(render,1000);
})();
</script>
'''


def enhance_trade_progress_page(body: str, nonce: str) -> str:
    if 'id="v316-trade-progress-script"' in body:
        return body
    if '<div class="layout">' not in body or 'id="lastPrice"' not in body or '</style>' not in body or '</body>' not in body:
        raise RuntimeError("V3.16 işlem ilerleme ankrajları bulunamadı.")
    body = body.replace('</style>', PROGRESS_CSS + '\n</style>', 1)
    body = body.replace('<div class="layout">', PROGRESS_HTML + '\n<div class="layout">', 1)
    script = PROGRESS_SCRIPT.replace('__NONCE__', html.escape(str(nonce), quote=True))
    return body.replace('</body>', script + '\n</body>', 1)


def make_v316_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = livechart.make_v315_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V316Handler(BaseHandler):
        server_version = "KriptoPanel/3.16"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith('text/html') and urllib.parse.urlsplit(self.path).path == '/coin-center' and nonce:
                body = enhance_trade_progress_page(body, str(nonce))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            if urllib.parse.urlsplit(self.path).path == '/healthz':
                self._json(HTTPStatus.OK, {
                    'status':'ok','version':VERSION,'coin_center':True,'live_chart':True,
                    'trade_progress':True,'direction_aware':True,'live_r':True,
                    'target_distance':True,'sl_distance':True,'progress_corridor':True,
                    'signal_engine':'unchanged','telegram':'unchanged',
                })
                return
            return super().do_GET()

    return V316Handler


def main() -> None:
    parser = argparse.ArgumentParser(description='Kripto Kontrol Merkezi V3.16 Canlı İşlem İlerleme Merkezi')
    parser.add_argument('--host', default=os.getenv('HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '8080')))
    parser.add_argument('--root', default='.')
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root)); config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter(); store = lifecycle.lifecycle_store_from_env(config)
    market_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=2)
    server = ThreadingHTTPServer((args.host, args.port), make_v316_handler(config, service, sessions, limiter, store, market_client, overview_client))
    print(f"{VERSION} http://{args.host}:{args.port} trade_progress=on live_r=on signal_engine=unchanged")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__':
    main()
