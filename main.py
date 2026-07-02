import requests
import pandas as pd
import json
import os
from datetime import datetime, timedelta

from config import OKX_BASE_URL, INTERVAL, LIMIT
from telegram import send_message
from strategy import analyze_signal

SIGNAL_FILE = "last_signals.json"

if not os.path.exists(SIGNAL_FILE):
    with open(SIGNAL_FILE, "w") as f:
        json.dump({}, f)
def load_last_signals():
    with open(SIGNAL_FILE, "r") as f:
        return json.load(f)


def save_last_signals(data):
    with open(SIGNAL_FILE, "w") as f:
        json.dump(data, f)
def get_okx_usdt_futures_pairs():
    url = f"{OKX_BASE_URL}/api/v5/public/instruments"
    params = {"instType": "SWAP"}

    response = requests.get(url, params=params, timeout=20)
    data = response.json()

    if data.get("code") != "0":
        print(f"OKX parite hatası: {data}")
        return []

    pairs = []

    for item in data.get("data", []):
        inst_id = item.get("instId", "")

        if inst_id.endswith("-USDT-SWAP"):
            symbol = inst_id.replace("-SWAP", "")
            pairs.append(symbol)

    return pairs


def get_okx_candles(symbol):
    okx_symbol = f"{symbol}-SWAP"
    url = f"{OKX_BASE_URL}/api/v5/market/candles"

    params = {
        "instId": okx_symbol,
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
    pairs = get_okx_usdt_futures_pairs()
    last_signals = load_last_signals()

    if not pairs:
        send_message("⚠️ OKX USDT futures pariteleri alınamadı.")
        return

    print(f"Toplam taranan parite: {len(pairs)}")

    signals = []

    for symbol in pairs:
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

        send_message(
            f"✅ OKX futures taraması tamamlandı.\n"
            f"Taranan parite: {len(pairs)}\n"
            f"En güçlü sinyal sayısı: {len(strong_signals)}"
        )

        for signal in strong_signals:
            send_message(signal["message"])
    else:
        print("Şu an güçlü sinyal yok.")


if __name__ == "__main__":
    main()
