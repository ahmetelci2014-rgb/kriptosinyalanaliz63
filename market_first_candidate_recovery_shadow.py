"""Read-only post-stop reversal study for Telegram background candidates.

Only verified POST_ALERT_CLOSED_1M_V2 candidates whose FIRST outcome was
SL_FIRST are eligible. After a stop, watch whether price returns to original
entry/TP1/TP2/TP3 in the same originally suggested direction.

Never sends Telegram, never opens orders, never edits a signal/strategy file.
Writes ONLY market_first_candidate_recovery_shadow.json.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

SOURCE = Path("market_first_candidate_shadow.json")
OUTPUT = Path("market_first_candidate_recovery_shadow.json")
VERSION = "POST_STOP_RECOVERY_SHADOW_V1_2026_10_13"
ELIGIBLE_POLICY = "POST_ALERT_CLOSED_1M_V2"
TRACK_SECONDS = 6 * 3600
SEED_MAX_AGE_SECONDS = 2 * 3600
MAX_RECORDS = 500
MAX_ACTIVE = 48
MAX_FETCH_PER_RUN = 24
FETCH_LIMIT = 300
CANDLE_SECONDS = 60


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def _int(value: Any) -> int:
    return int(_num(value))


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Invalid JSON root in {path}")
    return data


def save_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, dir=path.parent,
            prefix=".recovery-shadow-", suffix=".tmp",
        ) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        check = json.loads(temp_path.read_text(encoding="utf-8"))
        if not isinstance(check, dict) or "episodes" not in check:
            raise ValueError("Recovery shadow write verification failed")
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def post_stop_start(closed_at: int) -> int:
    # Exclude the stop candle: its high/low may precede the stop.
    return (closed_at // 60 + 1) * 60


def _eligible(row: Mapping[str, Any], now: int) -> bool:
    closed = _int(row.get("closed_at"))
    entry = _num(row.get("entry"))
    direction = str(row.get("direction") or "").upper()
    stop = _num(row.get("sl"))
    tp1 = _num(row.get("tp1"))
    return (
        row.get("tracking_policy") == ELIGIBLE_POLICY
        and row.get("status") == "CLOSED"
        and row.get("first_result") == "SL_FIRST"
        and row.get("final_result") == "SL_FIRST"
        and closed > 0 and 0 <= now - closed <= SEED_MAX_AGE_SECONDS
        and direction in ("LONG", "SHORT")
        and min(entry, stop, tp1) > 0
        and ((stop < entry < tp1) if direction == "LONG" else (stop > entry > tp1))
    )


def seed(source: dict, ledger: dict, now: int) -> int:
    trades = source.get("trades") or {}
    if not isinstance(trades, dict):
        raise ValueError("Invalid candidate trades")
    episodes = ledger.setdefault("episodes", {})
    count = 0
    active = sum(1 for r in episodes.values() if r.get("status") == "TRACKING")
    for key, row in sorted(trades.items(), key=lambda x: _int(x[1].get("closed_at")) if isinstance(x[1], dict) else 0):
        if active >= MAX_ACTIVE:
            break
        if not isinstance(row, dict) or not _eligible(row, now) or key in episodes:
            continue
        closed = _int(row["closed_at"])
        # A candidate may reuse a symbol; key by its unique recorded trade ID.
        episodes[key] = {
            "trade_id": key, "symbol": row["symbol"],
            "direction": row["direction"], "opened_at": _int(row.get("opened_at")),
            "stop_at": closed, "entry": _num(row["entry"]), "sl": _num(row["sl"]),
            "tp1": _num(row["tp1"]), "tp2": _num(row.get("tp2")),
            "tp3": _num(row.get("tp3")),
            "status": "TRACKING", "first_eligible_bar_at": post_stop_start(closed),
            "last_bar_ts": post_stop_start(closed) - CANDLE_SECONDS,
            "return_to_entry_at": None, "tp1_after_sl_at": None,
            "tp2_after_sl_at": None, "tp3_after_sl_at": None,
            "coverage_complete": True, "missing_candles": 0,
            "last_checked_at": 0, "completed_at": 0,
        }
        active += 1
        count += 1
    return count


def to_ccxt_symbol(symbol: str) -> str:
    base = str(symbol).upper().strip()
    if not base.endswith("USDT") or len(base) <= 4:
        raise ValueError("Not a USDT futures symbol")
    return f"{base[:-4]}/USDT:USDT"


def fetch_closed_candles(exchange: Any, symbol: str, since: int, now: int) -> list[dict]:
    raw = exchange.fetch_ohlcv(
        to_ccxt_symbol(symbol), timeframe="1m",
        since=since * 1000, limit=FETCH_LIMIT,
    )
    bars = []
    for row in raw or []:
        ts = _int(_num(row[0]) / 1000)
        if ts < since or ts + CANDLE_SECONDS > now:
            continue
        high, low = _num(row[2]), _num(row[3])
        if min(high, low) <= 0 or high < low:
            continue
        bars.append({"ts": ts, "high": high, "low": low})
    return sorted({b["ts"]: b for b in bars}.values(), key=lambda x: x["ts"])


def _touch(direction: str, candle: dict, level: float) -> bool:
    return level > 0 and (
        candle["high"] >= level if direction == "LONG" else candle["low"] <= level
    )


def advance(episode: dict, candles: list[dict], now: int) -> None:
    if episode.get("status") != "TRACKING":
        return
    previous = _int(episode["last_bar_ts"])
    first = _int(episode["first_eligible_bar_at"])
    stop = _int(episode["stop_at"])
    ending = stop + TRACK_SECONDS
    for candle in candles:
        ts = _int(candle["ts"])
        if ts <= previous or ts < first or ts >= ending:
            continue
        expected = previous + CANDLE_SECONDS
        if ts > expected:
            episode["missing_candles"] += (ts - expected) // CANDLE_SECONDS
            episode["coverage_complete"] = False
        direction = str(episode["direction"]).upper()
        for label, price_key in (
            ("return_to_entry_at", "entry"),
            ("tp1_after_sl_at", "tp1"),
            ("tp2_after_sl_at", "tp2"),
            ("tp3_after_sl_at", "tp3"),
        ):
            if episode.get(label) is None and _touch(direction, candle, _num(episode.get(price_key))):
                episode[label] = ts
                episode[label.replace("_at", "_minutes")] = round((ts - stop) / 60.0, 2)
        previous = ts
        episode["last_bar_ts"] = ts
    episode["last_checked_at"] = now
    if now >= ending:
        # Gaps are counted as unknown, never as proof a recovery failed.
        if previous + CANDLE_SECONDS < ending:
            episode["missing_candles"] += max(0, (ending - previous - CANDLE_SECONDS) // CANDLE_SECONDS)
            episode["coverage_complete"] = False
        episode["status"] = "COMPLETE" if episode["coverage_complete"] else "PARTIAL_COVERAGE"
        episode["completed_at"] = now


def summarize(ledger: dict, now: int) -> dict:
    episodes = list(ledger.get("episodes", {}).values())
    complete = [r for r in episodes if r.get("status") == "COMPLETE"]
    partial = [r for r in episodes if r.get("status") == "PARTIAL_COVERAGE"]
    return {
        "total": len(episodes),
        "tracking": sum(r.get("status") == "TRACKING" for r in episodes),
        "complete": len(complete),
        "partial_coverage": len(partial),
        "return_to_entry_observed": sum(r.get("return_to_entry_at") is not None for r in episodes),
        "tp1_after_stop_observed": sum(r.get("tp1_after_sl_at") is not None for r in episodes),
        "tp2_after_stop_observed": sum(r.get("tp2_after_sl_at") is not None for r in episodes),
        "tp3_after_stop_observed": sum(r.get("tp3_after_sl_at") is not None for r in episodes),
        "confirmed_full_window_tp1": sum(r.get("tp1_after_sl_at") is not None for r in complete),
        "confirmed_full_window_no_tp1": sum(r.get("tp1_after_sl_at") is None for r in complete),
        "note": "Shadow-only first-SL then same-direction recovery. Not realized P&L. Partial windows are not proof of no recovery.",
        "generated_at": now,
    }


def run(now: int | None = None, exchange: Any = None) -> dict:
    now = _int(now if now is not None else time.time())
    if not SOURCE.exists():
        return {"status": "SOURCE_MISSING"}
    source = load_json(SOURCE)
    ledger = load_json(OUTPUT) if OUTPUT.exists() else {"version": VERSION, "episodes": {}, "summary": {}}
    if not isinstance(ledger.get("episodes"), dict):
        raise ValueError("Recovery shadow ledger has invalid episodes")
    added = seed(source, ledger, now)
    active = sorted(
        [r for r in ledger["episodes"].values() if r.get("status") == "TRACKING"],
        key=lambda r: _int(r["stop_at"]),
    )[:MAX_FETCH_PER_RUN]
    if active and exchange is None:
        import ccxt
        exchange = ccxt.okx({"enableRateLimit": True, "timeout": 10000,
                             "options": {"defaultType": "swap"}})
    failures = 0
    for row in active:
        since = _int(row["last_bar_ts"]) + CANDLE_SECONDS
        if now - since > (FETCH_LIMIT - 5) * 60:
            # The API cannot guarantee candles for the full unobserved gap.
            since = (now // 60 - FETCH_LIMIT + 5) * 60
            row["coverage_complete"] = False
            row["missing_candles"] += max(0, (since - _int(row["last_bar_ts"]) - 60) // 60)
            row["last_bar_ts"] = since - CANDLE_SECONDS
        try:
            candles = fetch_closed_candles(exchange, row["symbol"], since, now)
            if candles:
                advance(row, candles, now)
            elif now >= _int(row["stop_at"]) + TRACK_SECONDS:
                advance(row, [], now)
        except Exception as exc:
            failures += 1
            print("RECOVERY_SHADOW_DATA_ERROR", row["symbol"], type(exc).__name__)
    # Never delete active observations. Prune only the oldest completed ones.
    episodes = ledger["episodes"]
    if len(episodes) > MAX_RECORDS:
        finished = sorted(
            ((key, r) for key, r in episodes.items() if r.get("status") != "TRACKING"),
            key=lambda pair: _int(pair[1].get("stop_at")),
        )
        for key, _ in finished[:max(0, len(episodes) - MAX_RECORDS)]:
            episodes.pop(key, None)
    ledger["version"] = VERSION
    ledger["updated_at"] = now
    ledger["summary"] = summarize(ledger, now)
    save_atomic(OUTPUT, ledger)
    print("POST_STOP_RECOVERY_SHADOW", "seeded=", added, "checked=", len(active),
          "api_errors=", failures, "summary=", json.dumps(ledger["summary"]))
    return ledger["summary"]


if __name__ == "__main__":
    run()
