"""Multi-day 4H trend-life shadow for Premium movement candidates.

Purpose
-------
Learn whether a movement detected anywhere in the all-coins Premium scan becomes
an hours/days-long directional trend. This module never sends Telegram messages,
never opens/closes orders, and never changes TP/SL/BE or live signal eligibility.

It reuses Movement Start V2's already-discovered all-market candidates, then
checks only a small rotating set with 1H + 4H data. Once a symbol is enrolled it
can remain under observation for up to 96 hours even after the original 5M
movement record disappears.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange

VERSION = "MULTI_DAY_4H_TREND_LIFE_SHADOW_V1_2026_08_24"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_EXIT_MUTATION"
STATE_FILE = "multi_day_trend_shadow.json"
MOVEMENT_FILE = "movement_start_v2_shadow.json"

MAX_TRACK_HOURS = 96
MIN_RECHECK_SECONDS = 25 * 60
MAX_CHECKS_PER_RUN = 12
MAX_ACTIVE_RECORDS = 120
MAX_HISTORY_PER_RECORD = 40
DISCOVERY_MAX_AGE_SECONDS = 60 * 60
DISCOVERY_MIN_ARMED_SCORE = 76
DISCOVERY_MIN_PREP_SCORE = 84

FETCH_1H_LIMIT = 180
FETCH_4H_LIMIT = 180
CHECKPOINT_HOURS = (6, 12, 24, 48, 72, 96)

STATUS_START = "4H_TREND_BASLANGIC"
STATUS_STRONG = "4H_DEVAM_GUCLU"
STATUS_CONTINUE = "4H_DEVAM"
STATUS_WEAK = "4H_ZAYIFLIYOR"
STATUS_EXIT = "4H_BITIS_RISKI"


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
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
            prefix=".multi_day_trend.",
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


def _default_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "updated_at": 0,
        "records": {},
        "summary": {},
    }


def _to_okx_symbol(symbol: str) -> str:
    raw = str(symbol or "").upper().strip()
    base = raw[:-4] if raw.endswith("USDT") else raw
    return f"{base}/USDT:USDT"


def _frame(rows: Any) -> Optional[pd.DataFrame]:
    if not isinstance(rows, list) or len(rows) < 60:
        return None
    try:
        df = pd.DataFrame(
            rows,
            columns=["time", "open", "high", "low", "close", "volume"],
        )
        for col in ("time", "open", "high", "low", "close", "volume"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna().reset_index(drop=True)
        if len(df) < 60:
            return None
        df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
        df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
        df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
        df["adx"] = ADXIndicator(
            df["high"], df["low"], df["close"], window=14
        ).adx()
        df["atr"] = AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range()
        df["ema20_slope"] = df["ema20"] - df["ema20"].shift(3)
        df = df.dropna().reset_index(drop=True)
        return df if len(df) >= 20 else None
    except Exception:
        return None


def _directional_percent(direction: str, start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    raw = (end - start) / start * 100.0
    return raw if str(direction).upper() == "LONG" else -raw


def _trend_state(row: pd.Series, direction: str) -> int:
    direction = str(direction or "").upper()
    sign = 1 if direction == "LONG" else -1
    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    slope = float(row["ema20_slope"])
    aligned = (
        sign * (close - ema20) > 0
        and sign * (ema20 - ema50) > 0
        and sign * slope > 0
    )
    opposed = sign * (close - ema20) < 0 and sign * slope < 0
    if aligned:
        return 1
    if opposed:
        return -1
    return 0


def _structure_state(df: pd.DataFrame, direction: str) -> int:
    closed = df.iloc[:-1]
    if len(closed) < 5:
        return 0
    a = closed.iloc[-3]
    b = closed.iloc[-2]
    if direction == "LONG":
        if float(b["high"]) > float(a["high"]) and float(b["low"]) > float(a["low"]):
            return 1
        if float(b["high"]) < float(a["high"]) and float(b["low"]) < float(a["low"]):
            return -1
    else:
        if float(b["high"]) < float(a["high"]) and float(b["low"]) < float(a["low"]):
            return 1
        if float(b["high"]) > float(a["high"]) and float(b["low"]) > float(a["low"]):
            return -1
    return 0


def _trend_origin(df4h: pd.DataFrame, direction: str) -> Tuple[float, int, str]:
    closed = df4h.iloc[:-1].reset_index(drop=True)
    if len(closed) < 20:
        row = closed.iloc[-1]
        return float(row["close"]), int(float(row["time"]) / 1000), "FALLBACK_LAST"

    start = max(4, len(closed) - 80)
    for idx in range(len(closed) - 1, start, -1):
        row = closed.iloc[idx]
        prev = closed.iloc[idx - 1]
        if direction == "LONG":
            crossed = (
                float(row["close"]) > float(row["ema20"])
                and float(prev["close"]) <= float(prev["ema20"])
                and float(row["ema20_slope"]) > 0
            )
        else:
            crossed = (
                float(row["close"]) < float(row["ema20"])
                and float(prev["close"]) >= float(prev["ema20"])
                and float(row["ema20_slope"]) < 0
            )
        if not crossed:
            continue
        left = max(0, idx - 4)
        window = closed.iloc[left : idx + 1]
        if direction == "LONG":
            pos = int(window["low"].astype(float).idxmin())
            price = float(closed.loc[pos, "low"])
        else:
            pos = int(window["high"].astype(float).idxmax())
            price = float(closed.loc[pos, "high"])
        origin_at = int(float(closed.loc[pos, "time"]) / 1000)
        return price, origin_at, "4H_EMA20_REGIME_CROSS"

    window = closed.iloc[-30:]
    if direction == "LONG":
        pos = int(window["low"].astype(float).idxmin())
        price = float(closed.loc[pos, "low"])
    else:
        pos = int(window["high"].astype(float).idxmax())
        price = float(closed.loc[pos, "high"])
    origin_at = int(float(closed.loc[pos, "time"]) / 1000)
    return price, origin_at, "RECENT_4H_SWING"


def evaluate_frames(
    direction: str,
    df1h: pd.DataFrame,
    df4h: pd.DataFrame,
    current_price: float,
    *,
    now_ts: int,
) -> Dict[str, Any]:
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if len(df1h) < 10 or len(df4h) < 10:
        raise ValueError("not enough data")

    r1 = df1h.iloc[-2]
    r4 = df4h.iloc[-2]
    trend1 = _trend_state(r1, direction)
    trend4 = _trend_state(r4, direction)
    structure4 = _structure_state(df4h, direction)

    score = 50
    reasons: List[str] = []

    score += trend4 * 24
    score += trend1 * 12
    score += structure4 * 6

    if trend4 > 0:
        reasons.append("4H ana trend yönü destekliyor")
    elif trend4 < 0:
        reasons.append("4H ana trend tersine dönüyor")
    if trend1 > 0:
        reasons.append("1H trend devamı destekliyor")
    elif trend1 < 0:
        reasons.append("1H trend yönün tersine dönüyor")
    if structure4 > 0:
        reasons.append("4H tepe/dip yapısı devam yönünde")
    elif structure4 < 0:
        reasons.append("4H yapı bozulma işareti veriyor")

    adx4 = float(r4["adx"])
    adx1 = float(r1["adx"])
    if adx4 >= 28:
        score += 8
        reasons.append("4H ADX güçlü")
    elif adx4 >= 20:
        score += 5
    elif adx4 < 14:
        score -= 6
        reasons.append("4H trend gücü zayıf")

    if adx1 >= 24:
        score += 4
    elif adx1 >= 18:
        score += 2
    elif adx1 < 13:
        score -= 3

    rsi4 = float(r4["rsi"])
    if direction == "LONG":
        if 48 <= rsi4 <= 72:
            score += 5
        elif rsi4 >= 80 or rsi4 <= 40:
            score -= 5
            reasons.append("4H RSI trend devamı için sağlıksız")
    else:
        if 28 <= rsi4 <= 52:
            score += 5
        elif rsi4 <= 20 or rsi4 >= 62:
            score -= 5
            reasons.append("4H RSI trend devamı için sağlıksız")

    atr4 = float(r4["atr"])
    ema20_4 = float(r4["ema20"])
    close4 = float(r4["close"])
    atr_distance = abs(close4 - ema20_4) / atr4 if atr4 > 0 else 0.0
    if atr_distance >= 2.8:
        score -= 7
        reasons.append("4H fiyat EMA20'den aşırı uzak; olgun/geç trend")
    elif atr_distance >= 2.0:
        score -= 3

    origin_price, origin_at, origin_method = _trend_origin(df4h, direction)
    move_from_origin = _directional_percent(direction, origin_price, current_price)
    life_hours = max(0.0, (int(now_ts) - int(origin_at)) / 3600.0)

    score = max(0, min(100, int(round(score))))
    if score >= 78 and life_hours <= 16:
        status = STATUS_START
    elif score >= 78:
        status = STATUS_STRONG
    elif score >= 64:
        status = STATUS_CONTINUE
    elif score >= 50:
        status = STATUS_WEAK
    else:
        status = STATUS_EXIT

    confidence_points = sum(
        (
            int(trend4 > 0),
            int(trend1 > 0),
            int(structure4 > 0),
            int(adx4 >= 20),
            int(adx1 >= 18),
        )
    )
    confidence = "YUKSEK" if confidence_points >= 4 else "ORTA" if confidence_points >= 2 else "DUSUK"

    return {
        "version": VERSION,
        "mode": MODE,
        "status": status,
        "score": score,
        "confidence": confidence,
        "direction": direction,
        "current_price": round(float(current_price), 12),
        "trend_origin": {
            "price": round(float(origin_price), 12),
            "at": int(origin_at),
            "method": origin_method,
            "move_so_far_percent": round(float(move_from_origin), 4),
            "life_hours": round(float(life_hours), 2),
        },
        "metrics": {
            "trend_1h": trend1,
            "trend_4h": trend4,
            "structure_4h": structure4,
            "adx_1h": round(adx1, 2),
            "adx_4h": round(adx4, 2),
            "rsi_4h": round(rsi4, 2),
            "atr_distance_from_4h_ema20": round(atr_distance, 3),
        },
        "reasons": reasons[:8],
    }


def _movement_candidates(path: str, now: int) -> List[Dict[str, Any]]:
    state = _load(path)
    rows = state.get("open") if isinstance(state.get("open"), dict) else {}
    candidates: List[Dict[str, Any]] = []
    for record in rows.values():
        if not isinstance(record, dict):
            continue
        symbol = str(record.get("symbol") or "").upper()
        direction = str(record.get("direction") or "").upper()
        stage = str(record.get("best_stage") or record.get("initial_stage") or "").upper()
        score = int(_sf(record.get("best_score"), _sf(record.get("initial_score"), 0)) or 0)
        updated_at = int(record.get("last_updated_at") or record.get("started_at") or 0)
        if not symbol or direction not in {"LONG", "SHORT"} or updated_at <= 0:
            continue
        if now - updated_at > DISCOVERY_MAX_AGE_SECONDS:
            continue
        if stage in {"ARMED", "TRIGGER"} and score >= DISCOVERY_MIN_ARMED_SCORE:
            pass
        elif stage == "PREP" and score >= DISCOVERY_MIN_PREP_SCORE:
            pass
        else:
            continue
        candidates.append({
            "symbol": symbol,
            "direction": direction,
            "stage": stage,
            "base_score": score,
            "movement_started_at": int(record.get("started_at") or updated_at),
            "movement_entry": _sf(record.get("entry")),
            "last_candidate_at": updated_at,
        })
    candidates.sort(
        key=lambda row: (
            2 if row["stage"] == "TRIGGER" else 1 if row["stage"] == "ARMED" else 0,
            int(row["base_score"]),
            int(row["last_candidate_at"]),
        ),
        reverse=True,
    )
    return candidates


def _history_item(snapshot: Dict[str, Any], at: int) -> Dict[str, Any]:
    return {
        "at": int(at),
        "status": snapshot.get("status"),
        "score": snapshot.get("score"),
        "confidence": snapshot.get("confidence"),
        "price": snapshot.get("current_price"),
        "move_from_origin_percent": (snapshot.get("trend_origin") or {}).get("move_so_far_percent"),
        "life_hours": (snapshot.get("trend_origin") or {}).get("life_hours"),
    }


def monitor(
    exchange: Any,
    state_file: str = STATE_FILE,
    movement_file: str = MOVEMENT_FILE,
    *,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    state = _load(state_file)
    if not state:
        state = _default_state()
    records = state.get("records") if isinstance(state.get("records"), dict) else {}

    for candidate in _movement_candidates(movement_file, now):
        key = f"{candidate['symbol']}_{candidate['direction']}"
        current = records.get(key)
        if not isinstance(current, dict):
            current = {
                "symbol": candidate["symbol"],
                "direction": candidate["direction"],
                "enrolled_at": now,
                "movement_started_at": candidate.get("movement_started_at"),
                "movement_entry": candidate.get("movement_entry"),
                "initial_stage": candidate.get("stage"),
                "initial_base_score": candidate.get("base_score"),
                "best_base_score": candidate.get("base_score"),
                "last_candidate_at": candidate.get("last_candidate_at"),
                "last_checked_at": 0,
                "status": "PENDING_4H_CHECK",
                "history": [],
                "checkpoints": {},
                "shadow_only": True,
            }
            records[key] = current
        else:
            current["last_candidate_at"] = max(
                int(current.get("last_candidate_at") or 0),
                int(candidate.get("last_candidate_at") or 0),
            )
            current["best_base_score"] = max(
                int(current.get("best_base_score") or 0),
                int(candidate.get("base_score") or 0),
            )
            if candidate.get("stage") == "TRIGGER":
                current["best_stage"] = "TRIGGER"
            elif not current.get("best_stage"):
                current["best_stage"] = candidate.get("stage")

    active: List[Tuple[str, Dict[str, Any]]] = []
    for key, record in list(records.items()):
        if not isinstance(record, dict):
            records.pop(key, None)
            continue
        enrolled_at = int(record.get("enrolled_at") or now)
        age_hours = max(0.0, (now - enrolled_at) / 3600.0)
        if age_hours > MAX_TRACK_HOURS:
            record["status"] = "COMPLETED_96H"
            record["completed_at"] = record.get("completed_at") or now
            continue
        last_checked = int(record.get("last_checked_at") or 0)
        if last_checked > 0 and now - last_checked < MIN_RECHECK_SECONDS:
            continue
        active.append((key, record))

    active.sort(
        key=lambda pair: (
            int(pair[1].get("last_checked_at") or 0),
            -int(pair[1].get("best_base_score") or 0),
        )
    )
    active = active[:MAX_CHECKS_PER_RUN]

    checked = 0
    changed = 0
    statuses: Counter[str] = Counter()

    for key, record in active:
        symbol = str(record.get("symbol") or "").upper()
        direction = str(record.get("direction") or "").upper()
        try:
            rows1 = exchange.fetch_ohlcv(
                _to_okx_symbol(symbol), timeframe="1h", limit=FETCH_1H_LIMIT
            )
            rows4 = exchange.fetch_ohlcv(
                _to_okx_symbol(symbol), timeframe="4h", limit=FETCH_4H_LIMIT
            )
        except Exception as exc:
            print(symbol, "çok günlük trend veri hatası:", exc)
            continue

        f1 = _frame(rows1)
        f4 = _frame(rows4)
        if f1 is None or f4 is None:
            continue
        current_price = float(f1.iloc[-1]["close"])
        try:
            snapshot = evaluate_frames(
                direction, f1, f4, current_price, now_ts=now
            )
        except Exception as exc:
            print(symbol, "çok günlük trend değerlendirme hatası:", exc)
            continue

        checked += 1
        record["last_checked_at"] = now
        record["status"] = snapshot["status"]
        record["score"] = snapshot["score"]
        record["confidence"] = snapshot["confidence"]
        record["current_price"] = snapshot["current_price"]
        record["trend_origin"] = snapshot["trend_origin"]
        record["metrics"] = snapshot["metrics"]
        record["reasons"] = snapshot["reasons"]

        move = float((snapshot.get("trend_origin") or {}).get("move_so_far_percent") or 0.0)
        record["best_directional_move_percent"] = max(
            float(record.get("best_directional_move_percent") or 0.0), move
        )

        history = record.get("history") if isinstance(record.get("history"), list) else []
        if (
            not history
            or str(history[-1].get("status")) != str(snapshot.get("status"))
            or now - int(history[-1].get("at") or 0) >= 6 * 3600
        ):
            history.append(_history_item(snapshot, now))
            record["history"] = history[-MAX_HISTORY_PER_RECORD:]

        checkpoints = record.get("checkpoints") if isinstance(record.get("checkpoints"), dict) else {}
        enrolled_at = int(record.get("enrolled_at") or now)
        for hour in CHECKPOINT_HOURS:
            if str(hour) in checkpoints:
                continue
            if now >= enrolled_at + hour * 3600:
                checkpoints[str(hour)] = {
                    "hour": int(hour),
                    "at": now,
                    "status": snapshot.get("status"),
                    "score": snapshot.get("score"),
                    "price": snapshot.get("current_price"),
                    "move_from_origin_percent": move,
                }
        record["checkpoints"] = checkpoints
        changed += 1
        statuses[str(snapshot.get("status") or "UNKNOWN")] += 1

        print(
            "4H TREND LIFE SHADOW:",
            symbol,
            direction,
            snapshot.get("status"),
            "score=",
            snapshot.get("score"),
            "life_h=",
            (snapshot.get("trend_origin") or {}).get("life_hours"),
            "move%=",
            move,
        )

    if len(records) > MAX_ACTIVE_RECORDS:
        ordered = sorted(
            records.items(),
            key=lambda pair: int(pair[1].get("enrolled_at") or 0),
            reverse=True,
        )[:MAX_ACTIVE_RECORDS]
        records = dict(ordered)

    state["version"] = VERSION
    state["mode"] = MODE
    state["updated_at"] = now
    state["records"] = records
    state["summary"] = {
        "checked_this_run": checked,
        "changed_this_run": changed,
        "tracked_records": len(records),
        "statuses_this_run": dict(statuses),
        "max_track_hours": MAX_TRACK_HOURS,
        "recheck_minutes": int(MIN_RECHECK_SECONDS / 60),
    }
    _atomic_save(state_file, state)
    return state["summary"]


def main() -> None:
    try:
        import ccxt
        exchange = ccxt.okx({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        result = monitor(exchange)
        print("4H çok günlük trend shadow özet:", result)
    except Exception as exc:
        print("4H çok günlük trend shadow ana hata:", exc)


if __name__ == "__main__":
    main()
