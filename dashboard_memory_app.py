"""Kripto Kontrol Merkezi V2.10 - Fırsat Favorileri ve Filtre Hafızası.

V2.9 aktif sayfa korumasını değiştirmeden yalnız panel tarafında:
- Fırsat kartından tek tıkla mevcut İzleme Listesi favorilerine ekleme/çıkarma,
- Fırsat arama, filtre ve sıralama tercihlerini tarayıcı localStorage alanında hatırlama,
- tercihleri tek tuşla sıfırlama
özelliklerini ekler.

Sinyal üretimi, strateji, radar, Telegram ve emir akışı bu katmanda yoktur.
"""

from __future__ import annotations

import argparse
import os
import secrets
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_market_app as market
import dashboard_product_app as product
import dashboard_route_app as route
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_10_MEMORY_FAVORITES_2026_08_14"


def memory_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = route.route_dashboard_page(session, nonce)
    nonce_attr = str(nonce).replace('"', "&quot;")

    css = r'''
    /* V2.10: fırsat kartından favori + filtre tercih hafızası. */
    #page-opportunities .opp-card{grid-template-columns:36px minmax(95px,1fr) auto auto 30px}
    .opp-fav-btn{width:28px;height:28px;border-radius:9px;border:1px solid rgba(255,189,89,.18);background:rgba(255,189,89,.035);color:#647d79;display:grid;place-items:center;padding:0;font-size:15px;line-height:1;cursor:pointer;transition:.16s ease}.opp-fav-btn:hover{color:var(--amber);border-color:rgba(255,189,89,.45);background:rgba(255,189,89,.08)}.opp-fav-btn.active{color:var(--amber);border-color:rgba(255,189,89,.38);background:rgba(255,189,89,.10);box-shadow:0 0 0 2px rgba(255,189,89,.035)}
    .opp-pref-reset{border:1px solid var(--line);background:#0b1921;color:#718a87;border-radius:9px;padding:7px 9px;font-size:8px;font-weight:850;cursor:pointer}.opp-pref-reset:hover{border-color:rgba(96,165,250,.35);color:#9fcaff}
    @media(max-width:620px){#page-opportunities .opp-card{grid-template-columns:34px 1fr auto 30px}}
    '''
    body = body.replace("  </style>", css + "\n  </style>", 1)

    script = r'''
<script nonce="__NONCE__">
(() => {
  const page=document.getElementById('page-opportunities');
  if(!page)return;

  const FAV_KEY='kripto_focus_favs';
  const PREF_KEY='kripto_opportunity_preferences_v210';
  const FILTERS=new Set(['all','score80','up','down','active','volume']);
  const SORTS=new Set(['default','score','change','volume']);
  const search=document.getElementById('oppFilterSearch');
  const sort=document.getElementById('oppSort');
  const bar=document.getElementById('oppFilterBar');
  let decorateTimer=null;

  const normalize=value=>{let s=String(value||'').toUpperCase().replace(/[^A-Z0-9]/g,'');if(s&&!s.endsWith('USDT'))s+='USDT';return s;};
  const valid=s=>/^[A-Z0-9]{2,15}USDT$/.test(s);

  function favorites(){
    try{
      const raw=JSON.parse(localStorage.getItem(FAV_KEY)||'[]');
      return Array.isArray(raw)?[...new Set(raw.map(normalize).filter(valid))].slice(0,12):[];
    }catch{return [];}
  }
  function saveFavorites(list){
    try{localStorage.setItem(FAV_KEY,JSON.stringify([...new Set(list.map(normalize).filter(valid))].slice(0,12)));}catch{}
  }
  function isFavorite(symbol){return favorites().includes(normalize(symbol));}
  function syncFavoriteButtons(){
    const fav=new Set(favorites());
    page.querySelectorAll('.opp-card[data-focus-symbol]').forEach(card=>{
      const symbol=normalize(card.dataset.focusSymbol);
      if(!valid(symbol))return;
      let button=card.querySelector('[data-opp-fav]');
      if(!button){
        button=document.createElement('button');
        button.type='button';
        button.className='opp-fav-btn';
        button.dataset.oppFav=symbol;
        card.appendChild(button);
      }
      const active=fav.has(symbol);
      button.classList.toggle('active',active);
      button.textContent=active?'★':'☆';
      button.title=active?'İzleme Listesi’nden çıkar':'İzleme Listesi’ne ekle';
      button.setAttribute('aria-label',button.title);
    });
  }
  function toggleFavorite(symbol){
    symbol=normalize(symbol);if(!valid(symbol))return;
    const list=favorites();const index=list.indexOf(symbol);let added=false;
    if(index>=0)list.splice(index,1);else{list.unshift(symbol);added=true;}
    saveFavorites(list);syncFavoriteButtons();
    window.dispatchEvent(new CustomEvent('kripto:favorites-changed',{detail:{symbol,added}}));
  }

  function currentFilter(){
    const active=page.querySelector('.opp-filter-chip.active');
    const key=String(active?.dataset?.filter||'all');
    return FILTERS.has(key)?key:'all';
  }
  function readPrefs(){
    try{
      const raw=JSON.parse(localStorage.getItem(PREF_KEY)||'{}');
      return {
        filter:FILTERS.has(String(raw.filter||''))?String(raw.filter):'all',
        search:String(raw.search||'').slice(0,16),
        sort:SORTS.has(String(raw.sort||''))?String(raw.sort):'default',
      };
    }catch{return{filter:'all',search:'',sort:'default'};}
  }
  function writePrefs(){
    if(!search||!sort)return;
    try{localStorage.setItem(PREF_KEY,JSON.stringify({filter:currentFilter(),search:search.value.slice(0,16),sort:sort.value}));}catch{}
  }
  function restorePrefs(){
    if(!search||!sort)return;
    const prefs=readPrefs();
    search.value=prefs.search;
    sort.value=SORTS.has(prefs.sort)?prefs.sort:'default';
    const button=page.querySelector(`.opp-filter-chip[data-filter="${prefs.filter}"]`)||page.querySelector('.opp-filter-chip[data-filter="all"]');
    if(button)button.click();
    else{
      search.dispatchEvent(new Event('input',{bubbles:true}));
      sort.dispatchEvent(new Event('change',{bubbles:true}));
    }
  }
  function resetPrefs(){
    try{localStorage.removeItem(PREF_KEY);}catch{}
    if(search)search.value='';
    if(sort)sort.value='default';
    const all=page.querySelector('.opp-filter-chip[data-filter="all"]');
    if(all)all.click();
  }
  function addResetButton(){
    if(!bar||document.getElementById('oppPrefsReset'))return;
    const button=document.createElement('button');
    button.type='button';button.id='oppPrefsReset';button.className='opp-pref-reset';button.textContent='Filtreyi sıfırla';button.title='Kaydedilen fırsat filtrelerini temizle';
    const status=document.getElementById('oppFilterStatus');
    if(status)bar.insertBefore(button,status);else bar.appendChild(button);
    button.addEventListener('click',resetPrefs);
  }

  page.addEventListener('click',event=>{
    const fav=event.target.closest('[data-opp-fav]');
    if(fav){event.preventDefault();event.stopPropagation();toggleFavorite(fav.dataset.oppFav);return;}
    const chip=event.target.closest('.opp-filter-chip');
    if(chip)setTimeout(writePrefs,0);
  },true);
  search?.addEventListener('input',writePrefs);
  sort?.addEventListener('change',writePrefs);

  const observer=new MutationObserver(()=>{
    clearTimeout(decorateTimer);
    decorateTimer=setTimeout(syncFavoriteButtons,60);
  });
  observer.observe(page,{childList:true,subtree:true});

  addResetButton();
  syncFavoriteButtons();
  requestAnimationFrame(()=>setTimeout(()=>{restorePrefs();syncFavoriteButtons();},80));
})();
</script>
'''.replace("__NONCE__", nonce_attr)
    return body.replace("</body>", script + "\n</body>", 1)


def make_v210_handler(
    config: PanelConfig,
    service,
    sessions,
    limiter: LoginRateLimiter,
    store,
    market_client=None,
    overview_client=None,
):
    BaseHandler = route.make_v29_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
        overview_client,
    )

    class V210Handler(BaseHandler):
        server_version = "KriptoPanel/2.10"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, memory_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            return super().do_GET()

    return V210Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.10 favori ve filtre hafızası.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = product.account_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v210_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} favorites=on filter_memory=on signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
