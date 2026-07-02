import requests
import pandas as pd
from datetime import datetime
from config import INTERVAL, LIMIT, TOP_COINS
from telegram import send_message
from strategy import analyze_signal


BINANCE_BASE = "https://fapi.binance.com"


def get_top_symbols():
    url = f"{BINANCE_BASE}/fapi/v1/ticker/24hr"

    response = requests.get(url, timeout=20)

    if response.status_code != 200:
        raise Exception(f"HTTP {response.status_code}")

    data = response.json()

    if not isinstance(data, list):
        raise Exception(f"Binance cevabı: {data}")

    symbols = []

    for item in data:
        if not isinstance(item, dict):
            continue

        symbol = item.get("symbol")

        if symbol and symbol.endswith("USDT"):
            volume = float(item.get("quoteVolume", 0))
            symbols.append((symbol, volume))

    symbols.sort(key=lambda x: x[1], reverse=True)

    return [s[0] for s in symbols[:TOP_COINS]]


def get_klines(symbol):
    url = f"{BINANCE_BASE}/fapi/v1/klines"
    params = {
        "symbol": symbol,
        "interval": INTERVAL,
        "limit": LIMIT
    }

    data = requests.get(url, params=params, timeout=20).json()

    if not isinstance(data, list) or len(data) < LIMIT:
        return None

    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore"
    ])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df


def main():
    send_message(f"🤖 Bot çalıştı.\n⏰ {datetime.now().strftime('%d.%m.%Y %H:%M')}")

    signals = []

    try:
        symbols = get_top_symbols()
    except Exception as e:
        send_message(f"❌ Coin listesi alınamadı:\n{e}")
        return

    for symbol in symbols:
        try:
            df = get_klines(symbol)
            result = analyze_signal(symbol, df)

            if result:
                signals.append(result)

        except Exception as e:
            print(f"{symbol} hata: {e}")

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    if signals:
        send_message(f"✅ Tarama tamamlandı.\nGüçlü sinyal sayısı: {len(signals)}")

        for signal in signals[:5]:
            send_message(signal["message"])
    else:
        send_message("📊 Tarama tamamlandı.\nŞu an güçlü sinyal yok.")


if __name__ == "__main__":
    main()
