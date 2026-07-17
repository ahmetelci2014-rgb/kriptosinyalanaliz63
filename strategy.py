# strategy.py
# Premium GitHub V4 - Destek Direnç Futures strateji motoru

import math
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange

from config import (
    SR_LOOKBACK,
    SR_ZONE_PERCENT,
    MAX_DISTANCE_TO_ENTRY_ZONE_PERCENT,
    MIN_DISTANCE_TO_TARGET_PERCENT,
    MIN_SCORE_TRADE,
    MIN_SCORE_WATCH,
    MIN_ADX_4H,
    MIN_ADX_1H,
    MIN_VOLUME_RATIO,
    MIN_RR_TP1,
    MIN_RR_TP2,
    TP1_R_MULTIPLIER,
    TP2_R_MULTIPLIER,
    TP3_R_MULTIPLIER,
    MIN_TP1_R_MULTIPLIER,
    RADAR_MIN_MOVE_PERCENT,
    RADAR_MAX_MOVE_PERCENT,
    RADAR_MIN_VOLUME_RATIO,
    RADAR_LONG_MAX_RSI,
    RADAR_SHORT_MIN_RSI,
    MAX_ENTRY_DISTANCE_PERCENT,
    MAX_TP1_PROGRESS_PERCENT,
    MIN_TRADE_RISK_PERCENT,
    MIN_TRADE_VOLUME_RATIO,
    REQUIRE_15M_REVERSAL_CANDLE,
    REQUIRE_15M_EMA20_CONFIRM,
    MIN_RISK_PERCENT,
    MAX_RISK_PERCENT,
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

    df["atr"] = AverageTrueRange(
        df["high"],
        df["low"],
        df["close"],
        window=14
    ).average_true_range()

    df["adx"] = ADXIndicator(
        df["high"],
        df["low"],
        df["close"],
        window=14
    ).adx()

    df["volume_avg"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg"]
    df["ema20_slope"] = df["ema20"] - df["ema20"].shift(3)

    return df.dropna().reset_index(drop=True)


def percent_distance(a, b):
    if b == 0:
        return 999
    return abs((a - b) / b) * 100


def get_trend_4h(df4h):
    df = add_indicators(df4h)

    if df is None or len(df) < 20:
        return "NEUTRAL", "4H veri yetersiz", {}

    row = df.iloc[-2]

    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    ema200 = float(row["ema200"])
    slope = float(row["ema20_slope"])
    adx = float(row["adx"])
    rsi = float(row["rsi"])

    info = {
        "adx_4h": round(adx, 2),
        "rsi_4h": round(rsi, 2)
    }

    if close > ema200 and ema20 > ema50 and slope > 0 and adx >= MIN_ADX_4H:
        return "LONG", "4H ana trend yukarı", info

    if close < ema200 and ema20 < ema50 and slope < 0 and adx >= MIN_ADX_4H:
        return "SHORT", "4H ana trend aşağı", info

    # Hafif trend: çok katı kalmasın diye nötr ama yön bilgisi verir.
    if close > ema200 and ema20 > ema50:
        return "LONG", "4H yukarı eğilim", info

    if close < ema200 and ema20 < ema50:
        return "SHORT", "4H aşağı eğilim", info

    return "NEUTRAL", "4H kararsız", info


def get_confirm_1h(df1h):
    df = add_indicators(df1h)

    if df is None or len(df) < 20:
        return "NEUTRAL", "1H veri yetersiz", {}

    row = df.iloc[-2]

    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    ema200 = float(row["ema200"])
    macd_value = float(row["macd"])
    macd_signal = float(row["macd_signal"])
    adx = float(row["adx"])
    rsi = float(row["rsi"])

    info = {
        "adx_1h": round(adx, 2),
        "rsi_1h": round(rsi, 2)
    }

    if close > ema200 and ema20 > ema50 and macd_value > macd_signal and rsi >= 47 and adx >= MIN_ADX_1H:
        return "LONG", "1H alım onayı", info

    if close < ema200 and ema20 < ema50 and macd_value < macd_signal and rsi <= 53 and adx >= MIN_ADX_1H:
        return "SHORT", "1H satış onayı", info

    if close > ema20 and ema20 >= ema50 and macd_value > macd_signal:
        return "LONG", "1H hafif alım eğilimi", info

    if close < ema20 and ema20 <= ema50 and macd_value < macd_signal:
        return "SHORT", "1H hafif satış eğilimi", info

    return "NEUTRAL", "1H kararsız", info


def calculate_support_resistance(df, lookback=SR_LOOKBACK):
    if df is None or len(df) < lookback + 5:
        return None

    recent = df.iloc[-lookback - 1:-1].copy()
    close = float(df.iloc[-2]["close"])

    highs = list(recent["high"].astype(float))
    lows = list(recent["low"].astype(float))

    supports = sorted([x for x in lows if x < close], reverse=True)
    resistances = sorted([x for x in highs if x > close])

    support = supports[0] if supports else float(recent["low"].min())
    resistance = resistances[0] if resistances else float(recent["high"].max())

    # Daha anlamlı seviye için yakın dip/tepe kümelerinin medyan benzeri ortalaması
    support_zone = [x for x in lows if abs((x - support) / support) * 100 <= SR_ZONE_PERCENT] if support > 0 else []
    resistance_zone = [x for x in highs if abs((x - resistance) / resistance) * 100 <= SR_ZONE_PERCENT] if resistance > 0 else []

    if support_zone:
        support = sum(support_zone) / len(support_zone)

    if resistance_zone:
        resistance = sum(resistance_zone) / len(resistance_zone)

    return {
        "support": float(support),
        "resistance": float(resistance),
        "support_distance": percent_distance(close, support),
        "resistance_distance": percent_distance(close, resistance)
    }


def leverage_suggestion(risk_percent):
    risk_percent = float(risk_percent)

    if risk_percent <= 0.80:
        return "3x"
    if risk_percent <= 1.60:
        return "2x"
    if risk_percent <= 2.40:
        return "1x-2x"

    return "1x veya işlem yok"


def quality_label(score):
    if score >= 86:
        return "A"
    if score >= 78:
        return "A-"
    if score >= 66:
        return "B Takip"
    return "Zayıf"


def build_signal_message(signal):
    direction = signal["direction"]
    icon = "🟢" if direction == "LONG" else "🔴"
    signal_type = "A KALİTE FUTURES GİRİŞİ" if signal["signal_class"] == "TRADE" else "B KALİTE TAKİP RADARI"

    return f"""
🚀 {signal_type}

{icon} {direction}
🟡 Coin: {signal["symbol"]}

🔥 Giriş Bölgesi: {format_price(signal["entry_low"])} - {format_price(signal["entry_high"])}
📌 Önerilen Giriş: {format_price(signal["entry"])}
🟢 Destek: {format_price(signal["support"])}
🔴 Direnç: {format_price(signal["resistance"])}

🎯 TP1: {format_price(signal["tp1"])}
🎯 TP2: {format_price(signal["tp2"])}
🎯 TP3: {format_price(signal["tp3"])}
🛑 SL: {format_price(signal["sl"])}

📊 Skor: %{signal["score"]} ({signal["quality"]})
📈 R/R TP1: {round(signal["rr_tp1"], 2)}
📌 TP1: Erken kâr hedefi
📈 R/R TP2: {round(signal["rr_tp2"], 2)}
🛡️ Stop Mesafesi: %{round(signal["risk_percent"], 2)}
⚙️ Kaldıraç Önerisi: {signal["leverage"]}

📈 Analiz:
• 4H: {signal["trend_reason"]}
• 1H: {signal["confirm_reason"]}
• Destek/Direnç: {signal["sr_reason"]}
• Hacim: {signal["volume_ratio"]}x
• RSI 15M: {signal["rsi_15m"]}
• ADX 15M: {signal["adx_15m"]}

📌 Kural:
• Giriş bölgesinden uzaklaştıysa girme.
• TP1'e yaklaşmışsa girme.
• TP1 gelirse %50 kâr al, SL'yi girişe çek.
• Stop mutlaka girilmeli.
• Marjin: Isolated.
• Aynı coinde ikinci işlem açma.

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işlem açma.
"""


def build_targets_with_sr(direction, entry, atr, support, resistance):
    """
    V4.2 düzeltmesi:
    Önceki sürümlerde TP1 bazı işlemlerde fazla uzak kalabiliyordu.
    Burada TP1 daha yakın alınır.
    TP2 ana hedef, TP3 ekstra hedef gibi çalışır.
    """
    if direction == "LONG":
        sl = min(support - atr * 0.45, entry - atr * 1.30)
        risk = entry - sl

        if risk <= 0:
            return None

        default_tp1 = entry + risk * TP1_R_MULTIPLIER
        default_tp2 = entry + risk * TP2_R_MULTIPLIER
        default_tp3 = entry + risk * TP3_R_MULTIPLIER

        if resistance > entry:
            tp1 = min(resistance, default_tp1)

            if (tp1 - entry) / risk < MIN_TP1_R_MULTIPLIER:
                tp1 = default_tp1
        else:
            tp1 = default_tp1

        tp2 = max(default_tp2, tp1 + risk * 0.35)
        tp3 = max(default_tp3, tp2 + risk * 0.35)

    else:
        sl = max(resistance + atr * 0.45, entry + atr * 1.30)
        risk = sl - entry

        if risk <= 0:
            return None

        default_tp1 = entry - risk * TP1_R_MULTIPLIER
        default_tp2 = entry - risk * TP2_R_MULTIPLIER
        default_tp3 = entry - risk * TP3_R_MULTIPLIER

        if support < entry:
            tp1 = max(support, default_tp1)

            if (entry - tp1) / risk < MIN_TP1_R_MULTIPLIER:
                tp1 = default_tp1
        else:
            tp1 = default_tp1

        tp2 = min(default_tp2, tp1 - risk * 0.35)
        tp3 = min(default_tp3, tp2 - risk * 0.35)

        if tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
            return None

    risk_percent = (risk / entry) * 100
    rr_tp1 = abs(tp1 - entry) / risk
    rr_tp2 = abs(tp2 - entry) / risk

    return {
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_percent": risk_percent,
        "rr_tp1": rr_tp1,
        "rr_tp2": rr_tp2
    }

def analyze_futures_setup(symbol, df5m, df15m, df1h, df4h, current_price=None):
    trend, trend_reason, trend_info = get_trend_4h(df4h)
    confirm, confirm_reason, confirm_info = get_confirm_1h(df1h)

    df15 = add_indicators(df15m)
    if df15 is None or len(df15) < 100:
        return None

    last = df15.iloc[-2]
    prev = df15.iloc[-3]

    entry = float(current_price) if current_price is not None and current_price > 0 else float(last["close"])
    atr = float(last["atr"])
    rsi = float(last["rsi"])
    adx = float(last["adx"])
    volume_ratio = float(last["volume_ratio"])

    last_open = float(last["open"])
    last_close = float(last["close"])
    last_high = float(last["high"])
    last_low = float(last["low"])
    prev_open = float(prev["open"])
    prev_close = float(prev["close"])
    ema20 = float(last["ema20"])

    bullish_reversal = (
        last_close > last_open
        and last_close >= ema20
        and last_close > prev_close
    )

    bearish_reversal = (
        last_close < last_open
        and last_close <= ema20
        and last_close < prev_close
    )

    if entry <= 0 or atr <= 0:
        return None

    sr = calculate_support_resistance(df15)
    if sr is None:
        return None

    support = sr["support"]
    resistance = sr["resistance"]

    direction = None
    setup_reason = ""
    sr_reason = ""

    if trend == "LONG" and confirm in ["LONG", "NEUTRAL"]:
        near_support = percent_distance(entry, support) <= MAX_DISTANCE_TO_ENTRY_ZONE_PERCENT
        enough_to_resistance = percent_distance(resistance, entry) >= MIN_DISTANCE_TO_TARGET_PERCENT
        ema_recovery = last_close >= ema20 or float(prev["low"]) <= float(prev["ema20"])
        reversal_ok = bullish_reversal if REQUIRE_15M_REVERSAL_CANDLE else True
        ema_ok = last_close >= ema20 if REQUIRE_15M_EMA20_CONFIRM else ema_recovery

        if near_support and enough_to_resistance and ema_recovery:
            direction = "LONG"
            setup_reason = "Destek bölgesinden trend yönlü LONG"
            sr_reason = "Fiyat desteğe yakın, direnç hedef alanı uygun"

    if trend == "SHORT" and confirm in ["SHORT", "NEUTRAL"]:
        near_resistance = percent_distance(entry, resistance) <= MAX_DISTANCE_TO_ENTRY_ZONE_PERCENT
        enough_to_support = percent_distance(entry, support) >= MIN_DISTANCE_TO_TARGET_PERCENT
        ema_reject = last_close <= ema20 or float(prev["high"]) >= float(prev["ema20"])
        reversal_ok = bearish_reversal if REQUIRE_15M_REVERSAL_CANDLE else True
        ema_ok = last_close <= ema20 if REQUIRE_15M_EMA20_CONFIRM else ema_reject

        if near_resistance and enough_to_support and ema_reject:
            direction = "SHORT"
            setup_reason = "Direnç bölgesinden trend yönlü SHORT"
            sr_reason = "Fiyat dirence yakın, destek hedef alanı uygun"

    # Radar destekli alternatif: fiyat bölgeden uzak değilse ve hacim/momentum varsa takip adayı olabilir.
    if direction is None and df5m is not None and len(df5m) > 35:
        last5 = df5m.iloc[-2]
        move5 = ((float(last5["close"]) - float(last5["open"])) / float(last5["open"])) * 100
        vol5_avg = float(df5m["volume"].iloc[-22:-2].mean())
        vol5_ratio = float(last5["volume"]) / vol5_avg if vol5_avg > 0 else 0

        if abs(move5) >= RADAR_MIN_MOVE_PERCENT and abs(move5) <= RADAR_MAX_MOVE_PERCENT and vol5_ratio >= RADAR_MIN_VOLUME_RATIO:
            if move5 > 0 and trend != "SHORT" and rsi <= RADAR_LONG_MAX_RSI:
                direction = "LONG"
                setup_reason = "5M hareket + destek/direnç takip radarı"
                sr_reason = "Radar hareketi var, A kalite için bölge/onay beklenmeli"
            elif move5 < 0 and trend != "LONG" and rsi >= RADAR_SHORT_MIN_RSI:
                direction = "SHORT"
                setup_reason = "5M hareket + destek/direnç takip radarı"
                sr_reason = "Radar hareketi var, A kalite için bölge/onay beklenmeli"

    if direction is None:
        return None

    if direction == "LONG":
        entry_low = min(entry, support * (1 + SR_ZONE_PERCENT / 100))
        entry_high = max(entry, support * (1 + SR_ZONE_PERCENT / 100))
    else:
        entry_low = min(entry, resistance * (1 - SR_ZONE_PERCENT / 100))
        entry_high = max(entry, resistance * (1 - SR_ZONE_PERCENT / 100))

    targets = build_targets_with_sr(direction, entry, atr, support, resistance)
    if targets is None:
        return None

    risk_percent = targets["risk_percent"]
    if risk_percent < MIN_RISK_PERCENT or risk_percent > MAX_RISK_PERCENT:
        return None

    if direction == "LONG":
        reversal_reason = "15M yeşil kapanış + EMA20 üstü" if bullish_reversal and last_close >= ema20 else "Dönüş zayıf / takip"
        trade_reversal_ok = bullish_reversal and last_close >= ema20
    else:
        reversal_reason = "15M kırmızı kapanış + EMA20 altı" if bearish_reversal and last_close <= ema20 else "Dönüş zayıf / takip"
        trade_reversal_ok = bearish_reversal and last_close <= ema20

    strong_trade_conditions = (
        trade_reversal_ok
        and risk_percent >= MIN_TRADE_RISK_PERCENT
        and volume_ratio >= MIN_TRADE_VOLUME_RATIO
        and confirm == direction
        and trend == direction
    )

    score = 40

    if trend == direction:
        score += 15
    elif trend != "NEUTRAL":
        score += 6

    if confirm == direction:
        score += 15
    elif confirm == "NEUTRAL":
        score += 5

    if adx >= 25:
        score += 12
    elif adx >= 18:
        score += 8

    if volume_ratio >= 1.25:
        score += 12
    elif volume_ratio >= MIN_VOLUME_RATIO:
        score += 7

    if direction == "LONG":
        if 45 <= rsi <= 62:
            score += 10
        elif rsi <= 70:
            score += 5

        if percent_distance(entry, support) <= MAX_DISTANCE_TO_ENTRY_ZONE_PERCENT:
            score += 12

    else:
        if 38 <= rsi <= 55:
            score += 10
        elif rsi >= 35:
            score += 5

        if percent_distance(entry, resistance) <= MAX_DISTANCE_TO_ENTRY_ZONE_PERCENT:
            score += 12

    if targets["rr_tp1"] >= MIN_RR_TP1:
        score += 6

    if targets["rr_tp2"] >= MIN_RR_TP2:
        score += 6

    signal_class = "TRADE" if (
        score >= MIN_SCORE_TRADE
        and targets["rr_tp2"] >= MIN_RR_TP2
        and volume_ratio >= MIN_VOLUME_RATIO
        and strong_trade_conditions
    ) else "WATCH"

    if score < MIN_SCORE_WATCH:
        return None

    signal = {
        "symbol": symbol,
        "direction": direction,
        "source": "SR",
        "signal_class": signal_class,
        "entry": round(entry, 10),
        "entry_low": round(entry_low, 10),
        "entry_high": round(entry_high, 10),
        "support": round(support, 10),
        "resistance": round(resistance, 10),
        "tp1": round(targets["tp1"], 10),
        "tp2": round(targets["tp2"], 10),
        "tp3": round(targets["tp3"], 10),
        "sl": round(targets["sl"], 10),
        "risk_percent": round(risk_percent, 3),
        "rr_tp1": round(targets["rr_tp1"], 3),
        "rr_tp2": round(targets["rr_tp2"], 3),
        "score": int(score),
        "quality": quality_label(score),
        "leverage": leverage_suggestion(risk_percent),
        "trend_reason": trend_reason,
        "confirm_reason": confirm_reason,
        "entry_reason": setup_reason,
        "sr_reason": sr_reason,
        "reversal_reason": reversal_reason,
        "rsi_15m": round(rsi, 2),
        "adx_15m": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2)
    }

    signal["message"] = build_signal_message(signal)
    return signal
