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

COINS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
    "TRXUSDT", "MATICUSDT", "LTCUSDT", "ATOMUSDT", "UNIUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "NEARUSDT", "INJUSDT"
]


def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": message})


def get_klines(symbol):
    url = "https://fapi.binance.com/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": LIMIT
    }

    data = requests.get(url, params=params, timeout=15).json()

    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])

    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)

    return df


def analyze(symbol):
    df = get_klines(symbol)

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()

    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    atr = AverageTrueRange(
        high=df["high"],
        low=df["low"],
        close=df["close"],
        window=14
    )
    df["atr"] = atr.average_true_range()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    price = last["close"]
    rsi = last["rsi"]
    ema20 = last["ema20"]
    ema50 = last["ema50"]
    macd_val = last["macd"]
    macd_signal = last["macd_signal"]
    atr_val = last["atr"]

    score = 0
    direction = None

    if ema20 > ema50:
        score += 25
        direction = "LONG"

    if ema20 < ema50:
        score += 25
        direction = "SHORT"

    if macd_val > macd_signal and direction == "LONG":
        score += 25

    if macd_val < macd_signal and direction == "SHORT":
        score += 25

    if rsi < 35 and direction == "LONG":
        score += 25

    if rsi > 65 and direction == "SHORT":
        score += 25

    if last["volume"] > df["volume"].rolling(20).mean().iloc[-1]:
        score += 15

    if score < 70 or direction is None:
        return None

    if direction == "LONG":
        sl = price - (atr_val * 1.5)
        tp1 = price + (atr_val * 2)
        tp2 = price + (atr_val * 3)
        icon = "🟢"
    else:
        sl = price + (atr_val * 1.5)
        tp1 = price - (atr_val * 2)
        tp2 = price - (atr_val * 3)
        icon = "🔴"

    return f"""
🚀 KRİPTO FUTURES SİNYALİ

{icon} {direction}
Coin: {symbol}
Zaman: {INTERVAL}

💰 Giriş: {round(price, 4)}
🎯 TP1: {round(tp1, 4)}
🎯 TP2: {round(tp2, 4)}
🛑 SL: {round(sl, 4)}

📊 RSI: {round(rsi, 2)}
📈 EMA20: {round(ema20, 4)}
📉 EMA50: {round(ema50, 4)}
📌 MACD: {round(macd_val, 4)}
🔥 Güven Puanı: %{score}

⚠️ Kaldıraç düşük tutulmalı.
⏰ {datetime.now().strftime("%d.%m.%Y %H:%M")}
"""


def main():
    signals = []

    for coin in COINS:
        try:
            signal = analyze(coin)
            if signal:
                signals.append(signal)
        except Exception as e:
            print(f"{coin} hata: {e}")

    if signals:
        for signal in signals[:5]:
            send_telegram(signal)
    else:
        send_telegram("📊 Tarama tamamlandı.\nŞu an güçlü sinyal yok.")


main()
