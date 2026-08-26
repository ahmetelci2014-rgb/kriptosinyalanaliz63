"""All-coins runner for Market Structure AI shadow learning.

No Telegram messages and no exchange orders. Scans active OKX USDT perpetuals with
closed 5M candles and records WATCH/READY structure reversals.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Tuple

import ccxt
import pandas as pd

import market_structure_ai_shadow as structure

MAX_COINS = int(os.getenv("MARKET_STRUCTURE_MAX_COINS", "300"))
MIN_QUOTE_VOLUME = float(os.getenv("MARKET_STRUCTURE_MIN_QUOTE_VOLUME", "100000"))
FETCH_LIMIT = int(os.getenv("MARKET_STRUCTURE_FETCH_LIMIT", "90"))


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _exchange() -> ccxt.okx:
    return ccxt.okx(
        {
            "enableRateLimit": True,
            "timeout": 15000,
            "options": {"defaultType": "swap"},
        }
    )


def _is_usdt_swap(market: Dict[str, Any]) -> bool:
    return bool(
        market.get("active", True)
        and market.get("swap")
        and str(market.get("quote") or "").upper() == "USDT"
        and str(market.get("settle") or "").upper() == "USDT"
    )


def _quote_volume(ticker: Dict[str, Any]) -> float:
    quote = _sf(ticker.get("quoteVolume"))
    if quote > 0:
        return quote
    base = _sf(ticker.get("baseVolume"))
    last = _sf(ticker.get("last"))
    return base * last


def build_universe(exchange: ccxt.okx) -> List[Tuple[str, str, Dict[str, Any]]]:
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()
    rows: List[Tuple[str, str, Dict[str, Any], float]] = []
    for ccxt_symbol, market in markets.items():
        if not _is_usdt_swap(market):
            continue
        ticker = tickers.get(ccxt_symbol) or {}
        volume = _quote_volume(ticker)
        if volume < MIN_QUOTE_VOLUME:
            continue
        raw_symbol = str(market.get("id") or "").replace("-USDT-SWAP", "USDT")
        if not raw_symbol:
            raw_symbol = str(market.get("base") or "").upper() + "USDT"
        rows.append((ccxt_symbol, raw_symbol, ticker, volume))
    rows.sort(key=lambda item: item[3], reverse=True)
    return [(a, b, c) for a, b, c, _ in rows[:MAX_COINS]]


def fetch_df(exchange: ccxt.okx, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
    candles = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    return pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])


def run() -> None:
    exchange = _exchange()
    universe = build_universe(exchange)
    structure.begin()
    started = time.time()
    events: List[Dict[str, Any]] = []
    errors = 0

    print(
        "MARKET STRUCTURE AI SHADOW START:",
        structure.VERSION,
        "| coins=",
        len(universe),
        "| telegram=OFF | orders=OFF",
    )

    try:
        for ccxt_symbol, raw_symbol, ticker in universe:
            try:
                df5m = fetch_df(exchange, ccxt_symbol, "5m", FETCH_LIMIT)
                current = _sf(ticker.get("last"))
                event = structure.observe(raw_symbol, df5m, None, current)
                if event is None:
                    continue
                result = event.get("result") or {}
                record = event.get("record") or {}
                events.append(
                    {
                        "symbol": raw_symbol,
                        "direction": result.get("direction"),
                        "stage": result.get("stage"),
                        "score": result.get("score"),
                        "origin": record.get("origin"),
                        "entry": record.get("entry"),
                        "origin_distance_atr": record.get("origin_distance_atr"),
                        "origin_distance_percent": record.get("origin_distance_percent"),
                        "event": event.get("event"),
                    }
                )
                print(
                    "MARKET STRUCTURE:",
                    raw_symbol,
                    result.get("direction"),
                    result.get("stage"),
                    "score=",
                    result.get("score"),
                    "origin=",
                    record.get("origin"),
                    "entry=",
                    record.get("entry"),
                    "originATR=",
                    record.get("origin_distance_atr"),
                    "origin%=",
                    record.get("origin_distance_percent"),
                    "event=",
                    event.get("event"),
                )
            except Exception as exc:
                errors += 1
                if errors <= 12:
                    print("MARKET STRUCTURE scan error:", raw_symbol, type(exc).__name__, str(exc)[:160])
    finally:
        summary = structure.finish()

    events.sort(
        key=lambda item: (
            0 if item.get("stage") == "READY" else 1,
            _sf(item.get("origin_distance_atr"), 999.0),
            -_sf(item.get("score")),
        )
    )
    print("MARKET STRUCTURE TOP EARLY:", events[:15])
    print(
        "MARKET STRUCTURE AI SHADOW END | elapsed_s=",
        round(time.time() - started, 2),
        "| events=",
        len(events),
        "| errors=",
        errors,
        "| summary=",
        summary,
    )


if __name__ == "__main__":
    run()
