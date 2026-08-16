"""Kripto Kontrol Merkezi V3.29 - Sinyal -> İşlem -> Sonuç ürün akışı.

V3.28 genel bilgi hiyerarşisini korur. Bu katman yalnız ana ürün akışını
sadeleştirir ve üç ekranın görevini birbirinden ayırır:
- Sinyaller: coin, yön, giriş ve inceleme önceliklidir; hedef ayrıntısı isteğe bağlıdır.
- İşlemler: giriş, TP1 ve SL ilk bakışta; TP2/TP3 isteğe bağlıdır.
- Sonuçlar: mevcut kompakt kayıtlar korunur, filtrelenen sonuç özeti eklenir.
- Mobil ana menüyü büyütmeden üç adım arasında ekran içi geçiş sağlanır.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger yazımı ve
otomatik filtre davranışı değiştirilmez.
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
import dashboard_commercial_app as commercial
import dashboard_earlyperformance_app as earlyperf
import dashboard_market_app as market
import dashboard_sitewideux_app as sitewide
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_29_FLOW_UX_2026_08_16"

CSS = r'''
/* V3.29 - Sinyal > İşlem > Sonuç görev ayrımı */
.v329-flow{display:flex;align-items:center;gap:6px;margin:-5px 0 14px;padding:7px;border:1px solid #1b3943;border-radius:12px;background:#07151c;overflow:auto}
.v329-step{border:1px solid transparent;background:transparent;color:#718d89;border-radius:9px;padding:7px 9px;display:flex;align-items:center;gap:6px;white-space:nowrap;font-size:9px;font-weight:850;cursor:pointer}
.v329-step i{width:19px;height:19px;border:1px solid #29444c;border-radius:7px;display:grid;place-items:center;font-style:normal;font-size:8px;color:#8ea9a5}.v329-step:hover{background:#0b1b23;color:#c8dad7}.v329-step.active{border-color:rgba(44,230,191,.25);background:rgba(44,230,191,.07);color:#2ce6bf}.v329-step.active i{border-color:rgba(44,230,191,.38);color:#2ce6bf}.v329-flow-note{margin-left:auto;color:#607d79;font-size:8px;white-space:nowrap;padding:0 5px}
.v329-results-summary{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin:-3px 0 10px}.v329-summary-pill{border:1px solid #29444c;border-radius:999px;background:#07151c;color:#88a29e;padding:5px 8px;font-size:8px;font-weight:850}.v329-summary-pill.tp{color:#42e28c;border-color:rgba(66,226,140,.25)}.v329-summary-pill.sl{color:#ff7189;border-color:rgba(255,113,137,.25)}.v329-summary-pill.be{color:#ffbd59;border-color:rgba(255,189,89,.25)}
#page-signals .v328-card-toggle{display:inline-flex!important;align-items:center;justify-content:center}
#page-signals .v329-signal-entry{display:block!important}
#page-signals .v329-signal-level{display:none!important}
#page-signals .row-card.v328-expanded .v329-signal-level{display:block!important}
#page-signals .row-card{grid-template-columns:minmax(150px,1.2fr) .65fr .75fr auto auto}
#page-trades .wide-card .levels{display:grid!important;grid-template-columns:repeat(3,1fr)}
#page-trades .wide-card .v329-trade-extra{display:none!important}
#page-trades .wide-card.v328-level-open .v329-trade-extra{display:block!important}
#page-trades .wide-card.v328-level-open .levels{grid-template-columns:repeat(5,1fr)}
#page-trades .v328-level-toggle{display:inline-flex!important;align-items:center;justify-content:center}
.v329-coin-link{border:1px solid #29444c;background:#081820;color:#9db5b1;border-radius:8px;padding:6px 8px;font-size:8px;font-weight:900;text-decoration:none;white-space:nowrap}.v329-coin-link:hover{border-color:#2ce6bf;color:#2ce6bf}
@media(max-width:900px){#page-signals .row-card{grid-template-columns:minmax(135px,1fr) .65fr .75fr auto}.v329-flow-note{display:none}}
@media(max-width:760px){
 .v329-flow{margin-top:-2px;padding:6px;scrollbar-width:none}.v329-flow::-webkit-scrollbar{display:none}.v329-step{flex:1 0 auto;justify-content:center;min-height:38px}.v329-step b{font-size:8px}
 #page-signals .row-card{grid-template-columns:minmax(0,1fr) auto auto!important}
 #page-signals .v329-signal-entry{display:block!important;grid-column:1/2}
 #page-signals .row-card.v328-expanded .v329-signal-level{display:flex!important;grid-column:1/-1;justify-content:space-between;border-top:1px solid rgba(29,48,59,.65);padding-top:6px}
 #page-trades .wide-card .levels{display:grid!important;grid-template-columns:repeat(3,1fr)!important}
 #page-trades .wide-card.v328-level-open .levels{grid-template-columns:repeat(2,1fr)!important}
 .v329-coin-link{min-height:32px;display:inline-flex;align-items:center}
}
@media(max-width:430px){#page-trades .wide-card .levels{grid-template-columns:repeat(3,minmax(0,1fr))!important}.v329-step{padding:6px 7px}.v329-step i{display:none}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v329-flow-script">
(()=>{'use strict';if(window.__v329Flow)return;window.__v329Flow=true;
const PREMIUM=__PREMIUM__;
const $=id=>document.getElementById(id);
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
function switchView(view){const target=[...document.querySelectorAll('[data-view]')].find(el=>el.dataset.view===view);if(target)target.click()}
function flow(pageId,current){const page=$(pageId);if(!page||page.querySelector('.v329-flow'))return;const head=page.querySelector('.page-head');if(!head)return;const wrap=document.createElement('div');wrap.className='v329-flow';wrap.setAttribute('aria-label','Sinyal işlem sonuç akışı');const steps=[['signals','1','Sinyaller'],['trades','2','İşlem Takibi'],['results','3','Sonuçlar']];wrap.innerHTML=steps.map(([view,n,label])=>`<button type="button" class="v329-step ${view===current?'active':''}" data-v329-view="${view}" ${view===current?'aria-current="step"':''}><i>${n}</i><b>${label}</b></button>`).join('')+'<span class="v329-flow-note">Aynı kaydı farklı amaçla gösterir</span>';head.insertAdjacentElement('afterend',wrap)}
function pageCopy(){const specs=[['page-signals','Canlı Sinyaller','Coin, yön ve girişe odaklan. Hedef ayrıntılarını yalnız gerektiğinde aç.'],['page-trades','İşlem Takibi','Giriş, TP1 ve SL ilk bakışta. TP2 / TP3 ayrıntıları isteğe bağlı.'],['page-results','Sonuçlar','Kapanan kayıtları TP, SL, BE ve R sonucu ile sade biçimde incele.']];for(const [id,title,copy] of specs){const page=$(id);if(!page)continue;const h=page.querySelector('.page-head h1'),p=page.querySelector('.page-head p');if(h)h.textContent=title;if(p)p.textContent=copy}}
function labelOf(block){return String(block?.querySelector('small')?.textContent||'').trim().toUpperCase()}
function decorateSignals(){const root=$('signalsList');if(!root)return;const apply=()=>root.querySelectorAll('.row-card').forEach(card=>{const blocks=[...card.querySelectorAll('.data-block')];for(const block of blocks){const label=labelOf(block);if(label.includes('GİRİŞ'))block.classList.add('v329-signal-entry');if(label.includes('TP1'))block.classList.add('v329-signal-level')}const link=card.querySelector('a.btn');if(link){link.textContent=PREMIUM?'İncele':'Grafik';if(PREMIUM){const symbol=(link.getAttribute('href')||'').match(/symbol=([^&]+)/)?.[1]||'';if(symbol)link.href=`/coin-center?symbol=${symbol}`}}});apply();new MutationObserver(apply).observe(root,{childList:true,subtree:true})}
function tradeLink(card){if(card.querySelector('.v329-coin-link'))return;const symbol=card.querySelector('.coin strong')?.textContent?.trim();const head=card.querySelector('.wide-top');if(!head||!symbol)return;const a=document.createElement('a');a.className='v329-coin-link';a.textContent=PREMIUM?'Coin Merkezi':'Piyasa';a.href=PREMIUM?`/coin-center?symbol=${encodeURIComponent(symbol)}`:`/market-center?symbol=${encodeURIComponent(symbol)}`;head.appendChild(a)}
function decorateTrades(){const root=$('tradesList');if(!root)return;const apply=()=>root.querySelectorAll('.wide-card').forEach(card=>{const levels=[...card.querySelectorAll('.level')];for(const level of levels){const label=labelOf(level);if(label==='TP2'||label==='TP3')level.classList.add('v329-trade-extra')}tradeLink(card);const btn=card.querySelector('.v328-level-toggle');if(btn){btn.textContent=card.classList.contains('v328-level-open')?'Az göster':'Tüm seviyeler';if(btn.dataset.v329Watch!=='1'){btn.dataset.v329Watch='1';new MutationObserver(()=>{btn.textContent=card.classList.contains('v328-level-open')?'Az göster':'Tüm seviyeler'}).observe(card,{attributes:true,attributeFilter:['class']})}}});apply();new MutationObserver(()=>setTimeout(apply,0)).observe(root,{childList:true,subtree:true})}
function resultSummary(){const page=$('page-results'),list=$('resultsList');if(!page||!list)return;let bar=$('v329ResultSummary');if(!bar){bar=document.createElement('div');bar.id='v329ResultSummary';bar.className='v329-results-summary';const toolbar=page.querySelector('.toolbar');(toolbar||page.querySelector('.page-head'))?.insertAdjacentElement('afterend',bar)}if(!bar)return;const rows=[...list.querySelectorAll('.result-item')];let tp=0,sl=0,be=0;for(const row of rows){const text=String(row.textContent||'').toUpperCase();if(/\bTP[123]?\b/.test(text)&&!text.includes('BE'))tp++;else if(/\bSL\b/.test(text))sl++;else if(text.includes('BE'))be++}bar.innerHTML=`<span class="v329-summary-pill">Filtrelenen ${rows.length}</span><span class="v329-summary-pill tp">TP ${tp}</span><span class="v329-summary-pill sl">SL ${sl}</span><span class="v329-summary-pill be">BE ${be}</span>`}
function decorateResults(){const list=$('resultsList');if(!list)return;resultSummary();new MutationObserver(()=>setTimeout(resultSummary,0)).observe(list,{childList:true,subtree:true});$('resultSearch')?.addEventListener('input',()=>setTimeout(resultSummary,0));$('resultOutcome')?.addEventListener('change',()=>setTimeout(resultSummary,0))}
function init(){document.body.classList.add('v329-flow-ui');pageCopy();flow('page-signals','signals');flow('page-trades','trades');flow('page-results','results');decorateSignals();decorateTrades();decorateResults()}
document.addEventListener('click',event=>{const b=event.target.closest('[data-v329-view]');if(!b)return;event.preventDefault();switchView(b.dataset.v329View)});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
</script>
'''


def enhance_flow_ui(body: str, nonce: str, *, premium_access: bool) -> str:
    """Mevcut gerçek SPA ekranlarını veri katmanına dokunmadan görev bazlı sadeleştirir."""
    if 'id="v329-flow-script"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True)).replace(
        "__PREMIUM__", "true" if premium_access else "false"
    )
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v329_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store,
    market_client=None,
    overview_client=None,
    history_cache: earlyperf.HistoricalPulseCache | None = None,
):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = sitewide.make_v328_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V329Handler(BaseHandler):
        server_version = "KriptoPanel/3.29"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path == "/":
                    session = self._session()
                    if session:
                        info = self._plan_info(session)
                        premium_access = str(info.get("plan") or "") != commercial.PLAN_FREE
                        body = enhance_flow_ui(body, str(nonce or ""), premium_access=premium_access)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "product_flow": "signal_trade_result",
                    "signal_primary": "coin_direction_entry",
                    "signal_levels": "on_demand",
                    "trade_primary_levels": "entry_tp1_sl",
                    "trade_extra_levels": "tp2_tp3_on_demand",
                    "results": "compact_with_filtered_summary",
                    "mobile_flow_without_nav_growth": True,
                    "sitewide_information_hierarchy": "preserved",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V329Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.29 Sinyal İşlem Sonuç UX")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = commercial.commercial_store_from_env(config)
    candle_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v329_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} product_flow=signal_trade_result signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
