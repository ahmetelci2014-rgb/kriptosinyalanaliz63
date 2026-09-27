"""Cross-exchange mover radar for Market First.

Purpose:
- observe Binance USDT perpetual movers without changing the OKX execution venue,
- push Binance movers to the front of the existing OKX deep-scan queue only when
  the same symbol is currently tradable as an OKX USDT perpetual,
- record Binance-only movers so large external moves are no longer invisible,
- never create a trade, bypass Market First quality gates, place orders, widen
  stops, or send Telegram by itself.

The module is deliberately fail-open. If Binance public data is unavailable, the
existing OKX-only runner proceeds exactly as before.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
import urllib.request
from typing import Any, Dict, Iterable, List, Mapping, Optional

VERSION = "MARKET_FIRST_CROSS_EXCHANGE_MOVER_RADAR_V1_2026_09_27"
STATE_FILE = "market_first_cross_exchange_mover_state.json"
BINANCE_FUTURES_24H_URL = "https://fapi.binance.com/fapi/v1/ticker/24hr"

MIN_QUOTE_VOLUME_USDT = 1_000_000.0
MIN_SAMPLE_MOVE_PERCENT = 1.50
MIN_24H_MOVE_PERCENT = 8.00
MAX_MOVER_RECORDS = 40
MAX_OKX_PRIORITY = 12
HTTP_TIMEOUT_SECONDS = 8.0


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


def _load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save_state(payload: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".cross_exchange_mover.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, STATE_FILE)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def fetch_binance_futures_24h(*, opener=None, timeout: float = HTTP_TIMEOUT_SECONDS) -> List[Dict[str, Any]]:
    """Fetch Binance USD-M 24h ticker data using a public endpoint only."""
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
        quote_volume = _sf(item.get("quoteVolume"))
        change_24h = _sf(item.get("priceChangePercent"))
        snapshot[symbol] = {
            "symbol": symbol,
            "price": price,
            "quote_volume": quote_volume,
            "change_24h_percent": change_24h,
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

        direction = "LONG" if (sample_move if abs(sample_move) >= MIN_SAMPLE_MOVE_PERCENT else change_24h) > 0 else "SHORT"
        on_okx = symbol in okx_set
        movers.append(
            {
                "symbol": symbol,
                "direction": direction,
                "price": round(price, 12),
                "sample_move_percent": round(sample_move, 4),
                "change_24h_percent": round(change_24h, 4),
                "quote_volume": round(quote_volume, 2),
                "okx_tradable": on_okx,
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


def scan(okx_symbols: Iterable[str], *, now: Optional[int] = None, opener=None) -> Dict[str, Any]:
    """Run one Binance mover scan. Never raises into the live trading runner."""
    now = int(now or time.time())
    state = _load_state()
    previous_prices = state.get("previous_prices") if isinstance(state.get("previous_prices"), dict) else {}

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
        result["previous_prices"] = {
            symbol: _sf(item.get("price"))
            for symbol, item in snapshot.items()
            if _sf(item.get("price")) > 0
        }
        _save_state(result)
        return result
    except Exception as exc:
        payload = {
            "version": VERSION,
            "generated_at": now,
            "fetch_ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "movers": [],
            "okx_priority_symbols": [],
            "external_only_movers": [],
            "previous_prices": dict(previous_prices),
            "note": "Fail-open: existing OKX Market First runner continues unchanged.",
        }
        _save_state(payload)
        return payload


def summary(payload: Mapping[str, Any]) -> Dict[str, Any]:
    movers = payload.get("movers") if isinstance(payload.get("movers"), list) else []
    okx_priority = payload.get("okx_priority_symbols") if isinstance(payload.get("okx_priority_symbols"), list) else []
    external_only = payload.get("external_only_movers") if isinstance(payload.get("external_only_movers"), list) else []
    return {
        "version": VERSION,
        "fetch_ok": bool(payload.get("fetch_ok")),
        "movers": len(movers),
        "okx_priority": len(okx_priority),
        "external_only": len(external_only),
        "priority_symbols": okx_priority[:MAX_OKX_PRIORITY],
        "error": payload.get("error"),
        "trade_promotion": False,
        "exchange_orders": False,
        "telegram": False,
    }
