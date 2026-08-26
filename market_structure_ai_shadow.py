"""Market Structure AI V1 — pivot/structure reversal learner (shadow only).

Amaç:
- İnsanların grafikte yaptığı swing dip/tepe ve çizgi okumasını makineye taşımak.
- Sadece kapanmış 5M mumlarla pivot, destek/direnç, trend çizgisi, sweep/reclaim,
  double bottom/top, CHOCH/BOS ve origin uzaklığını ölçmek.
- Mevcut Premium canlı kurallarını değiştirmeden WATCH/READY adaylarını gölgede
  takip etmek ve önce 2R mi stop mu geldiğini öğrenmek.

Bu modül Telegram göndermez ve emir açmaz.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange

VERSION = "MARKET_STRUCTURE_AI_SHADOW_V1_2026_08_26"
STATE_FILE = "market_structure_ai_shadow.json"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_RULE_MUTATION"

WATCH_SCORE = 54
READY_SCORE = 72
MIN_DIRECTION_GAP = 8
PIVOT_LEFT = 2
PIVOT_RIGHT = 1
MAX_READY_ORIGIN_DISTANCE_ATR = 2.80
MAX_TRACK_SECONDS = 180 * 60
DUPLICATE_SECONDS = 30 * 60
KEEP_SECONDS = 14 * 24 * 60 * 60
MAX_RECORDS = 3000
STOP_ATR_BUFFER = 0.20

_STATE: Optional[Dict[str, Any]] = None
_STATE_PATH = STATE_FILE
_DIRTY = False


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
            prefix=".market_structure_ai.",
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
    global _STATE, _STATE_PATH, _DIRTY
    _STATE_PATH = path
    _STATE = _load(path)
    _DIRTY = False


def _state() -> Dict[str, Any]:
    global _STATE
    if _STATE is None:
        begin()
    return _STATE if isinstance(_STATE, dict) else _default_state()


def _clean(df: Any, min_len: int = 55) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy"):
        return None
    frame = df.copy()
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return None
    for col in required:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    return frame if len(frame) >= min_len else None


def _enrich(df: Any, min_len: int = 55) -> Optional[pd.DataFrame]:
    frame = _clean(df, min_len)
    if frame is None:
        return None
    frame["atr"] = AverageTrueRange(
        frame["high"], frame["low"], frame["close"], window=14
    ).average_true_range()
    frame["ema20"] = EMAIndicator(frame["close"], window=20).ema_indicator()
    frame["ema20_slope"] = frame["ema20"] - frame["ema20"].shift(3)
    frame["volume_avg"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_avg"].replace(0, float("nan"))
    return frame.dropna().reset_index(drop=True)


def _close_power(row: pd.Series) -> float:
    high, low, close = _sf(row.get("high")), _sf(row.get("low")), _sf(row.get("close"))
    span = high - low
    if span <= 0:
        return 50.0
    return max(0.0, min(100.0, (close - low) / span * 100.0))


def _find_pivots(frame: pd.DataFrame) -> Tuple[List[Dict[str, float]], List[Dict[str, float]]]:
    lows: List[Dict[str, float]] = []
    highs: List[Dict[str, float]] = []
    if len(frame) < PIVOT_LEFT + PIVOT_RIGHT + 3:
        return lows, highs
    for i in range(PIVOT_LEFT, len(frame) - PIVOT_RIGHT):
        lo = _sf(frame.iloc[i]["low"])
        hi = _sf(frame.iloc[i]["high"])
        left_lows = [_sf(frame.iloc[j]["low"]) for j in range(i - PIVOT_LEFT, i)]
        right_lows = [_sf(frame.iloc[j]["low"]) for j in range(i + 1, i + 1 + PIVOT_RIGHT)]
        left_highs = [_sf(frame.iloc[j]["high"]) for j in range(i - PIVOT_LEFT, i)]
        right_highs = [_sf(frame.iloc[j]["high"]) for j in range(i + 1, i + 1 + PIVOT_RIGHT)]
        if lo <= min(left_lows + right_lows) and lo < max(left_lows + right_lows):
            lows.append({"i": float(i), "price": lo})
        if hi >= max(left_highs + right_highs) and hi > min(left_highs + right_highs):
            highs.append({"i": float(i), "price": hi})
    return lows, highs


def _project_line(points: List[Dict[str, float]], current_i: int, falling: bool) -> Optional[float]:
    if len(points) < 2:
        return None
    a, b = points[-2], points[-1]
    x1, y1, x2, y2 = int(a["i"]), _sf(a["price"]), int(b["i"]), _sf(b["price"])
    if x2 <= x1:
        return None
    if falling and y2 >= y1:
        return None
    if not falling and y2 <= y1:
        return None
    slope = (y2 - y1) / (x2 - x1)
    return y2 + slope * (current_i - x2)


def _cluster_level(points: List[Dict[str, float]], atr: float, side: str) -> Tuple[float, int]:
    if not points:
        return 0.0, 0
    recent = points[-6:]
    anchor = _sf(recent[-1]["price"])
    tolerance = max(atr * 0.55, abs(anchor) * 0.0025)
    cluster = [p for p in recent if abs(_sf(p["price"]) - anchor) <= tolerance]
    if not cluster:
        return anchor, 1
    values = [_sf(p["price"]) for p in cluster]
    level = min(values) if side == "support" else max(values)
    return level, len(values)


def _fifteen_not_opposing(df15m: Any, direction: str) -> bool:
    f15 = _enrich(df15m, 35)
    if f15 is None or len(f15) < 4:
        return True
    row = f15.iloc[-2]
    close, ema, slope = _sf(row["close"]), _sf(row["ema20"]), _sf(row["ema20_slope"])
    if direction == "LONG":
        return not (close < ema and slope < 0)
    return not (close > ema and slope > 0)


def extract_features(df5m: Any, df15m: Any = None, current_price: Any = None) -> Optional[Dict[str, Any]]:
    f5 = _enrich(df5m, 60)
    if f5 is None or len(f5) < 45:
        return None

    # Son satır açık mum olabilir. Repaint riskini azaltmak için -2 ve öncesi kullanılır.
    closed = f5.iloc[:-1].copy().reset_index(drop=True)
    if len(closed) < 40:
        return None
    row = closed.iloc[-1]
    prev = closed.iloc[-2]
    close = _sf(row["close"])
    atr = _sf(row["atr"])
    if close <= 0 or atr <= 0:
        return None

    lows, highs = _find_pivots(closed)
    if len(lows) < 2 or len(highs) < 2:
        return None

    last_low, prev_low = lows[-1], lows[-2]
    last_high, prev_high = highs[-1], highs[-2]
    higher_low = _sf(last_low["price"]) > _sf(prev_low["price"]) + 0.05 * atr
    lower_low = _sf(last_low["price"]) < _sf(prev_low["price"]) - 0.05 * atr
    higher_high = _sf(last_high["price"]) > _sf(prev_high["price"]) + 0.05 * atr
    lower_high = _sf(last_high["price"]) < _sf(prev_high["price"]) - 0.05 * atr
    prior_downtrend = bool(lower_low and lower_high)
    prior_uptrend = bool(higher_low and higher_high)

    current_i = len(closed) - 1
    falling_line = _project_line(highs, current_i, falling=True)
    rising_line = _project_line(lows, current_i, falling=False)
    trendline_break_long = falling_line is not None and close > falling_line + 0.05 * atr
    trendline_break_short = rising_line is not None and close < rising_line - 0.05 * atr

    support, support_touches = _cluster_level(lows, atr, "support")
    resistance, resistance_touches = _cluster_level(highs, atr, "resistance")

    recent = closed.iloc[-14:]
    recent3 = closed.iloc[-3:]
    origin_long_i = int(recent["low"].idxmin())
    origin_short_i = int(recent["high"].idxmax())
    origin_long = _sf(closed.loc[origin_long_i, "low"])
    origin_short = _sf(closed.loc[origin_short_i, "high"])
    origin_long_bars = current_i - origin_long_i
    origin_short_bars = current_i - origin_short_i

    support_ref = support if support > 0 else _sf(prev_low["price"])
    resistance_ref = resistance if resistance > 0 else _sf(prev_high["price"])
    sweep_long = bool(
        recent3["low"].min() < support_ref - 0.08 * atr
        and close > support_ref
    )
    sweep_short = bool(
        recent3["high"].max() > resistance_ref + 0.08 * atr
        and close < resistance_ref
    )

    double_bottom = bool(
        abs(_sf(last_low["price"]) - _sf(prev_low["price"])) <= 0.60 * atr
        and int(last_low["i"] - prev_low["i"]) >= 3
    )
    double_top = bool(
        abs(_sf(last_high["price"]) - _sf(prev_high["price"])) <= 0.60 * atr
        and int(last_high["i"] - prev_high["i"]) >= 3
    )

    last_swing_high = _sf(last_high["price"])
    last_swing_low = _sf(last_low["price"])
    choch_long = bool(close > last_swing_high + 0.05 * atr and (prior_downtrend or lower_high))
    choch_short = bool(close < last_swing_low - 0.05 * atr and (prior_uptrend or higher_low))
    bos_long = bool(close > last_swing_high + 0.05 * atr)
    bos_short = bool(close < last_swing_low - 0.05 * atr)

    volume_ratio = _sf(row.get("volume_ratio"), 1.0)
    volume_wake = volume_ratio >= 1.20
    true_range = _sf(row["high"] - row["low"])
    impulse_ratio = true_range / atr if atr > 0 else 0.0
    close_power = _close_power(row)
    impulse_long = impulse_ratio >= 1.10 and close_power >= 62
    impulse_short = impulse_ratio >= 1.10 and close_power <= 38

    ema_slope = _sf(row["ema20_slope"])
    prev_ema_slope = _sf(prev["ema20_slope"])
    ema_turn_long = ema_slope > prev_ema_slope and (ema_slope >= 0 or close > _sf(row["ema20"]))
    ema_turn_short = ema_slope < prev_ema_slope and (ema_slope <= 0 or close < _sf(row["ema20"]))

    long_origin_distance_atr = (close - origin_long) / atr if origin_long > 0 else 999.0
    short_origin_distance_atr = (origin_short - close) / atr if origin_short > close else 999.0
    long_origin_distance_pct = (close - origin_long) / origin_long * 100 if origin_long > 0 else 999.0
    short_origin_distance_pct = (origin_short - close) / origin_short * 100 if origin_short > 0 else 999.0

    observed_price = _sf(current_price, close)
    return {
        "signal_price": close,
        "observed_market_price": observed_price,
        "atr5": atr,
        "volume_ratio": round(volume_ratio, 4),
        "close_power": round(close_power, 2),
        "impulse_ratio": round(impulse_ratio, 4),
        "ema20_slope": ema_slope,
        "prior_downtrend": prior_downtrend,
        "prior_uptrend": prior_uptrend,
        "higher_low": higher_low,
        "lower_low": lower_low,
        "higher_high": higher_high,
        "lower_high": lower_high,
        "support": support_ref,
        "resistance": resistance_ref,
        "support_touches": support_touches,
        "resistance_touches": resistance_touches,
        "sweep_long": sweep_long,
        "sweep_short": sweep_short,
        "double_bottom": double_bottom,
        "double_top": double_top,
        "trendline_break_long": bool(trendline_break_long),
        "trendline_break_short": bool(trendline_break_short),
        "falling_trendline": falling_line,
        "rising_trendline": rising_line,
        "choch_long": choch_long,
        "choch_short": choch_short,
        "bos_long": bos_long,
        "bos_short": bos_short,
        "volume_wake": volume_wake,
        "impulse_long": impulse_long,
        "impulse_short": impulse_short,
        "ema_turn_long": bool(ema_turn_long),
        "ema_turn_short": bool(ema_turn_short),
        "fifteen_long_ok": _fifteen_not_opposing(df15m, "LONG"),
        "fifteen_short_ok": _fifteen_not_opposing(df15m, "SHORT"),
        "origin_long": origin_long,
        "origin_short": origin_short,
        "origin_long_bars_ago": origin_long_bars,
        "origin_short_bars_ago": origin_short_bars,
        "origin_long_distance_atr": round(long_origin_distance_atr, 4),
        "origin_short_distance_atr": round(short_origin_distance_atr, 4),
        "origin_long_distance_percent": round(long_origin_distance_pct, 4),
        "origin_short_distance_percent": round(short_origin_distance_pct, 4),
        "last_swing_high": last_swing_high,
        "last_swing_low": last_swing_low,
    }


def score_direction(features: Dict[str, Any], direction: str) -> Tuple[int, Dict[str, bool]]:
    long = str(direction).upper() == "LONG"
    conditions = {
        "prior_trend": bool(features.get("prior_downtrend" if long else "prior_uptrend")),
        "structure_shift": bool(features.get("higher_low" if long else "lower_high")),
        "zone_touch": int(features.get("support_touches" if long else "resistance_touches") or 0) >= 2,
        "sweep_reclaim": bool(features.get("sweep_long" if long else "sweep_short")),
        "double_extreme": bool(features.get("double_bottom" if long else "double_top")),
        "trendline_break": bool(features.get("trendline_break_long" if long else "trendline_break_short")),
        "choch": bool(features.get("choch_long" if long else "choch_short")),
        "bos": bool(features.get("bos_long" if long else "bos_short")),
        "volume_wake": bool(features.get("volume_wake")),
        "impulse": bool(features.get("impulse_long" if long else "impulse_short")),
        "ema_turn": bool(features.get("ema_turn_long" if long else "ema_turn_short")),
        "fifteen_not_opposing": bool(features.get("fifteen_long_ok" if long else "fifteen_short_ok")),
    }
    distance_atr = _sf(features.get("origin_long_distance_atr" if long else "origin_short_distance_atr"), 999.0)
    conditions["origin_very_close"] = 0.0 <= distance_atr <= 1.50
    conditions["origin_close"] = 1.50 < distance_atr <= MAX_READY_ORIGIN_DISTANCE_ATR

    weights = {
        "prior_trend": 8,
        "structure_shift": 11,
        "zone_touch": 8,
        "sweep_reclaim": 14,
        "double_extreme": 10,
        "trendline_break": 16,
        "choch": 18,
        "bos": 6,
        "volume_wake": 8,
        "impulse": 8,
        "ema_turn": 6,
        "fifteen_not_opposing": 3,
        "origin_very_close": 8,
        "origin_close": 4,
    }
    score = sum(w for name, w in weights.items() if conditions.get(name))
    if distance_atr > MAX_READY_ORIGIN_DISTANCE_ATR:
        score -= 16
    return max(0, min(100, int(round(score)))), conditions


def _stage(score: int, conditions: Dict[str, bool], distance_atr: float) -> str:
    structural_break = conditions.get("choch") or conditions.get("trendline_break")
    momentum_confirm = conditions.get("volume_wake") or conditions.get("impulse")
    origin_evidence = (
        conditions.get("sweep_reclaim")
        or conditions.get("double_extreme")
        or conditions.get("structure_shift")
        or conditions.get("zone_touch")
    )
    if (
        score >= READY_SCORE
        and structural_break
        and momentum_confirm
        and origin_evidence
        and distance_atr <= MAX_READY_ORIGIN_DISTANCE_ATR
    ):
        return "READY"
    if score >= WATCH_SCORE and origin_evidence:
        return "WATCH"
    return "NONE"


def analyze(symbol: str, df5m: Any, df15m: Any = None, current_price: Any = None) -> Optional[Dict[str, Any]]:
    features = extract_features(df5m, df15m, current_price)
    if not features:
        return None
    long_score, long_conditions = score_direction(features, "LONG")
    short_score, short_conditions = score_direction(features, "SHORT")
    if long_score >= short_score:
        direction, score, other, conditions = "LONG", long_score, short_score, long_conditions
    else:
        direction, score, other, conditions = "SHORT", short_score, long_score, short_conditions
    if score - other < MIN_DIRECTION_GAP:
        return None

    long = direction == "LONG"
    origin = _sf(features.get("origin_long" if long else "origin_short"))
    distance_atr = _sf(features.get("origin_long_distance_atr" if long else "origin_short_distance_atr"), 999.0)
    stage = _stage(score, conditions, distance_atr)
    if stage == "NONE":
        return None

    entry = _sf(features.get("signal_price"))
    atr = _sf(features.get("atr5"))
    if entry <= 0 or origin <= 0 or atr <= 0:
        return None
    stop = origin - STOP_ATR_BUFFER * atr if long else origin + STOP_ATR_BUFFER * atr
    risk = entry - stop if long else stop - entry
    if risk <= 0:
        return None

    return {
        "symbol": str(symbol or "").upper(),
        "direction": direction,
        "stage": stage,
        "score": score,
        "opposite_score": other,
        "entry": entry,
        "origin": origin,
        "origin_distance_atr": round(distance_atr, 4),
        "origin_distance_percent": features.get("origin_long_distance_percent" if long else "origin_short_distance_percent"),
        "origin_bars_ago": features.get("origin_long_bars_ago" if long else "origin_short_bars_ago"),
        "stop": stop,
        "risk_abs": risk,
        "risk_percent": round(risk / entry * 100.0, 4),
        "target_2r": entry + (2 * risk if long else -2 * risk),
        "target_3r": entry + (3 * risk if long else -3 * risk),
        "features": features,
        "conditions": conditions,
        "version": VERSION,
    }


def _bar_extremes(df5m: Any, fallback: float) -> Tuple[float, float]:
    f = _clean(df5m, 3)
    if f is None:
        return fallback, fallback
    row = f.iloc[-2]
    return _sf(row["high"], fallback), _sf(row["low"], fallback)


def _update_open(record: Dict[str, Any], price: float, high: float, low: float, now: int) -> None:
    entry = _sf(record.get("entry"))
    stop = _sf(record.get("stop"))
    risk = _sf(record.get("risk_abs"))
    direction = str(record.get("direction") or "")
    if entry <= 0 or risk <= 0:
        return
    if direction == "LONG":
        favorable = max(high, price) - entry
        adverse = entry - min(low, price)
        hit_stop = min(low, price) <= stop
        hit2 = max(high, price) >= entry + 2 * risk
        hit3 = max(high, price) >= entry + 3 * risk
    else:
        favorable = entry - min(low, price)
        adverse = max(high, price) - entry
        hit_stop = max(high, price) >= stop
        hit2 = min(low, price) <= entry - 2 * risk
        hit3 = min(low, price) <= entry - 3 * risk
    record["max_mfe_r"] = round(max(_sf(record.get("max_mfe_r")), favorable / risk), 4)
    record["max_mae_r"] = round(max(_sf(record.get("max_mae_r")), adverse / risk), 4)
    if hit3:
        record["hit_3r"] = True
    # Conservative same-bar rule: stop and 2R aynı mumdaysa belirsiz say.
    if hit_stop and hit2:
        record["status"] = "AMBIGUOUS_SAME_5M_BAR"
        record["closed_at"] = now
    elif hit_stop:
        record["status"] = "STOP_FIRST"
        record["closed_at"] = now
    elif hit2:
        record["status"] = "R2_FIRST"
        record["closed_at"] = now
    elif now - int(record.get("started_at") or now) >= MAX_TRACK_SECONDS:
        record["status"] = "TIMEOUT"
        record["closed_at"] = now


def observe(symbol: str, df5m: Any, df15m: Any = None, current_price: Any = None) -> Optional[Dict[str, Any]]:
    global _DIRTY
    state = _state()
    now = int(time.time())
    price = _sf(current_price)
    if price <= 0:
        f = _clean(df5m, 3)
        if f is not None:
            price = _sf(f.iloc[-2]["close"])
    high, low = _bar_extremes(df5m, price)

    for key, record in list(state.get("open", {}).items()):
        if str(record.get("symbol")) != str(symbol).upper():
            continue
        _update_open(record, price, high, low, now)
        if record.get("status") != "OPEN":
            state["records"].append(dict(record))
            state["open"].pop(key, None)
            _DIRTY = True

    result = analyze(symbol, df5m, df15m, current_price)
    if not result:
        return None
    key = f"{result['symbol']}_{result['direction']}"
    existing = state.get("open", {}).get(key)
    if isinstance(existing, dict):
        if result.get("stage") == "READY" and existing.get("stage") != "READY":
            existing["stage"] = "READY"
            existing["upgraded_at"] = now
            existing["upgrade_score"] = result.get("score")
            _DIRTY = True
            return {"event": "UPGRADE", "result": result, "record": existing}
        return None

    last_started = int(state.get("last_started", {}).get(key) or 0)
    if now - last_started < DUPLICATE_SECONDS:
        return None

    record = {
        **result,
        "started_at": now,
        "status": "OPEN",
        "max_mfe_r": 0.0,
        "max_mae_r": 0.0,
        "hit_3r": False,
        "shadow_only": True,
    }
    state["open"][key] = record
    state["last_started"][key] = now
    _DIRTY = True
    return {"event": "NEW", "result": result, "record": record}


def _summary(state: Dict[str, Any]) -> Dict[str, Any]:
    records = list(state.get("records", []))
    open_records = list(state.get("open", {}).values())
    outcomes = Counter(str(r.get("status")) for r in records)
    by_stage: Dict[str, Counter] = defaultdict(Counter)
    distances: Dict[str, List[float]] = defaultdict(list)
    for r in records:
        stage = str(r.get("stage") or "UNKNOWN")
        by_stage[stage][str(r.get("status"))] += 1
        d = _sf(r.get("origin_distance_atr"), -1.0)
        if d >= 0:
            distances[stage].append(d)
    return {
        "version": VERSION,
        "mode": MODE,
        "closed": len(records),
        "open": len(open_records),
        "outcomes": dict(outcomes),
        "by_stage": {k: dict(v) for k, v in by_stage.items()},
        "avg_origin_distance_atr_by_stage": {
            k: round(sum(v) / len(v), 4) for k, v in distances.items() if v
        },
        "ready_open": sum(1 for r in open_records if r.get("stage") == "READY"),
        "watch_open": sum(1 for r in open_records if r.get("stage") == "WATCH"),
    }


def finish(path: Optional[str] = None) -> Dict[str, Any]:
    global _DIRTY
    state = _state()
    now = int(time.time())
    cutoff = now - KEEP_SECONDS
    state["records"] = [
        r for r in state.get("records", [])
        if int(r.get("closed_at") or r.get("started_at") or now) >= cutoff
    ][-MAX_RECORDS:]
    state["last_started"] = {
        k: int(v) for k, v in state.get("last_started", {}).items()
        if int(v or 0) >= cutoff
    }
    state["summary"] = _summary(state)
    state["updated_at"] = now
    _atomic_save(path or _STATE_PATH, state)
    _DIRTY = False
    return dict(state["summary"])
