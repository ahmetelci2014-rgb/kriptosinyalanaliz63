"""Kripto Kontrol Merkezi V2.7 - İnceleme Skoru.

V2.6 Piyasa Fırsat Merkezi'ni genişletir. Bu katman yalnız panel tarafında:
- 15m ve 1H EMA trend uyumunu,
- 15m/1H RSI durumunu,
- 15m hacim oranını,
- 24 saatlik fiyat momentumunu
0-100 arasında bir teknik uyum puanına dönüştürür.

Puan başarı olasılığı değildir, yeni işlem sinyali üretmez, stratejiyi değiştirmez
ve emir açmaz. Amaç yalnızca kullanıcının önce hangi coinleri inceleyeceğini
hızlıca sıralamasına yardımcı olmaktır.
"""

from __future__ import annotations

import argparse
import math
import os
import secrets
import threading
import time
import urllib.parse
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_accounts_app as accounts
import dashboard_market_app as market
import dashboard_opportunity_app as opp
import dashboard_product_app as product
from dashboard_live_app import (
    LoginRateLimiter,
    MarketDataError,
    OKXMarketDataClient,
    PanelConfig,
    build_service,
)

VERSION = "KRIPTO_KONTROL_MERKEZI_V2_7_ANALYSIS_SCORE_2026_08_14"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _closes(candles: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for candle in candles:
        value = _finite(candle.get("close")) if isinstance(candle, dict) else None
        if value is not None:
            values.append(value)
    return values


def _ema(values: list[float], period: int) -> float | None:
    if not values:
        return None
    period = max(2, int(period))
    factor = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = value * factor + result * (1.0 - factor)
    return result


def _rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    window = values[-(period + 1) :]
    gains = 0.0
    losses = 0.0
    for previous, current in zip(window, window[1:]):
        delta = current - previous
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    if losses == 0:
        return 100.0
    if gains == 0:
        return 0.0
    rs = (gains / period) / (losses / period)
    return 100.0 - (100.0 / (1.0 + rs))


def _trend(candles: list[dict[str, Any]]) -> tuple[str, float | None, float | None]:
    values = _closes(candles)
    if len(values) < 20:
        return "NÖTR", None, None
    ema20 = _ema(values[-100:], 20)
    ema50 = _ema(values[-120:], 50)
    if ema20 is None or ema50 is None:
        return "NÖTR", ema20, ema50
    tolerance = max(abs(ema50) * 0.0005, 1e-12)
    if ema20 > ema50 + tolerance:
        return "YUKARI", ema20, ema50
    if ema20 < ema50 - tolerance:
        return "AŞAĞI", ema20, ema50
    return "NÖTR", ema20, ema50


def _volume_ratio(candles: list[dict[str, Any]]) -> float | None:
    volumes = [
        value
        for candle in candles
        if isinstance(candle, dict)
        for value in [_finite(candle.get("volume"))]
        if value is not None and value >= 0
    ]
    if len(volumes) < 21:
        return None
    average = sum(volumes[-21:-1]) / 20.0
    if average <= 0:
        return None
    return volumes[-1] / average


def _rsi_points(value: float | None, direction: str, maximum: int) -> int:
    if value is None:
        return 0
    if direction == "YUKARI":
        if 50 <= value <= 68:
            return maximum
        if 43 <= value <= 75:
            return round(maximum * 0.72)
        if 35 <= value <= 80:
            return round(maximum * 0.42)
        return round(maximum * 0.15)
    if direction == "AŞAĞI":
        if 32 <= value <= 50:
            return maximum
        if 25 <= value <= 57:
            return round(maximum * 0.72)
        if 20 <= value <= 65:
            return round(maximum * 0.42)
        return round(maximum * 0.15)
    if 42 <= value <= 58:
        return round(maximum * 0.55)
    return round(maximum * 0.25)


def compute_analysis_score(
    candles_15m: list[dict[str, Any]],
    candles_1h: list[dict[str, Any]],
    change_24h_pct: Any,
) -> dict[str, Any]:
    """Teknik uyumu 0-100 puanlar. Puan bir işlem sinyali veya olasılık değildir."""
    trend_15m, ema20_15m, ema50_15m = _trend(candles_15m)
    trend_1h, ema20_1h, ema50_1h = _trend(candles_1h)
    change = _finite(change_24h_pct) or 0.0

    if trend_15m == trend_1h and trend_15m in {"YUKARI", "AŞAĞI"}:
        direction = trend_15m
        trend_points = 40
    else:
        if trend_1h in {"YUKARI", "AŞAĞI"}:
            direction = trend_1h
        elif trend_15m in {"YUKARI", "AŞAĞI"}:
            direction = trend_15m
        elif change > 0.5:
            direction = "YUKARI"
        elif change < -0.5:
            direction = "AŞAĞI"
        else:
            direction = "KARIŞIK"

        if trend_1h == direction and trend_15m == "NÖTR":
            trend_points = 27
        elif trend_15m == direction and trend_1h == "NÖTR":
            trend_points = 23
        elif trend_15m != trend_1h and trend_15m != "NÖTR" and trend_1h != "NÖTR":
            trend_points = 10
        else:
            trend_points = 16

    rsi_15m = _rsi(_closes(candles_15m))
    rsi_1h = _rsi(_closes(candles_1h))
    rsi_points = _rsi_points(rsi_15m, direction, 12) + _rsi_points(rsi_1h, direction, 8)

    volume_ratio = _volume_ratio(candles_15m)
    if volume_ratio is None:
        volume_points = 0
    elif volume_ratio >= 2.0:
        volume_points = 20
    elif volume_ratio >= 1.5:
        volume_points = 16
    elif volume_ratio >= 1.1:
        volume_points = 12
    elif volume_ratio >= 0.8:
        volume_points = 7
    else:
        volume_points = 3

    absolute_change = abs(change)
    if absolute_change >= 8:
        momentum_points = 20
    elif absolute_change >= 5:
        momentum_points = 16
    elif absolute_change >= 3:
        momentum_points = 12
    elif absolute_change >= 1.5:
        momentum_points = 8
    elif absolute_change >= 0.5:
        momentum_points = 4
    else:
        momentum_points = 1

    score = max(0, min(100, trend_points + rsi_points + volume_points + momentum_points))
    if score >= 80:
        band = "YÜKSEK UYUM"
    elif score >= 65:
        band = "ORTA-YÜKSEK"
    elif score >= 50:
        band = "ORTA UYUM"
    else:
        band = "DÜŞÜK UYUM"

    return {
        "score": int(score),
        "band": band,
        "direction": direction,
        "components": {
            "trend": int(trend_points),
            "rsi": int(rsi_points),
            "volume": int(volume_points),
            "momentum": int(momentum_points),
        },
        "metrics": {
            "trend_15m": trend_15m,
            "trend_1h": trend_1h,
            "rsi_15m": round(rsi_15m, 2) if rsi_15m is not None else None,
            "rsi_1h": round(rsi_1h, 2) if rsi_1h is not None else None,
            "volume_ratio_15m": round(volume_ratio, 2) if volume_ratio is not None else None,
            "change_24h_pct": round(change, 3),
            "ema20_15m": ema20_15m,
            "ema50_15m": ema50_15m,
            "ema20_1h": ema20_1h,
            "ema50_1h": ema50_1h,
        },
        "note": "İnceleme skoru teknik uyum göstergesidir; başarı ihtimali veya işlem sinyali değildir.",
    }


class AnalysisScoreService:
    def __init__(
        self,
        market_client: OKXMarketDataClient,
        overview_client: market.OKXMarketOverviewClient,
        cache_seconds: int = 120,
    ):
        self.market_client = market_client
        self.overview_client = overview_client
        self.cache_seconds = max(30, min(int(cache_seconds), 300))
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def get_score(self, symbol_value: str, force: bool = False) -> dict[str, Any]:
        symbol = OKXMarketDataClient.normalize_symbol(symbol_value)
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(symbol)
            if not force and cached and now - cached[0] < self.cache_seconds:
                return dict(cached[1])

        candles_15m = self.market_client.get_candles(symbol, "15m").get("candles", [])
        candles_1h = self.market_client.get_candles(symbol, "1H").get("candles", [])
        overview = self.overview_client.get_overview([symbol])
        items = overview.get("items") if isinstance(overview, dict) else []
        change = items[0].get("change_24h_pct") if isinstance(items, list) and items else None
        result = compute_analysis_score(candles_15m, candles_1h, change)
        result.update(
            {
                "symbol": symbol,
                "fetched_at": int(time.time()),
                "source": "OKX_PUBLIC_NO_API_KEY",
                "version": VERSION,
            }
        )
        with self._lock:
            self._cache[symbol] = (time.monotonic(), dict(result))
        return result


def score_dashboard_page(session: dict[str, Any], nonce: str) -> str:
    body = opp.opportunity_dashboard_page(session, nonce)
    nonce_attr = str(nonce).replace('"', "&quot;")

    css = r'''
    /* V2.7: Fırsat kartlarına düşük maliyetli teknik uyum skoru ekle. */
    .score-chip{display:inline-flex;align-items:center;gap:4px;margin-top:4px;border:1px solid var(--line);background:#0a1922;color:#7f9a97;border-radius:999px;padding:3px 6px;font-size:7px;font-weight:950;cursor:pointer}.score-chip:hover{border-color:#7db8ff;color:#b7d6ff}.score-chip.loading{opacity:.7}.score-chip.high{color:var(--green);border-color:rgba(66,226,140,.30);background:rgba(66,226,140,.055)}.score-chip.mid{color:var(--amber);border-color:rgba(255,189,89,.28);background:rgba(255,189,89,.05)}.score-chip.low{color:#87a3a0;border-color:rgba(135,163,160,.20)}
    .score-legend{display:flex;gap:7px;align-items:center;flex-wrap:wrap;margin:-4px 0 12px;color:#667f7c;font-size:8px}.score-dot{width:6px;height:6px;border-radius:50%;display:inline-block}.score-dot.high{background:var(--green)}.score-dot.mid{background:var(--amber)}.score-dot.low{background:#718b88}
    '''
    body = body.replace("  </style>", css + "\n  </style>", 1)

    action_anchor = '<button class="btn primary" id="oppRefreshBtn" type="button">Piyasayı yenile</button>'
    if action_anchor not in body:
        raise RuntimeError("V2.6 fırsat yenileme düğmesi bulunamadı.")
    body = body.replace(
        action_anchor,
        action_anchor + '<button class="btn" id="scoreLoadAllBtn" type="button">Skorları getir</button>',
        1,
    )

    summary_anchor = '<div class="opp-summary" id="oppSummary"></div>'
    legend = '<div class="score-legend"><b>İnceleme Skoru:</b><span class="score-dot high"></span> yüksek uyum <span class="score-dot mid"></span> orta uyum <span class="score-dot low"></span> düşük uyum · başarı ihtimali değildir</div>'
    if summary_anchor not in body:
        raise RuntimeError("V2.6 fırsat özeti bulunamadı.")
    body = body.replace(summary_anchor, summary_anchor + legend, 1)

    script = r'''
<script nonce="__NONCE__">
(() => {
  const cache=new Map(),pending=new Map();let autoTimer=null;
  const scoreClass=score=>score>=80?'high':score>=65?'mid':'low';
  function chip(card){
    let node=card.querySelector('.score-chip');
    if(node)return node;
    node=document.createElement('button');node.type='button';node.className='score-chip';node.textContent='Skor';
    node.addEventListener('click',event=>{event.preventDefault();event.stopPropagation();loadCard(card,true);});
    card.querySelector('.opp-main')?.appendChild(node);return node;
  }
  function tooltip(payload){const m=payload?.metrics||{},c=payload?.components||{};return `Teknik uyum ${payload?.score??'—'}/100 · ${payload?.direction||'—'} · 15m ${m.trend_15m||'—'} · 1H ${m.trend_1h||'—'} · RSI15 ${m.rsi_15m??'—'} · RSI1H ${m.rsi_1h??'—'} · Hacim ${m.volume_ratio_15m??'—'}x · Trend ${c.trend??0}/40 · RSI ${c.rsi??0}/20 · Hacim ${c.volume??0}/20 · Momentum ${c.momentum??0}/20. Başarı ihtimali değildir.`;}
  function paint(card,payload){const node=chip(card),score=Number(payload?.score);node.className=`score-chip ${scoreClass(Number.isFinite(score)?score:0)}`;node.textContent=Number.isFinite(score)?`${score} · ${payload.direction||'—'}`:'Skor yok';node.title=tooltip(payload);}
  async function request(symbol,force=false){
    if(!force&&cache.has(symbol))return cache.get(symbol);
    if(pending.has(symbol))return pending.get(symbol);
    const task=(async()=>{const q=`?symbol=${encodeURIComponent(symbol)}${force?'&refresh=1':''}`;const r=await fetch('/api/market/analysis-score'+q,{credentials:'same-origin',cache:'no-store',headers:{Accept:'application/json'}});if(r.status===401){location.assign('/login');throw new Error('Oturum gerekli');}const p=await r.json();if(!r.ok)throw new Error(p.message||p.error||`HTTP ${r.status}`);cache.set(symbol,p);return p;})().finally(()=>pending.delete(symbol));pending.set(symbol,task);return task;
  }
  async function loadCard(card,force=false){const symbol=String(card?.dataset?.focusSymbol||'').toUpperCase();if(!symbol)return;const node=chip(card);node.className='score-chip loading';node.textContent='Hesap…';try{paint(card,await request(symbol,force));}catch{node.className='score-chip low';node.textContent='Skor yok';node.title='Piyasa verisi alınamadı.';}}
  function cards(){return [...document.querySelectorAll('#page-opportunities .opp-card[data-focus-symbol]')];}
  function decorate(){cards().forEach(chip);scheduleAuto();}
  function priority(){const selectors=['#oppActive .opp-card','#oppRising .opp-card','#oppFalling .opp-card','#oppVolume .opp-card'];const result=[],seen=new Set();for(const selector of selectors){for(const card of document.querySelectorAll(selector)){const symbol=card.dataset.focusSymbol;if(symbol&&!seen.has(symbol)){seen.add(symbol);result.push(card);if(result.length>=8)return result;}}}return result;}
  async function runQueue(list,force=false,workers=2){const queue=[...list];async function worker(){while(queue.length){const card=queue.shift();if(card)await loadCard(card,force);}}await Promise.all(Array.from({length:Math.min(workers,queue.length||1)},worker));}
  function scheduleAuto(){clearTimeout(autoTimer);autoTimer=setTimeout(()=>runQueue(priority(),false,2),450);}
  const observer=new MutationObserver(()=>decorate());const page=document.getElementById('page-opportunities');if(page)observer.observe(page,{childList:true,subtree:true});
  document.getElementById('scoreLoadAllBtn')?.addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;button.textContent='Skorlar hesaplanıyor…';const unique=[],seen=new Set();for(const card of cards()){const s=card.dataset.focusSymbol;if(s&&!seen.has(s)){seen.add(s);unique.push(card);if(unique.length>=20)break;}}await runQueue(unique,false,2);button.disabled=false;button.textContent='Skorları getir';});
  document.addEventListener('click',event=>{if(event.target.closest('[data-view="opportunities"]'))setTimeout(decorate,80);});
  decorate();
})();
</script>
'''.replace("__NONCE__", nonce_attr)
    return body.replace("</body>", script + "\n</body>", 1)


def make_v27_handler(
    config: PanelConfig,
    service,
    sessions,
    limiter: LoginRateLimiter,
    store,
    market_client: OKXMarketDataClient | None = None,
    overview_client: market.OKXMarketOverviewClient | None = None,
    score_service: AnalysisScoreService | None = None,
):
    market_client = market_client or OKXMarketDataClient(cache_seconds=30)
    overview_client = overview_client or market.OKXMarketOverviewClient(cache_seconds=20)
    score_service = score_service or AnalysisScoreService(market_client, overview_client)
    BaseHandler = opp.make_v26_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
        overview_client,
    )

    class V27Handler(BaseHandler):
        server_version = "KriptoPanel/2.7"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            nonce = secrets.token_urlsafe(18)
            self._send(HTTPStatus.OK, score_dashboard_page(session, nonce), "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            if parsed.path == "/api/market/analysis-score":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication_required"})
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=3)
                symbol = (query.get("symbol") or [""])[0]
                force = (query.get("refresh") or [""])[0] == "1"
                try:
                    payload = score_service.get_score(symbol, force=force)
                    self._json(HTTPStatus.OK, payload)
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_symbol", "message": str(exc)})
                except MarketDataError:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": "market_data_unavailable"})
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "analysis_score_unavailable"})
                return
            return super().do_GET()

    return V27Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Merkezi V2.7 inceleme skoru.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = accounts.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = product.account_store_from_env(config)
    market_client = OKXMarketDataClient(cache_seconds=30)
    overview_client = market.OKXMarketOverviewClient(cache_seconds=20)
    handler = make_v27_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
        overview_client,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"{VERSION} http://{args.host}:{args.port} opportunity=on analysis_score=on signal_engine=unchanged")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
