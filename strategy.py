import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from config import MIN_SCORE

def analyze_signal(symbol, df):
    if df is None or df.empty or len(df) < 60:
        return None

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()

    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["volatility"] = df["close"].pct_change().rolling(20).std()

    df = df.dropna()
    if df.empty:
        return None

    last = df.iloc[-1]

    price = last["close"]
    rsi = last["rsi"]
    ema20 = last["ema20"]
    ema50 = last["ema50"]
    macd_now = last["macd"]
    macd_signal = last["macd_signal"]
    volatility = last["volatility"]

    long_score = 0
    short_score = 0

    if ema20 > ema50:
        long_score += 30
    else:
        short_score += 30

    if price > ema20:
        long_score += 20
    else:
        short_score += 20

    if macd_now > macd_signal:
        long_score += 25
    else:
        short_score += 25

    if rsi < 35:
        long_score += 20

    if rsi > 65:
        short_score += 20

    if long_score >= short_score:
        direction = "LONG"
        score = long_score
        icon = "🟢"
    else:
        direction = "SHORT"
        score = short_score
        icon = "🔴"

    if score < MIN_SCORE:
        return None

    risk_percent = max(volatility * 100, 1.2)

    if direction == "LONG":
        sl = price * (1 - risk_percent / 100)
        tp1 = price * (1 + risk_percent * 1.5 / 100)
        tp2 = price * (1 + risk_percent * 2.5 / 100)
    else:
        sl = price * (1 + risk_percent / 100)
        tp1 = price * (1 - risk_percent * 1.5 / 100)
        tp2 = price * (1 - risk_percent * 2.5 / 100)

    return {
        "score": score,
        "message": f"""
🚀 KRİPTO SİNYALİ

{icon} {direction}
🪙 Coin: {symbol}/USDT

💰 Giriş: ${round(price, 5)}
🎯 TP1: ${round(tp1, 5)}
🎯 TP2: ${round(tp2, 5)}
🛑 SL: ${round(sl, 5)}

📊 RSI: {round(rsi, 2)}
📈 EMA20: {round(ema20, 5)}
📉 EMA50: {round(ema50, 5)}
📌 MACD: {round(macd_now, 5)}

🔥 Güven Puanı: %{score}
⏱ Veri: CoinGecko / 30dk tarama

⚠️ Finansal tavsiye değildir.
⚠️ Düşük kaldıraç kullan.
"""
    }
