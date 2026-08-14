"""Kripto Kontrol Merkezi V2.3 - tarayıcı tabanlı bildirim merkezi.

V2.2 akıllı ana sayfayı korur; yalnız mevcut panel verilerinden bildirim üretir:
- Yeni açık sinyal bildirimi
- Yeni TP / SL / BE sonuç bildirimi
- Okunmuş / okunmamış takibi
- Coin bildiriminden hızlı analiz açma

Bildirim durumu yalnız kullanıcının tarayıcı localStorage alanında tutulur.
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

import dashboard_home_app as home
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_3_NOTIFY_2026_08_14"


def notification_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = home.home_dashboard_page(session, nonce)
    nonce_attr = html.escape(nonce, quote=True)

    extra_css = r'''
    .notify-trigger{position:relative;width:35px;height:35px;border:1px solid var(--line);background:#0b1720;border-radius:10px;color:#9eb5b2;display:grid;place-items:center;font-size:15px}.notify-trigger:hover{border-color:rgba(44,230,191,.5);color:var(--teal)}.notify-badge{position:absolute;right:-5px;top:-5px;min-width:17px;height:17px;padding:0 4px;border-radius:999px;background:var(--red);color:white;border:2px solid var(--bg);display:none;place-items:center;font-size:8px;font-weight:950}.notify-badge.show{display:grid}
    .notify-overlay{position:fixed;inset:0;background:rgba(1,8,12,.58);backdrop-filter:blur(3px);z-index:88;opacity:0;pointer-events:none;transition:opacity .2s}.notify-overlay.open{opacity:1;pointer-events:auto}.notify-drawer{position:fixed;top:0;right:0;bottom:0;width:min(430px,94vw);background:#08131b;border-left:1px solid var(--line);z-index:89;transform:translateX(102%);transition:transform .22s ease;display:flex;flex-direction:column;box-shadow:-25px 0 70px rgba(0,0,0,.32)}.notify-drawer.open{transform:translateX(0)}
    .notify-head{padding:17px 16px 12px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:9px}.notify-head-main{flex:1}.notify-head-main strong{display:block;font-size:16px}.notify-head-main small{color:var(--muted);font-size:9px}.notify-close{width:33px;height:33px;border:1px solid var(--line);background:#0b1b24;color:#9db6b3;border-radius:9px;font-size:16px}.notify-tools{padding:9px 12px;border-bottom:1px solid var(--line);display:flex;gap:6px;align-items:center;overflow:auto}.notify-filter{border:1px solid var(--line);background:#091720;color:#799591;border-radius:999px;padding:6px 8px;font-size:9px;font-weight:850;white-space:nowrap}.notify-filter.active{border-color:rgba(44,230,191,.42);background:rgba(44,230,191,.08);color:var(--teal)}.notify-read-all{margin-left:auto;border:0;background:transparent;color:var(--teal);font-size:9px;font-weight:850;white-space:nowrap}
    .notify-list{padding:9px 10px 28px;overflow:auto;flex:1}.notify-item{position:relative;border:1px solid var(--line);background:#091720;border-radius:11px;padding:11px 11px 10px;margin-bottom:7px;cursor:pointer}.notify-item:hover{border-color:#2a4856}.notify-item.unread{border-color:rgba(44,230,191,.32);background:rgba(44,230,191,.045)}.notify-item.unread:before{content:'';position:absolute;left:-4px;top:15px;width:7px;height:7px;border-radius:50%;background:var(--teal);box-shadow:0 0 9px rgba(44,230,191,.7)}.notify-top{display:flex;align-items:center;gap:8px}.notify-icon{width:31px;height:31px;display:grid;place-items:center;border:1px solid #1d3844;background:#0d2029;border-radius:9px;font-size:12px}.notify-main{flex:1;min-width:0}.notify-main strong{display:block;font-size:11px}.notify-main small{display:block;color:var(--muted);font-size:9px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.notify-time{color:#68827f;font-size:8px;white-space:nowrap}.notify-detail{display:flex;gap:6px;align-items:center;margin-top:7px;padding-left:39px}.notify-detail span{color:#708c88;font-size:9px}.notify-kind{border:1px solid var(--line);border-radius:999px;padding:3px 6px;font-size:8px;font-weight:900}.notify-kind.signal{color:var(--blue)}.notify-kind.tp{color:var(--green)}.notify-kind.sl{color:var(--red)}.notify-kind.be{color:var(--amber)}
    .notify-empty{padding:34px 16px;text-align:center;color:var(--muted);font-size:11px}.notify-foot{padding:10px 13px;border-top:1px solid var(--line);color:#607a77;font-size:8px;text-align:center}
    @media(max-width:760px){.notify-drawer{width:100vw;top:7vh;border-radius:18px 18px 0 0;border-left:0;border-top:1px solid var(--line)}.notify-trigger{width:33px;height:33px}.notify-detail{padding-left:0}}
    '''
    body = body.replace("  </style>", extra_css + "\n  </style>", 1)

    bell = '<button class="notify-trigger" id="notifyTrigger" type="button" aria-label="Bildirimler" title="Bildirimler">♢<span class="notify-badge" id="notifyBadge">0</span></button>'
    refresh_anchor = '<button class="icon-btn" id="refreshBtn" type="button">Yenile</button>'
    if refresh_anchor not in body:
        raise RuntimeError("V2.2 üst bar yenile düğmesi bulunamadı.")
    body = body.replace(refresh_anchor, bell + refresh_anchor, 1)

    drawer = r'''
<div class="notify-overlay" id="notifyOverlay"></div>
<aside class="notify-drawer" id="notifyDrawer" aria-hidden="true">
  <div class="notify-head">
    <div class="notify-head-main"><strong>Bildirim Merkezi</strong><small id="notifySubtitle">Yeni sinyal ve sonuçlar</small></div>
    <button class="notify-close" id="notifyClose" type="button" aria-label="Kapat">×</button>
  </div>
  <div class="notify-tools">
    <button class="notify-filter active" data-notify-filter="all" type="button">Tümü</button>
    <button class="notify-filter" data-notify-filter="unread" type="button">Okunmamış</button>
    <button class="notify-filter" data-notify-filter="signal" type="button">Sinyaller</button>
    <button class="notify-filter" data-notify-filter="result" type="button">Sonuçlar</button>
    <button class="notify-read-all" id="notifyReadAll" type="button">Tümünü okundu yap</button>
  </div>
  <div class="notify-list" id="notifyList"></div>
  <div class="notify-foot">Bildirim durumu yalnız bu tarayıcıda tutulur · Telegram sisteminden bağımsızdır</div>
</aside>
'''

    script = rf'''
<script nonce="{nonce_attr}">
(() => {{
  const $=id=>document.getElementById(id);
  const drawer=$('notifyDrawer'),overlay=$('notifyOverlay'),badge=$('notifyBadge'),listEl=$('notifyList');
  const STORE='kripto_notify_read_v23',INIT='kripto_notify_initialized_v23';
  const state={{items:[],filter:'all'}};
  const esc=v=>String(v??'').replace(/[&<>\"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[ch]));
  const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const system=r=>String(r?.system_label||r?.system||r?.source||'Sistem');
  const direction=r=>String(r?.direction||'').toUpperCase();
  const outcome=r=>String(r?.outcome||r?.result||'').toUpperCase();
  const num=v=>{{const n=Number(v);return Number.isFinite(n)?n:null;}};
  const parseTs=v=>{{if(v===null||v===undefined||v==='')return null;if(typeof v==='number'||/^\d+(\.\d+)?$/.test(String(v))){{let n=Number(v);if(!Number.isFinite(n))return null;if(n>1e12)n/=1000;return Math.round(n);}}const d=new Date(v);return Number.isNaN(d.getTime())?null:Math.round(d.getTime()/1000);}};
  const signalTs=r=>parseTs(r?.opened_at||r?.sent_at||r?.created_at||r?.detected_at||r?.updated_at)||0;
  const resultTs=r=>parseTs(r?.closed_at||r?.finalized_at||r?.updated_at||r?.opened_at||r?.sent_at)||0;
  const fmtTime=ts=>ts?new Date(ts*1000).toLocaleString('tr-TR',{{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}}):'—';
  const fmtPrice=v=>{{const n=num(v);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{{maximumFractionDigits:2}});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{{maximumFractionDigits:5}});return n.toLocaleString('tr-TR',{{maximumFractionDigits:9}});}};
  function readSet(){{try{{const raw=JSON.parse(localStorage.getItem(STORE)||'[]');return new Set(Array.isArray(raw)?raw:[]);}}catch{{return new Set();}}}}
  function saveRead(set){{try{{localStorage.setItem(STORE,JSON.stringify([...set].slice(-600)));}}catch{{}}}}
  function key(type,row,ts){{return [type,normalize(row?.symbol),system(row),direction(row)||outcome(row),ts].join('|');}}
  function buildItems(data){{
    const open=Array.isArray(data?.open_trades)?data.open_trades:[],results=Array.isArray(data?.recent_results)?data.recent_results:[],items=[];
    open.forEach(r=>{{const ts=signalTs(r);if(!normalize(r.symbol))return;items.push({{id:key('signal',r,ts),type:'signal',symbol:normalize(r.symbol),system:system(r),label:`${{direction(r)||'YENİ'}} sinyal`,detail:`Giriş ${{fmtPrice(r.entry)}} · TP1 ${{fmtPrice(r.tp1)}}`,ts,row:r}});}});
    results.forEach(r=>{{const ts=resultTs(r),o=outcome(r)||'KAPALI';if(!normalize(r.symbol))return;items.push({{id:key('result',r,ts),type:'result',symbol:normalize(r.symbol),system:system(r),label:`${{o}} sonucu`,detail:`${{direction(r)||'İşlem'}} · ${{o}}`,outcome:o,ts,row:r}});}});
    const cutoff=Math.round(Date.now()/1000)-7*86400;return items.filter(i=>!i.ts||i.ts>=cutoff).sort((a,b)=>b.ts-a.ts).slice(0,80);
  }}
  function kindClass(item){{if(item.type==='signal')return 'signal';const o=String(item.outcome||'').toUpperCase();if(o.startsWith('TP'))return 'tp';if(o==='SL')return 'sl';if(o.includes('BE'))return 'be';return 'result';}}
  function icon(item){{if(item.type==='signal')return '⚡';const c=kindClass(item);return c==='tp'?'✓':c==='sl'?'×':c==='be'?'↔':'•';}}
  function unreadItems(){{const read=readSet();return state.items.filter(i=>!read.has(i.id));}}
  function updateBadge(){{const n=unreadItems().length;badge.textContent=n>99?'99+':String(n);badge.classList.toggle('show',n>0);$('notifySubtitle').textContent=n?`${{n}} okunmamış bildirim`:'Yeni bildirim yok';}}
  function render(){{
    const read=readSet();let rows=state.items;if(state.filter==='unread')rows=rows.filter(i=>!read.has(i.id));else if(state.filter==='signal')rows=rows.filter(i=>i.type==='signal');else if(state.filter==='result')rows=rows.filter(i=>i.type==='result');
    listEl.innerHTML=rows.map(i=>{{const unread=!read.has(i.id),kind=kindClass(i);return `<div class="notify-item ${{unread?'unread':''}}" data-notify-id="${{esc(i.id)}}" data-focus-symbol="${{esc(i.symbol)}}"><div class="notify-top"><div class="notify-icon">${{icon(i)}}</div><div class="notify-main"><strong>${{esc(i.symbol)}} · ${{esc(i.label)}}</strong><small>${{esc(i.system)}} · ${{esc(i.detail)}}</small></div><span class="notify-time">${{fmtTime(i.ts)}}</span></div><div class="notify-detail"><span class="notify-kind ${{kind}}">${{i.type==='signal'?'SİNYAL':esc(i.outcome||'SONUÇ')}}</span><span>${{unread?'Yeni':'Okundu'}} · Coin analizini aç</span></div></div>`;}}).join('')||'<div class="notify-empty">Bu filtrede bildirim yok.</div>';
    updateBadge();
  }}
  function ingest(data){{
    state.items=buildItems(data);let read=readSet();
    try{{if(!localStorage.getItem(INIT)){{state.items.forEach(i=>read.add(i.id));saveRead(read);localStorage.setItem(INIT,String(Date.now()));}}}}catch{{}}
    render();
  }}
  function markRead(id){{const read=readSet();read.add(id);saveRead(read);render();}}
  function openDrawer(){{drawer.classList.add('open');overlay.classList.add('open');drawer.setAttribute('aria-hidden','false');render();}}
  function closeDrawer(){{drawer.classList.remove('open');overlay.classList.remove('open');drawer.setAttribute('aria-hidden','true');}}
  $('notifyTrigger').addEventListener('click',openDrawer);$('notifyClose').addEventListener('click',closeDrawer);overlay.addEventListener('click',closeDrawer);
  $('notifyReadAll').addEventListener('click',()=>{{const read=readSet();state.items.forEach(i=>read.add(i.id));saveRead(read);render();}});
  document.querySelectorAll('[data-notify-filter]').forEach(btn=>btn.addEventListener('click',()=>{{state.filter=btn.dataset.notifyFilter;document.querySelectorAll('[data-notify-filter]').forEach(x=>x.classList.toggle('active',x===btn));render();}}));
  listEl.addEventListener('click',event=>{{const item=event.target.closest('[data-notify-id]');if(!item)return;markRead(item.dataset.notifyId);closeDrawer();}});
  document.addEventListener('keydown',e=>{{if(e.key==='Escape'&&drawer.classList.contains('open'))closeDrawer();}});
  window.addEventListener('kripto-dashboard-data',event=>ingest(event.detail||{{}}));if(window.__kriptoDashboardData)ingest(window.__kriptoDashboardData);
}})();
</script>
'''
    return body.replace("</body>", drawer + "\n" + script + "\n</body>", 1)


def make_v23_handler(config: PanelConfig, service, sessions, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    BaseHandler = home.make_v22_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V23Handler(BaseHandler):
        server_version = "KriptoPanel/2.3"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, notification_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            return super().do_GET()

    return V23Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.3 bildirim merkezi.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = home.v21.focus.v2.v19.v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = home.v21.focus.v2.v19.v18.account_store_from_env(config)
    handler = make_v23_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        OKXMarketDataClient(),
        home.v21.market.OKXMarketOverviewClient(),
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} notifications=on smart_home=on focus_drawer=on")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
