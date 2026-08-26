"""Compare current Market Structure events with Smart-structure trend filters.

The purpose is not to create another strategy. It asks one narrow question:
does the SmartDCA-inspired trend state improve the existing Market Structure
WATCH/READY candidate quality when used only as an extra filter?
"""
from __future__ import annotations

import json
import math
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import ccxt
import pandas as pd

import crypto_universe_guard as universe_guard
import market_structure_ai_shadow as market_structure
import smart_structure_adapter as smart

OUTPUT_FILE = os.getenv("SMART_STRUCTURE_COMBINED_RESULT_FILE", "smart_structure_combined_validation.json")
LOOKBACK_DAYS = int(os.getenv("SMART_STRUCTURE_COMBINED_DAYS", "7"))
HORIZON_BARS = 36
COOLDOWN_BARS = 6
ROUND_TRIP_NOTIONAL_COST = 0.0012
DEFAULT_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "DOGEUSDT", "SUIUSDT", "LINKUSDT")
FILTERS = ("BASE", "SMART_NOT_OPPOSING", "SMART_ALIGNED_OR_WATCH", "SMART_ALIGNED")


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _exchange() -> ccxt.okx:
    return ccxt.okx({"enableRateLimit": True, "timeout": 30000, "options": {"defaultType": "swap"}})


def _market_map(exchange: Any) -> Dict[str, str]:
    markets = exchange.load_markets()
    filtered, _ = universe_guard.filter_crypto_markets(markets)
    out: Dict[str, str] = {}
    for market in filtered.values():
        if not isinstance(market, dict) or not market.get("swap"):
            continue
        base = str(market.get("base") or "").upper()
        unified = str(market.get("symbol") or "")
        if base and unified and str(market.get("quote") or "").upper() == "USDT":
            out[f"{base}USDT"] = unified
    return out


def _fetch(exchange: Any, symbol: str, since_ms: int, until_ms: int) -> pd.DataFrame:
    rows: List[List[float]] = []
    cursor = since_ms
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe="5m", since=cursor, limit=300)
        if not batch:
            break
        for bar in batch:
            ts = int(bar[0])
            if ts <= until_ms and (not rows or ts > int(rows[-1][0])):
                rows.append(bar)
        nxt = int(batch[-1][0]) + 300_000
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 300:
            break
        time.sleep(0.02)
    return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])


def _allowed(filter_name: str, direction: str, smart_features: Dict[str, Any]) -> bool:
    trend = int(smart_features.get("trend") or 0)
    watch_dir = int(smart_features.get("watch_dir") or 0)
    same = 1 if direction == "LONG" else -1
    confirm = bool(smart_features.get("confirm_long" if direction == "LONG" else "confirm_short"))
    if filter_name == "BASE":
        return True
    if filter_name == "SMART_NOT_OPPOSING":
        return trend != -same
    if filter_name == "SMART_ALIGNED_OR_WATCH":
        return trend == same or watch_dir == same or confirm
    if filter_name == "SMART_ALIGNED":
        return trend == same or confirm
    return False


def _outcome(df: pd.DataFrame, index: int, signal: Dict[str, Any]) -> Dict[str, Any] | None:
    direction = str(signal.get("direction") or "")
    entry = _sf(signal.get("entry"))
    stop = _sf(signal.get("stop"))
    risk = _sf(signal.get("risk_abs"))
    target = _sf(signal.get("target_2r"))
    if entry <= 0 or risk <= 0 or stop <= 0 or target <= 0:
        return None
    risk_pct = risk / entry
    if risk_pct <= 0 or risk_pct > 0.08:
        return None
    future = df.iloc[index + 1 : min(len(df), index + 1 + HORIZON_BARS)]
    status = "TIMEOUT"
    gross_r = 0.0
    for _, bar in future.iterrows():
        high = _sf(bar.get("high"))
        low = _sf(bar.get("low"))
        if direction == "LONG":
            hit_stop, hit_target = low <= stop, high >= target
        else:
            hit_stop, hit_target = high >= stop, low <= target
        if hit_stop and hit_target:
            status, gross_r = "AMBIGUOUS", 0.0
            break
        if hit_stop:
            status, gross_r = "STOP_FIRST", -1.0
            break
        if hit_target:
            status, gross_r = "R2_FIRST", 2.0
            break
    else:
        if len(future):
            last = _sf(future.iloc[-1].get("close"), entry)
            raw = (last - entry) / risk if direction == "LONG" else (entry - last) / risk
            gross_r = max(-1.0, min(2.0, raw))
    cost_r = ROUND_TRIP_NOTIONAL_COST / risk_pct
    return {"status": status, "net_r": gross_r - cost_r, "risk_pct": risk_pct * 100.0}


def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not rows:
        return {"sample": 0}
    pos = [r["net_r"] for r in rows if r["net_r"] > 0]
    neg = [-r["net_r"] for r in rows if r["net_r"] < 0]
    n = len(rows)
    net = sum(r["net_r"] for r in rows)
    return {
        "sample": n,
        "r2_rate_pct": round(sum(r["status"] == "R2_FIRST" for r in rows) / n * 100, 2),
        "stop_rate_pct": round(sum(r["status"] == "STOP_FIRST" for r in rows) / n * 100, 2),
        "net_r": round(net, 4),
        "avg_net_r": round(net / n, 4),
        "profit_factor": round(sum(pos) / sum(neg), 3) if neg else 999.0,
    }


def run() -> Dict[str, Any]:
    exchange = _exchange()
    market_map = _market_map(exchange)
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - LOOKBACK_DAYS * 86_400_000
    totals: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_symbol: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []

    for symbol in DEFAULT_SYMBOLS:
        unified = market_map.get(symbol)
        if not unified:
            errors.append({"symbol": symbol, "error": "MARKET_NOT_FOUND"})
            continue
        try:
            df = _fetch(exchange, unified, since_ms, now_ms)
            symbol_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            last_accept = {name: {"LONG": -99999, "SHORT": -99999} for name in FILTERS}
            warmup = 220
            for i in range(warmup, len(df) - HORIZON_BARS - 2):
                # analyze() treats the final row as open, so include i+1 as the
                # open placeholder and bar i remains the latest closed candle.
                prefix = df.iloc[max(0, i - 230) : i + 2].copy().reset_index(drop=True)
                signal = market_structure.analyze(symbol, prefix, None, _sf(df.iloc[i]["close"]))
                if not signal:
                    continue
                direction = str(signal.get("direction") or "")
                smart_features = smart.latest_features(
                    prefix,
                    entry_period=100,
                    trend_period=200,
                    exclude_open_candle=True,
                )
                if not smart_features:
                    continue
                outcome = _outcome(df, i, signal)
                if not outcome:
                    continue
                for filter_name in FILTERS:
                    if not _allowed(filter_name, direction, smart_features):
                        continue
                    if i - last_accept[filter_name][direction] < COOLDOWN_BARS:
                        continue
                    last_accept[filter_name][direction] = i
                    row = {**outcome, "direction": direction, "stage": signal.get("stage")}
                    symbol_rows[filter_name].append(row)
                    totals[filter_name].append(row)
            by_symbol[symbol] = {name: _stats(symbol_rows[name]) for name in FILTERS}
            print(symbol, "candles=", len(df), "base=", len(symbol_rows["BASE"]))
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            print(symbol, "ERROR", type(exc).__name__, str(exc)[:160])

    global_stats = {name: _stats(totals[name]) for name in FILTERS}
    base = global_stats["BASE"]
    ranked = []
    for name in FILTERS[1:]:
        s = global_stats[name]
        ranked.append({
            "filter": name,
            **s,
            "avg_net_r_delta_vs_base": round(_sf(s.get("avg_net_r")) - _sf(base.get("avg_net_r")), 4),
            "pf_delta_vs_base": round(_sf(s.get("profit_factor")) - _sf(base.get("profit_factor")), 3),
        })
    ranked.sort(key=lambda r: (r.get("avg_net_r_delta_vs_base", -999), r.get("profit_factor", 0)), reverse=True)
    best = ranked[0] if ranked else None
    validated = bool(
        best
        and best.get("sample", 0) >= 50
        and best.get("avg_net_r_delta_vs_base", 0) > 0.03
        and best.get("profit_factor", 0) >= 1.10
        and best.get("avg_net_r", 0) > 0
    )
    payload = {
        "generated_at": int(time.time()),
        "timeframe": "5m",
        "lookback_days": LOOKBACK_DAYS,
        "symbols": list(DEFAULT_SYMBOLS),
        "global": global_stats,
        "ranked_filters": ranked,
        "best_filter": best,
        "validated_for_market_structure_alerts": validated,
        "by_symbol": by_symbol,
        "errors": errors,
    }
    Path(OUTPUT_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("GLOBAL", global_stats)
    print("BEST_FILTER", best)
    print("VALIDATED", validated)
    return payload


if __name__ == "__main__":
    run()
