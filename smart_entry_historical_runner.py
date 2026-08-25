"""Manual runner for historical Smart Entry bootstrap research."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

import ccxt

import smart_entry_historical_bootstrap as research
from crypto_universe_guard import filter_crypto_markets

DEFAULT_SYMBOLS = (
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "ADAUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "SUIUSDT",
    "LTCUSDT",
    "NEARUSDT",
)


def _symbols() -> List[str]:
    raw = str(os.getenv("SMART_ENTRY_SYMBOLS") or "").strip()
    if not raw:
        return list(DEFAULT_SYMBOLS)
    items = []
    seen = set()
    for part in raw.replace(";", ",").split(","):
        symbol = part.strip().upper().replace("/USDT:USDT", "USDT").replace("/", "")
        if not symbol:
            continue
        if not symbol.endswith("USDT"):
            symbol += "USDT"
        if symbol not in seen:
            seen.add(symbol)
            items.append(symbol)
    return items


def _market_map(exchange: Any) -> Dict[str, str]:
    markets = exchange.load_markets()
    filtered, _ = filter_crypto_markets(markets)
    result: Dict[str, str] = {}
    for market in filtered.values():
        if not isinstance(market, dict) or not market.get("swap"):
            continue
        if str(market.get("quote") or "").upper() != "USDT":
            continue
        if str(market.get("settle") or "USDT").upper() != "USDT":
            continue
        if market.get("active") is False:
            continue
        base = str(market.get("base") or "").upper()
        market_symbol = str(market.get("symbol") or "")
        if base and market_symbol:
            result[f"{base}USDT"] = market_symbol
    return result


def main() -> int:
    exchange = ccxt.okx({"enableRateLimit": True, "timeout": 30000})
    selected = _symbols()
    lookback_days = int(os.getenv("SMART_ENTRY_HISTORY_DAYS", str(research.LOOKBACK_DAYS)))
    market_map = _market_map(exchange)
    now_ms = int(time.time() * 1000)

    symbol_results: List[Dict[str, Any]] = []
    raw_rows: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    print("Smart Entry historical bootstrap başlıyor.")
    print("Sembol:", len(selected), "| Gün:", lookback_days)

    for position, symbol in enumerate(selected, start=1):
        market_symbol = market_map.get(symbol)
        if not market_symbol:
            errors.append({"symbol": symbol, "error": "OKX_ACTIVE_USDT_SWAP_NOT_FOUND"})
            print(f"[{position}/{len(selected)}] {symbol}: market yok")
            continue
        try:
            since_ms = now_ms - lookback_days * 24 * 60 * 60 * 1000
            max_bars = lookback_days * 24 * 4 + 100
            df = research._fetch_range(exchange, market_symbol, since_ms, now_ms, max_bars=max_bars)
            events = research.detect_events(symbol, df)
            rows: List[Dict[str, Any]] = []
            for event in events:
                rows.extend(research.evaluate_event(event, df))
            model = research.build_model(rows)
            raw_rows.extend(rows)
            symbol_results.append(
                {
                    "symbol": symbol,
                    "market_symbol": market_symbol,
                    "timeframe": research.TIMEFRAME,
                    "lookback_days": lookback_days,
                    "candle_count": int(len(df)),
                    "event_count": int(len(events)),
                    "hypothesis_count": int(len(rows)),
                    "model": model,
                }
            )
            print(
                f"[{position}/{len(selected)}] {symbol}: mum={len(df)} event={len(events)} "
                f"hipotez={len(rows)} best={model.get('best_validated_zone')}"
            )
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{position}/{len(selected)}] {symbol}: HATA {type(exc).__name__}: {exc}")

    payload = research.aggregate(symbol_results, raw_rows)
    payload["requested_symbols"] = selected
    payload["completed_symbols"] = len(symbol_results)
    payload["errors"] = errors
    research.atomic_save(research.STATE_FILE, payload)

    global_model = payload.get("global_model") or {}
    print("Smart Entry historical bootstrap tamamlandı.")
    print("Global best zone:", global_model.get("best_validated_zone"))
    print("Validated zone:", global_model.get("validated_zone_count"))
    print("State:", research.STATE_FILE)
    return 0 if symbol_results else 1


if __name__ == "__main__":
    raise SystemExit(main())
