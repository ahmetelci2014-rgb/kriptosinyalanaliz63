"""Fast combined validator: current Market Structure + precomputed Smart state."""
from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

import market_structure_ai_shadow as market_structure
import smart_structure_adapter as smart
from smart_structure_combined_validation import (
    FILTERS,
    HORIZON_BARS,
    COOLDOWN_BARS,
    _allowed,
    _exchange,
    _fetch,
    _market_map,
    _outcome,
    _sf,
    _stats,
)

OUTPUT_FILE = os.getenv("SMART_STRUCTURE_COMBINED_RESULT_FILE", "smart_structure_combined_validation.json")
LOOKBACK_DAYS = int(os.getenv("SMART_STRUCTURE_COMBINED_DAYS", "5"))
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT", "SUIUSDT")


def _smart_row(states: Any, i: int) -> Dict[str, Any]:
    if states is None or len(states) <= i:
        return {}
    row = states.iloc[i]
    return {
        "trend": int(_sf(row.get("smart_trend"), 0)),
        "watch_dir": int(_sf(row.get("smart_watch_dir"), 0)),
        "confirm_long": bool(row.get("smart_confirm_long")),
        "confirm_short": bool(row.get("smart_confirm_short")),
    }


def run() -> Dict[str, Any]:
    exchange = _exchange()
    market_map = _market_map(exchange)
    now_ms = int(time.time() * 1000)
    since_ms = now_ms - LOOKBACK_DAYS * 86_400_000
    totals: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_symbol: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []

    for symbol in SYMBOLS:
        unified = market_map.get(symbol)
        if not unified:
            errors.append({"symbol": symbol, "error": "MARKET_NOT_FOUND"})
            continue
        try:
            df = _fetch(exchange, unified, since_ms, now_ms)
            smart_states = smart.compute_state_frame(df, entry_period=100, trend_period=200)
            symbol_rows: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
            last_accept = {name: {"LONG": -99999, "SHORT": -99999} for name in FILTERS}
            warmup = 220
            for i in range(warmup, len(df) - HORIZON_BARS - 2):
                prefix = df.iloc[max(0, i - 230) : i + 2].copy().reset_index(drop=True)
                signal = market_structure.analyze(symbol, prefix, None, _sf(df.iloc[i]["close"]))
                if not signal:
                    continue
                direction = str(signal.get("direction") or "")
                smart_features = _smart_row(smart_states, i)
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
        stats = global_stats[name]
        ranked.append({
            "filter": name,
            **stats,
            "avg_net_r_delta_vs_base": round(_sf(stats.get("avg_net_r")) - _sf(base.get("avg_net_r")), 4),
            "pf_delta_vs_base": round(_sf(stats.get("profit_factor")) - _sf(base.get("profit_factor")), 3),
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
        "symbols": list(SYMBOLS),
        "global": global_stats,
        "ranked_filters": ranked,
        "best_filter": best,
        "validated_for_market_structure_alerts": validated,
        "by_symbol": by_symbol,
        "errors": errors,
        "note": "Smart 100/200 state is precomputed once; no DCA is used.",
    }
    Path(OUTPUT_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("GLOBAL", global_stats)
    print("BEST_FILTER", best)
    print("VALIDATED", validated)
    return payload


if __name__ == "__main__":
    run()
