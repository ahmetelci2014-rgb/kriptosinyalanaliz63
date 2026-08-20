"""Movement Start Shadow V1.

Amaç: Premium canlı sinyalini değiştirmeden, bir coin hareket etmeden ÖNCE
oluşan hazırlık / sıkışma / yapı dönüşü işaretlerini kaydetmek ve sonraki
30/60/120 dakikadaki sonucu otomatik etiketlemek.

Bu modül:
- Telegram mesajı göndermez.
- Emir açmaz.
- Premium canlı giriş kurallarını değiştirmez.
- Sadece Premium taramasında zaten indirilen 15M/1H/4H verisini kullanır.
- Her gözlemin MFE/MAE'sini ve ilk başarı/başarısızlık bariyerini kaydeder.

Hedef, RAYUSDT gibi hareketleri 4H/1H tamamen teyit olduktan sonra değil;
taban/sıkışma aşamasında veriye dönüştürmek ve yeterli örnek oluşunca
canlı erken-giriş kuralını bu gerçek sonuçlardan üretmektir.
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

VERSION = "MOVEMENT_START_SHADOW_V1_2026_08_20"
STATE_FILE = "movement_start_shadow.json"

# İlk sürümde canlı sinyal YOK. Bunlar yalnız gölge aday eşikleridir.
PREP_SCORE = 64
ARMED_SCORE = 74
TRIGGER_SCORE = 84
MIN_DIRECTION_GAP = 5

# Öğrenme etiketi: girişten sonra hangi bariyer önce görüldü?
SUCCESS_MOVE_PERCENT = 2.00
FAIL_MOVE_PERCENT = 1.00
MAX_TRACK_SECONDS = 120 * 60
SNAPSHOT_EVERY_SECONDS = 10 * 60
DUPLICATE_SECONDS = 45 * 60
KEEP_SECONDS = 14 * 24 * 60 * 60
MAX_RECORDS = 1800

_STATE: Optional[Dict[str, Any]] = None
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
            prefix=".movement_start.",
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
        "mode": "SHADOW_LEARNING_ONLY_NO_TELEGRAM_NO_ORDERS",
        "updated_at": 0,
        "records": [],
        "open": {},
        "last_started": {},
        "summary": {},
    }


def _load_state(path: str = STATE_FILE) -> Dict[str, Any]:
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
    data["mode"] = "SHADOW_LEARNING_ONLY_NO_TELEGRAM_NO_ORDERS"
    return data


def begin(path: str = STATE_FILE) -> None:
    global _STATE, _DIRTY
    _STATE = _load_state(path)
    _DIRTY = False


def _state() -> Dict[str, Any]:
    global _STATE
    if _STATE is None:
        begin()
    return _STATE or _default_state()


def _clean_frame(df: Any, min_len: int) -> Optional[pd.DataFrame]:
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


def _enrich_15m(df: Any) -> Optional[pd.DataFrame]:
    frame = _clean_frame(df, 70)
    if frame is None:
        return None
    frame["ema20"] = EMAIndicator(frame["close"], window=20).ema_indicator()
    frame["ema50"] = EMAIndicator(frame["close"], window=50).ema_indicator()
    frame["rsi"] = RSIIndicator(frame["close"], window=14).rsi()
    frame["atr"] = AverageTrueRange(
        frame["high"], frame["low"], frame["close"], window=14
    ).average_true_range()
    frame["volume_avg"] = frame["volume"].rolling(20).mean()
    frame["vol_ratio"] = frame["volume"] / frame["volume_avg"]
    frame["ema20_slope_pct"] = (
        (frame["ema20"] - frame["ema20"].shift(3))
        / frame["ema20"].shift(3).replace(0, float("nan"))
        * 100.0
    )
    frame["atr_pct"] = frame["atr"] / frame["close"].replace(0, float("nan")) * 100.0
    return frame.dropna().reset_index(drop=True)


def _enrich_upper(df: Any) -> Optional[pd.DataFrame]:
    frame = _clean_frame(df, 55)
    if frame is None:
        return None
    frame["ema20"] = EMAIndicator(frame["close"], window=20).ema_indicator()
    frame["ema50"] = EMAIndicator(frame["close"], window=50).ema_indicator()
    frame["rsi"] = RSIIndicator(frame["close"], window=14).rsi()
    frame["ema20_slope"] = frame["ema20"] - frame["ema20"].shift(3)
    return frame.dropna().reset_index(drop=True)


def _close_power(row: pd.Series) -> float:
    high = _sf(row.get("high"))
    low = _sf(row.get("low"))
    close = _sf(row.get("close"))
    span = high - low
    if span <= 0:
        return 50.0
    return max(0.0, min(100.0, (close - low) / span * 100.0))


def _upper_not_opposing(frame: Optional[pd.DataFrame], direction: str) -> bool:
    # Hareket başlangıcı motoru 1H/4H teyidini beklemez. Sadece çok güçlü ters
    # yapıyı veto eder; bu nedenle Premium'dan daha erken gözlem üretebilir.
    if frame is None or len(frame) < 5:
        return True
    row = frame.iloc[-2]
    close = _sf(row["close"])
    ema20 = _sf(row["ema20"])
    ema50 = _sf(row["ema50"])
    rsi = _sf(row["rsi"], 50.0)
    slope = _sf(row["ema20_slope"])

    if direction == "LONG":
        strong_short = close < ema20 < ema50 and slope < 0 and rsi < 40
        return not strong_short
    strong_long = close > ema20 > ema50 and slope > 0 and rsi > 60
    return not strong_long


def _extract_features(
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any,
) -> Optional[Dict[str, Any]]:
    f15 = _enrich_15m(df15m)
    if f15 is None or len(f15) < 50:
        return None
    f1 = _enrich_upper(df1h)
    f4 = _enrich_upper(df4h)

    # -1 borsanın açık mumu olabilir; karar özellikleri son kapanan mumdan gelir.
    row = f15.iloc[-2]
    prev = f15.iloc[-5]
    recent = f15.iloc[-10:-2]
    older = f15.iloc[-34:-10]
    if len(recent) < 8 or len(older) < 20:
        return None

    close = _sf(row["close"])
    price = _sf(current_price, close)
    if close <= 0 or price <= 0:
        return None

    recent_high = _sf(recent["high"].max())
    recent_low = _sf(recent["low"].min())
    older_high = _sf(older["high"].max())
    older_low = _sf(older["low"].min())
    recent_range = max(0.0, recent_high - recent_low)
    older_range = max(0.0, older_high - older_low)
    range_compression = recent_range / older_range if older_range > 0 else 1.0

    recent_atr = _sf(recent["atr_pct"].mean())
    older_atr = _sf(older["atr_pct"].mean())
    atr_compression = recent_atr / older_atr if older_atr > 0 else 1.0

    last3 = f15.iloc[-5:-2]
    prior6 = f15.iloc[-11:-5]
    last3_low = _sf(last3["low"].min())
    prior6_low = _sf(prior6["low"].min())
    last3_high = _sf(last3["high"].max())
    prior6_high = _sf(prior6["high"].max())

    # Toleranslı higher-low / lower-high: taban/tepe tekrar testlerinde ufak fitil
    # farkı yüzünden iyi birikim örneğini kaybetmeyelim.
    higher_low = last3_low >= prior6_low * 0.998
    lower_high = last3_high <= prior6_high * 1.002

    low_cluster_pct = (
        (last3["low"].max() - last3["low"].min()) / close * 100.0
        if close > 0 else 999.0
    )
    high_cluster_pct = (
        (last3["high"].max() - last3["high"].min()) / close * 100.0
        if close > 0 else 999.0
    )
    support_hold = bool(higher_low and low_cluster_pct <= 1.25)
    resistance_hold = bool(lower_high and high_cluster_pct <= 1.25)

    ema20 = _sf(row["ema20"])
    ema50 = _sf(row["ema50"])
    ema_slope = _sf(row["ema20_slope_pct"])
    prev_slope = _sf(prev["ema20_slope_pct"])
    ema_turn = ema_slope - prev_slope
    rsi = _sf(row["rsi"], 50.0)
    rsi_prev = _sf(prev["rsi"], 50.0)
    rsi_slope = rsi - rsi_prev

    volume_recent = _sf(f15.iloc[-4:-2]["volume"].mean())
    volume_before = _sf(f15.iloc[-16:-4]["volume"].mean())
    volume_wake = volume_recent / volume_before if volume_before > 0 else 1.0
    vol_ratio = _sf(row["vol_ratio"], 1.0)

    lookback = f15.iloc[-18:-2]
    prior_break = f15.iloc[-14:-2]
    high16 = _sf(lookback["high"].max())
    low16 = _sf(lookback["low"].min())
    prior_high = _sf(prior_break.iloc[:-1]["high"].max()) if len(prior_break) > 1 else high16
    prior_low = _sf(prior_break.iloc[:-1]["low"].min()) if len(prior_break) > 1 else low16
    dist_high_pct = max(0.0, (high16 - close) / close * 100.0)
    dist_low_pct = max(0.0, (close - low16) / close * 100.0)
    breakout_long = bool(close > prior_high and vol_ratio >= 1.15)
    breakout_short = bool(close < prior_low and vol_ratio >= 1.15)

    ema_distance_pct = abs(close - ema20) / ema20 * 100.0 if ema20 > 0 else 999.0

    return {
        "price": price,
        "close_15m": close,
        "rsi_15m": round(rsi, 3),
        "rsi_slope": round(rsi_slope, 3),
        "ema20": ema20,
        "ema50": ema50,
        "ema20_slope_pct": round(ema_slope, 5),
        "ema_turn": round(ema_turn, 5),
        "ema_distance_pct": round(ema_distance_pct, 4),
        "atr_compression": round(atr_compression, 4),
        "range_compression": round(range_compression, 4),
        "volume_wake": round(volume_wake, 4),
        "vol_ratio": round(vol_ratio, 4),
        "close_power": round(_close_power(row), 2),
        "higher_low": bool(higher_low),
        "lower_high": bool(lower_high),
        "support_hold": bool(support_hold),
        "resistance_hold": bool(resistance_hold),
        "dist_high_pct": round(dist_high_pct, 4),
        "dist_low_pct": round(dist_low_pct, 4),
        "breakout_long": breakout_long,
        "breakout_short": breakout_short,
        "one_hour_long_ok": _upper_not_opposing(f1, "LONG"),
        "one_hour_short_ok": _upper_not_opposing(f1, "SHORT"),
        "four_hour_long_ok": _upper_not_opposing(f4, "LONG"),
        "four_hour_short_ok": _upper_not_opposing(f4, "SHORT"),
    }


def score_direction(features: Dict[str, Any], direction: str) -> Tuple[int, Dict[str, bool]]:
    """Saf skor fonksiyonu; test ve ileride adaptif kalibrasyon için ayrı tutulur."""
    direction = str(direction).upper()
    if direction not in {"LONG", "SHORT"}:
        return 0, {}

    long = direction == "LONG"
    rsi = _sf(features.get("rsi_15m"), 50.0)
    rsi_slope = _sf(features.get("rsi_slope"))
    ema_slope = _sf(features.get("ema20_slope_pct"))
    ema_turn = _sf(features.get("ema_turn"))
    volume_wake = _sf(features.get("volume_wake"), 1.0)
    vol_ratio = _sf(features.get("vol_ratio"), 1.0)
    atr_comp = _sf(features.get("atr_compression"), 1.0)
    range_comp = _sf(features.get("range_compression"), 1.0)
    ema_dist = _sf(features.get("ema_distance_pct"), 999.0)
    close_power = _sf(features.get("close_power"), 50.0)
    distance = _sf(features.get("dist_high_pct" if long else "dist_low_pct"), 999.0)

    conditions = {
        "compression_atr": atr_comp <= 0.90,
        "compression_range": range_comp <= 0.72,
        "structure_hold": bool(features.get("support_hold" if long else "resistance_hold")),
        "structure_step": bool(features.get("higher_low" if long else "lower_high")),
        "ema_not_opposing": ema_slope >= -0.03 if long else ema_slope <= 0.03,
        "ema_turning": ema_turn > 0 if long else ema_turn < 0,
        "rsi_zone": 42 <= rsi <= 64 if long else 36 <= rsi <= 58,
        "rsi_turning": rsi_slope >= 1.0 if long else rsi_slope <= -1.0,
        "volume_waking": volume_wake >= 1.08 or vol_ratio >= 1.12,
        "near_trigger": distance <= 1.20,
        "close_strength": close_power >= 56 if long else close_power <= 44,
        "one_hour_not_opposing": bool(features.get("one_hour_long_ok" if long else "one_hour_short_ok", True)),
        "four_hour_not_opposing": bool(features.get("four_hour_long_ok" if long else "four_hour_short_ok", True)),
        "breakout": bool(features.get("breakout_long" if long else "breakout_short")),
        "not_extended": ema_dist <= 1.20,
    }

    score = 28
    weights = {
        "compression_atr": 7,
        "compression_range": 7,
        "structure_hold": 9,
        "structure_step": 7,
        "ema_not_opposing": 5,
        "ema_turning": 5,
        "rsi_zone": 5,
        "rsi_turning": 6,
        "volume_waking": 7,
        "near_trigger": 5,
        "close_strength": 4,
        "one_hour_not_opposing": 6,
        "four_hour_not_opposing": 4,
        "breakout": 10,
        "not_extended": 6,
    }
    for key, weight in weights.items():
        if conditions[key]:
            score += weight

    # Güçlü ters üst-zaman yapısında hazırlık adayı canlıya yaklaşmasın.
    if not conditions["one_hour_not_opposing"]:
        score -= 18
    if not conditions["four_hour_not_opposing"]:
        score -= 8

    return max(0, min(100, int(round(score)))), conditions


def _stage(score: int, conditions: Dict[str, bool]) -> Optional[str]:
    if score >= TRIGGER_SCORE and conditions.get("breakout") and conditions.get("volume_waking"):
        return "TRIGGER"
    if score >= ARMED_SCORE:
        return "ARMED"
    if score >= PREP_SCORE:
        return "PREP"
    return None


def analyze(
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any,
) -> Optional[Dict[str, Any]]:
    features = _extract_features(df15m, df1h, df4h, current_price)
    if features is None:
        return None
    long_score, long_conditions = score_direction(features, "LONG")
    short_score, short_conditions = score_direction(features, "SHORT")

    if long_score >= short_score:
        direction, score, other, conditions = "LONG", long_score, short_score, long_conditions
    else:
        direction, score, other, conditions = "SHORT", short_score, long_score, short_conditions

    if score - other < MIN_DIRECTION_GAP:
        return None
    stage = _stage(score, conditions)
    if stage is None:
        return None

    return {
        "direction": direction,
        "stage": stage,
        "score": score,
        "opposite_score": other,
        "features": features,
        "conditions": conditions,
    }


def _directional_moves(direction: str, entry: float, price: float) -> Tuple[float, float]:
    raw = (price - entry) / entry * 100.0
    favorable = raw if direction == "LONG" else -raw
    adverse = -raw if direction == "LONG" else raw
    return max(0.0, favorable), max(0.0, adverse)


def _record_by_id(state: Dict[str, Any], record_id: str) -> Optional[Dict[str, Any]]:
    for row in reversed(state.get("records", [])):
        if str(row.get("id")) == str(record_id):
            return row
    return None


def _update_open_for_symbol(symbol: str, price: float, now: int) -> None:
    global _DIRTY
    state = _state()
    open_map = state.setdefault("open", {})
    for key, record_id in list(open_map.items()):
        if not key.startswith(f"{symbol}|"):
            continue
        record = _record_by_id(state, record_id)
        if not record:
            open_map.pop(key, None)
            _DIRTY = True
            continue
        if record.get("outcome"):
            open_map.pop(key, None)
            _DIRTY = True
            continue

        entry = _sf(record.get("entry"))
        if entry <= 0 or price <= 0:
            continue
        favorable, adverse = _directional_moves(record.get("direction"), entry, price)
        record["latest_price"] = price
        record["max_favorable_percent"] = round(max(_sf(record.get("max_favorable_percent")), favorable), 4)
        record["max_adverse_percent"] = round(max(_sf(record.get("max_adverse_percent")), adverse), 4)
        record["last_seen_at"] = now
        _DIRTY = True

        if not record.get("success_reached_at") and favorable >= SUCCESS_MOVE_PERCENT:
            record["success_reached_at"] = now
        if not record.get("fail_reached_at") and adverse >= FAIL_MOVE_PERCENT:
            record["fail_reached_at"] = now

        last_snapshot = int(record.get("last_snapshot_at") or record.get("created_at") or now)
        if now - last_snapshot >= SNAPSHOT_EVERY_SECONDS:
            record.setdefault("snapshots", []).append({
                "at": now,
                "price": round(price, 12),
                "favorable_percent": round(favorable, 4),
                "adverse_percent": round(adverse, 4),
            })
            record["snapshots"] = record["snapshots"][-16:]
            record["last_snapshot_at"] = now

        success_at = int(record.get("success_reached_at") or 0)
        fail_at = int(record.get("fail_reached_at") or 0)
        age = now - int(record.get("created_at") or now)
        outcome = None
        if success_at and (not fail_at or success_at <= fail_at):
            outcome = "SUCCESS_FIRST"
        elif fail_at and (not success_at or fail_at < success_at):
            outcome = "FAIL_FIRST"
        elif age >= MAX_TRACK_SECONDS:
            outcome = "TIMEOUT"

        if outcome:
            record["outcome"] = outcome
            record["closed_at"] = now
            record["duration_minutes"] = round(age / 60.0, 1)
            open_map.pop(key, None)


def _start_record(symbol: str, result: Dict[str, Any], now: int) -> Dict[str, Any]:
    global _DIRTY
    state = _state()
    direction = result["direction"]
    key = f"{symbol}|{direction}"
    price = _sf(result["features"].get("price"))
    record_id = f"{symbol}_{direction}_START_{now}"
    record = {
        "id": record_id,
        "symbol": symbol,
        "direction": direction,
        "stage": result["stage"],
        "max_stage": result["stage"],
        "score": result["score"],
        "opposite_score": result["opposite_score"],
        "created_at": now,
        "last_seen_at": now,
        "entry": round(price, 12),
        "latest_price": round(price, 12),
        "max_favorable_percent": 0.0,
        "max_adverse_percent": 0.0,
        "success_reached_at": 0,
        "fail_reached_at": 0,
        "outcome": None,
        "features": result["features"],
        "conditions": result["conditions"],
        "snapshots": [],
        "last_snapshot_at": now,
    }
    state.setdefault("records", []).append(record)
    state.setdefault("open", {})[key] = record_id
    state.setdefault("last_started", {})[key] = now
    _DIRTY = True
    return record


def observe(
    symbol: str,
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any,
    *,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Bir Premium derin-tarama sembolünü gölge öğrenme motoruna işler."""
    global _DIRTY
    now = int(now_ts if now_ts is not None else time.time())
    symbol = str(symbol or "").upper().replace("/", "").replace(":", "")
    price = _sf(current_price)
    if not symbol or price <= 0:
        return None

    _update_open_for_symbol(symbol, price, now)
    result = analyze(df15m, df1h, df4h, current_price)
    if result is None:
        return None

    state = _state()
    direction = result["direction"]
    key = f"{symbol}|{direction}"
    existing_id = state.setdefault("open", {}).get(key)
    if existing_id:
        record = _record_by_id(state, existing_id)
        if record:
            stage_rank = {"PREP": 1, "ARMED": 2, "TRIGGER": 3}
            old_stage = str(record.get("max_stage") or record.get("stage") or "PREP")
            new_stage = result["stage"]
            record["latest_score"] = result["score"]
            if stage_rank.get(new_stage, 0) > stage_rank.get(old_stage, 0):
                record["max_stage"] = new_stage
                record.setdefault("stage_history", []).append({"stage": new_stage, "at": now, "score": result["score"]})
                _DIRTY = True
                return {"event": "UPGRADE", "record": record, "result": result}
        return None

    last = int(state.setdefault("last_started", {}).get(key, 0) or 0)
    if last and now - last < DUPLICATE_SECONDS:
        return None

    record = _start_record(symbol, result, now)
    return {"event": "NEW", "record": record, "result": result}


def _rebuild_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    records = state.get("records", [])
    outcomes = Counter()
    stages = defaultdict(Counter)
    directions = defaultdict(Counter)
    success_minutes = []

    for row in records:
        outcome = str(row.get("outcome") or "OPEN")
        stage = str(row.get("max_stage") or row.get("stage") or "UNKNOWN")
        direction = str(row.get("direction") or "UNKNOWN")
        outcomes[outcome] += 1
        stages[stage][outcome] += 1
        directions[direction][outcome] += 1
        if outcome == "SUCCESS_FIRST" and row.get("success_reached_at"):
            success_minutes.append(
                max(0.0, (int(row["success_reached_at"]) - int(row.get("created_at") or row["success_reached_at"])) / 60.0)
            )

    closed = outcomes["SUCCESS_FIRST"] + outcomes["FAIL_FIRST"] + outcomes["TIMEOUT"]
    decisive = outcomes["SUCCESS_FIRST"] + outcomes["FAIL_FIRST"]
    return {
        "records": len(records),
        "open": outcomes["OPEN"],
        "closed": closed,
        "success_first": outcomes["SUCCESS_FIRST"],
        "fail_first": outcomes["FAIL_FIRST"],
        "timeout": outcomes["TIMEOUT"],
        "success_rate_decisive_percent": round(outcomes["SUCCESS_FIRST"] / decisive * 100.0, 2) if decisive else None,
        "avg_minutes_to_success": round(sum(success_minutes) / len(success_minutes), 1) if success_minutes else None,
        "by_stage": {key: dict(value) for key, value in stages.items()},
        "by_direction": {key: dict(value) for key, value in directions.items()},
    }


def finish(path: str = STATE_FILE) -> Dict[str, Any]:
    global _STATE, _DIRTY
    state = _state()
    now = int(time.time())
    cutoff = now - KEEP_SECONDS
    state["records"] = [
        row for row in state.get("records", [])
        if int(row.get("created_at") or 0) >= cutoff
    ][-MAX_RECORDS:]
    valid_ids = {str(row.get("id")) for row in state["records"]}
    state["open"] = {
        key: value for key, value in state.get("open", {}).items()
        if str(value) in valid_ids
    }
    state["last_started"] = {
        key: value for key, value in state.get("last_started", {}).items()
        if int(value or 0) >= cutoff
    }
    state["summary"] = _rebuild_summary(state)
    state["updated_at"] = now
    state["version"] = VERSION
    state["mode"] = "SHADOW_LEARNING_ONLY_NO_TELEGRAM_NO_ORDERS"
    if _DIRTY or not os.path.exists(path):
        _atomic_save(path, state)
    summary = dict(state["summary"])
    _STATE = None
    _DIRTY = False
    return summary
