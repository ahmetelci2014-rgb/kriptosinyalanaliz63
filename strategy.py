import pandas as pd
from indicators import add_indicators
from config import MIN_SCORE

def analyze_signal(symbol, df):
    if df is None or df.empty or len(df) < 200:
        return None

    df = add_indicators(df).dropna()

    if df.empty:
        return None

    last = df.iloc[-1]

    price = last["close"]
    rsi = last["rsi"]
    ema20 = last["ema20"]
    ema50 = last["ema50"]
    ema200 = last["ema200"]
    macd = last["macd"]
    macd_signal = last["macd_signal"]
    atr = last["atr"]
    adx = last["adx"]

    long_score = 0
    short_score = 0

    if price > ema200:
        long_score += 20
    else:
        short_score += 20

    if ema20 > ema50:
        long_score += 20
    else:
        short_score += 20

    if macd > macd_signal:
        long_score += 20
    else:
        short_score += 20

    if adx > 20:
        long_score += 15
        short_score += 15

    if 40 <= rsi <= 65:
        long_score += 15

    if 35 <= rsi <= 60:
        short_score += 15

    volume_avg = df["volume"].rolling(20).mean().iloc[-1]
    if last["volume"] > volume_avg:
        long_score += 10
        short_score += 10

    if long_score >= short_score:
        direction = "LONG"
        score = long_score
    else:
        direction = "SHORT"
        score = short_score

    if score < MIN_SCORE:
        return None

    if direction == "LONG":
        sl = price - atr * 1.5
        tp1 = price + atr * 2
        tp2 = price + atr * 3
        tp3 = price + atr * 4
        icon = "🟢"
    else:
        sl = price + atr * 1.5
        tp1 = price - atr * 2
        tp2 = price - atr * 3
        tp3 = price - atr * 4
        icon = "🔴"

    risk = abs(price - sl)
    reward = abs(tp1 - price)
    rr = reward / risk if risk > 0 else 0

    if rr < 1.2:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "score": score,
        "message": f"""
🚀 PROFESYONEL FUTURES SİNYALİ

{icon} {direction}
🪙 Coin: {symbol}
⏱ Periyot: 15m

💰 Giriş: {round(price, 5)}
🎯 TP1: {round(tp1, 5)}
🎯 TP2: {round(tp2, 5)}
🎯 TP3: {round(tp3, 5)}
🛑 SL: {round(sl, 5)}

📊 RSI: {round(rsi, 2)}
📈 EMA20: {round(ema20, 5)}
📉 EMA50: {round(ema50, 5)}
📌 EMA200: {round(ema200, 5)}
💪 ADX: {round(adx, 2)}

🔥 Güven Puanı: %{score}
⚖️ Risk/Ödül: 1:{round(rr, 2)}

⚠️ Finansal tavsiye değildir.
⚠️ Düşük kaldıraç kullan.
"""
    }
