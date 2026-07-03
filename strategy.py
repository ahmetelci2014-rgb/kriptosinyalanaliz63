import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange
from config import MIN_SCORE
PREMIUM_COINS = [
    "BTC-USDT",
    "ETH-USDT",
    "SOL-USDT",
    "BNB-USDT",
    "XRP-USDT",
    "LINK-USDT",
    "AVAX-USDT",
    "SUI-USDT",
    "DOGE-USDT"
]
def get_trend_direction(df):
    if df is None or df.empty or len(df) < 200:
        return None

    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

    last = df.iloc[-1]

    if last["close"] > last["ema50"] > last["ema200"]:
        return "LONG"

    if last["close"] < last["ema50"] < last["ema200"]:
        return "SHORT"

    return None
def analyze_signal(symbol, df):
    if df is None or df.empty or len(df) < 200:
        return None

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

    df = df.dropna()
    if df.empty:
        return None

    last = df.iloc[-1]
    price = last["close"]

    long_score = 0
    short_score = 0

    # Ana trend filtresi
    if price > last["ema200"]:
        long_score += 25
    else:
        short_score += 25

    # Kısa trend onayı
    if last["ema20"] > last["ema50"]:
        long_score += 20
    else:
        short_score += 20

    # MACD onayı
    if last["macd"] > last["macd_signal"]:
        long_score += 20
    else:
        short_score += 20

    # Trend gücü
    if last["adx"] >= 25:
        long_score += 15
        short_score += 15
    else:
        return None

    # RSI filtresi
    if 45 <= last["rsi"] <= 65:
        long_score += 15

    if 35 <= last["rsi"] <= 55:
        short_score += 15

    # Hacim onayı
    if last["volume"] > last["volume_avg"] * 1.05:
        long_score += 15
        short_score += 15
    else:
        long_score += 0
        short_score += 0
   
        return None

    if long_score > short_score:
        direction = "LONG"
        score = long_score
        icon = "🟢"
    else:
        direction = "SHORT"
        score = short_score
        icon = "🔴"
    # Premium coin önceliği
    if symbol in PREMIUM_COINS:
        score += 10
    if score < MIN_SCORE:
        return None

    # RSI filtresi
    if direction == "LONG" and last["rsi"] > 75:
        return None

    if direction == "SHORT" and last["rsi"] < 25:
        return None
    # Volatilite filtresi
    atr_percent = (last["atr"] / price) * 100

    if atr_percent < 0.8:
        return None
    atr = last["atr"]
    # Geç giriş filtresi
    ema_distance_percent = abs(price - last["ema20"]) / price * 100
    atr_percent = (atr / price) * 100

    if ema_distance_percent > atr_percent * 1.3:
        return None
    if direction == "LONG":
        sl = price - atr * 1.3
        tp1 = price + atr * 2
        tp2 = price + atr * 3
        tp3 = price + atr * 4
    else:
        sl = price + atr * 1.3
        tp1 = price - atr * 2
        tp2 = price - atr * 3
        tp3 = price - atr * 4

    risk = abs(price - sl)
    reward = abs(tp2 - price)
    rr = reward / risk if risk > 0 else 0

    if rr < 1.7:
        return None

    leverage = "2x - 3x"
    if score >= 90 and last["adx"] >= 30:
        leverage = "3x - 5x"

        score = min(score, 100)

        return {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "message": f"""
🚀 KRİPTO SİNYAL ANALİZ BOTU FUTURES SİNYALİ

{icon} {direction}
🟡 Coin: {symbol}

🔥 Giriş: {round(price, 5)}
🎯 TP1: {round(tp1, 5)}
🎯 TP2: {round(tp2, 5)}
🎯 TP3: {round(tp3, 5)}
🔴 SL: {round(sl, 5)}

📊 RSI: {round(last["rsi"], 2)}
📈 EMA20: {round(last["ema20"], 5)}
📉 EMA50: {round(last["ema50"], 5)}
📌 EMA200: {round(last["ema200"], 5)}
💪 ADX: {round(last["adx"], 2)}
⚖️ Risk/Ödül: 1:{round(rr, 2)}
🧮 Kaldıraç Önerisi: {leverage}

🔥 Güven Puanı: %{score}
⏱ Veri: OKX / 30dk

⚠️ Finansal tavsiye değildir.
"""
    }
