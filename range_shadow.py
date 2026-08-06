"""
Range Cycle Shadow v3 Diagnostic — bağımsız destek/direnç bant döngü gölge motoru.

Güvenlik:
- Ana bot, Scalp, Swing, Pump/Dump ve Yeni Liste sistemlerini import etmez.
- Telegram mesajı göndermez.
- OKX hesabına bağlanmaz, API anahtarı kullanmaz ve emir açmaz.
- Yalnız herkese açık piyasa verisini okuyarak sanal işlemler üretir.
- Sonuçları yalnız range_shadow.json dosyasına atomik biçimde yazar.

V3 Diagnostic:
- Giriş, hedef, stop ve timeout mantığını değiştirmez.
- Stop mesafesi/risk bandı, maliyet-R oranı ve giriş uyarıları kaydeder.
- Kapanışları teşhis nedenleriyle sınıflandırır.
- LONG/SHORT ve risk bandı bazında Gross R / Net R özeti üretir.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import tempfile
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import ccxt
except ImportError:
    ccxt = None

try:
    import pandas as pd
    from ta.momentum import RSIIndicator
    from ta.trend import ADXIndicator, EMAIndicator
    from ta.volatility import AverageTrueRange
except ImportError:
    pd = None
    RSIIndicator = None
    ADXIndicator = None
    EMAIndicator = None
    AverageTrueRange = None


VERSION = "RANGE_CYCLE_SHADOW_V3_DIAGNOSTIC_2026_08_06"
LEDGER_FILE = "range_shadow.json"

TIMEFRAME = "5m"
OHLCV_LIMIT = 190
RANGE_WINDOW = 48
MAX_SCAN_COINS = 80
MAX_NEW_POSITIONS_PER_RUN = 3
MAX_OPEN_POSITIONS = 6
MAX_CLOSED_RECORDS = 2500
MAX_CANDIDATES_SAVED = 30
MAX_HOLD_MINUTES = 360
DUPLICATE_COOLDOWN_MINUTES = 90

MIN_QUOTE_VOLUME_USDT = 3_000_000.0
MIN_RANGE_WIDTH_PERCENT = 0.55
MAX_RANGE_WIDTH_PERCENT = 4.50
MAX_ADX_5M = 25.0
MAX_ADX_15M = 28.0
MAX_EMA_SPREAD_5M_PERCENT = 1.20
MAX_EMA_SPREAD_15M_PERCENT = 2.00
MIN_CONTAINMENT_RATIO = 0.84
MIN_TOUCHES_PER_SIDE = 2
ZONE_WIDTH_RATIO = 0.08
ZONE_ATR_MULTIPLIER = 0.45
ENTRY_ZONE_FRACTION = 0.24
MIN_VOLUME_RATIO = 0.45
MAX_VOLUME_RATIO = 2.30
MIN_EXPECTED_TARGET_R = 1.10
BTC_HARD_MOVE_PERCENT = 1.20

# Taker ücretleri, spread ve kayma için ihtiyatlı toplam tahmin.
ESTIMATED_ROUND_TRIP_COST_PERCENT = 0.12

RISK_BANDS = (
    (0.20, "LT_0_20"),
    (0.35, "0_20_TO_0_35"),
    (0.60, "0_35_TO_0_60"),
    (float("inf"), "GTE_0_60"),
)


def now_ts() -> int:
    return int(time.time())


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def normalize_symbol(symbol: Any) -> str:
    value = str(symbol or "").upper().strip()
    return (
        value.replace("/", "")
        .replace(":", "")
        .replace("-", "")
        .replace("_", "")
        .replace(" ", "")
    )


def symbol_to_okx(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    if not normalized.endswith("USDT"):
        raise ValueError(f"USDT perpetual sembolü değil: {symbol}")
    base = normalized[:-4]
    if not base:
        raise ValueError(f"Geçersiz sembol: {symbol}")
    return f"{base}/USDT:USDT"


def load_json(filename: str, default: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fallback = copy.deepcopy(default) if isinstance(default, dict) else {}
    if not filename or not os.path.exists(filename):
        return fallback
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else fallback
    except Exception as exc:
        print(filename, "okuma hatası:", exc)
        return fallback


def save_json_atomically(filename: str, data: Dict[str, Any]) -> bool:
    directory = os.path.dirname(os.path.abspath(filename)) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(filename)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(temp_path, "r", encoding="utf-8") as verify:
            verified = json.load(verify)
        if not isinstance(verified, dict):
            raise ValueError("Range gölge ledger kökü sözlük değil.")

        os.replace(temp_path, filename)
        temp_path = None
        return True
    except Exception as exc:
        print(filename, "atomik yazma hatası:", exc)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def risk_band(risk_percent: float) -> str:
    value = max(0.0, safe_float(risk_percent))
    for upper, label in RISK_BANDS:
        if value < upper:
            return label
    return "UNKNOWN"


def cost_r_from_risk_percent(risk_percent: float) -> float:
    value = safe_float(risk_percent)
    if value <= 0:
        return 0.0
    return ESTIMATED_ROUND_TRIP_COST_PERCENT / value


def build_entry_diagnostics(candidate: Dict[str, Any]) -> Dict[str, Any]:
    risk_pct = safe_float(candidate.get("risk_percent"))
    cost_r = cost_r_from_risk_percent(risk_pct)
    confirmations = list(candidate.get("confirmation") or [])
    adx_5m = safe_float(candidate.get("adx_5m"))
    adx_15m = safe_float(candidate.get("adx_15m"))
    volume_ratio = safe_float(candidate.get("volume_ratio_5m"))
    expected_r = safe_float(candidate.get("expected_target_r"))

    flags: List[str] = []
    if risk_pct < 0.20:
        flags.append("VERY_TIGHT_STOP")
    elif risk_pct < 0.35:
        flags.append("TIGHT_STOP")

    if cost_r >= 0.75:
        flags.append("COST_DOMINATES_RISK")
    elif cost_r >= 0.40:
        flags.append("HIGH_COST_TO_RISK")

    if len(confirmations) <= 1:
        flags.append("SINGLE_CONFIRMATION")
    if volume_ratio < 0.70:
        flags.append("LOW_VOLUME")
    if adx_5m >= 22.0 or adx_15m >= 24.0:
        flags.append("TREND_PRESSURE")
    if expected_r >= 5.0 and risk_pct < 0.25:
        flags.append("EXPECTED_R_INFLATED_BY_TIGHT_STOP")

    return {
        "risk_band": risk_band(risk_pct),
        "estimated_cost_r": round(cost_r, 4),
        "cost_to_stop_percent_ratio": (
            round(ESTIMATED_ROUND_TRIP_COST_PERCENT / risk_pct, 4)
            if risk_pct > 0
            else 0.0
        ),
        "confirmation_count": len(confirmations),
        "flags": flags,
        "shadow_only": True,
    }


def classify_close_diagnostics(
    position: Dict[str, Any],
    outcome: str,
    gross_r: float,
    net_r: float,
) -> Dict[str, Any]:
    entry_diag = position.get("entry_diagnostics")
    if not isinstance(entry_diag, dict):
        entry_diag = build_entry_diagnostics(position)

    flags = list(entry_diag.get("flags") or [])
    reason = "UNCLASSIFIED"

    if outcome in {"RESISTANCE_EXIT", "SUPPORT_EXIT"}:
        reason = "COMPLETED_RANGE_LEG"
    elif outcome == "TIMEOUT":
        if gross_r > 0 and net_r <= 0:
            reason = "TIMEOUT_EDGE_ERASED_BY_COST"
        elif gross_r > 0:
            reason = "TIMEOUT_PARTIAL_FAVORABLE_MOVE"
        elif gross_r < 0:
            reason = "TIMEOUT_DIRECTION_WEAK"
        else:
            reason = "TIMEOUT_NO_DIRECTION"
    elif outcome.startswith("AMBIGUOUS"):
        reason = "SAME_CANDLE_PATH_AMBIGUOUS"
    elif outcome == "SL":
        if "COST_DOMINATES_RISK" in flags or "VERY_TIGHT_STOP" in flags:
            reason = "TIGHT_STOP_COST_DOMINATED"
        elif "TREND_PRESSURE" in flags:
            reason = "RANGE_BROKEN_BY_TREND_PRESSURE"
        elif "SINGLE_CONFIRMATION" in flags:
            reason = "WEAK_SINGLE_CONFIRMATION"
        elif "LOW_VOLUME" in flags:
            reason = "LOW_VOLUME_NO_CONTINUATION"
        else:
            reason = "DIRECTION_OR_ENTRY_TIMING_FAILURE"

    return {
        "primary_reason": reason,
        "risk_band": entry_diag.get("risk_band"),
        "estimated_cost_r": entry_diag.get("estimated_cost_r"),
        "entry_flags": flags,
        "gross_positive_net_negative": bool(gross_r > 0 and net_r <= 0),
    }


def empty_ledger() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS",
        "cycle_logic": "SUPPORT_LONG_TO_RESISTANCE_EXIT_THEN_SHORT_TO_SUPPORT_EXIT",
        "open_positions": {},
        "closed_positions": [],
        "latest_candidates": [],
        "summary": {
            "total_opened": 0,
            "total_closed": 0,
            "open_count": 0,
            "resistance_exit_count": 0,
            "support_exit_count": 0,
            "completed_cycle_legs": 0,
            "sl_count": 0,
            "timeout_count": 0,
            "ambiguous_count": 0,
            "gross_r": 0.0,
            "net_r": 0.0,
            "gross_to_net_cost_r": 0.0,
            "average_cost_r_per_closed": 0.0,
            "cost_flipped_positive_gross_count": 0,
            "risk_band_stats": {},
            "direction_stats": {},
            "close_reason_counts": {},
            "win_rate_percent": 0.0,
        },
        "last_cycle": {},
        "last_update": 0,
    }


def frame_from_ohlcv(rows: Iterable[Iterable[Any]]) -> Any:
    if pd is None:
        raise RuntimeError("pandas kurulu değil; requirements.txt kurulmalı.")
    return pd.DataFrame(
        list(rows),
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )


def add_indicators(frame: Any) -> Optional[Any]:
    if (
        pd is None
        or RSIIndicator is None
        or ADXIndicator is None
        or EMAIndicator is None
        or AverageTrueRange is None
    ):
        raise RuntimeError("pandas/ta paketleri kurulu değil; requirements.txt kurulmalı.")
    if frame is None or frame.empty:
        return None

    required = {"timestamp", "open", "high", "low", "close", "volume"}
    if not required.issubset(set(frame.columns)):
        return None

    result = frame.copy()
    for column in ["timestamp", "open", "high", "low", "close", "volume"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna().reset_index(drop=True)
    if len(result) < 80:
        return None

    result["ema20"] = EMAIndicator(result["close"], window=20).ema_indicator()
    result["ema50"] = EMAIndicator(result["close"], window=50).ema_indicator()
    result["rsi"] = RSIIndicator(result["close"], window=14).rsi()
    result["adx"] = ADXIndicator(
        result["high"], result["low"], result["close"], window=14
    ).adx()
    result["atr"] = AverageTrueRange(
        result["high"], result["low"], result["close"], window=14
    ).average_true_range()
    result["volume_avg"] = result["volume"].rolling(20).mean()
    result["volume_ratio"] = result["volume"] / result["volume_avg"]
    result = result.dropna().reset_index(drop=True)
    return result if len(result) >= RANGE_WINDOW + 5 else None


def candle_body_direction(row: Any) -> str:
    opened = safe_float(row.get("open"))
    closed = safe_float(row.get("close"))
    if closed > opened:
        return "BULL"
    if closed < opened:
        return "BEAR"
    return "DOJI"


def lower_wick_percent(row: Any) -> float:
    high = safe_float(row.get("high"))
    low = safe_float(row.get("low"))
    opened = safe_float(row.get("open"))
    closed = safe_float(row.get("close"))
    span = high - low
    if span <= 0:
        return 0.0
    return max(0.0, min(opened, closed) - low) / span * 100.0


def upper_wick_percent(row: Any) -> float:
    high = safe_float(row.get("high"))
    low = safe_float(row.get("low"))
    opened = safe_float(row.get("open"))
    closed = safe_float(row.get("close"))
    span = high - low
    if span <= 0:
        return 0.0
    return max(0.0, high - max(opened, closed)) / span * 100.0


def percent_change(old: float, new: float) -> float:
    if old <= 0:
        return 0.0
    return (new - old) / old * 100.0


def detect_range(frame: Any) -> Dict[str, Any]:
    if frame is None or len(frame) < RANGE_WINDOW + 2:
        return {"is_range": False, "reason": "YETERSIZ_VERI", "score": 0}

    closed = frame.iloc[:-1].tail(RANGE_WINDOW).copy()
    if len(closed) < RANGE_WINDOW:
        return {"is_range": False, "reason": "YETERSIZ_KAPANMIS_MUM", "score": 0}

    lows = closed["low"].astype(float)
    highs = closed["high"].astype(float)
    closes = closed["close"].astype(float)

    support = float(lows.nsmallest(5).median())
    resistance = float(highs.nlargest(5).median())
    midpoint = (support + resistance) / 2.0
    width = resistance - support
    if midpoint <= 0 or width <= 0:
        return {"is_range": False, "reason": "GECERSIZ_BANT", "score": 0}

    width_percent = width / midpoint * 100.0
    last = closed.iloc[-1]
    atr = safe_float(last.get("atr"))
    zone_width = max(atr * ZONE_ATR_MULTIPLIER, width * ZONE_WIDTH_RATIO)

    support_touches = int((lows <= support + zone_width).sum())
    resistance_touches = int((highs >= resistance - zone_width).sum())
    containment = float(
        ((closes >= support - zone_width) & (closes <= resistance + zone_width)).mean()
    )

    close = safe_float(last.get("close"))
    adx = safe_float(last.get("adx"))
    ema20 = safe_float(last.get("ema20"))
    ema50 = safe_float(last.get("ema50"))
    ema_spread = abs(ema20 - ema50) / close * 100.0 if close > 0 else 999.0
    drift = abs(percent_change(safe_float(closes.iloc[0]), safe_float(closes.iloc[-1])))
    max_allowed_drift = max(width_percent * 0.85, 0.35)
    inside_last = support - zone_width <= close <= resistance + zone_width

    checks = {
        "width_ok": MIN_RANGE_WIDTH_PERCENT <= width_percent <= MAX_RANGE_WIDTH_PERCENT,
        "support_touches_ok": support_touches >= MIN_TOUCHES_PER_SIDE,
        "resistance_touches_ok": resistance_touches >= MIN_TOUCHES_PER_SIDE,
        "adx_ok": adx <= MAX_ADX_5M,
        "ema_spread_ok": ema_spread <= MAX_EMA_SPREAD_5M_PERCENT,
        "containment_ok": containment >= MIN_CONTAINMENT_RATIO,
        "drift_ok": drift <= max_allowed_drift,
        "inside_last": inside_last,
    }

    score = 100
    if width_percent < 0.75:
        score -= 8
    if support_touches == 2:
        score -= 5
    if resistance_touches == 2:
        score -= 5
    if adx > 22:
        score -= 10
    if ema_spread > 0.8:
        score -= 8
    if containment < 0.90:
        score -= 8
    if drift > width_percent * 0.60:
        score -= 8
    score = max(0, min(100, score))

    failed = [name for name, passed in checks.items() if not passed]
    return {
        "is_range": not failed,
        "reason": "OK" if not failed else ",".join(failed),
        "score": score,
        "support": round(support, 12),
        "resistance": round(resistance, 12),
        "midpoint": round(midpoint, 12),
        "width_percent": round(width_percent, 4),
        "zone_width": round(zone_width, 12),
        "support_touches": support_touches,
        "resistance_touches": resistance_touches,
        "containment_ratio": round(containment, 4),
        "adx_5m": round(adx, 2),
        "ema_spread_5m_percent": round(ema_spread, 4),
        "drift_percent": round(drift, 4),
        "last_close": round(close, 12),
        "last_closed_candle_ms": int(safe_float(last.get("timestamp"))),
        "checks": checks,
    }


def trend_guard_15m(frame: Any) -> Dict[str, Any]:
    if frame is None or len(frame) < 55:
        return {"allowed": False, "reason": "15M_YETERSIZ_VERI"}
    closed = frame.iloc[:-1]
    last = closed.iloc[-1]
    close = safe_float(last.get("close"))
    ema20 = safe_float(last.get("ema20"))
    ema50 = safe_float(last.get("ema50"))
    adx = safe_float(last.get("adx"))
    spread = abs(ema20 - ema50) / close * 100.0 if close > 0 else 999.0
    allowed = adx <= MAX_ADX_15M and spread <= MAX_EMA_SPREAD_15M_PERCENT
    return {
        "allowed": allowed,
        "reason": "OK" if allowed else "15M_TREND_GUCLU",
        "adx_15m": round(adx, 2),
        "ema_spread_15m_percent": round(spread, 4),
    }


def evaluate_entry_candidate(
    symbol: str,
    frame_5m: Any,
    range_info: Dict[str, Any],
    guard_15m: Optional[Dict[str, Any]] = None,
    quote_volume: float = 0.0,
) -> Optional[Dict[str, Any]]:
    if not range_info.get("is_range"):
        return None
    if guard_15m is not None and not guard_15m.get("allowed"):
        return None

    closed = frame_5m.iloc[:-1]
    last = closed.iloc[-1]
    previous = closed.iloc[-2]

    support = safe_float(range_info.get("support"))
    resistance = safe_float(range_info.get("resistance"))
    midpoint = safe_float(range_info.get("midpoint"))
    zone_width = safe_float(range_info.get("zone_width"))
    width = resistance - support
    close = safe_float(last.get("close"))
    if width <= 0 or close <= 0:
        return None

    position = (close - support) / width
    rsi = safe_float(last.get("rsi"))
    volume_ratio = safe_float(last.get("volume_ratio"))
    lower_wick = lower_wick_percent(last)
    upper_wick = upper_wick_percent(last)
    body = candle_body_direction(last)
    previous_close = safe_float(previous.get("close"))

    if not (MIN_VOLUME_RATIO <= volume_ratio <= MAX_VOLUME_RATIO):
        return None

    direction: Optional[str] = None
    confirmation: List[str] = []

    near_support = -0.08 <= position <= ENTRY_ZONE_FRACTION
    near_resistance = (1.0 - ENTRY_ZONE_FRACTION) <= position <= 1.08

    long_reversal = (
        body == "BULL"
        or lower_wick >= 28.0
        or (close > previous_close and lower_wick >= 16.0)
    )
    short_reversal = (
        body == "BEAR"
        or upper_wick >= 28.0
        or (close < previous_close and upper_wick >= 16.0)
    )

    if near_support and long_reversal and 28.0 <= rsi <= 58.0:
        direction = "LONG"
        if body == "BULL":
            confirmation.append("YESIL_DONUS_MUMU")
        if lower_wick >= 16.0:
            confirmation.append("ALT_FITIL_RED")
        if close > previous_close:
            confirmation.append("KAPANIS_TOPARLANIYOR")
    elif near_resistance and short_reversal and 42.0 <= rsi <= 72.0:
        direction = "SHORT"
        if body == "BEAR":
            confirmation.append("KIRMIZI_DONUS_MUMU")
        if upper_wick >= 16.0:
            confirmation.append("UST_FITIL_RED")
        if close < previous_close:
            confirmation.append("KAPANIS_ZAYIFLIYOR")
    else:
        return None

    atr = safe_float(last.get("atr"))
    stop_buffer = max(atr * 0.55, zone_width * 0.55)
    target_inset = zone_width * 0.20

    if direction == "LONG":
        entry = close
        sl = support - stop_buffer
        target = resistance - target_inset
        target_zone = "RESISTANCE"
        next_direction = "SHORT"
        cycle_leg = "SUPPORT_TO_RESISTANCE"
        risk = entry - sl
        reward = target - entry
    else:
        entry = close
        sl = resistance + stop_buffer
        target = support + target_inset
        target_zone = "SUPPORT"
        next_direction = "LONG"
        cycle_leg = "RESISTANCE_TO_SUPPORT"
        risk = sl - entry
        reward = entry - target

    if risk <= 0 or reward <= 0:
        return None

    expected_target_r = reward / risk
    if expected_target_r < MIN_EXPECTED_TARGET_R:
        return None

    risk_percent = risk / entry * 100.0
    distance_to_zone_percent = (
        abs(entry - support) / entry * 100.0
        if direction == "LONG"
        else abs(resistance - entry) / entry * 100.0
    )

    score = int(range_info.get("score", 0))
    score += min(8, max(0, len(confirmation) * 3))
    if 0.70 <= volume_ratio <= 1.70:
        score += 4
    if expected_target_r >= 1.8:
        score += 4
    score = max(0, min(100, score))

    candidate = {
        "symbol": normalize_symbol(symbol),
        "direction": direction,
        "source": "RANGE_CYCLE_SHADOW",
        "timeframe": TIMEFRAME,
        "entry": round(entry, 12),
        "sl": round(sl, 12),
        "target": round(target, 12),
        "target_zone": target_zone,
        "next_direction": next_direction,
        "cycle_leg": cycle_leg,
        "support": round(support, 12),
        "resistance": round(resistance, 12),
        "midpoint": round(midpoint, 12),
        "range_width_percent": range_info.get("width_percent"),
        "range_score": range_info.get("score"),
        "score": score,
        "support_touches": range_info.get("support_touches"),
        "resistance_touches": range_info.get("resistance_touches"),
        "adx_5m": range_info.get("adx_5m"),
        "adx_15m": (guard_15m or {}).get("adx_15m"),
        "ema_spread_5m_percent": range_info.get("ema_spread_5m_percent"),
        "ema_spread_15m_percent": (guard_15m or {}).get(
            "ema_spread_15m_percent"
        ),
        "rsi_5m": round(rsi, 2),
        "volume_ratio_5m": round(volume_ratio, 3),
        "quote_volume_usdt": round(quote_volume, 2),
        "lower_wick_percent": round(lower_wick, 2),
        "upper_wick_percent": round(upper_wick, 2),
        "confirmation": confirmation,
        "risk_percent": round(risk_percent, 4),
        "expected_target_r": round(expected_target_r, 4),
        "distance_to_zone_percent": round(distance_to_zone_percent, 4),
        "estimated_round_trip_cost_percent": ESTIMATED_ROUND_TRIP_COST_PERCENT,
        "signal_candle_ms": int(range_info.get("last_closed_candle_ms") or 0),
        "created_at": now_ts(),
    }
    candidate["entry_diagnostics"] = build_entry_diagnostics(candidate)
    return candidate


def position_id(candidate: Dict[str, Any]) -> str:
    return (
        f"{candidate['symbol']}_{candidate['direction']}_"
        f"{int(candidate.get('signal_candle_ms') or now_ts() * 1000)}"
    )


def build_position(candidate: Dict[str, Any]) -> Dict[str, Any]:
    result = copy.deepcopy(candidate)
    if not isinstance(result.get("entry_diagnostics"), dict):
        result["entry_diagnostics"] = build_entry_diagnostics(result)
    result.update(
        {
            "position_id": position_id(candidate),
            "status": "OPEN",
            "opened_at": now_ts(),
            "entry_candle_ms": int(candidate.get("signal_candle_ms") or 0),
            "last_checked_candle_ms": int(candidate.get("signal_candle_ms") or 0),
            "gross_r": 0.0,
            "net_r": 0.0,
            "outcome": None,
            "closed_at": 0,
            "reverse_ready": False,
        }
    )
    return result


def risk_amount(position: Dict[str, Any]) -> float:
    entry = safe_float(position.get("entry"))
    sl = safe_float(position.get("sl"))
    return abs(entry - sl)


def cost_in_r(position: Dict[str, Any]) -> float:
    return cost_r_from_risk_percent(safe_float(position.get("risk_percent")))


def close_shadow_position(
    position: Dict[str, Any],
    outcome: str,
    gross_r: float,
    closed_at_ms: int,
    exit_price: float,
) -> Dict[str, Any]:
    result = copy.deepcopy(position)
    result["status"] = "CLOSED"
    result["outcome"] = outcome
    result["gross_r"] = round(gross_r, 4)
    result["cost_r"] = round(cost_in_r(result), 4)
    result["net_r"] = round(result["gross_r"] - result["cost_r"], 4)
    result["close_diagnostics"] = classify_close_diagnostics(
        result, outcome, result["gross_r"], result["net_r"]
    )
    result["closed_at"] = (
        int(closed_at_ms / 1000)
        if closed_at_ms > 10_000_000_000
        else int(closed_at_ms)
    )
    result["exit_price"] = round(exit_price, 12)
    result["last_checked_candle_ms"] = int(closed_at_ms)
    return result


def simulate_position_on_candles(
    position: Dict[str, Any],
    candles: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    result = copy.deepcopy(position)
    if result.get("status") != "OPEN":
        return result

    direction = str(result.get("direction") or "").upper()
    entry = safe_float(result.get("entry"))
    sl = safe_float(result.get("sl"))
    target = safe_float(result.get("target"), safe_float(result.get("tp2")))
    risk = risk_amount(result)
    if direction not in {"LONG", "SHORT"} or risk <= 0 or target <= 0:
        return result

    target_r = abs(target - entry) / risk

    for candle in candles:
        timestamp = int(safe_float(candle.get("timestamp")))
        if timestamp <= int(result.get("last_checked_candle_ms") or 0):
            continue

        high = safe_float(candle.get("high"))
        low = safe_float(candle.get("low"))
        close = safe_float(candle.get("close"))
        result["last_checked_candle_ms"] = timestamp

        if direction == "LONG":
            sl_hit = low <= sl
            target_hit = high >= target
            target_outcome = "RESISTANCE_EXIT"
            next_direction = "SHORT"
        else:
            sl_hit = high >= sl
            target_hit = low <= target
            target_outcome = "SUPPORT_EXIT"
            next_direction = "LONG"

        if sl_hit and target_hit:
            return close_shadow_position(
                result,
                "AMBIGUOUS_SL_TARGET_SAME_CANDLE",
                -1.0,
                timestamp,
                sl,
            )
        if sl_hit:
            return close_shadow_position(result, "SL", -1.0, timestamp, sl)
        if target_hit:
            closed = close_shadow_position(
                result, target_outcome, target_r, timestamp, target
            )
            closed["reverse_ready"] = True
            closed["next_direction"] = next_direction
            closed["reverse_zone"] = (
                "RESISTANCE" if direction == "LONG" else "SUPPORT"
            )
            return closed

        result["last_market_price"] = round(close, 12)

    return result


def timeout_position(position: Dict[str, Any], latest: Dict[str, Any]) -> Dict[str, Any]:
    if position.get("status") != "OPEN":
        return position
    opened_at = int(position.get("opened_at") or 0)
    if opened_at <= 0 or now_ts() - opened_at < MAX_HOLD_MINUTES * 60:
        return position

    direction = str(position.get("direction") or "").upper()
    entry = safe_float(position.get("entry"))
    close = safe_float(latest.get("close"), entry)
    risk = risk_amount(position)
    if risk <= 0:
        gross = 0.0
    else:
        gross = (
            (close - entry) / risk
            if direction == "LONG"
            else (entry - close) / risk
        )
    return close_shadow_position(
        position,
        "TIMEOUT",
        gross,
        int(safe_float(latest.get("timestamp"), now_ts() * 1000)),
        close,
    )


def recent_duplicate(
    ledger: Dict[str, Any], symbol: str, direction: str, current_ts: int
) -> bool:
    normalized = normalize_symbol(symbol)
    for position in (ledger.get("open_positions") or {}).values():
        if normalize_symbol(position.get("symbol")) == normalized:
            return True

    cutoff = current_ts - DUPLICATE_COOLDOWN_MINUTES * 60
    for position in reversed(ledger.get("closed_positions") or []):
        if int(position.get("closed_at") or 0) < cutoff:
            break
        if (
            normalize_symbol(position.get("symbol")) == normalized
            and str(position.get("direction") or "").upper() == direction
        ):
            return True
    return False


def calculate_summary(ledger: Dict[str, Any]) -> Dict[str, Any]:
    closed = ledger.get("closed_positions") or []
    outcomes = [str(item.get("outcome") or "") for item in closed]
    wins = sum(1 for item in closed if safe_float(item.get("net_r")) > 0)
    total_closed = len(closed)
    resistance_exits = outcomes.count("RESISTANCE_EXIT")
    support_exits = outcomes.count("SUPPORT_EXIT")
    gross_total = sum(safe_float(item.get("gross_r")) for item in closed)
    net_total = sum(safe_float(item.get("net_r")) for item in closed)
    total_cost_r = gross_total - net_total

    risk_band_stats: Dict[str, Dict[str, Any]] = {}
    direction_stats: Dict[str, Dict[str, Any]] = {}
    close_reason_counts: Dict[str, int] = {}
    cost_flipped = 0

    for item in closed:
        band = str(
            ((item.get("entry_diagnostics") or {}).get("risk_band"))
            or risk_band(safe_float(item.get("risk_percent")))
        )
        direction = str(item.get("direction") or "UNKNOWN").upper()
        gross = safe_float(item.get("gross_r"))
        net = safe_float(item.get("net_r"))
        outcome = str(item.get("outcome") or "")

        band_row = risk_band_stats.setdefault(
            band,
            {"closed": 0, "wins": 0, "sl": 0, "gross_r": 0.0, "net_r": 0.0},
        )
        band_row["closed"] += 1
        band_row["wins"] += int(net > 0)
        band_row["sl"] += int(outcome == "SL")
        band_row["gross_r"] += gross
        band_row["net_r"] += net

        direction_row = direction_stats.setdefault(
            direction,
            {"closed": 0, "wins": 0, "sl": 0, "gross_r": 0.0, "net_r": 0.0},
        )
        direction_row["closed"] += 1
        direction_row["wins"] += int(net > 0)
        direction_row["sl"] += int(outcome == "SL")
        direction_row["gross_r"] += gross
        direction_row["net_r"] += net

        close_diag = item.get("close_diagnostics")
        if not isinstance(close_diag, dict):
            close_diag = classify_close_diagnostics(item, outcome, gross, net)
        reason = str(close_diag.get("primary_reason") or "UNCLASSIFIED")
        close_reason_counts[reason] = close_reason_counts.get(reason, 0) + 1

        if gross > 0 and net <= 0:
            cost_flipped += 1

    for group in (risk_band_stats, direction_stats):
        for row in group.values():
            row["gross_r"] = round(safe_float(row["gross_r"]), 4)
            row["net_r"] = round(safe_float(row["net_r"]), 4)
            row["win_rate_percent"] = (
                round(row["wins"] / row["closed"] * 100.0, 2)
                if row["closed"]
                else 0.0
            )

    return {
        "total_opened": total_closed + len(ledger.get("open_positions") or {}),
        "total_closed": total_closed,
        "open_count": len(ledger.get("open_positions") or {}),
        "resistance_exit_count": resistance_exits,
        "support_exit_count": support_exits,
        "completed_cycle_legs": resistance_exits + support_exits,
        "sl_count": outcomes.count("SL"),
        "timeout_count": outcomes.count("TIMEOUT"),
        "ambiguous_count": sum(
            1 for value in outcomes if value.startswith("AMBIGUOUS")
        ),
        "gross_r": round(gross_total, 4),
        "net_r": round(net_total, 4),
        "gross_to_net_cost_r": round(total_cost_r, 4),
        "average_cost_r_per_closed": (
            round(total_cost_r / total_closed, 4) if total_closed else 0.0
        ),
        "cost_flipped_positive_gross_count": cost_flipped,
        "risk_band_stats": risk_band_stats,
        "direction_stats": direction_stats,
        "close_reason_counts": close_reason_counts,
        "win_rate_percent": (
            round(wins / total_closed * 100.0, 2) if total_closed else 0.0
        ),
    }


def create_exchange() -> Any:
    if ccxt is None:
        raise RuntimeError("ccxt kurulu değil; requirements.txt kurulmalı.")
    return ccxt.okx(
        {
            "enableRateLimit": True,
            "timeout": 20000,
            "options": {"defaultType": "swap"},
        }
    )


def quote_volume_from_ticker(ticker: Dict[str, Any]) -> float:
    quote = safe_float(ticker.get("quoteVolume"))
    if quote > 0:
        return quote
    base = safe_float(ticker.get("baseVolume"))
    last = safe_float(ticker.get("last"))
    return base * last if base > 0 and last > 0 else 0.0


def get_scan_universe(exchange: Any) -> List[Tuple[str, str, float]]:
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()
    candidates: List[Tuple[str, str, float]] = []

    for okx_symbol, market in markets.items():
        if not isinstance(market, dict):
            continue
        if not market.get("swap") or market.get("linear") is False:
            continue
        if market.get("quote") != "USDT" or market.get("active") is False:
            continue

        ticker = tickers.get(okx_symbol) or {}
        quote_volume = quote_volume_from_ticker(ticker)
        if quote_volume < MIN_QUOTE_VOLUME_USDT:
            continue

        base = str(market.get("base") or "").upper().strip()
        quote = str(market.get("quote") or "").upper().strip()
        normalized = normalize_symbol(f"{base}{quote}")
        if not base or quote != "USDT" or not normalized.endswith("USDT"):
            continue
        candidates.append((normalized, okx_symbol, quote_volume))

    candidates.sort(key=lambda item: item[2], reverse=True)
    return candidates[:MAX_SCAN_COINS]


def fetch_indicator_frame(
    exchange: Any, okx_symbol: str, timeframe: str
) -> Optional[Any]:
    rows = exchange.fetch_ohlcv(
        okx_symbol, timeframe=timeframe, limit=OHLCV_LIMIT
    )
    return add_indicators(frame_from_ohlcv(rows))


def btc_hard_move_guard(exchange: Any) -> Dict[str, Any]:
    try:
        frame = fetch_indicator_frame(exchange, "BTC/USDT:USDT", "5m")
        if frame is None or len(frame) < 3:
            return {"allowed": True, "reason": "BTC_VERI_YOK"}
        closed = frame.iloc[:-1]
        last = closed.iloc[-1]
        move = percent_change(
            safe_float(last.get("open")), safe_float(last.get("close"))
        )
        allowed = abs(move) < BTC_HARD_MOVE_PERCENT
        return {
            "allowed": allowed,
            "reason": "OK" if allowed else "BTC_5M_SERT_HAREKET",
            "btc_5m_move_percent": round(move, 4),
        }
    except Exception as exc:
        return {"allowed": True, "reason": f"BTC_GUARD_HATA:{type(exc).__name__}"}


def update_existing_positions(
    exchange: Any, ledger: Dict[str, Any]
) -> Tuple[int, int]:
    open_positions = ledger.get("open_positions") or {}
    closed_positions = ledger.get("closed_positions") or []
    updated = 0
    resolved = 0

    for key, position in list(open_positions.items()):
        try:
            okx_symbol = symbol_to_okx(str(position.get("symbol")))
            frame = fetch_indicator_frame(exchange, okx_symbol, TIMEFRAME)
            if frame is None:
                continue
            closed = frame.iloc[:-1]
            candles = [row.to_dict() for _, row in closed.iterrows()]
            result = simulate_position_on_candles(position, candles)
            if result.get("status") == "OPEN":
                result = timeout_position(result, candles[-1])

            updated += 1
            if result.get("status") == "CLOSED":
                closed_positions.append(result)
                open_positions.pop(key, None)
                resolved += 1
            else:
                open_positions[key] = result
        except Exception as exc:
            print(position.get("symbol"), "açık gölge işlem takip hatası:", exc)

    ledger["open_positions"] = open_positions
    ledger["closed_positions"] = closed_positions[-MAX_CLOSED_RECORDS:]
    return updated, resolved


def scan_new_candidates(
    exchange: Any,
    ledger: Dict[str, Any],
    global_guard: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], int, int]:
    universe = get_scan_universe(exchange)
    candidates: List[Dict[str, Any]] = []
    scanned = 0
    range_count = 0

    if not global_guard.get("allowed"):
        return candidates, scanned, range_count

    for symbol, okx_symbol, quote_volume in universe:
        try:
            frame5 = fetch_indicator_frame(exchange, okx_symbol, "5m")
            scanned += 1
            if frame5 is None:
                continue
            range_info = detect_range(frame5)
            if not range_info.get("is_range"):
                continue
            range_count += 1

            close = safe_float(range_info.get("last_close"))
            support = safe_float(range_info.get("support"))
            resistance = safe_float(range_info.get("resistance"))
            width = resistance - support
            if width <= 0:
                continue
            position = (close - support) / width
            near_zone = (
                -0.08 <= position <= ENTRY_ZONE_FRACTION
                or (1 - ENTRY_ZONE_FRACTION) <= position <= 1.08
            )
            if not near_zone:
                continue

            frame15 = fetch_indicator_frame(exchange, okx_symbol, "15m")
            guard15 = trend_guard_15m(frame15)
            candidate = evaluate_entry_candidate(
                symbol, frame5, range_info, guard15, quote_volume
            )
            if candidate:
                candidates.append(candidate)
        except Exception as exc:
            print(symbol, "range tarama hatası:", type(exc).__name__, exc)

    candidates.sort(
        key=lambda item: (
            safe_float(item.get("score")),
            safe_float(item.get("expected_target_r")),
            safe_float(item.get("quote_volume_usdt")),
        ),
        reverse=True,
    )
    return candidates, scanned, range_count


def apply_new_candidates(
    ledger: Dict[str, Any], candidates: Sequence[Dict[str, Any]]
) -> int:
    open_positions = ledger.get("open_positions") or {}
    available = max(0, MAX_OPEN_POSITIONS - len(open_positions))
    limit = min(MAX_NEW_POSITIONS_PER_RUN, available)
    added = 0
    current_ts = now_ts()

    for candidate in candidates:
        if added >= limit:
            break
        symbol = str(candidate.get("symbol") or "")
        direction = str(candidate.get("direction") or "").upper()
        if recent_duplicate(ledger, symbol, direction, current_ts):
            continue
        position = build_position(candidate)
        open_positions[position["position_id"]] = position
        added += 1

    ledger["open_positions"] = open_positions
    return added


def enrich_legacy_diagnostics(ledger: Dict[str, Any]) -> None:
    for position in (ledger.get("open_positions") or {}).values():
        if not isinstance(position.get("entry_diagnostics"), dict):
            position["entry_diagnostics"] = build_entry_diagnostics(position)

    for position in ledger.get("closed_positions") or []:
        if not isinstance(position.get("entry_diagnostics"), dict):
            position["entry_diagnostics"] = build_entry_diagnostics(position)
        if "cost_r" not in position:
            position["cost_r"] = round(cost_in_r(position), 4)
        if not isinstance(position.get("close_diagnostics"), dict):
            position["close_diagnostics"] = classify_close_diagnostics(
                position,
                str(position.get("outcome") or ""),
                safe_float(position.get("gross_r")),
                safe_float(position.get("net_r")),
            )


def run_cycle(filename: str = LEDGER_FILE) -> Dict[str, Any]:
    ledger = load_json(filename, empty_ledger())
    if ledger.get("version") != VERSION:
        ledger["version"] = VERSION
        ledger.setdefault("mode", "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS")
        ledger.setdefault(
            "cycle_logic",
            "SUPPORT_LONG_TO_RESISTANCE_EXIT_THEN_SHORT_TO_SUPPORT_EXIT",
        )
        ledger.setdefault("open_positions", {})
        ledger.setdefault("closed_positions", [])
        ledger.setdefault("latest_candidates", [])

    enrich_legacy_diagnostics(ledger)

    exchange = create_exchange()
    updated, resolved = update_existing_positions(exchange, ledger)
    global_guard = btc_hard_move_guard(exchange)
    candidates, scanned, range_count = scan_new_candidates(
        exchange, ledger, global_guard
    )
    ledger["latest_candidates"] = list(candidates[:MAX_CANDIDATES_SAVED])
    added = apply_new_candidates(ledger, candidates)
    ledger["summary"] = calculate_summary(ledger)
    ledger["last_update"] = now_ts()
    ledger["last_cycle"] = {
        "scanned_coins": scanned,
        "range_coins": range_count,
        "candidate_count": len(candidates),
        "new_positions": added,
        "updated_open_positions": updated,
        "resolved_positions": resolved,
        "global_guard": global_guard,
    }

    if not save_json_atomically(filename, ledger):
        raise RuntimeError("range_shadow.json kaydedilemedi.")

    print("Range Cycle Shadow v3 Diagnostic tamamlandı.")
    print("Taranan coin:", scanned)
    print("Bant bulunan:", range_count)
    print("Bant döngü adayı:", len(candidates))
    print("Yeni sanal işlem:", added)
    print("Açık sanal işlem:", len(ledger.get("open_positions") or {}))
    print("Sonuçlanan:", resolved)
    print("Gross R:", ledger["summary"].get("gross_r"))
    print("Net R:", ledger["summary"].get("net_r"))
    print(
        "Brüt-Net maliyet farkı R:",
        ledger["summary"].get("gross_to_net_cost_r"),
    )
    print("Stop/kapanış nedenleri:", ledger["summary"].get("close_reason_counts"))
    print("Telegram: KAPALI | Gerçek emir: KAPALI")
    return ledger


def _synthetic_range_rows(count: int = 190) -> List[List[float]]:
    rows: List[List[float]] = []
    base_ts = 1_700_000_000_000
    for index in range(count):
        wave = math.sin(index * 0.42)
        center = 100.0
        close = center + wave * 1.0
        opened = center + math.sin((index - 1) * 0.42) * 1.0
        high = max(opened, close) + 0.18
        low = min(opened, close) - 0.18
        volume = 1000.0 + (index % 7) * 20.0
        rows.append(
            [base_ts + index * 300_000, opened, high, low, close, volume]
        )
    return rows


def self_test() -> None:
    base_candidate = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "entry": 100.0,
        "sl": 99.0,
        "target": 102.0,
        "target_zone": "RESISTANCE",
        "next_direction": "SHORT",
        "risk_percent": 1.0,
        "signal_candle_ms": 1_700_000_000_000,
        "confirmation": ["YESIL_DONUS_MUMU", "ALT_FITIL_RED"],
        "adx_5m": 18.0,
        "adx_15m": 18.0,
        "volume_ratio_5m": 1.0,
        "expected_target_r": 2.0,
    }
    position = build_position(base_candidate)
    later = [
        {
            "timestamp": position["entry_candle_ms"] + 300_000,
            "open": 100.1,
            "high": 102.1,
            "low": 100.0,
            "close": 102.0,
        }
    ]
    resolved = simulate_position_on_candles(position, later)
    assert resolved["outcome"] == "RESISTANCE_EXIT", resolved
    assert resolved["reverse_ready"] is True
    assert resolved["next_direction"] == "SHORT"
    assert resolved["net_r"] < resolved["gross_r"]
    assert resolved["cost_r"] > 0
    assert (
        resolved["close_diagnostics"]["primary_reason"]
        == "COMPLETED_RANGE_LEG"
    )

    tight_candidate = copy.deepcopy(base_candidate)
    tight_candidate["sl"] = 99.85
    tight_candidate["risk_percent"] = 0.15
    tight_candidate["target"] = 100.60
    tight_candidate["expected_target_r"] = 4.0
    tight_position = build_position(tight_candidate)
    stopped = simulate_position_on_candles(
        tight_position,
        [
            {
                "timestamp": tight_position["entry_candle_ms"] + 300_000,
                "open": 100.0,
                "high": 100.1,
                "low": 99.80,
                "close": 99.90,
            }
        ],
    )
    assert stopped["outcome"] == "SL"
    assert stopped["net_r"] < -1.0
    assert (
        stopped["close_diagnostics"]["primary_reason"]
        == "TIGHT_STOP_COST_DOMINATED"
    )

    timeout_candidate = copy.deepcopy(base_candidate)
    timeout_candidate["risk_percent"] = 0.20
    timeout_candidate["sl"] = 99.80
    timeout_position_data = build_position(timeout_candidate)
    timeout_position_data["opened_at"] = now_ts() - (MAX_HOLD_MINUTES + 1) * 60
    timeout_result = timeout_position(
        timeout_position_data,
        {
            "timestamp": timeout_position_data["entry_candle_ms"] + 300_000,
            "close": 100.05,
        },
    )
    assert timeout_result["outcome"] == "TIMEOUT"
    assert timeout_result["gross_r"] > 0
    assert timeout_result["net_r"] <= 0
    assert (
        timeout_result["close_diagnostics"]["primary_reason"]
        == "TIMEOUT_EDGE_ERASED_BY_COST"
    )

    if pd is not None and RSIIndicator is not None:
        frame = add_indicators(frame_from_ohlcv(_synthetic_range_rows()))
        assert frame is not None
        frame.loc[:, "adx"] = 18.0
        frame.loc[:, "ema20"] = frame["close"]
        frame.loc[:, "ema50"] = frame["close"] * 1.0005
        frame.loc[:, "atr"] = 0.25
        frame.loc[:, "rsi"] = 50.0
        frame.loc[:, "volume_ratio"] = 1.0
        info = detect_range(frame)
        assert info.get("is_range"), info

        candidate_frame = frame.copy()
        support = safe_float(info.get("support"))
        last_index = candidate_frame.index[-2]
        candidate_frame.loc[last_index, "open"] = support + 0.10
        candidate_frame.loc[last_index, "low"] = support - 0.05
        candidate_frame.loc[last_index, "high"] = support + 0.35
        candidate_frame.loc[last_index, "close"] = support + 0.28
        candidate_frame.loc[last_index, "rsi"] = 42.0
        candidate_frame.loc[last_index, "volume_ratio"] = 1.0
        refreshed = detect_range(candidate_frame)
        guard = {
            "allowed": True,
            "adx_15m": 18.0,
            "ema_spread_15m_percent": 0.4,
        }
        candidate = evaluate_entry_candidate(
            "TESTUSDT",
            candidate_frame,
            refreshed,
            guard,
            10_000_000,
        )
        assert candidate is not None
        assert candidate["direction"] == "LONG"
        assert candidate["target_zone"] == "RESISTANCE"
        assert "entry_diagnostics" in candidate
        assert "tp1" not in candidate and "tp2" not in candidate

    sample_ledger = empty_ledger()
    sample_ledger["closed_positions"] = [resolved, stopped, timeout_result]
    summary = calculate_summary(sample_ledger)
    assert summary["total_closed"] == 3
    assert summary["gross_to_net_cost_r"] > 0
    assert summary["risk_band_stats"]
    assert summary["direction_stats"]
    assert summary["close_reason_counts"]

    print("Range Cycle Shadow v3 Diagnostic self-test BAŞARILI")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Range Cycle Shadow v3 Diagnostic"
    )
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--ledger", default=LEDGER_FILE)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.self_test:
        self_test()
        return
    run_cycle(args.ledger)


if __name__ == "__main__":
    main()
