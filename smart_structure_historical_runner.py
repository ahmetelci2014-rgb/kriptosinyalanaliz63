"""Historical validation for the SmartDCA-inspired structure adapter.

This intentionally does NOT reproduce DCA. It tests entry quality under the
bot's conservative structure-risk model: structural stop, 2R target, 3-hour
horizon and estimated round-trip trading cost.
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import ccxt
import pandas as pd

import crypto_universe_guard as universe_guard
import smart_structure_adapter as smart

OUTPUT_FILE = os.getenv("SMART_STRUCTURE_RESULT_FILE", "smart_structure_validation.json")
TIMEFRAME = "5m"
LOOKBACK_DAYS = int(os.getenv("SMART_STRUCTURE_HISTORY_DAYS", "14"))
ROUND_TRIP_NOTIONAL_COST = float(os.getenv("SMART_STRUCTURE_ROUND_TRIP_COST", "0.0012"))
HORIZON_BARS = int(os.getenv("SMART_STRUCTURE_HORIZON_BARS", "36"))
COOLDOWN_BARS = int(os.getenv("SMART_STRUCTURE_COOLDOWN_BARS", "6"))
STOP_ATR_BUFFER = 0.20

DEFAULT_SYMBOLS = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT",
    "ADAUSDT", "LINKUSDT", "AVAXUSDT", "SUIUSDT", "LTCUSDT", "NEARUSDT",
)
CONFIGS = (
    {"name": "FAST_36_72", "entry": 36, "trend": 72},
    {"name": "BALANCED_60_120", "entry": 60, "trend": 120},
    {"name": "ORIGINAL_100_200", "entry": 100, "trend": 200},
)
MODELS = (
    "STRUCTURE_BREAK",
    "BREAK_WITH_TREND",
    "TREND_CONFIRM",
    "CONFIRM_WITH_DIVERGENCE",
)


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _symbols() -> List[str]:
    raw = str(os.getenv("SMART_STRUCTURE_SYMBOLS") or "").strip()
    if not raw:
        return list(DEFAULT_SYMBOLS)
    out: List[str] = []
    seen = set()
    for part in raw.replace(";", ",").split(","):
        symbol = part.strip().upper().replace("/USDT:USDT", "USDT").replace("/", "")
        if symbol and not symbol.endswith("USDT"):
            symbol += "USDT"
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def _exchange() -> ccxt.okx:
    return ccxt.okx({"enableRateLimit": True, "timeout": 30000, "options": {"defaultType": "swap"}})


def _market_map(exchange: Any) -> Dict[str, str]:
    markets = exchange.load_markets()
    filtered, _ = universe_guard.filter_crypto_markets(markets)
    out: Dict[str, str] = {}
    for market in filtered.values():
        if not isinstance(market, dict) or not market.get("swap"):
            continue
        if str(market.get("quote") or "").upper() != "USDT":
            continue
        if str(market.get("settle") or "").upper() != "USDT":
            continue
        base = str(market.get("base") or "").upper()
        unified = str(market.get("symbol") or "")
        if base and unified:
            out[f"{base}USDT"] = unified
    return out


def _fetch_range(exchange: Any, market_symbol: str, since_ms: int, until_ms: int) -> pd.DataFrame:
    rows: List[List[float]] = []
    cursor = since_ms
    limit = 300
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(market_symbol, timeframe=TIMEFRAME, since=cursor, limit=limit)
        if not batch:
            break
        for bar in batch:
            ts = int(bar[0])
            if ts > until_ms:
                break
            if not rows or ts > int(rows[-1][0]):
                rows.append(bar)
        next_cursor = int(batch[-1][0]) + 5 * 60 * 1000
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < limit:
            break
        time.sleep(0.03)
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _event_direction(row: pd.Series, model: str) -> str:
    trend = int(_sf(row.get("smart_trend"), 0))
    if model == "STRUCTURE_BREAK":
        if bool(row.get("smart_range_break_long")):
            return "LONG"
        if bool(row.get("smart_range_break_short")):
            return "SHORT"
    elif model == "BREAK_WITH_TREND":
        if bool(row.get("smart_range_break_long")) and trend >= 0:
            return "LONG"
        if bool(row.get("smart_range_break_short")) and trend <= 0:
            return "SHORT"
    elif model == "TREND_CONFIRM":
        if bool(row.get("smart_confirm_long")):
            return "LONG"
        if bool(row.get("smart_confirm_short")):
            return "SHORT"
    elif model == "CONFIRM_WITH_DIVERGENCE":
        if bool(row.get("smart_confirm_long")) and bool(row.get("smart_recent_rsi_div_long")):
            return "LONG"
        if bool(row.get("smart_confirm_short")) and bool(row.get("smart_recent_rsi_div_short")):
            return "SHORT"
    return ""


def _evaluate(df: pd.DataFrame, index: int, direction: str) -> Dict[str, Any] | None:
    row = df.iloc[index]
    entry = _sf(row.get("close"))
    atr = _sf(row.get("atr"))
    if entry <= 0 or atr <= 0:
        return None
    start = max(0, index - 13)
    recent = df.iloc[start : index + 1]
    if direction == "LONG":
        origin = _sf(recent["low"].min())
        stop = origin - STOP_ATR_BUFFER * atr
        risk = entry - stop
        target = entry + 2.0 * risk
    else:
        origin = _sf(recent["high"].max())
        stop = origin + STOP_ATR_BUFFER * atr
        risk = stop - entry
        target = entry - 2.0 * risk
    if risk <= 0:
        return None
    risk_pct = risk / entry
    if risk_pct <= 0 or risk_pct > 0.08:
        return None

    end = min(len(df), index + 1 + HORIZON_BARS)
    future = df.iloc[index + 1 : end]
    status = "TIMEOUT"
    close_r = 0.0
    for _, bar in future.iterrows():
        high = _sf(bar.get("high"))
        low = _sf(bar.get("low"))
        if direction == "LONG":
            hit_stop = low <= stop
            hit_target = high >= target
        else:
            hit_stop = high >= stop
            hit_target = low <= target
        if hit_stop and hit_target:
            status = "AMBIGUOUS"
            close_r = 0.0
            break
        if hit_stop:
            status = "STOP_FIRST"
            close_r = -1.0
            break
        if hit_target:
            status = "R2_FIRST"
            close_r = 2.0
            break
    else:
        if len(future):
            last = _sf(future.iloc[-1].get("close"), entry)
            raw_r = (last - entry) / risk if direction == "LONG" else (entry - last) / risk
            close_r = max(-1.0, min(2.0, raw_r))

    cost_r = ROUND_TRIP_NOTIONAL_COST / risk_pct
    net_r = close_r - cost_r
    return {
        "direction": direction,
        "entry": entry,
        "origin": origin,
        "risk_pct": risk_pct * 100.0,
        "status": status,
        "gross_r": close_r,
        "cost_r": cost_r,
        "net_r": net_r,
    }


def _stats(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    data = list(rows)
    if not data:
        return {"sample": 0}
    positives = [r["net_r"] for r in data if r["net_r"] > 0]
    negatives = [-r["net_r"] for r in data if r["net_r"] < 0]
    sample = len(data)
    wins = sum(1 for r in data if r["status"] == "R2_FIRST")
    stops = sum(1 for r in data if r["status"] == "STOP_FIRST")
    ambiguous = sum(1 for r in data if r["status"] == "AMBIGUOUS")
    net_sum = sum(r["net_r"] for r in data)
    return {
        "sample": sample,
        "r2_first": wins,
        "stop_first": stops,
        "ambiguous": ambiguous,
        "r2_rate_pct": round(wins / sample * 100.0, 2),
        "stop_rate_pct": round(stops / sample * 100.0, 2),
        "net_r": round(net_sum, 4),
        "avg_net_r": round(net_sum / sample, 4),
        "profit_factor": round(sum(positives) / sum(negatives), 3) if negatives else 999.0,
        "avg_risk_pct": round(sum(r["risk_pct"] for r in data) / sample, 3),
    }


def run() -> Dict[str, Any]:
    exchange = _exchange()
    symbols = _symbols()
    market_map = _market_map(exchange)
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - LOOKBACK_DAYS * 24 * 60 * 60 * 1000

    all_rows: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    by_symbol: Dict[str, Dict[str, Any]] = {}
    errors: List[Dict[str, str]] = []

    for symbol in symbols:
        market = market_map.get(symbol)
        if not market:
            errors.append({"symbol": symbol, "error": "MARKET_NOT_FOUND"})
            continue
        try:
            raw = _fetch_range(exchange, market, since_ms, now_ms)
            symbol_summary: Dict[str, Any] = {"candles": len(raw), "configs": {}}
            for config in CONFIGS:
                states = smart.compute_state_frame(raw, entry_period=config["entry"], trend_period=config["trend"])
                config_summary: Dict[str, Any] = {}
                for model in MODELS:
                    model_rows: List[Dict[str, Any]] = []
                    last_index = {"LONG": -10_000, "SHORT": -10_000}
                    warmup = max(config["trend"], config["entry"]) + 2
                    for i in range(warmup, len(states) - HORIZON_BARS - 1):
                        direction = _event_direction(states.iloc[i], model)
                        if not direction:
                            continue
                        if i - last_index[direction] < COOLDOWN_BARS:
                            continue
                        outcome = _evaluate(states, i, direction)
                        if not outcome:
                            continue
                        last_index[direction] = i
                        outcome.update({"symbol": symbol, "index": i, "config": config["name"], "model": model})
                        model_rows.append(outcome)
                        all_rows[(config["name"], model)].append(outcome)
                    config_summary[model] = _stats(model_rows)
                symbol_summary["configs"][config["name"]] = config_summary
            by_symbol[symbol] = symbol_summary
            print(symbol, "candles=", len(raw))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            print(symbol, "ERROR", type(exc).__name__, str(exc)[:180])

    global_results: Dict[str, Dict[str, Any]] = {}
    candidates: List[Dict[str, Any]] = []
    for config in CONFIGS:
        cfg_name = config["name"]
        global_results[cfg_name] = {}
        for model in MODELS:
            stats = _stats(all_rows[(cfg_name, model)])
            global_results[cfg_name][model] = stats
            if stats.get("sample", 0) >= 30:
                candidates.append({"config": cfg_name, "model": model, **stats})

    candidates.sort(
        key=lambda r: (
            r.get("avg_net_r", -999),
            r.get("profit_factor", 0),
            -r.get("stop_rate_pct", 100),
            r.get("sample", 0),
        ),
        reverse=True,
    )
    best = candidates[0] if candidates else None
    validated = bool(
        best
        and best.get("avg_net_r", 0) > 0.03
        and best.get("profit_factor", 0) >= 1.15
        and best.get("stop_rate_pct", 100) <= 45.0
    )

    payload = {
        "version": smart.VERSION,
        "generated_at": int(time.time()),
        "timeframe": TIMEFRAME,
        "lookback_days": LOOKBACK_DAYS,
        "symbols_requested": symbols,
        "symbols_completed": len(by_symbol),
        "cost_model": {"round_trip_notional": ROUND_TRIP_NOTIONAL_COST},
        "risk_model": {"structural_origin_bars": 14, "stop_atr_buffer": STOP_ATR_BUFFER, "target_r": 2.0, "horizon_bars": HORIZON_BARS},
        "global": global_results,
        "best_candidate": best,
        "validated_for_integration": validated,
        "by_symbol": by_symbol,
        "errors": errors,
        "note": "This validates entry quality without SmartDCA averaging/pyramiding; a high TradingView DCA win rate is not assumed to transfer to this risk model.",
    }
    Path(OUTPUT_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("BEST", best)
    print("VALIDATED", validated)
    print("OUTPUT", OUTPUT_FILE)
    return payload


if __name__ == "__main__":
    run()
