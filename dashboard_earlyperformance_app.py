"""Kripto Kontrol Merkezi V3.18 - İlk 15 Dakika Performans Analizi.

V3.17 İlk 15 Dakika İşlem Nabzı korunur. Bu sürüm geçmiş kapanmış gerçek panel
kayıtlarını, işlem açılışından sonraki 15 dakikalık 1m public piyasa mumlarıyla
salt-okunur eşleştirir ve sistem bazında başlangıç davranışı istatistikleri üretir.

Canlı sinyal, strateji, radar, Telegram, emir, TP/SL, BE ve state/ledger yazma
mantığı değiştirilmez. Sonuçlar yatırım kararı veya otomatik filtre değildir.
"""
from __future__ import annotations

import argparse
import html
import math
import os
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_earlypulse_app as pulse
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_18_EARLY_PERFORMANCE_2026_08_16"
MAX_PER_SYSTEM = 3
MAX_HISTORY_TRADES = 12
CACHE_SECONDS = 12 * 3600
SYSTEM_ORDER = ("PREMIUM", "SCALP", "PUMP_DUMP", "NEW_LISTING")
SYSTEM_LABELS = {
    "PREMIUM": "Premium MTF",
    "SCALP": "Scalp Radar",
    "PUMP_DUMP": "Pump / Dump",
    "NEW_LISTING": "Yeni Liste",
}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> int:
    number = _number(value)
    if number is None:
        return 0
    if number > 1e12:
        number /= 1000
    return max(0, int(number))


def _valid_history_row(row: dict[str, Any]) -> bool:
    if str(row.get("direction") or "").upper() not in {"LONG", "SHORT"}:
        return False
    if not str(row.get("symbol") or "").upper().endswith("USDT"):
        return False
    opened_at = _timestamp(row.get("opened_at"))
    entry = _number(row.get("entry"))
    sl = _number(row.get("sl"))
    return bool(opened_at and entry is not None and sl is not None and entry != sl)


def select_history_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Select recent history in a system-balanced way without changing source data."""
    rows = data.get("recent_results") if isinstance(data.get("recent_results"), list) else []
    selected: list[dict[str, Any]] = []
    counts: dict[str, int] = {key: 0 for key in SYSTEM_ORDER}
    for raw in rows:
        if not isinstance(raw, dict) or not _valid_history_row(raw):
            continue
        system = str(raw.get("system") or "").upper()
        if system not in counts or counts[system] >= MAX_PER_SYSTEM:
            continue
        selected.append(dict(raw))
        counts[system] += 1
        if len(selected) >= MAX_HISTORY_TRADES:
            break
    selected.sort(key=lambda row: (_timestamp(row.get("opened_at")), str(row.get("id") or "")), reverse=True)
    return selected


class HistoricalPulseCache:
    def __init__(self, ttl_seconds: int = CACHE_SECONDS):
        self.ttl_seconds = max(300, int(ttl_seconds))
        self._items: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(row: dict[str, Any]) -> str:
        return ":".join((
            str(row.get("system") or ""),
            str(row.get("id") or row.get("symbol") or ""),
            str(_timestamp(row.get("opened_at"))),
        ))

    def get(self, row: dict[str, Any]) -> dict[str, Any] | None:
        key = self.key(row)
        now = time.monotonic()
        with self._lock:
            cached = self._items.get(key)
            if not cached:
                return None
            if now - cached[0] > self.ttl_seconds:
                self._items.pop(key, None)
                return None
            return dict(cached[1])

    def put(self, row: dict[str, Any], value: dict[str, Any]) -> None:
        with self._lock:
            self._items[self.key(row)] = (time.monotonic(), dict(value))


def analyze_history_row(row: dict[str, Any], market_client) -> dict[str, Any]:
    opened_at = _timestamp(row.get("opened_at"))
    payload = market_client.get_candles(str(row.get("symbol") or ""), "1m", opened_at)
    result = pulse.analyze_first_15m(
        row,
        payload.get("candles") if isinstance(payload.get("candles"), list) else [],
        now_ts=opened_at + pulse.WINDOW_SECONDS,
        source=str(payload.get("source") or "PUBLIC_1M"),
    )
    result.update({
        "id": str(row.get("id") or ""),
        "symbol": str(row.get("symbol") or ""),
        "system": str(row.get("system") or ""),
        "system_label": str(row.get("system_label") or SYSTEM_LABELS.get(str(row.get("system") or ""), "Sistem")),
        "outcome": str(row.get("outcome") or ""),
        "closed_at": _timestamp(row.get("closed_at")),
    })
    return result


def _average(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [_number(row.get(key)) for row in rows]
    clean = [value for value in values if value is not None]
    return round(sum(clean) / len(clean), 4) if clean else None


def summarize_pulses(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [row for row in rows if row.get("status") == "ok"]
    events = [str(row.get("first_event") or "NONE") for row in ok]
    positive = sum(1 for row in ok if (_number(row.get("window_close_r")) or 0) > 0)
    complete = sum(1 for row in ok if row.get("data_quality") == "COMPLETE")
    return {
        "sample": len(ok),
        "requested": len(rows),
        "tp1_first": events.count("TP1_FIRST"),
        "sl_first": events.count("SL_FIRST"),
        "same_candle": events.count("TP1_SL_SAME_CANDLE"),
        "no_touch": events.count("NONE"),
        "tp1_first_rate": round(events.count("TP1_FIRST") * 100 / len(ok), 1) if ok else None,
        "sl_first_rate": round(events.count("SL_FIRST") * 100 / len(ok), 1) if ok else None,
        "positive_close_rate": round(positive * 100 / len(ok), 1) if ok else None,
        "average_mfe_r": _average(ok, "mfe_r"),
        "average_mae_r": _average(ok, "mae_r"),
        "average_close_r": _average(ok, "window_close_r"),
        "complete_data": complete,
        "partial_data": len(ok) - complete,
        "insufficient_sample": len(ok) < 3,
    }


def build_history_payload(data: dict[str, Any], market_client, cache: HistoricalPulseCache) -> dict[str, Any]:
    candidates = select_history_rows(data)
    results: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for row in candidates:
        cached = cache.get(row)
        if cached is not None:
            results.append(cached)
        else:
            pending.append(row)

    if pending:
        with ThreadPoolExecutor(max_workers=min(3, len(pending))) as executor:
            futures = {executor.submit(analyze_history_row, row, market_client): row for row in pending}
            for future in as_completed(futures):
                row = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "status": "unavailable",
                        "id": str(row.get("id") or ""),
                        "symbol": str(row.get("symbol") or ""),
                        "system": str(row.get("system") or ""),
                        "system_label": str(row.get("system_label") or SYSTEM_LABELS.get(str(row.get("system") or ""), "Sistem")),
                        "outcome": str(row.get("outcome") or ""),
                        "opened_at": _timestamp(row.get("opened_at")),
                        "error_type": type(exc).__name__,
                    }
                if result.get("status") == "ok":
                    cache.put(row, result)
                results.append(result)

    results.sort(key=lambda row: (_timestamp(row.get("opened_at")), str(row.get("id") or "")), reverse=True)
    system_rows: list[dict[str, Any]] = []
    for system in SYSTEM_ORDER:
        subset = [row for row in results if str(row.get("system") or "").upper() == system]
        summary = summarize_pulses(subset)
        summary.update({"system": system, "label": SYSTEM_LABELS[system]})
        system_rows.append(summary)

    return {
        "version": VERSION,
        "generated_at": int(time.time()),
        "overall": summarize_pulses(results),
        "systems": system_rows,
        "samples": results,
        "candidate_count": len(candidates),
        "note": "İlk 15 dakika istatistikleri geçmiş piyasa mumlarından salt-okunur üretilir; canlı filtre, sinyal veya işlem yönetimi kuralı değildir.",
        "ambiguity_note": "TP1 ve SL aynı 1 dakikalık mum içinde görülürse sıra belirsiz kabul edilir.",
    }


PAGE = r'''<!doctype html><html lang="tr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"><meta name="color-scheme" content="dark"><title>İlk 15 Dakika Performans Analizi</title><style>
:root{--bg:#061016;--panel:#0a1921;--line:#1b3943;--text:#eef8f6;--muted:#7f9d99;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59;--blue:#69a9ff}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 85% -10%,rgba(44,230,191,.12),transparent 29%),var(--bg);color:var(--text);font:13px/1.5 Inter,system-ui,-apple-system,"Segoe UI",sans-serif}.shell{width:min(1280px,calc(100% - 24px));margin:auto;padding:20px 0 70px}.top{display:flex;align-items:center;gap:8px;margin-bottom:16px}.btn{border:1px solid var(--line);background:#091821;color:#9db6b2;border-radius:10px;padding:9px 11px;font-weight:850;text-decoration:none}.btn:hover{border-color:var(--teal);color:var(--teal)}.spacer{flex:1}.hero{border:1px solid rgba(44,230,191,.21);background:linear-gradient(135deg,rgba(12,33,42,.98),rgba(7,20,27,.97));border-radius:20px;padding:18px}.eyebrow{font-size:9px;color:var(--teal);font-weight:900;letter-spacing:.08em}.hero h1{margin:4px 0 5px;font-size:28px;letter-spacing:-.04em}.hero p{margin:0;color:var(--muted);max-width:850px}.grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;margin-top:13px}.metric,.card,.panel{border:1px solid var(--line);background:rgba(10,25,33,.96);border-radius:14px}.metric{padding:11px}.metric small{display:block;color:#6d8986;font-size:8px;text-transform:uppercase}.metric b{display:block;font-size:17px;margin-top:3px}.systems{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:9px;margin-top:13px}.card{padding:12px}.card h3{font-size:12px;margin:0 0 7px}.card .sample{color:var(--muted);font-size:8px}.rows{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px}.mini{background:#07151c;border-radius:9px;padding:7px}.mini small{display:block;color:#617d79;font-size:7px}.mini b{font-size:10px}.warn{margin-top:7px;color:var(--amber);font-size:8px}.panel{margin-top:13px;overflow:hidden}.panel-head{padding:12px 14px;border-bottom:1px solid var(--line);display:flex;justify-content:space-between;gap:10px}.panel-head h2{font-size:13px;margin:0}.panel-head small{color:var(--muted)}.list{padding:0 12px}.item{display:grid;grid-template-columns:1.2fr .7fr .7fr .7fr .7fr auto;gap:8px;align-items:center;padding:10px 2px;border-bottom:1px solid rgba(27,57,67,.72)}.item:last-child{border-bottom:0}.item strong{font-size:10px}.item small{display:block;color:var(--muted);font-size:8px}.tag{border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:8px;font-weight:900}.pos{color:var(--green)}.neg{color:var(--red)}.amber{color:var(--amber)}.empty{padding:24px;color:var(--muted);text-align:center}.note{margin-top:12px;color:#617f7b;font-size:9px}@media(max-width:1000px){.grid{grid-template-columns:repeat(3,1fr)}.systems{grid-template-columns:1fr 1fr}.item{grid-template-columns:1fr 1fr 1fr}.hide-tablet{display:none}}@media(max-width:620px){.shell{width:calc(100% - 14px);padding-top:8px}.hero{padding:13px;border-radius:15px}.hero h1{font-size:22px}.grid{display:flex;overflow:auto}.metric{flex:0 0 128px}.systems{grid-template-columns:1fr}.item{grid-template-columns:1fr auto}.hide-mobile{display:none}}
</style></head><body><div class="shell"><div class="top"><a class="btn" href="/">← Panel</a><a class="btn" href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a><div class="spacer"></div><button class="btn" id="refreshBtn">Yenile</button></div><section class="hero"><div class="eyebrow">V3.18 · SALT OKUNUR GEÇMİŞ ANALİZİ</div><h1>İlk 15 Dakika Performans Analizi</h1><p>Geçmiş kapanmış gerçek panel kayıtlarının işlem açılışından sonraki ilk 15 dakikasını 1 dakikalık public piyasa mumlarıyla karşılaştırır. Bu ekran canlı filtre veya işlem yönetimi kuralı üretmez.</p></section><div class="grid"><div class="metric"><small>Ölçülen örnek</small><b id="sample">—</b></div><div class="metric"><small>TP1 önce</small><b id="tpFirst">—</b></div><div class="metric"><small>SL önce</small><b id="slFirst">—</b></div><div class="metric"><small>Ort. MFE</small><b id="mfe">—</b></div><div class="metric"><small>Ort. MAE</small><b id="mae">—</b></div><div class="metric"><small>Ort. 15dk R</small><b id="closeR">—</b></div></div><div class="systems" id="systems"><div class="empty">Analiz hazırlanıyor…</div></div><section class="panel"><div class="panel-head"><div><h2>Son ölçülen işlemler</h2><small>En fazla sistem başına 3, toplam 12 geçmiş kayıt</small></div><small id="status">Hazırlanıyor…</small></div><div class="list" id="samples"><div class="empty">Piyasa mumları eşleştiriliyor…</div></div></section><div class="note" id="note">Aynı 1 dakikalık mum içinde TP1 ve SL birlikte görülürse olay sırası kesinleştirilmez.</div></div><script nonce="__NONCE__">(()=>{'use strict';const $=id=>document.getElementById(id);const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};const r=v=>{const n=num(v);return n===null?'—':`${n>=0?'+':''}${n.toFixed(2)}R`};const pct=v=>{const n=num(v);return n===null?'—':`%${n.toFixed(1)}`};const esc=v=>String(v??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));const when=v=>{const n=num(v);return n?new Date(n*1000).toLocaleString('tr-TR',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}):'—'};function tone(v){const n=num(v);return n===null?'':n>0?'pos':n<0?'neg':''}function event(v){return ({TP1_FIRST:'TP1 önce',SL_FIRST:'SL önce',TP1_SL_SAME_CANDLE:'Aynı 1m mum',NONE:'Temas yok'})[v]||'—'}async function get(){const res=await fetch(`/api/early-performance?v=${Date.now()}`,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(res.status===401){location.assign('/login');throw new Error('Oturum gerekli')}if(res.status===403){location.assign('/premium');throw new Error('Premium gerekli')}const p=await res.json();if(!res.ok)throw new Error(p.message||p.error||`HTTP ${res.status}`);return p}function render(p){const o=p.overall||{};$('sample').textContent=o.sample??0;$('tpFirst').textContent=`${o.tp1_first??0} · ${pct(o.tp1_first_rate)}`;$('slFirst').textContent=`${o.sl_first??0} · ${pct(o.sl_first_rate)}`;$('mfe').textContent=r(o.average_mfe_r);$('mfe').className=tone(o.average_mfe_r);$('mae').textContent=r(o.average_mae_r);$('mae').className=tone(o.average_mae_r);$('closeR').textContent=r(o.average_close_r);$('closeR').className=tone(o.average_close_r);$('systems').innerHTML=(p.systems||[]).map(s=>`<div class="card"><h3>${esc(s.label)}</h3><div class="sample">${s.sample} ölçülen / ${s.requested} aday</div><div class="rows"><div class="mini"><small>TP1 önce</small><b>${s.tp1_first} · ${pct(s.tp1_first_rate)}</b></div><div class="mini"><small>SL önce</small><b>${s.sl_first} · ${pct(s.sl_first_rate)}</b></div><div class="mini"><small>Ort. MFE</small><b class="${tone(s.average_mfe_r)}">${r(s.average_mfe_r)}</b></div><div class="mini"><small>Ort. MAE</small><b class="${tone(s.average_mae_r)}">${r(s.average_mae_r)}</b></div><div class="mini"><small>Ort. 15dk R</small><b class="${tone(s.average_close_r)}">${r(s.average_close_r)}</b></div><div class="mini"><small>Pozitif 15dk</small><b>${pct(s.positive_close_rate)}</b></div></div>${s.insufficient_sample?'<div class="warn">Örnek az; yorum için birikmeye devam etmeli.</div>':''}</div>`).join('');const rows=(p.samples||[]);$('samples').innerHTML=rows.map(x=>x.status==='ok'?`<div class="item"><div><strong>${esc(x.symbol)}</strong><small>${esc(x.system_label)} · ${esc(x.direction)} · ${when(x.opened_at)}</small></div><div class="hide-mobile"><small>Sonuç</small><strong>${esc(x.outcome)}</strong></div><div class="hide-mobile"><small>İlk temas</small><strong>${event(x.first_event)}</strong></div><div class="hide-tablet"><small>MFE</small><strong class="${tone(x.mfe_r)}">${r(x.mfe_r)}</strong></div><div class="hide-tablet"><small>MAE</small><strong class="${tone(x.mae_r)}">${r(x.mae_r)}</strong></div><span class="tag ${tone(x.window_close_r)}">15dk ${r(x.window_close_r)}</span></div>`:`<div class="item"><div><strong>${esc(x.symbol||'Kayıt')}</strong><small>${esc(x.system_label||'Sistem')}</small></div><span class="tag amber">Veri alınamadı</span></div>`).join('')||'<div class="empty">Ölçülebilir geçmiş işlem bulunamadı.</div>';$('status').textContent=`${o.sample||0} örnek · ${new Date((p.generated_at||0)*1000).toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'})}`;$('note').textContent=`${p.note||''} ${p.ambiguity_note||''}`.trim()}async function load(){ $('status').textContent='Analiz hazırlanıyor…';try{render(await get())}catch(e){$('status').textContent='Analiz alınamadı';$('samples').innerHTML=`<div class="empty">${esc(e.message)}</div>`}}$('refreshBtn').addEventListener('click',load);load()})();</script></body></html>'''


def page(nonce: str) -> str:
    return PAGE.replace("__NONCE__", html.escape(nonce, quote=True))


def enhance_navigation(body: str) -> str:
    if 'href="/early-performance"' in body:
        return body
    coin_link = '<a href="/coin-center?symbol=BTCUSDT">Coin Merkezi</a>'
    if coin_link in body:
        body = body.replace(coin_link, coin_link + '<a href="/early-performance">15 dk Analizi</a>', 1)
    fav = '<button class="fav" id="favBtn"'
    if fav in body:
        body = body.replace(fav, '<a class="back" href="/early-performance">15 dk Analizi</a>' + fav, 1)
    return body


def make_v318_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None, history_cache: HistoricalPulseCache | None = None):
    candle_client = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    cache = history_cache or HistoricalPulseCache()
    BaseHandler = pulse.make_v317_handler(config, service, sessions, limiter, store, candle_client, overview_client)

    class V318Handler(BaseHandler):
        server_version = "KriptoPanel/3.18"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith("text/html"):
                path = urllib.parse.urlsplit(self.path).path
                if path in {"/", "/coin-center"}:
                    body = enhance_navigation(body)
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/healthz":
                self._json(HTTPStatus.OK, {
                    "status":"ok","version":VERSION,"early_performance":True,"history_1m":True,
                    "balanced_sample":True,"history_cache":True,"signal_engine":"unchanged",
                    "telegram":"unchanged","trade_management":"unchanged","ledger_write":"unchanged",
                })
                return
            if parsed.path == "/early-performance":
                session = self._session()
                if not session:
                    self._redirect("/login"); return
                if not self._is_premium(session):
                    self._redirect("/premium"); return
                nonce = __import__("secrets").token_urlsafe(18)
                self._send(HTTPStatus.OK, page(nonce), "text/html; charset=utf-8", nonce=nonce)
                return
            if parsed.path == "/api/early-performance":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error":"authentication_required"}); return
                if not self._is_premium(session):
                    self._json(HTTPStatus.FORBIDDEN, {"error":"premium_required","upgrade":"/premium"}); return
                try:
                    self._json(HTTPStatus.OK, build_history_payload(service.get_data(), candle_client, cache))
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error":"early_performance_unavailable"})
                return
            return super().do_GET()

    return V318Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V3.18 İlk 15 Dakika Performans Analizi")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root)); config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter(); store = lifecycle.lifecycle_store_from_env(config)
    market_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=2)
    history_cache = HistoricalPulseCache()
    server = ThreadingHTTPServer((args.host, args.port), make_v318_handler(config, service, sessions, limiter, store, market_client, overview_client, history_cache))
    print(f"{VERSION} http://{args.host}:{args.port} early_performance=on signal_engine=unchanged")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == "__main__":
    main()
