import requests
import pandas as pd
from datetime import datetime

from config import OKX_BASE_URL, INTERVAL, LIMIT, TOP_COINS
from telegram import send_message
from strategy import analyze_signal


COINS = [
    "BTC-USDT", "ETH-USDT", "SOL-USDT", "XRP-USDT", "BNB-USDT",
    "DOGE-USDT", "ADA-USDT", "AVAX-USDT", "LINK-USDT", "DOT-USDT",
    "TRX-USDT", "LTC-USDT", "BCH-USDT", "UNI-USDT", "ATOM-USDT",
    "APT-USDT", "OP-USDT", "ARB-USDT", "NEAR-USDT", "FIL-USDT"
]


def get_okx_candles(symbol):
    url = f"{OKX_BASE_URL}/api/v5/market/candles"
    params = {
        "instId": symbol,
        "bar": INTERVAL,
        "limit": LIMIT
    }

    response = requests.get(url, params=params, timeout=20)
    data = response.json()

    if data.get("code") != "0":
        print(f"{symbol} OKX hata: {data}")
        return None

    candles = data.get("data", [])

    if len(candles) < 200:
        return None

    df = pd.DataFrame(candles, columns=[
        "time", "open", "high", "low", "close",
        "volume", "vol_ccy", "vol_ccy_quote", "confirm"
    ])

    df = df.iloc[::-1].reset_index(drop=True)

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df


def main():
    signals = []

    for symbol in COINS[:TOP_COINS]:
        try:
            df = get_okx_candles(symbol)
            result = analyze_signal(symbol, df)

            if result:
                signals.append(result)

        except Exception as e:
            print(f"{symbol} hata: {e}")

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    if signals:
        strong_signals = signals[:3]

        send_message(f"✅ OKX taraması tamamlandı.\nEn güçlü sinyal sayısı: {len(strong_signals)}")

        for signal in strong_signals:
            send_message(signal["message"])
    else:
        print("Şu an güçlü sinyal yok.")


if __name__ == "__main__":
    main()
