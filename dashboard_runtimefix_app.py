"""Kripto Kontrol Merkezi V3.32.1 - klasik panel runtime onarımı.

V3.32 görünümünü ve üyelik sınırlarını korur. Premium/Admin klasik panelde eski
istemci scriptlerinden biri hata verse bile temel SPA navigasyonu ve /api/dashboard
veri yüklemesi bağımsız, küçük bir kurtarma scriptiyle çalışmaya devam eder.

FREE sayfasına bu script eklenmez; ticari katmandaki FREE/PREMIUM API sınırı aynen
korunur. Canlı sinyal, strateji, radar, Telegram, TP/SL/BE, state/ledger ve ödeme
backend yazımları değiştirilmez.
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
import dashboard_marketcoinux_app as v332
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_32_1_RUNTIME_REPAIR_2026_08_16"

CSS = r'''
/* V3.32.1 - kapalı eski çekmeceler hit-test dışı, ana UI tıklanabilir */
.focus-overlay:not(.open),.notify-overlay:not(.open){pointer-events:none!important;visibility:hidden!important}
.focus-drawer:not(.open),.notify-drawer:not(.open){pointer-events:none!important;visibility:hidden!important}
.mobile-nav,.sidebar,.topbar,main{pointer-events:auto}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v3321-runtime-repair-script">
(()=>{'use strict';if(window.__v3321RuntimeRepair)return;window.__v3321RuntimeRepair=true;
const $=id=>document.getElementById(id);if(!$('page-home'))return;
let DATA=null;
const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};
const price=v=>{const n=num(v);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{maximumFractionDigits:2});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{maximumFractionDigits:5});return n.toLocaleString('tr-TR',{maximumFractionDigits:9})};
const system=r=>String(r?.system_label||r?.system||r?.source||'Sistem');
const direction=r=>String(r?.direction||'').toUpperCase();
const outcome=r=>String(r?.outcome||r?.result||'').toUpperCase();
const isTp=o=>String(o||'').startsWith('TP')&&!String(o||'').includes('BE');
const initials=s=>String(s||'?').replace('USDT','').slice(0,5);
const tag=(text,kind='')=>`<span class="tag ${kind}">${esc(text||'—')}</span>`;
function switchView(view){
 if(view==='system'&&document.documentElement.dataset.admin!=='true')return;
 document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id===`page-${view}`));
 document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
 const top=$('topTitle');if(top){const titles={home:'Ana Sayfa',signals:'Sinyaller',trades:'İşlemler',results:'Sonuçlar',system:'Sistem'};top.textContent=titles[view]||'Kripto Kontrol'}
 try{window.scrollTo({top:0,behavior:'auto'})}catch{}
}
document.addEventListener('click',event=>{const el=event.target.closest?.('[data-view]');if(!el)return;event.preventDefault();event.stopImmediatePropagation();switchView(el.dataset.view)},true);
function rowCard(r){const d=direction(r),kind=d==='LONG'?'long':d==='SHORT'?'short':'';return `<div class="row-card"><div class="coin"><div class="coin-mark">${esc(initials(r.symbol))}</div><div><strong>${esc(r.symbol||'—')}</strong><small>${esc(system(r))}</small></div></div><div class="data-block"><small>Yön</small>${tag(d,kind)}</div><div class="data-block hide-mid"><small>Giriş</small><b>${price(r.entry)}</b></div><div class="data-block"><small>TP1</small><b>${price(r.tp1)}</b></div><a class="btn" href="/market-center?symbol=${encodeURIComponent(r.symbol||'')}">Grafik</a></div>`}
function tradeCard(r){const d=direction(r),kind=d==='LONG'?'long':d==='SHORT'?'short':'';return `<div class="wide-card"><div class="wide-top"><div class="coin"><div class="coin-mark">${esc(initials(r.symbol))}</div><div><strong>${esc(r.symbol||'—')}</strong><small>${esc(system(r))}</small></div></div>${tag(d,kind)}</div><div class="levels"><div class="level"><small>Giriş</small><b>${price(r.entry)}</b></div><div class="level"><small>TP1</small><b>${price(r.tp1)}</b></div><div class="level"><small>TP2</small><b>${price(r.tp2)}</b></div><div class="level"><small>TP3</small><b>${price(r.tp3)}</b></div><div class="level"><small>SL</small><b>${price(r.sl)}</b></div></div></div>`}
function resultCard(r){const o=outcome(r),kind=isTp(o)?'tp':o==='SL'?'sl':o.includes('BE')?'be':'';const rv=num(r.r_result);return `<div class="result-item"><div class="coin-mark">${esc(initials(r.symbol))}</div><div class="result-main"><strong>${esc(r.symbol||'—')} · ${esc(system(r))}</strong><div>${esc(direction(r)||'İşlem')}</div></div><div class="result-right">${tag(o||'KAPALI',kind)}<small>${rv===null?'':`${rv>=0?'+':''}${rv.toFixed(2)}R`}</small></div></div>`}
function renderHome(data){const open=Array.isArray(data.open_trades)?data.open_trades:[],results=Array.isArray(data.recent_results)?data.recent_results:[];const tp=results.filter(r=>isTp(outcome(r))).length,sl=results.filter(r=>outcome(r)==='SL').length;
 const metrics=$('homeSmartMetrics')||$('homeMetrics');if(metrics)metrics.innerHTML=`<div class="metric blue"><small>Açık işlem</small><strong>${open.length}</strong><em>Takipte</em></div><div class="metric green"><small>Son TP</small><strong>${tp}</strong><em>Sonuçlarda</em></div><div class="metric red"><small>Son SL</small><strong>${sl}</strong><em>Sonuçlarda</em></div><div class="metric"><small>Veri</small><strong>${data.data_quality?.ok===false?'Kontrol':'Canlı'}</strong><em>Panel kaydı</em></div>`;
 const strong=$('homeStrongSignals')||$('homeOpen');if(strong)strong.innerHTML=open.slice(0,5).map(rowCard).join('')||'<div class="empty">Şu anda açık sinyal yok.</div>';
 const recent=$('homeTodayFlow')||$('homeResults');if(recent)recent.innerHTML=results.slice(0,7).map(resultCard).join('')||'<div class="empty">Henüz sonuç kaydı yok.</div>';
}
function renderSignals(){if(!DATA)return;let rows=Array.isArray(DATA.open_trades)?DATA.open_trades:[];const q=String($('signalSearch')?.value||'').trim().toUpperCase(),dir=String($('signalDirection')?.value||''),sys=String($('signalSystem')?.value||'');rows=rows.filter(r=>(!q||String(r.symbol||'').toUpperCase().includes(q))&&(!dir||direction(r)===dir)&&(!sys||system(r)===sys));const root=$('signalsList');if(root)root.innerHTML=rows.map(rowCard).join('')||'<div class="empty panel">Filtreye uygun açık sinyal yok.</div>'}
function renderTrades(){const root=$('tradesList'),rows=Array.isArray(DATA?.open_trades)?DATA.open_trades:[];if(root)root.innerHTML=rows.map(tradeCard).join('')||'<div class="empty panel">Açık işlem yok.</div>'}
function renderResults(){if(!DATA)return;let rows=Array.isArray(DATA.recent_results)?DATA.recent_results:[];const q=String($('resultSearch')?.value||'').trim().toUpperCase(),filter=String($('resultOutcome')?.value||'');rows=rows.filter(r=>(!q||`${r.symbol||''} ${system(r)}`.toUpperCase().includes(q))&&(!filter||(filter==='TP'?isTp(outcome(r)):outcome(r).includes(filter))));const root=$('resultsList');if(root)root.innerHTML=rows.map(resultCard).join('')||'<div class="empty">Filtreye uygun sonuç yok.</div>'}
function fillSystems(){const select=$('signalSystem');if(!select)return;const current=select.value,open=Array.isArray(DATA?.open_trades)?DATA.open_trades:[],values=[...new Set(open.map(system).filter(Boolean))].sort();select.innerHTML='<option value="">Tüm sistemler</option>'+values.map(v=>`<option>${esc(v)}</option>`).join('');select.value=values.includes(current)?current:''}
function renderAll(data){DATA=data;window.__kriptoDashboardData=data;try{window.dispatchEvent(new CustomEvent('kripto-dashboard-data',{detail:data}))}catch{}renderHome(data);fillSystems();renderSignals();renderTrades();renderResults()}
async function loadData(force=false){const live=$('liveText');if(live)live.textContent='Güncelleniyor…';try{const r=await fetch('/api/dashboard'+(force?'?refresh=1':''),{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');return}if(r.status===403){location.assign('/premium');return}const p=await r.json();if(!r.ok)throw new Error(p.error||`HTTP ${r.status}`);renderAll(p);if(live)live.textContent=p.data_quality?.ok===false?'Son geçerli veri':'Canlı veri'}catch(err){if(live)live.textContent='Veri alınamadı';console.error('v3321 runtime repair',err)}}
for(const id of ['signalSearch','resultSearch'])$(id)?.addEventListener('input',()=>id==='signalSearch'?renderSignals():renderResults());
for(const id of ['signalDirection','signalSystem'])$(id)?.addEventListener('change',renderSignals);$('resultOutcome')?.addEventListener('change',renderResults);$('refreshBtn')?.addEventListener('click',()=>loadData(true));
loadData();setInterval(()=>loadData(false),30000);
})();
</script>
'''


def enhance_runtime_repair(body: str, nonce: str) -> str:
    if 'id="v3321-runtime-repair-script"' in body or 'id="page-home"' not in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    return body.replace("</body>", script + "\n</body>", 1) if "</body>" in body else body


def make_v3321_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None, history_cache: earlyperf.HistoricalPulseCache | None = None):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or earlyperf.HistoricalPulseCache()
    BaseHandler = v332.make_v332_handler(config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache)

    class V3321Handler(BaseHandler):
        server_version = "KriptoPanel/3.32.1"
        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html") and urllib.parse.urlsplit(self.path).path == "/":
                session = self._session()
                if session and self._is_premium(session):
                    body = enhance_runtime_repair(body, str(nonce or ""))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)
        def do_GET(self):
            if urllib.parse.urlsplit(self.path).path == "/healthz":
                self._json(HTTPStatus.OK,{"status":"ok","version":VERSION,"classic_runtime_repair":True,"free_runtime":"separate_preserved","premium_dashboard_api":"preserved","signal_engine":"unchanged","telegram":"unchanged","trade_management":"unchanged","ledger_write":"unchanged"});return
            return super().do_GET()
    return V3321Handler


def main() -> None:
    parser=argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.32.1 runtime repair")
    parser.add_argument("--host",default=os.getenv("HOST","127.0.0.1"));parser.add_argument("--port",type=int,default=int(os.getenv("PORT","8080")));parser.add_argument("--root",default=".");args=parser.parse_args()
    config=PanelConfig.from_env(Path(args.root));config.validate();service=build_service(config);sessions=accounts.ManagedSessionStore(config.session_hours*3600);limiter=LoginRateLimiter();store=commercial.commercial_store_from_env(config);candle_client=chartfix.ResilientMarketDataClient(cache_seconds=2);overview_client=market.OKXMarketOverviewClient(cache_seconds=20);handler=make_v3321_handler(config,service,sessions,limiter,store,candle_client,overview_client);server=ThreadingHTTPServer((args.host,args.port),handler);print(f"{VERSION} http://{args.host}:{args.port} classic_runtime_repair=1 signal_engine=unchanged")
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=="__main__":main()
