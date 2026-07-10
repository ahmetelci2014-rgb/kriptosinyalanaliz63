import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange
from config import MIN_SCORE, COINS


# Premium coin listesi artık config.py içindeki COINS listesinden gelir.
# Böylece coin eklemek istediğinde sadece config.py düzenlenir.
PREMIUM_COINS = COINS


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

    # En az birkaç kapanmış mum kalmalı
    if df.empty or len(df) < 10:
        return reject("indikator verisi yetersiz")

    # ÖNEMLİ:
    # -1 açık / oluşan son mum olabilir.
    # Bu yüzden sinyal için kapanmış son mumu kullanıyoruz.
    last = df.iloc[-2]
    prev = df.iloc[-3]

    price = float(last["close"])
    atr = float(last["atr"])

    if price <= 0 or atr <= 0:
        return reject("fiyat veya atr hatalı")

    rsi = float(last["rsi"])
    prev_rsi = float(prev["rsi"])
    adx = float(last["adx"])
    volume_ratio = float(last["volume"] / last["volume_avg"])
    atr_percent = (atr / price) * 100

    # Çok düşük hacimli sinyalleri engelle
    if volume_ratio < 0.30:
        return reject(f"hacim çok düşük: {round(volume_ratio, 2)}x")

    # Çok zayıf trendleri engelle
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

    # DÖNÜŞ FİLTRESİ
    # Amaç: Düşüş bitmişken geç SHORT, yükseliş bitmişken geç LONG sinyallerini azaltmak.
    if direction == "SHORT":
        # SHORT için fiyat EMA20 altında kalmalı
        if float(last["close"]) > float(last["ema20"]):
            return reject("short dönüş riski: fiyat EMA20 üstünde")

        # SHORT için son kapanış önceki kapanıştan düşük olmalı
        if float(last["close"]) > float(prev["close"]):
            return reject("short dönüş riski: son mum yukarı kapattı")

        # RSI sert yukarı dönmüşse SHORT riskli
        if rsi > prev_rsi + 3:
            return reject(
                f"short dönüş riski: RSI yukarı dönüyor "
                f"{round(prev_rsi, 2)} -> {round(rsi, 2)}"
            )

    if direction == "LONG":
        # LONG için fiyat EMA20 üstünde kalmalı
        if float(last["close"]) < float(last["ema20"]):
            return reject("long dönüş riski: fiyat EMA20 altında")

        # LONG için son kapanış önceki kapanıştan yüksek olmalı
        if float(last["close"]) < float(prev["close"]):
            return reject("long dönüş riski: son mum aşağı kapattı")

        # RSI sert aşağı dönmüşse LONG riskli
        if rsi < prev_rsi - 3:
            return reject(
                f"long dönüş riski: RSI aşağı dönüyor "
                f"{round(prev_rsi, 2)} -> {round(rsi, 2)}"
            )

    # Premium coin bonusu
    if symbol in PREMIUM_COINS:
        score += 8

    # Minimum puan kontrolü
    if score < MIN_SCORE:
        return reject(
            f"puan düşük: {score} / min {MIN_SCORE} | "
            f"long: {long_score} short: {short_score}"
        )

    # Geç giriş filtresi şimdilik pasif.
    # Sebep: Bot sürekli güçlü sinyal yok diyordu.
    # Bu bölümü ileride tekrar daha dengeli şekilde açacağız.

    # TP / SL hesaplama
    # ÖNEMLİ:
    # Son açık mumu değil, kapanmış son 5 mumu kullanıyoruz.
    # -6:-1 aralığı açık mumu dışarıda bırakır.
    recent_high = float(df["high"].iloc[-6:-1].max())
    recent_low = float(df["low"].iloc[-6:-1].min())
    buffer = atr * 0.25

    if direction == "LONG":
        sl_atr = price - atr * 2.2
        sl_swing = recent_low - buffer

        # LONG için daha güvenli olan, daha aşağıdaki stop kullanılır
        sl = min(sl_atr, sl_swing)

        risk = abs(price - sl)

        tp1 = price + risk * 1.0
        tp2 = price + risk * 1.7
        tp3 = price + risk * 2.5

    else:
        sl_atr = price + atr * 2.2
        sl_swing = recent_high + buffer

        # SHORT için daha güvenli olan, daha yukarıdaki stop kullanılır
        sl = max(sl_atr, sl_swing)

        risk = abs(sl - price)

        tp1 = price - risk * 1.0
        tp2 = price - risk * 1.7
        tp3 = price - risk * 2.5

    if risk <= 0:
        return reject("risk hesaplanamadı")

    # Stop çok aşırı uzaksa sinyal alma
    risk_percent = (risk / price) * 100

    if risk_percent > 3.0:
        return reject(f"stop çok uzak: %{round(risk_percent, 2)}")

    reward = abs(tp2 - price)
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

    if quality_score >= 6:
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
        f"score: {score} | quality: {signal_quality} | "
        f"rsi: {round(rsi, 2)} | adx: {round(adx, 2)} | "
        f"hacim: {round(volume_ratio, 2)}x | rr: 1:{round(rr, 2)} | "
        f"risk: %{round(risk_percent, 2)}"
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
🛡️ Stop Mesafesi: %{round(risk_percent, 2)}
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

📌 Sinyal Nedenleri:
{reasons_text}

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işleme girme.
"""

    return {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "quality": signal_quality,
        "entry": round(price, 6),
        "tp1": round(tp1, 6),
        "tp2": round(tp2, 6),
        "tp3": round(tp3, 6),
        "sl": round(sl, 6),
        "message": message
    }
