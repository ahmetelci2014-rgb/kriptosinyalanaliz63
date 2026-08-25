"""Historical bootstrap research for Premium Smart Entry.

Purpose
-------
Build an initial evidence model from historical 15M OKX candles so Smart Entry
features do not need to start from zero in live shadow mode.

This module is RESEARCH ONLY:
- no Telegram,
- no exchange orders,
- no live signal admission changes,
- no TP/SL/BE mutation.

Method
------
1. Detect breakout/impulse events chronologically using only candles available at
   the event timestamp.
2. Evaluate predefined retracement-zone hypotheses on future candles.
3. Measure 1R/2R/3R reach, SL-first, MFE/MAE and support/resistance flip retests.
4. Split chronologically into train/holdout data.
5. Produce a bootstrap model only when both train and holdout evidence are
   acceptable. The result is still shadow evidence, never a live gate by itself.

The historical model intentionally uses simple, auditable price structure. More
complex Premium/Movement Start/order-flow evidence can be layered on later.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from ta.volatility import AverageTrueRange

VERSION = "SMART_ENTRY_HISTORICAL_BOOTSTRAP_V1_2026_08_25"
MODE = "RESEARCH_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_RULE_MUTATION"
STATE_FILE = "smart_entry_historical_bootstrap.json"

TIMEFRAME = "15m"
TIMEFRAME_MS = 15 * 60 * 1000
LOOKBACK_DAYS = int(os.getenv("SMART_ENTRY_HISTORY_DAYS", "120"))
EVENT_LOOKBACK_BARS = int(os.getenv("SMART_ENTRY_EVENT_LOOKBACK", "32"))
EVENT_HORIZON_BARS = int(os.getenv("SMART_ENTRY_EVENT_HORIZON", "32"))
MIN_EVENT_SEPARATION_BARS = int(os.getenv("SMART_ENTRY_EVENT_SEPARATION", "8"))
MIN_IMPULSE_PERCENT = float(os.getenv("SMART_ENTRY_MIN_IMPULSE_PERCENT", "1.25"))
MIN_IMPULSE_ATR = float(os.getenv("SMART_ENTRY_MIN_IMPULSE_ATR", "2.0"))
STOP_ATR_BUFFER = float(os.getenv("SMART_ENTRY_STOP_ATR_BUFFER", "0.15"))
FLIP_TOLERANCE_ATR = float(os.getenv("SMART_ENTRY_FLIP_TOLERANCE_ATR", "0.25"))
TRAIN_SHARE = float(os.getenv("SMART_ENTRY_TRAIN_SHARE", "0.70"))
MIN_TRAIN_SAMPLE = int(os.getenv("SMART_ENTRY_MIN_TRAIN_SAMPLE", "20"))
MIN_HOLDOUT_SAMPLE = int(os.getenv("SMART_ENTRY_MIN_HOLDOUT_SAMPLE", "8"))
MAX_EVENTS_PER_SYMBOL = int(os.getenv("SMART_ENTRY_MAX_EVENTS_PER_SYMBOL", "500"))

ZONE_BUCKETS: Tuple[Tuple[str, float, float], ...] = (
    ("SHALLOW_030_042", 0.30, 0.42),
    ("MID_042_052", 0.42, 0.52),
    ("GOLDEN_052_062", 0.52, 0.62),
    ("DEEP_062_072", 0.62, 0.72),
    ("EXTREME_072_079", 0.72, 0.79),
)


@dataclass(frozen=True)
class ImpulseEvent:
    symbol: str
    direction: str
    detected_at: int
    origin_price: float
    impulse_price: float
    breakout_level: float
    atr: float
    event_index: int


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _frame(rows: Iterable[Iterable[Any]]) -> pd.DataFrame:
    data = list(rows or [])
    if not data:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume"])
    for col in ("time", "open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    if len(df) >= 20:
        df["atr"] = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    else:
        df["atr"] = 0.0
    return df


def _fetch_range(exchange: Any, market_symbol: str, since_ms: int, until_ms: int, max_bars: int) -> pd.DataFrame:
    cursor = int(since_ms)
    rows: Dict[int, List[Any]] = {}
    loops = 0
    while cursor < until_ms and len(rows) < max_bars and loops < 80:
        loops += 1
        limit = min(300, max(10, max_bars - len(rows)))
        batch = exchange.fetch_ohlcv(market_symbol, timeframe=TIMEFRAME, since=cursor, limit=limit)
        if not batch:
            break
        for row in batch:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            ts = int(row[0])
            if since_ms <= ts <= until_ms:
                rows[ts] = list(row[:6])
        next_cursor = int(batch[-1][0]) + TIMEFRAME_MS
        if next_cursor <= cursor:
            break
        cursor = next_cursor
    return _frame(rows[key] for key in sorted(rows))


def directional_move_percent(direction: str, start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    raw = (end - start) / start * 100.0
    return raw if str(direction).upper() == "LONG" else -raw


def zone_price(direction: str, origin: float, impulse: float, ratio: float) -> float:
    direction = str(direction).upper()
    span = abs(float(impulse) - float(origin))
    if direction == "LONG":
        return float(impulse) - span * float(ratio)
    return float(impulse) + span * float(ratio)


def _touches_zone(direction: str, low: float, high: float, origin: float, impulse: float, lo_ratio: float, hi_ratio: float) -> bool:
    p1 = zone_price(direction, origin, impulse, lo_ratio)
    p2 = zone_price(direction, origin, impulse, hi_ratio)
    bottom, top = sorted((p1, p2))
    return float(low) <= top and float(high) >= bottom


def _first_hit(direction: str, entry: float, stop: float, future: pd.DataFrame) -> Dict[str, Any]:
    risk = abs(entry - stop)
    if min(entry, stop) <= 0 or risk <= 0 or future.empty:
        return {"result": "INVALID", "mfe_r": 0.0, "mae_r": 0.0, "hit_1r": False, "hit_2r": False, "hit_3r": False}

    sign = 1.0 if str(direction).upper() == "LONG" else -1.0
    targets = {1: entry + sign * risk, 2: entry + sign * 2.0 * risk, 3: entry + sign * 3.0 * risk}
    hit = {1: False, 2: False, 3: False}
    mfe_r = 0.0
    mae_r = 0.0
    result = "TIMEOUT"

    for _, row in future.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        if sign > 0:
            favorable = (high - entry) / risk
            adverse = (entry - low) / risk
            stop_hit = low <= stop
            one = high >= targets[1]
            two = high >= targets[2]
            three = high >= targets[3]
        else:
            favorable = (entry - low) / risk
            adverse = (high - entry) / risk
            stop_hit = high >= stop
            one = low <= targets[1]
            two = low <= targets[2]
            three = low <= targets[3]
        mfe_r = max(mfe_r, favorable)
        mae_r = max(mae_r, adverse)

        # Conservative same-candle ordering: if stop and target are both touched,
        # assume stop came first unless a prior candle already proved the target.
        if stop_hit and not hit[1]:
            result = "SL_FIRST"
            break
        if one:
            hit[1] = True
        if stop_hit and hit[1]:
            result = "TP1_THEN_STOP_OR_BE"
            break
        if two:
            hit[2] = True
        if three:
            hit[3] = True
            result = "TP3"
            break

    if result == "TIMEOUT":
        if hit[2]:
            result = "TP2"
        elif hit[1]:
            result = "TP1"

    return {
        "result": result,
        "mfe_r": round(max(0.0, mfe_r), 4),
        "mae_r": round(max(0.0, mae_r), 4),
        "hit_1r": bool(hit[1]),
        "hit_2r": bool(hit[2]),
        "hit_3r": bool(hit[3]),
    }


def detect_events(symbol: str, df: pd.DataFrame) -> List[ImpulseEvent]:
    if df is None or len(df) < EVENT_LOOKBACK_BARS + EVENT_HORIZON_BARS + 5:
        return []
    events: List[ImpulseEvent] = []
    last_index = {"LONG": -10_000, "SHORT": -10_000}

    # The final candle may still be forming on some exchanges. Research events
    # therefore stop before it and before the future outcome horizon.
    end_index = len(df) - EVENT_HORIZON_BARS - 1
    for idx in range(EVENT_LOOKBACK_BARS, end_index):
        row = df.iloc[idx]
        atr = _sf(row.get("atr"), 0.0)
        close = _sf(row["close"])
        if min(atr, close) <= 0:
            continue
        prior = df.iloc[idx - EVENT_LOOKBACK_BARS : idx]
        prior_high = _sf(prior["high"].max())
        prior_low = _sf(prior["low"].min())

        candidates: List[Tuple[str, float, float, float]] = []
        if close > prior_high:
            origin = prior_low
            impulse = max(_sf(row["high"]), close)
            candidates.append(("LONG", origin, impulse, prior_high))
        if close < prior_low:
            origin = prior_high
            impulse = min(_sf(row["low"]), close)
            candidates.append(("SHORT", origin, impulse, prior_low))

        for direction, origin, impulse, level in candidates:
            if idx - last_index[direction] < MIN_EVENT_SEPARATION_BARS:
                continue
            move_pct = directional_move_percent(direction, origin, impulse)
            span = abs(impulse - origin)
            if move_pct < MIN_IMPULSE_PERCENT or span < MIN_IMPULSE_ATR * atr:
                continue
            events.append(
                ImpulseEvent(
                    symbol=str(symbol).upper(),
                    direction=direction,
                    detected_at=int(_sf(row["time"]) / 1000),
                    origin_price=origin,
                    impulse_price=impulse,
                    breakout_level=level,
                    atr=atr,
                    event_index=idx,
                )
            )
            last_index[direction] = idx
            if len(events) >= MAX_EVENTS_PER_SYMBOL:
                return events
    return events


def evaluate_event(event: ImpulseEvent, df: pd.DataFrame) -> List[Dict[str, Any]]:
    start = event.event_index + 1
    future = df.iloc[start : start + EVENT_HORIZON_BARS].reset_index(drop=True)
    if future.empty:
        return []
    rows: List[Dict[str, Any]] = []

    for zone_name, lo_ratio, hi_ratio in ZONE_BUCKETS:
        touch_idx: Optional[int] = None
        for idx, candle in future.iterrows():
            if _touches_zone(
                event.direction,
                float(candle["low"]),
                float(candle["high"]),
                event.origin_price,
                event.impulse_price,
                lo_ratio,
                hi_ratio,
            ):
                touch_idx = int(idx)
                break
        if touch_idx is None:
            continue
        ratio_mid = (lo_ratio + hi_ratio) / 2.0
        entry = zone_price(event.direction, event.origin_price, event.impulse_price, ratio_mid)
        if event.direction == "LONG":
            stop = event.origin_price - event.atr * STOP_ATR_BUFFER
        else:
            stop = event.origin_price + event.atr * STOP_ATR_BUFFER
        outcome = _first_hit(event.direction, entry, stop, future.iloc[touch_idx:].reset_index(drop=True))
        rows.append(
            {
                "symbol": event.symbol,
                "direction": event.direction,
                "detected_at": event.detected_at,
                "zone": zone_name,
                "zone_low_ratio": lo_ratio,
                "zone_high_ratio": hi_ratio,
                "entry": round(entry, 12),
                "stop": round(stop, 12),
                "touch_delay_bars": touch_idx + 1,
                **outcome,
            }
        )

    # S/R flip is evaluated independently from Fibonacci zones.
    tolerance = max(event.atr * FLIP_TOLERANCE_ATR, abs(event.breakout_level) * 0.0005)
    flip_idx: Optional[int] = None
    for idx, candle in future.iterrows():
        low = float(candle["low"])
        high = float(candle["high"])
        if low <= event.breakout_level + tolerance and high >= event.breakout_level - tolerance:
            flip_idx = int(idx)
            break
    if flip_idx is not None:
        entry = event.breakout_level
        if event.direction == "LONG":
            stop = min(event.origin_price, entry - event.atr * 1.25)
        else:
            stop = max(event.origin_price, entry + event.atr * 1.25)
        outcome = _first_hit(event.direction, entry, stop, future.iloc[flip_idx:].reset_index(drop=True))
        rows.append(
            {
                "symbol": event.symbol,
                "direction": event.direction,
                "detected_at": event.detected_at,
                "zone": "SR_FLIP_RETEST",
                "zone_low_ratio": None,
                "zone_high_ratio": None,
                "entry": round(entry, 12),
                "stop": round(stop, 12),
                "touch_delay_bars": flip_idx + 1,
                **outcome,
            }
        )
    return rows


def wilson_lower(successes: int, sample: int, z: float = 1.96) -> float:
    if sample <= 0:
        return 0.0
    p = successes / sample
    denom = 1.0 + (z * z) / sample
    centre = p + (z * z) / (2.0 * sample)
    margin = z * math.sqrt((p * (1.0 - p) + (z * z) / (4.0 * sample)) / sample)
    return max(0.0, (centre - margin) / denom)


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    sample = len(rows)
    counts = Counter(str(row.get("result") or "UNKNOWN") for row in rows)
    hit1 = sum(bool(row.get("hit_1r")) for row in rows)
    hit2 = sum(bool(row.get("hit_2r")) for row in rows)
    hit3 = sum(bool(row.get("hit_3r")) for row in rows)
    sl_first = counts.get("SL_FIRST", 0)
    mfe = [_sf(row.get("mfe_r")) for row in rows]
    mae = [_sf(row.get("mae_r")) for row in rows]
    return {
        "sample": sample,
        "hit_1r_percent": round(hit1 / sample * 100.0, 2) if sample else 0.0,
        "hit_2r_percent": round(hit2 / sample * 100.0, 2) if sample else 0.0,
        "hit_3r_percent": round(hit3 / sample * 100.0, 2) if sample else 0.0,
        "sl_first_percent": round(sl_first / sample * 100.0, 2) if sample else 0.0,
        "hit_2r_wilson_lower_percent": round(wilson_lower(hit2, sample) * 100.0, 2) if sample else 0.0,
        "avg_mfe_r": round(sum(mfe) / sample, 4) if sample else 0.0,
        "avg_mae_r": round(sum(mae) / sample, 4) if sample else 0.0,
        "result_counts": dict(counts),
    }


def _split_time(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    ordered = sorted(rows, key=lambda row: int(row.get("detected_at") or 0))
    if len(ordered) <= 1:
        return list(ordered), []
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * TRAIN_SHARE)))
    return list(ordered[:cut]), list(ordered[cut:])


def build_model(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    by_zone: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_zone[str(row.get("zone") or "UNKNOWN")].append(dict(row))

    zones: Dict[str, Any] = {}
    ranked: List[Tuple[float, str]] = []
    for zone, zone_rows in sorted(by_zone.items()):
        train, holdout = _split_time(zone_rows)
        train_metrics = summarize(train)
        holdout_metrics = summarize(holdout)

        train_ok = (
            train_metrics["sample"] >= MIN_TRAIN_SAMPLE
            and train_metrics["hit_2r_wilson_lower_percent"] >= 35.0
            and train_metrics["sl_first_percent"] <= 40.0
        )
        holdout_ok = (
            holdout_metrics["sample"] >= MIN_HOLDOUT_SAMPLE
            and holdout_metrics["hit_2r_percent"] >= 45.0
            and holdout_metrics["sl_first_percent"] <= 40.0
        )
        if train_ok and holdout_ok:
            evidence_status = "HISTORICALLY_VALIDATED"
        elif train_ok:
            evidence_status = "TRAIN_EDGE_HOLDOUT_INSUFFICIENT"
        else:
            evidence_status = "EDGE_NOT_PROVEN"

        score = (
            train_metrics["hit_2r_wilson_lower_percent"]
            + holdout_metrics["hit_2r_percent"] * 0.35
            - train_metrics["sl_first_percent"] * 0.35
            - holdout_metrics["sl_first_percent"] * 0.15
        )
        if evidence_status == "HISTORICALLY_VALIDATED":
            ranked.append((score, zone))
        zones[zone] = {
            "evidence_status": evidence_status,
            "train": train_metrics,
            "holdout": holdout_metrics,
            "ranking_score": round(score, 3),
        }

    ranked.sort(reverse=True)
    best_zone = ranked[0][1] if ranked else None
    return {
        "best_validated_zone": best_zone,
        "validated_zone_count": len(ranked),
        "zones": zones,
    }


def research_symbol(exchange: Any, symbol: str, market_symbol: str, *, now_ms: Optional[int] = None, lookback_days: int = LOOKBACK_DAYS) -> Dict[str, Any]:
    now_ms = int(now_ms if now_ms is not None else time.time() * 1000)
    since_ms = now_ms - int(lookback_days) * 24 * 60 * 60 * 1000
    max_bars = int(lookback_days * 24 * 4) + 100
    df = _fetch_range(exchange, market_symbol, since_ms, now_ms, max_bars=max_bars)
    events = detect_events(symbol, df)
    rows: List[Dict[str, Any]] = []
    for event in events:
        rows.extend(evaluate_event(event, df))
    model = build_model(rows)
    return {
        "symbol": str(symbol).upper(),
        "market_symbol": market_symbol,
        "timeframe": TIMEFRAME,
        "lookback_days": int(lookback_days),
        "candle_count": int(len(df)),
        "event_count": int(len(events)),
        "hypothesis_count": int(len(rows)),
        "model": model,
    }


def aggregate(symbol_results: Sequence[Dict[str, Any]], raw_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "generated_at": int(time.time()),
        "method": "chronological breakout/impulse -> retracement-zone and S/R-flip hypotheses -> 70/30 time holdout",
        "live_use": "BOOTSTRAP_ONLY_NOT_A_LIVE_GATE",
        "symbols": list(symbol_results),
        "global_model": build_model(raw_rows),
        "notes": [
            "Historical evidence reduces cold-start time but cannot prove current live edge by itself.",
            "Order-book/taker-flow history is not reconstructed here; Movement Start V3 still needs forward evidence.",
            "Only historically validated zones should be candidates for later live-shadow scoring.",
        ],
    }


def atomic_save(path: str, payload: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, prefix=".smart_entry_history.", suffix=".tmp", delete=False) as handle:
            temp_path = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
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
