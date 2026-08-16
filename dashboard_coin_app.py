"""Kripto Kontrol Merkezi V3.13 - Coin İnceleme Merkezi.

V3.12 profesyonel paneli korur ve Premium/Admin için tek coin odaklı inceleme sayfası ekler.
Sayfa mevcut OKX public market API'lerini ve panelin zaten ürettiği işlem kayıtlarını kullanır.

Sinyal, strateji, radar, Telegram, emir ve TP/SL davranışı değiştirilmez.
Yeni periyodik GitHub Actions işi eklenmez.
"""
from __future__ import annotations

import argparse
import html
import math
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
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
import dashboard_ux_app as ux
from dashboard_live_app import LoginRateLimiter, OKXMarketDataClient, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_13_COIN_CENTER_2026_08_16"
MAX_RESULTS = 40
MAX_SYMBOLS = 60


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(row: dict[str, Any]) -> int:
    for key in ("closed_at", "finalized_at", "updated_at", "opened_at", "sent_at", "created_at", "detected_at"):
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            value = float(raw)
            if value > 1e12:
                value /= 1000
            return max(0, int(value))
        except (TypeError, ValueError):
            pass
    return 0


def _outcome(row: dict[str, Any]) -> str:
    return str(row.get("outcome") or row.get("result") or row.get("final_result") or "").upper()


def _system(row: dict[str, Any]) -> str:
    return str(row.get("system_label") or row.get("system") or row.get("source") or "Sistem")[:80]


def _symbol(row: dict[str, Any]) -> str:
    try:
        return OKXMarketDataClient.normalize_symbol(str(row.get("symbol") or ""))
    except ValueError:
        return ""


def _safe_open(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "symbol": _symbol(row),
        "direction": str(row.get("direction") or "").upper(),
        "system": _system(row),
        "opened_at": _timestamp(row),
    }
    for key in ("entry", "tp1", "tp2", "tp3", "sl", "score", "signal_score", "quality_score"):
        value = _number(row.get(key))
        if value is not None:
            result[key] = value
    return result


def _safe_result(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "symbol": _symbol(row),
        "direction": str(row.get("direction") or "").upper(),
        "system": _system(row),
        "outcome": _outcome(row),
        "closed_at": _timestamp(row),
    }
    for key in ("entry", "tp1", "tp2", "tp3", "sl", "net_r", "r_multiple", "realized_r"):
        value = _number(row.get(key))
        if value is not None:
            result[key] = value
    return result


def build_coin_summary(data: dict[str, Any], symbol_value: str) -> dict[str, Any]:
    symbol = OKXMarketDataClient.normalize_symbol(symbol_value)
    open_rows = data.get("open_trades") if isinstance(data.get("open_trades"), list) else []
    result_rows = data.get("recent_results") if isinstance(data.get("recent_results"), list) else []

    open_items = [_safe_open(row) for row in open_rows if isinstance(row, dict) and _symbol(row) == symbol]
    result_items = [_safe_result(row) for row in result_rows if isinstance(row, dict) and _symbol(row) == symbol]
    open_items.sort(key=lambda row: int(row.get("opened_at") or 0), reverse=True)
    result_items.sort(key=lambda row: int(row.get("closed_at") or 0), reverse=True)
    result_items = result_items[:MAX_RESULTS]

    tp = sum(1 for row in result_items if str(row.get("outcome") or "").startswith("TP"))
    sl = sum(1 for row in result_items if str(row.get("outcome") or "") == "SL" or str(row.get("outcome") or "").startswith("SL_"))
    be = sum(1 for row in result_items if "BE" in str(row.get("outcome") or ""))
    decided = tp + sl
    tp_rate = round(tp * 100 / decided, 1) if decided else None

    net_values: list[float] = []
    for row in result_items:
        for key in ("net_r", "r_multiple", "realized_r"):
            value = _number(row.get(key))
            if value is not None:
                net_values.append(value)
                break

    systems: dict[str, dict[str, Any]] = {}
    for row in result_items:
        name = str(row.get("system") or "Sistem")
        bucket = systems.setdefault(name, {"system": name, "count": 0, "tp": 0, "sl": 0, "be": 0})
        bucket["count"] += 1
        out = str(row.get("outcome") or "")
        if out.startswith("TP"):
            bucket["tp"] += 1
        elif out == "SL" or out.startswith("SL_"):
            bucket["sl"] += 1
        elif "BE" in out:
            bucket["be"] += 1

    candidates: list[str] = []
    seen: set[str] = set()
    for row in [*open_rows, *result_rows]:
        if not isinstance(row, dict):
            continue
        item = _symbol(row)
        if item and item not in seen:
            seen.add(item)
            candidates.append(item)
        if len(candidates) >= MAX_SYMBOLS:
            break
    for default in market.DEFAULT_MARKET_SYMBOLS:
        if default not in seen:
            seen.add(default)
            candidates.append(default)

    return {
        "version": VERSION,
        "symbol": symbol,
        "open_trades": open_items,
        "results": result_items,
        "performance": {
            "sample": len(result_items),
            "tp": tp,
            "sl": sl,
            "be": be,
            "tp_rate_percent": tp_rate,
            "net_r": round(sum(net_values), 4) if net_values else None,
            "net_r_sample": len(net_values),
        },
        "systems": sorted(systems.values(), key=lambda row: (-int(row["count"]), str(row["system"])))[:8],
        "available_symbols": candidates[:MAX_SYMBOLS],
        "generated_at": int(time.time()),
        "note": "Coin performansı panelde bulunan gerçek kapanış kayıtlarının coin bazlı özetidir; gelecek performansı garanti etmez.",
    }


COIN_PAGE = r'''<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>Coin İnceleme Merkezi · __INITIAL__</title>
<style>
:root{--bg:#061016;--panel:#0a1921;--line:#1b3943;--text:#eef8f6;--muted:#7f9d99;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59;--blue:#69a9ff}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:radial-gradient(circle at 82% -10%,rgba(44,230,191,.13),transparent 28%),radial-gradient(circle at 20% 4%,rgba(105,169,255,.08),transparent 23%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;min-height:100vh}a{color:inherit;text-decoration:none}button,input{font:inherit}button{cursor:pointer}.shell{width:min(1380px,calc(100% - 24px));margin:auto;padding:20px 0 70px}.top{position:sticky;top:0;z-index:30;margin:0 -2px 16px;padding:10px 2px;background:rgba(6,16,22,.86);backdrop-filter:blur(18px);display:flex;align-items:center;gap:10px}.back,.bar-btn,.fav{border:1px solid var(--line);background:#091821;color:#9db6b2;border-radius:10px;min-height:40px;padding:8px 11px;font-weight:850}.back:hover,.bar-btn:hover,.fav:hover{border-color:var(--teal);color:var(--teal)}.search{display:flex;gap:7px;flex:1;max-width:560px}.search input{min-width:0;flex:1;border:1px solid var(--line);background:#07141c;color:var(--text);border-radius:11px;padding:10px 12px;outline:none}.search input:focus{border-color:var(--teal);box-shadow:0 0 0 3px rgba(44,230,191,.08)}.primary{border:0;background:var(--teal);color:#03120e;border-radius:10px;padding:8px 13px;font-weight:950;min-height:40px}.spacer{flex:1}.status{color:#67827f;font-size:9px;white-space:nowrap}.hero{border:1px solid rgba(44,230,191,.2);background:linear-gradient(135deg,rgba(13,34,43,.98),rgba(7,20,27,.96));border-radius:20px;padding:18px;display:grid;grid-template-columns:1.1fr .9fr;gap:18px;box-shadow:0 18px 60px rgba(0,0,0,.17)}.hero-main{display:flex;align-items:flex-start;gap:14px}.coin-mark{width:52px;height:52px;border-radius:15px;display:grid;place-items:center;background:rgba(44,230,191,.09);border:1px solid rgba(44,230,191,.22);color:var(--teal);font-weight:950;font-size:14px}.hero h1{margin:0;font-size:29px;letter-spacing:-.045em}.sub{color:var(--muted);font-size:10px}.price{font-size:27px;font-weight:950;letter-spacing:-.04em;margin-top:7px}.change{font-size:11px;font-weight:900;margin-left:8px}.up{color:var(--green)}.down{color:var(--red)}.hero-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;align-self:center}.stat{background:rgba(3,14,19,.36);border:1px solid rgba(40,75,86,.55);border-radius:11px;padding:10px}.stat small{display:block;color:#6d8986;font-size:8px;text-transform:uppercase;font-weight:850;letter-spacing:.06em}.stat b{display:block;font-size:14px;margin-top:3px}.layout{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(300px,.65fr);gap:13px;margin-top:13px}.panel{border:1px solid var(--line);background:rgba(10,25,33,.96);border-radius:16px;overflow:hidden;box-shadow:0 11px 38px rgba(0,0,0,.1)}.panel-head{display:flex;justify-content:space-between;align-items:center;gap:10px;padding:13px 14px;border-bottom:1px solid var(--line)}.panel-head h2{margin:0;font-size:14px}.panel-head small{color:var(--muted)}.panel-body{padding:12px}.bars{display:flex;gap:5px;flex-wrap:wrap}.bar-btn{min-height:32px;padding:5px 8px;font-size:9px}.bar-btn.active{background:rgba(44,230,191,.1);border-color:rgba(44,230,191,.4);color:var(--teal)}.chart-wrap{height:470px;position:relative}.chart-wrap canvas{width:100%;height:100%;display:block}.chart-tip{display:none;position:absolute;pointer-events:none;background:rgba(4,14,20,.95);border:1px solid #2b4a55;border-radius:9px;padding:7px 8px;font-size:9px;color:#b7cdca;box-shadow:0 8px 24px rgba(0,0,0,.25);z-index:5}.chart-foot{display:flex;justify-content:space-between;gap:10px;color:#688580;font-size:9px;margin-top:7px}.score-wrap{display:grid;grid-template-columns:auto 1fr;gap:13px;align-items:center}.score-ring{width:82px;height:82px;border-radius:50%;display:grid;place-items:center;background:conic-gradient(var(--teal) var(--score,0%),#132a32 0);position:relative}.score-ring:after{content:"";position:absolute;inset:8px;border-radius:50%;background:#091820}.score-ring b{position:relative;z-index:1;font-size:21px}.score-copy strong{font-size:13px}.score-copy p{margin:4px 0 0;color:var(--muted);font-size:9px}.metrics{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:11px}.mini{background:#07151c;border-radius:9px;padding:8px}.mini small{display:block;color:#607c78;font-size:8px}.mini b{font-size:10px}.open-card{border:1px solid rgba(105,169,255,.2);background:rgba(105,169,255,.045);border-radius:12px;padding:11px;margin-bottom:8px}.open-top{display:flex;justify-content:space-between;gap:8px}.direction{border-radius:999px;padding:3px 7px;font-size:8px;font-weight:950}.direction.long{color:var(--green);border:1px solid rgba(66,226,140,.28)}.direction.short{color:var(--red);border:1px solid rgba(255,98,125,.28)}.levels{display:grid;grid-template-columns:repeat(3,1fr);gap:5px;margin-top:9px}.level{background:#06141b;border-radius:8px;padding:6px}.level small{display:block;color:#58736f;font-size:7px}.level b{font-size:9px}.empty{padding:22px;text-align:center;color:var(--muted)}.perf-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.perf{border:1px solid var(--line);background:#08171f;border-radius:11px;padding:10px}.perf small{display:block;color:#698581;font-size:8px;text-transform:uppercase}.perf b{display:block;font-size:16px;margin-top:2px}.perf.green b{color:var(--green)}.perf.red b{color:var(--red)}.result-list{display:flex;flex-direction:column}.result{display:grid;grid-template-columns:minmax(130px,1fr) .7fr .7fr auto;gap:9px;align-items:center;padding:10px 2px;border-bottom:1px solid rgba(27,57,67,.72)}.result:last-child{border-bottom:0}.result strong{font-size:10px}.result small{display:block;color:var(--muted);font-size:8px}.tag{border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:8px;font-weight:900}.tag.tp{color:var(--green);border-color:rgba(66,226,140,.28)}.tag.sl{color:var(--red);border-color:rgba(255,98,125,.28)}.tag.be{color:var(--amber)}.system-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:7px}.system-card{border:1px solid var(--line);background:#07151c;border-radius:10px;padding:9px}.system-card b{font-size:10px}.system-card small{display:block;color:var(--muted);font-size:8px}.note{margin-top:13px;color:#617f7b;font-size:9px}.fav.on{color:var(--amber);border-color:rgba(255,189,89,.35)}@media(max-width:980px){.hero{grid-template-columns:1fr}.layout{grid-template-columns:1fr}.hero-stats{grid-template-columns:repeat(4,1fr)}.chart-wrap{height:400px}}@media(max-width:680px){body{padding-bottom:env(safe-area-inset-bottom)}.shell{width:calc(100% - 14px);padding-top:7px}.top{gap:6px;flex-wrap:wrap}.back{padding:7px 9px}.search{order:3;flex-basis:100%;max-width:none}.search input,.primary{min-height:43px}.status{display:none}.hero{padding:13px;border-radius:15px;gap:12px}.coin-mark{width:43px;height:43px;border-radius:12px}.hero h1{font-size:23px}.price{font-size:22px}.hero-stats{display:flex;overflow:auto;gap:6px;padding-bottom:3px}.stat{flex:0 0 118px}.panel{border-radius:13px}.panel-head{padding:11px}.panel-body{padding:9px}.chart-wrap{height:340px}.perf-grid{display:flex;overflow:auto}.perf{flex:0 0 112px}.result{grid-template-columns:1fr auto}.result .hide-mobile{display:none}.levels{grid-template-columns:repeat(2,1fr)}}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}
</style></head><body><div class="shell"><div class="top"><a class="back" href="/">← Panel</a><div class="search"><input id="symbolInput" value="__INITIAL__" autocomplete="off" placeholder="BTCUSDT, ETHUSDT..."><button class="primary" id="loadBtn">İncele</button></div><div class="spacer"></div><button class="fav" id="favBtn" title="Favoriye ekle">☆ Favori</button><span class="status" id="status">Hazırlanıyor…</span></div>
<section class="hero"><div class="hero-main"><div class="coin-mark" id="coinMark">—</div><div><div class="sub">COIN İNCELEME MERKEZİ · OKX PUBLIC</div><h1 id="coinTitle">__INITIAL__</h1><div><span class="price" id="lastPrice">—</span><span class="change" id="change24">—</span></div><div class="sub" id="coinContext">Gerçek panel kayıtları ve canlı piyasa verisi birlikte gösterilir.</div></div></div><div class="hero-stats"><div class="stat"><small>24s yüksek</small><b id="high24">—</b></div><div class="stat"><small>24s düşük</small><b id="low24">—</b></div><div class="stat"><small>24s hacim</small><b id="vol24">—</b></div><div class="stat"><small>Sonuç örneği</small><b id="sampleCount">—</b></div></div></section>
<div class="layout"><section class="panel"><div class="panel-head"><div><h2>Canlı mum grafiği</h2><small id="chartLabel">__INITIAL__ · 15m</small></div><div class="bars" id="bars"><button class="bar-btn" data-bar="5m">5m</button><button class="bar-btn active" data-bar="15m">15m</button><button class="bar-btn" data-bar="1H">1H</button><button class="bar-btn" data-bar="4H">4H</button><button class="bar-btn" data-bar="1D">1D</button></div></div><div class="panel-body"><div class="chart-wrap"><canvas id="chart"></canvas><div class="chart-tip" id="chartTip"></div></div><div class="chart-foot"><span id="chartInfo">Grafik yükleniyor…</span><span>Salt okunur · emir açmaz</span></div></div></section><div><section class="panel"><div class="panel-head"><div><h2>Teknik görünüm</h2><small>İnceleme skoru · olasılık değildir</small></div></div><div class="panel-body"><div class="score-wrap"><div class="score-ring" id="scoreRing"><b id="scoreValue">—</b></div><div class="score-copy"><strong id="scoreBand">Hesaplanıyor…</strong><p id="scoreDirection">15m + 1H trend, RSI, hacim ve momentum</p></div></div><div class="metrics"><div class="mini"><small>15m trend</small><b id="trend15">—</b></div><div class="mini"><small>1H trend</small><b id="trend1h">—</b></div><div class="mini"><small>RSI 15m</small><b id="rsi15">—</b></div><div class="mini"><small>RSI 1H</small><b id="rsi1h">—</b></div><div class="mini"><small>Hacim oranı</small><b id="volumeRatio">—</b></div><div class="mini"><small>24s momentum</small><b id="momentum">—</b></div></div></div></section><section class="panel" style="margin-top:13px"><div class="panel-head"><div><h2>Açık teknik senaryolar</h2><small>Bu coin için panelde takipte olan kayıtlar</small></div></div><div class="panel-body" id="openTrades"><div class="empty">Yükleniyor…</div></div></section></div></div>
<section class="panel" style="margin-top:13px"><div class="panel-head"><div><h2>Coin performansı</h2><small>Paneldeki gerçek kapanış kayıtları üzerinden</small></div></div><div class="panel-body"><div class="perf-grid"><div class="perf"><small>Örnek</small><b id="perfSample">—</b></div><div class="perf green"><small>TP</small><b id="perfTp">—</b></div><div class="perf red"><small>SL</small><b id="perfSl">—</b></div><div class="perf"><small>TP oranı</small><b id="perfRate">—</b></div><div class="perf"><small>Net R</small><b id="perfR">—</b></div></div></div></section><div class="layout"><section class="panel"><div class="panel-head"><div><h2>Bu coin'in son sonuçları</h2><small id="resultCount">—</small></div></div><div class="panel-body result-list" id="results"><div class="empty">Yükleniyor…</div></div></section><section class="panel"><div class="panel-head"><div><h2>Sistem bazlı görünüm</h2><small>Aynı coin farklı sistemlerde nasıl sonuçlandı?</small></div></div><div class="panel-body"><div class="system-grid" id="systems"><div class="empty">Yükleniyor…</div></div></div></section></div><div class="note">Teknik skor ve piyasa verileri inceleme amaçlıdır; işlem emri veya kazanç garantisi değildir. Coin performansı yalnız panelde mevcut geçmiş kayıtların özetidir.</div></div>
<script nonce="__NONCE__">(()=>{'use strict';const $=id=>document.getElementById(id);const state={symbol:'__INITIAL__',bar:'15m',candles:null,token:0};const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};const price=v=>{const n=num(v);if(n===null)return '—';if(Math.abs(n)>=1000)return n.toLocaleString('tr-TR',{maximumFractionDigits:2});if(Math.abs(n)>=1)return n.toLocaleString('tr-TR',{maximumFractionDigits:5});return n.toLocaleString('tr-TR',{maximumFractionDigits:9})};const compact=v=>{const n=num(v);if(n===null)return '—';return Intl.NumberFormat('tr-TR',{notation:'compact',maximumFractionDigits:1}).format(n)};const pct=v=>{const n=num(v);return n===null?'—':`${n>=0?'+':''}${n.toFixed(2)}%`};const time=v=>{const n=num(v);if(n===null||!n)return '—';return new Date(n*1000).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})};async function getJson(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');throw new Error('Oturum gerekli')}if(r.status===403){location.assign('/premium');throw new Error('Premium gerekli')}const p=await r.json();if(!r.ok)throw new Error(p.message||p.error||`HTTP ${r.status}`);return p}function setStatus(t){$('status').textContent=t}function resultClass(o){o=String(o||'').toUpperCase();return o.startsWith('TP')?'tp':o==='SL'||o.startsWith('SL_')?'sl':o.includes('BE')?'be':''}function renderOverview(p){const item=(p.items||[])[0]||{};$('lastPrice').textContent=price(item.last);$('change24').textContent=pct(item.change_24h_pct);$('change24').className=`change ${num(item.change_24h_pct)>=0?'up':'down'}`;$('high24').textContent=price(item.high_24h);$('low24').textContent=price(item.low_24h);$('vol24').textContent=compact(item.volume_24h)}function renderScore(p){const score=num(p.score);$('scoreValue').textContent=score===null?'—':Math.round(score);$('scoreRing').style.setProperty('--score',`${Math.max(0,Math.min(100,score||0))}%`);$('scoreBand').textContent=p.band||'Skor yok';$('scoreDirection').textContent=`${p.direction||'KARIŞIK'} · teknik uyum göstergesi`;const m=p.metrics||{};$('trend15').textContent=m.trend_15m||'—';$('trend1h').textContent=m.trend_1h||'—';$('rsi15').textContent=m.rsi_15m??'—';$('rsi1h').textContent=m.rsi_1h??'—';$('volumeRatio').textContent=m.volume_ratio_15m==null?'—':`${m.volume_ratio_15m}x`;$('momentum').textContent=pct(m.change_24h_pct)}function renderSummary(p){const perf=p.performance||{};$('sampleCount').textContent=perf.sample??0;$('perfSample').textContent=perf.sample??0;$('perfTp').textContent=perf.tp??0;$('perfSl').textContent=perf.sl??0;$('perfRate').textContent=perf.tp_rate_percent==null?'—':`%${perf.tp_rate_percent}`;$('perfR').textContent=perf.net_r==null?'—':`${perf.net_r>=0?'+':''}${perf.net_r.toFixed(2)}R`;$('coinContext').textContent=(p.open_trades||[]).length?`${p.open_trades.length} açık teknik senaryo takipte · ${perf.sample||0} kapanış kaydı`:`Açık teknik senaryo yok · ${perf.sample||0} kapanış kaydı`;const opens=p.open_trades||[];$('openTrades').innerHTML=opens.map(r=>`<div class="open-card"><div class="open-top"><div><b>${esc(r.system||'Sistem')}</b><small style="display:block;color:var(--muted)">${time(r.opened_at)}</small></div><span class="direction ${String(r.direction).toLowerCase()}">${esc(r.direction||'AÇIK')}</span></div><div class="levels"><div class="level"><small>Giriş</small><b>${price(r.entry)}</b></div><div class="level"><small>TP1</small><b>${price(r.tp1)}</b></div><div class="level"><small>SL</small><b>${price(r.sl)}</b></div><div class="level"><small>TP2</small><b>${price(r.tp2)}</b></div><div class="level"><small>TP3</small><b>${price(r.tp3)}</b></div><div class="level"><small>Skor</small><b>${r.score??r.signal_score??r.quality_score??'—'}</b></div></div></div>`).join('')||'<div class="empty">Bu coin için açık teknik senaryo yok.</div>';const rows=p.results||[];$('resultCount').textContent=`${rows.length} kayıt`;$('results').innerHTML=rows.map(r=>`<div class="result"><div><strong>${esc(r.system||'Sistem')}</strong><small>${esc(r.direction||'')} · ${time(r.closed_at)}</small></div><div class="hide-mobile"><small>Giriş</small><strong>${price(r.entry)}</strong></div><div class="hide-mobile"><small>Net R</small><strong>${r.net_r!=null?`${r.net_r>=0?'+':''}${Number(r.net_r).toFixed(2)}R`:'—'}</strong></div><span class="tag ${resultClass(r.outcome)}">${esc(r.outcome||'KAPALI')}</span></div>`).join('')||'<div class="empty">Bu coin için kapanmış sonuç yok.</div>';const systems=p.systems||[];$('systems').innerHTML=systems.map(s=>`<div class="system-card"><b>${esc(s.system)}</b><small>${s.count} kayıt · ${s.tp} TP · ${s.sl} SL · ${s.be} BE</small></div>`).join('')||'<div class="empty">Sistem bazlı örnek yok.</div>'}function favoriteList(){try{const v=JSON.parse(localStorage.getItem('kripto_focus_favs')||'[]');return Array.isArray(v)?v:[]}catch{return []}}function paintFavorite(){const on=favoriteList().includes(state.symbol);$('favBtn').classList.toggle('on',on);$('favBtn').textContent=on?'★ Favoride':'☆ Favori'}function toggleFavorite(){let list=favoriteList().filter(v=>typeof v==='string');const i=list.indexOf(state.symbol);if(i>=0)list.splice(i,1);else list.unshift(state.symbol);list=[...new Set(list)].slice(0,30);localStorage.setItem('kripto_focus_favs',JSON.stringify(list));paintFavorite()}async function loadChart(token){$('chartInfo').textContent='Grafik yükleniyor…';try{const p=await getJson(`/api/market/candles?symbol=${encodeURIComponent(state.symbol)}&bar=${encodeURIComponent(state.bar)}`);if(token!==state.token)return;state.candles=p;drawChart();$('chartInfo').textContent=`${p.candles?.length||0} mum · Son ${price(p.last_price)} · ${state.bar}`}catch(e){if(token===state.token)$('chartInfo').textContent=`Grafik alınamadı: ${e.message}`}}function drawChart(){const canvas=$('chart'),p=state.candles;if(!canvas||!p||(p.candles||[]).length)return;const candles=p.candles,box=canvas.parentElement.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),w=Math.max(320,box.width),h=Math.max(280,box.height);canvas.width=w*dpr;canvas.height=h*dpr;canvas.style.width=`${w}px`;canvas.style.height=`${h}px`;const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,w,h);const lows=candles.map(c=>Number(c.low)).filter(Number.isFinite),highs=candles.map(c=>Number(c.high)).filter(Number.isFinite),lo=Math.min(...lows),hi=Math.max(...highs),pad=Math.max((hi-lo)*.065,Math.abs(hi)*.001,1e-10),min=lo-pad,max=hi+pad,m={left:12,right:82,top:14,bottom:26},cw=w-m.left-m.right,ch=h-m.top-m.bottom,y=v=>m.top+(max-Number(v))/(max-min)*ch;ctx.font='10px system-ui';ctx.strokeStyle='rgba(126,157,153,.14)';ctx.fillStyle='#6f8d89';for(let i=0;i<=5;i++){const yy=m.top+ch*i/5;ctx.beginPath();ctx.moveTo(m.left,yy);ctx.lineTo(w-m.right,yy);ctx.stroke();ctx.fillText(price(max-(max-min)*i/5),w-m.right+6,yy+4)}const step=cw/candles.length,body=Math.max(2,Math.min(8,step*.62));candles.forEach((c,i)=>{const x=m.left+step*(i+.5),o=y(c.open),cl=y(c.close),hh=y(c.high),ll=y(c.low),up=Number(c.close)>=Number(c.open);ctx.strokeStyle=up?'#42e28c':'#ff627d';ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(x,hh);ctx.lineTo(x,ll);ctx.stroke();ctx.fillRect(x-body/2,Math.min(o,cl),body,Math.max(1,Math.abs(cl-o)))});canvas.__chart={candles,m,cw,ch,w,h,min,max,step,y}}function chartMove(ev){const canvas=$('chart'),meta=canvas.__chart,tip=$('chartTip');if(!meta)return;const r=canvas.getBoundingClientRect(),x=ev.clientX-r.left,index=Math.floor((x-meta.m.left)/meta.step);if(index<0||index>=meta.candles.length){tip.style.display='none';return}const c=meta.candles[index],xx=meta.m.left+meta.step*(index+.5),ctx=canvas.getContext('2d');drawChart();ctx.save();ctx.strokeStyle='rgba(190,217,212,.28)';ctx.setLineDash([3,4]);ctx.beginPath();ctx.moveTo(xx,meta.m.top);ctx.lineTo(xx,meta.h-meta.m.bottom);ctx.stroke();ctx.restore();tip.innerHTML=`<b>${new Date(c.ts*1000).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}</b><br>A ${price(c.open)} · Y ${price(c.high)}<br>D ${price(c.low)} · K ${price(c.close)}`;tip.style.display='block';tip.style.left=`${Math.min(meta.w-160,Math.max(8,xx+10))}px`;tip.style.top='16px'}async function loadAll(value){const symbol=normalize(value);if(!/^[A-Z0-9]{2,15}USDT$/.test(symbol)){setStatus('Coin BTCUSDT biçiminde olmalı');return}state.symbol=symbol;$('symbolInput').value=symbol;$('coinTitle').textContent=symbol;$('coinMark').textContent=symbol.replace('USDT','').slice(0,4);$('chartLabel').textContent=`${symbol} · ${state.bar}`;document.title=`Coin İnceleme · ${symbol}`;history.replaceState(null,'',`/coin-center?symbol=${encodeURIComponent(symbol)}`);paintFavorite();const token=++state.token;setStatus(`${symbol} yükleniyor…`);const jobs=[getJson(`/api/coin-center/summary?symbol=${encodeURIComponent(symbol)}`).then(p=>token===state.token&&renderSummary(p)),getJson(`/api/market/overview?symbols=${encodeURIComponent(symbol)}`).then(p=>token===state.token&&renderOverview(p)),getJson(`/api/market/analysis-score?symbol=${encodeURIComponent(symbol)}`).then(p=>token===state.token&&renderScore(p)),loadChart(token)];const settled=await Promise.allSettled(jobs);if(token!==state.token)return;const failed=settled.filter(x=>x.status==='rejected').length;setStatus(failed?`${symbol} · ${failed} veri bölümü alınamadı`:`${symbol} · canlı`)}$('loadBtn').addEventListener('click',()=>loadAll($('symbolInput').value));$('symbolInput').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();loadAll(e.currentTarget.value)}});$('bars').addEventListener('click',e=>{const b=e.target.closest('[data-bar]');if(!b)return;state.bar=b.dataset.bar;document.querySelectorAll('[data-bar]').forEach(x=>x.classList.toggle('active',x===b));$('chartLabel').textContent=`${state.symbol} · ${state.bar}`;loadChart(++state.token)});$('favBtn').addEventListener('click',toggleFavorite);$('chart').addEventListener('mousemove',chartMove);$('chart').addEventListener('mouseleave',()=>{$('chartTip').style.display='none';drawChart()});window.addEventListener('resize',()=>state.candles&&drawChart());paintFavorite();loadAll(state.symbol)})();</script></body></html>'''


def coin_center_page(nonce: str, initial_symbol: str) -> str:
    try:
        symbol = OKXMarketDataClient.normalize_symbol(initial_symbol)
    except ValueError:
        symbol = "BTCUSDT"
    return COIN_PAGE.replace("__NONCE__", html.escape(nonce, quote=True)).replace("__INITIAL__", html.escape(symbol, quote=True))


def enhance_dashboard_shortcuts(body: str) -> str:
    if 'href="/coin-center' in body:
        return body
    pulse = '<a href="/market-center">Piyasayı incele</a>'
    if pulse in body:
        body = body.replace(pulse, pulse + '<a href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a>', 1)
    nav = '<a class="nav-item" href="/market-center"><span>⌁</span><b>Piyasa</b></a>'
    if nav in body:
        body = body.replace(nav, nav + '<a class="nav-item" href="/coin-center?symbol=BTCUSDT"><span>◇</span><b>Coin Merkezi</b></a>', 1)
    return body


def make_v313_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store: commercial.CommercialAccountStore, market_client=None, overview_client=None):
    BaseHandler = ux.make_v312_handler(config, service, sessions, limiter, store, market_client, overview_client)

    class V313Handler(BaseHandler):
        server_version = "KriptoPanel/3.13"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html") and urllib.parse.urlsplit(self.path).path == "/":
                session = self._session()
                if session and self._is_premium(session):
                    body = enhance_dashboard_shortcuts(body)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION, "coin_center": True, "premium_only": True, "new_api_schedule": False, "signal_engine": "unchanged", "telegram": "unchanged"})
                return
            if path == "/coin-center":
                session = self._session()
                if not session:
                    self._redirect("/login")
                    return
                if not self._is_premium(session):
                    self._redirect("/premium")
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
                symbol = (query.get("symbol") or ["BTCUSDT"])[0]
                nonce = secrets.token_urlsafe(18)
                self._send(HTTPStatus.OK, coin_center_page(nonce, symbol), "text/html; charset=utf-8", nonce=nonce)
                return
            if path == "/api/coin-center/summary":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication_required"})
                    return
                if not self._is_premium(session):
                    self._json(HTTPStatus.FORBIDDEN, {"error": "premium_required", "upgrade": "/premium"})
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
                symbol = (query.get("symbol") or [""])[0]
                try:
                    self._json(HTTPStatus.OK, build_coin_summary(service.get_data(), symbol))
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_symbol", "message": str(exc)})
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "coin_summary_unavailable"})
                return
            return super().do_GET()

    return V313Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.13 Coin İnceleme Merkezi")
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
    server = ThreadingHTTPServer((args.host, args.port), make_v313_handler(config, service, sessions, limiter, store, market_client, overview_client))
    print(f"{VERSION} http://{args.host}:{args.port} coin_center=on premium_only=1 signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
