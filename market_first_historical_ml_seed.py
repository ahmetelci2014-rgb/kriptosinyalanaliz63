"""No-lookahead historical ML seeding for the single Market First V5 system.

Purpose
-------
The live Random Forest should not have to wait for 120 brand-new live trades
before it can learn anything. This research runner replays the *current*
Market First candidate logic on historical OKX candles and writes only resolved,
chronological training examples into ``market_first_ml_samples.json``.

Safety / leakage rules
----------------------
- no Telegram and no exchange orders;
- features use only candles closed at the historical evaluation timestamp;
- future candles are touched only after the feature vector is frozen, solely to
  create the outcome label;
- current ticker volume is used only to choose which symbols a batch researches,
  never as a historical model feature;
- historical market-wide breadth is intentionally neutral (0.50) rather than
  reconstructed with future-biased/current-universe data;
- historical order-book/CVD/OI/funding fields remain unavailable (zero + their
  existing availability flags), so the model can distinguish missing data;
- same-5m-bar TP1/SL collisions are ambiguous and are not labelled.

This is a seed/validation data source, not a second trading strategy. Live hard
rules, pre-send safety guards and manual-order behavior remain unchanged.
"""
from __future__ import annotations

from collections import Counter
import json
import math
import os
import tempfile
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import ccxt
import pandas as pd

import crypto_universe_guard as universe_guard
import market_first_audit_layer as audit
import market_first_crypto_purity as purity
from market_first_ml import (
    FEATURE_NAMES,
    MODEL_VERSION,
    bundle_summary,
    extract_features,
    load_store,
    save_store,
    train_quality_model,
)
from market_first_pre_send_guard import evaluate_pre_send_market
from market_first_strategy import (
    MAJOR_WEIGHTS,
    TP1_R,
    VERSION as STRATEGY_VERSION,
    analyze_candidate,
    build_market_context,
)

VERSION = "MARKET_FIRST_HISTORICAL_ML_SEED_V1_2026_08_29"
STATE_FILE = "market_first_historical_seed_state.json"
REPORT_FILE = "market_first_historical_seed_report.json"

LOOKBACK_DAYS = int(os.getenv("MARKET_FIRST_HIST_LOOKBACK_DAYS", "45"))
BATCH_SYMBOLS = int(os.getenv("MARKET_FIRST_HIST_BATCH_SYMBOLS", "20"))
MAX_EVALS_PER_SYMBOL = int(os.getenv("MARKET_FIRST_HIST_MAX_EVALS", "50"))
OUTCOME_HOURS = int(os.getenv("MARKET_FIRST_HIST_OUTCOME_HOURS", "18"))
MIN_HISTORICAL_QUOTE_VOLUME = float(
    os.getenv("MARKET_FIRST_HIST_MIN_QUOTE_VOLUME", "250000")
)
# Conservative taker-fee + modest slippage proxy. Labels are based on net R.
ROUND_TRIP_COST_PERCENT = float(os.getenv("MARKET_FIRST_HIST_COST_PERCENT", "0.12"))

TF_MS = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 60 * 60_000,
}


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


def _frame(rows: Iterable[Iterable[Any]]) -> pd.DataFrame:
    data = list(rows or [])
    if not data:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume"])
    for col in ("time", "open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return (
        frame.dropna()
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )


def _load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else dict(default)
    except Exception:
        return dict(default)


def _atomic_json(path: str, data: Mapping[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(data), handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _fetch_range(
    exchange: Any,
    market_symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
    *,
    max_bars: int,
) -> pd.DataFrame:
    """Paginate OHLCV in chronological order without crossing ``until_ms``."""
    tf_ms = TF_MS[timeframe]
    cursor = int(since_ms)
    rows: Dict[int, List[Any]] = {}
    loops = 0
    while cursor < until_ms and len(rows) < max_bars and loops < 240:
        loops += 1
        limit = min(300, max(10, max_bars - len(rows)))
        batch = exchange.fetch_ohlcv(
            market_symbol,
            timeframe=timeframe,
            since=cursor,
            limit=limit,
        )
        if not batch:
            break
        for raw in batch:
            if not isinstance(raw, (list, tuple)) or len(raw) < 6:
                continue
            ts = int(raw[0])
            if since_ms <= ts <= until_ms:
                rows[ts] = list(raw[:6])
        next_cursor = int(batch[-1][0]) + tf_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if int(batch[-1][0]) >= until_ms - tf_ms:
            break
    return _frame(rows[key] for key in sorted(rows))


def _fetch_1m_window(exchange: Any, market_symbol: str, eval_ms: int) -> pd.DataFrame:
    since_ms = eval_ms - 50 * TF_MS["1m"]
    rows = exchange.fetch_ohlcv(market_symbol, timeframe="1m", since=since_ms, limit=80)
    frame = _frame(rows)
    if frame.empty:
        return frame
    # A 1m candle is usable only after its close. No future row survives.
    return frame[(frame["time"] + TF_MS["1m"]) <= eval_ms].tail(45).reset_index(drop=True)


def _closed_slice(
    frame: pd.DataFrame,
    eval_ms: int,
    timeframe: str,
    *,
    tail: int = 100,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    tf_ms = TF_MS[timeframe]
    return (
        frame[(frame["time"] + tf_ms) <= eval_ms]
        .tail(tail)
        .copy()
        .reset_index(drop=True)
    )


def _structure_input(
    frame: pd.DataFrame,
    eval_ms: int,
    timeframe: str,
    current_price: float,
) -> Optional[pd.DataFrame]:
    """Closed bars + one synthetic current bar that strategy drops internally."""
    closed = _closed_slice(frame, eval_ms, timeframe, tail=100)
    if len(closed) < 60 or current_price <= 0:
        return None
    synthetic = pd.DataFrame(
        [[eval_ms, current_price, current_price, current_price, current_price, 0.0]],
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    return pd.concat([closed, synthetic], ignore_index=True)


def _even_sample(values: Sequence[int], limit: int) -> List[int]:
    values = list(values)
    if limit <= 0 or len(values) <= limit:
        return values
    if limit == 1:
        return [values[len(values) // 2]]
    result: List[int] = []
    last_index = len(values) - 1
    for pos in range(limit):
        idx = int(round(pos * last_index / (limit - 1)))
        value = values[idx]
        if not result or value != result[-1]:
            result.append(value)
    return result


def select_eval_times(
    df5: pd.DataFrame,
    start_ms: int,
    end_ms: int,
    *,
    max_events: int = MAX_EVALS_PER_SYMBOL,
) -> List[int]:
    """Cheap 5m prefilter; exact Market First still runs later with real 1m data."""
    if df5 is None or len(df5) < 100:
        return []
    candidates: List[int] = []
    last_eval = 0
    min_gap = 20 * 60_000

    for idx in range(3, len(df5)):
        row = df5.iloc[idx]
        eval_ms = int(_sf(row["time"])) + TF_MS["5m"]
        if eval_ms < start_ms or eval_ms > end_ms:
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
        interesting = move5 >= 0.10 or move15 >= 0.22 or range5 >= 0.35
        if not interesting:
            continue
        if last_eval and eval_ms - last_eval < min_gap:
            continue
        candidates.append(eval_ms)
        last_eval = eval_ms

    return _even_sample(candidates, max_events)


def _historical_quote_volume(
    df5: pd.DataFrame,
    eval_ms: int,
    contract_size: float,
) -> float:
    closed = _closed_slice(df5, eval_ms, "5m", tail=288)
    if len(closed) < 48:
        return 0.0
    size = contract_size if contract_size > 0 else 1.0
    quote = (
        pd.to_numeric(closed["close"], errors="coerce")
        * pd.to_numeric(closed["volume"], errors="coerce")
        * size
    ).fillna(0.0)
    return max(0.0, float(quote.sum()))


def resolve_historical_outcome(
    direction: str,
    entry: float,
    sl: float,
    tp1: float,
    risk_percent: float,
    future5: pd.DataFrame,
    *,
    round_trip_cost_percent: float = ROUND_TRIP_COST_PERCENT,
) -> Dict[str, Any]:
    """Resolve TP1-vs-SL first touch, then net-R timeout if neither was touched."""
    direction = str(direction or "").upper()
    if min(entry, sl, tp1, risk_percent) <= 0 or future5 is None or future5.empty:
        return {"label": None, "result": "DATA_MISSING", "net_r": None}

    cost_r = max(0.0, round_trip_cost_percent) / risk_percent
    for _, bar in future5.iterrows():
        high = _sf(bar["high"])
        low = _sf(bar["low"])
        if direction == "LONG":
            sl_hit = low <= sl
            tp_hit = high >= tp1
        else:
            sl_hit = high >= sl
            tp_hit = low <= tp1

        if sl_hit and tp_hit:
            return {"label": None, "result": "AMBIGUOUS_SAME_BAR", "net_r": None}
        if sl_hit:
            net_r = -1.0 - cost_r
            return {"label": 0, "result": "HIST_SL_FIRST", "net_r": round(net_r, 6)}
        if tp_hit:
            net_r = TP1_R - cost_r
            return {
                "label": 1 if net_r > 0 else 0,
                "result": "HIST_TP1_FIRST",
                "net_r": round(net_r, 6),
            }

    last_close = _sf(future5.iloc[-1]["close"])
    if last_close <= 0:
        return {"label": None, "result": "DATA_MISSING", "net_r": None}
    raw_percent = _pct(entry, last_close)
    directional_percent = raw_percent if direction == "LONG" else -raw_percent
    gross_r = directional_percent / risk_percent
    net_r = gross_r - cost_r
    return {
        "label": 1 if net_r > 0 else 0,
        "result": "HIST_TIMEOUT_NET_R",
        "net_r": round(net_r, 6),
    }


def _market_symbol_map(exchange: Any) -> List[Dict[str, Any]]:
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

    # Current volume only orders research batches. It never enters historical
    # feature generation and therefore cannot leak into labels/features.
    try:
        tickers = exchange.fetch_tickers()
    except Exception:
        tickers = {}
    for row in rows:
        ticker = (tickers or {}).get(row["market_symbol"], {})
        row["research_sort_volume"] = _sf((ticker or {}).get("quoteVolume"), 0.0)
    rows.sort(key=lambda item: (-_sf(item.get("research_sort_volume")), item["symbol"]))
    return rows


def _major_market_symbols(exchange: Any) -> Dict[str, str]:
    markets = exchange.load_markets()
    result: Dict[str, str] = {}
    for key, market in (markets or {}).items():
        if not isinstance(market, Mapping):
            continue
        compact = universe_guard.market_bot_symbol(dict(market), key)
        if compact not in MAJOR_WEIGHTS:
            continue
        if purity.crypto_derivative_exclusion_reason(market):
            continue
        result[compact] = str(market.get("symbol") or "")
    return result


def _prefetch_major_cache(
    exchange: Any,
    major_symbols: Mapping[str, str],
    start_ms: int,
    end_ms: int,
) -> Dict[str, Dict[str, pd.DataFrame]]:
    warmup = {
        "5m": 12 * 60 * 60_000,
        "15m": 4 * 24 * 60 * 60_000,
        "1h": 10 * 24 * 60 * 60_000,
        "4h": 28 * 24 * 60 * 60_000,
    }
    cache: Dict[str, Dict[str, pd.DataFrame]] = {}
    for compact, market_symbol in major_symbols.items():
        cache[compact] = {}
        for timeframe in ("5m", "15m", "1h", "4h"):
            since = start_ms - warmup[timeframe]
            bars = int((end_ms - since) / TF_MS[timeframe]) + 20
            cache[compact][timeframe] = _fetch_range(
                exchange,
                market_symbol,
                timeframe,
                since,
                end_ms,
                max_bars=max(120, bars),
            )
    return cache


def _context_at(
    major_cache: Mapping[str, Mapping[str, pd.DataFrame]],
    eval_ms: int,
) -> Tuple[Optional[Any], Dict[str, float]]:
    payloads: Dict[str, Dict[str, Any]] = {}
    major_moves: Dict[str, float] = {}
    for compact in MAJOR_WEIGHTS:
        frames = major_cache.get(compact) or {}
        df5_closed = _closed_slice(frames.get("5m"), eval_ms, "5m", tail=100)
        if len(df5_closed) < 60:
            continue
        last5 = df5_closed.iloc[-1]
        current = _sf(last5["close"])
        open5 = _sf(last5["open"])
        if min(current, open5) <= 0:
            continue
        f15 = _structure_input(frames.get("15m"), eval_ms, "15m", current)
        f1h = _structure_input(frames.get("1h"), eval_ms, "1h", current)
        f4h = _structure_input(frames.get("4h"), eval_ms, "4h", current)
        if f15 is None or f1h is None or f4h is None:
            continue
        payloads[compact] = {
            "current_price": current,
            "5m": df5_closed,
            "15m": f15,
            "1h": f1h,
            "4h": f4h,
        }
        major_moves[compact] = round(_pct(open5, current), 5)

    if len(payloads) < 2:
        return None, major_moves
    # Exact historical breadth for all 274 contracts would require millions of
    # extra candles. Neutral breadth avoids inventing or future-biasing it; the
    # major basket still supplies 80% of Market First's market score.
    return build_market_context(payloads, breadth_5m=0.50, breadth_24h=0.50), major_moves


def _candidate_frames(
    exchange: Any,
    market_symbol: str,
    df5: pd.DataFrame,
    df15: pd.DataFrame,
    df1h: pd.DataFrame,
    eval_ms: int,
) -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame], float]:
    df1m = _fetch_1m_window(exchange, market_symbol, eval_ms)
    if len(df1m) < 30:
        return None, None, None, None, 0.0
    current = _sf(df1m.iloc[-1]["close"])
    if current <= 0:
        return None, None, None, None, 0.0
    f5 = _structure_input(df5, eval_ms, "5m", current)
    f15 = _structure_input(df15, eval_ms, "15m", current)
    f1h = _structure_input(df1h, eval_ms, "1h", current)
    if f5 is None or f15 is None or f1h is None:
        return None, None, None, None, 0.0
    return df1m, f5, f15, f1h, current


def _future_5m(df5: pd.DataFrame, eval_ms: int) -> pd.DataFrame:
    end_ms = eval_ms + OUTCOME_HOURS * 60 * 60_000
    return df5[(df5["time"] >= eval_ms) & (df5["time"] < end_ms)].copy().reset_index(drop=True)


def _historical_sample(
    symbol: str,
    eval_ms: int,
    decision: Mapping[str, Any],
    features: Mapping[str, Any],
    outcome: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "trade_id": f"HIST:{symbol}:{int(eval_ms // 1000)}",
        "symbol": symbol,
        "direction": str(decision.get("direction") or ""),
        "source": "MARKET_FIRST_HISTORICAL_REPLAY",
        "opened_at": int(eval_ms // 1000),
        "entry": _sf(decision.get("current_price")),
        "features": {name: _sf(features.get(name)) for name in FEATURE_NAMES},
        "model_version_at_open": MODEL_VERSION,
        "model_mode_at_open": "HISTORICAL_SEED",
        "model_probability_at_open": None,
        "label": int(outcome["label"]),
        "resolved": True,
        "ignored_reason": None,
        "resolved_result": str(outcome.get("result") or ""),
        "resolved_net_r": _sf(outcome.get("net_r")),
        "resolved_at": int((eval_ms + OUTCOME_HOURS * 60 * 60_000) // 1000),
        "label_target": "HISTORICAL_NET_R_AFTER_COST_PROXY",
        "sample_origin": VERSION,
        "strategy_version": STRATEGY_VERSION,
        "historical_breadth_mode": "NEUTRAL_0_50_NO_LOOKAHEAD",
        "historical_derivatives_mode": "UNAVAILABLE_FLAGS_ZERO",
        "round_trip_cost_percent": ROUND_TRIP_COST_PERCENT,
    }


def _process_symbol(
    exchange: Any,
    row: Mapping[str, Any],
    major_cache: Mapping[str, Mapping[str, pd.DataFrame]],
    start_ms: int,
    end_eval_ms: int,
    end_data_ms: int,
    store: Dict[str, Any],
) -> Dict[str, Any]:
    symbol = str(row["symbol"])
    market_symbol = str(row["market_symbol"])
    contract_size = _sf(row.get("contract_size"), 1.0)
    stats: Counter = Counter()

    warm5 = 16 * 60 * 60_000
    warm15 = 4 * 24 * 60 * 60_000
    warm1h = 10 * 24 * 60 * 60_000
    df5 = _fetch_range(
        exchange,
        market_symbol,
        "5m",
        start_ms - warm5,
        end_data_ms,
        max_bars=int((end_data_ms - (start_ms - warm5)) / TF_MS["5m"]) + 40,
    )
    df15 = _fetch_range(
        exchange,
        market_symbol,
        "15m",
        start_ms - warm15,
        end_eval_ms,
        max_bars=int((end_eval_ms - (start_ms - warm15)) / TF_MS["15m"]) + 40,
    )
    df1h = _fetch_range(
        exchange,
        market_symbol,
        "1h",
        start_ms - warm1h,
        end_eval_ms,
        max_bars=int((end_eval_ms - (start_ms - warm1h)) / TF_MS["1h"]) + 40,
    )
    if min(len(df5), len(df15), len(df1h)) < 80:
        return {"symbol": symbol, "status": "DATA_MISSING", "stats": {}}

    eval_times = select_eval_times(df5, start_ms, end_eval_ms)
    stats["eval_times"] = len(eval_times)
    samples = store.setdefault("samples", {})

    for eval_ms in eval_times:
        sample_id = f"HIST:{symbol}:{int(eval_ms // 1000)}"
        if sample_id in samples:
            stats["duplicate"] += 1
            continue

        quote_volume = _historical_quote_volume(df5, eval_ms, contract_size)
        if quote_volume < MIN_HISTORICAL_QUOTE_VOLUME:
            stats["LOW_HIST_VOLUME"] += 1
            continue

        context, major_moves = _context_at(major_cache, eval_ms)
        if context is None:
            stats["MARKET_DATA"] += 1
            continue

        df1m, f5, f15, f1h, current = _candidate_frames(
            exchange,
            market_symbol,
            df5,
            df15,
            df1h,
            eval_ms,
        )
        if df1m is None or f5 is None or f15 is None or f1h is None:
            stats["CANDIDATE_DATA"] += 1
            continue

        decision, reason = analyze_candidate(
            symbol=symbol,
            df1m=df1m,
            df5m=f5,
            df15m=f15,
            df1h=f1h,
            current_price=current,
            quote_volume_24h=quote_volume,
            context=context,
        )
        decision, reason = audit.revise_late_decision(decision, reason)
        if decision is None:
            stats[str(reason or "REJECTED")] += 1
            continue
        stats[f"stage_{decision.get('stage')}"] += 1
        if not decision.get("trade_eligible"):
            stats[str(decision.get("risk_reject_reason") or "NOT_TRADE_ELIGIBLE")] += 1
            continue

        guard = evaluate_pre_send_market(str(decision.get("direction") or ""), major_moves)
        if guard.get("blocked"):
            stats[f"GUARD_{guard.get('reason')}"] += 1
            continue

        features = extract_features(decision, context)
        future = _future_5m(df5, eval_ms)
        outcome = resolve_historical_outcome(
            str(decision.get("direction") or ""),
            _sf(decision.get("current_price")),
            _sf(decision.get("sl")),
            _sf(decision.get("tp1")),
            _sf(decision.get("risk_percent")),
            future,
        )
        if outcome.get("label") not in (0, 1):
            stats[str(outcome.get("result") or "OUTCOME_UNRESOLVED")] += 1
            continue

        samples[sample_id] = _historical_sample(
            symbol,
            eval_ms,
            decision,
            features,
            outcome,
        )
        stats["seeded"] += 1
        stats["positive" if int(outcome["label"]) == 1 else "negative"] += 1

    return {"symbol": symbol, "status": "OK", "stats": dict(stats)}


def run() -> Dict[str, Any]:
    now_ms = int(time.time() * 1000)
    outcome_ms = OUTCOME_HOURS * 60 * 60_000
    end_eval_ms = now_ms - outcome_ms - 10 * 60_000
    start_ms = end_eval_ms - LOOKBACK_DAYS * 24 * 60 * 60_000
    end_data_ms = now_ms - 5 * 60_000

    exchange = ccxt.okx(
        {
            "enableRateLimit": True,
            "timeout": 20_000,
            "options": {"defaultType": "swap"},
        }
    )

    research_rows = _market_symbol_map(exchange)
    state = _load_json(
        STATE_FILE,
        {"version": VERSION, "cursor": 0, "runs": 0, "processed_symbols": []},
    )
    if not research_rows:
        raise RuntimeError("No eligible OKX crypto perpetual markets found")

    cursor = int(_sf(state.get("cursor"), 0.0)) % len(research_rows)
    batch_size = max(1, min(BATCH_SYMBOLS, len(research_rows)))
    batch = [research_rows[(cursor + offset) % len(research_rows)] for offset in range(batch_size)]
    state["cursor"] = (cursor + batch_size) % len(research_rows)
    state["runs"] = int(_sf(state.get("runs"), 0.0)) + 1

    major_symbols = _major_market_symbols(exchange)
    if len(major_symbols) < 2:
        raise RuntimeError(f"Major market mapping incomplete: {major_symbols}")
    major_cache = _prefetch_major_cache(exchange, major_symbols, start_ms, end_eval_ms)

    store = load_store()
    before_labeled = sum(
        1
        for sample in (store.get("samples") or {}).values()
        if isinstance(sample, Mapping) and sample.get("label") in (0, 1)
    )

    symbol_reports: List[Dict[str, Any]] = []
    run_totals: Counter = Counter()
    for index, row in enumerate(batch, start=1):
        symbol = str(row["symbol"])
        print(f"HIST ML {index}/{len(batch)}: {symbol}")
        try:
            result = _process_symbol(
                exchange,
                row,
                major_cache,
                start_ms,
                end_eval_ms,
                end_data_ms,
                store,
            )
        except Exception as exc:
            result = {
                "symbol": symbol,
                "status": f"ERROR_{type(exc).__name__}",
                "error": str(exc)[:300],
                "stats": {},
            }
        symbol_reports.append(result)
        for key, value in (result.get("stats") or {}).items():
            run_totals[key] += int(_sf(value, 0.0))

    save_store(store)
    after_labeled = sum(
        1
        for sample in (store.get("samples") or {}).values()
        if isinstance(sample, Mapping) and sample.get("label") in (0, 1)
    )
    historical_labeled = sum(
        1
        for sample in (store.get("samples") or {}).values()
        if isinstance(sample, Mapping)
        and sample.get("label") in (0, 1)
        and str(sample.get("sample_origin") or "").startswith("MARKET_FIRST_HISTORICAL_ML_SEED")
    )

    bundle = train_quality_model(store)
    ml_summary = bundle_summary(bundle)

    processed = list(state.get("processed_symbols") or [])
    processed.extend(str(row["symbol"]) for row in batch)
    # Keep a compact recent history only; cursor is the authoritative rotation.
    state["processed_symbols"] = processed[-120:]
    state["version"] = VERSION
    state["last_run_at"] = int(time.time())
    state["last_batch"] = [str(row["symbol"]) for row in batch]
    state["last_seeded"] = int(run_totals.get("seeded", 0))
    _atomic_json(STATE_FILE, state)

    report = {
        "version": VERSION,
        "strategy_version": STRATEGY_VERSION,
        "generated_at": int(time.time()),
        "lookback_days": LOOKBACK_DAYS,
        "batch_symbols": len(batch),
        "max_evals_per_symbol": MAX_EVALS_PER_SYMBOL,
        "outcome_hours": OUTCOME_HOURS,
        "round_trip_cost_percent": ROUND_TRIP_COST_PERCENT,
        "historical_breadth_mode": "NEUTRAL_0_50_NO_LOOKAHEAD",
        "historical_derivatives_mode": "UNAVAILABLE_FLAGS_ZERO",
        "liquidity_note": "Historical order-book depth unavailable; quote-volume gate used, live depth guard remains unchanged.",
        "cursor_start": cursor,
        "cursor_end": state["cursor"],
        "eligible_research_universe": len(research_rows),
        "symbols": symbol_reports,
        "run_totals": dict(run_totals),
        "labeled_before": before_labeled,
        "labeled_after": after_labeled,
        "labeled_added": max(0, after_labeled - before_labeled),
        "historical_labeled_total": historical_labeled,
        "ml_quality_after_seed": ml_summary,
    }
    _atomic_json(REPORT_FILE, report)
    print(
        "MARKET FIRST HISTORICAL ML SEED |",
        "seeded=", run_totals.get("seeded", 0),
        "| +/−=", f"{run_totals.get('positive', 0)}/{run_totals.get('negative', 0)}",
        "| labeled=", after_labeled,
        "| ML=", ml_summary,
    )
    return report


if __name__ == "__main__":
    run()
