"""Premium V4 trend-continuation route.

Uses recent Pump/Dump shadow evidence only as an internal confirmation input.
It never sends Telegram messages and never opens exchange orders by itself.
A live candidate is produced only when current 1H/4H structure still supports
that shadow move, the entry has not drifted, risk is controlled, and costs pass.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, Optional

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange

VERSION = "PREMIUM_TREND_CONTINUATION_V1_2026_08_20"
PUMP_STATE_FILE = "pump_radar_state.json"
SOURCE = "TREND_CONTINUATION"

MAX_EVENT_AGE_SECONDS = 35 * 60
MIN_15M_MOVE_PERCENT = 0.55
MAX_15M_MOVE_PERCENT = 1.85
MAX_EVENT_DRIFT_PERCENT = 0.45
MIN_5M_VOLUME_RATIO = 1.35
MAX_EMA20_DISTANCE_PERCENT = 0.80
MIN_SCORE = 94
MIN_RISK_PERCENT = 0.35
MAX_RISK_PERCENT = 1.80
STOP_BUFFER_PERCENT = 0.15
TP1_R = 0.55
TP2_R = 1.05
TP3_R = 1.60


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


def _latest_event(symbol: str, state_file: str, now: int) -> Optional[Dict[str, Any]]:
    data = _load_json(state_file)
    rows = data.get("shadow_moves") or []
    if not isinstance(rows, list):
        return None
    wanted = str(symbol or "").upper()
    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").upper() != wanted:
            continue
        recorded_at = int(_sf(row.get("recorded_at"), 0) or 0)
        if recorded_at <= 0 or now - recorded_at > MAX_EVENT_AGE_SECONDS:
            return None
        return row
    return None


def _frame(df: Any) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy") or len(df) < 60:
        return None
    frame = df.copy()
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(frame.columns)):
        return None
    for col in needed:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    if len(frame) < 60:
        return None
    frame["ema20"] = EMAIndicator(frame["close"], window=20).ema_indicator()
    frame["ema50"] = EMAIndicator(frame["close"], window=50).ema_indicator()
    frame["rsi"] = RSIIndicator(frame["close"], window=14).rsi()
    frame["adx"] = ADXIndicator(frame["high"], frame["low"], frame["close"], window=14).adx()
    frame["atr"] = AverageTrueRange(frame["high"], frame["low"], frame["close"], window=14).average_true_range()
    frame["ema20_slope"] = frame["ema20"] - frame["ema20"].shift(3)
    frame["volume_avg"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_avg"]
    frame = frame.dropna().reset_index(drop=True)
    return frame if len(frame) >= 10 else None


def _mtf_allows(direction: str, f15: pd.DataFrame, f1: pd.DataFrame, f4: pd.DataFrame) -> bool:
    r15 = f15.iloc[-2]
    r1 = f1.iloc[-2]
    r4 = f4.iloc[-2]

    if direction == "LONG":
        one_hour = (
            r1["close"] > r1["ema20"] > r1["ema50"]
            and r1["ema20_slope"] > 0
            and r1["adx"] >= 18
            and 45 <= r1["rsi"] <= 72
        )
        four_hour_not_opposing = (
            r4["close"] > r4["ema20"]
            or (r4["ema20"] >= r4["ema50"] and r4["ema20_slope"] >= 0)
        )
        entry_alive = (
            r15["close"] > r15["ema20"]
            and r15["ema20_slope"] > 0
            and 48 <= r15["rsi"] <= 72
            and r15["adx"] >= 16
        )
    else:
        one_hour = (
            r1["close"] < r1["ema20"] < r1["ema50"]
            and r1["ema20_slope"] < 0
            and r1["adx"] >= 18
            and 28 <= r1["rsi"] <= 55
        )
        four_hour_not_opposing = (
            r4["close"] < r4["ema20"]
            or (r4["ema20"] <= r4["ema50"] and r4["ema20_slope"] <= 0)
        )
        entry_alive = (
            r15["close"] < r15["ema20"]
            and r15["ema20_slope"] < 0
            and 28 <= r15["rsi"] <= 55
            and r15["adx"] >= 16
        )

    return bool(one_hour and four_hour_not_opposing and entry_alive)


def _score(event: Dict[str, Any], f15: pd.DataFrame, f1: pd.DataFrame, direction: str) -> int:
    score = 88
    vol5 = _sf(event.get("vol5"), 0.0) or 0.0
    distance = abs(_sf(event.get("ema20_distance_percent"), 999.0) or 999.0)
    move15 = abs(_sf(event.get("move15_percent"), 0.0) or 0.0)
    if bool(event.get("shadow_ready")):
        score += 3
    if bool(event.get("resume_confirmed")):
        score += 2
    if vol5 >= 2.0:
        score += 3
    elif vol5 >= 1.5:
        score += 2
    if distance <= 0.40:
        score += 2
    elif distance <= 0.60:
        score += 1
    if move15 >= 0.85:
        score += 1
    r15 = f15.iloc[-2]
    r1 = f1.iloc[-2]
    if r15["adx"] >= 25:
        score += 1
    if r1["adx"] >= 25:
        score += 1
    directional_count = int(_sf(event.get("green_5m_count" if direction == "LONG" else "red_5m_count"), 0) or 0)
    if directional_count >= 3:
        score += 1
    return min(100, score)


def analyze_continuation(
    symbol: str,
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any = None,
    *,
    state_file: str = PUMP_STATE_FILE,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    now = int(now_ts if now_ts is not None else time.time())
    event = _latest_event(symbol, state_file, now)
    if event is None:
        return None

    direction = str(event.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None

    shadow_ready = bool(event.get("shadow_ready"))
    resume = bool(event.get("resume_confirmed"))
    directional_count = int(_sf(event.get("green_5m_count" if direction == "LONG" else "red_5m_count"), 0) or 0)
    if not shadow_ready and not (resume and directional_count >= 3):
        return None

    move15 = _sf(event.get("move15_percent"), 0.0) or 0.0
    if direction == "LONG" and not (MIN_15M_MOVE_PERCENT <= move15 <= MAX_15M_MOVE_PERCENT):
        return None
    if direction == "SHORT" and not (-MAX_15M_MOVE_PERCENT <= move15 <= -MIN_15M_MOVE_PERCENT):
        return None

    vol5 = _sf(event.get("vol5"), 0.0) or 0.0
    event_distance = abs(_sf(event.get("ema20_distance_percent"), 999.0) or 999.0)
    if vol5 < MIN_5M_VOLUME_RATIO or event_distance > MAX_EMA20_DISTANCE_PERCENT:
        return None

    rsi5 = _sf(event.get("rsi5"), 50.0) or 50.0
    if direction == "LONG" and not (50 <= rsi5 <= 69):
        return None
    if direction == "SHORT" and not (31 <= rsi5 <= 50):
        return None

    event_price = _sf(event.get("price"))
    ema20_event = _sf(event.get("ema20"))
    if not event_price or not ema20_event or event_price <= 0 or ema20_event <= 0:
        return None
    if direction == "LONG" and event_price <= ema20_event:
        return None
    if direction == "SHORT" and event_price >= ema20_event:
        return None

    f15, f1, f4 = _frame(df15m), _frame(df1h), _frame(df4h)
    if f15 is None or f1 is None or f4 is None:
        return None
    if not _mtf_allows(direction, f15, f1, f4):
        return None

    entry = _sf(current_price, _sf(f15.iloc[-1]["close"]))
    if not entry or entry <= 0:
        return None
    drift = abs(entry - event_price) / event_price * 100.0
    if drift > MAX_EVENT_DRIFT_PERCENT:
        return None

    current_ema_distance = abs(entry - ema20_event) / ema20_event * 100.0
    if current_ema_distance > MAX_EMA20_DISTANCE_PERCENT:
        return None

    atr15 = _sf(f15.iloc[-2]["atr"], 0.0) or 0.0
    if atr15 <= 0:
        return None
    if direction == "LONG":
        sl = min(ema20_event * (1.0 - STOP_BUFFER_PERCENT / 100.0), entry - 0.60 * atr15)
        risk = entry - sl
    else:
        sl = max(ema20_event * (1.0 + STOP_BUFFER_PERCENT / 100.0), entry + 0.60 * atr15)
        risk = sl - entry
    if risk <= 0:
        return None
    risk_percent = risk / entry * 100.0
    if not (MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT):
        return None

    score = _score(event, f15, f1, direction)
    if score < MIN_SCORE:
        return None

    if direction == "LONG":
        tp1, tp2, tp3 = entry + risk * TP1_R, entry + risk * TP2_R, entry + risk * TP3_R
    else:
        tp1, tp2, tp3 = entry - risk * TP1_R, entry - risk * TP2_R, entry - risk * TP3_R
    if min(tp1, tp2, tp3, sl) <= 0:
        return None

    quality = "A+ TREND DEVAM" if score >= 98 else "A TREND DEVAM"
    icon = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"
    message = (
        "🚀 PREMIUM TREND DEVAM SİNYALİ\n\n"
        f"{icon}\n"
        f"🟡 Coin: {symbol}\n"
        "⏱️ Kaynak: TREND_CONTINUATION\n\n"
        f"📌 Giriş: {entry:.10g}\n"
        f"📍 Trend EMA20: {ema20_event:.10g}\n"
        f"🎯 TP1: {tp1:.10g}\n"
        f"🎯 TP2: {tp2:.10g}\n"
        f"🎯 TP3: {tp3:.10g}\n"
        f"🛑 SL: {sl:.10g}\n\n"
        f"📊 Skor: {score}/100 ({quality})\n"
        f"📈 5M hacim: {vol5:.2f}x | RSI5: {rsi5:.1f}\n"
        f"⚡ 15M hareket: %{move15:+.2f} | EMA uzaklık: %{current_ema_distance:.2f}\n"
        f"🛡️ Stop mesafesi: %{risk_percent:.2f}\n\n"
        "✅ Pump gölge devam teyidi + güncel 1H/4H yapı birlikte doğrulandı.\n"
        "⚠️ Fiyat girişten belirgin uzaklaştıysa peşinden koşma."
    )

    return {
        "symbol": str(symbol or "").upper(),
        "direction": direction,
        "source": SOURCE,
        "signal_class": "TRADE",
        "entry": round(entry, 12),
        "ideal_entry": round(ema20_event, 12),
        "zone_distance_percent": round(current_ema_distance, 3),
        "zone_name": "5M EMA20 trend devam",
        "tp1": round(tp1, 12),
        "tp2": round(tp2, 12),
        "tp3": round(tp3, 12),
        "sl": round(sl, 12),
        "risk_percent": round(risk_percent, 3),
        "rr_tp1": TP1_R,
        "rr_tp2": TP2_R,
        "rr_tp3": TP3_R,
        "score": score,
        "rsi_15m": round(float(f15.iloc[-2]["rsi"]), 2),
        "adx_15m": round(float(f15.iloc[-2]["adx"]), 2),
        "volume_ratio": round(float(f15.iloc[-2]["volume_ratio"]), 2),
        "adx_4h": round(float(f4.iloc[-2]["adx"]), 2),
        "adx_1h": round(float(f1.iloc[-2]["adx"]), 2),
        "quality": quality,
        "quality_note": "Klasik pullback kalıbı oluşmadan güçlü trend devamı; Pump gölge teyidi ve güncel MTF yapı ile doğrulandı.",
        "leverage": "1x-2x",
        "trend_reason": "4H ters değil + 1H trend devam yönünde",
        "confirm_reason": f"Pump shadow_ready={shadow_ready} | resume={resume} | 5M hacim {vol5:.2f}x",
        "entry_reason": f"Trend devamı; olaydan fiyat sapması %{drift:.2f}",
        "radar_reason": "IOTA tipi devam hareketlerini klasik pullback filtresinde kaybetmemek için kontrollü Premium yolu",
        "continuation_version": VERSION,
        "continuation_event_at": int(_sf(event.get("recorded_at"), 0) or 0),
        "message": message,
    }


def strong_direct_allowed(signal: Dict[str, Any], current_price: Any, base_validator: Any, profit_module: Any) -> bool:
    if str(signal.get("source") or "").upper() != SOURCE:
        return False
    if int(_sf(signal.get("score"), 0) or 0) < MIN_SCORE:
        return False
    if str(signal.get("signal_class") or "").upper() != "TRADE":
        return False
    risk = _sf(signal.get("risk_percent"), 999.0) or 999.0
    zone = abs(_sf(signal.get("zone_distance_percent"), 999.0) or 999.0)
    if not (MIN_RISK_PERCENT <= risk <= MAX_RISK_PERCENT) or zone > MAX_EMA20_DISTANCE_PERCENT:
        return False
    ok, _ = base_validator(signal, current_price)
    return bool(ok and profit_module.cost_viability(signal).get("ok"))
