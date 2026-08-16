"""Kripto Kontrol Merkezi V3.30 - Hesap / Üyelik sade kullanıcı deneyimi.

V3.29 ürün akışını ve önceki bütün panel katmanlarını korur. Bu katman yalnız
üyenin hesap ve Premium ekranlarındaki bilgi hiyerarşisini sadeleştirir:
- Hesabım: kullanıcı, plan ve gerçekten anlamlı üyelik durumu önceliklidir.
- FREE üyede boş "Premium bitiş" ve tekrarlı "Aktif" kutuları ilk bakıştan kalkar.
- Premium ekranında ödeme/üyelik eylemi öne çıkar; açıklamalar ve ödeme geçmişi
  isteğe bağlı açılır.
- Yönetim üyelik ekranı değiştirilmez; admin araçları yönetim tarafında kalır.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE, state/ledger yazımı ve
ödeme/üyelik backend davranışı değiştirilmez.
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
import dashboard_flowux_app as flowux
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_30_ACCOUNT_UX_2026_08_16"

CSS = r'''
/* V3.30 - üye hesabında önce gerekli bilgi */
.v330-account-page .shell,.v330-premium-page .shell{padding-top:20px!important}
.v330-account-page .card,.v330-premium-page .card{box-shadow:0 12px 34px rgba(0,0,0,.10)}
.v330-account-page .v330-hidden-primary{display:none!important}
.v330-account-page .v330-account-intro{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:15px}
.v330-account-page .v330-account-intro h1{margin:0;font-size:26px;letter-spacing:-.035em}.v330-account-page .v330-account-intro p{margin:3px 0 0;color:var(--muted);font-size:11px}
.v330-plan-pill{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(44,230,191,.28);background:rgba(44,230,191,.06);color:var(--teal);border-radius:999px;padding:6px 9px;font-size:9px;font-weight:900;white-space:nowrap}
.v330-account-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px}.v330-account-actions a{border:1px solid #29444c;border-radius:9px;padding:8px 10px;background:#07151c;color:#a8bfbc;font-size:10px}.v330-account-actions a.primary{border-color:#2ce6bf;background:#2ce6bf;color:#04120f}
.v330-detail{border:1px solid var(--line);border-radius:11px;background:#07151c;margin-top:12px;overflow:hidden}.v330-detail>summary{list-style:none;cursor:pointer;padding:11px 12px;font-size:10px;font-weight:900;color:#9db5b1;display:flex;align-items:center;justify-content:space-between}.v330-detail>summary::-webkit-details-marker{display:none}.v330-detail>summary:after{content:'⌄';color:#6e8a86}.v330-detail[open]>summary:after{transform:rotate(180deg)}.v330-detail-body{border-top:1px solid var(--line);padding:11px}
.v330-premium-page .v330-secondary-note{display:none!important}.v330-premium-page .v330-history-card>h2{margin-bottom:0}.v330-premium-page .v330-history-card .v330-detail{margin-top:12px}.v330-premium-page .v330-history-card table{margin-top:0}
.v330-premium-page .v330-action-card .active-plan,.v330-premium-page .v330-action-card .wait-box{margin-top:8px}
.v330-member-note{color:var(--muted);font-size:9px;margin:8px 0 0}
@media(max-width:650px){.v330-account-page .v330-account-intro{align-items:flex-start;flex-direction:column}.v330-account-page .grid{grid-template-columns:1fr!important}.v330-account-page .card,.v330-premium-page .card{padding:16px!important}.v330-account-actions{display:grid;grid-template-columns:1fr}.v330-account-actions a{text-align:center;min-height:40px;display:grid;place-items:center}}
'''

SCRIPT = r'''
<script nonce="__NONCE__" id="v330-account-script">
(()=>{'use strict';if(window.__v330AccountUx)return;window.__v330AccountUx=true;
const PLAN='__PLAN__';
function text(el){return String(el?.textContent||'').trim()}
function account(){document.body.classList.add('v330-account-page');const shell=document.querySelector('.shell');if(!shell)return;const cards=[...shell.querySelectorAll(':scope > .card')];const main=cards[0],payment=cards[1];if(!main)return;const h1=main.querySelector('h1');const grid=main.querySelector('.grid');if(!grid)return;const items=[...grid.querySelectorAll('.item')];let username='Üye',planLabel=PLAN,expiry='';for(const item of items){const label=text(item.querySelector('small')).toLocaleUpperCase('tr-TR');const value=text(item.querySelector('strong'));if(label.includes('KULLANICI'))username=value||username;if(label==='PLAN')planLabel=value||planLabel;if(label.includes('BİTİŞ')){expiry=value;if(PLAN==='FREE'||value==='—')item.classList.add('v330-hidden-primary');else item.querySelector('small').textContent='Üyelik bitiş';}if(label==='DURUM')item.classList.add('v330-hidden-primary')}
 if(h1){const intro=document.createElement('div');intro.className='v330-account-intro';intro.innerHTML=`<div><h1>Hesabım</h1><p>${username} · üyelik bilgilerin ve gerekli işlemler</p></div><span class="v330-plan-pill">${planLabel}</span>`;h1.replaceWith(intro)}
 const actions=document.createElement('div');actions.className='v330-account-actions';actions.innerHTML=`<a href="/" class="primary">Panele dön</a><a href="/premium">${PLAN==='FREE'?'Premium üyeliği incele':'Üyelik merkezine git'}</a>`;grid.insertAdjacentElement('afterend',actions);
 if(payment){const title=payment.querySelector('h2');if(title)title.textContent='Üyelik ve ödeme';const p=payment.querySelector('p');if(p)p.remove()}
 const back=shell.querySelector(':scope > a[href="/"]');if(back)back.style.display='none';
}
function detailWrap(nodes,label){const valid=nodes.filter(Boolean);if(!valid.length)return null;const d=document.createElement('details');d.className='v330-detail';const s=document.createElement('summary');s.textContent=label;const body=document.createElement('div');body.className='v330-detail-body';d.append(s,body);valid[0].parentNode.insertBefore(d,valid[0]);for(const n of valid)body.appendChild(n);return d}
function premium(){document.body.classList.add('v330-premium-page');const shell=document.querySelector('.shell');if(!shell)return;const cards=[...shell.querySelectorAll(':scope > .card')];const top=cards[0],action=cards[1],history=cards[2];if(top){const h=top.querySelector('h1');if(h)h.textContent='Üyeliğim';const p=top.querySelector('h1 + p');if(p)p.textContent=PLAN==='FREE'?'Premium özellikleri ve üyelik işlemini buradan yönetebilirsin.':'Premium üyeliğin ve yenileme durumun burada.'}
 if(action){action.classList.add('v330-action-card');const h=action.querySelector('h2');if(h)h.textContent=PLAN==='FREE'?'Premium üyelik işlemi':'Üyelik işlemleri';const intro=action.querySelector('h2 + p');if(intro)intro.textContent=PLAN==='FREE'?'Ödeme bildirimi yalnız ödeme tamamlandıktan sonra gönderilir.':'Aktif üyeliğin ve yenileme bilgilerin aşağıda.';const instruction=action.querySelector('.instructions');const paras=[...action.querySelectorAll('p')];const crypto=paras.find(p=>text(p).toLocaleUpperCase('tr-TR').includes('KRİPTO ÖDEME'));if(crypto)crypto.classList.add('v330-secondary-note');if(instruction)detailWrap([instruction,crypto], 'Ödeme açıklamasını göster');const active=action.querySelector('.active-plan,.wait-box');if(active&&h)h.insertAdjacentElement('afterend',active)}
 if(history){history.classList.add('v330-history-card');const table=history.querySelector('table');if(table)detailWrap([table],'Ödeme geçmişini göster');const h=history.querySelector('h2');if(h)h.textContent='Geçmiş işlemler'}
 const back=shell.querySelector(':scope > a[href="/"]');if(back)back.textContent='← Panele dön';
}
function init(){const p=location.pathname;if(p==='/account')account();else if(p==='/premium')premium()}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init,{once:true});else init();
})();
</script>
'''


def enhance_member_account_ui(body: str, nonce: str, *, plan: str) -> str:
    """Hesap/Premium HTML'ini salt sunum katmanıyla sadeleştirir."""
    if 'id="v330-account-script"' in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "\n</style>", 1)
    script = SCRIPT.replace("__NONCE__", html.escape(str(nonce or ""), quote=True)).replace(
        "__PLAN__", html.escape(str(plan or commercial.PLAN_FREE), quote=True)
    )
    if "</body>" in body:
        body = body.replace("</body>", script + "\n</body>", 1)
    return body


def make_v330_handler(
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
    BaseHandler = flowux.make_v329_handler(
        config, service, sessions, limiter, store, candle_client, overview_client, history_cache=cache
    )

    class V330Handler(BaseHandler):
        server_version = "KriptoPanel/3.30"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path in {"/account", "/premium"}:
                    session = self._session()
                    if session:
                        info = self._plan_info(session)
                        plan = str(info.get("plan") or commercial.PLAN_FREE)
                        # Yönetici üyelik backend'i değil; yalnız kişisel hesap sayfası sadeleşir.
                        body = enhance_member_account_ui(body, str(nonce or ""), plan=plan)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status": "ok",
                    "version": VERSION,
                    "account_information_hierarchy": True,
                    "free_empty_expiry_hidden": True,
                    "premium_details_on_demand": True,
                    "payment_history_on_demand": True,
                    "admin_membership_screen": "preserved",
                    "membership_backend": "unchanged",
                    "payment_backend": "unchanged",
                    "signal_engine": "unchanged",
                    "telegram": "unchanged",
                    "trade_management": "unchanged",
                    "ledger_write": "unchanged",
                })
                return
            return super().do_GET()

    return V330Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.30 Hesap UX")
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
    handler = make_v330_handler(config, service, sessions, limiter, store, candle_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} account_ux=1 membership_backend=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
