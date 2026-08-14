"""Kripto Kontrol Merkezi V2.5 - Favori / İzleme Listesi Merkezi.

V2.4 sesli-renkli bildirim katmanını korur. Bu dosya yalnız panel tarafında:
- kullanıcının tarayıcıda tuttuğu favori coinleri tek ekranda toplar,
- OKX public veriden fiyat, 24s değişim ve 15m RSI/EMA/hacim özeti gösterir,
- mevcut panel verisinden favori coin için açık sinyal / son sonuç bağlamı gösterir.

Sinyal üretimi, Telegram, strateji ve emir akışı bu dosyada yoktur.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_alert_app as alert
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_5_WATCHLIST_2026_08_14"


def watchlist_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = alert.alert_dashboard_page(session, nonce)
    nonce_attr = html.escape(nonce, quote=True)

    extra_css = r'''
    /* V2.5: favori coinleri tek bakışta takip et. */
    .nav-item[data-view="watchlist"].active{background:rgba(255,189,89,.10);color:var(--amber);border-color:rgba(255,189,89,.22)}
    #page-watchlist .panel{border-color:rgba(255,189,89,.15)}#page-watchlist .panel-head{background:linear-gradient(90deg,rgba(255,189,89,.05),transparent 40%)}
    .watch-add{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.watch-add input{width:150px;border:1px solid var(--line);background:#08141c;color:var(--text);border-radius:9px;padding:8px 10px;outline:none;text-transform:uppercase;font-size:10px}.watch-add input:focus{border-color:var(--amber)}
    .watch-quick{display:flex;gap:5px;flex-wrap:wrap;margin:0 0 13px}.watch-quick button{border:1px solid rgba(255,189,89,.20);background:rgba(255,189,89,.045);color:#d7b56f;border-radius:999px;padding:5px 8px;font-size:9px;font-weight:850}.watch-quick button:hover{border-color:var(--amber);color:var(--amber)}
    .watch-summary{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-bottom:13px}.watch-metric{border:1px solid var(--line);background:#0a171f;border-radius:12px;padding:11px 12px}.watch-metric small{display:block;color:#6e8986;font-size:8px;text-transform:uppercase;letter-spacing:.06em}.watch-metric strong{display:block;margin-top:4px;font-size:19px}.watch-metric.amber strong{color:var(--amber)}.watch-metric.green strong{color:var(--green)}.watch-metric.blue strong{color:var(--blue)}
    .watch-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.watch-card{border:1px solid var(--line);background:#091720;border-radius:13px;padding:12px;min-width:0}.watch-card:hover{border-color:rgba(255,189,89,.30)}.watch-top{display:flex;align-items:center;gap:9px}.watch-mark{width:37px;height:37px;border-radius:11px;display:grid;place-items:center;border:1px solid rgba(255,189,89,.22);background:rgba(255,189,89,.055);color:var(--amber);font-size:9px;font-weight:950}.watch-name{flex:1;min-width:0}.watch-name strong{display:block;font-size:13px}.watch-name small{display:block;color:var(--muted);font-size:9px}.watch-remove{border:0;background:transparent;color:#6b8582;font-size:15px;padding:5px}.watch-remove:hover{color:var(--red)}
    .watch-price{display:flex;justify-content:space-between;align-items:flex-end;gap:8px;margin:11px 0 9px}.watch-price strong{font-size:20px;letter-spacing:-.02em}.watch-change{font-size:10px;font-weight:900}.watch-change.up{color:var(--green)}.watch-change.down{color:var(--red)}.watch-change.flat{color:var(--muted)}
    .watch-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}.watch-stat{background:#07131a;border-radius:8px;padding:7px}.watch-stat small{display:block;color:#607b78;font-size:8px}.watch-stat b{display:block;margin-top:2px;font-size:10px}.watch-trend.up{color:var(--green)}.watch-trend.down{color:var(--red)}.watch-trend.flat{color:var(--muted)}
    .watch-context{margin-top:9px;padding-top:9px;border-top:1px solid rgba(29,48,59,.75);display:flex;align-items:center;justify-content:space-between;gap:8px}.watch-context small{color:var(--muted);font-size:9px}.watch-state{border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:8px;font-weight:900;white-space:nowrap}.watch-state.long{color:var(--green);border-color:rgba(66,226,140,.26);background:rgba(66,226,140,.05)}.watch-state.short{color:var(--red);border-color:rgba(255,98,125,.26);background:rgba(255,98,125,.05)}.watch-state.result{color:var(--amber);border-color:rgba(255,189,89,.24);background:rgba(255,189,89,.04)}.watch-state.none{color:#6d8784}
    .watch-actions{display:flex;gap:6px;margin-top:9px}.watch-actions button,.watch-actions a{flex:1;border:1px solid var(--line);background:#0a1922;color:#91aaa7;border-radius:8px;padding:7px 8px;text-align:center;font-size:9px;font-weight:850}.watch-actions button:hover{border-color:var(--teal);color:var(--teal)}.watch-actions a:hover{border-color:var(--amber);color:var(--amber)}
    .watch-loading{opacity:.62}.watch-note{color:#657f7c;font-size:9px;margin-top:8px}.watch-empty{padding:34px 16px;text-align:center;color:var(--muted)}
    @media(max-width:900px){.watch-summary{grid-template-columns:repeat(2,1fr)}.watch-grid{grid-template-columns:1fr}}
    @media(max-width:520px){.watch-add input{width:118px}.watch-summary{gap:7px}.watch-metric{padding:9px}.watch-stats{grid-template-columns:1fr 1fr 1fr}}
    '''
    body = body.replace("  </style>", extra_css + "\n  </style>", 1)

    market_nav = '<a class="nav-item" href="/market-center"><span>⌁</span><b>Piyasa</b></a>'
    watch_nav = '<button class="nav-item" data-view="watchlist"><span>★</span><b>İzleme Listesi</b></button>'
    if market_nav not in body:
        raise RuntimeError("V2 Piyasa menüsü bulunamadı.")
    body = body.replace(market_nav, market_nav + "\n      " + watch_nav, 1)

    mobile_market = '<a href="/market-center"><span>⌁</span>Piyasa</a>'
    mobile_watch = '<button data-view="watchlist"><span>★</span>İzle</button>'
    body = body.replace(mobile_market, mobile_market + "\n  " + mobile_watch, 1)

    title_anchor = "const titles={home:'Ana Sayfa',signals:'Sinyaller',trades:'İşlemler',results:'Sonuçlar',system:'Sistem'};"
    if title_anchor not in body:
        raise RuntimeError("V2 görünüm başlık haritası bulunamadı.")
    body = body.replace(
        title_anchor,
        "const titles={home:'Ana Sayfa',signals:'Sinyaller',trades:'İşlemler',results:'Sonuçlar',watchlist:'İzleme Listesi',system:'Sistem'};",
        1,
    )

    section = r'''
      <section class="page" id="page-watchlist">
        <div class="page-head">
          <div><h1>İzleme Listesi</h1><p>Favori coinlerini canlı fiyat, 15m momentum ve sistem durumuyla tek ekranda takip et.</p></div>
          <div class="watch-add"><input id="watchAddInput" placeholder="BTC veya BTCUSDT" maxlength="20"><button class="btn primary" id="watchAddBtn" type="button">Favoriye ekle</button><button class="btn" id="watchRefreshBtn" type="button">Yenile</button></div>
        </div>
        <div class="watch-quick"><span class="home-note" style="align-self:center">Hızlı ekle:</span><button type="button" data-watch-add="BTCUSDT">BTC</button><button type="button" data-watch-add="ETHUSDT">ETH</button><button type="button" data-watch-add="SOLUSDT">SOL</button><button type="button" data-watch-add="BNBUSDT">BNB</button><button type="button" data-watch-add="XRPUSDT">XRP</button></div>
        <div class="watch-summary" id="watchSummary"></div>
        <section class="panel"><div class="panel-head"><div><h2>Favori coinler</h2><small>Fiyat 30 sn, göstergeler yaklaşık 2 dk önbellekle yenilenir</small></div><span class="home-note" id="watchUpdated">—</span></div><div class="panel-body"><div class="watch-grid" id="watchGrid"></div><div class="watch-note">RSI, EMA ve hacim yalnız OKX public 15m mumlarından hesaplanır. Bu ekran emir açmaz ve sinyal üretmez.</div></div></section>
      </section>
'''
    system_anchor = '      <section class="page admin-only" id="page-system">'
    if system_anchor not in body:
        raise RuntimeError("V2 Sistem bölümü bulunamadı.")
    body = body.replace(system_anchor, section + "\n" + system_anchor, 1)

    script = r'''
<script nonce="__NONCE__">
(() => {
  const $=id=>document.getElementById(id),FAV='kripto_focus_favs';
  const indicatorCache=new Map();let dashboard=window.__kriptoDashboardData||null,loading=false;
  const normalize=v=>{let s=String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'');if(s&&!s.endsWith('USDT'))s+='USDT';return s;};
  const valid=s=>/^[A-Z0-9]{2,15}USDT$/.test(s);
  const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null;};
  const fmt=v=>{const n=num(v);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{maximumFractionDigits:2});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{maximumFractionDigits:5});return n.toLocaleString('tr-TR',{maximumFractionDigits:9});};
  const esc=v=>String(v??'').replace(/[&<>\"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}[ch]));
  const direction=r=>String(r?.direction||'').toUpperCase(),outcome=r=>String(r?.outcome||r?.result||'').toUpperCase(),system=r=>String(r?.system_label||r?.system||r?.source||'Sistem');
  function favorites(){try{const v=JSON.parse(localStorage.getItem(FAV)||'[]');return Array.isArray(v)?[...new Set(v.map(normalize).filter(valid))].slice(0,12):[];}catch{return [];}}
  function saveFavorites(list){try{localStorage.setItem(FAV,JSON.stringify([...new Set(list.map(normalize).filter(valid))].slice(0,12)));}catch{}}
  function ema(values,p){if(!values.length)return null;const k=2/(p+1);let e=values[0];for(let i=1;i<values.length;i++)e=values[i]*k+e*(1-k);return e;}
  function rsi(values,p=14){if(values.length<=p)return null;let gains=0,losses=0;for(let i=values.length-p;i<values.length;i++){const d=values[i]-values[i-1];if(d>=0)gains+=d;else losses-=d;}if(losses===0)return 100;const rs=(gains/p)/(losses/p);return 100-(100/(1+rs));}
  function compute(candles){const closes=candles.map(c=>num(c.close)).filter(v=>v!==null),vols=candles.map(c=>num(c.volume)).filter(v=>v!==null);const e20=ema(closes.slice(-80),20),e50=ema(closes.slice(-100),50),rv=rsi(closes);let vr=null;if(vols.length>=21){const avg=vols.slice(-21,-1).reduce((a,b)=>a+b,0)/20;if(avg>0)vr=vols.at(-1)/avg;}let trend='NÖTR';if(e20!==null&&e50!==null)trend=e20>e50?'YUKARI':'AŞAĞI';return{rsi:rv,e20,e50,vr,trend};}
  async function indicator(symbol,force=false){const cached=indicatorCache.get(symbol);if(!force&&cached&&Date.now()-cached.at<120000)return cached.value;try{const r=await fetch(`/api/market/candles?symbol=${encodeURIComponent(symbol)}&bar=15m`,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');throw new Error('Oturum gerekli');}const p=await r.json();if(!r.ok)throw new Error(p.error||p.message||`HTTP ${r.status}`);const value=compute(Array.isArray(p.candles)?p.candles:[]);indicatorCache.set(symbol,{at:Date.now(),value});return value;}catch{return{rsi:null,e20:null,e50:null,vr:null,trend:'NÖTR'};}}
  async function indicators(symbols,force=false){const result=new Map(),queue=[...symbols];async function worker(){while(queue.length){const s=queue.shift();result.set(s,await indicator(s,force));}}await Promise.all(Array.from({length:Math.min(3,queue.length)},worker));return result;}
  function context(symbol){const open=Array.isArray(dashboard?.open_trades)?dashboard.open_trades:[],results=Array.isArray(dashboard?.recent_results)?dashboard.recent_results:[];const active=open.find(r=>normalize(r.symbol)===symbol);if(active)return{kind:direction(active)==='SHORT'?'short':'long',label:`SİNYAL ${direction(active)||'AÇIK'}`,detail:system(active)};const last=results.find(r=>normalize(r.symbol)===symbol);if(last)return{kind:'result',label:outcome(last)||'SONUÇ',detail:system(last)};return{kind:'none',label:'SİNYAL YOK',detail:'Yakın kayıt yok'};}
  function metric(label,value,cls=''){return `<div class="watch-metric ${cls}"><small>${esc(label)}</small><strong>${esc(value)}</strong></div>`;}
  function renderSummary(list,overview){const open=Array.isArray(dashboard?.open_trades)?dashboard.open_trades:[],fav=new Set(list),signalCount=open.filter(r=>fav.has(normalize(r.symbol))).length,changes=list.map(s=>num(overview.get(s)?.change_24h_pct)).filter(v=>v!==null),up=changes.filter(v=>v>0).length,avg=changes.length?changes.reduce((a,b)=>a+b,0)/changes.length:null;$('watchSummary').innerHTML=[metric('Favori',list.length,'amber'),metric('Aktif sinyal',signalCount,'blue'),metric('24s yükselen',up,'green'),metric('Ort. 24s',avg===null?'—':`${avg>=0?'+':''}${avg.toFixed(2)}%`,avg!==null&&avg>=0?'green':'')].join('');}
  function trendClass(t){return t==='YUKARI'?'up':t==='AŞAĞI'?'down':'flat';}
  function renderCards(list,overview,inds){if(!list.length){$('watchGrid').innerHTML='<div class="watch-empty" style="grid-column:1/-1">Henüz favori coin yok. Yukarıdan coin ekleyebilir veya Coin Analiz ekranında ☆ kullanabilirsin.</div>';return;}$('watchGrid').innerHTML=list.map(symbol=>{const o=overview.get(symbol)||{},ch=num(o.change_24h_pct),ind=inds.get(symbol)||{},ctx=context(symbol),tc=trendClass(ind.trend);return `<article class="watch-card"><div class="watch-top"><div class="watch-mark">${esc(symbol.replace('USDT','').slice(0,5))}</div><div class="watch-name"><strong>${esc(symbol)}</strong><small>USDT · 15m izleme</small></div><button class="watch-remove" type="button" data-watch-remove="${esc(symbol)}" title="Favoriden çıkar">×</button></div><div class="watch-price"><strong>${fmt(o.last)}</strong><span class="watch-change ${ch===null?'flat':ch>=0?'up':'down'}">${ch===null?'—':`${ch>=0?'+':''}${ch.toFixed(2)}% · 24s`}</span></div><div class="watch-stats"><div class="watch-stat"><small>RSI 14</small><b>${ind.rsi==null?'—':ind.rsi.toFixed(1)}</b></div><div class="watch-stat"><small>Trend</small><b class="watch-trend ${tc}">${esc(ind.trend||'NÖTR')}</b></div><div class="watch-stat"><small>Hacim</small><b>${ind.vr==null?'—':`${ind.vr.toFixed(2)}x`}</b></div></div><div class="watch-context"><div><small>Bizim sistem</small><br><b style="font-size:9px">${esc(ctx.detail)}</b></div><span class="watch-state ${ctx.kind}">${esc(ctx.label)}</span></div><div class="watch-actions"><button type="button" data-focus-symbol="${esc(symbol)}">Hızlı analiz</button><a href="/market-center?symbol=${encodeURIComponent(symbol)}&bar=15m">Tam grafik</a></div></article>`;}).join('');}
  async function loadWatchlist(force=false){if(loading)return;loading=true;const grid=$('watchGrid'),list=favorites();grid?.classList.add('watch-loading');try{if(!list.length){renderSummary([],new Map());renderCards([],new Map(),new Map());$('watchUpdated').textContent='Favori bekleniyor';return;}let overview=new Map();try{const r=await fetch(`/api/market/overview?symbols=${encodeURIComponent(list.join(','))}`,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');return;}const p=await r.json();if(r.ok)overview=new Map((p.items||[]).map(i=>[normalize(i.symbol),i]));}catch{}const inds=await indicators(list,force);renderSummary(list,overview);renderCards(list,overview,inds);$('watchUpdated').textContent=new Date().toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'})+' güncellendi';}finally{loading=false;grid?.classList.remove('watch-loading');}}
  function addFavorite(raw){const s=normalize(raw);if(!valid(s))return;const list=favorites();if(!list.includes(s))list.unshift(s);saveFavorites(list);$('watchAddInput').value='';loadWatchlist(true);}
  $('watchAddBtn')?.addEventListener('click',()=>addFavorite($('watchAddInput')?.value));$('watchAddInput')?.addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();addFavorite(e.currentTarget.value);}});$('watchRefreshBtn')?.addEventListener('click',()=>loadWatchlist(true));
  document.addEventListener('click',event=>{const add=event.target.closest('[data-watch-add]');if(add){addFavorite(add.dataset.watchAdd);return;}const remove=event.target.closest('[data-watch-remove]');if(remove){const list=favorites().filter(s=>s!==normalize(remove.dataset.watchRemove));saveFavorites(list);loadWatchlist(true);return;}if(event.target.closest('#focusStar'))setTimeout(()=>loadWatchlist(true),60);const view=event.target.closest('[data-view="watchlist"]');if(view)setTimeout(()=>loadWatchlist(false),20);});
  window.addEventListener('kripto-dashboard-data',event=>{dashboard=event.detail||{};if(document.getElementById('page-watchlist')?.classList.contains('active'))loadWatchlist(false);});
  setInterval(()=>{if(document.getElementById('page-watchlist')?.classList.contains('active'))loadWatchlist(false);},30000);
  loadWatchlist(false);
})();
</script>
'''.replace("__NONCE__", nonce_attr)

    return body.replace("</body>", script + "\n</body>", 1)


def make_v25_handler(config: PanelConfig, service, sessions, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = alert.make_v24_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V25Handler(BaseHandler):
        server_version = "KriptoPanel/2.5"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, watchlist_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            return super().do_GET()

    return V25Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.5 izleme listesi.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = alert.notify.home.v21.focus.v2.v19.v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = alert.notify.home.v21.focus.v2.v19.v18.account_store_from_env(config)
    handler = make_v25_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        OKXMarketDataClient(),
        alert.notify.home.v21.market.OKXMarketOverviewClient(),
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} watchlist=on sound_alert=optional notifications=on")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
