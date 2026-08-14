"""Kripto Kontrol Merkezi V2.8 - Akıllı Fırsat Filtreleri.

V2.7 teknik inceleme skorunu korur ve yalnız panel tarafında Fırsat Merkezi'ne:
- coin arama,
- 80+ teknik uyum,
- teknik YUKARI / AŞAĞI yönü,
- aktif sistem sinyali,
- 1.5x+ 15m hacim oranı filtreleri,
- skor / 24s değişim / hacim oranına göre sıralama
katmanını ekler.

Filtreler yeni işlem sinyali üretmez, stratejiyi değiştirmez ve emir açmaz.
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
import dashboard_score_app as score
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_8_FILTER_SORT_2026_08_14"

FILTER_KEYS = ("all", "score80", "up", "down", "active", "volume")
SORT_KEYS = ("default", "score", "change", "volume")


def normalize_filter_key(value: Any) -> str:
    key = str(value or "all").strip().lower()
    return key if key in FILTER_KEYS else "all"


def normalize_sort_key(value: Any) -> str:
    key = str(value or "default").strip().lower()
    return key if key in SORT_KEYS else "default"


def filter_requires_score(value: Any) -> bool:
    return normalize_filter_key(value) in {"score80", "up", "down", "volume"}


def sort_requires_score(value: Any) -> bool:
    return normalize_sort_key(value) in {"score", "volume"}


def filter_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = score.score_dashboard_page(session, nonce)
    nonce_attr = str(nonce).replace('"', "&quot;")

    css = r'''
    /* V2.8: Fırsat Merkezi hızlı filtre / sıralama. */
    .opp-filterbar{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin:0 0 11px;padding:9px;border:1px solid rgba(96,165,250,.15);background:rgba(7,20,28,.78);border-radius:13px}.opp-filter-search{min-width:150px;max-width:210px;flex:1 1 160px;background:#08161e;border:1px solid var(--line);color:var(--text);border-radius:9px;padding:8px 10px;font:inherit;font-size:9px;outline:none}.opp-filter-search:focus{border-color:rgba(96,165,250,.46);box-shadow:0 0 0 2px rgba(96,165,250,.06)}.opp-filter-chips{display:flex;gap:5px;flex-wrap:wrap}.opp-filter-chip{border:1px solid var(--line);background:#0b1921;color:#78918e;border-radius:999px;padding:6px 9px;font-size:8px;font-weight:900;cursor:pointer}.opp-filter-chip:hover{color:#c1d6d3;border-color:#31515d}.opp-filter-chip.active{background:rgba(96,165,250,.09);color:#8fc4ff;border-color:rgba(96,165,250,.34)}.opp-filter-chip[data-filter="score80"].active{color:var(--green);border-color:rgba(66,226,140,.32);background:rgba(66,226,140,.06)}.opp-filter-chip[data-filter="down"].active{color:var(--red);border-color:rgba(255,98,125,.30);background:rgba(255,98,125,.05)}.opp-filter-chip[data-filter="volume"].active{color:var(--amber);border-color:rgba(255,189,89,.30);background:rgba(255,189,89,.05)}.opp-sort{background:#08161e;border:1px solid var(--line);color:#9ab2af;border-radius:9px;padding:7px 9px;font:inherit;font-size:8px;outline:none}.opp-filter-status{margin-left:auto;color:#69827f;font-size:8px;white-space:nowrap}.opp-card.v28-hidden{display:none!important}.opp-group.v28-hidden{display:none!important}.opp-filter-loading{opacity:.68}.opp-filter-hint{width:100%;color:#58726f;font-size:8px;padding:1px 2px 0}.opp-filter-empty{display:none;padding:28px 12px;text-align:center;border:1px dashed var(--line);border-radius:12px;color:#6e8885;font-size:9px;margin:0 0 10px}.opp-filter-empty.show{display:block}
    @media(max-width:760px){.opp-filterbar{align-items:stretch}.opp-filter-search{max-width:none;flex-basis:100%}.opp-filter-chips{width:100%}.opp-sort{flex:1}.opp-filter-status{width:100%;margin-left:0}.opp-filter-chip{flex:1 0 auto;text-align:center}}
    '''
    body = body.replace("  </style>", css + "\n  </style>", 1)

    legend_anchor = '<div class="score-legend">'
    if legend_anchor not in body:
        raise RuntimeError("V2.7 inceleme skoru açıklaması bulunamadı.")

    toolbar = r'''
        <div class="opp-filterbar" id="oppFilterBar">
          <input class="opp-filter-search" id="oppFilterSearch" type="search" maxlength="16" autocomplete="off" placeholder="Coin ara: BTC, ETH, SOL…">
          <div class="opp-filter-chips" role="group" aria-label="Fırsat filtreleri">
            <button class="opp-filter-chip active" data-filter="all" type="button">Tümü</button>
            <button class="opp-filter-chip" data-filter="score80" type="button">80+ skor</button>
            <button class="opp-filter-chip" data-filter="up" type="button">Teknik ↑</button>
            <button class="opp-filter-chip" data-filter="down" type="button">Teknik ↓</button>
            <button class="opp-filter-chip" data-filter="active" type="button">Aktif sinyal</button>
            <button class="opp-filter-chip" data-filter="volume" type="button">Hacim 1.5x+</button>
          </div>
          <select class="opp-sort" id="oppSort" aria-label="Fırsat sıralaması">
            <option value="default">Grup sırası</option>
            <option value="score">Skor yüksek</option>
            <option value="change">24s hareket gücü</option>
            <option value="volume">Hacim oranı</option>
          </select>
          <span class="opp-filter-status" id="oppFilterStatus">Filtre hazır</span>
          <div class="opp-filter-hint">Skor ve hacim filtreleri yalnız teknik inceleme verisidir; işlem sinyali veya başarı olasılığı değildir.</div>
        </div>
        <div class="opp-filter-empty" id="oppFilterEmpty">Bu filtreye uyan coin bulunamadı. Filtreyi değiştir veya aramayı temizle.</div>
'''
    body = body.replace(legend_anchor, toolbar + "\n        " + legend_anchor, 1)

    script = r'''
<script nonce="__NONCE__">
(() => {
  const page=document.getElementById('page-opportunities');
  const search=document.getElementById('oppFilterSearch');
  const sort=document.getElementById('oppSort');
  const status=document.getElementById('oppFilterStatus');
  const empty=document.getElementById('oppFilterEmpty');
  const bar=document.getElementById('oppFilterBar');
  if(!page||!search||!sort||!status||!bar)return;

  let filter='all',busy=false,decorateTimer=null,observer=null;
  const scoreCache=new Map(),pending=new Map();
  const observeOptions={childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['class','title']};

  const cards=()=>[...page.querySelectorAll('.opp-list .opp-card[data-focus-symbol]')];
  const symbolOf=card=>String(card?.dataset?.focusSymbol||'').toUpperCase();
  const number=v=>{const n=Number(v);return Number.isFinite(n)?n:null;};
  const needsScore=()=>['score80','up','down','volume'].includes(filter)||['score','volume'].includes(sort.value);
  const scoreClass=value=>value>=80?'high':value>=65?'mid':'low';

  function parseChange(card){
    const raw=String(card.querySelector('.opp-change')?.textContent||'').replace('%','').replace('+','').replace(',','.').trim();
    return number(raw)??0;
  }
  function active(card){return Boolean(card.querySelector('.opp-signal'));}

  function storeScore(symbol,payload){
    scoreCache.set(symbol,payload);
    for(const card of cards().filter(row=>symbolOf(row)===symbol)){
      const value=number(payload?.score);
      card.dataset.v28Score=value===null?'':String(value);
      card.dataset.v28Direction=String(payload?.direction||'').toUpperCase();
      const ratio=number(payload?.metrics?.volume_ratio_15m);
      card.dataset.v28Volume=ratio===null?'':String(ratio);
      const chip=card.querySelector('.score-chip');
      if(chip&&value!==null){chip.className=`score-chip ${scoreClass(value)}`;chip.textContent=`${value} · ${payload?.direction||'—'}`;}
    }
  }

  async function fetchScore(symbol){
    if(scoreCache.has(symbol))return scoreCache.get(symbol);
    if(pending.has(symbol))return pending.get(symbol);
    const task=(async()=>{
      const r=await fetch(`/api/market/analysis-score?symbol=${encodeURIComponent(symbol)}`,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});
      if(r.status===401){location.assign('/login');throw new Error('Oturum gerekli');}
      const payload=await r.json();if(!r.ok)throw new Error(payload.message||payload.error||`HTTP ${r.status}`);
      storeScore(symbol,payload);return payload;
    })().finally(()=>pending.delete(symbol));
    pending.set(symbol,task);return task;
  }

  async function ensureScores(){
    const symbols=[...new Set(cards().map(symbolOf).filter(Boolean))].slice(0,20);
    const queue=symbols.filter(symbol=>!scoreCache.has(symbol));
    if(!queue.length)return;
    busy=true;bar.classList.add('opp-filter-loading');status.textContent=`Teknik veri: 0/${queue.length}`;
    let done=0;
    async function worker(){while(queue.length){const symbol=queue.shift();if(!symbol)continue;try{await fetchScore(symbol);}catch{}done++;status.textContent=`Teknik veri: ${done}/${done+queue.length}`;apply(false);}}
    await Promise.all(Array.from({length:Math.min(2,queue.length||1)},worker));
    busy=false;bar.classList.remove('opp-filter-loading');
  }

  function scoreOf(card){return number(card.dataset.v28Score)??-1;}
  function volumeOf(card){return number(card.dataset.v28Volume)??-1;}
  function directionOf(card){return String(card.dataset.v28Direction||'').toUpperCase();}

  function matches(card){
    const query=search.value.trim().toUpperCase().replace(/[^A-Z0-9]/g,'');
    const symbol=symbolOf(card);
    if(query&&!symbol.includes(query))return false;
    if(filter==='score80')return scoreOf(card)>=80;
    if(filter==='up')return directionOf(card)==='YUKARI';
    if(filter==='down')return directionOf(card)==='AŞAĞI';
    if(filter==='active')return active(card);
    if(filter==='volume')return volumeOf(card)>=1.5;
    return true;
  }

  function sortList(list){
    const rows=[...list.querySelectorAll('.opp-card[data-focus-symbol]')];
    rows.forEach((card,index)=>{if(!card.dataset.v28Origin)card.dataset.v28Origin=String(index+1);});
    const mode=sort.value;
    rows.sort((a,b)=>{
      if(mode==='score')return scoreOf(b)-scoreOf(a);
      if(mode==='change')return Math.abs(parseChange(b))-Math.abs(parseChange(a));
      if(mode==='volume')return volumeOf(b)-volumeOf(a);
      return Number(a.dataset.v28Origin||0)-Number(b.dataset.v28Origin||0);
    });
    rows.forEach(card=>list.appendChild(card));
  }

  function apply(updateStatus=true){
    if(observer)observer.disconnect();
    try{
      const all=cards();
      const visibleSymbols=new Set(),allSymbols=new Set();
      for(const list of page.querySelectorAll('.opp-list'))sortList(list);
      for(const card of all){
        const symbol=symbolOf(card);if(symbol)allSymbols.add(symbol);
        const show=matches(card);card.classList.toggle('v28-hidden',!show);if(show&&symbol)visibleSymbols.add(symbol);
      }
      for(const group of page.querySelectorAll('.opp-group')){
        const any=[...group.querySelectorAll('.opp-card[data-focus-symbol]')].some(card=>!card.classList.contains('v28-hidden'));
        group.classList.toggle('v28-hidden',!any);
      }
      empty?.classList.toggle('show',visibleSymbols.size===0&&allSymbols.size>0);
      if(updateStatus&&!busy)status.textContent=`${visibleSymbols.size}/${allSymbols.size} coin gösteriliyor`;
    }finally{
      if(observer)observer.observe(page,observeOptions);
    }
  }

  async function activateFilter(key,button){
    filter=key;
    page.querySelectorAll('.opp-filter-chip').forEach(node=>node.classList.toggle('active',node===button));
    if(needsScore())await ensureScores();
    apply();
  }

  page.querySelectorAll('.opp-filter-chip').forEach(button=>button.addEventListener('click',()=>activateFilter(button.dataset.filter||'all',button)));
  search.addEventListener('input',()=>apply());
  sort.addEventListener('change',async()=>{if(needsScore())await ensureScores();apply();});

  function absorbExistingScores(){
    for(const card of cards()){
      const chip=card.querySelector('.score-chip');
      if(!chip)continue;
      const text=String(chip.textContent||'').trim();
      const match=text.match(/^(\d+)\s*·\s*(.+)$/);
      if(!match)continue;
      const title=String(chip.title||'');
      const volume=title.match(/Hacim\s+([0-9.]+)x/i);
      card.dataset.v28Score=match[1];card.dataset.v28Direction=match[2].trim().toUpperCase();
      if(volume)card.dataset.v28Volume=volume[1];
    }
  }

  function decorate(){
    absorbExistingScores();
    for(const list of page.querySelectorAll('.opp-list')){
      [...list.querySelectorAll('.opp-card[data-focus-symbol]')].forEach((card,index)=>{if(!card.dataset.v28Origin)card.dataset.v28Origin=String(index+1);});
    }
    apply();
  }

  observer=new MutationObserver(()=>{clearTimeout(decorateTimer);decorateTimer=setTimeout(decorate,70);});
  observer.observe(page,observeOptions);
  document.addEventListener('click',event=>{if(event.target.closest('[data-view="opportunities"]'))setTimeout(decorate,120);});
  decorate();
})();
</script>
'''.replace("__NONCE__", nonce_attr)
    return body.replace("</body>", script + "\n</body>", 1)


def make_v28_handler(
    config: PanelConfig,
    service,
    sessions,
    limiter: LoginRateLimiter,
    store,
    market_client=None,
    overview_client=None,
):
    BaseHandler = score.make_v27_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
        overview_client,
    )

    class V28Handler(BaseHandler):
        server_version = "KriptoPanel/2.8"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, filter_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            return super().do_GET()

    return V28Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.8 akıllı fırsat filtreleri.")
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
    handler = make_v28_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
        overview_client,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} filters=on score=on signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
