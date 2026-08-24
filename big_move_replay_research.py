"""Historical no-lookahead research for GRASS/SPK-style large moves.

This module is RESEARCH ONLY:
- no Telegram,
- no orders,
- no live entry/exit rule changes,
- no TP/SL/BE mutation.

It screens the last LOOKBACK_DAYS of 4H candles for >=10% directional moves,
then replays the existing Movement Start V2 analyzer around each move using only
candles that were available at each historical evaluation timestamp.

The goal is to answer with evidence:
1) Did Movement Start V2 see the correct direction?
2) How early did PREP / ARMED / TRIGGER appear after the true local origin?
3) What share of the eventual 10/20/40% move was still available after detection?
4) Which large moves were completely missed?
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pandas as pd

import movement_start_v2_shadow as movement_v2
from crypto_universe_guard import filter_crypto_markets

VERSION = "BIG_MOVE_REPLAY_RESEARCH_V1_2026_08_24"
MODE = "RESEARCH_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_RULE_MUTATION"
STATE_FILE = "big_move_replay_research.json"

LOOKBACK_DAYS = int(os.getenv("BIG_MOVE_REPLAY_LOOKBACK_DAYS", "90"))
BATCH_SYMBOLS = int(os.getenv("BIG_MOVE_REPLAY_BATCH_SYMBOLS", "8"))
MIN_EVENT_MOVE_PERCENT = float(os.getenv("BIG_MOVE_REPLAY_MIN_MOVE", "10"))
EVENT_HORIZON_HOURS = 96
EVENT_HORIZON_4H_BARS = EVENT_HORIZON_HOURS // 4
EVENT_MIN_SEPARATION_HOURS = 36
MAX_EVENTS_PER_SYMBOL = 2
REPLAY_HOURS = 16
MAX_STORED_EVENTS = 1600

TF_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".big_move_replay.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
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


def _frame(rows: Iterable[Iterable[Any]]) -> pd.DataFrame:
    data = list(rows or [])
    if not data:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume"])
    for col in ("time", "open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    return frame


def _fetch_range(
    exchange: Any,
    market_symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int,
    *,
    max_bars: int,
) -> pd.DataFrame:
    tf_ms = TF_MS[timeframe]
    cursor = int(since_ms)
    rows: Dict[int, List[Any]] = {}
    loops = 0
    while cursor < until_ms and len(rows) < max_bars and loops < 20:
        loops += 1
        limit = min(300, max(10, max_bars - len(rows)))
        batch = exchange.fetch_ohlcv(market_symbol, timeframe=timeframe, since=cursor, limit=limit)
        if not batch:
            break
        last_ts = cursor
        for row in batch:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            ts = int(row[0])
            if ts < since_ms or ts > until_ms:
                continue
            rows[ts] = list(row[:6])
            last_ts = max(last_ts, ts)
        next_cursor = int(batch[-1][0]) + tf_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if last_ts >= until_ms - tf_ms:
            break
    return _frame(rows[k] for k in sorted(rows))


def _compact_symbol(market: Dict[str, Any]) -> str:
    base = str(market.get("base") or "").upper().strip()
    return f"{base}USDT" if base else ""


def eligible_crypto_swaps(exchange: Any) -> List[Dict[str, str]]:
    markets = exchange.load_markets()
    filtered, _ = filter_crypto_markets(markets)
    result: List[Dict[str, str]] = []
    seen = set()
    for market in filtered.values():
        if not isinstance(market, dict):
            continue
        if not market.get("swap"):
            continue
        if str(market.get("quote") or "").upper() != "USDT":
            continue
        settle = str(market.get("settle") or "USDT").upper()
        if settle != "USDT":
            continue
        if market.get("active") is False:
            continue
        market_symbol = str(market.get("symbol") or "")
        compact = _compact_symbol(market)
        if not market_symbol or not compact or market_symbol in seen:
            continue
        seen.add(market_symbol)
        result.append({"market_symbol": market_symbol, "symbol": compact})
    result.sort(key=lambda row: row["symbol"])
    return result


def directional_percent(direction: str, start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    raw = (end - start) / start * 100.0
    return raw if str(direction).upper() == "LONG" else -raw


def capture_stage(delay_percent: float) -> str:
    delay = max(0.0, float(delay_percent or 0.0))
    if delay <= 2.0:
        return "COK_ERKEN"
    if delay <= 5.0:
        return "ERKEN"
    if delay <= 10.0:
        return "ORTA"
    if delay <= 20.0:
        return "GEC"
    return "COK_GEC"


def event_move_class(move_percent: float) -> str:
    move = max(0.0, float(move_percent or 0.0))
    if move >= 40.0:
        return "OLAGANUSTU_40P"
    if move >= 20.0:
        return "BUYUK_20P"
    return "GUCLU_10P"


def available_share_percent(direction: str, origin: float, detection: float, extreme: float) -> float:
    if min(origin, detection, extreme) <= 0:
        return 0.0
    if direction == "LONG":
        total = extreme - origin
        remaining = extreme - detection
    else:
        total = origin - extreme
        remaining = detection - extreme
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, remaining / total * 100.0))


def detect_big_events(df4h: pd.DataFrame) -> List[Dict[str, Any]]:
    """Find ex-post event origins. These labels are never fed into the analyzer."""
    if df4h is None or len(df4h) < 40:
        return []
    frame = df4h.reset_index(drop=True)
    candidates: List[Dict[str, Any]] = []
    horizon = EVENT_HORIZON_4H_BARS
    for idx in range(4, len(frame) - horizon - 1):
        row = frame.iloc[idx]
        prior = frame.iloc[idx - 3 : idx + 1]
        future = frame.iloc[idx + 1 : idx + 1 + horizon]
        if future.empty:
            continue
        low = _sf(row["low"])
        high = _sf(row["high"])
        ts = int(_sf(row["time"]) / 1000)
        if low > 0 and low <= _sf(prior["low"].min()) * 1.000001:
            future_high = _sf(future["high"].max())
            move = directional_percent("LONG", low, future_high)
            if move >= MIN_EVENT_MOVE_PERCENT:
                peak_idx = int(future["high"].astype(float).idxmax())
                candidates.append({
                    "direction": "LONG",
                    "origin_at": ts,
                    "origin_price": low,
                    "extreme_at": int(_sf(frame.loc[peak_idx, "time"]) / 1000),
                    "extreme_price": future_high,
                    "move_percent": move,
                })
        if high > 0 and high >= _sf(prior["high"].max()) * 0.999999:
            future_low = _sf(future["low"].min())
            move = directional_percent("SHORT", high, future_low)
            if move >= MIN_EVENT_MOVE_PERCENT:
                trough_idx = int(future["low"].astype(float).idxmin())
                candidates.append({
                    "direction": "SHORT",
                    "origin_at": ts,
                    "origin_price": high,
                    "extreme_at": int(_sf(frame.loc[trough_idx, "time"]) / 1000),
                    "extreme_price": future_low,
                    "move_percent": move,
                })

    candidates.sort(key=lambda row: (int(row["origin_at"]), -float(row["move_percent"])))
    selected: List[Dict[str, Any]] = []
    last_by_direction: Dict[str, int] = {}
    min_sep = EVENT_MIN_SEPARATION_HOURS * 3600
    for row in candidates:
        direction = row["direction"]
        previous = int(last_by_direction.get(direction) or 0)
        if previous and int(row["origin_at"]) - previous < min_sep:
            continue
        selected.append(row)
        last_by_direction[direction] = int(row["origin_at"])

    selected.sort(key=lambda row: float(row["move_percent"]), reverse=True)
    return selected[:MAX_EVENTS_PER_SYMBOL]


def _refine_origin(df5m: pd.DataFrame, event: Dict[str, Any]) -> Tuple[int, float]:
    origin_ms = int(event["origin_at"]) * 1000
    window = df5m[(df5m["time"] >= origin_ms) & (df5m["time"] < origin_ms + TF_MS["4h"])]
    if window.empty:
        return int(event["origin_at"]), float(event["origin_price"])
    if event["direction"] == "LONG":
        idx = int(window["low"].astype(float).idxmin())
        return int(_sf(df5m.loc[idx, "time"]) / 1000), _sf(df5m.loc[idx, "low"])
    idx = int(window["high"].astype(float).idxmax())
    return int(_sf(df5m.loc[idx, "time"]) / 1000), _sf(df5m.loc[idx, "high"])


def _signal_snapshot(result: Dict[str, Any], at: int, origin_at: int, origin_price: float, extreme_price: float) -> Dict[str, Any]:
    direction = str(result.get("direction") or "")
    entry = _sf(result.get("entry"))
    delay = max(0.0, directional_percent(direction, origin_price, entry))
    features = result.get("features") if isinstance(result.get("features"), dict) else {}
    return {
        "at": int(at),
        "minutes_after_origin": round(max(0, at - origin_at) / 60.0, 2),
        "direction": direction,
        "stage": result.get("stage"),
        "score": result.get("score"),
        "opposite_score": result.get("opposite_score"),
        "entry": entry,
        "risk_percent": result.get("risk_percent"),
        "delay_from_origin_percent": round(delay, 4),
        "capture_stage": capture_stage(delay),
        "available_share_to_event_extreme_percent": round(
            available_share_percent(direction, origin_price, entry, extreme_price), 2
        ),
        "volume_ratio_5m": round(_sf(features.get("volume_ratio"), 0.0), 4),
        "volume_wake": round(_sf(features.get("volume_wake"), 0.0), 4),
        "rsi_5m": round(_sf(features.get("rsi"), 0.0), 2),
        "atr_compression": round(_sf(features.get("atr_compression"), 0.0), 4),
        "range_compression": round(_sf(features.get("range_compression"), 0.0), 4),
        "internal_break_long": bool(features.get("internal_break_long")),
        "internal_break_short": bool(features.get("internal_break_short")),
        "squeeze_recent": bool(features.get("squeeze_recent")),
        "squeeze_release": bool(features.get("squeeze_release")),
    }


def replay_event(exchange: Any, market_symbol: str, compact_symbol: str, event: Dict[str, Any]) -> Dict[str, Any]:
    origin_4h_ms = int(event["origin_at"]) * 1000
    end_ms = origin_4h_ms + (REPLAY_HOURS + 5) * 3600 * 1000

    df5 = _fetch_range(exchange, market_symbol, "5m", origin_4h_ms - 10 * 3600 * 1000, end_ms, max_bars=420)
    df15 = _fetch_range(exchange, market_symbol, "15m", origin_4h_ms - 22 * 3600 * 1000, end_ms, max_bars=220)
    df1 = _fetch_range(exchange, market_symbol, "1h", origin_4h_ms - 80 * 3600 * 1000, end_ms, max_bars=140)
    df4 = _fetch_range(exchange, market_symbol, "4h", origin_4h_ms - 13 * 24 * 3600 * 1000, end_ms, max_bars=120)

    if min(len(df5), len(df15), len(df1), len(df4)) <= 0:
        return {"status": "REPLAY_DATA_MISSING"}

    precise_at, precise_price = _refine_origin(df5, event)
    replay_start_ms = (precise_at + 5 * 60) * 1000
    replay_end_ms = (precise_at + REPLAY_HOURS * 3600) * 1000

    first_any: Optional[Dict[str, Any]] = None
    first_confirmed: Optional[Dict[str, Any]] = None
    first_trigger: Optional[Dict[str, Any]] = None
    first_opposite: Optional[Dict[str, Any]] = None

    for _, bar in df5.iterrows():
        bar_open_ms = int(_sf(bar["time"]))
        eval_ms = bar_open_ms + TF_MS["5m"]
        if eval_ms < replay_start_ms or eval_ms > replay_end_ms:
            continue

        s5 = df5[df5["time"] <= eval_ms].tail(125)
        s15 = df15[df15["time"] <= eval_ms].tail(105)
        s1 = df1[df1["time"] <= eval_ms].tail(90)
        s4 = df4[df4["time"] <= eval_ms].tail(85)
        if len(s5) < 62 or len(s15) < 57 or len(s1) < 57 or len(s4) < 57:
            continue

        current_price = _sf(bar["close"])
        result = movement_v2.analyze(compact_symbol, s5, s15, s1, s4, current_price)
        if not result:
            continue
        eval_at = int(eval_ms / 1000)
        snapshot = _signal_snapshot(
            result,
            eval_at,
            precise_at,
            precise_price,
            float(event["extreme_price"]),
        )
        if str(result.get("direction")) != str(event["direction"]):
            if first_opposite is None:
                first_opposite = snapshot
            continue
        if first_any is None:
            first_any = snapshot
        if result.get("stage") in {"ARMED", "TRIGGER"} and first_confirmed is None:
            first_confirmed = snapshot
        if result.get("stage") == "TRIGGER":
            first_trigger = snapshot
            break

    chosen = first_confirmed or first_any
    return {
        "status": "REPLAY_OK",
        "precise_origin_at": precise_at,
        "precise_origin_price": round(precise_price, 12),
        "event_direction": event["direction"],
        "event_move_percent": round(float(event["move_percent"]), 4),
        "event_move_class": event_move_class(float(event["move_percent"])),
        "event_extreme_at": int(event["extreme_at"]),
        "event_extreme_price": round(float(event["extreme_price"]), 12),
        "first_same_direction": first_any,
        "first_confirmed": first_confirmed,
        "first_trigger": first_trigger,
        "first_opposite": first_opposite,
        "detected_same_direction": first_any is not None,
        "detected_confirmed": first_confirmed is not None,
        "detected_trigger": first_trigger is not None,
        "early_confirmed": bool(
            first_confirmed is not None
            and float(first_confirmed.get("delay_from_origin_percent") or 999) <= 5.0
        ),
        "chosen_detection_delay_percent": (
            chosen.get("delay_from_origin_percent") if isinstance(chosen, dict) else None
        ),
        "chosen_available_share_percent": (
            chosen.get("available_share_to_event_extreme_percent") if isinstance(chosen, dict) else None
        ),
        "replay_window_hours": REPLAY_HOURS,
        "no_lookahead": True,
    }


def _summary(events: Dict[str, Any]) -> Dict[str, Any]:
    usable = [row for row in events.values() if isinstance(row, dict) and row.get("status") == "REPLAY_OK"]
    by_class = Counter(str(row.get("event_move_class") or "UNKNOWN") for row in usable)
    by_direction = Counter(str(row.get("event_direction") or "UNKNOWN") for row in usable)
    detected = sum(bool(row.get("detected_same_direction")) for row in usable)
    confirmed = sum(bool(row.get("detected_confirmed")) for row in usable)
    triggers = sum(bool(row.get("detected_trigger")) for row in usable)
    early = sum(bool(row.get("early_confirmed")) for row in usable)
    delays = [
        _sf(row.get("chosen_detection_delay_percent"), float("nan"))
        for row in usable
        if row.get("chosen_detection_delay_percent") is not None
    ]
    delays = [x for x in delays if math.isfinite(x)]
    shares = [
        _sf(row.get("chosen_available_share_percent"), float("nan"))
        for row in usable
        if row.get("chosen_available_share_percent") is not None
    ]
    shares = [x for x in shares if math.isfinite(x)]
    sample = len(usable)
    return {
        "sample": sample,
        "by_move_class": dict(by_class),
        "by_direction": dict(by_direction),
        "same_direction_detection_rate_percent": round(detected / sample * 100.0, 2) if sample else 0.0,
        "confirmed_detection_rate_percent": round(confirmed / sample * 100.0, 2) if sample else 0.0,
        "trigger_detection_rate_percent": round(triggers / sample * 100.0, 2) if sample else 0.0,
        "early_confirmed_rate_percent": round(early / sample * 100.0, 2) if sample else 0.0,
        "avg_detection_delay_percent": round(sum(delays) / len(delays), 3) if delays else None,
        "avg_available_share_percent": round(sum(shares) / len(shares), 2) if shares else None,
    }


def run(exchange: Any, state_file: str = STATE_FILE, *, now_ts: Optional[int] = None) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    state = _load(state_file)
    if not state:
        state = {
            "version": VERSION,
            "mode": MODE,
            "cursor_index": 0,
            "pass_number": 1,
            "events": {},
            "errors": [],
        }
    state["version"] = VERSION
    state["mode"] = MODE
    events = state.get("events") if isinstance(state.get("events"), dict) else {}
    state["events"] = events

    universe = eligible_crypto_swaps(exchange)
    total = len(universe)
    cursor = int(state.get("cursor_index") or 0)
    if total == 0:
        state["updated_at"] = now
        state["summary"] = _summary(events)
        _atomic_save(state_file, state)
        return state
    if cursor >= total:
        cursor = 0
        state["pass_number"] = int(state.get("pass_number") or 1) + 1

    batch = universe[cursor : cursor + BATCH_SYMBOLS]
    processed: List[str] = []
    new_events = 0
    errors: List[Dict[str, Any]] = []
    since_ms = (now - LOOKBACK_DAYS * 24 * 3600) * 1000
    until_ms = now * 1000

    for market in batch:
        market_symbol = market["market_symbol"]
        compact = market["symbol"]
        processed.append(compact)
        try:
            df4 = _fetch_range(exchange, market_symbol, "4h", since_ms, until_ms, max_bars=700)
            historical_events = detect_big_events(df4.iloc[:-1].copy() if len(df4) > 1 else df4)
            for event in historical_events:
                event_id = f"{compact}_{event['direction']}_{int(event['origin_at'])}"
                replay = replay_event(exchange, market_symbol, compact, event)
                record = {
                    "event_id": event_id,
                    "symbol": compact,
                    "market_symbol": market_symbol,
                    "research_version": VERSION,
                    "analyzer_version": movement_v2.VERSION,
                    "lookback_days": LOOKBACK_DAYS,
                    "researched_at": now,
                    **replay,
                }
                if event_id not in events:
                    new_events += 1
                events[event_id] = record
        except Exception as exc:
            errors.append({"symbol": compact, "error": str(exc)[:240], "at": now})

    next_cursor = cursor + len(batch)
    if next_cursor >= total:
        next_cursor = 0
        state["pass_number"] = int(state.get("pass_number") or 1) + 1
    state["cursor_index"] = next_cursor
    state["universe_count"] = total
    state["processed_this_run"] = processed
    state["new_events_this_run"] = new_events
    state["updated_at"] = now

    existing_errors = state.get("errors") if isinstance(state.get("errors"), list) else []
    state["errors"] = (existing_errors + errors)[-100:]

    if len(events) > MAX_STORED_EVENTS:
        ordered = sorted(
            events.items(),
            key=lambda pair: int((pair[1] or {}).get("researched_at") or 0),
            reverse=True,
        )[:MAX_STORED_EVENTS]
        state["events"] = dict(ordered)
        events = state["events"]

    state["summary"] = _summary(events)
    _atomic_save(state_file, state)
    print(
        "BIG MOVE REPLAY | pass",
        state.get("pass_number"),
        "| cursor",
        state.get("cursor_index"),
        "/",
        total,
        "| processed",
        len(processed),
        "| events",
        len(events),
        "| new",
        new_events,
        "| errors",
        len(errors),
    )
    print("BIG MOVE REPLAY SUMMARY |", json.dumps(state["summary"], ensure_ascii=False))
    return state


def main() -> None:
    import ccxt

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })
    run(exchange)


if __name__ == "__main__":
    main()
