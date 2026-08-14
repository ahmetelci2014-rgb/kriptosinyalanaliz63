"""Kripto Kontrol Paneli V1.9 - canlı piyasa merkezi.

Bu katman V1.8 üyelik merkezini genişletir:
- OKX public market verisiyle salt-okunur piyasa özeti sunar.
- Açık işlemleri ve temel USDT paritelerini tek ekranda izletir.
- Kullanıcı istediği USDT paritesini arayıp mum grafiğinde inceleyebilir.
- Sinyal üretimi, Telegram, strateji ve emir akışı değişmez.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import math
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import dashboard_product_app as v18
from dashboard_builder import render_dashboard
from dashboard_live_app import (
    ROLE_ADMIN,
    ROLE_MEMBER,
    LoginRateLimiter,
    MarketDataError,
    OKXMarketDataClient,
    PanelConfig,
    build_service,
    cookie_value,
    dashboard_for_session,
)

VERSION = "KRIPTO_KONTROL_PANELI_LIVE_V1_9_2026_08_14"
DEFAULT_MARKET_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "SUIUSDT",
    "ADAUSDT",
)
MAX_OVERVIEW_SYMBOLS = 30


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_market_symbols(values: list[str] | tuple[str, ...], limit: int = MAX_OVERVIEW_SYMBOLS) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in values:
        try:
            symbol = OKXMarketDataClient.normalize_symbol(str(raw or ""))
        except ValueError:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
        if len(result) >= limit:
            break
    return result


def select_market_symbols(data: dict[str, Any], limit: int = MAX_OVERVIEW_SYMBOLS) -> list[str]:
    candidates: list[str] = list(DEFAULT_MARKET_SYMBOLS)
    for key in ("open_trades", "recent_results"):
        rows = data.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                candidates.append(str(row.get("symbol") or ""))
    return normalize_market_symbols(candidates, limit=limit)


def market_context(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    context: dict[str, dict[str, Any]] = {}
    for row in data.get("open_trades", []) if isinstance(data.get("open_trades"), list) else []:
        if not isinstance(row, dict):
            continue
        try:
            symbol = OKXMarketDataClient.normalize_symbol(str(row.get("symbol") or ""))
        except ValueError:
            continue
        context[symbol] = {
            "kind": "OPEN",
            "direction": str(row.get("direction") or ""),
            "system_label": str(row.get("system_label") or row.get("system") or ""),
            "outcome": "",
        }
    for row in data.get("recent_results", []) if isinstance(data.get("recent_results"), list) else []:
        if not isinstance(row, dict):
            continue
        try:
            symbol = OKXMarketDataClient.normalize_symbol(str(row.get("symbol") or ""))
        except ValueError:
            continue
        if symbol in context:
            continue
        context[symbol] = {
            "kind": "RECENT",
            "direction": str(row.get("direction") or ""),
            "system_label": str(row.get("system_label") or row.get("system") or ""),
            "outcome": str(row.get("outcome") or ""),
        }
    return context


class OKXMarketOverviewClient:
    """OKX public SWAP ticker listesini tek istekle okuyup kısa süre önbellekler."""

    def __init__(self, cache_seconds: int = 20):
        self.cache_seconds = max(5, min(int(cache_seconds), 60))
        self._cache: tuple[float, list[dict[str, Any]]] | None = None
        self._lock = threading.Lock()

    def _request_all(self) -> list[dict[str, Any]]:
        request = urllib.request.Request(
            "https://www.okx.com/api/v5/market/tickers?instType=SWAP",
            headers={
                "Accept": "application/json",
                "User-Agent": "Kripto-Kontrol-Paneli-Market-Overview/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            urllib.error.HTTPError,
        ) as exc:
            raise MarketDataError(f"OKX piyasa özeti alınamadı ({type(exc).__name__}).") from exc
        if not isinstance(payload, dict) or str(payload.get("code")) != "0":
            raise MarketDataError("OKX geçerli piyasa özeti döndürmedi.")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise MarketDataError("OKX piyasa özeti biçimi geçersiz.")
        return [row for row in rows if isinstance(row, dict)]

    def _all_tickers(self) -> list[dict[str, Any]]:
        now = time.monotonic()
        with self._lock:
            cached = self._cache
            if cached and now - cached[0] < self.cache_seconds:
                return copy.deepcopy(cached[1])
        rows = self._request_all()
        with self._lock:
            self._cache = (time.monotonic(), copy.deepcopy(rows))
        return rows

    @staticmethod
    def _parse_row(row: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        inst_id = str(row.get("instId") or "").upper()
        parts = inst_id.split("-")
        if len(parts) != 3 or parts[1] != "USDT" or parts[2] != "SWAP":
            return None
        symbol = f"{parts[0]}USDT"
        try:
            symbol = OKXMarketDataClient.normalize_symbol(symbol)
        except ValueError:
            return None
        last = _number(row.get("last"))
        open24h = _number(row.get("open24h"))
        high24h = _number(row.get("high24h"))
        low24h = _number(row.get("low24h"))
        volume = _number(row.get("volCcy24h"))
        change_pct = None
        if last is not None and open24h not in (None, 0):
            change_pct = (last / open24h - 1.0) * 100.0
        try:
            generated_at = int(float(row.get("ts") or 0) / 1000)
        except (TypeError, ValueError):
            generated_at = 0
        return symbol, {
            "symbol": symbol,
            "inst_id": inst_id,
            "last": last,
            "change_24h_pct": round(change_pct, 3) if change_pct is not None else None,
            "high_24h": high24h,
            "low_24h": low24h,
            "volume_24h": volume,
            "generated_at": generated_at,
        }

    def get_overview(self, symbols: list[str]) -> dict[str, Any]:
        requested = normalize_market_symbols(symbols)
        if not requested:
            raise ValueError("En az bir geçerli USDT paritesi gereklidir.")
        parsed: dict[str, dict[str, Any]] = {}
        for row in self._all_tickers():
            item = self._parse_row(row)
            if item is not None:
                parsed[item[0]] = item[1]
        items = [parsed[symbol] for symbol in requested if symbol in parsed]
        return {
            "items": items,
            "requested": requested,
            "missing": [symbol for symbol in requested if symbol not in parsed],
            "fetched_at": int(time.time()),
            "source": "OKX_PUBLIC_NO_API_KEY",
        }


def market_center_page(nonce: str) -> str:
    nonce_attr = html.escape(nonce, quote=True)
    return f"""<!doctype html>
<html lang="tr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>Kripto Kontrol · Piyasa Merkezi</title>
  <style>
    :root{{--bg:#061016;--panel:#0b1b23;--line:#1b3943;--text:#edf8f6;--muted:#86a5a1;--teal:#2ce6bf;--green:#42e28c;--red:#ff627d;--amber:#ffbd59}}
    *{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 85% 0,#10342f 0,transparent 30%),var(--bg);color:var(--text);font:14px/1.5 Inter,system-ui,sans-serif}}
    button,input,select{{font:inherit}}a{{color:var(--teal);font-weight:800;text-decoration:none}}.shell{{width:min(1380px,calc(100% - 28px));margin:auto;padding:28px 0 60px}}
    .top{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}h1{{margin:0;font-size:30px;letter-spacing:-.03em}}p{{color:var(--muted)}}
    .toolbar,.card{{border:1px solid var(--line);background:rgba(11,27,35,.94);border-radius:18px}}.toolbar{{padding:14px;display:flex;gap:9px;flex-wrap:wrap;margin-top:20px;align-items:center}}
    input,select,button{{background:#07151c;border:1px solid var(--line);color:var(--text);border-radius:10px;padding:10px 11px}}input{{min-width:240px;flex:1}}button{{cursor:pointer;font-weight:850}}button:hover{{border-color:var(--teal)}}.primary{{background:var(--teal);color:#04110e;border-color:transparent}}
    .layout{{display:grid;grid-template-columns:.85fr 1.45fr;gap:18px;margin-top:18px}}.card{{padding:18px;min-width:0}}.card h2{{margin:0 0 12px;font-size:18px}}
    .table-wrap{{overflow:auto;max-height:620px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid var(--line);text-align:left;white-space:nowrap}}th{{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.07em;position:sticky;top:0;background:#0b1b23}}tr[data-symbol]{{cursor:pointer}}tr[data-symbol]:hover{{background:rgba(44,230,191,.05)}}
    .up{{color:var(--green)}}.down{{color:var(--red)}}.muted{{color:var(--muted)}}.pill{{display:inline-flex;border:1px solid var(--line);border-radius:999px;padding:4px 7px;font-size:10px}}
    .chart-wrap{{height:500px;position:relative}}canvas{{width:100%;height:100%;display:block}}.chart-meta{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:12px;margin-top:10px}}
    .status{{margin-left:auto;color:var(--muted);font-size:12px}}.selected{{outline:1px solid rgba(44,230,191,.5);background:rgba(44,230,191,.04)}}
    @media(max-width:960px){{.layout{{grid-template-columns:1fr}}.chart-wrap{{height:390px}}}}@media(max-width:620px){{input{{min-width:100%}}.toolbar>*{{width:100%}}.status{{margin-left:0}}}}
  </style>
</head>
<body>
<div class="shell">
  <div class="top">
    <div><h1>Canlı Piyasa Merkezi</h1><p>OKX public veri · salt-okunur · coin ara, izle ve grafikte incele</p></div>
    <a href="/">← Kontrol merkezine dön</a>
  </div>
  <div class="toolbar">
    <input id="symbolInput" value="BTCUSDT" placeholder="Örn. BTCUSDT, ADAUSDT" autocomplete="off">
    <select id="barSelect"><option>1m</option><option>5m</option><option selected>15m</option><option>1H</option><option>4H</option><option>1D</option></select>
    <button id="loadButton" class="primary">Coini incele</button>
    <button id="refreshButton">Listeyi yenile</button>
    <span id="status" class="status">Hazırlanıyor…</span>
  </div>
  <div class="layout">
    <section class="card">
      <h2>Piyasa izleme listesi</h2>
      <div class="table-wrap"><table><thead><tr><th>Coin</th><th>Fiyat</th><th>24s</th><th>Bağlantı</th></tr></thead><tbody id="marketRows"></tbody></table></div>
    </section>
    <section class="card selected">
      <h2 id="chartTitle">BTCUSDT · 15m</h2>
      <div class="chart-wrap"><canvas id="chart"></canvas></div>
      <div class="chart-meta"><span id="chartInfo">Grafik yükleniyor…</span><span>Emir açmaz · sinyal üretmez</span></div>
    </section>
  </div>
</div>
<script nonce="{nonce_attr}">
(() => {{
  const $=id=>document.getElementById(id), state={{items:[],selected:"BTCUSDT",candles:null}};
  const e=value=>String(value??"").replace(/[&<>\"']/g,ch=>({{"&":"&amp;","<":"&lt;",">":"&gt;",'\"':"&quot;","'":"&#39;"}}[ch]));
  const normalize=value=>String(value||"").toUpperCase().replace(/[^A-Z0-9]/g,"").replace(/USDTUSDT$/, "USDT");
  const price=value=>{{const n=Number(value);if(!Number.isFinite(n))return "—";if(Math.abs(n)>=1000)return n.toLocaleString("tr-TR",{{maximumFractionDigits:2}});if(Math.abs(n)>=1)return n.toLocaleString("tr-TR",{{maximumFractionDigits:5}});return n.toLocaleString("tr-TR",{{maximumFractionDigits:9}});}};
  const pct=value=>{{const n=Number(value);return Number.isFinite(n)?`${{n>=0?"+":""}}${{n.toFixed(2)}}%`:"—";}};
  function setStatus(text){{$('status').textContent=text;}}
  function contextLabel(item){{if(item.kind==="OPEN")return `${{item.direction||""}} Açık`;if(item.kind==="RECENT")return item.outcome||"Geçmiş";return "Piyasa";}}
  function renderRows(){{
    $('marketRows').innerHTML=state.items.map(item=>`<tr data-symbol="${{e(item.symbol)}}"><td><strong>${{e(item.symbol)}}</strong></td><td>${{price(item.last)}}</td><td class="${{Number(item.change_24h_pct)>=0?'up':'down'}}">${{pct(item.change_24h_pct)}}</td><td><span class="pill">${{e(contextLabel(item))}}</span></td></tr>`).join("")||'<tr><td colspan="4" class="muted">Piyasa verisi bulunamadı.</td></tr>';
  }}
  async function loadOverview(symbols=""){{
    setStatus("Piyasa listesi yükleniyor…");
    const query=symbols?`?symbols=${{encodeURIComponent(symbols)}}`:"";
    try{{
      const response=await fetch(`/api/market/overview${{query}}`,{{credentials:"same-origin",cache:"no-store",headers:{{Accept:"application/json"}}}});
      if(response.status===401){{location.assign('/login');return;}}
      const payload=await response.json();if(!response.ok)throw new Error(payload.message||payload.error||`HTTP ${{response.status}}`);
      if(symbols){{const merged=new Map(state.items.map(item=>[item.symbol,item]));(payload.items||[]).forEach(item=>merged.set(item.symbol,item));state.items=[...merged.values()];}}else{{state.items=payload.items||[];}}
      renderRows();setStatus(`${{state.items.length}} coin · ${{payload.source||'OKX public'}}`);
    }}catch(error){{setStatus(`Liste alınamadı: ${{error.message}}`);}}
  }}
  async function loadChart(symbolValue=null){{
    const symbol=normalize(symbolValue||$('symbolInput').value);const bar=$('barSelect').value;
    if(!/^[A-Z0-9]{{2,15}}USDT$/.test(symbol)){{setStatus("Coin BTCUSDT biçiminde olmalı");return;}}
    state.selected=symbol;$('symbolInput').value=symbol;$('chartTitle').textContent=`${{symbol}} · ${{bar}}`;$('chartInfo').textContent="Grafik yükleniyor…";
    try{{
      const response=await fetch(`/api/market/candles?symbol=${{encodeURIComponent(symbol)}}&bar=${{encodeURIComponent(bar)}}`,{{credentials:"same-origin",cache:"no-store",headers:{{Accept:"application/json"}}}});
      if(response.status===401){{location.assign('/login');return;}}
      const payload=await response.json();if(!response.ok)throw new Error(payload.message||payload.error||`HTTP ${{response.status}}`);
      state.candles=payload;drawChart();$('chartInfo').textContent=`${{payload.candles?.length||0}} mum · Son ${{price(payload.last_price)}} · ${{payload.market_type||''}}`;
    }}catch(error){{$('chartInfo').textContent=`Grafik alınamadı: ${{error.message}}`;}}
  }}
  function drawChart(){{
    const canvas=$('chart'),payload=state.candles;if(!canvas||!payload||!(payload.candles||[]).length)return;const candles=payload.candles;
    const box=canvas.parentElement.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2),width=Math.max(320,box.width),height=Math.max(300,box.height);canvas.width=width*dpr;canvas.height=height*dpr;canvas.style.width=`${{width}}px`;canvas.style.height=`${{height}}px`;
    const ctx=canvas.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);ctx.clearRect(0,0,width,height);
    const lows=candles.map(c=>Number(c.low)).filter(Number.isFinite),highs=candles.map(c=>Number(c.high)).filter(Number.isFinite),lo=Math.min(...lows),hi=Math.max(...highs),pad=Math.max((hi-lo)*.07,Math.abs(hi)*.001,1e-10),min=lo-pad,max=hi+pad;
    const m={{left:12,right:86,top:16,bottom:28}},cw=width-m.left-m.right,ch=height-m.top-m.bottom,y=v=>m.top+(max-Number(v))/(max-min)*ch;ctx.font='11px system-ui';ctx.strokeStyle='rgba(134,165,161,.16)';ctx.fillStyle='#86a5a1';
    for(let i=0;i<=5;i++){{const yy=m.top+ch*i/5;ctx.beginPath();ctx.moveTo(m.left,yy);ctx.lineTo(width-m.right,yy);ctx.stroke();ctx.fillText(price(max-(max-min)*i/5),width-m.right+7,yy+4);}}
    const step=cw/candles.length,body=Math.max(2,Math.min(8,step*.64));candles.forEach((c,i)=>{{const x=m.left+step*(i+.5),o=y(c.open),cl=y(c.close),h=y(c.high),l=y(c.low),up=Number(c.close)>=Number(c.open);ctx.strokeStyle=up?'#42e28c':'#ff627d';ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.moveTo(x,h);ctx.lineTo(x,l);ctx.stroke();ctx.fillRect(x-body/2,Math.min(o,cl),body,Math.max(1,Math.abs(cl-o)));}});
    const first=new Date(candles[0].ts*1000),last=new Date(candles[candles.length-1].ts*1000);ctx.fillStyle='#86a5a1';ctx.fillText(first.toLocaleString('tr-TR',{{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}}),m.left,height-7);ctx.fillText(last.toLocaleString('tr-TR',{{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'}}),Math.max(m.left,width-m.right-110),height-7);
  }}
  $('loadButton').addEventListener('click',async()=>{{const symbol=normalize($('symbolInput').value);await loadOverview(symbol);await loadChart(symbol);}});
  $('refreshButton').addEventListener('click',()=>loadOverview());$('barSelect').addEventListener('change',()=>loadChart(state.selected));$('symbolInput').addEventListener('keydown',event=>{{if(event.key==='Enter'){{event.preventDefault();$('loadButton').click();}}}});
  $('marketRows').addEventListener('click',event=>{{const row=event.target.closest('[data-symbol]');if(row)loadChart(row.dataset.symbol);}});window.addEventListener('resize',()=>state.candles&&drawChart());
  loadOverview().then(()=>loadChart('BTCUSDT'));
}})();
</script>
</body>
</html>"""


def make_v19_handler(
    config: PanelConfig,
    service,
    sessions: v18.v17.SessionStore,
    limiter: LoginRateLimiter,
    store: v18.ProductAccountStore,
    market_client: OKXMarketDataClient | None = None,
    overview_client: OKXMarketOverviewClient | None = None,
):
    market_client = market_client or OKXMarketDataClient()
    overview_client = overview_client or OKXMarketOverviewClient()
    BaseHandler = v18.make_v18_handler(
        config,
        service,
        sessions,
        limiter,
        store,
        market_client,
    )

    class V19Handler(BaseHandler):
        server_version = "KriptoPanel/1.9"

        def _render_root_v17(self, session: dict[str, Any]) -> None:
            csrf = html.escape(str(session["csrf"]), quote=True)
            role = str(session.get("role") or ROLE_MEMBER).upper()
            role_label = "Yönetici" if role == ROLE_ADMIN else "Üye"
            username = html.escape(str(session.get("username") or "üye"))
            admin_link = '<a class="badge" href="/admin/users">Kullanıcılar</a>' if role == ROLE_ADMIN else ""
            market_link = '<a class="badge" href="/market-center">Piyasa Merkezi</a>'
            profile_link = '<a class="badge" href="/account">Hesabım</a>'
            account_badge = f'<span class="badge">{role_label} · {username}</span>'
            logout = (
                '<form method="post" action="/logout">'
                f'<input type="hidden" name="csrf" value="{csrf}">'
                '<button class="badge" type="submit">Çıkış</button>'
                '</form>'
            )
            nonce = secrets.token_urlsafe(18)
            body = render_dashboard(
                None,
                live_endpoint="/api/dashboard",
                market_endpoint="/api/market/candles",
                refresh_seconds=config.refresh_seconds,
                script_nonce=nonce,
                top_action_html=admin_link + market_link + profile_link + account_badge + logout,
            )
            self._send(HTTPStatus.OK, body, "text/html; charset=utf-8", nonce=nonce)

        def do_GET(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if path == "/healthz":
                self._json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
                return
            if path == "/market-center":
                if not self._session():
                    self._redirect("/login")
                    return
                nonce = secrets.token_urlsafe(18)
                self._send(
                    HTTPStatus.OK,
                    market_center_page(nonce),
                    "text/html; charset=utf-8",
                    nonce=nonce,
                )
                return
            if path == "/api/market/overview":
                session = self._session()
                if not session:
                    self._json(HTTPStatus.UNAUTHORIZED, {"error": "authentication_required"})
                    return
                query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True, max_num_fields=2)
                requested_raw = (query.get("symbols") or [""])[0]
                try:
                    filtered_data = dashboard_for_session(service.get_data(), session)
                    if requested_raw.strip():
                        symbols = normalize_market_symbols(requested_raw.split(","))
                    else:
                        symbols = select_market_symbols(filtered_data)
                    payload = overview_client.get_overview(symbols)
                    context = market_context(filtered_data)
                    for item in payload["items"]:
                        item.update(context.get(str(item.get("symbol") or ""), {}))
                    payload["version"] = VERSION
                    self._json(HTTPStatus.OK, payload)
                except ValueError as exc:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_market_request", "message": str(exc)})
                except MarketDataError:
                    self._json(HTTPStatus.BAD_GATEWAY, {"error": "market_data_unavailable"})
                except Exception:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "market_overview_unavailable"})
                return
            return super().do_GET()

    return V19Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Kripto Kontrol Paneli V1.9 canlı piyasa merkezi.")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8080")))
    parser.add_argument("--root", default=".")
    args = parser.parse_args()

    config = PanelConfig.from_env(Path(args.root))
    config.validate()
    service = build_service(config)
    sessions = v18.v17.ManagedSessionStore(config.session_hours * 3600)
    limiter = LoginRateLimiter()
    store = v18.account_store_from_env(config)
    handler = make_v19_handler(config, service, sessions, limiter, store)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(
        f"{VERSION} http://{args.host}:{args.port} "
        f"users_ref={store.ref} users_store={'on' if store.configured else 'off'} market_center=on"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
