import requests
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange
from datetime import datetime

TOKEN = "8619346423:AAHyaf5nk3IQYvMzEcNAYQFQH8eALdz6220"
CHAT_ID = "8439391876"

INTERVAL = "15m"
LIMIT = 200
MIN_SCORE = 75

COINS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "LTCUSDT", "TRXUSDT", "ATOMUSDT", "UNIUSDT", "APTUSDT",
    "ARBUSDT", "OPUSDT", "NEARUSDT", "INJUSDT", "SUIUSDT",
    "FILUSDT", "ETCUSDT", "AAVEUSDT", "SEIUSDT", "TIAUSDT"
]


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=15)


def get_klines(symbol):
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {"symbol": symbol, "interval": INTERVAL, "limit": LIMIT}
    data = requests.get(url, params=params, timeout=20).json()

    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df


def analyze(symbol):
    df = get_klines(symbol)

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()

    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    atr = AverageTrueRange(df["high"], df["low"], df["close"], window=14)
    df["atr"] = atr.average_true_range()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    price = last["close"]
    rsi = last["rsi"]
    ema20 = last["ema20"]
    ema50 = last["ema50"]
    macd_now = last["macd"]
    macd_signal = last["macd_signal"]
    atr_now = last["atr"]

    volume_avg = df["volume"].rolling(20).mean().iloc[-1]
    volume_strong = last["volume"] > volume_avg

    long_score = 0
    short_score = 0

    if ema20 > ema50:
        long_score += 25
    else:
        short_score += 25

    if price > ema20:
        long_score += 15
    else:
        short_score += 15

    if macd_now > macd_signal:
        long_score += 20
    else:
        short_score += 20

    if rsi < 35:
        long_score += 20

    if rsi > 65:
        short_score += 20

    if 45 <= rsi <= 60 and ema20 > ema50:
        long_score += 10

    if 40 <= rsi <= 55 and ema20 < ema50:
        short_score += 10

    if volume_strong:
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
        sl = price - (atr_now * 1.5)
        tp1 = price + (atr_now * 2)
        tp2 = price + (atr_now * 3)
        icon = "🟢"
    else:
        sl = price + (atr_now * 1.5)
        tp1 = price - (atr_now * 2)
        tp2 = price - (atr_now * 3)
        icon = "🔴"

    risk = abs(price - sl)
    reward = abs(tp1 - price)
    rr = reward / risk if risk > 0 else 0

    return {
        "symbol": symbol,
        "score": score,
        "message": f"""
🚀 KRİPTO FUTURES SİNYALİ

{icon} {direction}
🪙 Coin: {symbol}
⏱️ Zaman: {INTERVAL}

💰 Giriş: {round(price, 5)}
🎯 TP1: {round(tp1, 5)}
🎯 TP2: {round(tp2, 5)}
🛑 SL: {round(sl, 5)}

📊 RSI: {round(rsi, 2)}
📈 EMA20: {round(ema20, 5)}
📉 EMA50: {round(ema50, 5)}
📌 MACD: {round(macd_now, 5)}
📦 Hacim: {"Güçlü" if volume_strong else "Normal"}

🔥 Güven Puanı: %{score}
⚖️ Risk/Ödül: 1:{round(rr, 2)}

⚠️ Finansal tavsiye değildir.
⚠️ Düşük kaldıraç kullan.
⏰ {datetime.now().strftime("%d.%m.%Y %H:%M")}
"""
    }


def main():
    signals = []

    for coin in COINS:
        try:
            result = analyze(coin)
            if result:
                signals.append(result)
        except Exception as e:
            print(f"{coin} hata: {e}")

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    if signals:
        send_telegram(f"✅ Tarama tamamlandı.\nBulunan güçlü sinyal sayısı: {len(signals)}")
        for item in signals[:5]:
            send_telegram(item["message"])
    else:
        send_telegram("📊 Tarama tamamlandı.\nŞu an güçlü sinyal yok.")


main()
