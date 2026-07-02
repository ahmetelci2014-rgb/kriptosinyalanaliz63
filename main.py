import requests
import pandas as pd
from datetime import datetime

from config import TOP_COINS, VS_CURRENCY, DAYS
from telegram import send_message
from strategy import analyze_signal


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
    "DOT": "polkadot",
    "TRX": "tron",
    "LTC": "litecoin",
    "BCH": "bitcoin-cash",
    "UNI": "uniswap",
    "ATOM": "cosmos"
}


def get_data(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
    params = {
        "vs_currency": VS_CURRENCY,
        "days": DAYS
    }

    response = requests.get(url, params=params, timeout=20)

    if response.status_code != 200:
        print(f"CoinGecko HTTP hata: {response.status_code}")
        return None

    data = response.json()

    if "prices" not in data or len(data["prices"]) < 60:
        return None

    df = pd.DataFrame(data["prices"], columns=["time", "close"])
    df["close"] = df["close"].astype(float)

    return df


def main():
    send_message(f"🤖 CoinGecko bot çalıştı.\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    signals = []

    selected = list(COINS.items())[:TOP_COINS]

    for symbol, coin_id in selected:
        try:
            df = get_data(coin_id)
            result = analyze_signal(symbol, df)

            if result:
                signals.append(result)

        except Exception as e:
            print(f"{symbol} hata: {e}")

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    if signals:
        send_message(f"✅ CoinGecko taraması tamamlandı.\nGüçlü sinyal sayısı: {len(signals)}")

        for signal in signals[:5]:
            send_message(signal["message"])
    else:
        send_message("📊 CoinGecko taraması tamamlandı.\nŞu an güçlü sinyal yok.")


if __name__ == "__main__":
    main()
