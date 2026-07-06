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
    if df is None or df.empty or len(df) < 200:
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

    df = df.dropna()

    if df.empty:
        return None

    last = df.iloc[-1]

    price = float(last["close"])
    atr = float(last["atr"])

    if price <= 0 or atr <= 0:
        return None

    rsi = float(last["rsi"])
    adx = float(last["adx"])
    volume_ratio = float(last["volume"] / last["volume_avg"])
    atr_percent = (atr / price) * 100

    # Çok düşük hacimli sinyalleri kesin engelle
    if volume_ratio < 0.30:
        return None

    # Çok zayıf trendleri engelle
    if adx < 14:
        return None

    long_score = 0
    short_score = 0
    long_reasons = []
    short_reasons = []

    # Trend
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

    # MACD
    if last["macd"] > last["macd_signal"]:
        long_score += 15
        long_reasons.append("MACD pozitif")
    else:
        short_score += 15
        short_reasons.append("MACD negatif")

    # RSI
    if 42 <= rsi <= 70:
        long_score += 15
        long_reasons.append("RSI long için uygun")

    if 30 <= rsi <= 58:
        short_score += 15
        short_reasons.append("RSI short için uygun")

    # ADX puan verir
    if adx >= 25:
        long_score += 15
        short_score += 15
    elif adx >= 18:
        long_score += 8
        short_score += 8
    elif adx >= 14:
        long_score += 4
        short_score += 4

    # Hacim puan verir
    if volume_ratio >= 1.20:
        long_score += 10
        short_score += 10
    elif volume_ratio >= 0.70:
        long_score += 5
        short_score += 5

    # Volatilite
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
        return None

    # Aşırı RSI filtresi
    if direction == "LONG" and rsi > 76:
        return None

    if direction == "SHORT" and rsi < 24:
        return None

    # Premium coin bonusu
    if symbol in PREMIUM_COINS:
        score += 8

    # Minimum puan kontrolü
    if score < MIN_SCORE:
        return None

    # TP / SL hesaplama
    if direction == "LONG":
        sl = price - atr * 1.2
        tp1 = price + atr * 1.6
        tp2 = price + atr * 2.8
        tp3 = price + atr * 4.0
    else:
        sl = price + atr * 1.2
        tp1 = price - atr * 1.6
        tp2 = price - atr * 2.8
        tp3 = price - atr * 4.0

    risk = abs(price - sl)
    reward = abs(tp2 - price)

    if risk <= 0:
        return None

    rr = reward / risk

    reasons_text = "\n".join([f"• {r}" for r in reasons[:4]])

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

🔥 Güven Puanı: %{min(int(score), 100)}
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
