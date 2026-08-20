"""Movement Start V2 Shadow — 5M micro-structure learner.

Amaç: Premium canlı kararını değiştirmeden, hareketin doğuşunu mümkün olduğunca
erken yakalayabilecek profilleri 5M mikro yapı üzerinden öğrenmek.

Araştırma prensipleri:
- Volatilite sıkışması / squeeze -> olası enerji birikimi.
- Likidite sweep / spring -> eski dip/tepe ihlali sonrası hızlı geri alma.
- Internal structure break (BOS/CHoCH benzeri) -> mikro yön değişimi.
- Hacim uyanışı + güçlü kapanış -> sahte kırılımı azaltma.
- Yapı tabanlı stop -> erken giriş / düşük risk / yüksek R kapasitesi ölçümü.
- Repaint koruması: aday özellikleri yalnız KAPANMIŞ mumlardan hesaplanır.

Bu modül:
- Telegram göndermez.
- Emir açmaz.
- Premium sinyal kurallarını değiştirmez.
- PREP -> ARMED -> TRIGGER geçişlerini kaydeder.
- 2R/3R/5R ve stop sırasını 180 dakika boyunca gölgede ölçer.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange

VERSION = "MOVEMENT_START_V2_5M_STRUCTURE_SHADOW_2026_08_20"
STATE_FILE = "movement_start_v2_shadow.json"
MODE = "SHADOW_LEARNING_ONLY_NO_TELEGRAM_NO_ORDERS"

PREP_SCORE = 64
ARMED_SCORE = 76
TRIGGER_SCORE = 88
MIN_DIRECTION_GAP = 6

MAX_TRACK_SECONDS = 180 * 60
DUPLICATE_SECONDS = 45 * 60
KEEP_SECONDS = 14 * 24 * 60 * 60
MAX_RECORDS = 2200

MIN_USEFUL_RISK_PERCENT = 0.25
MAX_USEFUL_RISK_PERCENT = 2.20
STOP_ATR_BUFFER = 0.15

_STATE: Optional[Dict[str, Any]] = None
_DIRTY = False
_STATE_PATH = STATE_FILE


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".movement_start_v2.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
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


def _default_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "updated_at": 0,
        "records": [],
        "open": {},
        "last_started": {},
        "summary": {},
    }


def _load(path: str) -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return _default_state()
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return _default_state()
    except Exception:
        return _default_state()

    data.setdefault("records", [])
    data.setdefault("open", {})
    data.setdefault("last_started", {})
    data.setdefault("summary", {})
    data["version"] = VERSION
    data["mode"] = MODE
    return data


def begin(path: str = STATE_FILE) -> None:
    global _STATE, _DIRTY, _STATE_PATH
    _STATE_PATH = path
    _STATE = _load(path)
    _DIRTY = False


def _state() -> Dict[str, Any]:
    global _STATE
    if _STATE is None:
        begin()
    return _STATE if isinstance(_STATE, dict) else _default_state()


def _clean(df: Any, min_len: int) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy"):
        return None
    frame = df.copy()
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(frame.columns)):
        return None
    for col in required:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    return frame if len(frame) >= min_len else None


def _enrich(df: Any, min_len: int = 55) -> Optional[pd.DataFrame]:
    frame = _clean(df, min_len)
    if frame is None:
        return None
    frame["ema20"] = EMAIndicator(frame["close"], window=20).ema_indicator()
    frame["ema50"] = EMAIndicator(frame["close"], window=50).ema_indicator()
    frame["rsi"] = RSIIndicator(frame["close"], window=14).rsi()
    frame["atr"] = AverageTrueRange(
        frame["high"], frame["low"], frame["close"], window=14
    ).average_true_range()
    frame["volume_avg"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_avg"]
    frame["ema20_slope"] = frame["ema20"] - frame["ema20"].shift(3)

    mid = frame["close"].rolling(20).mean()
    std = frame["close"].rolling(20).std(ddof=0)
    frame["bb_upper"] = mid + 2.0 * std
    frame["bb_lower"] = mid - 2.0 * std
    frame["kc_upper"] = frame["ema20"] + 1.5 * frame["atr"]
    frame["kc_lower"] = frame["ema20"] - 1.5 * frame["atr"]
    frame["squeeze"] = (
        (frame["bb_upper"] <= frame["kc_upper"])
        & (frame["bb_lower"] >= frame["kc_lower"])
    )
    frame["atr_pct"] = frame["atr"] / frame["close"].replace(0, float("nan")) * 100.0
    return frame.dropna().reset_index(drop=True)


def _close_power(row: pd.Series) -> float:
    high = _sf(row.get("high"))
    low = _sf(row.get("low"))
    close = _sf(row.get("close"))
    span = high - low
    if span <= 0:
        return 50.0
    return max(0.0, min(100.0, (close - low) / span * 100.0))


def _lower_wick(row: pd.Series) -> float:
    high = _sf(row.get("high"))
    low = _sf(row.get("low"))
    open_ = _sf(row.get("open"))
    close = _sf(row.get("close"))
    span = high - low
    if span <= 0:
        return 0.0
    return max(0.0, (min(open_, close) - low) / span * 100.0)


def _upper_wick(row: pd.Series) -> float:
    high = _sf(row.get("high"))
    low = _sf(row.get("low"))
    open_ = _sf(row.get("open"))
    close = _sf(row.get("close"))
    span = high - low
    if span <= 0:
        return 0.0
    return max(0.0, (high - max(open_, close)) / span * 100.0)


def _not_opposing(frame: Optional[pd.DataFrame], direction: str) -> bool:
    if frame is None or len(frame) < 6:
        return True
    # -1 açık mum olabilir. Yalnız kapanmış -2 kullanılır.
    row = frame.iloc[-2]
    close = _sf(row["close"])
    ema20 = _sf(row["ema20"])
    ema50 = _sf(row["ema50"])
    slope = _sf(row["ema20_slope"])
    rsi = _sf(row["rsi"], 50.0)
    if direction == "LONG":
        return not (close < ema20 < ema50 and slope < 0 and rsi < 40)
    return not (close > ema20 > ema50 and slope > 0 and rsi > 60)


def extract_features(
    df5m: Any,
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any = None,
) -> Optional[Dict[str, Any]]:
    """Yalnız kapanmış mumlardan 5M başlangıç özelliklerini çıkarır."""
    f5 = _enrich(df5m, 60)
    f15 = _enrich(df15m, 55)
    f1 = _enrich(df1h, 55)
    f4 = _enrich(df4h, 55)
    if f5 is None or f15 is None:
        return None

    # Son satır açık mum olabilir; bütün karar özellikleri -2 ve öncesinden gelir.
    closed = f5.iloc[:-1].copy()
    if len(closed) < 35:
        return None
    row = closed.iloc[-1]
    prev = closed.iloc[-2]
    recent = closed.iloc[-8:]
    older = closed.iloc[-28:-8]
    prior_break = closed.iloc[-10:-1]
    sweep_base = closed.iloc[-18:-4]
    last3 = closed.iloc[-3:]
    prior6 = closed.iloc[-9:-3]
    if min(len(recent), len(older), len(prior_break), len(sweep_base), len(prior6)) <= 0:
        return None

    close = _sf(row["close"])
    if close <= 0:
        return None
    observed_price = _sf(current_price, close)

    recent_atr = _sf(recent["atr_pct"].mean(), 1.0)
    older_atr = _sf(older["atr_pct"].mean(), 1.0)
    atr_compression = recent_atr / older_atr if older_atr > 0 else 1.0

    recent_range = _sf(recent["high"].max() - recent["low"].min())
    older_range = _sf(older["high"].max() - older["low"].min())
    range_compression = recent_range / older_range if older_range > 0 else 1.0
    squeeze_count = int(recent["squeeze"].sum())
    squeeze_recent = squeeze_count >= 3
    squeeze_release = bool(recent.iloc[-2]["squeeze"] and not bool(row["squeeze"]))

    volume_recent = _sf(closed.iloc[-4:-1]["volume"].mean())
    volume_before = _sf(closed.iloc[-16:-4]["volume"].mean())
    volume_wake = volume_recent / volume_before if volume_before > 0 else 1.0
    volume_ratio = _sf(row["volume_ratio"], 1.0)

    last3_low = _sf(last3["low"].min())
    prior6_low = _sf(prior6["low"].min())
    last3_high = _sf(last3["high"].max())
    prior6_high = _sf(prior6["high"].max())
    higher_low = last3_low >= prior6_low * 0.998
    lower_high = last3_high <= prior6_high * 1.002

    support_hold = bool(higher_low and (last3["low"].max() - last3["low"].min()) / close * 100 <= 1.20)
    resistance_hold = bool(lower_high and (last3["high"].max() - last3["high"].min()) / close * 100 <= 1.20)

    old_low = _sf(sweep_base["low"].min())
    old_high = _sf(sweep_base["high"].max())
    sweep_long_rows = last3[
        (last3["low"] < old_low * 0.999)
        & (last3["close"] > old_low)
    ]
    sweep_short_rows = last3[
        (last3["high"] > old_high * 1.001)
        & (last3["close"] < old_high)
    ]
    liquidity_sweep_long = any(_lower_wick(r) >= 22 for _, r in sweep_long_rows.iterrows())
    liquidity_sweep_short = any(_upper_wick(r) >= 22 for _, r in sweep_short_rows.iterrows())
    sweep_low = _sf(sweep_long_rows["low"].min(), 0.0) if not sweep_long_rows.empty else 0.0
    sweep_high = _sf(sweep_short_rows["high"].max(), 0.0) if not sweep_short_rows.empty else 0.0

    local_high = _sf(prior_break["high"].max())
    local_low = _sf(prior_break["low"].min())
    close_power = _close_power(row)
    internal_break_long = bool(close > local_high and close_power >= 58 and volume_ratio >= 1.10)
    internal_break_short = bool(close < local_low and close_power <= 42 and volume_ratio >= 1.10)

    ema20 = _sf(row["ema20"])
    ema_slope = _sf(row["ema20_slope"])
    prev_slope = _sf(prev["ema20_slope"])
    ema_turn = ema_slope - prev_slope
    rsi = _sf(row["rsi"], 50.0)
    rsi_prev = _sf(closed.iloc[-4]["rsi"], 50.0)
    rsi_slope = rsi - rsi_prev

    r15 = f15.iloc[-2]
    c15 = _sf(r15["close"])
    e15 = _sf(r15["ema20"])
    s15 = _sf(r15["ema20_slope"])
    rsi15 = _sf(r15["rsi"], 50.0)
    fifteen_long_ok = not (c15 < e15 and s15 < 0 and rsi15 < 42)
    fifteen_short_ok = not (c15 > e15 and s15 > 0 and rsi15 > 58)

    atr5 = _sf(row["atr"])
    structure_low = min(_sf(closed.iloc[-7:]["low"].min()), sweep_low or float("inf"))
    if not math.isfinite(structure_low):
        structure_low = _sf(closed.iloc[-7:]["low"].min())
    structure_high = max(_sf(closed.iloc[-7:]["high"].max()), sweep_high)

    long_stop = structure_low - STOP_ATR_BUFFER * atr5 if atr5 > 0 else structure_low
    short_stop = structure_high + STOP_ATR_BUFFER * atr5 if atr5 > 0 else structure_high
    long_risk_pct = (close - long_stop) / close * 100 if 0 < long_stop < close else 999.0
    short_risk_pct = (short_stop - close) / close * 100 if short_stop > close else 999.0

    swing15 = f15.iloc[-22:-2]
    swing15_high = _sf(swing15["high"].max(), close)
    swing15_low = _sf(swing15["low"].min(), close)
    long_risk_abs = max(0.0, close - long_stop)
    short_risk_abs = max(0.0, short_stop - close)
    room_long_r = (swing15_high - close) / long_risk_abs if long_risk_abs > 0 and swing15_high > close else 0.0
    room_short_r = (close - swing15_low) / short_risk_abs if short_risk_abs > 0 and swing15_low < close else 0.0

    return {
        "signal_price": close,
        "observed_market_price": observed_price,
        "atr5": atr5,
        "atr_compression": round(atr_compression, 4),
        "range_compression": round(range_compression, 4),
        "squeeze_count": squeeze_count,
        "squeeze_recent": squeeze_recent,
        "squeeze_release": squeeze_release,
        "volume_wake": round(volume_wake, 4),
        "volume_ratio": round(volume_ratio, 4),
        "higher_low": bool(higher_low),
        "lower_high": bool(lower_high),
        "support_hold": bool(support_hold),
        "resistance_hold": bool(resistance_hold),
        "liquidity_sweep_long": bool(liquidity_sweep_long),
        "liquidity_sweep_short": bool(liquidity_sweep_short),
        "sweep_low": sweep_low,
        "sweep_high": sweep_high,
        "internal_break_long": bool(internal_break_long),
        "internal_break_short": bool(internal_break_short),
        "local_high": local_high,
        "local_low": local_low,
        "close_power": round(close_power, 2),
        "ema20": ema20,
        "ema20_slope": ema_slope,
        "ema_turn": ema_turn,
        "rsi5": round(rsi, 3),
        "rsi_slope": round(rsi_slope, 3),
        "fifteen_long_ok": bool(fifteen_long_ok),
        "fifteen_short_ok": bool(fifteen_short_ok),
        "one_hour_long_ok": _not_opposing(f1, "LONG"),
        "one_hour_short_ok": _not_opposing(f1, "SHORT"),
        "four_hour_long_ok": _not_opposing(f4, "LONG"),
        "four_hour_short_ok": _not_opposing(f4, "SHORT"),
        "long_stop": long_stop,
        "short_stop": short_stop,
        "long_risk_percent": round(long_risk_pct, 4),
        "short_risk_percent": round(short_risk_pct, 4),
        "room_long_r": round(room_long_r, 3),
        "room_short_r": round(room_short_r, 3),
    }


def score_direction(features: Dict[str, Any], direction: str) -> Tuple[int, Dict[str, bool]]:
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return 0, {}
    long = direction == "LONG"

    conditions = {
        "atr_compression": _sf(features.get("atr_compression"), 1.0) <= 0.88,
        "range_compression": _sf(features.get("range_compression"), 1.0) <= 0.78,
        "squeeze": bool(features.get("squeeze_recent")),
        "squeeze_release": bool(features.get("squeeze_release")),
        "structure_hold": bool(features.get("support_hold" if long else "resistance_hold")),
        "micro_structure": bool(features.get("higher_low" if long else "lower_high")),
        "liquidity_sweep": bool(features.get("liquidity_sweep_long" if long else "liquidity_sweep_short")),
        "internal_break": bool(features.get("internal_break_long" if long else "internal_break_short")),
        "volume_wake": _sf(features.get("volume_wake"), 1.0) >= 1.08,
        "volume_confirm": _sf(features.get("volume_ratio"), 1.0) >= 1.20,
        "ema_turn": (
            _sf(features.get("ema20_slope")) >= 0 or _sf(features.get("ema_turn")) > 0
            if long
            else _sf(features.get("ema20_slope")) <= 0 or _sf(features.get("ema_turn")) < 0
        ),
        "rsi_turn": (
            43 <= _sf(features.get("rsi5"), 50.0) <= 69 and _sf(features.get("rsi_slope")) > 0
            if long
            else 31 <= _sf(features.get("rsi5"), 50.0) <= 57 and _sf(features.get("rsi_slope")) < 0
        ),
        "close_power": (
            _sf(features.get("close_power"), 50.0) >= 58
            if long
            else _sf(features.get("close_power"), 50.0) <= 42
        ),
        "fifteen_not_opposing": bool(features.get("fifteen_long_ok" if long else "fifteen_short_ok")),
        "one_hour_not_opposing": bool(features.get("one_hour_long_ok" if long else "one_hour_short_ok")),
        "four_hour_not_opposing": bool(features.get("four_hour_long_ok" if long else "four_hour_short_ok")),
    }
    risk_pct = _sf(features.get("long_risk_percent" if long else "short_risk_percent"), 999.0)
    conditions["risk_quality"] = MIN_USEFUL_RISK_PERCENT <= risk_pct <= MAX_USEFUL_RISK_PERCENT
    room_r = _sf(features.get("room_long_r" if long else "room_short_r"), 0.0)
    conditions["structure_room"] = room_r >= 1.0

    weights = {
        "atr_compression": 6,
        "range_compression": 6,
        "squeeze": 8,
        "squeeze_release": 5,
        "structure_hold": 10,
        "micro_structure": 6,
        "liquidity_sweep": 10,
        "internal_break": 16,
        "volume_wake": 7,
        "volume_confirm": 8,
        "ema_turn": 6,
        "rsi_turn": 5,
        "close_power": 5,
        "fifteen_not_opposing": 6,
        "one_hour_not_opposing": 7,
        "four_hour_not_opposing": 3,
        "risk_quality": 6,
        "structure_room": 3,
    }
    score = sum(weight for name, weight in weights.items() if conditions.get(name))

    # 1H/4H burada teyit değil veto mantığıdır. Güçlü ters yapı erken adayı
    # öldürmek yerine ağır puan cezası alır; gölgede bu örnekleri de öğreniriz.
    if not conditions["one_hour_not_opposing"]:
        score -= 18
    if not conditions["four_hour_not_opposing"]:
        score -= 8
    return max(0, min(100, int(round(score)))), conditions


def _stage(score: int, conditions: Dict[str, bool]) -> str:
    if (
        score >= TRIGGER_SCORE
        and conditions.get("internal_break")
        and conditions.get("volume_confirm")
        and (conditions.get("liquidity_sweep") or conditions.get("squeeze_release") or conditions.get("structure_hold"))
    ):
        return "TRIGGER"
    if score >= ARMED_SCORE:
        return "ARMED"
    if score >= PREP_SCORE:
        return "PREP"
    return "NONE"


def analyze(
    symbol: str,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any = None,
) -> Optional[Dict[str, Any]]:
    features = extract_features(df5m, df15m, df1h, df4h, current_price)
    if not features:
        return None
    long_score, long_conditions = score_direction(features, "LONG")
    short_score, short_conditions = score_direction(features, "SHORT")
    if long_score >= short_score:
        direction, score, other, conditions = "LONG", long_score, short_score, long_conditions
    else:
        direction, score, other, conditions = "SHORT", short_score, long_score, short_conditions
    stage = _stage(score, conditions)
    if stage == "NONE" or score - other < MIN_DIRECTION_GAP:
        return None

    entry = _sf(features.get("signal_price"))
    stop = _sf(features.get("long_stop" if direction == "LONG" else "short_stop"))
    risk_abs = entry - stop if direction == "LONG" else stop - entry
    if entry <= 0 or stop <= 0 or risk_abs <= 0:
        return None

    return {
        "symbol": str(symbol or "").upper(),
        "direction": direction,
        "stage": stage,
        "score": score,
        "opposite_score": other,
        "entry": entry,
        "stop": stop,
        "risk_abs": risk_abs,
        "risk_percent": round(risk_abs / entry * 100.0, 4),
        "target_2r": entry + (2 * risk_abs if direction == "LONG" else -2 * risk_abs),
        "target_3r": entry + (3 * risk_abs if direction == "LONG" else -3 * risk_abs),
        "target_5r": entry + (5 * risk_abs if direction == "LONG" else -5 * risk_abs),
        "features": features,
        "conditions": conditions,
        "version": VERSION,
    }


def _rank(stage: str) -> int:
    return {"PREP": 1, "ARMED": 2, "TRIGGER": 3}.get(str(stage), 0)


def _bar_extremes(df5m: Any, fallback: float) -> Tuple[float, float]:
    f5 = _clean(df5m, 3)
    if f5 is None:
        return fallback, fallback
    row = f5.iloc[-2]
    return _sf(row.get("high"), fallback), _sf(row.get("low"), fallback)


def _update_record(record: Dict[str, Any], price: float, high: float, low: float, now: int) -> None:
    direction = record.get("direction")
    entry = _sf(record.get("entry"))
    stop = _sf(record.get("stop"))
    risk = _sf(record.get("risk_abs"))
    if entry <= 0 or risk <= 0:
        return

    if direction == "LONG":
        favorable = max(high, price) - entry
        adverse = entry - min(low, price)
        hit_stop = min(low, price) <= stop
        hit2 = max(high, price) >= _sf(record.get("target_2r"))
        hit3 = max(high, price) >= _sf(record.get("target_3r"))
        hit5 = max(high, price) >= _sf(record.get("target_5r"))
    else:
        favorable = entry - min(low, price)
        adverse = max(high, price) - entry
        hit_stop = max(high, price) >= stop
        hit2 = min(low, price) <= _sf(record.get("target_2r"))
        hit3 = min(low, price) <= _sf(record.get("target_3r"))
        hit5 = min(low, price) <= _sf(record.get("target_5r"))

    record["max_favorable_r"] = round(max(_sf(record.get("max_favorable_r")), favorable / risk), 4)
    record["max_adverse_r"] = round(max(_sf(record.get("max_adverse_r")), adverse / risk), 4)
    record["max_favorable_percent"] = round(record["max_favorable_r"] * record["risk_percent"], 4)
    record["max_adverse_percent"] = round(record["max_adverse_r"] * record["risk_percent"], 4)
    record["last_price"] = price
    record["last_updated_at"] = now

    for level, hit in ((2, hit2), (3, hit3), (5, hit5)):
        key = f"hit_{level}r_at"
        if hit and not record.get(key):
            record[key] = now

    if hit_stop and not record.get("stop_hit_at"):
        record["stop_hit_at"] = now

    if not record.get("first_resolution"):
        if hit_stop and hit2:
            record["first_resolution"] = "AMBIGUOUS_SAME_5M_BAR"
            record["first_resolution_at"] = now
        elif hit2:
            record["first_resolution"] = "R2_FIRST"
            record["first_resolution_at"] = now
        elif hit_stop:
            record["first_resolution"] = "STOP_FIRST"
            record["first_resolution_at"] = now


def _finish_due(now: int) -> None:
    global _DIRTY
    state = _state()
    for key, record in list(state.get("open", {}).items()):
        if now - int(record.get("started_at") or 0) < MAX_TRACK_SECONDS:
            continue
        record["closed_at"] = now
        record["status"] = record.get("first_resolution") or "TIMEOUT"
        if record.get("hit_5r_at"):
            record["highest_r_hit"] = 5
        elif record.get("hit_3r_at"):
            record["highest_r_hit"] = 3
        elif record.get("hit_2r_at"):
            record["highest_r_hit"] = 2
        else:
            record["highest_r_hit"] = 0
        state.setdefault("records", []).append(record)
        state["open"].pop(key, None)
        _DIRTY = True


def _update_existing(symbol: str, df5m: Any, price: float, now: int) -> None:
    global _DIRTY
    state = _state()
    high, low = _bar_extremes(df5m, price)
    for record in state.get("open", {}).values():
        if str(record.get("symbol")) != symbol:
            continue
        _update_record(record, price, high, low, now)
        _DIRTY = True


def observe(
    symbol: str,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any = None,
    *,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    global _DIRTY
    now = int(now_ts if now_ts is not None else time.time())
    symbol = str(symbol or "").upper()
    price = _sf(current_price)
    if price <= 0:
        f5 = _clean(df5m, 3)
        price = _sf(f5.iloc[-1]["close"]) if f5 is not None else 0.0
    if not symbol or price <= 0:
        return None

    _update_existing(symbol, df5m, price, now)
    _finish_due(now)

    result = analyze(symbol, df5m, df15m, df1h, df4h, current_price)
    if not result:
        return None

    state = _state()
    direction = result["direction"]
    key = f"{symbol}_{direction}"
    active = state.get("open", {}).get(key)
    if isinstance(active, dict):
        if result["score"] > int(active.get("best_score") or 0):
            active["best_score"] = result["score"]
        if _rank(result["stage"]) > _rank(active.get("best_stage")):
            active["best_stage"] = result["stage"]
            active.setdefault("stage_path", []).append({
                "stage": result["stage"],
                "at": now,
                "score": result["score"],
                "price": result["entry"],
                "minutes_from_start": round((now - int(active.get("started_at") or now)) / 60.0, 2),
            })
            _DIRTY = True
            return {"event": "UPGRADE", "record": active, "result": result}
        return None

    last_started = int(state.get("last_started", {}).get(key) or 0)
    if now - last_started < DUPLICATE_SECONDS:
        return None

    record = {
        "id": f"{key}_{now}",
        "symbol": symbol,
        "direction": direction,
        "initial_stage": result["stage"],
        "best_stage": result["stage"],
        "initial_score": result["score"],
        "best_score": result["score"],
        "opposite_score": result["opposite_score"],
        "started_at": now,
        "entry": result["entry"],
        "stop": result["stop"],
        "risk_abs": result["risk_abs"],
        "risk_percent": result["risk_percent"],
        "target_2r": result["target_2r"],
        "target_3r": result["target_3r"],
        "target_5r": result["target_5r"],
        "features": result["features"],
        "conditions": result["conditions"],
        "stage_path": [{
            "stage": result["stage"],
            "at": now,
            "score": result["score"],
            "price": result["entry"],
            "minutes_from_start": 0.0,
        }],
        "max_favorable_r": 0.0,
        "max_adverse_r": 0.0,
        "max_favorable_percent": 0.0,
        "max_adverse_percent": 0.0,
        "hit_2r_at": 0,
        "hit_3r_at": 0,
        "hit_5r_at": 0,
        "stop_hit_at": 0,
        "first_resolution": None,
        "first_resolution_at": 0,
        "status": "OPEN_SHADOW",
        "version": VERSION,
    }
    state.setdefault("open", {})[key] = record
    state.setdefault("last_started", {})[key] = now
    _DIRTY = True
    return {"event": "NEW", "record": record, "result": result}


def _summary(records: list, open_count: int) -> Dict[str, Any]:
    by_stage: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
        "sample": 0,
        "r2_first": 0,
        "stop_first": 0,
        "ambiguous": 0,
        "hit_3r": 0,
        "hit_5r": 0,
        "mfe_r_sum": 0.0,
        "mae_r_sum": 0.0,
    })
    outcomes = Counter()
    for record in records:
        stage = str(record.get("initial_stage") or "UNKNOWN")
        row = by_stage[stage]
        row["sample"] += 1
        status = str(record.get("first_resolution") or "TIMEOUT")
        outcomes[status] += 1
        if status == "R2_FIRST":
            row["r2_first"] += 1
        elif status == "STOP_FIRST":
            row["stop_first"] += 1
        elif status == "AMBIGUOUS_SAME_5M_BAR":
            row["ambiguous"] += 1
        if record.get("hit_3r_at"):
            row["hit_3r"] += 1
        if record.get("hit_5r_at"):
            row["hit_5r"] += 1
        row["mfe_r_sum"] += _sf(record.get("max_favorable_r"))
        row["mae_r_sum"] += _sf(record.get("max_adverse_r"))

    clean_stage = {}
    for stage, row in by_stage.items():
        sample = row["sample"] or 1
        clean_stage[stage] = {
            "sample": row["sample"],
            "r2_first_rate_percent": round(row["r2_first"] / sample * 100.0, 2),
            "stop_first_rate_percent": round(row["stop_first"] / sample * 100.0, 2),
            "ambiguous_rate_percent": round(row["ambiguous"] / sample * 100.0, 2),
            "hit_3r_rate_percent": round(row["hit_3r"] / sample * 100.0, 2),
            "hit_5r_rate_percent": round(row["hit_5r"] / sample * 100.0, 2),
            "avg_mfe_r": round(row["mfe_r_sum"] / sample, 3),
            "avg_mae_r": round(row["mae_r_sum"] / sample, 3),
        }
    return {
        "version": VERSION,
        "closed": len(records),
        "open": open_count,
        "outcomes": dict(outcomes),
        "by_initial_stage": clean_stage,
    }


def finish(path: Optional[str] = None) -> Dict[str, Any]:
    global _DIRTY
    now = int(time.time())
    _finish_due(now)
    state = _state()
    cutoff = now - KEEP_SECONDS
    records = [
        row for row in state.get("records", [])
        if int(row.get("started_at") or 0) >= cutoff
    ][-MAX_RECORDS:]
    state["records"] = records
    state["summary"] = _summary(records, len(state.get("open", {})))
    state["updated_at"] = now
    target = path or _STATE_PATH
    _atomic_save(target, state)
    _DIRTY = False
    return state["summary"]
