"""Cross-exchange mover radar for Market First.

Observes Binance USD-M perpetual movers and uses them only as a discovery layer.
If the same symbol is tradable on OKX, it is moved to the front of the existing
Market First deep-scan queue. Binance-only movers are recorded so large external
moves are no longer invisible.

This module never creates trades, bypasses A+/A++ gates, widens stops, places
orders, or sends Telegram. Network failures are fail-open: the original OKX
selection continues unchanged.
"""
from __future__ import annotations

import json
import math
import time
import urllib.request
from typing import Any, Dict, Iterable, List, Mapping, Optional

VERSION = "MARKET_FIRST_CROSS_EXCHANGE_MOVER_RADAR_V1_2026_09_27"
BINANCE_FUTURES_24H_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

MIN_QUOTE_VOLUME_USDT = 1_000_000.0
MIN_SAMPLE_MOVE_PERCENT = 1.50
MIN_24H_MOVE_PERCENT = 8.00
MAX_MOVER_RECORDS = 40
MAX_OKX_PRIORITY = 12
HTTP_TIMEOUT_SECONDS = 8.0
HTTP_451_BACKOFF_SECONDS = 6 * 60 * 60

_INSTALLED = False
_LAST_RESULT: Dict[str, Any] = {}


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _pct(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return (end / start - 1.0) * 100.0


def fetch_binance_futures_24h(*, opener=None, timeout: float = HTTP_TIMEOUT_SECONDS) -> List[Dict[str, Any]]:
    """Fetch Binance USD-M 24h tickers from the public API."""
    open_fn = opener or urllib.request.urlopen
    request = urllib.request.Request(
        BINANCE_FUTURES_24H_URL,
        headers={
            "User-Agent": "MarketFirstCrossExchangeRadar/1.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    with open_fn(request, timeout=timeout) as response:
        raw = response.read()
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Binance futures ticker response is not a list")
    return [item for item in payload if isinstance(item, dict)]


def normalize_snapshot(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    snapshot: Dict[str, Dict[str, Any]] = {}
    for item in rows:
        symbol = str(item.get("symbol") or "").upper().strip()
        if not symbol.endswith("USDT"):
            continue
        price = _sf(item.get("lastPrice"))
        if price <= 0:
            continue
        snapshot[symbol] = {
            "symbol": symbol,
            "price": price,
            "quote_volume": _sf(item.get("quoteVolume")),
            "change_24h_percent": _sf(item.get("priceChangePercent")),
        }
    return snapshot


def analyze_snapshot(
    snapshot: Mapping[str, Mapping[str, Any]],
    *,
    previous_prices: Mapping[str, Any],
    okx_symbols: Iterable[str],
    now: Optional[int] = None,
) -> Dict[str, Any]:
    now = int(now or time.time())
    okx_set = {str(symbol).upper() for symbol in okx_symbols}
    movers: List[Dict[str, Any]] = []

    for symbol, item in snapshot.items():
        price = _sf(item.get("price"))
        quote_volume = _sf(item.get("quote_volume"))
        change_24h = _sf(item.get("change_24h_percent"))
        previous = _sf(previous_prices.get(symbol))
        sample_move = _pct(previous, price) if previous > 0 else 0.0

        if quote_volume < MIN_QUOTE_VOLUME_USDT:
            continue
        if abs(sample_move) < MIN_SAMPLE_MOVE_PERCENT and abs(change_24h) < MIN_24H_MOVE_PERCENT:
            continue

        reference_move = sample_move if abs(sample_move) >= MIN_SAMPLE_MOVE_PERCENT else change_24h
        movers.append(
            {
                "symbol": symbol,
                "direction": "LONG" if reference_move > 0 else "SHORT",
                "price": round(price, 12),
                "sample_move_percent": round(sample_move, 4),
                "change_24h_percent": round(change_24h, 4),
                "quote_volume": round(quote_volume, 2),
                "okx_tradable": symbol in okx_set,
                "detected_at": now,
            }
        )

    movers.sort(
        key=lambda row: (
            abs(_sf(row.get("sample_move_percent"))) >= MIN_SAMPLE_MOVE_PERCENT,
            abs(_sf(row.get("sample_move_percent"))),
            abs(_sf(row.get("change_24h_percent"))),
            _sf(row.get("quote_volume")),
        ),
        reverse=True,
    )
    movers = movers[:MAX_MOVER_RECORDS]
    okx_priority = [row["symbol"] for row in movers if row.get("okx_tradable")][:MAX_OKX_PRIORITY]
    external_only = [row for row in movers if not row.get("okx_tradable")]

    return {
        "version": VERSION,
        "generated_at": now,
        "thresholds": {
            "min_quote_volume_usdt": MIN_QUOTE_VOLUME_USDT,
            "min_sample_move_percent": MIN_SAMPLE_MOVE_PERCENT,
            "min_24h_move_percent": MIN_24H_MOVE_PERCENT,
            "max_okx_priority": MAX_OKX_PRIORITY,
        },
        "movers": movers,
        "okx_priority_symbols": okx_priority,
        "external_only_movers": external_only,
    }


def scan(
    okx_symbols: Iterable[str],
    *,
    previous_state: Optional[Mapping[str, Any]] = None,
    now: Optional[int] = None,
    opener=None,
) -> Dict[str, Any]:
    """Run one discovery scan. Returns a state payload and never raises."""
    now = int(now or time.time())
    previous_state = previous_state if isinstance(previous_state, Mapping) else {}
    previous_prices = previous_state.get("previous_prices")
    previous_prices = previous_prices if isinstance(previous_prices, Mapping) else {}

    blocked_until = int(_sf(previous_state.get("blocked_until")))
    if blocked_until > now:
        return {
            "version": VERSION,
            "generated_at": now,
            "fetch_ok": False,
            "error": "BINANCE_HTTP_451_BACKOFF",
            "movers": [],
            "okx_priority_symbols": [],
            "external_only_movers": [],
            "previous_prices": dict(previous_prices),
            "blocked_until": blocked_until,
            "note": "Fail-open: Binance 451 backoff active; existing OKX Market First selection continues unchanged.",
        }

    try:
        raw = fetch_binance_futures_24h(opener=opener)
        snapshot = normalize_snapshot(raw)
        result = analyze_snapshot(
            snapshot,
            previous_prices=previous_prices,
            okx_symbols=okx_symbols,
            now=now,
        )
        result["fetch_ok"] = True
        result["binance_symbols"] = len(snapshot)
        result["error"] = None
        result["blocked_until"] = 0
        result["previous_prices"] = {
            symbol: _sf(item.get("price"))
            for symbol, item in snapshot.items()
            if _sf(item.get("price")) > 0
        }
        return result
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        is_http_451 = "451" in error
        return {
            "version": VERSION,
            "generated_at": now,
            "fetch_ok": False,
            "error": error,
            "movers": [],
            "okx_priority_symbols": [],
            "external_only_movers": [],
            "previous_prices": dict(previous_prices),
            "blocked_until": now + HTTP_451_BACKOFF_SECONDS if is_http_451 else 0,
            "note": (
                "Fail-open: Binance HTTP 451; 6h backoff active and existing OKX selection continues unchanged."
                if is_http_451
                else "Fail-open: existing OKX Market First selection continues unchanged."
            ),
        }


def install(runner: Any) -> None:
    """Prepend Binance movers to the existing OKX deep-scan selection."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_select = runner._select_deep_scan

    def select_with_cross_exchange(rows, sample_moves, state):
        global _LAST_RESULT
        base = original_select(rows, sample_moves, state)
        okx_symbols = [str(row.get("symbol") or "") for row in rows if row.get("symbol")]
        previous_state = state.get("cross_exchange_mover") if isinstance(state, dict) else {}
        result = scan(okx_symbols, previous_state=previous_state, now=runner.bot.now_ts())
        _LAST_RESULT = result
        if isinstance(state, dict):
            state["cross_exchange_mover"] = result

        priority = [
            str(symbol)
            for symbol in result.get("okx_priority_symbols", [])
            if str(symbol) and str(symbol) not in getattr(runner, "MAJOR_WEIGHTS", {})
        ]
        merged: List[str] = []
        seen = set()
        for symbol in priority + list(base):
            if symbol and symbol not in seen:
                seen.add(symbol)
                merged.append(symbol)
            if len(merged) >= int(getattr(runner, "MAX_DEEP_SCAN", 40)):
                break

        for row in result.get("external_only_movers", [])[:8]:
            print(
                "CROSS-EXCHANGE DIŞ PİYASA HAREKET:",
                row.get("symbol"), row.get("direction"),
                "sample=", row.get("sample_move_percent"),
                "24h=", row.get("change_24h_percent"),
                "| OKX yok",
            )
        if priority:
            print("CROSS-EXCHANGE OKX ÖNCELİK:", priority)
        if not result.get("fetch_ok"):
            print("CROSS-EXCHANGE FAIL-OPEN:", result.get("error"))
        return merged

    runner._select_deep_scan = select_with_cross_exchange


def summary(payload: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    payload = payload if isinstance(payload, Mapping) else _LAST_RESULT
    movers = payload.get("movers") if isinstance(payload.get("movers"), list) else []
    okx_priority = payload.get("okx_priority_symbols") if isinstance(payload.get("okx_priority_symbols"), list) else []
    external_only = payload.get("external_only_movers") if isinstance(payload.get("external_only_movers"), list) else []
    return {
        "version": VERSION,
        "fetch_ok": bool(payload.get("fetch_ok")) if payload else None,
        "movers": len(movers),
        "okx_priority": len(okx_priority),
        "external_only": len(external_only),
        "priority_symbols": okx_priority[:MAX_OKX_PRIORITY],
        "error": payload.get("error") if payload else None,
        "trade_promotion": False,
        "changes_quality_gates": False,
        "exchange_orders": False,
        "telegram": False,
        "state_storage": "market_first_state.json/cross_exchange_mover",
    }
