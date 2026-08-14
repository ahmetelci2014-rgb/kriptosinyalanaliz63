"""Kripto Kontrol Merkezi V2 - sade ve görev odaklı arayüz.

Bu katman V1.9 canlı piyasa/üyelik altyapısını değiştirmeden yalnız sunumu sadeleştirir.
Eski ayrıntılı panel /advanced altında korunur.
Sinyal üretimi, strateji, Telegram ve emir akışı bu dosyada yoktur.
"""

from __future__ import annotations

import argparse
import html
import os
import secrets
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_market_app as v19
from dashboard_live_app import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    LoginRateLimiter,
    OKXMarketDataClient,
    PanelConfig,
    build_service,
)


VERSION = "KRIPTO_KONTROL_MERKEZI_V2_COMPACT_2026_08_14"


def compact_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    username_raw = str(session.get("username") or "üye")
    role = str(session.get("role") or ROLE_MEMBER).upper()
    is_admin = role == ROLE_ADMIN
    username = html.escape(username_raw)
    role_label = "Yönetici" if is_admin else "Üye"
    csrf = html.escape(str(session.get("csrf") or ""), quote=True)
    nonce_attr = html.escape(nonce, quote=True)
    admin_nav = (
        '<button class="nav-item admin-only" data-view="system"><span>◉</span><b>Sistem</b></button>'
        '<a class="nav-item admin-only" href="/admin/users"><span>♙</span><b>Kullanıcılar</b></a>'
        if is_admin
        else ""
    )
    admin_top = '<a class="top-link admin-only" href="/admin/users">Kullanıcılar</a>' if is_admin else ""

    return f'''<!doctype html>
<html lang="tr" data-admin="{str(is_admin).lower()}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="color-scheme" content="dark">
  <title>Kripto Kontrol Merkezi</title>
  <style>
    :root{{--bg:#071018;--panel:#0c1720;--panel2:#101e29;--line:#1d303b;--text:#edf7f5;--muted:#7f9b98;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59;--blue:#60a5fa;--purple:#a78bfa;--side:222px;--radius:15px}}
    *{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}}
    a{{color:inherit;text-decoration:none}}button,input,select{{font:inherit}}button{{cursor:pointer}}.app{{min-height:100vh}}
    .sidebar{{position:fixed;inset:0 auto 0 0;width:var(--side);background:#09131b;border-right:1px solid var(--line);padding:20px 13px 16px;display:flex;flex-direction:column;z-index:30}}
    .brand{{display:flex;align-items:center;gap:10px;padding:4px 7px 20px}}.logo{{width:38px;height:38px;border:1px solid rgba(44,230,191,.45);border-radius:12px;display:grid;place-items:center;color:var(--teal);font-weight:950;background:rgba(44,230,191,.08)}}.brand strong{{display:block;font-size:14px}}.brand small{{color:var(--muted);font-size:9px;letter-spacing:.12em;text-transform:uppercase}}
    .nav-title{{padding:10px 10px 6px;color:#536e6b;font-size:9px;font-weight:850;letter-spacing:.12em;text-transform:uppercase}}.nav{{display:flex;flex-direction:column;gap:4px}}
    .nav-item{{width:100%;border:1px solid transparent;background:transparent;color:#8ca5a2;border-radius:10px;padding:10px 11px;display:flex;align-items:center;gap:10px;text-align:left;font-size:12px;font-weight:750}}.nav-item span{{width:18px;text-align:center;font-size:14px}}.nav-item:hover{{background:#0d1c25;color:#dcebea}}.nav-item.active{{background:rgba(44,230,191,.09);border-color:rgba(44,230,191,.22);color:var(--teal)}}
    .sidebar-foot{{margin-top:auto;border-top:1px solid var(--line);padding-top:12px}}.profile-mini{{display:flex;align-items:center;gap:9px;padding:8px}}.avatar{{width:32px;height:32px;border-radius:10px;display:grid;place-items:center;background:linear-gradient(145deg,rgba(44,230,191,.25),rgba(96,165,250,.15));color:var(--teal);font-weight:900}}.profile-mini strong{{font-size:11px;display:block}}.profile-mini small{{font-size:9px;color:var(--muted)}}
    .content{{margin-left:var(--side);min-height:100vh}}.topbar{{height:64px;border-bottom:1px solid var(--line);background:rgba(7,16,24,.92);backdrop-filter:blur(14px);position:sticky;top:0;z-index:20;display:flex;align-items:center;padding:0 26px;gap:12px}}.top-title{{font-weight:850;letter-spacing:-.01em}}.top-spacer{{flex:1}}.live-pill,.top-link,.icon-btn{{border:1px solid var(--line);background:#0b1720;border-radius:999px;color:#9eb5b2;padding:7px 10px;font-size:10px;font-weight:800}}.live-pill{{display:flex;align-items:center;gap:6px}}.live-dot{{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 9px var(--green)}}.icon-btn:hover,.top-link:hover{{border-color:rgba(44,230,191,.5);color:var(--teal)}}
    main{{width:min(1260px,calc(100% - 42px));margin:0 auto;padding:28px 0 74px}}.page{{display:none}}.page.active{{display:block}}.page-head{{display:flex;justify-content:space-between;align-items:flex-end;gap:14px;margin-bottom:20px}}.page-head h1{{margin:0;font-size:26px;letter-spacing:-.035em}}.page-head p{{margin:4px 0 0;color:var(--muted);font-size:12px}}.actions{{display:flex;gap:7px;flex-wrap:wrap}}
    .btn{{border:1px solid var(--line);border-radius:10px;background:#0c1922;color:#a9bfbc;padding:8px 11px;font-weight:800;font-size:11px}}.btn:hover{{border-color:var(--teal);color:var(--teal)}}.btn.primary{{background:var(--teal);color:#04120f;border-color:var(--teal)}}
    .summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}}.metric{{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel);padding:15px 16px;min-height:91px}}.metric small{{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.08em;font-weight:850}}.metric strong{{display:block;margin-top:6px;font-size:25px;line-height:1.1}}.metric em{{font-style:normal;color:#78928f;font-size:10px}}.metric.green strong{{color:var(--green)}}.metric.red strong{{color:var(--red)}}.metric.blue strong{{color:var(--blue)}}
    .grid-2{{display:grid;grid-template-columns:1.25fr .75fr;gap:14px}}.panel{{border:1px solid var(--line);border-radius:var(--radius);background:var(--panel);overflow:hidden}}.panel-head{{padding:14px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;gap:10px}}.panel-head h2{{margin:0;font-size:14px}}.panel-head small{{color:var(--muted)}}.panel-body{{padding:12px}}
    .empty{{padding:26px;text-align:center;color:var(--muted);font-size:12px}}.list{{display:flex;flex-direction:column;gap:7px}}.row-card{{border:1px solid #182c37;background:#0a161e;border-radius:11px;padding:11px 12px;display:grid;grid-template-columns:minmax(140px,1.1fr) .72fr .72fr .72fr auto;align-items:center;gap:10px}}.row-card:hover{{border-color:#2a4856;background:#0b1922}}.coin{{display:flex;align-items:center;gap:9px;min-width:0}}.coin-mark{{width:34px;height:34px;border-radius:10px;background:#102732;border:1px solid #21424d;color:var(--teal);display:grid;place-items:center;font-size:9px;font-weight:900;flex:0 0 auto}}.coin strong{{display:block;font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.coin small{{color:var(--muted);font-size:9px}}.data-block small{{display:block;color:#607c79;font-size:8px;text-transform:uppercase;letter-spacing:.06em}}.data-block b{{font-size:11px;font-weight:800}}.tag{{display:inline-flex;align-items:center;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:9px;font-weight:850;white-space:nowrap}}.tag.long,.tag.tp{{color:var(--green);border-color:rgba(66,226,140,.25);background:rgba(66,226,140,.06)}}.tag.short,.tag.sl{{color:var(--red);border-color:rgba(255,98,125,.25);background:rgba(255,98,125,.06)}}.tag.be{{color:var(--amber)}}
    .result-item{{display:flex;align-items:center;gap:9px;padding:9px 4px;border-bottom:1px solid rgba(29,48,59,.7)}}.result-item:last-child{{border:0}}.result-main{{flex:1;min-width:0}}.result-main strong{{font-size:11px}}.result-main div{{color:var(--muted);font-size:9px}}.result-right{{text-align:right}}.result-right b{{font-size:11px}}.result-right small{{display:block;color:var(--muted);font-size:8px}}
    .toolbar{{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin-bottom:12px}}.toolbar input,.toolbar select{{border:1px solid var(--line);background:#08141c;color:var(--text);border-radius:9px;padding:9px 10px;outline:none;font-size:11px}}.toolbar input{{min-width:220px;flex:1}}.toolbar input:focus,.toolbar select:focus{{border-color:var(--teal)}}
    .table-list{{display:flex;flex-direction:column;gap:7px}}.wide-card{{border:1px solid var(--line);border-radius:12px;background:#0a161e;padding:12px 13px}}.wide-top{{display:flex;align-items:center;gap:10px;justify-content:space-between}}.wide-top .coin{{flex:1}}.levels{{display:grid;grid-template-columns:repeat(5,1fr);gap:6px;margin-top:10px}}.level{{background:#08131a;border-radius:8px;padding:7px}}.level small{{display:block;color:#5f7976;font-size:8px}}.level b{{font-size:10px}}
    .system-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}}.system-card{{border:1px solid var(--line);border-radius:12px;background:#0a161e;padding:13px}}.system-card small{{color:var(--muted);font-size:9px}}.system-card strong{{display:block;font-size:17px;margin-top:4px}}.warnings{{margin-top:12px}}.warning{{border:1px solid rgba(255,189,89,.24);background:rgba(255,189,89,.05);color:#e9c98f;border-radius:9px;padding:9px 10px;margin-top:6px;font-size:10px}}.source-line{{display:grid;grid-template-columns:1fr auto auto;gap:10px;padding:8px 0;border-bottom:1px solid rgba(29,48,59,.7);align-items:center}}.source-line:last-child{{border:0}}.source-line small{{color:var(--muted)}}
    .mobile-nav{{display:none}}.mobile-more{{display:none}}
    @media(max-width:1050px){{:root{{--side:190px}}.summary{{grid-template-columns:repeat(2,1fr)}}.grid-2{{grid-template-columns:1fr}}.row-card{{grid-template-columns:minmax(130px,1.2fr) .8fr .8fr auto}}.row-card .hide-mid{{display:none}}}}
    @media(max-width:760px){{body{{padding-bottom:68px}}.sidebar{{display:none}}.content{{margin-left:0}}.topbar{{height:56px;padding:0 14px}}.top-title{{font-size:12px}}.top-link,.live-pill span:last-child{{display:none}}main{{width:calc(100% - 22px);padding:18px 0 34px}}.page-head{{align-items:flex-start}}.page-head h1{{font-size:22px}}.page-head p{{font-size:11px}}.summary{{gap:8px}}.metric{{padding:12px;min-height:82px}}.metric strong{{font-size:22px}}.row-card{{grid-template-columns:1fr auto;padding:10px}}.row-card .data-block{{display:none}}.levels{{grid-template-columns:repeat(2,1fr)}}.system-grid{{grid-template-columns:1fr 1fr}}.toolbar input{{min-width:100%}}.mobile-nav{{display:flex;position:fixed;left:0;right:0;bottom:0;height:66px;background:rgba(8,18,25,.97);border-top:1px solid var(--line);z-index:50;padding-bottom:env(safe-area-inset-bottom)}}.mobile-nav button,.mobile-nav a{{flex:1;border:0;background:none;color:#607a77;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;font-size:8px;font-weight:750}}.mobile-nav span{{font-size:17px}}.mobile-nav .active{{color:var(--teal)}}}}
    @media(max-width:430px){{.summary{{grid-template-columns:1fr 1fr}}.metric em{{display:none}}.system-grid{{grid-template-columns:1fr}}}}
  </style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"><div class="logo">K</div><div><strong>Kripto Kontrol</strong><small>Merkezi</small></div></div>
    <div class="nav-title">Kontrol</div>
    <nav class="nav">
      <button class="nav-item active" data-view="home"><span>⌂</span><b>Ana Sayfa</b></button>
      <button class="nav-item" data-view="signals"><span>⚡</span><b>Sinyaller</b></button>
      <button class="nav-item" data-view="trades"><span>↕</span><b>İşlemler</b></button>
      <button class="nav-item" data-view="results"><span>✓</span><b>Sonuçlar</b></button>
      <a class="nav-item" href="/market-center"><span>⌁</span><b>Piyasa</b></a>
      {admin_nav}
    </nav>
    <div class="nav-title">Hesap</div>
    <nav class="nav">
      <a class="nav-item" href="/account"><span>○</span><b>Hesabım</b></a>
      <a class="nav-item" href="/advanced"><span>▦</span><b>Gelişmiş Görünüm</b></a>
    </nav>
    <div class="sidebar-foot">
      <div class="profile-mini"><div class="avatar">{html.escape(username_raw[:1].upper() or 'K')}</div><div><strong>{username}</strong><small>{role_label}</small></div></div>
      <form method="post" action="/logout"><input type="hidden" name="csrf" value="{csrf}"><button class="nav-item" type="submit"><span>↪</span><b>Çıkış</b></button></form>
    </div>
  </aside>

  <div class="content">
    <header class="topbar">
      <div class="top-title" id="topTitle">Ana Sayfa</div>
      <div class="top-spacer"></div>
      <div class="live-pill"><i class="live-dot"></i><span id="liveText">Canlı veri</span></div>
      {admin_top}
      <a class="top-link" href="/account">{role_label} · {username}</a>
      <button class="icon-btn" id="refreshBtn" type="button">Yenile</button>
    </header>

    <main>
      <section class="page active" id="page-home">
        <div class="page-head"><div><h1>Kontrol Merkezi</h1><p>Önemli olanlar önde; ayrıntılar ilgili bölümde.</p></div><div class="actions"><a class="btn primary" href="/market-center">Piyasayı incele</a></div></div>
        <div class="summary" id="homeMetrics"></div>
        <div class="grid-2">
          <div class="panel"><div class="panel-head"><div><h2>Açık sinyaller</h2><small>En güncel aktif işlemler</small></div><button class="btn" data-view="signals">Tümünü gör</button></div><div class="panel-body"><div class="list" id="homeOpen"></div></div></div>
          <div class="panel"><div class="panel-head"><div><h2>Son sonuçlar</h2><small>En yeni kapanışlar</small></div><button class="btn" data-view="results">Tümünü gör</button></div><div class="panel-body" id="homeResults"></div></div>
        </div>
      </section>

      <section class="page" id="page-signals">
        <div class="page-head"><div><h1>Sinyaller</h1><p>Premium, Scalp ve radar işlemlerini tek listeden takip et.</p></div></div>
        <div class="toolbar"><input id="signalSearch" placeholder="Coin ara: BTC, ETH, SOL..."><select id="signalDirection"><option value="">Tüm yönler</option><option>LONG</option><option>SHORT</option></select><select id="signalSystem"><option value="">Tüm sistemler</option></select></div>
        <div class="table-list" id="signalsList"></div>
      </section>

      <section class="page" id="page-trades">
        <div class="page-head"><div><h1>İşlemler</h1><p>Açık işlemlerde giriş, hedef ve stop seviyeleri.</p></div></div>
        <div class="table-list" id="tradesList"></div>
      </section>

      <section class="page" id="page-results">
        <div class="page-head"><div><h1>Sonuçlar</h1><p>TP, SL, BE ve kapanan işlemler sade geçmiş görünümünde.</p></div></div>
        <div class="toolbar"><input id="resultSearch" placeholder="Coin veya sistem ara"><select id="resultOutcome"><option value="">Tüm sonuçlar</option><option value="TP">TP</option><option value="SL">SL</option><option value="BE">BE</option></select></div>
        <div class="panel"><div class="panel-body" id="resultsList"></div></div>
      </section>

      <section class="page admin-only" id="page-system">
        <div class="page-head"><div><h1>Sistem</h1><p>Teknik sağlık ve veri kaynakları yalnız yönetici görünümünde.</p></div><div class="actions"><a class="btn" href="/advanced">Ayrıntılı teknik ekran</a></div></div>
        <div class="system-grid" id="systemMetrics"></div>
        <div class="grid-2" style="margin-top:14px"><div class="panel"><div class="panel-head"><h2>Kaynak sağlığı</h2></div><div class="panel-body" id="sourceList"></div></div><div class="panel"><div class="panel-head"><h2>Uyarılar</h2></div><div class="panel-body warnings" id="warningList"></div></div></div>
      </section>
    </main>
  </div>
</div>

<nav class="mobile-nav">
  <button class="active" data-view="home"><span>⌂</span>Ana</button>
  <button data-view="signals"><span>⚡</span>Sinyal</button>
  <a href="/market-center"><span>⌁</span>Piyasa</a>
  <button data-view="trades"><span>↕</span>İşlem</button>
  <a href="/account"><span>○</span>Hesap</a>
</nav>

<script nonce="{nonce_attr}">
(() => {{
  const state={{data:null,view:'home'}};
  const $=id=>document.getElementById(id);
  const esc=value=>String(value??'').replace(/[&<>\"']/g,ch=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',"'":'&#39;'}}[ch]));
  const num=value=>{{const n=Number(value);return Number.isFinite(n)?n:null}};
  const price=value=>{{const n=num(value);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{{maximumFractionDigits:2}});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{{maximumFractionDigits:5}});return n.toLocaleString('tr-TR',{{maximumFractionDigits:9}});}};
  const date=value=>{{const n=num(value);if(!n)return '—';return new Date(n*1000).toLocaleString('tr-TR',{{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}});}};
  const system=row=>String(row?.system_label||row?.system||row?.source||'Sistem');
  const direction=row=>String(row?.direction||'').toUpperCase();
  const outcome=row=>String(row?.outcome||row?.result||'').toUpperCase();
  const tag=(text,kind='')=>`<span class="tag ${{kind}}">${{esc(text||'—')}}</span>`;
  const initials=symbol=>String(symbol||'?').replace('USDT','').slice(0,5);
  const isTp=o=>String(o||'').startsWith('TP')&&!String(o||'').includes('BE');

  function switchView(view){{
    if(view==='system' && document.documentElement.dataset.admin!=='true')return;
    state.view=view;
    document.querySelectorAll('.page').forEach(el=>el.classList.toggle('active',el.id===`page-${{view}}`));
    document.querySelectorAll('[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view));
    const titles={{home:'Ana Sayfa',signals:'Sinyaller',trades:'İşlemler',results:'Sonuçlar',system:'Sistem'}};
    $('topTitle').textContent=titles[view]||'Kripto Kontrol';
    window.scrollTo({{top:0,behavior:'smooth'}});
  }}

  function metric(label,value,note,cls=''){{return `<div class="metric ${{cls}}"><small>${{esc(label)}}</small><strong>${{esc(value)}}</strong><em>${{esc(note)}}</em></div>`;}}
  function compactTrade(row){{
    const dir=direction(row),kind=dir==='LONG'?'long':dir==='SHORT'?'short':'';
    return `<div class="row-card"><div class="coin"><div class="coin-mark">${{esc(initials(row.symbol))}}</div><div><strong>${{esc(row.symbol||'—')}}</strong><small>${{esc(system(row))}}</small></div></div><div class="data-block"><small>Yön</small>${{tag(dir,kind)}}</div><div class="data-block hide-mid"><small>Giriş</small><b>${{price(row.entry)}}</b></div><div class="data-block"><small>TP1</small><b>${{price(row.tp1)}}</b></div><a class="btn" href="/market-center?symbol=${{encodeURIComponent(row.symbol||'')}}">Grafik</a></div>`;
  }}
  function detailedTrade(row){{
    const dir=direction(row),kind=dir==='LONG'?'long':dir==='SHORT'?'short':'';
    return `<div class="wide-card"><div class="wide-top"><div class="coin"><div class="coin-mark">${{esc(initials(row.symbol))}}</div><div><strong>${{esc(row.symbol||'—')}}</strong><small>${{esc(system(row))}} · ${{date(row.opened_at||row.sent_at)}}</small></div></div>${{tag(dir,kind)}}</div><div class="levels"><div class="level"><small>Giriş</small><b>${{price(row.entry)}}</b></div><div class="level"><small>TP1</small><b>${{price(row.tp1)}}</b></div><div class="level"><small>TP2</small><b>${{price(row.tp2)}}</b></div><div class="level"><small>TP3</small><b>${{price(row.tp3)}}</b></div><div class="level"><small>SL</small><b>${{price(row.sl)}}</b></div></div></div>`;
  }}
  function resultItem(row){{
    const o=outcome(row),kind=isTp(o)?'tp':o==='SL'?'sl':o.includes('BE')?'be':'';const r=num(row.r_result);
    return `<div class="result-item"><div class="coin-mark">${{esc(initials(row.symbol))}}</div><div class="result-main"><strong>${{esc(row.symbol||'—')}} · ${{esc(system(row))}}</strong><div>${{esc(direction(row))}} · ${{date(row.closed_at||row.finalized_at)}}</div></div><div class="result-right">${{tag(o||'KAPALI',kind)}}<small>${{r===null?'':`${{r>=0?'+':''}}${{r.toFixed(2)}}R`}}</small></div></div>`;
  }}

  function renderHome(data){{
    const open=Array.isArray(data.open_trades)?data.open_trades:[],results=Array.isArray(data.recent_results)?data.recent_results:[];
    const tp=results.filter(r=>isTp(outcome(r))).length,sl=results.filter(r=>outcome(r)==='SL').length;
    const overall=String(data.health?.overall|| (data.data_quality?.ok?'GREEN':'YELLOW')).toUpperCase();
    $('homeMetrics').innerHTML=[metric('Açık işlem',open.length,'Aktif sinyaller','blue'),metric('Son TP',tp,'Sonuç listesinde','green'),metric('Son SL',sl,'Sonuç listesinde','red'),metric('Sistem',overall, data.data_quality?.ok?'Veri akışı normal':'Kontrol gerekli',overall==='GREEN'?'green':'')].join('');
    $('homeOpen').innerHTML=open.slice(0,5).map(compactTrade).join('')||'<div class="empty">Şu anda açık sinyal yok.</div>';
    $('homeResults').innerHTML=results.slice(0,7).map(resultItem).join('')||'<div class="empty">Henüz sonuç kaydı yok.</div>';
  }}

  function fillSignalSystems(open){{
    const select=$('signalSystem'),current=select.value;const values=[...new Set(open.map(system).filter(Boolean))].sort();
    select.innerHTML='<option value="">Tüm sistemler</option>'+values.map(v=>`<option>${{esc(v)}}</option>`).join('');select.value=values.includes(current)?current:'';
  }}
  function renderSignals(){{
    const data=state.data||{{}},open=Array.isArray(data.open_trades)?data.open_trades:[];const q=$('signalSearch').value.trim().toUpperCase(),dir=$('signalDirection').value,sys=$('signalSystem').value;
    const rows=open.filter(r=>(!q||String(r.symbol||'').toUpperCase().includes(q))&&(!dir||direction(r)===dir)&&(!sys||system(r)===sys));
    $('signalsList').innerHTML=rows.map(compactTrade).join('')||'<div class="empty panel">Filtreye uygun açık sinyal yok.</div>';
  }}
  function renderTrades(data){{const open=Array.isArray(data.open_trades)?data.open_trades:[];$('tradesList').innerHTML=open.map(detailedTrade).join('')||'<div class="empty panel">Açık işlem yok.</div>';}}
  function renderResults(){{
    const data=state.data||{{}},results=Array.isArray(data.recent_results)?data.recent_results:[];const q=$('resultSearch').value.trim().toUpperCase(),filter=$('resultOutcome').value;
    const rows=results.filter(r=>(!q||`${{r.symbol||''}} ${{system(r)}}`.toUpperCase().includes(q))&&(!filter||(filter==='TP'?isTp(outcome(r)):outcome(r).includes(filter))));
    $('resultsList').innerHTML=rows.map(resultItem).join('')||'<div class="empty">Filtreye uygun sonuç yok.</div>';
  }}
  function renderSystem(data){{
    if(document.documentElement.dataset.admin!=='true')return;const health=data.health||{{}},quality=data.data_quality||{{}},sources=Array.isArray(data.sources)?data.sources:[];
    const counts=health.counts||{{}};$('systemMetrics').innerHTML=[metric('Genel durum',String(health.overall||'UNKNOWN'),'System Control'),metric('Yeşil',counts.green??0,'Sağlıklı bileşen','green'),metric('Sarı / Kırmızı',(Number(counts.yellow)||0)+(Number(counts.red)||0),'İncelenecek')].join('');
    $('sourceList').innerHTML=sources.map(s=>`<div class="source-line"><div><b>${{esc(s.label||s.filename||'Kaynak')}}</b><br><small>${{esc(s.filename||'')}}</small></div>${{tag(String(s.status||'UNKNOWN'),String(s.status||'').toLowerCase())}}<small>${{s.age_hours==null?'—':`${{Number(s.age_hours).toFixed(1)}}s`}}</small></div>`).join('')||'<div class="empty">Kaynak ayrıntısı yok.</div>';
    const warnings=Array.isArray(quality.warnings)?quality.warnings:[];$('warningList').innerHTML=warnings.map(w=>`<div class="warning">${{esc(w)}}</div>`).join('')||(quality.ok?'<div class="empty">Aktif veri uyarısı yok.</div>':'<div class="warning">Veri kalitesi kontrol edilmeli.</div>');
  }}
  function renderAll(data){{state.data=data;renderHome(data);const open=Array.isArray(data.open_trades)?data.open_trades:[];fillSignalSystems(open);renderSignals();renderTrades(data);renderResults();renderSystem(data);}}

  async function loadData(force=false){{
    $('liveText').textContent='Güncelleniyor…';
    try{{const response=await fetch('/api/dashboard'+(force?'?refresh=1':''),{{credentials:'same-origin',cache:'no-store',headers:{{Accept:'application/json'}}}});if(response.status===401){{location.assign('/login');return;}}const data=await response.json();if(!response.ok)throw new Error(data.error||`HTTP ${{response.status}}`);renderAll(data);$('liveText').textContent=data.data_quality?.ok===false?'Son geçerli veri':'Canlı veri';}}catch(error){{$('liveText').textContent='Veri alınamadı';console.error(error);}}
  }}

  document.addEventListener('click',event=>{{const el=event.target.closest('[data-view]');if(!el)return;event.preventDefault();switchView(el.dataset.view);}});
  ['signalSearch','signalDirection','signalSystem'].forEach(id=>$(id).addEventListener(id==='signalSearch'?'input':'change',renderSignals));
  ['resultSearch','resultOutcome'].forEach(id=>$(id).addEventListener(id==='resultSearch'?'input':'change',renderResults));
  $('refreshBtn').addEventListener('click',()=>loadData(true));
  loadData();setInterval(()=>loadData(false),30000);
}})();
</script>
</body>
</html>'''


def make_v2_handler(
    config: PanelConfig,
    service,
    sessions,
    limiter: LoginRateLimiter,
    store,
    market_client: OKXMarketDataClient | None = None,
    overview_client: v19.OKXMarketOverviewClient | None = None,
):
    market_client = market_client or OKXMarketDataClient()
    overview_client = overview_client or v19.OKXMarketOverviewClient()
    BaseHandler = v19.make_v19_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
        overview_client,
    )

    class V2Handler(BaseHandler):
        server_version = "KriptoPanel/2.0"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(
                HTTPStatus.OK,
                compact_dashboard_page(session, nonce),
                "text/html; charset=utf-8",
                nonce=nonce,
            )

        def do_GET(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            if path == "/advanced":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                # V1.9 ayrıntılı görünümünü aynen koru; veri katmanı değişmez.
                super()._render_root_v17(session)
                return
            return super().do_GET()

    return V2Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2 sade arayüz.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = v19.v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = v19.v18.account_store_from_env(config)
    handler = make_v2_handler(config, service, sessions, limiter, store)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"{VERSION} http://{args.host}:{args.port} "
        f"users_ref={store.ref} compact_ui=on advanced_ui=/advanced market_center=/market-center"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
