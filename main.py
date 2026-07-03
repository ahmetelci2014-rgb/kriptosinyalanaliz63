import requests
import pandas as pd
import json
import os
from datetime import datetime, timedelta

from config import OKX_BASE_URL, INTERVAL, LIMIT, MAIN_TREND_INTERVAL, CONFIRM_INTERVAL, ENTRY_INTERVAL, COINS
from telegram import send_message
from strategy import analyze_signal, get_trend_direction

SIGNAL_FILE = "last_signals.json"
OPEN_SIGNALS_FILE = "open_signals.json"

if not os.path.exists(SIGNAL_FILE):
    with open(SIGNAL_FILE, "w") as f:
        json.dump({}, f)

if not os.path.exists(OPEN_SIGNALS_FILE):
    with open(OPEN_SIGNALS_FILE, "w") as f:
        json.dump({}, f)
def load_last_signals():
    try:
        with open(SIGNAL_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception:
        return {}


def save_last_signals(data):
    with open(SIGNAL_FILE, "w") as f:
        json.dump(data, f)


def load_open_signals():
    try:
        with open(OPEN_SIGNALS_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception:
        return {}


def save_open_signals(data):
    with open(OPEN_SIGNALS_FILE, "w") as f:
        json.dump(data, f)
def get_current_price(symbol):
    okx_symbol = f"{symbol}-SWAP"
    url = f"{OKX_BASE_URL}/api/v5/market/ticker"
    params = {
        "instId": okx_symbol
    }

    response = requests.get(url, params=params, timeout=20)
    data = response.json()

    if data.get("code") != "0":
        print(f"{symbol} fiyat alınamadı: {data}")
        return None

    ticker = data.get("data", [])

    if not ticker:
        return None

    return float(ticker[0]["last"])


def check_open_signals():
    open_signals = load_open_signals()

    if not open_signals:
        return

    updated_signals = {}

    for key, signal in open_signals.items():
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]
            tp1 = float(signal["tp1"])
            sl = float(signal["sl"])

            current_price = get_current_price(symbol)

            if current_price is None:
                updated_signals[key] = signal
                continue

            if direction == "LONG":
                if current_price >= tp1:
                    send_message(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"TP1: {tp1}\n"
                        f"Güncel Fiyat: {current_price}"
                    )
                    continue

                if current_price <= sl:
                    send_message(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"SL: {sl}\n"
                        f"Güncel Fiyat: {current_price}"
                    )
                    continue

            if direction == "SHORT":
                if current_price <= tp1:
                    send_message(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"TP1: {tp1}\n"
                        f"Güncel Fiyat: {current_price}"
                    )
                    continue

                if current_price >= sl:
                    send_message(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"SL: {sl}\n"
                        f"Güncel Fiyat: {current_price}"
                    )
                    continue

            updated_signals[key] = signal

        except Exception as e:
            print(f"{key} takip hatası: {e}")
            updated_signals[key] = signal

    save_open_signals(updated_signals)
        
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


def get_okx_candles(symbol, interval=ENTRY_INTERVAL):
    okx_symbol = f"{symbol}-SWAP"
    url = f"{OKX_BASE_URL}/api/v5/market/candles"

    params = {
        "instId": okx_symbol,
        "bar": interval,
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
    pairs = COINS
    last_signals = load_last_signals()
    check_open_signals()
    open_signals = load_open_signals()
    
    if not pairs:
        send_message("⚠️ Coin listesi boş.")
        return

    print(f"Toplam taranan parite: {len(pairs)}")

    signals = []

    for symbol in pairs:
        try:
            df = get_okx_candles(symbol, ENTRY_INTERVAL)
            result = analyze_signal(symbol, df)

            if result:
                key = f"{result['symbol']}_{result['direction']}"
                last_time = last_signals.get(key)

                if last_time:
                    last_dt = datetime.fromisoformat(last_time)
                    if datetime.utcnow() - last_dt < timedelta(hours=6):
                        print(f"{key} tekrar sinyal, atlandı.")
                        continue

                signals.append(result)

        except Exception as e:
            print(f"{symbol} hata: {e}")

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    if signals:
        strong_signals = signals[:5]

        send_message(
            f"✅ KSA Futures taraması tamamlandı.\n"
            f"Taranan parite: {len(pairs)}\n"
            f"En güçlü sinyal sayısı: {len(strong_signals)}"
        )

    for signal in strong_signals:
        send_message(signal["message"])

        key = f"{signal['symbol']}_{signal['direction']}"
        last_signals[key] = datetime.utcnow().isoformat()

        open_signals[key] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "entry": signal["entry"],
        "tp1": signal["tp1"],
        "sl": signal["sl"],
        "opened_at": datetime.utcnow().isoformat()
    }

        save_last_signals(last_signals)
        save_open_signals(open_signals)

    else:
        print("Şu an güçlü sinyal yok.")


if __name__ == "__main__":
    main()
