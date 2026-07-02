import requests
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from datetime import datetime

TOKEN = "8619346423:AAHyaf5nk3IQYvMzEcNAYQFQH8eALdz6220"
CHAT_ID = "8439391876"

MIN_SCORE = 70

COINS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "BNB": "binancecoin",
    "SOL": "solana",
    "XRP": "ripple",
    "DOGE": "dogecoin",
    "ADA": "cardano",
    "AVAX": "avalanche-2",
    "LINK": "chainlink",
    "DOT": "polkadot"
}


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": CHAT_ID, "text": text}, timeout=15)


def get_data(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {
        "vs_currency": "usd",
        "days": "7"
    }

    data = requests.get(url, params=params, timeout=20).json()

    if "prices" not in data or len(data["prices"]) < 60:
        return None

    df = pd.DataFrame(data["prices"], columns=["time", "close"])
    df["volume"] = [v[1] for v in data.get("total_volumes", [])[:len(df)]]

    df["close"] = df["close"].astype(float)
    df["volume"] = df["volume"].astype(float)

    return df


def analyze(symbol, coin_id):
    df = get_data(coin_id)

    if df is None or df.empty:
        return None

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()

    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    df["volatility"] = df["close"].pct_change().rolling(20).std()

    last = df.iloc[-1]

    price = last["close"]
    rsi = last["rsi"]
    ema20 = last["ema20"]
    ema50 = last["ema50"]
    macd_now = last["macd"]
    macd_signal = last["macd_signal"]
    volatility = last["volatility"]

    if pd.isna(rsi) or pd.isna(ema20) or pd.isna(ema50) or pd.isna(macd_now):
        return None

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
🚀 KRİPTO FUTURES SİNYALİ

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
⏱ Tarama: CoinGecko
⚠️ Finansal tavsiye değildir.
⏰ {datetime.now().strftime("%d.%m.%Y %H:%M")}
"""
    }


def main():
    signals = []

    for symbol, coin_id in COINS.items():
        try:
            result = analyze(symbol, coin_id)
            if result:
                signals.append(result)
        except Exception as e:
            print(f"{symbol} hata: {e}")

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    if signals:
        send_telegram(f"✅ Tarama tamamlandı.\nGüçlü sinyal sayısı: {len(signals)}")
        for s in signals[:5]:
            send_telegram(s["message"])
    else:
        send_telegram("📊 CoinGecko taraması tamamlandı.\nŞu an güçlü sinyal yok.")


main()
