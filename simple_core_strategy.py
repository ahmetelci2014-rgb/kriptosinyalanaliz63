"""Simple Core V1 strategy.

Live decision model:
1H trend direction -> 15M support/resistance rejection -> 5M trigger.
No score maze, no pending queue, no experimental live routes.
This module never places exchange orders.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

import pandas as pd

import strategy as indicators

VERSION = "SIMPLE_CORE_V1_2026_08_28"
SOURCE = "SIMPLE_CORE_V1"

MIN_1H_ADX = 15.0
MIN_5M_VOLUME_RATIO = 0.90
MIN_RISK_PERCENT = 0.35
MAX_RISK_PERCENT = 1.80
MIN_ROOM_R = 2.0

TP1_R = 0.75
TP2_R = 1.25
TP3_R = 2.00

SWING_LOOKBACK_15M = 48
OPPOSING_LOOKBACK_15M = 72


def _safe(value: Any, default: float = 0.0) -> float:
    return indicators.safe_float(value, default)


def _pct_distance(price: float, level: float) -> float:
    return indicators.percent_distance(price, level)


def _local_levels(
    frame: pd.DataFrame,
    mode: str,
    lookback: int = SWING_LOOKBACK_15M,
) -> list[float]:
    """Return simple local swing lows/highs from closed candles only."""
    closed = frame.iloc[:-1].tail(max(lookback, 12)).reset_index(drop=True)
    if len(closed) < 7:
        return []

    values: list[float] = []
    for i in range(2, len(closed) - 2):
        if mode == "LOW":
            value = _safe(closed.iloc[i]["low"])
            window = [_safe(v) for v in closed["low"].iloc[i - 2 : i + 3]]
            if value > 0 and value <= min(window):
                values.append(value)
        else:
            value = _safe(closed.iloc[i]["high"])
            window = [_safe(v) for v in closed["high"].iloc[i - 2 : i + 3]]
            if value > 0 and value >= max(window):
                values.append(value)

    compact: list[float] = []
    for value in reversed(values):
        if not compact or all(_pct_distance(value, item) > 0.08 for item in compact):
            compact.append(value)
    return list(reversed(compact))


def _one_hour_direction(
    df1h: Optional[pd.DataFrame],
) -> Tuple[Optional[str], str, Dict[str, float]]:
    frame = indicators.add_indicators(df1h)
    if frame is None or len(frame) < 30:
        return None, "1H_DATA", {}

    last = frame.iloc[-2]
    past = frame.iloc[-5]

    close = _safe(last["close"])
    ema20 = _safe(last["ema20"])
    ema50 = _safe(last["ema50"])
    ema20_past = _safe(past["ema20"])
    rsi = _safe(last["rsi"])
    adx = _safe(last["adx"])

    info = {
        "adx_1h": round(adx, 2),
        "rsi_1h": round(rsi, 2),
        "ema_gap_percent": round(
            abs(ema20 - ema50) / close * 100 if close > 0 else 0.0,
            4,
        ),
    }

    if (
        close > ema50
        and ema20 > ema50
        and ema20 > ema20_past
        and 45.0 <= rsi <= 70.0
        and adx >= MIN_1H_ADX
    ):
        return "LONG", "1H yükseliş trendi", info

    if (
        close < ema50
        and ema20 < ema50
        and ema20 < ema20_past
        and 30.0 <= rsi <= 55.0
        and adx >= MIN_1H_ADX
    ):
        return "SHORT", "1H düşüş trendi", info

    return None, "1H_DIRECTION", info


def _adaptive_zone_limit(atr_percent: float) -> float:
    return round(min(0.75, max(0.30, 0.25 + 0.45 * atr_percent)), 4)


def _find_zone(
    direction: str,
    frame15: pd.DataFrame,
    entry: float,
    atr_percent: float,
) -> Tuple[Optional[float], float, str]:
    levels = _local_levels(
        frame15,
        "LOW" if direction == "LONG" else "HIGH",
        SWING_LOOKBACK_15M,
    )
    if not levels:
        return None, 999.0, "NO_SWING"

    if direction == "LONG":
        candidates = [level for level in levels if level <= entry * 1.0025]
        label = "15M swing destek"
    else:
        candidates = [level for level in levels if level >= entry * 0.9975]
        label = "15M swing direnç"

    if not candidates:
        return None, 999.0, "NO_SIDE_LEVEL"

    zone = min(candidates, key=lambda level: _pct_distance(entry, level))
    distance = _pct_distance(entry, zone)
    if distance > _adaptive_zone_limit(atr_percent):
        return None, distance, "ZONE_FAR"

    return zone, distance, label


def _fifteen_minute_rejection(
    direction: str,
    frame15: pd.DataFrame,
    zone: float,
    atr_percent: float,
) -> Tuple[bool, str]:
    last = frame15.iloc[-2]
    prev = frame15.iloc[-3]

    tolerance_percent = min(0.45, max(0.15, atr_percent * 0.35))
    tolerance = zone * tolerance_percent / 100.0

    last_open = _safe(last["open"])
    last_close = _safe(last["close"])
    last_high = _safe(last["high"])
    last_low = _safe(last["low"])
    prev_open = _safe(prev["open"])
    prev_close = _safe(prev["close"])
    prev_high = _safe(prev["high"])
    prev_low = _safe(prev["low"])

    if direction == "LONG":
        touched = min(last_low, prev_low) <= zone + tolerance
        bullish = last_close > last_open and last_close > prev_close
        rejection = (
            indicators.lower_wick_percent(last) >= 18.0
            or last_close >= (last_low + (last_high - last_low) * 0.60)
            or (last_close >= prev_open and last_open <= prev_close)
        )
        return bool(touched and bullish and rejection), "15M destekten bullish reddedilme"

    touched = max(last_high, prev_high) >= zone - tolerance
    bearish = last_close < last_open and last_close < prev_close
    rejection = (
        indicators.upper_wick_percent(last) >= 18.0
        or last_close <= (last_low + (last_high - last_low) * 0.40)
        or (last_close <= prev_open and last_open >= prev_close)
    )
    return bool(touched and bearish and rejection), "15M dirençten bearish reddedilme"


def _five_minute_trigger(
    direction: str,
    df5m: Optional[pd.DataFrame],
) -> Tuple[bool, str, Dict[str, float]]:
    frame = indicators.add_indicators(df5m)
    if frame is None or len(frame) < 30:
        return False, "5M_DATA", {}

    last = frame.iloc[-2]
    prior = frame.iloc[-5:-2]
    if prior.empty:
        return False, "5M_DATA", {}

    open_price = _safe(last["open"])
    close = _safe(last["close"])
    volume_ratio = _safe(last["volume_ratio"])
    close_power = indicators.close_power_percent(last)
    rsi = _safe(last["rsi"])

    if direction == "LONG":
        trigger_level = _safe(prior["high"].max())
        ok = (
            close > open_price
            and close > trigger_level
            and close_power >= 58.0
            and volume_ratio >= MIN_5M_VOLUME_RATIO
        )
        reason = "5M önceki 3 mum tepesini bullish kırdı"
    else:
        trigger_level = _safe(prior["low"].min())
        ok = (
            close < open_price
            and close < trigger_level
            and close_power <= 42.0
            and volume_ratio >= MIN_5M_VOLUME_RATIO
        )
        reason = "5M önceki 3 mum dibini bearish kırdı"

    return bool(ok), reason, {
        "volume_5m": round(volume_ratio, 2),
        "rsi_5m": round(rsi, 2),
        "close_power_5m": round(close_power, 1),
        "trigger_level_5m": round(trigger_level, 10),
    }


def _opposing_level(
    direction: str,
    frame15: pd.DataFrame,
    entry: float,
) -> Optional[float]:
    levels = _local_levels(
        frame15,
        "HIGH" if direction == "LONG" else "LOW",
        OPPOSING_LOOKBACK_15M,
    )
    if direction == "LONG":
        candidates = [level for level in levels if level > entry]
        return min(candidates) if candidates else None

    candidates = [level for level in levels if level < entry]
    return max(candidates) if candidates else None


def _targets_and_room(
    direction: str,
    frame15: pd.DataFrame,
    entry: float,
    zone: float,
    atr: float,
) -> Tuple[Optional[Dict[str, float]], str]:
    closed = frame15.iloc[:-1].tail(12)
    if closed.empty or entry <= 0 or atr <= 0:
        return None, "RISK_DATA"

    if direction == "LONG":
        local_low = _safe(closed["low"].min())
        sl = min(zone - atr * 0.15, local_low - atr * 0.05)
        risk = entry - sl
    else:
        local_high = _safe(closed["high"].max())
        sl = max(zone + atr * 0.15, local_high + atr * 0.05)
        risk = sl - entry

    if risk <= 0:
        return None, "RISK_GEOMETRY"

    risk_percent = risk / entry * 100.0
    if not (MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT):
        return None, "RISK_RANGE"

    opposing = _opposing_level(direction, frame15, entry)
    room_r = None
    if opposing is not None:
        room = (opposing - entry) if direction == "LONG" else (entry - opposing)
        room_r = room / risk if risk > 0 else 0.0
        if room_r < MIN_ROOM_R:
            return None, "NO_2R_ROOM"

    if direction == "LONG":
        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R
    else:
        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    if min(tp1, tp2, tp3, sl) <= 0:
        return None, "RISK_GEOMETRY"

    return {
        "sl": round(sl, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "risk_percent": round(risk_percent, 3),
        "rr_tp1": TP1_R,
        "rr_tp2": TP2_R,
        "rr_tp3": TP3_R,
        "room_r": round(room_r, 2) if room_r is not None else 99.0,
        "opposing_level": round(opposing, 10) if opposing is not None else None,
    }, "OK"


def analyze_simple_trade(
    symbol: str,
    df5m: Optional[pd.DataFrame],
    df15m: Optional[pd.DataFrame],
    df1h: Optional[pd.DataFrame],
    current_price: Optional[float],
) -> Tuple[Optional[Dict[str, Any]], str]:
    direction, trend_reason, trend_info = _one_hour_direction(df1h)
    if direction is None:
        return None, trend_reason

    frame15 = indicators.add_indicators(df15m)
    if frame15 is None or len(frame15) < 40:
        return None, "15M_DATA"

    last15 = frame15.iloc[-2]
    entry = _safe(current_price) if _safe(current_price) > 0 else _safe(last15["close"])
    atr = _safe(last15["atr"])
    if entry <= 0 or atr <= 0:
        return None, "15M_DATA"

    atr_percent = atr / entry * 100.0
    zone, zone_distance, zone_name = _find_zone(
        direction,
        frame15,
        entry,
        atr_percent,
    )
    if zone is None:
        return None, zone_name

    rejection_ok, rejection_reason = _fifteen_minute_rejection(
        direction,
        frame15,
        zone,
        atr_percent,
    )
    if not rejection_ok:
        return None, "15M_NO_REJECTION"

    trigger_ok, trigger_reason, trigger_info = _five_minute_trigger(
        direction,
        df5m,
    )
    if not trigger_ok:
        return None, trigger_reason if trigger_reason == "5M_DATA" else "5M_NO_CONFIRM"

    targets, risk_reason = _targets_and_room(
        direction,
        frame15,
        entry,
        zone,
        atr,
    )
    if targets is None:
        return None, risk_reason

    adx_1h = _safe(trend_info.get("adx_1h"))
    volume_5m = _safe(trigger_info.get("volume_5m"))
    score = 80
    if adx_1h >= 25:
        score += 5
    if zone_distance <= 0.25:
        score += 5
    if volume_5m >= 1.30:
        score += 5
    if _safe(targets.get("room_r")) >= 3.0:
        score += 5
    score = min(100, score)

    quality = "A+ SADE" if score >= 95 else "A SADE"

    signal: Dict[str, Any] = {
        "symbol": symbol,
        "direction": direction,
        "source": SOURCE,
        "signal_class": "TRADE",
        "entry": round(entry, 10),
        "ideal_entry": round(zone, 10),
        "zone_distance_percent": round(zone_distance, 3),
        "zone_name": zone_name,
        "tp1": targets["tp1"],
        "tp2": targets["tp2"],
        "tp3": targets["tp3"],
        "sl": targets["sl"],
        "risk_percent": targets["risk_percent"],
        "rr_tp1": targets["rr_tp1"],
        "rr_tp2": targets["rr_tp2"],
        "rr_tp3": targets["rr_tp3"],
        "score": score,
        "quality": quality,
        "quality_note": (
            f"1H yön + 15M destek/direnç reddi + 5M kırılım. "
            f"Karşı seviyeye alan: {targets['room_r']}R."
        ),
        "trend_reason": trend_reason,
        "confirm_reason": "15M bölge ve dönüş teyidi",
        "entry_reason": rejection_reason,
        "radar_reason": trigger_reason,
        "rsi_15m": round(_safe(last15["rsi"]), 2),
        "adx_15m": round(_safe(last15["adx"]), 2),
        "adx_1h": trend_info.get("adx_1h", "-"),
        "adx_4h": "-",
        "volume_ratio": round(_safe(last15["volume_ratio"]), 2),
        "volume_5m": trigger_info.get("volume_5m"),
        "rsi_5m": trigger_info.get("rsi_5m"),
        "room_r": targets["room_r"],
        "opposing_level": targets["opposing_level"],
        "simple_core_version": VERSION,
        "adaptive_zone_limit_percent": _adaptive_zone_limit(atr_percent),
        "atr_15m_percent": round(atr_percent, 3),
    }
    signal["leverage"] = indicators.leverage_suggestion(signal["risk_percent"])
    return signal, "OK"
