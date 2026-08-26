"""All-coins runner for Market Structure AI shadow learning + early warnings.

Scans verified live OKX crypto USDT perpetuals with closed 5M candles and records
WATCH/READY structure reversals. Optional Telegram messages are informational
early warnings only; this runner never creates exchange orders or Premium trade
entries.
"""
from __future__ import annotations

import os
import time
from typing import Any, Dict, List, Tuple

import ccxt
import pandas as pd

import crypto_universe_guard as universe_guard
import market_structure_ai_shadow as structure
import market_structure_early_alerts as alerts

MAX_COINS = int(os.getenv("MARKET_STRUCTURE_MAX_COINS", "300"))
MIN_QUOTE_VOLUME = float(os.getenv("MARKET_STRUCTURE_MIN_QUOTE_VOLUME", "100000"))
FETCH_LIMIT = int(os.getenv("MARKET_STRUCTURE_FETCH_LIMIT", "90"))
ALERTS_ENABLED = str(os.getenv("MARKET_STRUCTURE_ALERTS_ENABLED", "0")).strip() == "1"
MAX_ALERTS_PER_RUN = int(os.getenv("MARKET_STRUCTURE_MAX_ALERTS_PER_RUN", "4"))
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")


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
        market.get("active") is True
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

    # Reuse the same strict universe policy as the Premium crypto bot. Positive
    # crypto metadata is required; RWA/stock/index-style swaps fail closed. When
    # read-only OKX credentials are available, the account-specific instrument
    # allowlist is also applied.
    universe_guard.refresh_account_tradable_futures_from_env()
    filtered_markets, excluded = universe_guard.filter_crypto_markets(markets)
    if excluded:
        counts: Dict[str, int] = {}
        for row in excluded:
            reason = str(row.get("reason") or "UNKNOWN")
            counts[reason] = counts.get(reason, 0) + 1
        print(
            "MARKET STRUCTURE CRYPTO-ONLY GUARD | excluded=",
            len(excluded),
            "| verified_crypto_swaps=",
            len(universe_guard.VERIFIED_LIVE_FUTURES_SYMBOLS),
            "| account_allowlist=",
            "ON" if universe_guard.ACCOUNT_ALLOWLIST_ACTIVE else "OFF",
            "| reasons=",
            counts,
        )

    tickers = exchange.fetch_tickers()
    rows: List[Tuple[str, str, Dict[str, Any], float]] = []
    for ccxt_symbol, market in filtered_markets.items():
        if not _is_usdt_swap(market):
            continue
        ticker = tickers.get(ccxt_symbol) or {}
        volume = _quote_volume(ticker)
        if volume < MIN_QUOTE_VOLUME:
            continue
        raw_symbol = universe_guard.market_bot_symbol(market, ccxt_symbol)
        if not raw_symbol:
            continue
        if not universe_guard.is_verified_live_futures_symbol(raw_symbol):
            continue
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
    sent_alerts = 0

    print(
        "MARKET STRUCTURE AI SHADOW START:",
        structure.VERSION,
        "| coins=",
        len(universe),
        "| early_alerts=",
        "ON" if ALERTS_ENABLED else "OFF",
        "| max_alerts=",
        MAX_ALERTS_PER_RUN,
        "| crypto_only=ON | orders=OFF",
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

                if ALERTS_ENABLED and sent_alerts < MAX_ALERTS_PER_RUN:
                    sent, reason = alerts.send_event(event, TOKEN, CHAT_ID)
                    if sent:
                        sent_alerts += 1
                        print(
                            "MARKET STRUCTURE EARLY ALERT SENT:",
                            raw_symbol,
                            result.get("direction"),
                            result.get("stage"),
                            "reason=",
                            reason,
                        )
                    elif reason not in {
                        "WATCH_SCORE_LOW",
                        "WATCH_DIRECTION_AMBIGUOUS",
                        "WATCH_TOO_FAR_FROM_ORIGIN",
                        "WATCH_15M_OPPOSING",
                        "WATCH_ORIGIN_EVIDENCE_WEAK",
                        "WATCH_TURN_NOT_STARTED",
                        "READY_SCORE_LOW",
                        "READY_DIRECTION_AMBIGUOUS",
                        "READY_TOO_FAR_FROM_ORIGIN",
                        "NOT_ALERT_STAGE",
                    }:
                        print(
                            "MARKET STRUCTURE EARLY ALERT NOT SENT:",
                            raw_symbol,
                            reason,
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
        "| alerts_sent=",
        sent_alerts,
        "| errors=",
        errors,
        "| summary=",
        summary,
    )


if __name__ == "__main__":
    run()
