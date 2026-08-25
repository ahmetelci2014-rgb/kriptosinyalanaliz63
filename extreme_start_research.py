"""Historical Extreme Start research for Premium early entries.

Goal
----
Measure whether LONG entries can be detected close to a recent macro swing low and
SHORT entries close to a recent macro swing high, before a large part of the move
has already happened.

Research only:
- no Telegram,
- no exchange orders,
- no live Premium gate changes,
- all setup features use only data available at the signal timestamp,
- future candles are used only after the signal to score the outcome.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

VERSION = "EXTREME_START_RESEARCH_V1_2026_08_25"
MODE = "HISTORICAL_RESEARCH_ONLY_NO_LIVE_MUTATION"
STATE_FILE = "extreme_start_research.json"

TIMEFRAME = "15m"
TIMEFRAME_MS = 15 * 60 * 1000
LOOKBACK_DAYS = int(os.getenv("EXTREME_START_HISTORY_DAYS", "120"))
HORIZON_BARS = int(os.getenv("EXTREME_START_HORIZON_BARS", "48"))
MIN_SEPARATION_BARS = int(os.getenv("EXTREME_START_MIN_SEPARATION", "12"))
TRAIN_SHARE = float(os.getenv("EXTREME_START_TRAIN_SHARE", "0.70"))

MACRO_LOOKBACK_4H = int(os.getenv("EXTREME_START_MACRO_4H_BARS", "60"))
MAX_ORIGIN_AGE_4H = int(os.getenv("EXTREME_START_MAX_ORIGIN_AGE_4H", "36"))
START_MAX_ATR = float(os.getenv("EXTREME_START_MAX_ATR", "4.50"))
MAX_RISK_PERCENT = float(os.getenv("EXTREME_START_MAX_RISK_PERCENT", "2.25"))
MIN_RISK_PERCENT = float(os.getenv("EXTREME_START_MIN_RISK_PERCENT", "0.25"))
STOP_ATR_BUFFER = float(os.getenv("EXTREME_START_STOP_ATR_BUFFER", "0.15"))
MIN_VOLUME_RATIO = float(os.getenv("EXTREME_START_MIN_VOLUME_RATIO", "1.10"))


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def frame(rows: Iterable[Iterable[Any]]) -> pd.DataFrame:
    data = list(rows or [])
    if not data:
        return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(data, columns=["time", "open", "high", "low", "close", "volume"])
    for col in ("time", "open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return (
        df.dropna()
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )


def fetch_range(exchange: Any, market_symbol: str, since_ms: int, until_ms: int, max_bars: int) -> pd.DataFrame:
    cursor = int(since_ms)
    rows: Dict[int, List[Any]] = {}
    loops = 0
    while cursor < until_ms and len(rows) < max_bars and loops < 100:
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
    return frame(rows[key] for key in sorted(rows))


def _atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(window).mean()


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1.0 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1.0 / window, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


def enrich_15m(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["atr"] = _atr(out)
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    out["ema20_slope"] = out["ema20"] - out["ema20"].shift(3)
    out["rsi"] = _rsi(out["close"])
    out["volume_avg"] = out["volume"].rolling(20).mean()
    out["volume_ratio"] = out["volume"] / out["volume_avg"].replace(0, float("nan"))
    return out.dropna().reset_index(drop=True)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    work = df.copy()
    work["dt"] = pd.to_datetime(work["time"], unit="ms", utc=True)
    work = work.set_index("dt")
    agg = work.resample(rule, label="right", closed="right").agg(
        {
            "time": "last",
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    agg = agg.dropna().reset_index(drop=True)
    agg["atr"] = _atr(agg)
    agg["ema20"] = agg["close"].ewm(span=20, adjust=False).mean()
    agg["ema20_slope"] = agg["ema20"] - agg["ema20"].shift(3)
    agg["rsi"] = _rsi(agg["close"])
    return agg.dropna().reset_index(drop=True)


def close_power(row: pd.Series) -> float:
    high = _sf(row.get("high"))
    low = _sf(row.get("low"))
    close = _sf(row.get("close"))
    span = high - low
    if span <= 0:
        return 0.5
    return max(0.0, min(1.0, (close - low) / span))


def phase_from_atr(move_atr: float) -> str:
    if move_atr <= 1.50:
        return "NEAR_EXTREME"
    if move_atr <= 3.00:
        return "START_ENTRY"
    if move_atr <= START_MAX_ATR:
        return "EARLY_ENTRY"
    return "LATE_CONTINUATION"


def _macro_origin(macro: pd.DataFrame, direction: str, current_price: float) -> Optional[Dict[str, Any]]:
    if macro is None or len(macro) < 25 or current_price <= 0:
        return None
    recent = macro.iloc[-MACRO_LOOKBACK_4H:].copy()
    if recent.empty:
        return None
    direction = str(direction).upper()
    if direction == "LONG":
        pos = int(recent["low"].astype(float).values.argmin())
        row = recent.iloc[pos]
        origin = _sf(row["low"])
    else:
        pos = int(recent["high"].astype(float).values.argmax())
        row = recent.iloc[pos]
        origin = _sf(row["high"])
    atr = _sf(row.get("atr"), _sf(recent.iloc[-1].get("atr")))
    if min(origin, atr) <= 0:
        return None
    age = len(recent) - 1 - pos
    directional_move = current_price - origin if direction == "LONG" else origin - current_price
    move_atr = directional_move / atr
    move_pct = directional_move / origin * 100.0
    return {
        "origin_price": origin,
        "origin_time": int(_sf(row.get("time"))),
        "origin_age_4h": int(age),
        "origin_atr": atr,
        "move_from_origin_atr": round(move_atr, 4),
        "move_from_origin_percent": round(move_pct, 4),
        "phase": phase_from_atr(move_atr),
    }


def _one_hour_turn(hourly: pd.DataFrame, direction: str) -> bool:
    if hourly is None or len(hourly) < 5:
        return False
    row = hourly.iloc[-1]
    close = _sf(row["close"])
    ema = _sf(row["ema20"])
    slope = _sf(row["ema20_slope"])
    rsi = _sf(row["rsi"], 50.0)
    if direction == "LONG":
        return close >= ema and slope > 0 and rsi >= 47
    return close <= ema and slope < 0 and rsi <= 53


def _micro_trigger(df15: pd.DataFrame, direction: str) -> Optional[Dict[str, Any]]:
    if df15 is None or len(df15) < 32:
        return None
    row = df15.iloc[-1]
    prior_break = df15.iloc[-9:-1]
    prior_sweep = df15.iloc[-18:-4]
    last3 = df15.iloc[-3:]
    close = _sf(row["close"])
    atr = _sf(row["atr"])
    volume_ratio = _sf(row["volume_ratio"], 1.0)
    cp = close_power(row)
    if min(close, atr) <= 0:
        return None

    if direction == "LONG":
        level = _sf(prior_break["high"].max())
        old_extreme = _sf(prior_sweep["low"].min())
        sweep = bool(((last3["low"] < old_extreme * 0.999) & (last3["close"] > old_extreme)).any())
        structure = close > level and cp >= 0.56
        local_stop = _sf(df15.iloc[-7:]["low"].min()) - STOP_ATR_BUFFER * atr
        risk = close - local_stop
    else:
        level = _sf(prior_break["low"].min())
        old_extreme = _sf(prior_sweep["high"].max())
        sweep = bool(((last3["high"] > old_extreme * 1.001) & (last3["close"] < old_extreme)).any())
        structure = close < level and cp <= 0.44
        local_stop = _sf(df15.iloc[-7:]["high"].max()) + STOP_ATR_BUFFER * atr
        risk = local_stop - close

    risk_pct = risk / close * 100.0 if risk > 0 else 999.0
    volume_ok = volume_ratio >= MIN_VOLUME_RATIO
    if not structure or not volume_ok or not (MIN_RISK_PERCENT <= risk_pct <= MAX_RISK_PERCENT):
        return None
    return {
        "entry": close,
        "stop": local_stop,
        "risk_abs": risk,
        "risk_percent": round(risk_pct, 4),
        "volume_ratio": round(volume_ratio, 4),
        "close_power": round(cp, 4),
        "liquidity_sweep": sweep,
        "break_level": level,
    }


def detect_setups(symbol: str, raw15: pd.DataFrame) -> List[Dict[str, Any]]:
    df15 = enrich_15m(raw15)
    if len(df15) < 300:
        return []
    hourly = resample(raw15, "1h")
    macro = resample(raw15, "4h")
    if len(hourly) < 40 or len(macro) < 30:
        return []

    setups: List[Dict[str, Any]] = []
    last_signal = {"LONG": -10_000, "SHORT": -10_000}
    # Last HORIZON_BARS are reserved for outcome evaluation.
    for idx in range(160, len(df15) - HORIZON_BARS - 1):
        now = int(_sf(df15.iloc[idx]["time"]))
        hist15 = df15.iloc[: idx + 1]
        hist1 = hourly[hourly["time"] <= now]
        hist4 = macro[macro["time"] <= now]
        if len(hist1) < 25 or len(hist4) < 25:
            continue
        current_price = _sf(hist15.iloc[-1]["close"])

        for direction in ("LONG", "SHORT"):
            if idx - last_signal[direction] < MIN_SEPARATION_BARS:
                continue
            origin = _macro_origin(hist4, direction, current_price)
            if not origin:
                continue
            move_atr = _sf(origin.get("move_from_origin_atr"), 999.0)
            age = int(origin.get("origin_age_4h") or 0)
            if move_atr < 0 or move_atr > START_MAX_ATR or age > MAX_ORIGIN_AGE_4H:
                continue
            if not _one_hour_turn(hist1, direction):
                continue
            trigger = _micro_trigger(hist15, direction)
            if not trigger:
                continue

            setups.append(
                {
                    "symbol": str(symbol).upper(),
                    "direction": direction,
                    "detected_at": now // 1000 if now > 10_000_000_000 else now,
                    "event_index": idx,
                    **origin,
                    **trigger,
                }
            )
            last_signal[direction] = idx
    return setups


def evaluate_setup(setup: Dict[str, Any], df15: pd.DataFrame) -> Dict[str, Any]:
    idx = int(setup["event_index"])
    future = df15.iloc[idx + 1 : idx + 1 + HORIZON_BARS]
    entry = _sf(setup["entry"])
    stop = _sf(setup["stop"])
    direction = str(setup["direction"]).upper()
    risk = abs(entry - stop)
    if risk <= 0 or future.empty:
        return {**setup, "result": "INVALID", "mfe_r": 0.0, "mae_r": 0.0}

    sign = 1.0 if direction == "LONG" else -1.0
    t1 = entry + sign * risk
    t2 = entry + sign * 2.0 * risk
    t3 = entry + sign * 3.0 * risk
    hit1 = hit2 = False
    mfe = mae = 0.0
    result = "TIMEOUT"

    for _, row in future.iterrows():
        high, low = _sf(row["high"]), _sf(row["low"])
        if direction == "LONG":
            favorable = (high - entry) / risk
            adverse = (entry - low) / risk
            stop_hit = low <= stop
            one = high >= t1
            two = high >= t2
            three = high >= t3
        else:
            favorable = (entry - low) / risk
            adverse = (high - entry) / risk
            stop_hit = high >= stop
            one = low <= t1
            two = low <= t2
            three = low <= t3
        mfe = max(mfe, favorable)
        mae = max(mae, adverse)

        # Conservative same-candle ordering.
        if stop_hit and not hit1:
            result = "SL_FIRST"
            break
        if one:
            hit1 = True
        if stop_hit and hit1:
            result = "TP1_THEN_STOP_OR_BE"
            break
        if two:
            hit2 = True
        if three:
            result = "TP3"
            break

    if result == "TIMEOUT":
        if hit2:
            result = "TP2"
        elif hit1:
            result = "TP1"

    return {
        **setup,
        "result": result,
        "mfe_r": round(max(0.0, mfe), 4),
        "mae_r": round(max(0.0, mae), 4),
        "hit_1r": bool(hit1),
        "hit_2r": bool(hit2),
        "hit_3r": result == "TP3",
    }


def summarize(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows or [])
    sample = len(rows)
    if sample <= 0:
        return {"sample": 0}
    results = Counter(str(r.get("result") or "UNKNOWN") for r in rows)
    tp3 = results.get("TP3", 0)
    sl = results.get("SL_FIRST", 0)
    positive = sum(1 for r in rows if str(r.get("result")) in {"TP1", "TP2", "TP3", "TP1_THEN_STOP_OR_BE"})
    return {
        "sample": sample,
        "tp3_rate_percent": round(tp3 / sample * 100.0, 2),
        "sl_first_rate_percent": round(sl / sample * 100.0, 2),
        "positive_path_rate_percent": round(positive / sample * 100.0, 2),
        "avg_origin_distance_atr": round(sum(_sf(r.get("move_from_origin_atr")) for r in rows) / sample, 3),
        "avg_origin_distance_percent": round(sum(_sf(r.get("move_from_origin_percent")) for r in rows) / sample, 3),
        "avg_mfe_r": round(sum(_sf(r.get("mfe_r")) for r in rows) / sample, 3),
        "avg_mae_r": round(sum(_sf(r.get("mae_r")) for r in rows) / sample, 3),
        "results": dict(results),
    }


def build_report(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    ordered = sorted(list(rows or []), key=lambda r: int(r.get("detected_at") or 0))
    split = max(1, min(len(ordered), int(len(ordered) * TRAIN_SHARE))) if ordered else 0
    train = ordered[:split]
    holdout = ordered[split:]
    by_phase: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    by_direction: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in ordered:
        by_phase[str(row.get("phase") or "UNKNOWN")].append(row)
        by_direction[str(row.get("direction") or "UNKNOWN")].append(row)
    return {
        "version": VERSION,
        "mode": MODE,
        "generated_at": int(time.time()),
        "all": summarize(ordered),
        "train": summarize(train),
        "holdout": summarize(holdout),
        "by_phase": {key: summarize(value) for key, value in sorted(by_phase.items())},
        "by_direction": {key: summarize(value) for key, value in sorted(by_direction.items())},
        "rows": ordered[-2000:],
    }


def atomic_save(path: str, payload: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, delete=False, prefix=".extreme_start.", suffix=".tmp") as handle:
            tmp = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
