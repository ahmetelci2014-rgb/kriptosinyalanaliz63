# strategy.py
# Sade Premium V1 strateji
# Sadece LONG:
# 4H ana trend yukarı + 1H onay yukarı + 15M geri çekilme sonrası toparlanma.
# Emir açmaz; main.py sadece Telegram sinyali gönderir.

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange

from config import (
    MIN_SCORE,
    MIN_ADX_4H,
    MIN_ADX_1H,
    MIN_VOLUME_RATIO,
    MIN_RISK_PERCENT,
    MAX_RISK_PERCENT
)


def format_price(value):
    value = float(value)

    if value >= 100:
        return f"{value:.2f}"
    elif value >= 1:
        return f"{value:.4f}"
    elif value >= 0.01:
        return f"{value:.6f}"
    else:
        return f"{value:.10f}"


def add_indicators(df):
    if df is None or df.empty:
        return None

    df = df.copy()

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

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

    df = df.dropna().reset_index(drop=True)

    if df.empty:
        return None

    return df


def reject(symbol, reason):
    print(f"{symbol}: elendi -> {reason}")
    return None


def get_closed_row(df):
    # Son açık mum yerine kapanmış son mumu kullan.
    if df is None or len(df) < 3:
        return None

    return df.iloc[-2]


def analyze_signal(symbol, df15m, df1h, df4h):
    """
    Sadece LONG sinyal döndürür.
    Uygun sinyal yoksa None döndürür.
    """

    df15m = add_indicators(df15m)
    df1h = add_indicators(df1h)
    df4h = add_indicators(df4h)

    if df15m is None or len(df15m) < 30:
        return reject(symbol, "15M veri/indikatör yetersiz")

    if df1h is None or len(df1h) < 30:
        return reject(symbol, "1H veri/indikatör yetersiz")

    if df4h is None or len(df4h) < 30:
        return reject(symbol, "4H veri/indikatör yetersiz")

    last_15 = get_closed_row(df15m)
    prev_15 = df15m.iloc[-3]
    last_1h = get_closed_row(df1h)
    last_4h = get_closed_row(df4h)

    if last_15 is None or last_1h is None or last_4h is None:
        return reject(symbol, "kapanmış mum okunamadı")

    # =========================
    # 4H ANA TREND FİLTRESİ
    # =========================
    if float(last_4h["close"]) <= float(last_4h["ema200"]):
        return reject(symbol, "4H fiyat EMA200 altında")

    if float(last_4h["ema20"]) <= float(last_4h["ema50"]):
        return reject(symbol, "4H EMA20 EMA50 altında")

    if float(last_4h["ema20_slope"]) <= 0:
        return reject(symbol, "4H EMA20 eğimi yukarı değil")

    if float(last_4h["adx"]) < MIN_ADX_4H:
        return reject(symbol, f"4H ADX düşük: {round(float(last_4h['adx']), 2)}")

    # =========================
    # 1H ONAY FİLTRESİ
    # =========================
    if float(last_1h["close"]) <= float(last_1h["ema200"]):
        return reject(symbol, "1H fiyat EMA200 altında")

    if float(last_1h["ema20"]) <= float(last_1h["ema50"]):
        return reject(symbol, "1H EMA20 EMA50 altında")

    if float(last_1h["macd"]) <= float(last_1h["macd_signal"]):
        return reject(symbol, "1H MACD onayı yok")

    if float(last_1h["rsi"]) < 50:
        return reject(symbol, f"1H RSI zayıf: {round(float(last_1h['rsi']), 2)}")

    if float(last_1h["adx"]) < MIN_ADX_1H:
        return reject(symbol, f"1H ADX düşük: {round(float(last_1h['adx']), 2)}")

    # =========================
    # 15M GİRİŞ FİLTRESİ
    # =========================
    price = float(last_15["close"])
    atr = float(last_15["atr"])

    if price <= 0 or atr <= 0:
        return reject(symbol, "fiyat/ATR hatalı")

    if float(last_15["close"]) <= float(last_15["ema200"]):
        return reject(symbol, "15M fiyat EMA200 altında")

    # Çok uzamış fiyatları alma: EMA20'den ATR'nin 1.2 katından fazla uzaksa geç giriş olabilir.
    ema20_distance = float(last_15["close"]) - float(last_15["ema20"])

    if ema20_distance < 0:
        return reject(symbol, "15M fiyat EMA20 altında")

    if ema20_distance > atr * 1.20:
        return reject(symbol, "15M fiyat EMA20'den fazla uzak, geç giriş riski")

    # Geri çekilme sonrası toparlanma:
    # Önceki mum EMA20 civarında/altında, son kapanış tekrar EMA20 üstünde ve yükseliyor.
    pullback_happened = (
        float(prev_15["close"]) <= float(prev_15["ema20"])
        or float(prev_15["low"]) <= float(prev_15["ema20"]) * 1.003
    )

    if not pullback_happened:
        return reject(symbol, "15M geri çekilme yok")

    if float(last_15["close"]) <= float(prev_15["close"]):
        return reject(symbol, "15M son mum toparlanmadı")

    rsi = float(last_15["rsi"])

    if not (45 <= rsi <= 64):
        return reject(symbol, f"15M RSI uygun değil: {round(rsi, 2)}")

    if float(last_15["macd"]) <= float(last_15["macd_signal"]):
        return reject(symbol, "15M MACD pozitif değil")

    volume_ratio = float(last_15["volume_ratio"])

    if volume_ratio < MIN_VOLUME_RATIO:
        return reject(symbol, f"hacim düşük: {round(volume_ratio, 2)}x")

    # =========================
    # TP / SL
    # =========================
    recent_low = float(df15m["low"].iloc[-10:-1].min())

    sl_atr = price - (atr * 1.8)
    sl_swing = recent_low - (atr * 0.20)

    # LONG işlemde daha güvenli stop daha aşağıdadır.
    sl = min(sl_atr, sl_swing)

    risk = price - sl

    if risk <= 0:
        return reject(symbol, "risk hesaplanamadı")

    risk_percent = (risk / price) * 100

    if risk_percent < MIN_RISK_PERCENT:
        return reject(symbol, f"stop çok yakın: %{round(risk_percent, 2)}")

    if risk_percent > MAX_RISK_PERCENT:
        return reject(symbol, f"stop çok uzak: %{round(risk_percent, 2)}")

    tp1 = price + risk * 1.00
    tp2 = price + risk * 1.70
    tp3 = price + risk * 2.50

    # =========================
    # SKOR
    # =========================
    score = 0
    notes = []

    if float(last_4h["adx"]) >= 25:
        score += 25
        notes.append("4H trend güçlü")
    else:
        score += 15
        notes.append("4H trend orta")

    if float(last_1h["adx"]) >= 25:
        score += 20
        notes.append("1H onay güçlü")
    else:
        score += 10
        notes.append("1H onay orta")

    if 48 <= rsi <= 58:
        score += 20
        notes.append("15M RSI ideal")
    else:
        score += 10
        notes.append("15M RSI kabul edilebilir")

    if volume_ratio >= 1.20:
        score += 20
        notes.append("Hacim güçlü")
    else:
        score += 10
        notes.append("Hacim yeterli")

    if float(last_15["adx"]) >= 20:
        score += 15
        notes.append("15M hareket güçlü")
    else:
        score += 10
        notes.append("15M hareket orta")

    if score < MIN_SCORE:
        return reject(symbol, f"skor düşük: {score}")

    notes_text = "\n".join([f"• {n}" for n in notes])

    message = f"""
🚀 SADE PREMIUM V1 FUTURES SİNYALİ

🟢 LONG
🟡 Coin: {symbol}

🔥 Giriş: {format_price(price)}
🎯 TP1: {format_price(tp1)}
🎯 TP2: {format_price(tp2)}
🎯 TP3: {format_price(tp3)}
🔴 SL: {format_price(sl)}

📊 15M RSI: {round(rsi, 2)}
💪 15M ADX: {round(float(last_15["adx"]), 2)}
📊 Hacim: {round(volume_ratio, 2)}x
🛡️ Stop Mesafesi: %{round(risk_percent, 2)}
🔥 Skor: %{min(int(score), 100)}

📈 Sistem Onayı:
• 4H ana trend yukarı ✅
• 1H onay yukarı ✅
• 15M geri çekilme sonrası toparlanma ✅
• SHORT kapalı, sadece LONG ✅

📋 Kalite Notları:
{notes_text}

📌 İşlem Kuralı:
• Fiyat girişe yakınsa değerlendir.
• TP1'e yaklaşmışsa işleme girme.
• TP1 gelirse %50 kâr al, SL'yi girişe çek.
• Stop mutlaka girilmeli.
• Marjin: Isolated.
• Kaldıraç: 2x - 3x.

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işleme girme.
"""

    print(
        f"{symbol}: SİNYAL -> LONG | skor: {score} | "
        f"risk: %{round(risk_percent, 2)} | hacim: {round(volume_ratio, 2)}x"
    )

    return {
        "symbol": symbol,
        "direction": "LONG",
        "entry": round(price, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "sl": round(sl, 10),
        "score": int(score),
        "risk_percent": round(risk_percent, 3),
        "message": message
    }
