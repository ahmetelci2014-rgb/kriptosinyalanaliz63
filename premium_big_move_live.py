"""Premium Big Move live route.

Goal: detect the beginning of POL/GRASS/SPK-style large directional moves while
there is still meaningful trend available. The route reuses Movement Start V2
as the micro trigger, then adds classic trend-following evidence used globally:

- multi-horizon time-series momentum / EMA alignment,
- Donchian-style 1H/4H range breakout,
- volatility contraction -> expansion,
- ADX trend-strength confirmation,
- volume expansion,
- optional OKX order-flow confirmation,
- ATR-normalized anti-chase protection.

No exchange orders are placed. A promoted candidate still passes all existing
Premium market, quality, duplicate, portfolio, cost and ledger gates.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange

import strategy

VERSION = "PREMIUM_BIG_MOVE_LIVE_V1_2026_08_24"
SOURCE = "BIG_MOVE_ENTRY"
STATE_FILE = "premium_big_move_state.json"

MIN_BASE_SCORE = 76
MIN_DIRECTION_GAP = 12
MIN_LIVE_SCORE = 94
MIN_RISK_PERCENT = 0.45
MAX_RISK_PERCENT = 2.60
MAX_BREAK_EXTENSION_ATR = 1.25
MAX_ORIGIN_MOVE_PERCENT = 12.0
MAX_ZONE_DISTANCE_PERCENT = 0.45
MIN_VOLUME_RATIO = 1.10
MIN_VOLUME_WAKE = 1.08
MAX_RECORDS = 1800
KEEP_SECONDS = 21 * 24 * 60 * 60

_STATE: Dict[str, Any] = {}
_STATE_PATH = STATE_FILE
_DIRTY = False


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".premium_big_move.",
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


def begin(path: str = STATE_FILE) -> None:
    global _STATE, _STATE_PATH, _DIRTY
    _STATE_PATH = path
    _DIRTY = False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data.setdefault("version", VERSION)
    data.setdefault("updated_at", 0)
    data.setdefault("records", [])
    data.setdefault("summary", {})
    data["version"] = VERSION
    _STATE = data


def _state() -> Dict[str, Any]:
    global _STATE
    if not _STATE:
        begin()
    return _STATE


def _enrich(df: Any, min_len: int = 55) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy"):
        return None
    frame = df.copy()
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(frame.columns)):
        return None
    for col in required:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    if len(frame) < min_len:
        return None
    frame["ema20"] = EMAIndicator(frame["close"], window=20).ema_indicator()
    frame["ema50"] = EMAIndicator(frame["close"], window=50).ema_indicator()
    frame["rsi"] = RSIIndicator(frame["close"], window=14).rsi()
    frame["adx"] = ADXIndicator(frame["high"], frame["low"], frame["close"], window=14).adx()
    frame["atr"] = AverageTrueRange(frame["high"], frame["low"], frame["close"], window=14).average_true_range()
    frame["ema20_slope"] = frame["ema20"] - frame["ema20"].shift(3)
    volume_avg = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / volume_avg.replace(0, float("nan"))
    frame = frame.dropna().reset_index(drop=True)
    return frame if len(frame) >= 25 else None


def _closed(frame: pd.DataFrame) -> pd.DataFrame:
    # Last row can still be forming; decisions use only completed candles.
    return frame.iloc[:-1].copy().reset_index(drop=True)


def _direction_points(frame: pd.DataFrame, direction: str) -> Dict[str, Any]:
    closed = _closed(frame)
    row = closed.iloc[-1]
    prev = closed.iloc[-2]
    sign = 1.0 if direction == "LONG" else -1.0
    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    slope = float(row["ema20_slope"])
    rsi = float(row["rsi"])
    adx = float(row["adx"])
    points = sum(
        [
            int(sign * (close - ema20) > 0),
            int(sign * slope > 0),
            int(sign * (close - float(prev["close"])) > 0),
            int(rsi >= 50.0 if direction == "LONG" else rsi <= 50.0),
            int(sign * (ema20 - ema50) > 0),
            int(adx >= 18.0),
        ]
    )
    hard_opposing = bool(
        sign * (close - ema20) < 0
        and sign * (ema20 - ema50) < 0
        and sign * slope < 0
        and (rsi < 40.0 if direction == "LONG" else rsi > 60.0)
    )
    return {
        "points": points,
        "hard_opposing": hard_opposing,
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "slope": slope,
        "rsi": rsi,
        "adx": adx,
        "atr": float(row["atr"]),
    }


def _channel_profile(frame: pd.DataFrame, direction: str, current_price: float) -> Dict[str, Any]:
    closed = _closed(frame)
    if len(closed) < 15:
        return {"ok": False}
    latest = closed.iloc[-1]
    reference = closed.iloc[-13:-1]
    if reference.empty:
        return {"ok": False}
    atr = max(float(latest["atr"]), 1e-12)
    if direction == "LONG":
        level = float(reference["high"].max())
        extension_atr = (current_price - level) / atr
        accepted = current_price >= level - 0.12 * atr
    else:
        level = float(reference["low"].min())
        extension_atr = (level - current_price) / atr
        accepted = current_price <= level + 0.12 * atr
    zone_distance = abs(current_price - level) / level * 100.0 if level > 0 else 999.0
    return {
        "ok": bool(accepted),
        "level": level,
        "extension_atr": extension_atr,
        "zone_distance_percent": zone_distance,
        "atr": atr,
    }


def _compression_profile(frame: pd.DataFrame) -> Dict[str, Any]:
    closed = _closed(frame)
    if len(closed) < 16:
        return {"ratio": 1.0, "compressed": False}
    recent = float(closed.iloc[-4:]["atr"].mean())
    older = float(closed.iloc[-12:-4]["atr"].mean())
    ratio = recent / older if older > 0 else 1.0
    return {"ratio": ratio, "compressed": ratio <= 0.96}


def _recent_origin(frame4: pd.DataFrame, direction: str) -> float:
    closed = _closed(frame4).tail(12)
    if closed.empty:
        return 0.0
    return float(closed["low"].min() if direction == "LONG" else closed["high"].max())


def _directional_percent(direction: str, start: float, end: float) -> float:
    if min(start, end) <= 0:
        return 0.0
    raw = (end - start) / start * 100.0
    return raw if direction == "LONG" else -raw


def _normalize_flow(snapshot: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {"available": False, "score": None, "confirmed": False}
    return {
        "available": True,
        "score": int(_sf(snapshot.get("orderflow_score"), 0) or 0),
        "confirmed": bool(snapshot.get("orderflow_confirmed")),
    }


def _big_targets(direction: str, entry: float, stop: float) -> Optional[Dict[str, Any]]:
    risk = entry - stop if direction == "LONG" else stop - entry
    if entry <= 0 or stop <= 0 or risk <= 0:
        return None
    risk_percent = risk / entry * 100.0
    if not (MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT):
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    return {
        "sl": stop,
        "tp1": entry + sign * 0.80 * risk,
        "tp2": entry + sign * 1.60 * risk,
        "tp3": entry + sign * 3.00 * risk,
        "rr_tp1": 0.80,
        "rr_tp2": 1.60,
        "rr_tp3": 3.00,
        "risk_percent": risk_percent,
    }


def _structure_stop(direction: str, base_result: Dict[str, Any], frame15: pd.DataFrame) -> float:
    base_stop = _sf(base_result.get("stop"), 0.0) or 0.0
    closed = _closed(frame15)
    recent = closed.tail(8)
    row = closed.iloc[-1]
    atr = max(float(row["atr"]), 1e-12)
    if direction == "LONG":
        structural = float(recent["low"].min()) - 0.10 * atr
        return min(base_stop, structural) if base_stop > 0 else structural
    structural = float(recent["high"].max()) + 0.10 * atr
    return max(base_stop, structural) if base_stop > 0 else structural


def _score(
    *,
    stage: str,
    base_score: int,
    gap: int,
    p1: int,
    p4: int,
    channel1: Dict[str, Any],
    channel4: Dict[str, Any],
    compression4: Dict[str, Any],
    volume_ratio: float,
    volume_wake: float,
    origin_move: float,
    flow: Dict[str, Any],
) -> int:
    score = 74
    score += 5 if stage == "TRIGGER" else 3
    score += min(6, max(0, (base_score - 76) // 3))
    score += 3 if gap >= 25 else (2 if gap >= 18 else 1)
    score += 4 if p1 >= 5 else (3 if p1 >= 4 else 1)
    score += 4 if p4 >= 5 else (3 if p4 >= 4 else (1 if p4 >= 3 else 0))
    score += 5 if channel1.get("ok") else 0
    score += 2 if channel4.get("ok") else 0
    score += 2 if compression4.get("compressed") else 0
    score += 3 if volume_ratio >= 1.80 else (2 if volume_ratio >= 1.30 else 1)
    score += 2 if volume_wake >= 1.30 else (1 if volume_wake >= 1.10 else 0)
    score += 2 if origin_move <= 4.0 else (1 if origin_move <= 8.0 else 0)
    if flow.get("confirmed"):
        score += 5
    elif flow.get("available"):
        flow_score = int(flow.get("score") or 0)
        score += 2 if flow_score >= 60 else (1 if flow_score >= 45 else 0)
    perfect = bool(stage == "TRIGGER" and base_score >= 94 and flow.get("confirmed") and p1 >= 5 and p4 >= 4)
    return max(0, min(100 if perfect else 99, int(score)))


def _record(symbol: str, direction: str, decision: str, reason: str, evidence: Dict[str, Any], now: int) -> None:
    global _DIRTY
    state = _state()
    rows = state.setdefault("records", [])
    rows.append(
        {
            "at": now,
            "symbol": str(symbol or "").upper(),
            "direction": direction,
            "decision": decision,
            "reason": reason,
            "evidence": evidence,
        }
    )
    cutoff = now - KEEP_SECONDS
    rows[:] = [row for row in rows if int(row.get("at") or 0) >= cutoff][-MAX_RECORDS:]
    _DIRTY = True


def analyze_live_candidate(
    symbol: str,
    base_result: Optional[Dict[str, Any]],
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any,
    flow_snapshot: Optional[Dict[str, Any]] = None,
    *,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if not isinstance(base_result, dict):
        return None
    now = int(now_ts if now_ts is not None else time.time())
    symbol = str(symbol or base_result.get("symbol") or "").upper()
    direction = str(base_result.get("direction") or "").upper()
    stage = str(base_result.get("stage") or "").upper()
    base_score = int(_sf(base_result.get("score"), 0) or 0)
    opposite = int(_sf(base_result.get("opposite_score"), 0) or 0)
    gap = base_score - opposite
    price = _sf(current_price, _sf(base_result.get("entry"), 0.0)) or 0.0

    evidence: Dict[str, Any] = {
        "stage": stage,
        "base_score": base_score,
        "opposite_score": opposite,
        "direction_gap": gap,
    }
    if direction not in {"LONG", "SHORT"}:
        return None
    if stage not in {"ARMED", "TRIGGER"}:
        _record(symbol, direction, "REJECT", "PREP_HENUZ_CANLI_BUYUK_HAREKET_DEGIL", evidence, now)
        return None
    if base_score < MIN_BASE_SCORE or gap < MIN_DIRECTION_GAP or price <= 0:
        _record(symbol, direction, "REJECT", "MIKRO_YON_GUCU_YETERSIZ", evidence, now)
        return None

    f15 = _enrich(df15m, 55)
    f1 = _enrich(df1h, 55)
    f4 = _enrich(df4h, 55)
    if f15 is None or f1 is None or f4 is None:
        _record(symbol, direction, "REJECT", "UST_ZAMAN_VERISI_YETERSIZ", evidence, now)
        return None

    state1 = _direction_points(f1, direction)
    state4 = _direction_points(f4, direction)
    channel1 = _channel_profile(f1, direction, price)
    channel4 = _channel_profile(f4, direction, price)
    compression4 = _compression_profile(f4)
    features = base_result.get("features") if isinstance(base_result.get("features"), dict) else {}
    volume_ratio = _sf(features.get("volume_ratio"), 0.0) or 0.0
    volume_wake = _sf(features.get("volume_wake"), 0.0) or 0.0
    origin = _recent_origin(f4, direction)
    origin_move = max(0.0, _directional_percent(direction, origin, price)) if origin > 0 else 0.0
    flow = _normalize_flow(flow_snapshot)

    evidence.update(
        {
            "support_1h": state1,
            "support_4h": state4,
            "channel_1h": channel1,
            "channel_4h": channel4,
            "compression_4h": compression4,
            "volume_ratio_5m": round(volume_ratio, 4),
            "volume_wake_5m": round(volume_wake, 4),
            "origin_price": origin,
            "origin_move_percent": round(origin_move, 4),
            "flow": flow,
        }
    )

    if state1.get("points", 0) < 4:
        _record(symbol, direction, "REJECT", "1H_TREND_BASLANGICI_YETERSIZ", evidence, now)
        return None
    if state4.get("hard_opposing") or state4.get("points", 0) < 2:
        _record(symbol, direction, "REJECT", "4H_GUCLU_TERS_REJIM", evidence, now)
        return None
    if not channel1.get("ok"):
        _record(symbol, direction, "REJECT", "1H_KANAL_KIRILIMI_YOK", evidence, now)
        return None

    extension = _sf(channel1.get("extension_atr"), 99.0) or 99.0
    zone_distance = _sf(channel1.get("zone_distance_percent"), 999.0) or 999.0
    if extension > MAX_BREAK_EXTENSION_ATR or zone_distance > MAX_ZONE_DISTANCE_PERCENT:
        _record(symbol, direction, "REJECT", "BUYUK_HAREKET_KACMIS", evidence, now)
        return None
    if origin_move > MAX_ORIGIN_MOVE_PERCENT:
        _record(symbol, direction, "REJECT", "4H_ORIGIN_UZAK_GEC_GIRIS", evidence, now)
        return None
    if volume_ratio < MIN_VOLUME_RATIO and volume_wake < MIN_VOLUME_WAKE:
        _record(symbol, direction, "REJECT", "HACIM_GENISLEMESI_YOK", evidence, now)
        return None
    if flow.get("available") and int(flow.get("score") or 0) < 20 and not flow.get("confirmed"):
        _record(symbol, direction, "REJECT", "ORDERFLOW_GUCLU_TERS", evidence, now)
        return None

    stop = _structure_stop(direction, base_result, f15)
    targets = _big_targets(direction, price, stop)
    if not isinstance(targets, dict):
        evidence["stop"] = stop
        _record(symbol, direction, "REJECT", "BIG_MOVE_RISK_DISI", evidence, now)
        return None

    live_score = _score(
        stage=stage,
        base_score=base_score,
        gap=gap,
        p1=int(state1.get("points") or 0),
        p4=int(state4.get("points") or 0),
        channel1=channel1,
        channel4=channel4,
        compression4=compression4,
        volume_ratio=volume_ratio,
        volume_wake=volume_wake,
        origin_move=origin_move,
        flow=flow,
    )
    evidence["live_score"] = live_score
    evidence["risk_percent"] = round(float(targets["risk_percent"]), 4)
    if live_score < MIN_LIVE_SCORE:
        _record(symbol, direction, "REJECT", "BIG_MOVE_SKOR_YETERSIZ", evidence, now)
        return None

    candidate = {
        "symbol": symbol,
        "direction": direction,
        "source": SOURCE,
        "signal_class": "TRADE",
        "entry": round(price, 12),
        "ideal_entry": round(float(channel1["level"]), 12),
        "zone_name": "1H Donchian / 4H trend-start breakout",
        "zone_distance_percent": round(zone_distance, 4),
        "tp1": round(float(targets["tp1"]), 12),
        "tp2": round(float(targets["tp2"]), 12),
        "tp3": round(float(targets["tp3"]), 12),
        "sl": round(float(targets["sl"]), 12),
        "risk_percent": round(float(targets["risk_percent"]), 4),
        "rr_tp1": targets["rr_tp1"],
        "rr_tp2": targets["rr_tp2"],
        "rr_tp3": targets["rr_tp3"],
        "score": live_score,
        "quality": "A+ BÜYÜK HAREKET" if live_score >= 97 else "A BÜYÜK HAREKET",
        "quality_note": "Movement Start + 1H/4H breakout + momentum + volatility/hacim birleşimi.",
        "leverage": "1x-2x" if float(targets["risk_percent"]) > 1.25 else "2x",
        "trend_reason": f"1H destek {state1['points']}/6 • 4H destek {state4['points']}/6",
        "confirm_reason": f"{stage} {base_score}/100 • 1H kanal kırılımı • 4H trend başlangıcı",
        "entry_reason": f"Kırılım uzaması {extension:.2f} ATR • origin %{origin_move:.2f}",
        "big_move_version": VERSION,
        "big_move_stage": stage,
        "big_move_base_score": base_score,
        "big_move_opposite_score": opposite,
        "big_move_direction_gap": gap,
        "big_move_1h_points": state1["points"],
        "big_move_4h_points": state4["points"],
        "big_move_break_level": round(float(channel1["level"]), 12),
        "big_move_break_extension_atr": round(float(extension), 4),
        "big_move_origin_price": round(float(origin), 12),
        "big_move_origin_move_percent": round(float(origin_move), 4),
        "big_move_4h_compression_ratio": round(float(compression4.get("ratio") or 1.0), 4),
        "big_move_flow_score": flow.get("score"),
        "big_move_flow_confirmed": bool(flow.get("confirmed")),
        "volume_ratio": round(volume_ratio, 3),
    }
    _record(symbol, direction, "PROMOTE", "LIVE_BIG_MOVE_START", evidence, now)
    return candidate


def strong_direct_allowed(
    signal: Dict[str, Any],
    current_price: Any,
    base_validator: Callable[..., Any],
    profit_module: Any,
) -> bool:
    if str(signal.get("source") or "").upper() != SOURCE:
        return False
    if int(_sf(signal.get("score"), 0) or 0) < MIN_LIVE_SCORE:
        return False
    risk = _sf(signal.get("risk_percent"), 999.0) or 999.0
    if not (MIN_RISK_PERCENT <= risk <= MAX_RISK_PERCENT):
        return False
    try:
        result = base_validator(signal, current_price)
        ok = bool(result[0] if isinstance(result, tuple) else result)
    except Exception:
        return False
    if not ok:
        return False
    try:
        return bool(profit_module.cost_viability(signal).get("ok"))
    except Exception:
        return False


def make_trade_message_builder(original: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(signal: Dict[str, Any], current_price: Any = None, portfolio_risk: Any = None) -> str:
        if str(signal.get("source") or "").upper() != SOURCE:
            return original(signal, current_price=current_price, portfolio_risk=portfolio_risk)
        direction = str(signal.get("direction") or "").upper()
        icon = "🟢" if direction == "LONG" else "🔴"
        return (
            "🚀 PREMIUM BÜYÜK HAREKET\n"
            f"{icon} {direction} | {signal.get('symbol')}\n"
            f"Giriş: {strategy.format_price(float(signal['entry']))}\n"
            f"TP1: {strategy.format_price(float(signal['tp1']))}\n"
            f"TP2: {strategy.format_price(float(signal['tp2']))}\n"
            f"TP3: {strategy.format_price(float(signal['tp3']))}\n"
            f"SL: {strategy.format_price(float(signal['sl']))}"
        )
    return wrapped


def finish() -> Dict[str, Any]:
    global _DIRTY
    state = _state()
    rows = state.get("records") if isinstance(state.get("records"), list) else []
    decisions = Counter(str(row.get("decision") or "UNKNOWN") for row in rows)
    reasons = Counter(
        str(row.get("reason") or "UNKNOWN")
        for row in rows
        if str(row.get("decision") or "") == "REJECT"
    )
    summary = {
        "version": VERSION,
        "records": len(rows),
        "promoted": decisions.get("PROMOTE", 0),
        "rejected": decisions.get("REJECT", 0),
        "top_reject_reasons": dict(reasons.most_common(12)),
    }
    state["version"] = VERSION
    state["updated_at"] = int(time.time())
    state["summary"] = summary
    if _DIRTY or not os.path.exists(_STATE_PATH):
        _atomic_save(_STATE_PATH, state)
        _DIRTY = False
    return summary
