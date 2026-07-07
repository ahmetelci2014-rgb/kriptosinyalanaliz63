import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange
from config import MIN_SCORE


PREMIUM_COINS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "SUIUSDT",
    "ADAUSDT",
    "LTCUSDT",
    "DOTUSDT",
    "APTUSDT",
    "ARBUSDT",
    "OPUSDT",
    "NEARUSDT",
    "INJUSDT",
    "WLDUSDT",
    "FILUSDT",
    "ATOMUSDT",
    "UNIUSDT",
    "AAVEUSDT",
    "TRXUSDT",
    "ETCUSDT",
    "ICPUSDT",
    "SEIUSDT",
    "TIAUSDT",
    "ORDIUSDT",
    "JUPUSDT",
    "BCHUSDT"
]


def analyze_signal(symbol, df):
    def reject(reason):
        print(f"{symbol}: elendi -> {reason}")
        return None

    if df is None or df.empty or len(df) < 200:
        return reject("yetersiz veri")

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
    df = df.dropna()

    if df.empty or len(df) < 4:
        return reject("indikator verisi yetersiz")

    last = df.iloc[-1]

    price = float(last["close"])
    atr = float(last["atr"])

    if price <= 0 or atr <= 0:
        return reject("fiyat veya atr hatalı")

    rsi = float(last["rsi"])
    adx = float(last["adx"])
    volume_ratio = float(last["volume"] / last["volume_avg"])
    atr_percent = (atr / price) * 100

    if volume_ratio < 0.30:
        return reject(f"hacim çok düşük: {round(volume_ratio, 2)}x")

    if adx < 14:
        return reject(f"adx düşük: {round(adx, 2)}")

    long_score = 0
    short_score = 0
    long_reasons = []
    short_reasons = []

    # Trend yönü
    if last["close"] > last["ema200"]:
        long_score += 20
        long_reasons.append("Fiyat EMA200 üstünde")
    else:
        short_score += 20
        short_reasons.append("Fiyat EMA200 altında")

    if last["ema20"] > last["ema50"]:
        long_score += 15
        long_reasons.append("EMA20 EMA50 üstünde")
    else:
        short_score += 15
        short_reasons.append("EMA20 EMA50 altında")

    # MACD yönü
    if last["macd"] > last["macd_signal"]:
        long_score += 15
        long_reasons.append("MACD pozitif")
    else:
        short_score += 15
        short_reasons.append("MACD negatif")

    # RSI puanı
    if 42 <= rsi <= 68:
        long_score += 15
        long_reasons.append("RSI long için uygun")

    if 32 <= rsi <= 58:
        short_score += 15
        short_reasons.append("RSI short için uygun")

    # ADX puanı
    if adx >= 25:
        long_score += 15
        short_score += 15
    elif adx >= 18:
        long_score += 8
        short_score += 8
    elif adx >= 14:
        long_score += 4
        short_score += 4

    # Hacim puanı
    if volume_ratio >= 1.20:
        long_score += 10
        short_score += 10
    elif volume_ratio >= 0.70:
        long_score += 5
        short_score += 5

    # Volatilite puanı
    if atr_percent >= 0.35:
        long_score += 10
        short_score += 10

    # Yön belirleme
    if long_score > short_score:
        direction = "LONG"
        score = long_score
        icon = "🟢"
        reasons = long_reasons
    elif short_score > long_score:
        direction = "SHORT"
        score = short_score
        icon = "🔴"
        reasons = short_reasons
    else:
        return reject(
            f"long short eşit | long: {long_score} short: {short_score} "
            f"rsi: {round(rsi, 2)} adx: {round(adx, 2)}"
        )

    # Aşırı RSI filtresi
    if direction == "LONG" and rsi > 70:
        return reject(f"long rsi yüksek: {round(rsi, 2)}")

    if direction == "SHORT" and rsi < 30:
        return reject(f"short rsi düşük: {round(rsi, 2)}")

    # Premium coin bonusu
    if symbol in PREMIUM_COINS:
        score += 8

    # Minimum puan kontrolü
    if score < MIN_SCORE:
        return reject(
            f"puan düşük: {score} / min {MIN_SCORE} | "
            f"long: {long_score} short: {short_score}"
        )

    # Geç hareket / geç giriş filtresi
    ema_distance_percent = abs(price - last["ema20"]) / price * 100
    last_candle_move_percent = abs(last["close"] - last["open"]) / price * 100
    recent_3_candle_move_percent = abs(last["close"] - df.iloc[-4]["close"]) / price * 100

    if ema_distance_percent > atr_percent * 4.0:
        return reject(
            f"ema20 uzak geç giriş | ema uzaklık: {round(ema_distance_percent, 2)} "
            f"atr%: {round(atr_percent, 2)}"
        )

    if last_candle_move_percent > atr_percent * 4.0:
        return reject(
            f"son mum çok sert | mum: {round(last_candle_move_percent, 2)} "
            f"atr%: {round(atr_percent, 2)}"
        )

    if recent_3_candle_move_percent > atr_percent * 8.0:
        return reject(
            f"son 3 mum hareketi fazla | hareket: {round(recent_3_candle_move_percent, 2)} "
            f"atr%: {round(atr_percent, 2)}"
        )

    # TP / SL hesaplama
    # Stop geniş tutuldu: erken fitil stoplarını azaltmak için ATR 1.8
    if direction == "LONG":
        sl = price - atr * 1.8
        tp1 = price + atr * 1.8
        tp2 = price + atr * 3.0
        tp3 = price + atr * 4.5
    else:
        sl = price + atr * 1.8
        tp1 = price - atr * 1.8
        tp2 = price - atr * 3.0
        tp3 = price - atr * 4.5

    risk = abs(price - sl)
    reward = abs(tp2 - price)

    if risk <= 0:
        return reject("risk hesaplanamadı")

    rr = reward / risk

    if rr < 1.40:
        return reject(f"risk ödül düşük: 1:{round(rr, 2)}")

    # Sinyal kalite etiketi
    quality_score = 0
    quality_notes = []

    if adx >= 25:
        quality_score += 2
        quality_notes.append("Trend güçlü")
    elif adx >= 20:
        quality_score += 1
        quality_notes.append("Trend orta")
    else:
        quality_notes.append("Trend zayıf")

    if volume_ratio >= 1.00:
        quality_score += 2
        quality_notes.append("Hacim iyi")
    elif volume_ratio >= 0.70:
        quality_score += 1
        quality_notes.append("Hacim orta")
    else:
        quality_notes.append("Hacim düşük")

    if direction == "LONG":
        if 45 <= rsi <= 64:
            quality_score += 2
            quality_notes.append("RSI long için uygun")
        elif 42 <= rsi <= 68:
            quality_score += 1
            quality_notes.append("RSI idare eder")
        else:
            quality_notes.append("RSI riskli")

    if direction == "SHORT":
        if 36 <= rsi <= 54:
            quality_score += 2
            quality_notes.append("RSI short için uygun")
        elif 32 <= rsi <= 58:
            quality_score += 1
            quality_notes.append("RSI idare eder")
        else:
            quality_notes.append("RSI riskli")

    if quality_score >= 5:
        signal_quality = "A"
        trade_status = "✅ Değerlendirilebilir"
    elif quality_score >= 3:
        signal_quality = "B"
        trade_status = "⚠️ Dikkatli değerlendir"
    else:
        signal_quality = "C"
        trade_status = "⛔ Riskli, küçük bak veya girme"

    quality_notes_text = "\n".join([f"• {note}" for note in quality_notes])
    reasons_text = "\n".join([f"• {r}" for r in reasons[:4]])

    print(
        f"{symbol}: SİNYAL BULUNDU -> {direction} | "
        f"score: {score} | rsi: {round(rsi, 2)} | adx: {round(adx, 2)} | "
        f"hacim: {round(volume_ratio, 2)}x | rr: 1:{round(rr, 2)}"
    )

    message = f"""
🚀 KRİPTO SİNYAL ANALİZ BOTU FUTURES SİNYALİ

{icon} {direction}
🟡 Coin: {symbol}

🔥 Giriş: {round(price, 6)}
🎯 TP1: {round(tp1, 6)}
🎯 TP2: {round(tp2, 6)}
🎯 TP3: {round(tp3, 6)}
🔴 SL: {round(sl, 6)}

📊 RSI: {round(rsi, 2)}
💪 ADX: {round(adx, 2)}
📊 Hacim: {round(volume_ratio, 2)}x
📊 Volatilite: %{round(atr_percent, 2)}
⚖️ Risk/Ödül: 1:{round(rr, 2)}
🧮 Kaldıraç Önerisi: 2x - 3x

📌 İşlem Kuralı:
• Fiyat girişe yakınsa değerlendir.
• TP1'e yaklaşmışsa işleme girme.
• TP1 gelirse %50 kâr al, SL'yi giriş fiyatına çek.
• Stop mutlaka girilmeli.
• Marjin: Isolated kullan.

🔥 Güven Puanı: %{min(int(score), 100)}
📌 Sinyal Kalitesi: {signal_quality}
✅ İşlem Durumu: {trade_status}

📋 Kalite Notları:
{quality_notes_text}

⏱ Veri: OKX / 30dk

📌 Sinyal Nedenleri:
{reasons_text}

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işleme girme.
"""

    return {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "entry": round(price, 6),
        "tp1": round(tp1, 6),
        "sl": round(sl, 6),
        "message": message
    }