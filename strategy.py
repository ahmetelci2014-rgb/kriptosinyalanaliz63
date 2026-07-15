# strategy.py
# Premium GitHub V2 strateji motoru
# Normal sinyal: 4H trend + 1H onay + 15M giriş
# Radar sinyal: 5M ani hareket + hacim + 15M destek + 1H ters olmaması

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange

from config import (
    TRADE_MIN_SCORE,
    RADAR_MIN_SCORE,
    MIN_ADX_4H,
    MIN_ADX_1H,
    MIN_VOLUME_RATIO,
    MIN_RISK_PERCENT,
    MAX_RISK_PERCENT,
    RADAR_MIN_RISK_PERCENT,
    RADAR_MAX_RISK_PERCENT,
    RADAR_MIN_5M_MOVE_PERCENT,
    RADAR_MAX_5M_MOVE_PERCENT,
    RADAR_MIN_15M_MOVE_PERCENT,
    RADAR_MIN_VOLUME_RATIO,
    RADAR_MAX_CURRENT_FROM_CLOSE_PERCENT,
    RADAR_LONG_MAX_RSI,
    RADAR_SHORT_MIN_RSI,
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


def get_trend_4h(df4h):
    df = add_indicators(df4h)

    if df is None or len(df) < 10:
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

    return "NEUTRAL", "4H kararsız", info


def get_confirm_1h(df1h):
    df = add_indicators(df1h)

    if df is None or len(df) < 10:
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

    if close > ema200 and ema20 > ema50 and macd_value > macd_signal and rsi >= 48 and adx >= MIN_ADX_1H:
        return "LONG", "1H alım onayı", info

    if close < ema200 and ema20 < ema50 and macd_value < macd_signal and rsi <= 52 and adx >= MIN_ADX_1H:
        return "SHORT", "1H satış onayı", info

    return "NEUTRAL", "1H kararsız", info


def build_targets(direction, entry, atr, recent_high, recent_low, risk_mode="normal"):
    if direction == "LONG":
        if risk_mode == "radar":
            sl_atr = entry - atr * 1.25
        else:
            sl_atr = entry - atr * 1.70

        sl_swing = recent_low - atr * 0.20
        sl = min(sl_atr, sl_swing)
        risk = entry - sl

        if risk <= 0:
            return None

        tp1 = entry + risk * 1.00
        tp2 = entry + risk * 1.70
        tp3 = entry + risk * 2.50

    else:
        if risk_mode == "radar":
            sl_atr = entry + atr * 1.25
        else:
            sl_atr = entry + atr * 1.70

        sl_swing = recent_high + atr * 0.20
        sl = max(sl_atr, sl_swing)
        risk = sl - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * 1.00
        tp2 = entry - risk * 1.70
        tp3 = entry - risk * 2.50

        if tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
            return None

    risk_percent = (risk / entry) * 100

    return {
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_percent": risk_percent
    }


def make_message(signal):
    direction = signal["direction"]
    icon = "🟢" if direction == "LONG" else "🔴"
    source_text = "PREMIUM GİRİŞ SİNYALİ" if signal["source"] == "NORMAL" else "ANLIK RADAR GİRİŞ ADAYI"
    risk_text = "Daha onaylı" if signal["source"] == "NORMAL" else "Daha hızlı, daha riskli"

    return f"""
🚀 {source_text}

{icon} {direction}
🟡 Coin: {signal["symbol"]}

🔥 Giriş: {format_price(signal["entry"])}
🎯 TP1: {format_price(signal["tp1"])}
🎯 TP2: {format_price(signal["tp2"])}
🎯 TP3: {format_price(signal["tp3"])}
🔴 SL: {format_price(signal["sl"])}

📊 Skor: %{signal["score"]}
🛡️ Stop Mesafesi: %{round(signal["risk_percent"], 2)}
📌 Sinyal Tipi: {signal["source"]}
⚠️ Risk Notu: {risk_text}

📈 Onaylar:
• 4H: {signal.get("trend_reason", "-")}
• 1H: {signal.get("confirm_reason", "-")}
• Giriş: {signal.get("entry_reason", "-")}

📊 Detay:
• 15M RSI: {signal.get("rsi_15m", "-")}
• 15M ADX: {signal.get("adx_15m", "-")}
• Hacim: {signal.get("volume_ratio", "-")}x
• 5M Hareket: %{signal.get("move_5m", "-")}

📌 İşlem Kuralı:
• Fiyat girişe yakınsa değerlendir.
• TP1'e yaklaşmışsa girme.
• TP1 gelirse %50 kâr al, SL'yi girişe çek.
• Stop mutlaka girilmeli.
• Marjin: Isolated.
• Kaldıraç: 2x - 3x.
• Aynı coinde ikinci işlem açma.

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işleme girme.
"""


def analyze_normal_signal(symbol, df15m, df1h, df4h, current_price=None):
    trend, trend_reason, trend_info = get_trend_4h(df4h)

    if trend == "NEUTRAL":
        print(symbol, "normal elendi ->", trend_reason)
        return None

    confirm, confirm_reason, confirm_info = get_confirm_1h(df1h)

    if confirm != trend:
        print(symbol, "normal elendi -> 1H uyumsuz:", confirm_reason)
        return None

    df15 = add_indicators(df15m)

    if df15 is None or len(df15) < 20:
        print(symbol, "normal elendi -> 15M veri yetersiz")
        return None

    last = df15.iloc[-2]
    prev = df15.iloc[-3]

    entry = float(last["close"])
    atr = float(last["atr"])

    if current_price is not None and current_price > 0:
        entry = float(current_price)

    if entry <= 0 or atr <= 0:
        return None

    rsi = float(last["rsi"])
    adx = float(last["adx"])
    volume_ratio = float(last["volume_ratio"])

    if volume_ratio < MIN_VOLUME_RATIO:
        print(symbol, "normal elendi -> hacim düşük:", round(volume_ratio, 2))
        return None

    entry_ok = False
    entry_reason = "-"

    if trend == "LONG":
        pullback_ok = float(prev["low"]) <= float(prev["ema20"]) * 1.008
        recovery_ok = float(last["close"]) >= float(prev["close"]) * 0.998
        ema_ok = float(last["close"]) > float(last["ema20"]) and float(last["close"]) > float(last["ema200"])
        rsi_ok = 42 <= rsi <= 68
        macd_ok = float(last["macd"]) > float(last["macd_signal"])

        if pullback_ok and recovery_ok and ema_ok and rsi_ok and macd_ok:
            entry_ok = True
            entry_reason = "15M geri çekilme sonrası yukarı dönüş"

    if trend == "SHORT":
        pullback_ok = float(prev["high"]) >= float(prev["ema20"]) * 0.992
        recovery_ok = float(last["close"]) <= float(prev["close"]) * 1.002
        ema_ok = float(last["close"]) < float(last["ema20"]) and float(last["close"]) < float(last["ema200"])
        rsi_ok = 32 <= rsi <= 60
        macd_ok = float(last["macd"]) < float(last["macd_signal"])

        if pullback_ok and recovery_ok and ema_ok and rsi_ok and macd_ok:
            entry_ok = True
            entry_reason = "15M tepki sonrası aşağı dönüş"

    if not entry_ok:
        print(symbol, "normal elendi -> 15M giriş yok")
        return None

    recent = df15.iloc[-10:-1]
    targets = build_targets(
        direction=trend,
        entry=entry,
        atr=atr,
        recent_high=float(recent["high"].max()),
        recent_low=float(recent["low"].min()),
        risk_mode="normal"
    )

    if targets is None:
        return None

    risk_percent = targets["risk_percent"]

    if risk_percent < MIN_RISK_PERCENT or risk_percent > MAX_RISK_PERCENT:
        print(symbol, "normal elendi -> risk uygunsuz:", round(risk_percent, 2))
        return None

    score = 40

    if trend_info.get("adx_4h", 0) >= 22:
        score += 15
    else:
        score += 8

    if confirm_info.get("adx_1h", 0) >= 22:
        score += 15
    else:
        score += 8

    if adx >= 20:
        score += 10
    else:
        score += 5

    if volume_ratio >= 1.20:
        score += 15
    else:
        score += 8

    if trend == "LONG" and 48 <= rsi <= 58:
        score += 10
    elif trend == "SHORT" and 38 <= rsi <= 52:
        score += 10
    else:
        score += 5

    if score < TRADE_MIN_SCORE:
        print(symbol, "normal elendi -> skor düşük:", score)
        return None

    signal = {
        "symbol": symbol,
        "direction": trend,
        "source": "NORMAL",
        "entry": round(entry, 10),
        "tp1": round(targets["tp1"], 10),
        "tp2": round(targets["tp2"], 10),
        "tp3": round(targets["tp3"], 10),
        "sl": round(targets["sl"], 10),
        "risk_percent": round(risk_percent, 3),
        "score": int(score),
        "trend_reason": trend_reason,
        "confirm_reason": confirm_reason,
        "entry_reason": entry_reason,
        "rsi_15m": round(rsi, 2),
        "adx_15m": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2),
        "move_5m": "-"
    }

    signal["message"] = make_message(signal)

    return signal


def analyze_radar_signal(symbol, df5m, df15m, df1h, current_price=None):
    if df5m is None or df15m is None or len(df5m) < 35 or len(df15m) < 20:
        return None

    df5 = df5m.copy()
    df15 = add_indicators(df15m)

    if df15 is None or len(df15) < 20:
        return None

    last5 = df5.iloc[-2]
    prev5 = df5.iloc[-3]
    last15 = df15.iloc[-2]
    prev15 = df15.iloc[-3]

    open5 = float(last5["open"])
    close5 = float(last5["close"])
    high5 = float(last5["high"])
    low5 = float(last5["low"])

    if open5 <= 0 or close5 <= 0:
        return None

    move_5m = ((close5 - open5) / open5) * 100
    move_15m = ((float(last15["close"]) - float(prev15["close"])) / float(prev15["close"])) * 100

    if abs(move_5m) < RADAR_MIN_5M_MOVE_PERCENT:
        return None

    if abs(move_5m) > RADAR_MAX_5M_MOVE_PERCENT:
        print(symbol, "radar elendi -> 5M mum fazla uzamış:", round(move_5m, 2))
        return None

    volume_avg = float(df5["volume"].iloc[-22:-2].mean())

    if volume_avg <= 0:
        return None

    volume_ratio = float(last5["volume"]) / volume_avg

    if volume_ratio < RADAR_MIN_VOLUME_RATIO:
        return None

    direction = None

    if move_5m > 0 and move_15m >= RADAR_MIN_15M_MOVE_PERCENT:
        direction = "LONG"

    if move_5m < 0 and move_15m <= -RADAR_MIN_15M_MOVE_PERCENT:
        direction = "SHORT"

    if direction is None:
        return None

    entry = float(current_price) if current_price is not None and current_price > 0 else close5

    current_from_close = abs((entry - close5) / close5) * 100

    if current_from_close > RADAR_MAX_CURRENT_FROM_CLOSE_PERCENT:
        print(symbol, "radar elendi -> güncel fiyat kapanıştan uzak:", round(current_from_close, 2))
        return None

    # 1H çok ters ise radar iptal.
    confirm, confirm_reason, confirm_info = get_confirm_1h(df1h)

    if direction == "LONG" and confirm == "SHORT":
        print(symbol, "radar LONG elendi -> 1H ters")
        return None

    if direction == "SHORT" and confirm == "LONG":
        print(symbol, "radar SHORT elendi -> 1H ters")
        return None

    atr5 = float((df5["high"] - df5["low"]).iloc[-18:-2].mean())

    if atr5 <= 0:
        atr5 = abs(close5 - open5)

    recent_high = max(float(prev5["high"]), high5)
    recent_low = min(float(prev5["low"]), low5)

    targets = build_targets(
        direction=direction,
        entry=entry,
        atr=atr5,
        recent_high=recent_high,
        recent_low=recent_low,
        risk_mode="radar"
    )

    if targets is None:
        return None

    risk_percent = targets["risk_percent"]

    if risk_percent < RADAR_MIN_RISK_PERCENT or risk_percent > RADAR_MAX_RISK_PERCENT:
        print(symbol, "radar elendi -> risk uygunsuz:", round(risk_percent, 2))
        return None

    rsi15 = float(last15["rsi"])
    adx15 = float(last15["adx"])

    # Stop azaltma filtresi:
    # ZORA tipi: 15M RSI çok yüksekken LONG gelirse tepeden dönüş riski artıyor.
    # BILL tipi: 15M RSI çok düşükken SHORT gelirse dipten tepki riski artıyor.
    if direction == "LONG" and rsi15 > RADAR_LONG_MAX_RSI:
        print(symbol, "radar LONG elendi -> RSI çok yüksek:", round(rsi15, 2))
        return None

    if direction == "SHORT" and rsi15 < RADAR_SHORT_MIN_RSI:
        print(symbol, "radar SHORT elendi -> RSI çok düşük:", round(rsi15, 2))
        return None

    score = 55
    score += min(abs(move_5m) * 18, 18)
    score += min(volume_ratio * 7, 17)

    if confirm == direction:
        score += 10

    if direction == "LONG" and rsi15 >= 48:
        score += 5

    if direction == "SHORT" and rsi15 <= 52:
        score += 5

    if score < RADAR_MIN_SCORE:
        print(symbol, "radar elendi -> skor düşük:", score)
        return None

    signal = {
        "symbol": symbol,
        "direction": direction,
        "source": "RADAR",
        "entry": round(entry, 10),
        "tp1": round(targets["tp1"], 10),
        "tp2": round(targets["tp2"], 10),
        "tp3": round(targets["tp3"], 10),
        "sl": round(targets["sl"], 10),
        "risk_percent": round(risk_percent, 3),
        "score": int(score),
        "trend_reason": "5M ani hareket",
        "confirm_reason": confirm_reason,
        "entry_reason": "5M hareket + hacim + 15M destek",
        "rsi_15m": round(rsi15, 2),
        "adx_15m": round(adx15, 2),
        "volume_ratio": round(volume_ratio, 2),
        "move_5m": round(move_5m, 2)
    }

    signal["message"] = make_message(signal)

    return signal
