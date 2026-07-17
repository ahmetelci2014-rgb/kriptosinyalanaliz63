# strategy.py
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange

from config import (
    MIN_SCORE_TRADE, MIN_SCORE_WATCH, MIN_ADX_4H, MIN_ADX_1H,
    MIN_VOLUME_RATIO, LONG_RSI_MIN, LONG_RSI_MAX, SHORT_RSI_MIN,
    SHORT_RSI_MAX, RADAR_MIN_SCORE_TRADE, RADAR_MIN_SCORE_WATCH,
    RADAR_MIN_5M_MOVE_PERCENT, RADAR_MAX_5M_MOVE_PERCENT,
    RADAR_MIN_VOLUME_RATIO, MIN_RISK_PERCENT, MAX_RISK_PERCENT,
    TP1_R_MULTIPLIER, TP2_R_MULTIPLIER, TP3_R_MULTIPLIER,
)


def format_price(value):
    value = float(value)
    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.01:
        return f"{value:.6f}"
    return f"{value:.10f}"


def add_indicators(df):
    if df is None or df.empty:
        return None
    df = df.copy()
    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()
    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["atr"] = AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()
    df["adx"] = ADXIndicator(df["high"], df["low"], df["close"], window=14).adx()
    df["volume_avg"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg"]
    df["ema20_slope"] = df["ema20"] - df["ema20"].shift(3)
    return df.dropna().reset_index(drop=True)


def get_4h_trend(df4h):
    df = add_indicators(df4h)
    if df is None or len(df) < 20:
        return "NEUTRAL", "4H veri yetersiz", {}
    row = df.iloc[-2]
    close, ema20, ema50, ema200 = float(row["close"]), float(row["ema20"]), float(row["ema50"]), float(row["ema200"])
    slope, adx, rsi = float(row["ema20_slope"]), float(row["adx"]), float(row["rsi"])
    info = {"adx_4h": round(adx, 2), "rsi_4h": round(rsi, 2)}
    if close > ema200 and ema20 > ema50 and slope > 0 and adx >= MIN_ADX_4H:
        return "LONG", "4H trend yukarı", info
    if close < ema200 and ema20 < ema50 and slope < 0 and adx >= MIN_ADX_4H:
        return "SHORT", "4H trend aşağı", info
    if close > ema200 and ema20 > ema50:
        return "LONG_WEAK", "4H yukarı eğilim ama güç orta", info
    if close < ema200 and ema20 < ema50:
        return "SHORT_WEAK", "4H aşağı eğilim ama güç orta", info
    return "NEUTRAL", "4H kararsız", info


def get_1h_confirm(df1h):
    df = add_indicators(df1h)
    if df is None or len(df) < 20:
        return "NEUTRAL", "1H veri yetersiz", {}
    row = df.iloc[-2]
    close, ema20, ema50, ema200 = float(row["close"]), float(row["ema20"]), float(row["ema50"]), float(row["ema200"])
    macd_value, macd_signal = float(row["macd"]), float(row["macd_signal"])
    adx, rsi = float(row["adx"]), float(row["rsi"])
    info = {"adx_1h": round(adx, 2), "rsi_1h": round(rsi, 2)}
    if close > ema200 and ema20 > ema50 and macd_value >= macd_signal and rsi >= 47 and adx >= MIN_ADX_1H:
        return "LONG", "1H alım onayı", info
    if close < ema200 and ema20 < ema50 and macd_value <= macd_signal and rsi <= 53 and adx >= MIN_ADX_1H:
        return "SHORT", "1H satış onayı", info
    if close > ema20 and ema20 >= ema50 and macd_value >= macd_signal:
        return "LONG_WEAK", "1H hafif alım eğilimi", info
    if close < ema20 and ema20 <= ema50 and macd_value <= macd_signal:
        return "SHORT_WEAK", "1H hafif satış eğilimi", info
    return "NEUTRAL", "1H kararsız", info


def direction_allowed_by_trend(direction, trend, confirm, trade=True):
    if direction == "LONG":
        if trade:
            return trend == "LONG" and confirm == "LONG"
        return trend in ["LONG", "LONG_WEAK", "NEUTRAL"] and confirm in ["LONG", "LONG_WEAK", "NEUTRAL"]
    if direction == "SHORT":
        if trade:
            return trend == "SHORT" and confirm == "SHORT"
        return trend in ["SHORT", "SHORT_WEAK", "NEUTRAL"] and confirm in ["SHORT", "SHORT_WEAK", "NEUTRAL"]
    return False


def leverage_suggestion(risk_percent):
    risk_percent = float(risk_percent)
    if risk_percent <= 0.85:
        return "3x"
    if risk_percent <= 1.60:
        return "2x"
    if risk_percent <= 2.30:
        return "1x-2x"
    return "1x veya pas geç"


def quality_label(score, signal_class):
    if signal_class == "WATCH":
        return "B Takip"
    if score >= 90:
        return "A+"
    if score >= 84:
        return "A"
    return "A-"


def make_targets(direction, entry, atr, df15):
    recent = df15.iloc[-14:-2]
    if direction == "LONG":
        swing_low = float(recent["low"].min())
        sl = min(swing_low - atr * 0.15, entry - atr * 1.15)
        risk = entry - sl
        if risk <= 0:
            return None
        tp1 = entry + risk * TP1_R_MULTIPLIER
        tp2 = entry + risk * TP2_R_MULTIPLIER
        tp3 = entry + risk * TP3_R_MULTIPLIER
    else:
        swing_high = float(recent["high"].max())
        sl = max(swing_high + atr * 0.15, entry + atr * 1.15)
        risk = sl - entry
        if risk <= 0:
            return None
        tp1 = entry - risk * TP1_R_MULTIPLIER
        tp2 = entry - risk * TP2_R_MULTIPLIER
        tp3 = entry - risk * TP3_R_MULTIPLIER
        if tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
            return None
    risk_percent = (risk / entry) * 100
    if risk_percent < MIN_RISK_PERCENT or risk_percent > MAX_RISK_PERCENT:
        return None
    return {
        "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "risk_percent": risk_percent,
        "rr_tp1": abs(tp1 - entry) / risk,
        "rr_tp2": abs(tp2 - entry) / risk,
    }


def build_signal_message(signal):
    direction = signal["direction"]
    icon = "🟢" if direction == "LONG" else "🔴"
    title = "A KALİTE TREND MOMENTUM SİNYALİ" if signal["signal_class"] == "TRADE" else "TAKİP RADARI - İŞLEM AÇMA"
    return f"""
🚀 {title}

{icon} {direction}
🟡 Coin: {signal["symbol"]}

📌 Giriş: {format_price(signal["entry"])}
🎯 TP1: {format_price(signal["tp1"])}
🎯 TP2: {format_price(signal["tp2"])}
🎯 TP3: {format_price(signal["tp3"])}
🛑 SL: {format_price(signal["sl"])}

📊 Skor: %{signal["score"]} ({signal["quality"]})
📈 R/R TP1: {round(signal["rr_tp1"], 2)}
📈 R/R TP2: {round(signal["rr_tp2"], 2)}
🛡️ Stop Mesafesi: %{round(signal["risk_percent"], 2)}
⚙️ Kaldıraç Önerisi: {signal["leverage"]}

📈 Analiz:
• 4H: {signal["trend_reason"]}
• 1H: {signal["confirm_reason"]}
• 15M: {signal["entry_reason"]}
• Hacim: {signal["volume_ratio"]}x
• RSI 15M: {signal["rsi_15m"]}
• ADX 15M: {signal["adx_15m"]}

📌 Kural:
• Girişten uzaklaştıysa girme.
• TP1'e yaklaşmışsa girme.
• TP1 gelirse %50 kâr al, SL'yi girişe çek.
• Stop mutlaka girilmeli.
• Marjin: Isolated.
• Kaldıraç düşük tutulmalı.

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işlem açma.
"""


def analyze_normal_signal(symbol, df15m, df1h, df4h, current_price=None):
    trend, trend_reason, _ = get_4h_trend(df4h)
    confirm, confirm_reason, _ = get_1h_confirm(df1h)
    df15 = add_indicators(df15m)
    if df15 is None or len(df15) < 80:
        return None
    last, prev = df15.iloc[-2], df15.iloc[-3]
    recent = df15.iloc[-8:-2]
    entry = float(current_price) if current_price is not None and current_price > 0 else float(last["close"])
    atr, rsi, adx = float(last["atr"]), float(last["rsi"]), float(last["adx"])
    volume_ratio = float(last["volume_ratio"])
    close, open_ = float(last["close"]), float(last["open"])
    ema20, ema50 = float(last["ema20"]), float(last["ema50"])
    macd_value, macd_signal = float(last["macd"]), float(last["macd_signal"])
    if entry <= 0 or atr <= 0:
        return None
    direction = None
    entry_reason = ""
    touched_ema_long = float(recent["low"].min()) <= ema20 * 1.004 or float(recent["low"].min()) <= ema50 * 1.004
    touched_ema_short = float(recent["high"].max()) >= ema20 * 0.996 or float(recent["high"].max()) >= ema50 * 0.996
    bullish_reclaim = close > open_ and close >= ema20 and close > float(prev["close"])
    bearish_reject = close < open_ and close <= ema20 and close < float(prev["close"])
    if trend in ["LONG", "LONG_WEAK"] and confirm in ["LONG", "LONG_WEAK"] and touched_ema_long and bullish_reclaim and macd_value >= macd_signal and LONG_RSI_MIN <= rsi <= LONG_RSI_MAX:
        direction = "LONG"
        entry_reason = "15M EMA pullback sonrası yeşil dönüş"
    if trend in ["SHORT", "SHORT_WEAK"] and confirm in ["SHORT", "SHORT_WEAK"] and touched_ema_short and bearish_reject and macd_value <= macd_signal and SHORT_RSI_MIN <= rsi <= SHORT_RSI_MAX:
        direction = "SHORT"
        entry_reason = "15M EMA pullback sonrası kırmızı dönüş"
    if direction is None:
        return None
    targets = make_targets(direction, entry, atr, df15)
    if targets is None:
        return None
    score = 40
    if direction_allowed_by_trend(direction, trend, confirm, trade=True):
        score += 25
    elif direction_allowed_by_trend(direction, trend, confirm, trade=False):
        score += 12
    if volume_ratio >= 1.30:
        score += 14
    elif volume_ratio >= MIN_VOLUME_RATIO:
        score += 8
    if adx >= 25:
        score += 12
    elif adx >= 18:
        score += 8
    elif adx >= 14:
        score += 4
    if direction == "LONG":
        score += 10 if 48 <= rsi <= 62 else 5
    else:
        score += 10 if 38 <= rsi <= 52 else 5
    if targets["rr_tp2"] >= 1.30:
        score += 8
    elif targets["rr_tp2"] >= 1.10:
        score += 5
    strong_trade = direction_allowed_by_trend(direction, trend, confirm, trade=True) and volume_ratio >= MIN_VOLUME_RATIO and score >= MIN_SCORE_TRADE
    signal_class = "TRADE" if strong_trade else "WATCH"
    if score < MIN_SCORE_WATCH:
        return None
    signal = {
        "symbol": symbol, "direction": direction, "source": "NORMAL", "signal_class": signal_class,
        "entry": round(entry, 10), "tp1": round(targets["tp1"], 10), "tp2": round(targets["tp2"], 10),
        "tp3": round(targets["tp3"], 10), "sl": round(targets["sl"], 10),
        "risk_percent": round(targets["risk_percent"], 3), "rr_tp1": round(targets["rr_tp1"], 3),
        "rr_tp2": round(targets["rr_tp2"], 3), "score": int(score),
        "trend_reason": trend_reason, "confirm_reason": confirm_reason, "entry_reason": entry_reason,
        "rsi_15m": round(rsi, 2), "adx_15m": round(adx, 2), "volume_ratio": round(volume_ratio, 2),
    }
    signal["quality"] = quality_label(signal["score"], signal_class)
    signal["leverage"] = leverage_suggestion(signal["risk_percent"])
    signal["message"] = build_signal_message(signal)
    return signal


def analyze_radar_signal(symbol, df5m, df15m, df1h, df4h, current_price=None):
    if df5m is None or df15m is None or len(df5m) < 50:
        return None
    trend, trend_reason, _ = get_4h_trend(df4h)
    confirm, confirm_reason, _ = get_1h_confirm(df1h)
    df15 = add_indicators(df15m)
    if df15 is None or len(df15) < 80:
        return None
    last5 = df5m.iloc[-2]
    entry = float(current_price) if current_price is not None and current_price > 0 else float(last5["close"])
    move5 = ((float(last5["close"]) - float(last5["open"])) / float(last5["open"])) * 100
    vol5_avg = float(df5m["volume"].iloc[-22:-2].mean())
    vol5_ratio = float(last5["volume"]) / vol5_avg if vol5_avg > 0 else 0
    if abs(move5) < RADAR_MIN_5M_MOVE_PERCENT or abs(move5) > RADAR_MAX_5M_MOVE_PERCENT:
        return None
    if vol5_ratio < RADAR_MIN_VOLUME_RATIO:
        return None
    last15 = df15.iloc[-2]
    atr, rsi, adx = float(last15["atr"]), float(last15["rsi"]), float(last15["adx"])
    volume_ratio = max(float(last15["volume_ratio"]), vol5_ratio)
    if move5 > 0:
        direction = "LONG"
        if rsi > LONG_RSI_MAX or trend == "SHORT" or confirm == "SHORT":
            return None
    else:
        direction = "SHORT"
        if rsi < SHORT_RSI_MIN or trend == "LONG" or confirm == "LONG":
            return None
    targets = make_targets(direction, entry, atr, df15)
    if targets is None:
        return None
    score = 42
    if direction_allowed_by_trend(direction, trend, confirm, trade=True):
        score += 22
    elif direction_allowed_by_trend(direction, trend, confirm, trade=False):
        score += 10
    if vol5_ratio >= 2.0:
        score += 16
    elif vol5_ratio >= 1.50:
        score += 12
    else:
        score += 8
    if adx >= 25:
        score += 10
    elif adx >= 18:
        score += 7
    score += 8 if abs(move5) <= 0.85 else 4
    signal_class = "TRADE" if (
        score >= RADAR_MIN_SCORE_TRADE
        and (
            direction_allowed_by_trend(direction, trend, confirm, trade=True)
            or (
                direction_allowed_by_trend(direction, trend, confirm, trade=False)
                and score >= RADAR_MIN_SCORE_TRADE + 4
            )
        )
    ) else "WATCH"
    if score < RADAR_MIN_SCORE_WATCH:
        return None
    signal = {
        "symbol": symbol, "direction": direction, "source": "RADAR", "signal_class": signal_class,
        "entry": round(entry, 10), "tp1": round(targets["tp1"], 10), "tp2": round(targets["tp2"], 10),
        "tp3": round(targets["tp3"], 10), "sl": round(targets["sl"], 10),
        "risk_percent": round(targets["risk_percent"], 3), "rr_tp1": round(targets["rr_tp1"], 3),
        "rr_tp2": round(targets["rr_tp2"], 3), "score": int(score),
        "trend_reason": trend_reason, "confirm_reason": confirm_reason, "entry_reason": f"5M momentum radarı: %{round(move5, 2)}",
        "rsi_15m": round(rsi, 2), "adx_15m": round(adx, 2), "volume_ratio": round(volume_ratio, 2), "move_5m": round(move5, 2),
    }
    signal["quality"] = quality_label(signal["score"], signal_class)
    signal["leverage"] = leverage_suggestion(signal["risk_percent"])
    signal["message"] = build_signal_message(signal)
    return signal
