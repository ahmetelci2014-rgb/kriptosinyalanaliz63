"""Kripto Kontrol Merkezi V2.2 - akıllı ve kişisel ana sayfa.

V2.1 coin analizini korur; yalnız ana sayfayı günlük kullanım için sadeleştirir:
- Bugünün sinyal/sonuç özeti
- Öne çıkan açık sinyaller
- Tarayıcı favorilerinin canlı piyasa özeti
- Son sonuçlardan sade performans görünümü

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

import dashboard_focus_app as focus
import dashboard_focus_market_app as v21
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_2_HOME_2026_08_14"


def home_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = focus.focus_dashboard_page(session, nonce)
    nonce_attr = html.escape(nonce, quote=True)

    extra_css = r'''
    .home-smart-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin-bottom:14px}.home-flow{display:flex;flex-direction:column}.home-flow-line{display:grid;grid-template-columns:auto 1fr auto;gap:9px;align-items:center;padding:9px 4px;border-bottom:1px solid rgba(29,48,59,.7)}.home-flow-line:last-child{border-bottom:0}.home-flow-icon{width:30px;height:30px;border-radius:9px;display:grid;place-items:center;background:#0d2029;border:1px solid #1b3943;font-size:11px}.home-flow-main{min-width:0}.home-flow-main strong{display:block;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.home-flow-main small{display:block;color:var(--muted);font-size:9px}.home-flow-time{font-size:9px;color:#68827f;white-space:nowrap}.home-favs{display:flex;flex-direction:column;gap:6px}.home-fav{border:1px solid var(--line);background:#091720;border-radius:10px;padding:9px 10px;display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;cursor:pointer}.home-fav:hover{border-color:rgba(44,230,191,.4)}.home-fav strong{font-size:11px}.home-fav small{display:block;color:var(--muted);font-size:9px}.home-fav-price{text-align:right}.home-fav-price b{display:block;font-size:11px}.home-fav-price span{font-size:9px;font-weight:850}.home-strong-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.home-strong{border:1px solid var(--line);background:#091720;border-radius:11px;padding:11px;cursor:pointer}.home-strong:hover{border-color:rgba(44,230,191,.4)}.home-strong-top{display:flex;justify-content:space-between;gap:7px;align-items:center}.home-strong strong{font-size:12px}.home-strong small{color:var(--muted);font-size:9px}.home-strong-meta{display:grid;grid-template-columns:1fr 1fr;gap:5px;margin-top:8px}.home-strong-meta div{background:#07131a;border-radius:7px;padding:6px}.home-strong-meta span{display:block;color:#607a77;font-size:8px}.home-strong-meta b{font-size:10px}.home-score{color:var(--teal);font-size:10px;font-weight:900}.home-note{color:var(--muted);font-size:9px}.home-hidden-compat{display:none!important}.metric.compact strong{font-size:20px}.metric.compact em{margin-top:4px;display:block}
    @media(max-width:980px){.home-smart-grid{grid-template-columns:1fr}.home-strong-grid{grid-template-columns:1fr 1fr}}
    @media(max-width:620px){.home-strong-grid{grid-template-columns:1fr}.home-flow-line{grid-template-columns:auto 1fr}.home-flow-time{display:none}}
    '''
    body = body.replace("  </style>", extra_css + "\n  </style>", 1)

    start = '<section class="page active" id="page-home">'
    end = '<section class="page" id="page-signals">'
    if start not in body or end not in body:
        raise RuntimeError("V2 ana sayfa işaretleri bulunamadı.")
    before, tail = body.split(start, 1)
    _, after = tail.split(end, 1)
    home = r'''
<section class="page active" id="page-home">
  <div class="page-head">
    <div><h1>Kontrol Merkezi</h1><p>Bugün ne oldu, hangi sinyaller önde ve favoriler ne durumda?</p></div>
    <div class="actions"><button class="btn primary" type="button" data-focus-symbol="BTCUSDT">Coin incele</button><a class="btn" href="/market-center">Tüm piyasa</a></div>
  </div>
  <div class="summary" id="homeSmartMetrics"></div>
  <div class="home-smart-grid">
    <section class="panel">
      <div class="panel-head"><div><h2>Bugünün akışı</h2><small>Yeni sinyaller ve kapanan sonuçlar</small></div><span class="home-note" id="homeTodayLabel"></span></div>
      <div class="panel-body home-flow" id="homeTodayFlow"></div>
    </section>
    <section class="panel">
      <div class="panel-head"><div><h2>Favorilerim</h2><small>Bu cihazda kaydettiğin coinler</small></div><a class="btn" href="/market-center">Piyasa</a></div>
      <div class="panel-body home-favs" id="homeFavoriteMarket"></div>
    </section>
  </div>
  <section class="panel">
    <div class="panel-head"><div><h2>Öne çıkan açık sinyaller</h2><small>Skor bilgisi varsa en güçlüler; yoksa en güncel açıklar</small></div><button class="btn" data-view="signals">Tüm sinyaller</button></div>
    <div class="panel-body"><div class="home-strong-grid" id="homeStrongSignals"></div></div>
  </section>
  <div class="home-hidden-compat"><div id="homeMetrics"></div><div id="homeOpen"></div><div id="homeResults"></div></div>
</section>
'''
    body = before + home + end + after

    dispatch_old = "function renderAll(data){state.data=data;"
    dispatch_new = "function renderAll(data){state.data=data;window.__kriptoDashboardData=data;window.dispatchEvent(new CustomEvent('kripto-dashboard-data',{detail:data}));"
    body = body.replace(dispatch_old, dispatch_new, 1)

    script = rf'''
<script nonce="{nonce_attr}">
(() => {{
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>\"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[ch]));
  const num=v=>{{const n=Number(v);return Number.isFinite(n)?n:null;}};
  const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const system=r=>String(r?.system_label||r?.system||r?.source||'Sistem');
  const direction=r=>String(r?.direction||'').toUpperCase();
  const outcome=r=>String(r?.outcome||r?.result||'').toUpperCase();
  const fmt=v=>{{const n=num(v);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{{maximumFractionDigits:2}});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{{maximumFractionDigits:5}});return n.toLocaleString('tr-TR',{{maximumFractionDigits:9}});}};
  const parseTs=v=>{{if(v===null||v===undefined||v==='')return null;if(typeof v==='number'||/^\d+(\.\d+)?$/.test(String(v))){{let n=Number(v);if(!Number.isFinite(n))return null;if(n>1e12)n/=1000;return new Date(n*1000);}}const d=new Date(v);return Number.isNaN(d.getTime())?null:d;}};
  const rowTime=r=>parseTs(r?.opened_at||r?.sent_at||r?.created_at||r?.detected_at||r?.closed_at||r?.finalized_at||r?.updated_at);
  const resultTime=r=>parseTs(r?.closed_at||r?.finalized_at||r?.updated_at||r?.opened_at||r?.sent_at);
  const isToday=d=>d&&d.toDateString()===new Date().toDateString();
  const timeLabel=d=>d?d.toLocaleTimeString('tr-TR',{{hour:'2-digit',minute:'2-digit'}}):'—';
  const score=r=>{{for(const k of ['score','signal_score','confidence','quality_score','strength']){{const n=num(r?.[k]);if(n!==null)return n;}}return null;}};
  const isTp=o=>String(o||'').startsWith('TP')&&!String(o||'').includes('BE');
  const metric=(label,value,note,cls='')=>`<div class="metric compact ${{cls}}"><small>${{esc(label)}}</small><strong>${{esc(value)}}</strong><em>${{esc(note)}}</em></div>`;

  function renderMetrics(data){{
    const open=Array.isArray(data?.open_trades)?data.open_trades:[],results=Array.isArray(data?.recent_results)?data.recent_results:[];
    const todaySignals=[...open,...results].filter(r=>isToday(rowTime(r)));const keys=new Set(todaySignals.map(r=>`${{normalize(r.symbol)}}|${{system(r)}}|${{rowTime(r)?.getTime()||0}}`));
    const todayResults=results.filter(r=>isToday(resultTime(r))),todayTp=todayResults.filter(r=>isTp(outcome(r))).length,todaySl=todayResults.filter(r=>outcome(r)==='SL').length;
    const decided=results.filter(r=>isTp(outcome(r))||outcome(r)==='SL'),tp=decided.filter(r=>isTp(outcome(r))).length,ratio=decided.length?Math.round(tp/decided.length*100):null;
    $('homeSmartMetrics').innerHTML=[
      metric('Bugün yeni',keys.size,'Kayıtlardaki açılış zamanına göre','blue'),
      metric('Açık işlem',open.length,'Şu an takipte'),
      metric('Bugün TP / SL',`${{todayTp}} / ${{todaySl}}`,'Bugün kapanan sonuçlar',todayTp>=todaySl?'green':'red'),
      metric('Son sonuç TP oranı',ratio===null?'—':`%${{ratio}}`,`${{decided.length}} karar verilmiş sonuç`,'green')
    ].join('');
  }}

  function renderToday(data){{
    const open=Array.isArray(data?.open_trades)?data.open_trades:[],results=Array.isArray(data?.recent_results)?data.recent_results:[];const events=[];
    open.forEach(r=>{{const d=rowTime(r);if(isToday(d))events.push({{type:'SIGNAL',date:d,row:r,label:`${{direction(r)||'SİNYAL'}} · ${{system(r)}}`}});}});
    results.forEach(r=>{{const d=resultTime(r);if(isToday(d))events.push({{type:'RESULT',date:d,row:r,label:`${{outcome(r)||'KAPALI'}} · ${{system(r)}}`}});}});
    events.sort((a,b)=>(b.date?.getTime()||0)-(a.date?.getTime()||0));$('homeTodayLabel').textContent=new Date().toLocaleDateString('tr-TR',{{day:'2-digit',month:'long'}});
    $('homeTodayFlow').innerHTML=events.slice(0,7).map(e=>`<div class="home-flow-line" data-focus-symbol="${{esc(normalize(e.row.symbol))}}"><div class="home-flow-icon">${{e.type==='SIGNAL'?'⚡':'✓'}}</div><div class="home-flow-main"><strong>${{esc(e.row.symbol||'—')}}</strong><small>${{esc(e.label)}}</small></div><span class="home-flow-time">${{timeLabel(e.date)}}</span></div>`).join('')||'<div class="empty">Bugün için henüz yeni kayıt görünmüyor.</div>';
  }}

  function renderStrong(data){{
    const open=Array.isArray(data?.open_trades)?[...data.open_trades]:[];open.sort((a,b)=>{{const sa=score(a),sb=score(b);if(sa!==null||sb!==null)return (sb??-1)-(sa??-1);return (rowTime(b)?.getTime()||0)-(rowTime(a)?.getTime()||0);}});
    $('homeStrongSignals').innerHTML=open.slice(0,3).map(r=>{{const s=score(r),dir=direction(r),kind=dir==='LONG'?'long':dir==='SHORT'?'short':'';return `<div class="home-strong" data-focus-symbol="${{esc(normalize(r.symbol))}}"><div class="home-strong-top"><div><strong>${{esc(r.symbol||'—')}}</strong><small> · ${{esc(system(r))}}</small></div><span class="tag ${{kind}}">${{esc(dir||'AÇIK')}}</span></div><div class="home-strong-meta"><div><span>Giriş</span><b>${{fmt(r.entry)}}</b></div><div><span>TP1</span><b>${{fmt(r.tp1)}}</b></div></div><div style="margin-top:7px;display:flex;justify-content:space-between;align-items:center"><small>${{rowTime(r)?timeLabel(rowTime(r)):'Takipte'}}</small><span class="home-score">${{s===null?'Grafiği aç':`Skor ${{s.toFixed(s%1?1:0)}}`}}</span></div></div>`;}}).join('')||'<div class="empty" style="grid-column:1/-1">Şu anda açık sinyal yok.</div>';
  }}

  function favorites(){{try{{const v=JSON.parse(localStorage.getItem('kripto_focus_favs')||'[]');return Array.isArray(v)?v.filter(s=>/^[A-Z0-9]{{2,15}}USDT$/.test(String(s))).slice(0,8):[];}}catch{{return [];}}}}
  async function renderFavoriteMarket(){{
    const list=favorites();if(!list.length){{$('homeFavoriteMarket').innerHTML='<div class="empty">Coin analizinde ☆ ile favori ekleyebilirsin.</div>';return;}}
    $('homeFavoriteMarket').innerHTML='<div class="empty">Favoriler güncelleniyor…</div>';
    try{{const r=await fetch(`/api/market/overview?symbols=${{encodeURIComponent(list.join(','))}}`,{{credentials:'same-origin',cache:'no-store',headers:{{Accept:'application/json'}}}});if(r.status===401){{location.assign('/login');return;}}const p=await r.json();if(!r.ok)throw new Error(p.error||`HTTP ${{r.status}}`);const map=new Map((p.items||[]).map(i=>[normalize(i.symbol),i]));$('homeFavoriteMarket').innerHTML=list.map(symbol=>{{const i=map.get(normalize(symbol)),ch=num(i?.change_24h_pct);return `<div class="home-fav" data-focus-symbol="${{esc(symbol)}}"><div><strong>★ ${{esc(symbol.replace('USDT',''))}}</strong><small>USDT · hızlı analiz</small></div><div class="home-fav-price"><b>${{fmt(i?.last)}}</b><span style="color:${{ch===null?'var(--muted)':ch>=0?'var(--green)':'var(--red)'}}">${{ch===null?'—':`${{ch>=0?'+':''}}${{ch.toFixed(2)}}%`}}</span></div></div>`;}}).join('');}}catch(err){{$('homeFavoriteMarket').innerHTML=`<div class="empty">Favori piyasa verisi alınamadı.</div>`;}}
  }}

  function renderSmartHome(data){{renderMetrics(data);renderToday(data);renderStrong(data);renderFavoriteMarket();}}
  window.addEventListener('kripto-dashboard-data',event=>renderSmartHome(event.detail||{{}}));
  document.addEventListener('click',event=>{{if(event.target.closest('#focusStar'))setTimeout(renderFavoriteMarket,25);}});
  if(window.__kriptoDashboardData)renderSmartHome(window.__kriptoDashboardData);
}})();
</script>
'''
    return body.replace("</body>", script + "\n</body>", 1)


def make_v22_handler(config: PanelConfig, service, sessions, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = v21.make_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V22Handler(BaseHandler):
        server_version = "KriptoPanel/2.2"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, home_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            return super().do_GET()

    return V22Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.2 akıllı ana sayfa.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = v21.focus.v2.v19.v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = v21.focus.v2.v19.v18.account_store_from_env(config)
    handler = make_v22_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        OKXMarketDataClient(),
        v21.market.OKXMarketOverviewClient(),
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} smart_home=on focus_drawer=on compact_ui=on")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
