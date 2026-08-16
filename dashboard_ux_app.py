"""Kripto Kontrol Merkezi V3.12 - Profesyonel Panel ve Mobil UX.

V3.11 veri/karar/deney katmanlarını aynen korur. Bu dosya yalnız sunum katmanıdır:
- günlük tek-bakış durum kartı
- mobil uygulama hissi ve daha güçlü görsel hiyerarşi
- sonuç listesinde istemci tarafı sayfalama
- isteğe bağlı sıkı/rahat görünüm

Sinyal, strateji, radar, Telegram, emir ve TP/SL davranışı değiştirilmez.
Yeni periyodik GitHub Actions işi eklenmez.
"""
from __future__ import annotations

import argparse
import html
import os
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_commercial_app as commercial
import dashboard_experiment_app as experiment
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_12_PROFESSIONAL_UX_2026_08_16"
RESULT_PAGE_SIZE = 12


UX_CSS = r'''
/* V3.12 professional UX - presentation only */
.v312-professional{
  --v312-glow:rgba(44,230,191,.10);
  --v312-blue:rgba(96,165,250,.09);
  background:
    radial-gradient(circle at 78% -8%,var(--v312-glow),transparent 28%),
    radial-gradient(circle at 28% 12%,var(--v312-blue),transparent 24%),
    var(--bg);
}
.v312-professional .sidebar{background:linear-gradient(180deg,#08131b 0%,#071118 100%);box-shadow:18px 0 42px rgba(0,0,0,.13)}
.v312-professional .topbar{background:rgba(6,15,22,.86);backdrop-filter:blur(20px) saturate(130%);-webkit-backdrop-filter:blur(20px) saturate(130%)}
.v312-professional .brand .logo{box-shadow:0 0 24px rgba(44,230,191,.08)}
.v312-professional main{max-width:1320px}
.v312-professional .page-head{margin-bottom:18px}
.v312-professional .page-head h1{font-weight:900;letter-spacing:-.045em}
.v312-professional .panel,
.v312-professional .metric,
.v312-professional .home-strong,
.v312-professional .home-fav,
.v312-professional .wide-card,
.v312-professional .row-card{
  box-shadow:0 9px 30px rgba(0,0,0,.10);
  transition:border-color .18s ease,transform .18s ease,box-shadow .18s ease;
}
.v312-professional .panel:hover{border-color:#24424d}
.v312-professional .home-strong:hover,.v312-professional .home-fav:hover,.v312-professional .wide-card:hover,.v312-professional .row-card:hover{
  transform:translateY(-1px);box-shadow:0 13px 34px rgba(0,0,0,.14)
}
.v312-professional .btn,.v312-professional .nav-item,.v312-professional .top-link,.v312-professional .icon-btn{min-height:38px}
.v312-professional button:focus-visible,.v312-professional a:focus-visible,.v312-professional input:focus-visible,.v312-professional select:focus-visible{outline:2px solid var(--teal);outline-offset:2px}
.v312-professional .panel-head{background:linear-gradient(180deg,rgba(255,255,255,.012),transparent)}
.v312-professional .metric strong{letter-spacing:-.035em}

.v312-pulse{
  position:relative;overflow:hidden;margin:0 0 18px;border:1px solid rgba(44,230,191,.20);border-radius:20px;
  background:linear-gradient(135deg,rgba(14,35,44,.96),rgba(8,22,30,.94));padding:17px 18px;
  box-shadow:0 18px 50px rgba(0,0,0,.18)
}
.v312-pulse:after{content:"";position:absolute;width:280px;height:280px;border-radius:50%;right:-130px;top:-180px;background:rgba(44,230,191,.08);pointer-events:none}
.v312-pulse-top{display:flex;justify-content:space-between;align-items:flex-start;gap:14px;position:relative;z-index:1}
.v312-pulse-eyebrow{display:flex;align-items:center;gap:7px;color:#88aaa6;font-size:9px;font-weight:900;letter-spacing:.11em;text-transform:uppercase}
.v312-pulse-dot{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 12px rgba(66,226,140,.75)}
.v312-pulse h2{font-size:23px;margin:5px 0 2px;letter-spacing:-.04em}
.v312-pulse-copy{margin:0;color:#8ba7a3;font-size:10px}
.v312-pulse-badge{border:1px solid rgba(44,230,191,.25);background:rgba(44,230,191,.07);color:var(--teal);border-radius:999px;padding:6px 9px;font-size:9px;font-weight:900;white-space:nowrap}
.v312-pulse-grid{position:relative;z-index:1;display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:14px}
.v312-pulse-stat{border:1px solid rgba(48,82,92,.55);background:rgba(4,15,21,.35);border-radius:12px;padding:10px 11px}
.v312-pulse-stat small{display:block;color:#6e8c89;font-size:8px;font-weight:850;text-transform:uppercase;letter-spacing:.06em}
.v312-pulse-stat b{display:block;font-size:16px;margin-top:3px;letter-spacing:-.02em}
.v312-pulse-stat span{display:block;color:#78928f;font-size:8px;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.v312-pulse-actions{position:relative;z-index:1;display:flex;gap:7px;margin-top:12px;flex-wrap:wrap}
.v312-pulse-actions button,.v312-pulse-actions a{border:1px solid #23404b;background:#091a22;color:#a6bfbb;border-radius:9px;padding:7px 10px;font-size:9px;font-weight:850;text-decoration:none;cursor:pointer}
.v312-pulse-actions .primary{background:var(--teal);border-color:var(--teal);color:#04120f}
.v312-refresh{color:#617f7b;font-size:8px;margin-left:auto;align-self:center}

.v312-density-toggle{border:1px solid var(--line);background:#0b1720;color:#8ea9a5;border-radius:999px;padding:7px 10px;font-size:9px;font-weight:850;white-space:nowrap}
.v312-compact-density .panel-head{padding:10px 13px}
.v312-compact-density .panel-body{padding:8px}
.v312-compact-density .row-card,.v312-compact-density .wide-card{padding:8px 10px}
.v312-compact-density .result-item{padding:6px 3px}
.v312-compact-density .metric{min-height:76px;padding:11px 12px}

.v312-pager{display:flex;align-items:center;justify-content:center;gap:8px;margin:10px 0 18px;color:#78928f;font-size:9px}
.v312-pager button{border:1px solid var(--line);background:#0b1720;color:#9bb3af;border-radius:8px;min-width:36px;min-height:34px;padding:5px 9px;font-weight:900}
.v312-pager button:disabled{opacity:.35;cursor:default}
.v312-pager strong{min-width:92px;text-align:center;color:#a9c0bd}

.v312-professional *{scrollbar-width:thin;scrollbar-color:#24404a transparent}
.v312-professional ::-webkit-scrollbar{width:7px;height:7px}
.v312-professional ::-webkit-scrollbar-thumb{background:#24404a;border-radius:20px}

@media(max-width:760px){
  .v312-professional{padding-bottom:calc(76px + env(safe-area-inset-bottom))}
  .v312-professional main{width:calc(100% - 16px);padding-top:12px}
  .v312-professional .topbar{height:54px;padding:0 10px}
  .v312-professional .page-head{align-items:flex-start;margin-bottom:12px}
  .v312-professional .page-head h1{font-size:20px}
  .v312-professional .page-head p{font-size:10px;line-height:1.45}
  .v312-professional .actions{width:100%;overflow:auto;flex-wrap:nowrap;padding-bottom:2px}
  .v312-professional .actions .btn{white-space:nowrap;min-height:40px}
  .v312-professional .summary{display:flex;overflow-x:auto;scroll-snap-type:x mandatory;gap:8px;padding:1px 1px 7px;margin-right:-8px}
  .v312-professional .summary .metric{flex:0 0 154px;scroll-snap-align:start;min-height:78px}
  .v312-professional .summary::-webkit-scrollbar{display:none}
  .v312-professional .panel{border-radius:13px}
  .v312-professional .panel-head{padding:12px}
  .v312-professional .panel-body{padding:9px}
  .v312-professional .mobile-nav{height:70px;background:rgba(6,15,22,.96);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px);box-shadow:0 -10px 32px rgba(0,0,0,.20)}
  .v312-professional .mobile-nav button,.v312-professional .mobile-nav a{min-height:54px;touch-action:manipulation}
  .v312-professional .mobile-nav span{font-size:18px}
  .v312-pulse{border-radius:16px;padding:14px 13px;margin-bottom:13px}
  .v312-pulse-top{gap:8px}
  .v312-pulse h2{font-size:20px}
  .v312-pulse-badge{font-size:8px;padding:5px 7px}
  .v312-pulse-grid{grid-template-columns:1fr 1fr;gap:6px;margin-top:11px}
  .v312-pulse-stat{padding:9px}
  .v312-pulse-stat b{font-size:15px}
  .v312-pulse-actions{overflow:auto;flex-wrap:nowrap;margin-right:-8px;padding-bottom:2px}
  .v312-pulse-actions button,.v312-pulse-actions a{white-space:nowrap;min-height:38px}
  .v312-refresh{display:none}
  .v312-density-toggle{display:none}
  .v312-professional .home-smart-grid{gap:9px}
  .v312-professional .home-strong-grid{gap:7px}
  .v312-professional .toolbar{position:sticky;top:58px;z-index:9;background:rgba(7,16,24,.94);padding:7px 0 8px;margin-bottom:7px;backdrop-filter:blur(12px)}
  .v312-professional .toolbar input,.v312-professional .toolbar select{min-height:42px}
}
@media(max-width:430px){
  .v312-pulse-copy{max-width:235px}
  .v312-professional .summary .metric{flex-basis:145px}
}
@media(prefers-reduced-motion:reduce){
  .v312-professional *{scroll-behavior:auto!important;transition:none!important;animation:none!important}
}
'''


UX_SCRIPT = r'''
(() => {
  'use strict';
  if (window.__v312UxLoaded) return;
  window.__v312UxLoaded = true;

  const PAGE_SIZE = __PAGE_SIZE__;
  const $ = id => document.getElementById(id);
  const num = v => { const n = Number(v); return Number.isFinite(n) ? n : null; };
  const parseTs = v => {
    if (v === null || v === undefined || v === '') return null;
    if (typeof v === 'number' || /^\d+(\.\d+)?$/.test(String(v))) {
      let n = Number(v); if (!Number.isFinite(n)) return null; if (n > 1e12) n /= 1000; return new Date(n * 1000);
    }
    const d = new Date(v); return Number.isNaN(d.getTime()) ? null : d;
  };
  const isToday = d => d && d.toDateString() === new Date().toDateString();
  const outcome = r => String(r?.outcome || r?.result || '').toUpperCase();
  const isTp = o => String(o || '').startsWith('TP') && !String(o || '').includes('BE');
  const rowTime = r => parseTs(r?.closed_at || r?.finalized_at || r?.updated_at || r?.opened_at || r?.sent_at || r?.created_at);

  function setText(id, value) { const el = $(id); if (el) el.textContent = String(value ?? '—'); }

  function renderPulse(data) {
    const root = $('v312DailyPulse'); if (!root) return;
    const open = Array.isArray(data?.open_trades) ? data.open_trades : [];
    const results = Array.isArray(data?.recent_results) ? data.recent_results : [];
    const today = results.filter(r => isToday(rowTime(r)));
    const tp = today.filter(r => isTp(outcome(r))).length;
    const sl = today.filter(r => outcome(r) === 'SL').length;
    const latest = results.map(r => ({r, d: rowTime(r)})).filter(x => x.d).sort((a,b) => b.d - a.d)[0];
    const health = String(data?.health?.overall || data?.system_health?.overall || '').toUpperCase();
    let headline = open.length ? `${open.length} açık işlem takipte` : 'Piyasa takibi sakin';
    let copy = today.length ? `Bugün ${tp} TP, ${sl} SL ve ${today.length} kapanan sonuç kaydı var.` : 'Bugün kapanan sonuç henüz görünmüyor; canlı veriler yenilenmeye devam ediyor.';
    let badge = health && health !== 'UNKNOWN' ? `Sistem ${health}` : 'Canlı takip';
    if (sl > tp && today.length >= 2) headline = 'Bugün daha temkinli takip';
    else if (tp > sl && today.length >= 2) headline = 'Bugünkü sonuç akışı olumlu';
    setText('v312Headline', headline);
    setText('v312Copy', copy);
    setText('v312Badge', badge);
    setText('v312Open', open.length);
    setText('v312Today', `${tp} TP / ${sl} SL`);
    setText('v312Latest', latest ? String(latest.r?.symbol || '—') : '—');
    setText('v312LatestNote', latest ? (outcome(latest.r) || 'Son sonuç') : 'Henüz sonuç yok');
    setText('v312Refresh', `Son yenileme ${new Date().toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'})}`);
  }

  function activateView(view) {
    const target = document.querySelector(`[data-view="${view}"]`);
    if (target) { target.click(); return true; }
    return false;
  }

  document.addEventListener('click', event => {
    const button = event.target.closest('[data-v312-view]');
    if (!button) return;
    event.preventDefault(); activateView(button.getAttribute('data-v312-view'));
  });

  window.addEventListener('kripto-dashboard-data', event => renderPulse(event.detail || {}));
  if (window.__kriptoDashboardData) renderPulse(window.__kriptoDashboardData);

  function installDensityToggle() {
    const topbar = document.querySelector('.topbar');
    if (!topbar || $('v312DensityToggle')) return;
    const button = document.createElement('button');
    button.type = 'button'; button.id = 'v312DensityToggle'; button.className = 'v312-density-toggle';
    const compact = localStorage.getItem('v312_density') === 'compact';
    document.body.classList.toggle('v312-compact-density', compact);
    button.textContent = compact ? 'Rahat görünüm' : 'Sıkı görünüm';
    button.addEventListener('click', () => {
      const next = !document.body.classList.contains('v312-compact-density');
      document.body.classList.toggle('v312-compact-density', next);
      localStorage.setItem('v312_density', next ? 'compact' : 'comfortable');
      button.textContent = next ? 'Rahat görünüm' : 'Sıkı görünüm';
    });
    const spacer = topbar.querySelector('.top-spacer');
    if (spacer) spacer.insertAdjacentElement('afterend', button); else topbar.appendChild(button);
  }

  let resultPage = 1;
  function installResultPager() {
    const list = $('resultsList'); if (!list || $('v312ResultPager')) return;
    const pager = document.createElement('div'); pager.id = 'v312ResultPager'; pager.className = 'v312-pager';
    pager.innerHTML = '<button type="button" data-v312-page="prev" aria-label="Önceki sonuç sayfası">‹</button><strong id="v312PageLabel">—</strong><button type="button" data-v312-page="next" aria-label="Sonraki sonuç sayfası">›</button>';
    const panel = list.closest('.panel'); (panel || list).insertAdjacentElement('afterend', pager);
    pager.addEventListener('click', event => {
      const btn = event.target.closest('[data-v312-page]'); if (!btn) return;
      const rows = Array.from(list.children).filter(el => el.classList.contains('result-item'));
      const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
      resultPage = btn.dataset.v312Page === 'next' ? Math.min(pages, resultPage + 1) : Math.max(1, resultPage - 1);
      applyResultPage();
      list.closest('.panel')?.scrollIntoView({behavior:'smooth',block:'start'});
    });
    const reset = () => { resultPage = 1; setTimeout(applyResultPage, 0); };
    $('resultSearch')?.addEventListener('input', reset);
    $('resultOutcome')?.addEventListener('change', reset);
    new MutationObserver(() => setTimeout(applyResultPage, 0)).observe(list, {childList:true});
    applyResultPage();
  }

  function applyResultPage() {
    const list = $('resultsList'), pager = $('v312ResultPager'); if (!list || !pager) return;
    const rows = Array.from(list.children).filter(el => el.classList.contains('result-item'));
    const pages = Math.max(1, Math.ceil(rows.length / PAGE_SIZE)); resultPage = Math.min(resultPage, pages);
    rows.forEach((row, i) => { row.style.display = (i >= (resultPage-1)*PAGE_SIZE && i < resultPage*PAGE_SIZE) ? '' : 'none'; });
    pager.hidden = rows.length <= PAGE_SIZE;
    const start = rows.length ? (resultPage-1)*PAGE_SIZE + 1 : 0, end = Math.min(rows.length, resultPage*PAGE_SIZE);
    setText('v312PageLabel', `${start}-${end} / ${rows.length}`);
    const prev = pager.querySelector('[data-v312-page="prev"]'), next = pager.querySelector('[data-v312-page="next"]');
    if (prev) prev.disabled = resultPage <= 1; if (next) next.disabled = resultPage >= pages;
  }

  installDensityToggle();
  installResultPager();
})();
'''


PULSE_HTML = r'''
<section class="v312-pulse" id="v312DailyPulse" aria-label="Günlük kontrol özeti">
  <div class="v312-pulse-top">
    <div>
      <div class="v312-pulse-eyebrow"><i class="v312-pulse-dot"></i>Günlük kontrol</div>
      <h2 id="v312Headline">Canlı veriler hazırlanıyor</h2>
      <p class="v312-pulse-copy" id="v312Copy">Açık işlemler ve bugünkü sonuç akışı yükleniyor.</p>
    </div>
    <span class="v312-pulse-badge" id="v312Badge">Canlı takip</span>
  </div>
  <div class="v312-pulse-grid">
    <div class="v312-pulse-stat"><small>Açık işlem</small><b id="v312Open">—</b><span>Şu an takipte</span></div>
    <div class="v312-pulse-stat"><small>Bugün TP / SL</small><b id="v312Today">—</b><span>Kapanan sonuçlar</span></div>
    <div class="v312-pulse-stat"><small>Son coin</small><b id="v312Latest">—</b><span id="v312LatestNote">Son sonuç</span></div>
    <div class="v312-pulse-stat"><small>Yenileme</small><b>30 sn</b><span>Canlı panel döngüsü</span></div>
  </div>
  <div class="v312-pulse-actions">
    <button class="primary" type="button" data-v312-view="trades">Açık işlemleri gör</button>
    <button type="button" data-v312-view="results">Sonuçlara git</button>
    <a href="/market-center">Piyasayı incele</a>
    <span class="v312-refresh" id="v312Refresh">Son yenileme —</span>
  </div>
</section>
'''


def inject_professional_ux(body: str, nonce: str | None = None) -> str:
    """Authenticated compact dashboard HTML'ine yalnız sunum/UX katmanı ekler."""
    if not isinstance(body, str) or 'id="page-home"' not in body:
        return body
    if 'id="v312DailyPulse"' in body:
        return body

    nonce_attr = f' nonce="{html.escape(str(nonce), quote=True)}"' if nonce else ""
    style = f"<style{nonce_attr}>{UX_CSS}</style>"
    script_text = UX_SCRIPT.replace("__PAGE_SIZE__", str(RESULT_PAGE_SIZE))
    script = f"<script{nonce_attr}>{script_text}</script>"

    if '<body>' in body:
        body = body.replace('<body>', '<body class="v312-professional">', 1)
    elif '<body class="' in body:
        body = body.replace('<body class="', '<body class="v312-professional ', 1)

    if '</head>' in body:
        body = body.replace('</head>', style + '\n</head>', 1)

    marker = '<section class="page active" id="page-home">'
    if marker in body:
        body = body.replace(marker, marker + '\n' + PULSE_HTML, 1)

    if '</body>' in body:
        body = body.replace('</body>', script + '\n</body>', 1)
    return body


def make_v312_handler(
    config: PanelConfig,
    service,
    sessions: accounts.ManagedSessionStore,
    limiter: LoginRateLimiter,
    store: commercial.CommercialAccountStore,
    market_client: OKXMarketDataClient | None = None,
    overview_client: market.OKXMarketOverviewClient | None = None,
):
    BaseHandler = experiment.make_v311_handler(
        config, service, sessions, limiter, store, market_client, overview_client
    )

    class V312Handler(BaseHandler):
        server_version = "KriptoPanel/3.12"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            path = urllib.parse.urlsplit(self.path).path
            if (
                status == HTTPStatus.OK
                and isinstance(body, str)
                and content_type.startswith("text/html")
                and path == "/"
                and 'id="page-home"' in body
            ):
                body = inject_professional_ux(body, nonce)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            if urllib.parse.urlsplit(self.path).path == "/healthz":
                self._json(
                    HTTPStatus.OK,
                    {
                        "status": "ok",
                        "version": VERSION,
                        "professional_ux": True,
                        "mobile_ux": True,
                        "daily_pulse": True,
                        "result_pagination": True,
                        "result_page_size": RESULT_PAGE_SIZE,
                        "extra_scheduled_actions": False,
                        "signal_engine": "unchanged",
                        "telegram": "unchanged",
                        "auto_apply": False,
                    },
                )
                return
            return super().do_GET()

    return V312Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.12 Profesyonel Panel ve Mobil UX")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = lifecycle.lifecycle_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v312_handler(config, service, sessions, limiter, store, market_client, overview_client)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} professional_ux=1 mobile_ux=1 extra_schedule=0 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
