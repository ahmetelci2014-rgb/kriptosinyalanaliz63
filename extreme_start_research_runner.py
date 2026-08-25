"""Manual runner for historical Extreme Start research."""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List

import ccxt

import extreme_start_research as research
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
    "CHIPUSDT",
)


def _symbols() -> List[str]:
    raw = str(os.getenv("EXTREME_START_SYMBOLS") or "").strip()
    if not raw:
        return list(DEFAULT_SYMBOLS)
    items: List[str] = []
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
    lookback_days = int(os.getenv("EXTREME_START_HISTORY_DAYS", str(research.LOOKBACK_DAYS)))
    market_map = _market_map(exchange)
    now_ms = int(time.time() * 1000)

    all_rows: List[Dict[str, Any]] = []
    symbols: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []

    print("Extreme Start tarihsel araştırması başlıyor.")
    print("Sembol:", len(selected), "| Gün:", lookback_days)

    for position, symbol in enumerate(selected, start=1):
        market_symbol = market_map.get(symbol)
        if not market_symbol:
            errors.append({"symbol": symbol, "error": "OKX_ACTIVE_USDT_SWAP_NOT_FOUND"})
            print(f"[{position}/{len(selected)}] {symbol}: market yok")
            continue
        try:
            since_ms = now_ms - lookback_days * 24 * 60 * 60 * 1000
            max_bars = lookback_days * 24 * 4 + 200
            raw = research.fetch_range(exchange, market_symbol, since_ms, now_ms, max_bars=max_bars)
            setups = research.detect_setups(symbol, raw)
            enriched = research.enrich_15m(raw)
            rows = [research.evaluate_setup(setup, enriched) for setup in setups]
            all_rows.extend(rows)
            symbol_report = research.build_report(rows)
            symbols.append(
                {
                    "symbol": symbol,
                    "market_symbol": market_symbol,
                    "lookback_days": lookback_days,
                    "candle_count": int(len(raw)),
                    "setup_count": int(len(rows)),
                    "report": {k: v for k, v in symbol_report.items() if k != "rows"},
                }
            )
            holdout = symbol_report.get("holdout") or {}
            print(
                f"[{position}/{len(selected)}] {symbol}: mum={len(raw)} setup={len(rows)} "
                f"holdout={holdout.get('sample', 0)} SL={holdout.get('sl_first_rate_percent', '-')}% "
                f"TP3={holdout.get('tp3_rate_percent', '-')}%"
            )
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            print(f"[{position}/{len(selected)}] {symbol}: HATA {type(exc).__name__}: {exc}")

    report = research.build_report(all_rows)
    report["requested_symbols"] = selected
    report["completed_symbols"] = len(symbols)
    report["symbol_reports"] = symbols
    report["errors"] = errors
    research.atomic_save(research.STATE_FILE, report)

    holdout = report.get("holdout") or {}
    print("Extreme Start araştırması tamamlandı.")
    print("Toplam setup:", (report.get("all") or {}).get("sample", 0))
    print("Holdout setup:", holdout.get("sample", 0))
    print("Holdout SL-first:", holdout.get("sl_first_rate_percent"))
    print("Holdout TP3:", holdout.get("tp3_rate_percent"))
    print("State:", research.STATE_FILE)
    return 0 if symbols else 1


if __name__ == "__main__":
    raise SystemExit(main())
