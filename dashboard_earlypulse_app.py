"""Kripto Kontrol Merkezi V3.17 - İlk 15 Dakika İşlem Nabzı.

V3.16 canlı işlem ilerleme merkezini korur. Coin Merkezi'ndeki en güncel açık teknik
senaryonun ilk 15 dakikasını 1 dakikalık public mum verisiyle salt-okunur olarak ölçer:
MFE/MAE (R), TP1/SL ilk temas sırası, pencere kapanış R değeri ve zaman ilerlemesi.

Bu katman yalnız panel gözlemidir. Sinyal, strateji, radar, Telegram, emir, TP/SL,
BE veya state/ledger yazma davranışı değiştirilmez. Yeni periyodik Actions işi eklenmez.
"""
from __future__ import annotations

import argparse
import html
import math
import os
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_chartfix_app as chartfix
import dashboard_coin_app as coin
import dashboard_lifecycle_app as lifecycle
import dashboard_market_app as market
import dashboard_tradeprogress_app as progress
from dashboard_live_app import LoginRateLimiter, PanelConfig, build_service

VERSION = "KRIPTO_KONTROL_MERKEZI_V3_17_EARLY_PULSE_2026_08_16"
WINDOW_SECONDS = 15 * 60
BAR_SECONDS = 60


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


def _direction_sign(trade: dict[str, Any]) -> int:
    return -1 if str(trade.get("direction") or "").upper() == "SHORT" else 1


def analyze_first_15m(
    trade: dict[str, Any],
    candles: list[dict[str, Any]],
    *,
    now_ts: int | None = None,
    source: str = "PUBLIC_1M",
) -> dict[str, Any]:
    """Return a direction-aware, read-only first-15-minute observation."""
    now = int(time.time()) if now_ts is None else int(now_ts)
    opened_at = _timestamp(trade.get("opened_at"))
    entry = _number(trade.get("entry"))
    sl = _number(trade.get("sl"))
    tp1 = _number(trade.get("tp1"))
    direction = str(trade.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return {"status": "invalid_direction", "version": VERSION}
    if not opened_at:
        return {"status": "opened_at_missing", "version": VERSION}
    if entry is None or sl is None or entry == sl:
        return {"status": "risk_levels_missing", "version": VERSION, "opened_at": opened_at}

    sign = _direction_sign(trade)
    risk = abs(entry - sl)
    end_at = opened_at + WINDOW_SECONDS
    cutoff = min(max(now, opened_at), end_at)
    elapsed = max(0, min(WINDOW_SECONDS, now - opened_at))

    rows: list[dict[str, float | int]] = []
    for raw in candles:
        if not isinstance(raw, dict):
            continue
        ts = _timestamp(raw.get("ts"))
        high = _number(raw.get("high"))
        low = _number(raw.get("low"))
        close = _number(raw.get("close"))
        if not ts or high is None or low is None or close is None:
            continue
        if ts + BAR_SECONDS <= opened_at or ts >= end_at or ts > cutoff:
            continue
        rows.append({"ts": ts, "high": high, "low": low, "close": close})
    rows.sort(key=lambda row: int(row["ts"]))

    if not rows:
        return {
            "status": "candles_unavailable",
            "version": VERSION,
            "opened_at": opened_at,
            "window_end_at": end_at,
            "elapsed_seconds": elapsed,
            "completed": now >= end_at,
            "source": source,
        }

    favorable_r = 0.0
    adverse_r = 0.0
    first_event = "NONE"
    first_event_at: int | None = None
    first_event_candle_ambiguous = False
    last_close = entry

    for row in rows:
        high = float(row["high"])
        low = float(row["low"])
        last_close = float(row["close"])
        favorable_price = high if sign == 1 else low
        adverse_price = low if sign == 1 else high
        favorable_r = max(favorable_r, sign * (favorable_price - entry) / risk)
        adverse_r = min(adverse_r, sign * (adverse_price - entry) / risk)

        if first_event == "NONE":
            tp_hit = tp1 is not None and sign * (favorable_price - tp1) >= 0
            sl_hit = sign * (adverse_price - sl) <= 0
            if tp_hit and sl_hit:
                first_event = "TP1_SL_SAME_CANDLE"
                first_event_at = int(row["ts"])
                first_event_candle_ambiguous = True
            elif tp_hit:
                first_event = "TP1_FIRST"
                first_event_at = int(row["ts"])
            elif sl_hit:
                first_event = "SL_FIRST"
                first_event_at = int(row["ts"])

    close_r = sign * (last_close - entry) / risk
    target_cover_at = cutoff
    first_ts = int(rows[0]["ts"])
    last_ts = int(rows[-1]["ts"])
    coverage_start_ok = first_ts <= opened_at < first_ts + BAR_SECONDS or first_ts <= opened_at
    coverage_end_ok = last_ts + BAR_SECONDS >= target_cover_at
    data_quality = "COMPLETE" if coverage_start_ok and coverage_end_ok else "PARTIAL"

    return {
        "status": "ok",
        "version": VERSION,
        "direction": direction,
        "system": str(trade.get("system") or "Sistem")[:80],
        "opened_at": opened_at,
        "window_end_at": end_at,
        "elapsed_seconds": elapsed,
        "progress_percent": round(elapsed * 100 / WINDOW_SECONDS, 1),
        "completed": now >= end_at,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "risk_distance": risk,
        "mfe_r": round(favorable_r, 4),
        "mae_r": round(adverse_r, 4),
        "window_close_r": round(close_r, 4),
        "first_event": first_event,
        "first_event_at": first_event_at,
        "first_event_candle_ambiguous": first_event_candle_ambiguous,
        "sample_candles": len(rows),
        "coverage_from": first_ts,
        "coverage_to": last_ts + BAR_SECONDS,
        "data_quality": data_quality,
        "source": source,
        "resolution": "1m",
        "note": "1 dakikalık mum tabanlı salt-okunur gözlemdir; mum içindeki olay sırası kesinleştirilemez.",
    }


EARLY_CSS = r'''
#v317EarlyPulse{display:none;margin-top:13px;border:1px solid rgba(255,189,89,.20);background:linear-gradient(135deg,rgba(28,25,15,.96),rgba(8,21,27,.98));border-radius:16px;overflow:hidden;box-shadow:0 12px 38px rgba(0,0,0,.11)}
body.v317-has-pulse #v317EarlyPulse{display:block}.v317-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:12px 14px;border-bottom:1px solid rgba(89,77,42,.45)}.v317-title strong{font-size:13px}.v317-title small{display:block;color:#8e876c;font-size:8px}.v317-badge{border:1px solid rgba(255,189,89,.28);color:#ffbd59;border-radius:999px;padding:4px 8px;font-size:8px;font-weight:950}.v317-body{padding:12px 14px}.v317-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:7px}.v317-metric{background:rgba(5,16,21,.54);border:1px solid rgba(70,65,45,.52);border-radius:10px;padding:9px}.v317-metric small{display:block;color:#7e7b68;font-size:7px;text-transform:uppercase;letter-spacing:.05em;font-weight:850}.v317-metric b{display:block;font-size:13px;margin-top:3px}.v317-metric em{display:block;color:#7d8d88;font-size:7px;font-style:normal;margin-top:2px}.v317-pos{color:#42e28c}.v317-neg{color:#ff627d}.v317-neutral{color:#e9f5f2}.v317-amber{color:#ffbd59}.v317-time{margin-top:11px}.v317-time-label{display:flex;justify-content:space-between;gap:8px;color:#7b867f;font-size:8px;margin-bottom:5px}.v317-track{height:10px;border-radius:999px;border:1px solid rgba(89,77,42,.55);background:rgba(4,16,21,.74);overflow:hidden}.v317-fill{height:100%;width:0;background:linear-gradient(90deg,rgba(105,169,255,.78),rgba(255,189,89,.88));border-radius:999px;transition:width .3s ease}.v317-foot{display:flex;justify-content:space-between;gap:10px;margin-top:7px;color:#738783;font-size:8px}.v317-quality.partial{color:#ffbd59}.v317-quality.complete{color:#42e28c}
@media(max-width:900px){.v317-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:620px){.v317-grid{grid-template-columns:1fr 1fr}.v317-metric:last-child{grid-column:1/-1}.v317-body{padding:10px}.v317-foot{flex-direction:column;gap:3px}}
'''

EARLY_HTML = r'''
<section id="v317EarlyPulse" aria-live="polite">
  <div class="v317-head">
    <div class="v317-title"><strong>İlk 15 Dakika İşlem Nabzı</strong><small id="v317Subtitle">1m mumlarla erken işlem davranışı ölçümü</small></div>
    <span class="v317-badge" id="v317Phase">HAZIRLANIYOR</span>
  </div>
  <div class="v317-body">
    <div class="v317-grid">
      <div class="v317-metric"><small>İşlem yaşı</small><b id="v317Age">—</b><em id="v317Window">15 dk pencere</em></div>
      <div class="v317-metric"><small>En iyi hareket · MFE</small><b id="v317Mfe">—</b><em>Giriş-SL riskine göre</em></div>
      <div class="v317-metric"><small>En kötü hareket · MAE</small><b id="v317Mae">—</b><em>Giriş-SL riskine göre</em></div>
      <div class="v317-metric"><small>İlk seviye teması</small><b id="v317Event">—</b><em id="v317EventTime">1m çözünürlük</em></div>
      <div class="v317-metric"><small id="v317CloseTitle">Pencere R</small><b id="v317CloseR">—</b><em id="v317Samples">Mum verisi bekleniyor</em></div>
    </div>
    <div class="v317-time">
      <div class="v317-time-label"><span>0 dk · giriş</span><span id="v317Clock">—</span><span>15 dk</span></div>
      <div class="v317-track"><div class="v317-fill" id="v317Fill"></div></div>
      <div class="v317-foot"><span id="v317Quality" class="v317-quality">Veri kalitesi hazırlanıyor</span><span>Salt okunur gözlem · işlem yönetimi kuralı değildir</span></div>
    </div>
  </div>
</section>
'''

EARLY_SCRIPT = r'''
<script nonce="__NONCE__" id="v317-early-pulse-script">
(() => {
  'use strict';
  if (window.__v317EarlyPulse) return;
  window.__v317EarlyPulse = true;
  const $=id=>document.getElementById(id);
  const root=$('v317EarlyPulse'); if(!root)return;
  let token=0,lastSymbol='';
  const normalize=v=>String(v||'').toUpperCase().replace(/[^A-Z0-9]/g,'').replace(/USDTUSDT$/,'USDT');
  const symbol=()=>normalize(new URLSearchParams(location.search).get('symbol')||$('symbolInput')?.value||'BTCUSDT');
  const num=v=>{const n=Number(v);return Number.isFinite(n)?n:null};
  const fmtR=v=>{const n=num(v);return n===null?'—':`${n>=0?'+':''}${n.toFixed(2)}R`};
  const fmtAge=s=>{const n=Math.max(0,Number(s)||0),m=Math.floor(n/60),sec=Math.floor(n%60);return `${m} dk ${String(sec).padStart(2,'0')} sn`};
  const fmtTime=ts=>{const n=num(ts);return n?new Date(n*1000).toLocaleTimeString('tr-TR',{hour:'2-digit',minute:'2-digit'}):'—'};
  async function json(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');throw new Error('Oturum gerekli')}if(r.status===403){location.assign('/premium');throw new Error('Premium gerekli')}const p=await r.json();if(!r.ok)throw new Error(p.message||p.error||`HTTP ${r.status}`);return p}
  function phase(p){const e=Number(p.elapsed_seconds)||0;if(p.completed)return '15 DK TAMAMLANDI';if(e<300)return '0–5 DK';if(e<600)return '5–10 DK';return '10–15 DK'}
  function eventLabel(v){return ({TP1_FIRST:'TP1 önce',SL_FIRST:'SL önce',TP1_SL_SAME_CANDLE:'Aynı 1m mum',NONE:'Temas yok'})[v]||'—'}
  function color(node,v){if(!node)return;node.classList.remove('v317-pos','v317-neg','v317-neutral','v317-amber');const n=num(v);node.classList.add(n===null?'v317-neutral':n>0?'v317-pos':n<0?'v317-neg':'v317-neutral')}
  function render(p){
    if(!p||p.status!=='ok'){document.body.classList.remove('v317-has-pulse');return}
    document.body.classList.add('v317-has-pulse');
    $('v317Phase').textContent=phase(p);$('v317Age').textContent=fmtAge(p.elapsed_seconds);$('v317Window').textContent=p.completed?'İlk 15 dk tamamlandı':'Canlı pencere açık';
    $('v317Mfe').textContent=fmtR(p.mfe_r);color($('v317Mfe'),p.mfe_r);$('v317Mae').textContent=fmtR(p.mae_r);color($('v317Mae'),p.mae_r);
    $('v317Event').textContent=eventLabel(p.first_event);$('v317Event').className=p.first_event==='SL_FIRST'?'v317-neg':p.first_event==='TP1_FIRST'?'v317-pos':p.first_event==='TP1_SL_SAME_CANDLE'?'v317-amber':'v317-neutral';
    $('v317EventTime').textContent=p.first_event_at?`${fmtTime(p.first_event_at)} · 1m mum`:p.first_event==='NONE'?'Henüz TP1/SL teması yok':'1m çözünürlük';
    $('v317CloseTitle').textContent=p.completed?'15. dk kapanış R':'Şu ana kadarki R';$('v317CloseR').textContent=fmtR(p.window_close_r);color($('v317CloseR'),p.window_close_r);
    $('v317Samples').textContent=`${p.sample_candles||0} mum · ${p.source||'PUBLIC'}`;$('v317Fill').style.width=`${Math.max(0,Math.min(100,Number(p.progress_percent)||0)).toFixed(1)}%`;$('v317Clock').textContent=fmtAge(Math.min(900,Number(p.elapsed_seconds)||0));
    const q=String(p.data_quality||'PARTIAL').toLowerCase();$('v317Quality').className=`v317-quality ${q}`;$('v317Quality').textContent=p.data_quality==='COMPLETE'?'1m veri kapsamı tam':'1m veri kapsamı kısmi';
    $('v317Subtitle').textContent=`${p.system||'Sistem'} · ${p.direction||''} · 1 dakikalık mum tabanlı ölçüm`;
  }
  async function sync(){
    const s=symbol(),mine=++token;lastSymbol=s;
    try{const p=await json(`/api/coin-center/early-pulse?symbol=${encodeURIComponent(s)}&v317=${Date.now()}`);if(mine!==token||s!==symbol())return;render(p)}catch{if(mine===token)document.body.classList.remove('v317-has-pulse')}
  }
  function reset(){token++;document.body.classList.remove('v317-has-pulse');setTimeout(sync,260)}
  $('loadBtn')?.addEventListener('click',()=>setTimeout(reset,120));$('symbolInput')?.addEventListener('keydown',e=>{if(e.key==='Enter')setTimeout(reset,120)});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)sync()});
  sync();setInterval(()=>{if(!document.hidden&&symbol()===lastSymbol)sync()},15000);
})();
</script>
'''


def enhance_early_pulse_page(body: str, nonce: str) -> str:
    if 'id="v317-early-pulse-script"' in body:
        return body
    if 'id="v316TradeProgress"' not in body or '<div class="layout">' not in body or '</style>' not in body or '</body>' not in body:
        raise RuntimeError("V3.17 erken nabız ankrajları bulunamadı.")
    body = body.replace('</style>', EARLY_CSS + '\n</style>', 1)
    body = body.replace('<div class="layout">', EARLY_HTML + '\n<div class="layout">', 1)
    script = EARLY_SCRIPT.replace('__NONCE__', html.escape(str(nonce), quote=True))
    return body.replace('</body>', script + '\n</body>', 1)


def make_v317_handler(config: PanelConfig, service, sessions: accounts.ManagedSessionStore, limiter: LoginRateLimiter, store, market_client=None, overview_client=None):
    pulse_market = market_client or chartfix.ResilientMarketDataClient(cache_seconds=2)
    BaseHandler = progress.make_v316_handler(config, service, sessions, limiter, store, pulse_market, overview_client)

    class V317Handler(BaseHandler):
        server_version = "KriptoPanel/3.17"

        def _send(self, status, body, content_type, *, cookies=None, nonce=None):
            if status == HTTPStatus.OK and isinstance(body, str) and content_type.startswith('text/html') and urllib.parse.urlsplit(self.path).path == '/coin-center' and nonce:
                body = progress.enhance_trade_progress_page(body, str(nonce))
                body = enhance_early_pulse_page(body, str(nonce))
            return super()._send(status, body, content_type, cookies=cookies, nonce=nonce)

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == '/healthz':
                self._json(HTTPStatus.OK, {
                    'status':'ok','version':VERSION,'coin_center':True,'live_chart':True,'trade_progress':True,
                    'early_pulse':True,'first_15m':True,'mfe_mae_r':True,'first_touch':True,'resolution':'1m',
                    'signal_engine':'unchanged','telegram':'unchanged','trade_management':'unchanged',
                })
                return
            if parsed.path == '/api/coin-center/early-pulse':
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {'error':'authentication_required'})
                    return
                if not self._is_premium(session):
                    self._json(HTTPStatus.FORBIDDEN, {'error':'premium_required','upgrade':'/premium'})
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=3)
                symbol = (query.get('symbol') or [''])[0]
                try:
                    summary = coin.build_coin_summary(service.get_data(), symbol)
                    rows = summary.get('open_trades') if isinstance(summary.get('open_trades'), list) else []
                    if not rows:
                        self._json(HTTPStatus.OK, {'status':'no_open_trade','version':VERSION,'symbol':summary.get('symbol')})
                        return
                    trade = rows[0]
                    opened_at = _timestamp(trade.get('opened_at'))
                    if not opened_at:
                        self._json(HTTPStatus.OK, analyze_first_15m(trade, [], source='PUBLIC_1M'))
                        return
                    candle_payload = pulse_market.get_candles(summary.get('symbol') or symbol, '1m', opened_at)
                    payload = analyze_first_15m(
                        trade,
                        candle_payload.get('candles') if isinstance(candle_payload.get('candles'), list) else [],
                        source=str(candle_payload.get('source') or 'PUBLIC_1M'),
                    )
                    payload['symbol'] = summary.get('symbol')
                    self._json(HTTPStatus.OK, payload)
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {'error':'invalid_symbol','message':str(exc)})
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {'error':'early_pulse_unavailable'})
                return
            return super().do_GET()

    return V317Handler


def main() -> None:
    parser = argparse.ArgumentParser(description='Kripto Kontrol Merkezi V3.17 İlk 15 Dakika İşlem Nabzı')
    parser.add_argument('--host', default=os.getenv('HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT', '8080')))
    parser.add_argument('--root', default='.')
    args = parser.parse_args()
    config = PanelConfig.from_env(Path(args.root)); config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter(); store = lifecycle.lifecycle_store_from_env(config)
    market_client = chartfix.ResilientMarketDataClient(cache_seconds=2)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=2)
    server = ThreadingHTTPServer((args.host, args.port), make_v317_handler(config, service, sessions, limiter, store, market_client, overview_client))
    print(f"{VERSION} http://{args.host}:{args.port} early_pulse=on first_15m=on signal_engine=unchanged")
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__ == '__main__':
    main()
