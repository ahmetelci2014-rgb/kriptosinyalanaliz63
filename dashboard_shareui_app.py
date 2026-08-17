"""V3.32.9 paylaş UI dekoratörleri. Trading davranışına dokunmaz."""
from __future__ import annotations

import html
import re
from typing import Any

import dashboard_sharecard_app as cards

CSS = '.share-action,.share-mobile{display:inline-flex;align-items:center;justify-content:center;border:1px solid #3b665d;border-radius:9px;padding:6px 9px;background:#0d2823;color:#2ce6bf!important;font-size:10px;font-weight:900;text-decoration:none;white-space:nowrap}.share-mobile{margin-top:8px;width:100%}.resultcard .share-mobile{width:auto;margin-left:8px}.row-card .share-action{margin-left:6px}.wide-card .share-action,.result-item .share-action{margin-left:auto}'


def _pick(rows: list[dict[str, Any]], symbol: str, system: str = "", direction: str = "", outcome: str = "") -> dict[str, Any] | None:
    symbol = html.unescape(symbol).strip().upper()
    system = html.unescape(system).strip()
    direction = html.unescape(direction).strip().upper()
    outcome = html.unescape(outcome).strip().upper()
    for row in rows:
        if cards.symbol(row) != symbol:
            continue
        if system and cards.system_name(row) != system:
            continue
        if direction and cards.direction(row) != direction:
            continue
        if outcome and cards.outcome(row) != outcome:
            continue
        return row
    return None


def enhance_mobile(body: str, data: dict[str, Any], *, view: str) -> str:
    """JS'siz mobil işlem/sonuç kartlarına normal Paylaş bağlantısı ekler."""
    if "share-mobile" in body:
        return body
    open_rows = [row for row in (data.get("open_trades") or []) if isinstance(row, dict)]
    results = [row for row in (data.get("recent_results") or []) if isinstance(row, dict)]

    def open_repl(match: re.Match[str]) -> str:
        block = match.group(0)
        sm = re.search(r"<strong>([^<]+)</strong>", block)
        sy = re.search(r'class="system">([^<]*)</small>', block)
        dm = re.search(r'<span class="tag [^"]*">([^<]+)</span>', block)
        row = _pick(open_rows, sm.group(1), sy.group(1) if sy else "", dm.group(1) if dm else "") if sm else None
        if not row:
            return block
        stage = "tracking" if view == "trades" else "signal"
        href = html.escape(cards.share_href(row, "open", stage), quote=True)
        return block[:-10] + f'<a class="share-mobile" href="{href}">↗ Paylaş</a></article>'

    def result_repl(match: re.Match[str]) -> str:
        block = match.group(0)
        sm = re.search(r'class="resultmain"><strong>([^<]+)</strong>', block)
        small = re.search(r"<small>([^<]+)</small>", block)
        om = re.search(r'<span class="tag [^"]*">([^<]+)</span>', block)
        sys, direct = "", ""
        if small:
            bits = [part.strip() for part in html.unescape(small.group(1)).split("·")]
            sys = bits[0] if bits else ""
            direct = bits[-1] if len(bits) > 1 else ""
        row = _pick(results, sm.group(1), sys, direct, om.group(1) if om else "") if sm else None
        if not row:
            return block
        href = html.escape(cards.share_href(row, "result", "result"), quote=True)
        return block[:-10] + f'<a class="share-mobile" href="{href}">↗ Paylaş</a></article>'

    body = re.sub(r'<article class="card">.*?</article>', open_repl, body, flags=re.S)
    body = re.sub(r'<article class="resultcard">.*?</article>', result_repl, body, flags=re.S)
    if "</style>" in body:
        body = body.replace("</style>", CSS + "</style>", 1)
    return body


def enhance_desktop(body: str, nonce: str) -> str:
    """V3.32.1 masaüstü runtime'ını değiştirmeden kartları Paylaş ile dekore eder."""
    if "v3329-share-ui" in body or 'id="page-home"' not in body:
        return body
    if "</style>" in body:
        body = body.replace("</style>", CSS + "</style>", 1)
    script = r'''<script nonce="__NONCE__" id="v3329-share-ui">(()=>{'use strict';if(window.__v3329Share)return;window.__v3329Share=1;const sy=r=>String(r?.system_label||r?.system||r?.source||'Sistem'),di=r=>String(r?.direction||'').toUpperCase(),ou=r=>String(r?.outcome||r?.result||r?.final_result||'KAPALI').toUpperCase();function href(r,k,st){const p=new URLSearchParams({kind:k,stage:st,symbol:String(r?.symbol||'').replace('/USDT:USDT','USDT').replaceAll('/','').toUpperCase(),direction:di(r),system:sy(r)});const e=Number(r?.entry);if(Number.isFinite(e))p.set('entry',String(e));if(k==='result')p.set('outcome',ou(r));for(const x of ['trade_id','signal_id','id','opened_at','open_time','entry_time','created_at','created_ts','timestamp','closed_at','close_time','ended_at'])if(r?.[x]!=null&&String(r[x])!==''){p.set('stamp',String(r[x]));break}return '/share/trade?'+p}function add(el,r,k,st){if(!r||el.querySelector('.share-action'))return;const a=document.createElement('a');a.className='share-action';a.href=href(r,k,st);a.textContent='↗ Paylaş';el.appendChild(a)}function go(d){const o=Array.isArray(d?.open_trades)?d.open_trades:[],z=Array.isArray(d?.recent_results)?d.recent_results:[];document.querySelectorAll('.row-card,.wide-card').forEach(el=>{const s=el.querySelector('.coin strong')?.textContent?.trim().toUpperCase(),y=el.querySelector('.coin small')?.textContent?.trim();add(el,o.find(r=>String(r.symbol||'').toUpperCase()===s&&(!y||sy(r)===y)),'open',el.classList.contains('wide-card')?'tracking':'signal')});document.querySelectorAll('.result-item').forEach(el=>{const t=el.querySelector('.result-main strong')?.textContent||'',s=t.split('·')[0].trim().toUpperCase(),x=el.querySelector('.tag')?.textContent?.trim().toUpperCase();add(el,z.find(r=>String(r.symbol||'').toUpperCase()===s&&(!x||ou(r)===x)),'result','result')})}window.addEventListener('kripto-dashboard-data',e=>{setTimeout(()=>go(e.detail),0);setTimeout(()=>go(e.detail),120)});if(window.__kriptoDashboardData)setTimeout(()=>go(window.__kriptoDashboardData),0)})();</script>'''.replace("__NONCE__", html.escape(str(nonce or ""), quote=True))
    return body.replace("</body>", script + "</body>", 1) if "</body>" in body else body
