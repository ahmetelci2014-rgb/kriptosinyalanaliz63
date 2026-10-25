"""Independent 24/48/72h price-move observations for Telegram background candidates.

Observation only. Never sends Telegram messages, opens orders, or changes live
trade admission/stop/target decisions. Uses OKX perpetual 15m CLOSED candles.
The price reference is a hypothetical planned entry, not a verified fill.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import time
from typing import Any

VERSION = "MARKET_FIRST_EXTENDED_SHADOW_V1_2026_10_25"
SOURCE_PATH = Path("market_first_candidate_shadow.json")
RESULT_PATH = Path("market_first_extended_shadow.json")
HORIZONS = (24, 48, 72)
TARGETS = (3, 5, 10)
CANDLE_SECONDS = 15 * 60
MAX_MARKETS = 110
RECENT_SECONDS = 96 * 3600
MAX_PAGES_PER_MARKET = 8


def number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def load(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def eligible(source: dict[str, Any], state: dict[str, Any], now: int) -> list[dict[str, Any]]:
    previous = state.get("trades") or {}
    rows = []
    for key, raw in (source.get("trades") or {}).items():
        if not isinstance(raw, dict):
            continue
        if raw.get("source") != "SELECTIVE_PRE_SIGNAL_TELEGRAM":
            continue
        if raw.get("tracking_policy") != "POST_ALERT_CLOSED_1M_V2":
            continue  # Legacy timing is not verified.
        opened = int(number(raw.get("opened_at")))
        if opened <= 0 or opened > now or number(raw.get("entry")) <= 0:
            continue
        if opened < now - RECENT_SECONDS and (
            key not in previous or previous[key].get("finalized")
        ):
            continue
        row = dict(raw)
        row["trade_id"] = str(key)
        rows.append(row)
    return rows


def fetch_candles(exchange: Any, market: str, since: int, until: int) -> list[list[float]]:
    start = (since // CANDLE_SECONDS) * CANDLE_SECONDS * 1000
    limit_end = until * 1000
    by_time: dict[int, list[float]] = {}
    for _ in range(MAX_PAGES_PER_MARKET):
        if start >= limit_end:
            break
        batch = exchange.fetch_ohlcv(market, "15m", since=start, limit=100)
        if not batch:
            break
        for candle in batch:
            if len(candle) < 5:
                continue
            stamp = int(number(candle[0]))
            if start <= stamp < limit_end:
                by_time[stamp] = [number(v) for v in candle[:6]]
        following = max(int(number(c[0])) for c in batch) + CANDLE_SECONDS * 1000
        if following <= start:
            break
        start = following
    return [by_time[key] for key in sorted(by_time)]


def evaluate(raw: dict[str, Any], candles: list[list[float]], now: int) -> dict[str, Any]:
    opened = int(number(raw["opened_at"]))
    entry = number(raw.get("entry"))
    stop = number(raw.get("sl"))
    direction = str(raw.get("direction") or "")
    if entry <= 0 or stop <= 0 or direction not in ("LONG", "SHORT"):
        raise ValueError("Invalid hypothetical entry/stop/direction")
    sign = 1 if direction == "LONG" else -1
    if sign * (entry - stop) <= 0:
        raise ValueError("Stop is on wrong side of entry")
    start = ((opened + CANDLE_SECONDS - 1) // CANDLE_SECONDS) * CANDLE_SECONDS
    allowed_last_start = (now // CANDLE_SECONDS) * CANDLE_SECONDS - CANDLE_SECONDS
    rows = sorted(
        (
            (int(number(c[0]) // 1000), number(c[2]), number(c[3]))
            for c in candles if len(c) >= 5
        ),
        key=lambda x: x[0],
    )
    rows = [(at, high, low) for at, high, low in rows
            if start <= at <= allowed_last_start
            and at + CANDLE_SECONDS <= opened + HORIZONS[-1] * 3600
            and high >= low > 0]
    results: dict[str, Any] = {}
    for hours in HORIZONS:
        deadline = opened + hours * 3600
        window = [(at, high, low) for at, high, low in rows
                  if at + CANDLE_SECONDS <= deadline]
        first_stop = None
        first_hits: dict[int, int] = {}
        best_favorable = 0.0
        best_adverse = 0.0
        for at, high, low in window:
            favorable = (high / entry - 1) * 100 if sign > 0 else (1 - low / entry) * 100
            adverse = (1 - low / entry) * 100 if sign > 0 else (high / entry - 1) * 100
            best_favorable = max(best_favorable, favorable)
            best_adverse = max(best_adverse, adverse)
            if first_stop is None and (low <= stop if sign > 0 else high >= stop):
                first_stop = at
            for target in TARGETS:
                if target not in first_hits and favorable >= target:
                    first_hits[target] = at
        # Do not claim 24/48/72h completeness when candles are missing.
        expected_last_end = (deadline // CANDLE_SECONDS) * CANDLE_SECONDS
        expected_count = max(0, (expected_last_end - start) // CANDLE_SECONDS)
        complete = (now >= deadline and bool(window) and
                    window[-1][0] + CANDLE_SECONDS >= expected_last_end and
                    len(window) >= expected_count * 0.95)
        targets: dict[str, Any] = {}
        for target in TARGETS:
            hit = first_hits.get(target)
            if hit is None:
                first = "STOP_FIRST" if first_stop is not None else "NOT_REACHED"
            elif first_stop is None or hit < first_stop:
                first = "TARGET_FIRST"
            elif hit == first_stop:
                first = "AMBIGUOUS_SAME_15M"
            else:
                first = "STOP_FIRST"
            targets[str(target)] = {
                "directional_reached": hit is not None,
                "first_order": first,
                "first_hit_at": hit,
                "after_stop": hit is not None and first_stop is not None and hit > first_stop,
            }
        results[str(hours)] = {
            "complete": bool(complete),
            "candles": len(window),
            "max_favorable_percent": round(best_favorable, 4),
            "max_adverse_percent": round(best_adverse, 4),
            "stop_first_seen_at": first_stop,
            "targets": targets,
        }
    return {
        "symbol": raw.get("symbol"),
        "direction": direction,
        "source": raw.get("source"),
        "opened_at": opened,
        "reference_entry": entry,
        "original_stop": stop,
        "score": raw.get("score"),
        "original_result": raw.get("final_result"),
        "tracking_policy": raw.get("tracking_policy"),
        "observation_only": True,
        "exchange_orders": False,
        "telegram": False,
        "first_partial_candle_skipped": True,
        "horizons": results,
        "finalized": bool(results[str(HORIZONS[-1])]["complete"]),
        "updated_at": now,
    }


def aggregate(trades: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {"tracked": len(trades), "horizons": {}}
    for hours in HORIZONS:
        rows = [r["horizons"][str(hours)] for r in trades.values()
                if isinstance(r, dict) and
                isinstance(r.get("horizons"), dict) and
                isinstance(r["horizons"].get(str(hours)), dict) and
                r["horizons"][str(hours)].get("complete")]
        entry: dict[str, Any] = {"complete": len(rows), "targets": {}}
        for target in TARGETS:
            sub = [r["targets"][str(target)] for r in rows]
            entry["targets"][str(target)] = {
                "directional_reached": sum(bool(x["directional_reached"]) for x in sub),
                "target_before_stop": sum(x["first_order"] == "TARGET_FIRST" for x in sub),
                "stop_before_target": sum(x["first_order"] == "STOP_FIRST" for x in sub),
                "ambiguous_same_candle": sum(x["first_order"] == "AMBIGUOUS_SAME_15M" for x in sub),
                "reached_only_after_stop": sum(bool(x["after_stop"]) for x in sub),
            }
        output["horizons"][str(hours)] = entry
    output["note"] = (
        "Directional move is measured from the candidate's planned entry, not an actual fill. "
        "Target-before-stop is a conservative candle-based sequence, not realized PnL. "
        "Initial partial candle is omitted; simultaneous target/stop is ambiguous."
    )
    return output


def run() -> None:
    source = load(SOURCE_PATH, {})
    if not isinstance(source, dict) or not isinstance(source.get("trades"), dict):
        raise RuntimeError("Candidate source ledger is missing or malformed")
    now = int(time.time())
    old = load(RESULT_PATH, {})
    if not isinstance(old, dict):
        old = {}
    previous = old.get("trades") or {}
    if not isinstance(previous, dict):
        previous = {}
    rows = eligible(source, old, now)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "")
        if symbol.endswith("USDT"):
            grouped[symbol].append(row)
    markets = sorted(grouped, key=lambda s: min(int(r["opened_at"]) for r in grouped[s]))[:MAX_MARKETS]
    skipped_markets = len(grouped) - len(markets)
    if not markets:
        print("Extended shadow: no fresh candidate rows; prior observations preserved.")
        return
    import ccxt  # Only the standalone scheduled workflow uses exchange public data.

    exchange = ccxt.okx({"enableRateLimit": True, "timeout": 20000})
    available = exchange.load_markets()
    outcomes = dict(previous)
    errors = []
    succeeded = 0
    for symbol in markets:
        market = symbol[:-4] + "/USDT:USDT"
        if market not in available or not available[market].get("swap"):
            errors.append(symbol + ":NO_OKX_SWAP")
            continue
        items = grouped[symbol]
        since = min(int(x["opened_at"]) for x in items)
        until = min(now, max(int(x["opened_at"]) + HORIZONS[-1] * 3600 for x in items))
        try:
            candles = fetch_candles(exchange, market, since, until)
            if not candles:
                raise RuntimeError("NO_CLOSED_OHLCV")
            for item in items:
                trade_id = str(item["trade_id"])
                outcomes[trade_id] = evaluate(item, candles, now)
            succeeded += 1
        except Exception as exc:
            errors.append(symbol + ":" + type(exc).__name__)
    payload = {
        "version": VERSION,
        "generated_at": now,
        "generated_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "mode": "INDEPENDENT_BACKGROUND_MOVE_SHADOW_NO_LIVE_EFFECT",
        "reference": "PLANNED_ENTRY_NOT_VERIFIED_FILL",
        "source_legacy_timing_excluded": True,
        "candle_timeframe": "15m_closed",
        "horizons_hours": list(HORIZONS),
        "move_thresholds_percent": list(TARGETS),
        "source_count": len(source["trades"]),
        "processed_markets": succeeded,
        "skipped_markets_due_to_cap": skipped_markets,
        "errors": errors[:40],
        "trades": outcomes,
        "summary": aggregate(outcomes),
        "live_strategy_changed": False,
        "telegram": False,
        "exchange_orders": False,
    }
    if not succeeded:
        raise RuntimeError("No market OHLCV succeeded; preserving previous output")
    temporary = RESULT_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, RESULT_PATH)
    print("EXTENDED SHADOW:", json.dumps(payload["summary"], ensure_ascii=False))
    print("MARKETS:", succeeded, "ERRORS:", errors[:10], "SKIPPED:", skipped_markets)


def self_test() -> None:
    at = 1_800_000_000
    at = (at // CANDLE_SECONDS) * CANDLE_SECONDS
    base = {"opened_at": at + 120, "entry": 100, "sl": 99,
            "symbol": "TESTUSDT", "direction": "LONG", "source": "SELECTIVE_PRE_SIGNAL_TELEGRAM"}
    candles = [
        [at * 1000, 100, 110, 98, 102, 1],  # Opening bar MUST be skipped.
        [(at + 900) * 1000, 100, 102, 99.5, 101, 1],
        [(at + 1800) * 1000, 101, 104, 100, 103, 1],
        [(at + 2700) * 1000, 103, 106, 102, 105, 1],
        [(at + 3600) * 1000, 105, 112, 98, 100, 1],
    ]
    r = evaluate(base, candles, at + 25 * 3600)
    t = r["horizons"]["24"]["targets"]
    assert t["3"]["first_order"] == "TARGET_FIRST", t
    assert t["5"]["first_order"] == "TARGET_FIRST", t
    assert t["10"]["first_order"] == "AMBIGUOUS_SAME_15M", t
    stopped = dict(base, sl=99.8)
    r2 = evaluate(stopped, candles, at + 25 * 3600)
    assert r2["horizons"]["24"]["targets"]["5"]["first_order"] == "STOP_FIRST", r2
    short = dict(base, direction="SHORT", sl=101)
    candles2 = [[(at + 900) * 1000, 100, 101.5, 94, 96, 1]]
    r3 = evaluate(short, candles2, at + 25 * 3600)
    assert r3["horizons"]["24"]["targets"]["5"]["first_order"] == "AMBIGUOUS_SAME_15M"
    summary = aggregate({"one": r})
    assert summary["horizons"]["24"]["targets"]["5"]["target_before_stop"] == 0  # incomplete 24h window
    print("SELF-TEST PASSED: initial bar, direction, target/stop ordering, partial coverage")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        self_test()
    else:
        run()
