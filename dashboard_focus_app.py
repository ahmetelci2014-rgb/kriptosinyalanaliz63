"""Kripto Kontrol Merkezi V2.1 - hızlı coin analiz katmanı.

V2 sade arayüzünü korur ve yalnız kullanım kolaylığı ekler:
- Coin kartına tıklayınca hızlı analiz çekmecesi açılır.
- OKX public mumlarından RSI14, EMA20/50 ve hacim oranı hesaplanır.
- Coinin paneldeki açık/geçmiş sinyal bağlamı aynı yerde gösterilir.
- Favori coinler yalnız tarayıcının localStorage alanında tutulur.

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

import dashboard_compact_app as v2
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_1_FOCUS_2026_08_14"


def focus_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = v2.compact_dashboard_page(session, nonce)
    nonce_attr = html.escape(nonce, quote=True)

    extra_css = r'''
    .coin-quick{display:flex;align-items:center;gap:5px;border:1px solid var(--line);background:#091720;border-radius:999px;padding:3px 4px 3px 9px}.coin-quick input{width:92px;border:0;outline:0;background:transparent;color:var(--text);font-size:10px;text-transform:uppercase}.coin-quick button{border:0;background:var(--teal);color:#04120f;border-radius:999px;padding:5px 8px;font-size:9px;font-weight:900}
    .focus-overlay{position:fixed;inset:0;background:rgba(1,8,12,.68);backdrop-filter:blur(4px);z-index:80;opacity:0;pointer-events:none;transition:opacity .2s}.focus-overlay.open{opacity:1;pointer-events:auto}.focus-drawer{position:fixed;top:0;right:0;bottom:0;width:min(540px,94vw);background:#08131b;border-left:1px solid var(--line);z-index:81;transform:translateX(102%);transition:transform .24s ease;display:flex;flex-direction:column;box-shadow:-25px 0 70px rgba(0,0,0,.34)}.focus-drawer.open{transform:translateX(0)}
    .focus-head{padding:17px 18px 13px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px}.focus-symbol{flex:1;min-width:0}.focus-symbol strong{display:block;font-size:18px}.focus-symbol small{color:var(--muted);font-size:10px}.focus-close,.focus-star{width:34px;height:34px;border:1px solid var(--line);background:#0b1b24;color:#9db6b3;border-radius:10px;font-size:16px}.focus-star.on{color:var(--amber);border-color:rgba(255,189,89,.4)}
    .focus-body{padding:14px 16px 30px;overflow:auto}.focus-price{display:flex;align-items:flex-end;justify-content:space-between;gap:12px;margin-bottom:12px}.focus-price strong{font-size:26px;letter-spacing:-.03em}.focus-change{font-size:12px;font-weight:850}.focus-change.up{color:var(--green)}.focus-change.down{color:var(--red)}
    .focus-bars{display:flex;gap:5px;flex-wrap:wrap;margin:9px 0 12px}.focus-bars button{border:1px solid var(--line);background:#0a1821;color:#819c99;border-radius:8px;padding:6px 8px;font-size:9px;font-weight:850}.focus-bars button.active{border-color:rgba(44,230,191,.45);color:var(--teal);background:rgba(44,230,191,.08)}
    .focus-chart{height:260px;border:1px solid var(--line);background:#071119;border-radius:12px;padding:7px;position:relative}.focus-chart canvas{width:100%;height:100%;display:block}.focus-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:10px}.focus-stat{border:1px solid var(--line);background:#0a171f;border-radius:10px;padding:9px}.focus-stat small{display:block;color:#607d79;font-size:8px;text-transform:uppercase}.focus-stat b{display:block;margin-top:3px;font-size:11px}.focus-context{margin-top:12px;border:1px solid var(--line);background:#0a171f;border-radius:12px;padding:11px}.focus-context h3{font-size:11px;margin:0 0 7px}.focus-context-line{display:flex;justify-content:space-between;gap:8px;padding:5px 0;border-bottom:1px solid rgba(29,48,59,.6);font-size:10px}.focus-context-line:last-child{border:0}.focus-context-line span{color:var(--muted)}
    .focus-favs{display:flex;gap:5px;flex-wrap:wrap;margin:0 0 12px}.focus-favs button{border:1px solid var(--line);background:#0a171f;color:#9cb3b0;border-radius:999px;padding:5px 8px;font-size:9px}.focus-favs button:hover{border-color:var(--teal);color:var(--teal)}.focus-actions{display:flex;gap:7px;margin-top:12px}.focus-actions a{flex:1;text-align:center;border:1px solid var(--line);border-radius:9px;padding:8px 9px;font-size:10px;font-weight:850}.focus-actions a:first-child{background:var(--teal);color:#03110e;border-color:var(--teal)}
    .row-card,.wide-card,.result-item{cursor:pointer}.row-card:active,.wide-card:active,.result-item:active{transform:scale(.998)}
    @media(max-width:760px){.coin-quick input{display:none}.coin-quick{padding-left:4px}.focus-drawer{width:100vw;top:7vh;border-radius:18px 18px 0 0;border-left:0;border-top:1px solid var(--line)}.focus-chart{height:230px}.focus-stats{grid-template-columns:1fr 1fr}}
    '''
    body = body.replace("  </style>", extra_css + "\n  </style>", 1)

    quick = '<div class="coin-quick"><input id="quickCoinInput" value="BTCUSDT" aria-label="Coin"><button id="quickCoinBtn" type="button">Coin İncele</button></div>'
    body = body.replace('<button class="icon-btn" id="refreshBtn" type="button">Yenile</button>', quick + '<button class="icon-btn" id="refreshBtn" type="button">Yenile</button>', 1)

    drawer = r'''
<div class="focus-overlay" id="focusOverlay"></div>
<aside class="focus-drawer" id="focusDrawer" aria-hidden="true">
  <div class="focus-head">
    <button class="focus-star" id="focusStar" type="button" title="Favoriye ekle">☆</button>
    <div class="focus-symbol"><strong id="focusSymbol">BTCUSDT</strong><small id="focusSubtitle">Hızlı coin analizi · OKX public veri</small></div>
    <button class="focus-close" id="focusClose" type="button" aria-label="Kapat">×</button>
  </div>
  <div class="focus-body">
    <div class="focus-favs" id="focusFavs"></div>
    <div class="focus-price"><strong id="focusPrice">—</strong><span class="focus-change" id="focusChange">—</span></div>
    <div class="focus-bars" id="focusBars"><button data-bar="5m">5m</button><button data-bar="15m" class="active">15m</button><button data-bar="1H">1H</button><button data-bar="4H">4H</button><button data-bar="1D">1D</button></div>
    <div class="focus-chart"><canvas id="focusCanvas"></canvas></div>
    <div class="focus-stats">
      <div class="focus-stat"><small>RSI 14</small><b id="focusRsi">—</b></div>
      <div class="focus-stat"><small>EMA 20 / 50</small><b id="focusEma">—</b></div>
      <div class="focus-stat"><small>Hacim</small><b id="focusVolume">—</b></div>
      <div class="focus-stat"><small>Kısa Trend</small><b id="focusTrend">—</b></div>
    </div>
    <div class="focus-context"><h3>Bizim sistemdeki durum</h3><div id="focusContext"><div class="empty">Kontrol ediliyor…</div></div></div>
    <div class="focus-actions"><a id="focusFullChart" href="/market-center?symbol=BTCUSDT">Tam grafik</a><a href="/advanced">Gelişmiş görünüm</a></div>
  </div>
</aside>
'''

    script = f'''<script nonce="{nonce_attr}">
(() => {{
  const drawer=document.getElementById('focusDrawer'),overlay=document.getElementById('focusOverlay'),canvas=document.getElementById('focusCanvas');
  if(!drawer||!overlay||!canvas)return;
  const state={{symbol:'BTCUSDT',bar:'15m',candles:[],dashboard:null,overview:null}};
  const $=id=>document.getElementById(id);
  const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const valid=s=>/^[A-Z0-9]{{2,15}}USDT$/.test(s);
  const number=v=>{{const n=Number(v);return Number.isFinite(n)?n:null;}};
  const fmt=v=>{{const n=number(v);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{{maximumFractionDigits:2}});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{{maximumFractionDigits:5}});return n.toLocaleString('tr-TR',{{maximumFractionDigits:9}});}};
  const esc=v=>String(v??'').replace(/[&<>\"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[ch]));
  const favorites=()=>{{try{{return JSON.parse(localStorage.getItem('kripto_focus_favs')||'[]').filter?.(valid)||[];}}catch{{return [];}}}};
  const saveFavs=list=>{{try{{localStorage.setItem('kripto_focus_favs',JSON.stringify([...new Set(list)].slice(0,12)));}}catch{{}}}};
  function renderFavs(){{const list=favorites();$('focusFavs').innerHTML=list.length?list.map(s=>`<button type="button" data-focus-symbol="${{esc(s)}}">★ ${{esc(s.replace('USDT',''))}}</button>`).join(''):'<span style="color:var(--muted);font-size:9px">Favori coin eklemek için ☆ düğmesini kullan.</span>';const on=list.includes(state.symbol);$('focusStar').classList.toggle('on',on);$('focusStar').textContent=on?'★':'☆';}}
  function ema(values,p){{if(!values.length)return null;const k=2/(p+1);let e=values[0];for(let i=1;i<values.length;i++)e=values[i]*k+e*(1-k);return e;}}
  function rsi(values,p=14){{if(values.length<=p)return null;let gains=0,losses=0;for(let i=values.length-p;i<values.length;i++){{const d=values[i]-values[i-1];if(d>=0)gains+=d;else losses-=d;}}if(losses===0)return 100;const rs=(gains/p)/(losses/p);return 100-(100/(1+rs));}}
  function indicators(candles){{const closes=candles.map(c=>number(c.close)).filter(v=>v!==null),vols=candles.map(c=>number(c.volume)).filter(v=>v!==null);const e20=ema(closes.slice(-80),20),e50=ema(closes.slice(-100),50),rv=rsi(closes);let vr=null;if(vols.length>=21){{const recent=vols.at(-1),avg=vols.slice(-21,-1).reduce((a,b)=>a+b,0)/20;if(avg>0)vr=recent/avg;}}let trend='NÖTR';if(e20!==null&&e50!==null)trend=e20>e50?'YUKARI':'AŞAĞI';return{{rsi:rv,e20,e50,vr,trend}};}}
  function draw(candles){{const box=canvas.parentElement.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),w=Math.max(320,box.width-14),h=Math.max(210,box.height-14);canvas.width=w*dpr;canvas.height=h*dpr;canvas.style.width=`${{w}}px`;canvas.style.height=`${{h}}px`;const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);if(!candles.length)return;const lows=candles.map(c=>number(c.low)).filter(v=>v!==null),highs=candles.map(c=>number(c.high)).filter(v=>v!==null),lo=Math.min(...lows),hi=Math.max(...highs),pad=Math.max((hi-lo)*.06,Math.abs(hi)*.001,1e-10),min=lo-pad,max=hi+pad,m={{l:8,r:68,t:10,b:18}},cw=w-m.l-m.r,ch=h-m.t-m.b,y=v=>m.t+(max-number(v))/(max-min)*ch;ctx.strokeStyle='rgba(127,155,152,.15)';ctx.fillStyle='#718d89';ctx.font='9px system-ui';for(let i=0;i<=4;i++){{const yy=m.t+ch*i/4;ctx.beginPath();ctx.moveTo(m.l,yy);ctx.lineTo(w-m.r,yy);ctx.stroke();ctx.fillText(fmt(max-(max-min)*i/4),w-m.r+4,yy+3);}}const step=cw/candles.length,bw=Math.max(1,Math.min(6,step*.62));candles.forEach((c,i)=>{{const x=m.l+step*(i+.5),o=y(c.open),cl=y(c.close),hh=y(c.high),ll=y(c.low),up=number(c.close)>=number(c.open);ctx.strokeStyle=ctx.fillStyle=up?'#42e28c':'#ff627d';ctx.beginPath();ctx.moveTo(x,hh);ctx.lineTo(x,ll);ctx.stroke();ctx.fillRect(x-bw/2,Math.min(o,cl),bw,Math.max(1,Math.abs(cl-o)));}});}}
  function renderContext(data){{const open=Array.isArray(data?.open_trades)?data.open_trades:[],results=Array.isArray(data?.recent_results)?data.recent_results:[],o=open.find(r=>normalize(r.symbol)===state.symbol),recent=results.filter(r=>normalize(r.symbol)===state.symbol).slice(0,3);let html='';if(o)html+=`<div class="focus-context-line"><span>Açık sinyal</span><b>${{esc(o.direction||'')}} · ${{esc(o.system_label||o.system||'Sistem')}}</b></div><div class="focus-context-line"><span>Giriş / TP1 / SL</span><b>${{fmt(o.entry)}} / ${{fmt(o.tp1)}} / ${{fmt(o.sl)}}</b></div>`;recent.forEach(r=>html+=`<div class="focus-context-line"><span>${{esc(r.system_label||r.system||'Sonuç')}}</span><b>${{esc(r.outcome||r.result||'KAPALI')}}</b></div>`);$('focusContext').innerHTML=html||'<div class="empty">Bu coin için açık veya yakın tarihli sistem kaydı yok.</div>';}}
  async function ensureDashboard(){{if(state.dashboard)return state.dashboard;const r=await fetch('/api/dashboard',{{credentials:'same-origin',cache:'no-store',headers:{{Accept:'application/json'}}}});if(r.status===401){{location.assign('/login');throw new Error('Oturum gerekli');}}state.dashboard=await r.json();return state.dashboard;}}
  async function loadOverview(){{try{{const r=await fetch(`/api/market/overview?symbols=${{encodeURIComponent(state.symbol)}}`,{{credentials:'same-origin',cache:'no-store',headers:{{Accept:'application/json'}}}});const p=await r.json();const item=Array.isArray(p.items)?p.items[0]:null;state.overview=item;if(item){{$('focusPrice').textContent=fmt(item.last);const ch=number(item.change_24h_pct);$('focusChange').textContent=ch===null?'—':`${{ch>=0?'+':''}}${{ch.toFixed(2)}}% · 24s`;$('focusChange').className='focus-change '+(ch>=0?'up':'down');}}}}catch{{$('focusChange').textContent='24s veri alınamadı';}}}}
  async function loadCandles(){{$('focusSubtitle').textContent=`${{state.bar}} grafik yükleniyor…`;try{{const r=await fetch(`/api/market/candles?symbol=${{encodeURIComponent(state.symbol)}}&bar=${{encodeURIComponent(state.bar)}}`,{{credentials:'same-origin',cache:'no-store',headers:{{Accept:'application/json'}}}});if(r.status===401){{location.assign('/login');return;}}const p=await r.json();if(!r.ok)throw new Error(p.message||p.error||`HTTP ${{r.status}}`);state.candles=Array.isArray(p.candles)?p.candles:[];draw(state.candles);const ind=indicators(state.candles);$('focusRsi').textContent=ind.rsi===null?'—':ind.rsi.toFixed(1);$('focusEma').textContent=ind.e20===null||ind.e50===null?'—':`${{fmt(ind.e20)}} / ${{fmt(ind.e50)}}`;$('focusVolume').textContent=ind.vr===null?'—':`${{ind.vr.toFixed(2)}}x`;$('focusTrend').textContent=ind.trend;$('focusSubtitle').textContent=`${{state.bar}} · ${{p.market_type||'OKX'}} · salt okunur`;if(!state.overview)$('focusPrice').textContent=fmt(p.last_price);}}catch(err){{$('focusSubtitle').textContent=`Grafik alınamadı: ${{err.message}}`;state.candles=[];draw([]);}}}}
  async function openFocus(symbol){{const s=normalize(symbol);if(!valid(s))return;state.symbol=s;$('focusSymbol').textContent=s;$('focusFullChart').href=`/market-center?symbol=${{encodeURIComponent(s)}}&bar=${{encodeURIComponent(state.bar)}}`;drawer.classList.add('open');overlay.classList.add('open');drawer.setAttribute('aria-hidden','false');document.body.style.overflow='hidden';renderFavs();$('focusContext').innerHTML='<div class="empty">Kontrol ediliyor…</div>';state.overview=null;await Promise.allSettled([loadOverview(),loadCandles(),ensureDashboard().then(renderContext)]);}}
  function closeFocus(){{drawer.classList.remove('open');overlay.classList.remove('open');drawer.setAttribute('aria-hidden','true');document.body.style.overflow='';}}
  function extractSymbol(card){{const direct=card.querySelector('.coin strong')?.textContent||card.querySelector('.result-main strong')?.textContent||card.textContent||'';return (String(direct).toUpperCase().match(/[A-Z0-9]{{2,15}}USDT/)||[])[0]||'';}}
  document.addEventListener('click',event=>{{const symbolBtn=event.target.closest('[data-focus-symbol]');if(symbolBtn){{event.preventDefault();openFocus(symbolBtn.dataset.focusSymbol);return;}}const card=event.target.closest('.row-card,.wide-card,.result-item');if(card&&!event.target.closest('a,button,input,select')){{const s=extractSymbol(card);if(s)openFocus(s);}}}});
  $('quickCoinBtn')?.addEventListener('click',()=>openFocus($('quickCoinInput')?.value||'BTCUSDT'));$('quickCoinInput')?.addEventListener('keydown',e=>{{if(e.key==='Enter'){{e.preventDefault();openFocus(e.currentTarget.value);}}}});$('focusClose').addEventListener('click',closeFocus);overlay.addEventListener('click',closeFocus);document.addEventListener('keydown',e=>{{if(e.key==='Escape')closeFocus();}});
  $('focusBars').addEventListener('click',event=>{{const b=event.target.closest('[data-bar]');if(!b)return;state.bar=b.dataset.bar;document.querySelectorAll('#focusBars [data-bar]').forEach(x=>x.classList.toggle('active',x===b));$('focusFullChart').href=`/market-center?symbol=${{encodeURIComponent(state.symbol)}}&bar=${{encodeURIComponent(state.bar)}}`;loadCandles();}});
  $('focusStar').addEventListener('click',()=>{{const list=favorites(),i=list.indexOf(state.symbol);if(i>=0)list.splice(i,1);else list.unshift(state.symbol);saveFavs(list);renderFavs();}});window.addEventListener('resize',()=>{{if(drawer.classList.contains('open')&&state.candles.length)draw(state.candles);}});
}})();
</script>'''
    return body.replace("</body>", drawer + "\n" + script + "\n</body>", 1)


def make_v21_handler(config: PanelConfig, service, sessions, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = v2.make_v2_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V21Handler(BaseHandler):
        server_version = "KriptoPanel/2.1"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, focus_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            return super().do_GET()

    return V21Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.1 hızlı coin analiz arayüzü.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = v2.v19.v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = v2.v19.v18.account_store_from_env(config)
    handler = make_v21_handler(config, service, sessions, limiter, store, OKXMarketDataClient(), v2.v19.OKXMarketOverviewClient())
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} focus_drawer=on compact_ui=on")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
