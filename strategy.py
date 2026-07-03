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
    "DOGE-USDT",
    "LINK-USDT",
    "AVAX-USDT",
    "SUI-USDT",
    "ADA-USDT",
    "LTC-USDT",
    "DOT-USDT",
    "APT-USDT",
    "ARB-USDT",
    "OP-USDT",
    "NEAR-USDT",
    "INJ-USDT",
    "WLD-USDT",
    "FIL-USDT",
    "ATOM-USDT",
    "UNI-USDT",
    "AAVE-USDT",
    "TRX-USDT",
    "ETC-USDT",
    "ICP-USDT",
    "SEI-USDT",
    "TIA-USDT",
    "ORDI-USDT",
    "JUP-USDT",
    "BCH-USDT"
]


def get_trend_direction(df):
    if df is None or df.empty or len(df) < 200:
        return None

    df = df.copy()

    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

    df = df.dropna()

    if df.empty:
        return None

    last = df.iloc[-1]

    if last["close"] > last["ema50"] > last["ema200"]:
        return "LONG"

    if last["close"] < last["ema50"] < last["ema200"]:
        return "SHORT"

    return None


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

    long_score = 0
    short_score = 0

    # Trend puanı
    if last["close"] > last["ema200"]:
        long_score += 20

    if last["close"] < last["ema200"]:
        short_score += 20

    if last["ema20"] > last["ema50"]:
        long_score += 15

    if last["ema20"] < last["ema50"]:
        short_score += 15

    # MACD onayı
    if last["macd"] > last["macd_signal"]:
        long_score += 15

    if last["macd"] < last["macd_signal"]:
        short_score += 15

    # RSI dengesi
    if 45 <= last["rsi"] <= 68:
        long_score += 15

    if 32 <= last["rsi"] <= 55:
        short_score += 15

    # ADX trend gücü
    if last["adx"] >= 25:
        long_score += 15
        short_score += 15
    elif last["adx"] >= 20:
        long_score += 8
        short_score += 8

    # Hacim puanı - artık direkt eleme yapmaz
    if last["volume"] > last["volume_avg"] * 1.05:
        long_score += 10
        short_score += 10

    # Volatilite puanı
    atr_percent = (atr / price) * 100

    if atr_percent >= 0.4:
        long_score += 10
        short_score += 10

    # Yön belirleme
    if long_score > short_score:
        direction = "LONG"
        score = long_score
        icon = "🟢"
    elif short_score > long_score:
        direction = "SHORT"
        score = short_score
        icon = "🔴"
    else:
        return None
    # Aşırı RSI filtresi
    if direction == "LONG" and last["rsi"] > 70:
        return None

    if direction == "SHORT" and last["rsi"] < 30:
        return None    

    # Premium coin bonusu
    if symbol in PREMIUM_COINS:
        score += 8

    # Minimum puan kontrolü
    if score < MIN_SCORE:
        return None

    # Geç hareket / geç giriş filtresi
    ema_distance_percent = abs(price - last["ema20"]) / price * 100
    last_candle_move_percent = abs(last["close"] - last["open"]) / price * 100
    recent_3_candle_move_percent = abs(last["close"] - df.iloc[-4]["close"]) / price * 100

    # Fiyat EMA20'den fazla uzaklaştıysa işlem alma
    if ema_distance_percent > atr_percent * 1.3:
        return None

    # Son mum çok sert hareket etmişse işlem alma
    if last_candle_move_percent > atr_percent * 0.9:
        return None

    # Son 3 mumda hareket çoktan olmuşsa işlem alma
    if recent_3_candle_move_percent > atr_percent * 2.0:
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

    if rr < 1.5:
        return None

    leverage = "2x - 3x"

    if score >= 90 and last["adx"] >= 30:
        leverage = "3x - 5x"

    message = f"""
🚀 KRİPTO SİNYAL ANALİZ BOTU FUTURES SİNYALİ

{icon} {direction}
🟡 Coin: {symbol}

🔥 Giriş: {round(price, 6)}
🎯 TP1: {round(tp1, 6)}
🎯 TP2: {round(tp2, 6)}
🎯 TP3: {round(tp3, 6)}
🔴 SL: {round(sl, 6)}

📊 RSI: {round(last["rsi"], 2)}
📈 EMA20: {round(last["ema20"], 6)}
📉 EMA50: {round(last["ema50"], 6)}
📌 EMA200: {round(last["ema200"], 6)}
💪 ADX: {round(last["adx"], 2)}
⚖️ Risk/Ödül: 1:{round(rr, 2)}
🧮 Kaldıraç Önerisi: {leverage}

🔥 Güven Puanı: %{min(int(score), 100)}
⏱ Veri: OKX / 30dk

⚠️ Finansal tavsiye değildir.
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
