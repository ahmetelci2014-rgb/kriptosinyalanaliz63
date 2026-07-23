# strategy.py
# Premium MTF TP Odaklı v3 - Erken Giriş + Geç Sinyal Koruması
#
# Ana mantık:
# 4H = ana trend
# 1H = yön onayı
# 15M = ana kurulum / pullback / dönüş
# 5M = direnç-destek bölgesinde erken teyit
#
# Bu sürümde iki önemli düzeltme vardır:
# 1) 5M, 15M dönüş mumu tamamen kapanmadan önce uygun bölgede erken giriş verebilir.
# 2) 15M sinyali, ideal dönüş bölgesinden fazla uzaklaştıysa artık gönderilmez.
#
# Emir açmaz. Yalnızca sinyal üretir.

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange

from config import (
    MIN_SCORE_TRADE,
    MIN_SCORE_RADAR,
    MIN_ADX_4H,
    MIN_ADX_1H,
    MIN_VOLUME_RATIO_15M,
    LONG_RSI_MIN,
    LONG_RSI_MAX,
    SHORT_RSI_MIN,
    SHORT_RSI_MAX,
    RADAR_MIN_5M_MOVE_PERCENT,
    RADAR_MAX_5M_MOVE_PERCENT,
    RADAR_MIN_VOLUME_RATIO,
    RADAR_TRADE_MIN_SCORE,
    RADAR_TRADE_MIN_VOLUME_RATIO,
    MIN_RISK_PERCENT,
    MAX_RISK_PERCENT,
    TP1_R_MULTIPLIER,
    TP2_R_MULTIPLIER,
    TP3_R_MULTIPLIER,
    LEVERAGE_RISK_3X_MAX,
    LEVERAGE_RISK_2X_MAX,
    LEVERAGE_RISK_1X2X_MAX,
)


# =========================================================
# YENİ GİRİŞ ZAMANLAMA AYARLARI
# =========================================================

# 15M dönüş sinyalinde fiyat ideal dönüş bölgesinden bundan fazla
# uzaklaşmışsa sinyal artık geç kabul edilir.
MAX_LATE_ENTRY_DISTANCE_PERCENT = 0.35

# 5M erken girişte fiyat EMA / yakın destek-direnç bölgesine
# bundan daha yakın olmalıdır.
MAX_EARLY_ZONE_DISTANCE_PERCENT = 0.40

# 5M erken teyit için minimum gerçek mum hareketi.
EARLY_MIN_5M_MOVE_PERCENT = 0.10

# Erken işlemde 15M RSI'nın çok uç noktaya gitmesine izin verilmez.
EARLY_LONG_RSI_MIN = max(float(LONG_RSI_MIN), 44.0)
EARLY_LONG_RSI_MAX = min(float(LONG_RSI_MAX), 66.0)
EARLY_SHORT_RSI_MIN = max(float(SHORT_RSI_MIN), 36.0)
EARLY_SHORT_RSI_MAX = min(float(SHORT_RSI_MAX), 56.0)

# Erken işlemde 15M trend ve hacim tamamen zayıf olmamalıdır.
EARLY_MIN_ADX_15M = 15.0
EARLY_MIN_VOLUME_15M = max(float(MIN_VOLUME_RATIO_15M), 0.75)

# 5M dönüş mumunda fitil tek başına zorunlu değildir.
# Güçlü gövdeli reddedilme de kabul edilir.
EARLY_MIN_REJECTION_WICK_PERCENT = 18.0
EARLY_LONG_MIN_CLOSE_POWER = 58.0
EARLY_SHORT_MAX_CLOSE_POWER = 42.0


# =========================================================
# TEMEL YARDIMCILAR
# =========================================================

def format_price(value: float) -> str:
    value = float(value)

    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.01:
        return f"{value:.6f}"
    return f"{value:.10f}"


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


def percent_distance(price: float, reference: float) -> float:
    price = safe_float(price)
    reference = safe_float(reference)

    if reference <= 0:
        return 999.0

    return abs(price - reference) / reference * 100


def candle_move_percent(row: pd.Series) -> float:
    open_price = safe_float(row.get("open"))
    close_price = safe_float(row.get("close"))

    if open_price <= 0:
        return 0.0

    return (close_price - open_price) / open_price * 100


def close_power_percent(row: pd.Series) -> float:
    high = safe_float(row.get("high"))
    low = safe_float(row.get("low"))
    close = safe_float(row.get("close"))

    candle_range = high - low
    if candle_range <= 0:
        return 50.0

    return (close - low) / candle_range * 100


def upper_wick_percent(row: pd.Series) -> float:
    high = safe_float(row.get("high"))
    low = safe_float(row.get("low"))
    open_price = safe_float(row.get("open"))
    close_price = safe_float(row.get("close"))

    candle_range = high - low
    if candle_range <= 0:
        return 0.0

    upper_wick = high - max(open_price, close_price)
    return max(0.0, upper_wick / candle_range * 100)


def lower_wick_percent(row: pd.Series) -> float:
    high = safe_float(row.get("high"))
    low = safe_float(row.get("low"))
    open_price = safe_float(row.get("open"))
    close_price = safe_float(row.get("close"))

    candle_range = high - low
    if candle_range <= 0:
        return 0.0

    lower_wick = min(open_price, close_price) - low
    return max(0.0, lower_wick / candle_range * 100)


def add_indicators(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or df.empty:
        return None

    required_columns = {"open", "high", "low", "close", "volume"}
    if not required_columns.issubset(set(df.columns)):
        return None

    frame = df.copy()

    for column in required_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    frame = frame.dropna().reset_index(drop=True)

    if len(frame) < 35:
        return None

    frame["rsi"] = RSIIndicator(
        frame["close"],
        window=14,
    ).rsi()

    frame["ema20"] = EMAIndicator(
        frame["close"],
        window=20,
    ).ema_indicator()

    frame["ema50"] = EMAIndicator(
        frame["close"],
        window=50,
    ).ema_indicator()

    frame["ema200"] = EMAIndicator(
        frame["close"],
        window=200,
    ).ema_indicator()

    macd = MACD(frame["close"])
    frame["macd"] = macd.macd()
    frame["macd_signal"] = macd.macd_signal()
    frame["macd_hist"] = frame["macd"] - frame["macd_signal"]

    frame["atr"] = AverageTrueRange(
        frame["high"],
        frame["low"],
        frame["close"],
        window=14,
    ).average_true_range()

    frame["adx"] = ADXIndicator(
        frame["high"],
        frame["low"],
        frame["close"],
        window=14,
    ).adx()

    frame["volume_avg"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_avg"]
    frame["ema20_slope"] = frame["ema20"] - frame["ema20"].shift(3)

    frame = frame.dropna().reset_index(drop=True)

    return frame if len(frame) >= 20 else None


# =========================================================
# 4H / 1H TREND
# =========================================================

def get_4h_trend(
    df4h: Optional[pd.DataFrame],
) -> Tuple[str, str, Dict[str, Any]]:
    frame = add_indicators(df4h)

    if frame is None or len(frame) < 20:
        return "NEUTRAL", "4H veri yetersiz", {}

    row = frame.iloc[-2]

    close = safe_float(row["close"])
    ema20 = safe_float(row["ema20"])
    ema50 = safe_float(row["ema50"])
    ema200 = safe_float(row["ema200"])
    slope = safe_float(row["ema20_slope"])
    adx = safe_float(row["adx"])
    rsi = safe_float(row["rsi"])

    info = {
        "adx_4h": round(adx, 2),
        "rsi_4h": round(rsi, 2),
        "close_4h": round(close, 8),
    }

    if (
        close > ema200
        and ema20 > ema50
        and slope > 0
        and adx >= MIN_ADX_4H
    ):
        return "LONG", "4H ana trend yukarı", info

    if (
        close < ema200
        and ema20 < ema50
        and slope < 0
        and adx >= MIN_ADX_4H
    ):
        return "SHORT", "4H ana trend aşağı", info

    if close > ema200 and ema20 > ema50:
        return "LONG_WEAK", "4H yukarı eğilim ama güç orta", info

    if close < ema200 and ema20 < ema50:
        return "SHORT_WEAK", "4H aşağı eğilim ama güç orta", info

    return "NEUTRAL", "4H kararsız", info


def get_1h_confirm(
    df1h: Optional[pd.DataFrame],
) -> Tuple[str, str, Dict[str, Any]]:
    frame = add_indicators(df1h)

    if frame is None or len(frame) < 20:
        return "NEUTRAL", "1H veri yetersiz", {}

    row = frame.iloc[-2]

    close = safe_float(row["close"])
    ema20 = safe_float(row["ema20"])
    ema50 = safe_float(row["ema50"])
    ema200 = safe_float(row["ema200"])
    macd_value = safe_float(row["macd"])
    macd_signal = safe_float(row["macd_signal"])
    adx = safe_float(row["adx"])
    rsi = safe_float(row["rsi"])

    info = {
        "adx_1h": round(adx, 2),
        "rsi_1h": round(rsi, 2),
        "close_1h": round(close, 8),
    }

    if (
        close > ema200
        and ema20 > ema50
        and macd_value >= macd_signal
        and rsi >= 46
        and adx >= MIN_ADX_1H
    ):
        return "LONG", "1H alım onayı", info

    if (
        close < ema200
        and ema20 < ema50
        and macd_value <= macd_signal
        and rsi <= 54
        and adx >= MIN_ADX_1H
    ):
        return "SHORT", "1H satış onayı", info

    if close > ema20 and ema20 >= ema50 and macd_value >= macd_signal:
        return "LONG_WEAK", "1H hafif alım eğilimi", info

    if close < ema20 and ema20 <= ema50 and macd_value <= macd_signal:
        return "SHORT_WEAK", "1H hafif satış eğilimi", info

    return "NEUTRAL", "1H kararsız", info


def trend_supports_direction(
    direction: str,
    trend: str,
    confirm: str,
    strict: bool = True,
) -> bool:
    if direction == "LONG":
        if strict:
            return trend == "LONG" and confirm == "LONG"

        return (
            trend in ("LONG", "LONG_WEAK")
            and confirm in ("LONG", "LONG_WEAK", "NEUTRAL")
        )

    if direction == "SHORT":
        if strict:
            return trend == "SHORT" and confirm == "SHORT"

        return (
            trend in ("SHORT", "SHORT_WEAK")
            and confirm in ("SHORT", "SHORT_WEAK", "NEUTRAL")
        )

    return False


# =========================================================
# RİSK / HEDEF
# =========================================================

def leverage_suggestion(risk_percent: float) -> str:
    risk_percent = safe_float(risk_percent)

    if risk_percent <= LEVERAGE_RISK_3X_MAX:
        return "3x"
    if risk_percent <= LEVERAGE_RISK_2X_MAX:
        return "2x"
    if risk_percent <= LEVERAGE_RISK_1X2X_MAX:
        return "1x-2x"

    return "1x veya pas geç"


def make_targets_from_stop(
    direction: str,
    entry: float,
    sl: float,
) -> Optional[Dict[str, float]]:
    entry = safe_float(entry)
    sl = safe_float(sl)

    if entry <= 0 or sl <= 0:
        return None

    if direction == "LONG":
        risk = entry - sl

        if risk <= 0:
            return None

        tp1 = entry + risk * TP1_R_MULTIPLIER
        tp2 = entry + risk * TP2_R_MULTIPLIER
        tp3 = entry + risk * TP3_R_MULTIPLIER
    else:
        risk = sl - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * TP1_R_MULTIPLIER
        tp2 = entry - risk * TP2_R_MULTIPLIER
        tp3 = entry - risk * TP3_R_MULTIPLIER

    if min(tp1, tp2, tp3) <= 0:
        return None

    risk_percent = risk / entry * 100

    if (
        risk_percent < MIN_RISK_PERCENT
        or risk_percent > MAX_RISK_PERCENT
    ):
        return None

    return {
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_percent": risk_percent,
        "rr_tp1": abs(tp1 - entry) / risk,
        "rr_tp2": abs(tp2 - entry) / risk,
        "rr_tp3": abs(tp3 - entry) / risk,
    }


def make_targets(
    direction: str,
    entry: float,
    atr: float,
    df15: pd.DataFrame,
) -> Optional[Dict[str, float]]:
    if df15 is None or len(df15) < 16:
        return None

    recent = df15.iloc[-14:-2]
    atr = safe_float(atr)

    if atr <= 0:
        return None

    if direction == "LONG":
        swing_low = safe_float(recent["low"].min())
        sl = min(
            swing_low - atr * 0.10,
            entry - atr * 1.10,
        )
    else:
        swing_high = safe_float(recent["high"].max())
        sl = max(
            swing_high + atr * 0.10,
            entry + atr * 1.10,
        )

    return make_targets_from_stop(direction, entry, sl)


def make_early_targets(
    direction: str,
    entry: float,
    atr_15m: float,
    df5m: pd.DataFrame,
) -> Optional[Dict[str, float]]:
    if df5m is None or len(df5m) < 8:
        return None

    atr_15m = safe_float(atr_15m)
    if atr_15m <= 0:
        return None

    recent5 = df5m.iloc[-7:-1]

    if direction == "LONG":
        swing_low = safe_float(recent5["low"].min())
        sl = min(
            swing_low - atr_15m * 0.12,
            entry - atr_15m * 0.45,
        )
    else:
        swing_high = safe_float(recent5["high"].max())
        sl = max(
            swing_high + atr_15m * 0.12,
            entry + atr_15m * 0.45,
        )

    return make_targets_from_stop(direction, entry, sl)


# =========================================================
# KALİTE
# =========================================================

def smart_quality_label(
    signal_class: str,
    direction: str,
    score: int,
    volume_ratio: float,
    rsi: float,
    adx_15m: float,
    adx_4h: float,
    adx_1h: float,
    risk_percent: float,
) -> Tuple[str, str]:
    if signal_class != "TRADE":
        return "RADAR", "İşlem değil, sadece takip radarı."

    volume_ratio = safe_float(volume_ratio)
    rsi = safe_float(rsi)
    adx_15m = safe_float(adx_15m)
    adx_4h = safe_float(adx_4h)
    adx_1h = safe_float(adx_1h)
    risk_percent = safe_float(risk_percent)
    score = int(score)

    healthy_long_rsi = direction == "LONG" and 48 <= rsi <= 63
    healthy_short_rsi = direction == "SHORT" and 38 <= rsi <= 54

    strong_adx = (
        adx_15m >= 25
        and adx_1h >= 20
        and adx_4h >= 18
    )

    good_volume = volume_ratio >= 1.25
    good_risk = risk_percent <= 1.50

    cautions = []

    if volume_ratio < 0.80:
        cautions.append("hacim düşük")

    if adx_4h < 18:
        cautions.append("4H ADX sınırda")

    if adx_1h < 18:
        cautions.append("1H ADX sınırda")

    if direction == "LONG" and rsi >= 65:
        cautions.append("LONG RSI yüksek")

    if direction == "SHORT" and rsi <= 36:
        cautions.append("SHORT RSI düşük")

    if risk_percent >= 1.60:
        cautions.append("stop geniş")

    if (
        score >= 92
        and good_volume
        and strong_adx
        and good_risk
        and (healthy_long_rsi or healthy_short_rsi)
    ):
        return (
            "A+ ANA",
            "Ana aday profili: güçlü ADX, yeterli hacim, "
            "sağlıklı RSI ve makul stop.",
        )

    if score >= 88 and len(cautions) <= 1:
        note = "Güçlü sinyal ama tam A+ değil."

        if cautions:
            note += " Dikkat: " + ", ".join(cautions) + "."

        return "A DİKKATLİ", note

    if score >= 72:
        note = "TP1 odaklı dikkatli sinyal."

        if cautions:
            note += " Dikkat: " + ", ".join(cautions) + "."

        return "A- TP1", note

    return (
        "TAKİP",
        "Skor/kalite zayıf; işlem yerine takip daha mantıklı.",
    )


# =========================================================
# GİRİŞ BÖLGESİ
# =========================================================

def nearest_zone_reference(
    direction: str,
    current_price: float,
    df15: pd.DataFrame,
) -> Tuple[float, float, str]:
    """
    Güncel fiyata en yakın anlamlı 15M giriş bölgesini seçer.

    SHORT:
    - EMA20
    - EMA50
    - Son kapanmış 15M mum açılışı
    - Son üç kapanmış 15M mumun tepesi

    LONG:
    - EMA20
    - EMA50
    - Son kapanmış 15M mum açılışı
    - Son üç kapanmış 15M mumun dibi
    """
    last = df15.iloc[-2]
    recent = df15.iloc[-5:-1]

    ema20 = safe_float(last["ema20"])
    ema50 = safe_float(last["ema50"])
    last_open = safe_float(last["open"])

    candidates = [
        ("EMA20", ema20),
        ("EMA50", ema50),
        ("15M mum açılışı", last_open),
    ]

    if direction == "SHORT":
        candidates.append(
            ("15M yakın direnç", safe_float(recent["high"].max()))
        )
    else:
        candidates.append(
            ("15M yakın destek", safe_float(recent["low"].min()))
        )

    valid_candidates = [
        (name, value)
        for name, value in candidates
        if value > 0
    ]

    if not valid_candidates:
        return current_price, 0.0, "güncel fiyat"

    name, reference = min(
        valid_candidates,
        key=lambda item: percent_distance(
            current_price,
            item[1],
        ),
    )

    return (
        reference,
        percent_distance(current_price, reference),
        name,
    )


def closed_15m_confirmation(
    direction: str,
    df15: pd.DataFrame,
) -> bool:
    last = df15.iloc[-2]
    prev = df15.iloc[-3]

    close = safe_float(last["close"])
    open_price = safe_float(last["open"])
    ema20 = safe_float(last["ema20"])
    prev_close = safe_float(prev["close"])

    if direction == "LONG":
        return (
            close > open_price
            and close >= ema20
            and close > prev_close
        )

    return (
        close < open_price
        and close <= ema20
        and close < prev_close
    )


# =========================================================
# MESAJ
# =========================================================

def build_signal_message(signal: Dict[str, Any]) -> str:
    direction = signal["direction"]
    icon = "🟢" if direction == "LONG" else "🔴"
    quality = str(signal.get("quality", ""))
    source = signal.get("source", "")

    if signal["signal_class"] == "TRADE":
        if source == "5M_RADAR":
            title = "⚡ ERKEN ONAYLI MTF FUTURES SİNYALİ"
        elif quality.startswith("A+"):
            title = "🚀 A+ ANA MTF FUTURES SİNYALİ"
        elif quality.startswith("A-"):
            title = "📈 A- TP1 ODAKLI MTF FUTURES SİNYALİ"
        elif quality.startswith("TAKİP"):
            title = "⚠️ MTF TAKİP SİNYALİ - DİKKATLİ OL"
        else:
            title = "📈 A KALİTE DİKKATLİ MTF FUTURES SİNYALİ"
    else:
        title = "🔎 5M / 15M RADAR - İŞLEM AÇMA"

    ideal_entry = signal.get("ideal_entry", signal["entry"])
    zone_distance = safe_float(signal.get("zone_distance_percent", 0.0))
    zone_name = signal.get("zone_name", "giriş bölgesi")

    return f"""{title}

{icon} {direction}
🟡 Coin: {signal["symbol"]}
⏱️ Kaynak: {source}

📌 Giriş: {format_price(signal["entry"])}
📍 İdeal Bölge: {format_price(ideal_entry)} ({zone_name})
📏 Bölge Uzaklığı: %{round(zone_distance, 3)}
🎯 TP1: {format_price(signal["tp1"])}
🎯 TP2: {format_price(signal["tp2"])}
🎯 TP3: {format_price(signal["tp3"])}
🛑 SL: {format_price(signal["sl"])}

📊 Skor: %{signal["score"]} ({signal["quality"]})
🧠 Kalite Notu: {signal.get("quality_note", "-")}
📈 R/R TP1: {round(signal["rr_tp1"], 2)}
📈 R/R TP2: {round(signal["rr_tp2"], 2)}
🛡️ Stop Mesafesi: %{round(signal["risk_percent"], 2)}
⚙️ Kaldıraç Önerisi: {signal["leverage"]}

🧭 Çoklu Zaman Dilimi:
• 4H: {signal["trend_reason"]}
• 1H: {signal["confirm_reason"]}
• 15M: {signal["entry_reason"]}
• 5M: {signal["radar_reason"]}

📊 Göstergeler:
• Hacim: {signal["volume_ratio"]}x
• RSI 15M: {signal["rsi_15m"]}
• ADX 15M: {signal["adx_15m"]}
• 4H ADX: {signal["adx_4h"]}
• 1H ADX: {signal["adx_1h"]}

📌 İşlem Kuralı:
• Fiyat ideal giriş bölgesinden uzaklaştıysa girme.
• Mesaj gelmeden önce SL bölgesi görüldüyse girme.
• TP1'e yaklaşmışsa girme.
• TP1 gelirse %50 kâr al, SL'yi girişe çek.
• Stop mutlaka girilmeli.
• Marjin: Isolated.
• Kaldıraç düşük tutulmalı.

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işlem açma.
"""


def build_signal(
    *,
    symbol: str,
    direction: str,
    source: str,
    signal_class: str,
    entry: float,
    targets: Dict[str, float],
    score: int,
    trend_reason: str,
    confirm_reason: str,
    entry_reason: str,
    radar_reason: str,
    rsi: float,
    adx: float,
    volume_ratio: float,
    trend_info: Dict[str, Any],
    confirm_info: Dict[str, Any],
    ideal_entry: float,
    zone_distance: float,
    zone_name: str,
    extra_quality_note: str = "",
) -> Dict[str, Any]:
    adx_4h = safe_float(trend_info.get("adx_4h", 0))
    adx_1h = safe_float(confirm_info.get("adx_1h", 0))

    quality, quality_note = smart_quality_label(
        signal_class,
        direction,
        score,
        volume_ratio,
        rsi,
        adx,
        adx_4h,
        adx_1h,
        targets["risk_percent"],
    )

    if extra_quality_note:
        quality_note = (
            quality_note
            + " "
            + extra_quality_note
        ).strip()

    signal = {
        "symbol": symbol,
        "direction": direction,
        "source": source,
        "signal_class": signal_class,
        "entry": round(entry, 10),
        "ideal_entry": round(ideal_entry, 10),
        "zone_distance_percent": round(zone_distance, 3),
        "zone_name": zone_name,
        "tp1": round(targets["tp1"], 10),
        "tp2": round(targets["tp2"], 10),
        "tp3": round(targets["tp3"], 10),
        "sl": round(targets["sl"], 10),
        "risk_percent": round(targets["risk_percent"], 3),
        "rr_tp1": round(targets["rr_tp1"], 3),
        "rr_tp2": round(targets["rr_tp2"], 3),
        "rr_tp3": round(targets["rr_tp3"], 3),
        "score": int(score),
        "trend_reason": trend_reason,
        "confirm_reason": confirm_reason,
        "entry_reason": entry_reason,
        "radar_reason": radar_reason,
        "rsi_15m": round(rsi, 2),
        "adx_15m": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2),
        "adx_4h": trend_info.get("adx_4h", "-"),
        "adx_1h": confirm_info.get("adx_1h", "-"),
        "quality": quality,
        "quality_note": quality_note,
    }

    signal["leverage"] = leverage_suggestion(
        signal["risk_percent"]
    )

    signal["message"] = build_signal_message(signal)

    return signal


# =========================================================
# 15M ANA GİRİŞ
# =========================================================

def analyze_mtf_trade(
    symbol: str,
    df15m: Optional[pd.DataFrame],
    df1h: Optional[pd.DataFrame],
    df4h: Optional[pd.DataFrame],
    current_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    trend, trend_reason, trend_info = get_4h_trend(df4h)
    confirm, confirm_reason, confirm_info = get_1h_confirm(df1h)

    df15 = add_indicators(df15m)

    if df15 is None or len(df15) < 50:
        return None

    last = df15.iloc[-2]
    prev = df15.iloc[-3]
    recent = df15.iloc[-8:-2]

    entry = (
        safe_float(current_price)
        if safe_float(current_price) > 0
        else safe_float(last["close"])
    )

    atr = safe_float(last["atr"])
    rsi = safe_float(last["rsi"])
    adx = safe_float(last["adx"])
    volume_ratio = safe_float(last["volume_ratio"])

    close = safe_float(last["close"])
    open_price = safe_float(last["open"])
    ema20 = safe_float(last["ema20"])
    ema50 = safe_float(last["ema50"])

    macd_value = safe_float(last["macd"])
    macd_signal = safe_float(last["macd_signal"])
    macd_hist = safe_float(last["macd_hist"])
    prev_macd_hist = safe_float(prev["macd_hist"])

    if entry <= 0 or atr <= 0:
        return None

    direction = None
    entry_reason = ""

    touched_ema_long = (
        safe_float(recent["low"].min())
        <= safe_float(recent["ema20"].iloc[-1]) * 1.006
        or safe_float(recent["low"].min())
        <= safe_float(recent["ema50"].iloc[-1]) * 1.006
    )

    touched_ema_short = (
        safe_float(recent["high"].max())
        >= safe_float(recent["ema20"].iloc[-1]) * 0.994
        or safe_float(recent["high"].max())
        >= safe_float(recent["ema50"].iloc[-1]) * 0.994
    )

    bullish_reclaim = (
        close > open_price
        and close >= ema20
        and close > safe_float(prev["close"])
    )

    bearish_reject = (
        close < open_price
        and close <= ema20
        and close < safe_float(prev["close"])
    )

    macd_long_ok = (
        macd_value >= macd_signal
        or macd_hist > prev_macd_hist
    )

    macd_short_ok = (
        macd_value <= macd_signal
        or macd_hist < prev_macd_hist
    )

    if (
        trend_supports_direction(
            "LONG",
            trend,
            confirm,
            strict=False,
        )
        and touched_ema_long
        and bullish_reclaim
        and macd_long_ok
        and LONG_RSI_MIN <= rsi <= LONG_RSI_MAX
    ):
        direction = "LONG"
        entry_reason = (
            "15M EMA pullback sonrası yeşil dönüş; "
            "geç giriş koruması aktif"
        )

    if (
        trend_supports_direction(
            "SHORT",
            trend,
            confirm,
            strict=False,
        )
        and touched_ema_short
        and bearish_reject
        and macd_short_ok
        and SHORT_RSI_MIN <= rsi <= SHORT_RSI_MAX
    ):
        direction = "SHORT"
        entry_reason = (
            "15M EMA pullback sonrası kırmızı dönüş; "
            "geç giriş koruması aktif"
        )

    if direction is None:
        return None

    # MASK örneğindeki ana hata burada engellenir:
    # 15M dönüş mumu üstten/ alttan tamamlandıktan sonra fiyat çok uzaklaştıysa
    # mevcut fiyat yeni giriş olarak kullanılmaz.
    ideal_entry, zone_distance, zone_name = nearest_zone_reference(
        direction,
        entry,
        df15,
    )

    # Dönüş mumunun açılışı çoğu zaman reddedilmenin başladığı daha iyi bölgedir.
    if direction == "SHORT":
        rejection_origin = max(open_price, ema20)
    else:
        rejection_origin = min(open_price, ema20)

    rejection_distance = percent_distance(
        entry,
        rejection_origin,
    )

    if rejection_distance < zone_distance:
        ideal_entry = rejection_origin
        zone_distance = rejection_distance
        zone_name = "15M dönüş başlangıcı"

    if zone_distance > MAX_LATE_ENTRY_DISTANCE_PERCENT:
        print(
            f"{symbol}: 15M sinyal geç kaldı -> "
            f"bölge uzaklığı %{round(zone_distance, 3)}"
        )
        return None

    targets = make_targets(
        direction,
        entry,
        atr,
        df15,
    )

    if targets is None:
        return None

    # Oluşan mevcut 15M mum hedef/stop alanını daha sinyal gelmeden
    # ziyaret etmişse kurulum eski kabul edilir.
    forming = df15.iloc[-1]
    forming_high = safe_float(forming["high"])
    forming_low = safe_float(forming["low"])

    if direction == "SHORT" and forming_high >= targets["sl"]:
        print(
            f"{symbol}: SHORT kurulum sinyalden önce SL alanını gördü."
        )
        return None

    if direction == "LONG" and forming_low <= targets["sl"]:
        print(
            f"{symbol}: LONG kurulum sinyalden önce SL alanını gördü."
        )
        return None

    adx_4h = safe_float(trend_info.get("adx_4h", 0))
    adx_1h = safe_float(confirm_info.get("adx_1h", 0))

    score = 40

    if trend_supports_direction(
        direction,
        trend,
        confirm,
        strict=True,
    ):
        score += 24
    elif trend_supports_direction(
        direction,
        trend,
        confirm,
        strict=False,
    ):
        score += 14

    if volume_ratio >= 1.30:
        score += 14
    elif volume_ratio >= MIN_VOLUME_RATIO_15M:
        score += 8

    if adx >= 25:
        score += 12
    elif adx >= 18:
        score += 8
    elif adx >= 15:
        score += 4

    if direction == "LONG":
        score += 10 if 46 <= rsi <= 62 else 5
    else:
        score += 10 if 38 <= rsi <= 54 else 5

    if targets["rr_tp2"] >= 1.30:
        score += 8
    elif targets["rr_tp2"] >= 1.10:
        score += 5

    # Giriş bölgesine gerçekten yakınsa küçük kalite bonusu.
    if zone_distance <= 0.15:
        score += 4
    elif zone_distance <= 0.25:
        score += 2

    score = max(0, min(100, int(score)))

    strict_trade_ok = (
        trend_supports_direction(
            direction,
            trend,
            confirm,
            strict=True,
        )
        and volume_ratio >= MIN_VOLUME_RATIO_15M
        and adx >= 15
        and score >= MIN_SCORE_TRADE
    )

    signal_class = (
        "TRADE"
        if strict_trade_ok
        else "RADAR"
    )

    if (
        signal_class == "RADAR"
        and score < MIN_SCORE_RADAR
    ):
        return None

    if (
        signal_class == "TRADE"
        and score < MIN_SCORE_TRADE
    ):
        return None

    return build_signal(
        symbol=symbol,
        direction=direction,
        source="15M_ENTRY",
        signal_class=signal_class,
        entry=entry,
        targets=targets,
        score=score,
        trend_reason=trend_reason,
        confirm_reason=confirm_reason,
        entry_reason=entry_reason,
        radar_reason=(
            "5M erken giriş kaçtıysa 15M yalnızca ideal bölgeye "
            "yakınken kabul edilir"
        ),
        rsi=rsi,
        adx=adx,
        volume_ratio=volume_ratio,
        trend_info=trend_info,
        confirm_info=confirm_info,
        ideal_entry=ideal_entry,
        zone_distance=zone_distance,
        zone_name=zone_name,
        extra_quality_note=(
            "Giriş zamanlama kontrolü geçti; ideal bölgeden "
            f"uzaklık %{round(zone_distance, 3)}."
        ),
    )


# =========================================================
# 5M ERKEN ONAYLI GİRİŞ
# =========================================================

def analyze_5m_radar(
    symbol: str,
    df5m: Optional[pd.DataFrame],
    df15m: Optional[pd.DataFrame],
    df1h: Optional[pd.DataFrame],
    df4h: Optional[pd.DataFrame],
    current_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    if (
        df5m is None
        or df15m is None
        or len(df5m) < 50
    ):
        return None

    trend, trend_reason, trend_info = get_4h_trend(df4h)
    confirm, confirm_reason, confirm_info = get_1h_confirm(df1h)

    df15 = add_indicators(df15m)
    df5 = add_indicators(df5m)

    if (
        df15 is None
        or df5 is None
        or len(df15) < 50
        or len(df5) < 30
    ):
        return None

    last5 = df5.iloc[-2]
    prev5 = df5.iloc[-3]
    last15 = df15.iloc[-2]

    entry = (
        safe_float(current_price)
        if safe_float(current_price) > 0
        else safe_float(last5["close"])
    )

    if entry <= 0:
        return None

    move5 = candle_move_percent(last5)
    vol5_ratio = safe_float(last5["volume_ratio"])
    rsi5 = safe_float(last5["rsi"])

    atr15 = safe_float(last15["atr"])
    rsi15 = safe_float(last15["rsi"])
    adx15 = safe_float(last15["adx"])
    volume15 = safe_float(last15["volume_ratio"])

    close5 = safe_float(last5["close"])
    open5 = safe_float(last5["open"])
    prev_close5 = safe_float(prev5["close"])

    close_power5 = close_power_percent(last5)
    upper_wick5 = upper_wick_percent(last5)
    lower_wick5 = lower_wick_percent(last5)

    direction = None

    # Önce 4H + 1H kesin yön belirler.
    if trend == "LONG" and confirm == "LONG":
        direction = "LONG"
    elif trend == "SHORT" and confirm == "SHORT":
        direction = "SHORT"
    else:
        return None

    # 15M tam giriş kapanışı zaten oluşmuşsa aynı coinde hem normal hem
    # erken sinyal üretme. Bu durumda analyze_mtf_trade görev yapar.
    if closed_15m_confirmation(direction, df15):
        return None

    ideal_entry, zone_distance, zone_name = nearest_zone_reference(
        direction,
        entry,
        df15,
    )

    if zone_distance > MAX_EARLY_ZONE_DISTANCE_PERCENT:
        return None

    # Config radar hareket eşiği çok yüksekse erken girişi tamamen öldürmemek
    # için en az %0.10 kullanılır; üst hareket sınırı yine korunur.
    required_move = min(
        max(safe_float(RADAR_MIN_5M_MOVE_PERCENT), 0.0),
        EARLY_MIN_5M_MOVE_PERCENT,
    )

    if required_move <= 0:
        required_move = EARLY_MIN_5M_MOVE_PERCENT

    max_move = max(
        safe_float(RADAR_MAX_5M_MOVE_PERCENT),
        EARLY_MIN_5M_MOVE_PERCENT,
    )

    if abs(move5) < required_move:
        return None

    if abs(move5) > max_move:
        # Çok sert hareket olmuşsa artık erken giriş değil, kovalamadır.
        return None

    min_vol5 = max(
        safe_float(RADAR_MIN_VOLUME_RATIO),
        safe_float(RADAR_TRADE_MIN_VOLUME_RATIO),
        1.15,
    )

    if vol5_ratio < min_vol5:
        return None

    if adx15 < EARLY_MIN_ADX_15M:
        return None

    if volume15 < EARLY_MIN_VOLUME_15M:
        return None

    if direction == "LONG":
        bullish_body = (
            close5 > open5
            and close5 > prev_close5
            and move5 >= required_move
        )

        bullish_rejection = (
            lower_wick5 >= EARLY_MIN_REJECTION_WICK_PERCENT
            or close_power5 >= EARLY_LONG_MIN_CLOSE_POWER
        )

        if not bullish_body or not bullish_rejection:
            return None

        if not (
            EARLY_LONG_RSI_MIN
            <= rsi15
            <= EARLY_LONG_RSI_MAX
        ):
            return None

        entry_reason = (
            "15M destek/EMA bölgesinde, 15M kapanış beklenmeden "
            "5M yeşil dönüş onayı"
        )

        radar_reason = (
            f"5M erken LONG | hareket %{round(move5, 2)} | "
            f"hacim {round(vol5_ratio, 2)}x | "
            f"alt fitil %{round(lower_wick5, 1)} | "
            f"5M RSI {round(rsi5, 1)}"
        )
    else:
        bearish_body = (
            close5 < open5
            and close5 < prev_close5
            and move5 <= -required_move
        )

        bearish_rejection = (
            upper_wick5 >= EARLY_MIN_REJECTION_WICK_PERCENT
            or close_power5 <= EARLY_SHORT_MAX_CLOSE_POWER
        )

        if not bearish_body or not bearish_rejection:
            return None

        if not (
            EARLY_SHORT_RSI_MIN
            <= rsi15
            <= EARLY_SHORT_RSI_MAX
        ):
            return None

        entry_reason = (
            "15M direnç/EMA bölgesinde, 15M kapanış beklenmeden "
            "5M kırmızı reddedilme onayı"
        )

        radar_reason = (
            f"5M erken SHORT | hareket %{round(move5, 2)} | "
            f"hacim {round(vol5_ratio, 2)}x | "
            f"üst fitil %{round(upper_wick5, 1)} | "
            f"5M RSI {round(rsi5, 1)}"
        )

    targets = make_early_targets(
        direction,
        entry,
        atr15,
        df5,
    )

    if targets is None:
        return None

    # Son oluşan 5M mum stop alanını daha önce görmüşse sinyal eski kabul edilir.
    forming5 = df5.iloc[-1]
    forming_high = safe_float(forming5["high"])
    forming_low = safe_float(forming5["low"])

    if direction == "SHORT" and forming_high >= targets["sl"]:
        return None

    if direction == "LONG" and forming_low <= targets["sl"]:
        return None

    score = 50

    # 4H + 1H burada zaten kesin aynı yönde.
    score += 24

    if vol5_ratio >= 2.0:
        score += 12
    elif vol5_ratio >= 1.50:
        score += 9
    else:
        score += 6

    if adx15 >= 25:
        score += 8
    elif adx15 >= 18:
        score += 6
    else:
        score += 4

    if zone_distance <= 0.15:
        score += 6
    elif zone_distance <= 0.25:
        score += 4
    else:
        score += 2

    if direction == "LONG":
        if lower_wick5 >= 30 or close_power5 >= 70:
            score += 5
    else:
        if upper_wick5 >= 30 or close_power5 <= 30:
            score += 5

    if targets["risk_percent"] <= 1.20:
        score += 5
    elif targets["risk_percent"] <= 1.50:
        score += 3

    score = max(0, min(100, int(score)))

    can_be_trade = (
        score >= max(
            int(MIN_SCORE_TRADE),
            int(RADAR_TRADE_MIN_SCORE),
        )
        and vol5_ratio >= min_vol5
        and trend_supports_direction(
            direction,
            trend,
            confirm,
            strict=True,
        )
        and zone_distance <= MAX_EARLY_ZONE_DISTANCE_PERCENT
    )

    signal_class = (
        "TRADE"
        if can_be_trade
        else "RADAR"
    )

    if (
        signal_class == "RADAR"
        and score < MIN_SCORE_RADAR
    ):
        return None

    if (
        signal_class == "TRADE"
        and score < RADAR_TRADE_MIN_SCORE
    ):
        return None

    combined_volume = max(volume15, vol5_ratio)

    return build_signal(
        symbol=symbol,
        direction=direction,
        source="5M_RADAR",
        signal_class=signal_class,
        entry=entry,
        targets=targets,
        score=score,
        trend_reason=trend_reason,
        confirm_reason=confirm_reason,
        entry_reason=entry_reason,
        radar_reason=radar_reason,
        rsi=rsi15,
        adx=adx15,
        volume_ratio=combined_volume,
        trend_info=trend_info,
        confirm_info=confirm_info,
        ideal_entry=ideal_entry,
        zone_distance=zone_distance,
        zone_name=zone_name,
        extra_quality_note=(
            "Erken giriş: 15M dönüş tamamen bitmeden 5M teyidi alındı. "
            "Bu nedenle stop mutlaka kullanılmalı."
        ),
    )
