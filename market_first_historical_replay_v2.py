"""Minute-level capture overlay for Market First historical ML seeding.

The first historical pass proved the research pipeline works, but evaluating only
at 5-minute candle closes missed the short 1m/3m acceleration window that live
Market First often sees. This V2 keeps the same no-lookahead feature rules and
outcome labelling, but replays minute offsets inside historically interesting
5m bars. The 5m bar is used only to choose *where to research*; every feature
frame is still sliced strictly to the evaluation minute before analyze_candidate
runs.

It also orders research batches with the same quote-volume fallback used by the
live runner, so liquid contracts are researched first instead of alphabetical
order when CCXT does not expose direct quoteVolume.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping

import market_first_historical_ml_seed as seed
import crypto_universe_guard as universe_guard
import market_first_crypto_purity as purity
from market_first_strategy import MAJOR_WEIGHTS

CAPTURE_VERSION = "MARKET_FIRST_HISTORICAL_MINUTE_CAPTURE_V2_2026_08_29"


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


def select_eval_times_v2(df5, start_ms: int, end_ms: int, *, max_events: int = 120) -> List[int]:
    """Replay 1m..5m offsets inside selected 5m movement windows.

    The selector may use the completed 5m bar ex-post because it is only a
    research sampler. No value from that completed bar is passed to a feature at
    an earlier offset: seed._candidate_frames and seed._context_at slice every
    timeframe back to the requested eval timestamp.
    """
    if df5 is None or len(df5) < 100:
        return []

    event_starts: List[int] = []
    last_event = 0
    min_gap = 20 * 60_000

    for idx in range(3, len(df5)):
        row = df5.iloc[idx]
        bar_start = int(_sf(row["time"]))
        bar_end = bar_start + seed.TF_MS["5m"]
        if bar_end < start_ms or bar_start > end_ms:
            continue

        open5 = _sf(row["open"])
        close = _sf(row["close"])
        high = _sf(row["high"])
        low = _sf(row["low"])
        open15 = _sf(df5.iloc[idx - 2]["open"])
        if min(open5, close, high, low, open15) <= 0:
            continue

        move5 = abs(_pct(open5, close))
        move15 = abs(_pct(open15, close))
        range5 = abs(_pct(low, high))
        interesting = move5 >= 0.08 or move15 >= 0.18 or range5 >= 0.25
        if not interesting:
            continue
        if last_event and bar_start - last_event < min_gap:
            continue
        event_starts.append(bar_start)
        last_event = bar_start

    eval_times: List[int] = []
    for bar_start in event_starts:
        for minute in (1, 2, 3, 4, 5):
            eval_ms = bar_start + minute * seed.TF_MS["1m"]
            if start_ms <= eval_ms <= end_ms:
                eval_times.append(eval_ms)

    eval_times = sorted(set(eval_times))
    return seed._even_sample(eval_times, max_events)


def _quote_volume(ticker: Mapping[str, Any]) -> float:
    direct = _sf(ticker.get("quoteVolume"))
    if direct > 0:
        return direct
    base = _sf(ticker.get("baseVolume"))
    last = _sf(ticker.get("last") or ticker.get("close"))
    if base > 0 and last > 0:
        return base * last
    info = ticker.get("info") if isinstance(ticker.get("info"), Mapping) else {}
    info_quote = _sf(info.get("volCcy24h"))
    return info_quote if info_quote > 0 else 0.0


def market_symbol_map_v2(exchange: Any) -> List[Dict[str, Any]]:
    markets = exchange.load_markets()
    rows: List[Dict[str, Any]] = []
    for key, market in (markets or {}).items():
        if not isinstance(market, Mapping):
            continue
        if purity.crypto_derivative_exclusion_reason(market):
            continue
        compact = universe_guard.market_bot_symbol(dict(market), key)
        ccxt_symbol = str(market.get("symbol") or "").strip()
        if not compact or not ccxt_symbol or compact in MAJOR_WEIGHTS:
            continue
        rows.append(
            {
                "symbol": compact,
                "market_symbol": ccxt_symbol,
                "contract_size": _sf(market.get("contractSize"), 1.0),
            }
        )

    try:
        tickers = exchange.fetch_tickers()
    except Exception:
        tickers = {}
    for row in rows:
        ticker = (tickers or {}).get(row["market_symbol"], {})
        row["research_sort_volume"] = _quote_volume(ticker or {})

    rows.sort(key=lambda item: (-_sf(item.get("research_sort_volume")), item["symbol"]))
    return rows


def _reset_cursor_once_for_v2() -> None:
    state = seed._load_json(
        seed.STATE_FILE,
        {"version": seed.VERSION, "cursor": 0, "runs": 0, "processed_symbols": []},
    )
    if str(state.get("capture_version") or "") == CAPTURE_VERSION:
        return
    state["cursor"] = 0
    state["capture_version"] = CAPTURE_VERSION
    state["capture_reset_reason"] = "5m-close V1 produced no trade-eligible historical labels"
    seed._atomic_json(seed.STATE_FILE, state)


def run():
    _reset_cursor_once_for_v2()
    seed.select_eval_times = select_eval_times_v2
    seed._market_symbol_map = market_symbol_map_v2
    return seed.run()


if __name__ == "__main__":
    run()
