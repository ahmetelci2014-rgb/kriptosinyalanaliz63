"""Shadow monitor for open-trade continuation strength.

Purpose
-------
Estimate whether an already-open Premium trade still has directional life left.
This module never sends Telegram messages, never opens/closes exchange orders,
and never mutates TP/SL/BE rules. It only enriches trade_ledger.json with a
continuation snapshot that can later be calibrated against real outcomes.

The estimate is intentionally heuristic in V1. Numeric "remaining move" bands
are ATR-based shadow estimates, not probabilities or guarantees.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange

VERSION = "POSITION_CONTINUATION_SHADOW_V1_2026_08_24"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_EXIT_MUTATION"

MAX_OPEN_TO_CHECK = 6
MIN_RECHECK_SECONDS = 4 * 60
HISTORY_INTERVAL_SECONDS = 15 * 60
MAX_HISTORY = 48

FETCH_LIMIT_5M = 100
FETCH_LIMIT_15M = 120
FETCH_LIMIT_1H = 100

STATUS_STRONG = "DEVAM_GUCLU"
STATUS_CONTINUE = "DEVAM"
STATUS_WEAK = "ZAYIFLIYOR"
STATUS_EXIT = "CIKIS_RISKI"

ACTION_HOLD = "HOLD_SHADOW"
ACTION_PROTECT = "PROTECT_PROFIT_SHADOW"
ACTION_EXIT_WATCH = "EXIT_WATCH_SHADOW"


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _load_json(path: str) -> Dict[str, Any]:
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
            prefix=".continuation_shadow.",
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


def _to_okx_symbol(symbol: str) -> str:
    raw = str(symbol or "").upper().strip()
    base = raw[:-4] if raw.endswith("USDT") else raw
    return f"{base}/USDT:USDT"


def _ohlcv_frame(rows: Any) -> Optional[pd.DataFrame]:
    if not isinstance(rows, list) or len(rows) < 35:
        return None
    try:
        frame = pd.DataFrame(
            rows,
            columns=["time", "open", "high", "low", "close", "volume"],
        )
        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["time"] = pd.to_numeric(frame["time"], errors="coerce")
        frame = frame.dropna().reset_index(drop=True)
        return frame if len(frame) >= 35 else None
    except Exception:
        return None


def _enrich(frame: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if frame is None or len(frame) < 35:
        return None
    data = frame.copy()
    data["ema20"] = EMAIndicator(data["close"], window=20).ema_indicator()
    data["ema50"] = EMAIndicator(data["close"], window=50).ema_indicator()
    data["rsi"] = RSIIndicator(data["close"], window=14).rsi()
    data["adx"] = ADXIndicator(
        data["high"],
        data["low"],
        data["close"],
        window=14,
    ).adx()
    data["atr"] = AverageTrueRange(
        data["high"],
        data["low"],
        data["close"],
        window=14,
    ).average_true_range()
    data["ema20_slope"] = data["ema20"] - data["ema20"].shift(3)
    data["volume_avg"] = data["volume"].rolling(20).mean()
    data["volume_ratio"] = data["volume"] / data["volume_avg"]
    data = data.dropna().reset_index(drop=True)
    return data if len(data) >= 12 else None


def _fetch(exchange: Any, symbol: str, timeframe: str, limit: int) -> Optional[pd.DataFrame]:
    try:
        rows = exchange.fetch_ohlcv(
            _to_okx_symbol(symbol),
            timeframe=timeframe,
            limit=limit,
        )
        return _enrich(_ohlcv_frame(rows))
    except Exception as exc:
        print(symbol, timeframe, "devam gücü veri hatası:", exc)
        return None


def _dir_sign(direction: str) -> int:
    return 1 if str(direction).upper() == "LONG" else -1


def _trend_state(row: pd.Series, direction: str) -> int:
    """Return +1 aligned, 0 mixed, -1 opposed."""
    sign = _dir_sign(direction)
    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    slope = float(row["ema20_slope"])

    aligned = (
        sign * (close - ema20) > 0
        and sign * (ema20 - ema50) >= 0
        and sign * slope > 0
    )
    opposed = (
        sign * (close - ema20) < 0
        and sign * slope < 0
    )
    if aligned:
        return 1
    if opposed:
        return -1
    return 0


def _structure_state(frame: pd.DataFrame, direction: str) -> int:
    """Three-closed-bar directional structure: +1 / 0 / -1."""
    if len(frame) < 6:
        return 0
    rows = frame.iloc[-5:-1]
    highs = [float(value) for value in rows["high"]]
    lows = [float(value) for value in rows["low"]]
    rising = highs[-1] > highs[-2] and lows[-1] > lows[-2]
    falling = highs[-1] < highs[-2] and lows[-1] < lows[-2]
    if direction == "LONG":
        return 1 if rising else -1 if falling else 0
    return 1 if falling else -1 if rising else 0


def _trend_origin(frame: pd.DataFrame, direction: str) -> Tuple[float, int, str]:
    """Approximate 15M directional regime origin from the latest EMA20 cross."""
    closed = frame.iloc[:-1].reset_index(drop=True)
    if len(closed) < 12:
        row = closed.iloc[-1]
        return float(row["close"]), int(row["time"] // 1000), "FALLBACK_LAST"

    start = max(4, len(closed) - 72)
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
            pos = window["low"].astype(float).idxmin()
            origin = float(closed.loc[pos, "low"])
        else:
            pos = window["high"].astype(float).idxmax()
            origin = float(closed.loc[pos, "high"])
        origin_time = int(float(closed.loc[pos, "time"]) // 1000)
        return origin, origin_time, "EMA20_REGIME_CROSS"

    window = closed.iloc[-40:]
    if direction == "LONG":
        pos = window["low"].astype(float).idxmin()
        origin = float(closed.loc[pos, "low"])
    else:
        pos = window["high"].astype(float).idxmax()
        origin = float(closed.loc[pos, "high"])
    origin_time = int(float(closed.loc[pos, "time"]) // 1000)
    return origin, origin_time, "RECENT_15M_SWING"


def _directional_percent(direction: str, start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    raw = (end - start) / start * 100.0
    return raw if direction == "LONG" else -raw


def evaluate_frames(
    direction: str,
    entry: float,
    f5: pd.DataFrame,
    f15: pd.DataFrame,
    f1: pd.DataFrame,
) -> Dict[str, Any]:
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if min(len(f5), len(f15), len(f1)) < 6:
        raise ValueError("not enough enriched candles")

    r5 = f5.iloc[-2]
    r15 = f15.iloc[-2]
    r1 = f1.iloc[-2]
    current_price = float(f5.iloc[-1]["close"])

    trend5 = _trend_state(r5, direction)
    trend15 = _trend_state(r15, direction)
    trend1h = _trend_state(r1, direction)
    structure = _structure_state(f5, direction)

    score = 50
    reasons = []

    for label, state, weight in (
        ("5M", trend5, 10),
        ("15M", trend15, 18),
        ("1H", trend1h, 10),
    ):
        score += state * weight
        if state > 0:
            reasons.append(f"{label} trend yönü destekliyor")
        elif state < 0:
            reasons.append(f"{label} trend yönün tersine dönüyor")

    score += structure * 6
    if structure > 0:
        reasons.append("5M mikro yapı devam yönünde")
    elif structure < 0:
        reasons.append("5M mikro yapı ters kırılıyor")

    adx15 = float(r15["adx"])
    adx1h = float(r1["adx"])
    if adx15 >= 28:
        score += 7
        reasons.append("15M ADX güçlü")
    elif adx15 >= 20:
        score += 4
    elif adx15 < 15:
        score -= 5
        reasons.append("15M trend gücü zayıf")

    if adx1h >= 25:
        score += 5
    elif adx1h >= 18:
        score += 2
    elif adx1h < 14:
        score -= 4

    adx15_prev = float(f15.iloc[-5]["adx"])
    if adx15_prev - adx15 >= 5:
        score -= 4
        reasons.append("15M ADX belirgin güç kaybediyor")

    rsi15 = float(r15["rsi"])
    if direction == "LONG":
        if 50 <= rsi15 <= 70:
            score += 5
        elif rsi15 >= 76 or rsi15 <= 43:
            score -= 5
            reasons.append("15M RSI devam için sağlıksız bölgede")
    else:
        if 30 <= rsi15 <= 50:
            score += 5
        elif rsi15 <= 24 or rsi15 >= 57:
            score -= 5
            reasons.append("15M RSI devam için sağlıksız bölgede")

    vol5 = float(r5["volume_ratio"])
    vol15 = float(r15["volume_ratio"])
    if vol5 >= 1.50:
        score += 5
        reasons.append("5M hacim devamı destekliyor")
    elif vol5 >= 1.15:
        score += 2
    elif vol5 < 0.65:
        score -= 3
        reasons.append("5M hacim sönüyor")

    if vol15 >= 1.20:
        score += 3
    elif vol15 < 0.65:
        score -= 2

    score = max(0, min(100, int(round(score))))

    if score >= 80:
        status = STATUS_STRONG
        action = ACTION_HOLD
        low_factor, high_factor = 1.20, 3.20
    elif score >= 64:
        status = STATUS_CONTINUE
        action = ACTION_HOLD
        low_factor, high_factor = 0.70, 2.00
    elif score >= 48:
        status = STATUS_WEAK
        action = ACTION_PROTECT
        low_factor, high_factor = 0.20, 0.80
    else:
        status = STATUS_EXIT
        action = ACTION_EXIT_WATCH
        low_factor, high_factor = 0.00, 0.35

    atr15 = float(r15["atr"])
    atr15_pct = atr15 / float(r15["close"]) * 100.0 if float(r15["close"]) > 0 else 0.0
    strength_boost = 1.15 if (vol5 >= 1.50 and adx15 >= 25) else 1.0
    remaining_low = max(0.0, atr15_pct * low_factor)
    remaining_high = max(remaining_low, atr15_pct * high_factor * strength_boost)
    remaining_high = min(12.0, remaining_high)

    origin_price, origin_at, origin_method = _trend_origin(f15, direction)
    move_from_origin = _directional_percent(direction, origin_price, current_price)
    move_from_entry = _directional_percent(direction, entry, current_price)

    confidence_points = sum(
        (
            int(trend5 > 0),
            int(trend15 > 0),
            int(trend1h > 0),
            int(structure > 0),
            int(adx15 >= 20),
            int(vol5 >= 1.15),
        )
    )
    confidence = "YUKSEK" if confidence_points >= 5 else "ORTA" if confidence_points >= 3 else "DUSUK"

    return {
        "version": VERSION,
        "mode": MODE,
        "status": status,
        "score": score,
        "action_shadow": action,
        "confidence": confidence,
        "current_price": round(current_price, 12),
        "move_from_entry_percent": round(move_from_entry, 4),
        "trend_origin": {
            "price": round(origin_price, 12),
            "at": origin_at,
            "method": origin_method,
            "move_so_far_percent": round(move_from_origin, 4),
        },
        "remaining_move_shadow": {
            "low_percent": round(remaining_low, 4),
            "high_percent": round(remaining_high, 4),
            "method": "15M_ATR_HEURISTIC_UNCALIBRATED",
            "estimated_total_move_low_percent": round(max(0.0, move_from_origin) + remaining_low, 4),
            "estimated_total_move_high_percent": round(max(0.0, move_from_origin) + remaining_high, 4),
        },
        "metrics": {
            "trend_5m": trend5,
            "trend_15m": trend15,
            "trend_1h": trend1h,
            "structure_5m": structure,
            "adx_15m": round(adx15, 2),
            "adx_1h": round(adx1h, 2),
            "rsi_15m": round(rsi15, 2),
            "volume_ratio_5m": round(vol5, 3),
            "volume_ratio_15m": round(vol15, 3),
            "atr_15m_percent": round(atr15_pct, 4),
        },
        "reasons": reasons[:8],
    }


def _history_item(snapshot: Dict[str, Any], now: int) -> Dict[str, Any]:
    return {
        "at": now,
        "status": snapshot.get("status"),
        "score": snapshot.get("score"),
        "action_shadow": snapshot.get("action_shadow"),
        "current_price": snapshot.get("current_price"),
        "move_from_entry_percent": snapshot.get("move_from_entry_percent"),
        "origin_move_percent": (
            snapshot.get("trend_origin") or {}
        ).get("move_so_far_percent"),
        "remaining_move_shadow": snapshot.get("remaining_move_shadow"),
    }


def _should_append_history(current: Dict[str, Any], snapshot: Dict[str, Any], now: int) -> bool:
    history = current.get("history") if isinstance(current.get("history"), list) else []
    if not history:
        return True
    last = history[-1] if isinstance(history[-1], dict) else {}
    if str(last.get("status")) != str(snapshot.get("status")):
        return True
    return now - int(last.get("at") or 0) >= HISTORY_INTERVAL_SECONDS


def monitor_open_positions(
    exchange: Any,
    open_signals_file: str = "open_signals.json",
    ledger_file: str = "trade_ledger.json",
    *,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    """Evaluate each open Premium trade and persist shadow continuation state."""
    now = int(now_ts if now_ts is not None else time.time())
    open_signals = _load_json(open_signals_file)
    ledger = _load_json(ledger_file)
    trades = ledger.get("trades") if isinstance(ledger.get("trades"), dict) else {}

    if not open_signals or not trades:
        return {"checked": 0, "changed": 0, "statuses": {}}

    candidates = []
    for signal in open_signals.values():
        if not isinstance(signal, dict):
            continue
        if bool(signal.get("closed", False)):
            continue
        trade_id = str(signal.get("trade_id") or "")
        if not trade_id or trade_id not in trades:
            continue
        existing = trades[trade_id].get("continuation_shadow")
        if isinstance(existing, dict):
            updated_at = int(existing.get("updated_at") or 0)
            if updated_at > 0 and now - updated_at < MIN_RECHECK_SECONDS:
                continue
        candidates.append(signal)

    candidates.sort(
        key=lambda item: int(item.get("opened_at") or 0),
        reverse=True,
    )
    candidates = candidates[:MAX_OPEN_TO_CHECK]

    checked = 0
    changed = 0
    status_counter: Counter[str] = Counter()

    for signal in candidates:
        symbol = str(signal.get("symbol") or "").upper()
        direction = str(signal.get("direction") or "").upper()
        entry = _sf(signal.get("entry"), 0.0) or 0.0
        trade_id = str(signal.get("trade_id") or "")
        if not symbol or direction not in {"LONG", "SHORT"} or entry <= 0:
            continue

        f5 = _fetch(exchange, symbol, "5m", FETCH_LIMIT_5M)
        f15 = _fetch(exchange, symbol, "15m", FETCH_LIMIT_15M)
        f1 = _fetch(exchange, symbol, "1h", FETCH_LIMIT_1H)
        if f5 is None or f15 is None or f1 is None:
            continue

        try:
            snapshot = evaluate_frames(direction, entry, f5, f15, f1)
        except Exception as exc:
            print(symbol, "devam gücü değerlendirme hatası:", exc)
            continue

        checked += 1
        snapshot["updated_at"] = now
        snapshot["trade_id"] = trade_id
        snapshot["symbol"] = symbol
        snapshot["direction"] = direction
        snapshot["source"] = signal.get("source")
        snapshot["shadow_only"] = True

        trade = trades[trade_id]
        previous = trade.get("continuation_shadow")
        history = []
        if isinstance(previous, dict) and isinstance(previous.get("history"), list):
            history = list(previous.get("history") or [])

        holder = dict(snapshot)
        holder["history"] = history

        if _should_append_history({"history": history}, snapshot, now):
            history.append(_history_item(snapshot, now))
            holder["history"] = history[-MAX_HISTORY:]

        if isinstance(previous, dict):
            holder["peak_score"] = max(
                int(previous.get("peak_score") or previous.get("score") or 0),
                int(snapshot.get("score") or 0),
            )
            prior_min = int(previous.get("min_score") or previous.get("score") or 100)
            holder["min_score"] = min(prior_min, int(snapshot.get("score") or 0))
            holder["status_changed"] = (
                str(previous.get("status") or "") != str(snapshot.get("status") or "")
            )
        else:
            holder["peak_score"] = int(snapshot.get("score") or 0)
            holder["min_score"] = int(snapshot.get("score") or 0)
            holder["status_changed"] = True

        trade["continuation_shadow"] = holder
        changed += 1
        status_counter[str(snapshot.get("status") or "UNKNOWN")] += 1

        print(
            "DEVAM GÜCÜ SHADOW:",
            symbol,
            direction,
            snapshot.get("status"),
            "score=",
            snapshot.get("score"),
            "origin_move%=",
            (snapshot.get("trend_origin") or {}).get("move_so_far_percent"),
            "remaining%=",
            (
                (snapshot.get("remaining_move_shadow") or {}).get("low_percent"),
                (snapshot.get("remaining_move_shadow") or {}).get("high_percent"),
            ),
        )

    if changed:
        ledger["trades"] = trades
        ledger["continuation_shadow_summary"] = {
            "version": VERSION,
            "mode": MODE,
            "updated_at": now,
            "checked": checked,
            "statuses": dict(status_counter),
        }
        _atomic_save(ledger_file, ledger)

    return {
        "checked": checked,
        "changed": changed,
        "statuses": dict(status_counter),
        "version": VERSION,
    }
