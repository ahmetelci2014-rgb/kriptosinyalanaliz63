"""Kripto Kontrol Merkezi V3.4 - şeffaf performans ve FREE takip deneyimi.

V3.3 üzerine yalnız panel/ürün katmanında eklenir:
- girişsiz ana sayfada son 6 gerçek kapanış sonucu (coin/yön/sonuç; seviye yok),
- FREE kullanıcının gördüğü ücretsiz sinyali tarayıcıda takip etmesi,
- takip edilen sinyal kapanınca son sonuçlarda bulunursa TP/SL/BE sonucunun görünmesi,
- sonuçların kronolojik akıştan gelmesi; kazanan seçimi/cherry-pick yapılmaması.

Entry/TP/SL seviyeleri ziyaretçiye açılmaz. FREE sınırı V3.3 ile aynıdır:
1 açık sinyal için Entry + TP1 + SL; diğer sinyaller ve gelişmiş araçlar Premium kalır.
Sinyal üretimi, strateji, radar, Telegram ve emir akışına dokunmaz.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_commercial_app as commercial
import dashboard_freepreview_app as freepreview
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_4_TRANSPARENCY_2026_08_14"
PUBLIC_RESULT_LIMIT = 6
FREE_FOLLOW_STORAGE_KEY = "kripto_free_follow_v34"


def build_public_results(data: dict[str, Any], limit: int = PUBLIC_RESULT_LIMIT) -> dict[str, Any]:
    """Halka açık sayfa için seviyesiz ve sınırlı gerçek sonuç akışı üretir."""
    limit = max(1, min(int(limit), PUBLIC_RESULT_LIMIT))
    source = [
        row for row in (data.get("recent_results") if isinstance(data.get("recent_results"), list) else [])
        if isinstance(row, dict)
    ]
    source.sort(key=freepreview._row_timestamp, reverse=True)
    rows: list[dict[str, Any]] = []
    for row in source:
        symbol = freepreview._safe_symbol(row.get("symbol"))
        outcome = commercial._safe_text(commercial._result_outcome(row), 24)
        if not symbol or not outcome:
            continue
        rows.append({
            "symbol": symbol,
            "direction": freepreview._safe_direction(row.get("direction")),
            "outcome": outcome,
            "system": freepreview._system_label(row),
            "closed_at": freepreview._row_timestamp(row) or None,
        })
        if len(rows) >= limit:
            break
    return {
        "version": VERSION,
        "items": rows,
        "count": len(rows),
        "updated_at": int(time.time()),
        "fields": ["symbol", "direction", "outcome", "system", "closed_at"],
        "disclaimer": "Sonuçlar gerçek sistem kayıtlarının kronolojik akışıdır; kazanç garantisi değildir.",
    }


def enhance_public_home(body: str, nonce: str) -> str:
    """Mevcut açık vitrini bozmadan son gerçek sonuçlar bölümünü ekler."""
    section = r'''
<section class="section v34-results-section">
  <div class="v34-results-head"><div><h2>Son gerçek sonuçlar</h2><p>Yalnız kazananlar seçilmez. Sistemin en son kapanan kayıtları kronolojik olarak gösterilir.</p></div><a class="btn" href="/register">FREE hesap aç</a></div>
  <div class="v34-public-results" id="v34PublicResults"><div class="v34-result-empty">Sonuçlar yükleniyor…</div></div>
  <p class="v34-result-note">Ziyaretçiye işlem seviyeleri açılmaz. FREE hesapta 1 gerçek açık işlem; Premium'da bütün canlı sinyaller ve gelişmiş analizler bulunur.</p>
</section>
'''
    css = r'''
.v34-results-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start;flex-wrap:wrap}.v34-results-head p{color:var(--muted);margin:4px 0 0}.v34-public-results{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin-top:16px}.v34-result-card{border:1px solid var(--line);border-radius:13px;background:#091820;padding:13px}.v34-result-top{display:flex;justify-content:space-between;align-items:center;gap:8px}.v34-result-symbol{font-weight:950;font-size:15px}.v34-result-direction{font-size:8px;font-weight:950;color:#9bb3b0}.v34-result-outcome{font-size:10px;font-weight:950;margin-top:8px}.v34-result-outcome.tp{color:var(--green)}.v34-result-outcome.sl{color:var(--red)}.v34-result-outcome.be{color:var(--amber)}.v34-result-meta{display:block;color:#6f8986;font-size:9px;margin-top:4px}.v34-result-empty{grid-column:1/-1;border:1px dashed var(--line);border-radius:12px;padding:18px;text-align:center;color:var(--muted)}.v34-result-note{font-size:10px;color:#718b88;margin-top:11px}@media(max-width:760px){.v34-public-results{grid-template-columns:1fr 1fr}}@media(max-width:480px){.v34-public-results{grid-template-columns:1fr}}
'''
    if "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)
    marker = '<footer class="foot">'
    if marker in body:
        body = body.replace(marker, section + marker, 1)
    else:
        body = body.replace("</body>", section + "</body>", 1)

    nonce_attr = html.escape(nonce, quote=True)
    script = f'''<script nonce="{nonce_attr}">(()=>{{
const box=document.getElementById('v34PublicResults');if(!box)return;
const esc=v=>String(v??'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[c]));
const date=v=>{{const n=Number(v);return Number.isFinite(n)&&n>0?new Date(n*1000).toLocaleString('tr-TR',{{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}}):'—';}};
function cls(o){{o=String(o||'').toUpperCase();return o.startsWith('TP')?'tp':o==='SL'||o.startsWith('SL_')?'sl':o.includes('BE')?'be':'';}}
async function load(){{try{{const r=await fetch('/api/public/results',{{cache:'no-store'}}),d=await r.json();if(!r.ok)return;const rows=Array.isArray(d.items)?d.items:[];if(!rows.length){{box.innerHTML='<div class="v34-result-empty">Henüz gösterilecek kapanış sonucu yok.</div>';return;}}box.innerHTML=rows.map(x=>`<div class="v34-result-card"><div class="v34-result-top"><span class="v34-result-symbol">${{esc(x.symbol)}}</span><span class="v34-result-direction">${{esc(x.direction||'')}}</span></div><div class="v34-result-outcome ${{cls(x.outcome)}}">${{esc(x.outcome)}}</div><span class="v34-result-meta">${{esc(x.system||'Sistem')}} · ${{date(x.closed_at)}}</span></div>`).join('');}}catch{{}}}}
load();setInterval(load,45000);
}})();</script>'''
    return body.replace("</body>", script + "\n</body>", 1)


def enhance_free_page(body: str, nonce: str) -> str:
    """FREE sayfaya gördüğü sinyalin kapanış sonucunu hatırlayan tarayıcı katmanı ekler."""
    css = r'''
.v34-follow-result{display:none;margin-top:11px;border:1px solid rgba(44,230,191,.25);border-radius:13px;background:rgba(44,230,191,.045);padding:13px}.v34-follow-result.show{display:flex;justify-content:space-between;gap:10px;align-items:center}.v34-follow-result strong{display:block;font-size:13px}.v34-follow-result small{display:block;color:var(--muted);margin-top:2px}.v34-follow-outcome{font-weight:950;font-size:11px}.v34-follow-outcome.tp{color:var(--green)}.v34-follow-outcome.sl{color:var(--red)}.v34-follow-outcome.be{color:var(--amber)}@media(max-width:520px){.v34-follow-result.show{align-items:flex-start}.v34-follow-outcome{white-space:nowrap}}
'''
    if "</style>" in body:
        body = body.replace("</style>", css + "\n</style>", 1)
    follow_html = '<div class="v34-follow-result" id="v34FollowResult"><div><strong>Takip ettiğin FREE işlem sonuçlandı</strong><small id="v34FollowMeta"></small></div><span class="v34-follow-outcome" id="v34FollowOutcome"></span></div>'
    hero_end = "</section>\n<div class=\"grid\">"
    if hero_end in body:
        body = body.replace(hero_end, "</section>" + follow_html + "\n<div class=\"grid\">", 1)

    dispatch_from = "renderSignal(d);renderResults(d.recent_results);"
    dispatch_to = "renderSignal(d);renderResults(d.recent_results);window.dispatchEvent(new CustomEvent('kripto-free-preview',{detail:d}));"
    if dispatch_from in body:
        body = body.replace(dispatch_from, dispatch_to, 1)

    nonce_attr = html.escape(nonce, quote=True)
    listener = f'''<script nonce="{nonce_attr}">(()=>{{
const KEY='{FREE_FOLLOW_STORAGE_KEY}',box=document.getElementById('v34FollowResult'),meta=document.getElementById('v34FollowMeta'),out=document.getElementById('v34FollowOutcome');
if(!box||!meta||!out)return;
function read(){{try{{const x=JSON.parse(localStorage.getItem(KEY)||'{{}}');return x&&typeof x==='object'?x:{{}};}}catch{{return {{}};}}}}
function save(x){{try{{localStorage.setItem(KEY,JSON.stringify(x));}}catch{{}}}}
function sig(s){{if(!s)return null;return {{symbol:String(s.symbol||''),direction:String(s.direction||''),entry:Number(s.entry),opened_at:Number(s.opened_at||0)}};}}
function same(a,b){{return !!a&&!!b&&a.symbol===b.symbol&&a.direction===b.direction&&Number(a.entry)===Number(b.entry);}}
function kind(o){{o=String(o||'').toUpperCase();return o.startsWith('TP')?'tp':o==='SL'||o.startsWith('SL_')?'sl':o.includes('BE')?'be':'';}}
function show(last){{if(!last||Date.now()-Number(last.seen_at||0)>48*3600*1000){{box.classList.remove('show');return;}}box.classList.add('show');meta.textContent=`${{last.symbol}} ${{last.direction}} · sistemin kapanış kaydı`;out.textContent=last.outcome;out.className='v34-follow-outcome '+kind(last.outcome);}}
window.addEventListener('kripto-free-preview',e=>{{const d=e.detail||{{}},state=read(),current=sig(d.free_signal);if(state.current&&!same(state.current,current)&&!state.pending)state.pending=state.current;if(current)state.current=current;else if(state.current){{if(!state.pending)state.pending=state.current;state.current=null;}}if(state.pending&&Array.isArray(d.recent_results)){{const p=state.pending;const hit=d.recent_results.find(r=>String(r.symbol||'')===p.symbol&&String(r.direction||'')===p.direction&&(!p.opened_at||!Number(r.closed_at)||Number(r.closed_at)>=p.opened_at));if(hit){{state.last_result={{symbol:p.symbol,direction:p.direction,outcome:String(hit.outcome||''),closed_at:Number(hit.closed_at||0),seen_at:Date.now()}};state.pending=null;}}}}show(state.last_result);save(state);}});
show(read().last_result);
}})();</script>'''
    script_marker = "<script nonce=\""
    pos = body.find(script_marker)
    if pos >= 0:
        body = body[:pos] + listener + "\n" + body[pos:]
    else:
        body = body.replace("</body>", listener + "\n</body>", 1)
    return body


def make_v34_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client=None,
    overview_client=None,
):
    BaseHandler = freepreview.make_v33_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V34Handler(BaseHandler):
        server_version = "KriptoPanel/3.4"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            info = self._plan_info(session)
            if str(info.get("plan")) != commercial.PLAN_FREE:
                return super()._render_root_v17(session)
            nonce = secrets.token_urlsafe(18)
            body = enhance_free_page(freepreview.free_preview_page(session, nonce), nonce)
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "public_real_results": PUBLIC_RESULT_LIMIT,
                    "free_follow_result": True,
                    "free_visible_open_signals": 1,
                    "signal_engine": "unchanged",
                })
                return
            if path == "/api/public/results":
                try:
                    payload = build_public_results(service.get_data())
                except Exception:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": "public_results_unavailable"})
                    return
                self._json(HTTPStatus.OK, payload)
                return
            if path == "/" and not self._session():
                nonce = secrets.token_urlsafe(18)
                body = enhance_public_home(commercial.public_home_page(nonce), nonce)
                self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)
                return
            return super().do_GET()

    return V34Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.4 şeffaf performans ve FREE takip.")
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
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v34_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} public_results=6 free_follow=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
