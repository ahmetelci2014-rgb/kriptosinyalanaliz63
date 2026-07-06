import os
import time
import requests
import pandas as pd
import ccxt

from strategy import analyze_signal


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TIMEFRAME = "30m"
LIMIT = 200
MAX_SIGNALS = 5

COINS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "DOGEUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "SUIUSDT",
    "ADAUSDT",
    "LTCUSDT",
    "DOTUSDT",
    "APTUSDT",
    "ARBUSDT",
    "OPUSDT",
    "NEARUSDT",
    "INJUSDT",
    "WLDUSDT",
    "FILUSDT",
    "ATOMUSDT",
    "UNIUSDT",
    "AAVEUSDT",
    "TRXUSDT",
    "ETCUSDT",
    "ICPUSDT",
    "SEIUSDT",
    "TIAUSDT",
    "ORDIUSDT",
    "JUPUSDT",
    "BCHUSDT"
]


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik.")
        return

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:
        response = requests.post(url, data=data, timeout=20)
        print("Telegram cevap:", response.status_code, response.text)
    except Exception as e:
        print("Telegram gönderim hatası:", e)


def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap"
        }
    })


def to_okx_symbol(symbol):
    base = symbol.replace("USDT", "")
    return f"{base}/USDT:USDT"


def fetch_df(exchange, okx_symbol):
    try:
        ohlcv = exchange.fetch_ohlcv(
            okx_symbol,
            timeframe=TIMEFRAME,
            limit=LIMIT
        )

        if not ohlcv or len(ohlcv) < 200:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )

        return df

    except Exception as e:
        print(okx_symbol, "veri hatası:", e)
        return None


def format_other_signals(other_signals):
    if not other_signals:
        return None

    text = "📋 DİĞER SİNYAL ADAYLARI\n\n"

    for i, signal in enumerate(other_signals[:25], 1):
        text += (
            f"{i}) {signal['symbol']} | "
            f"{signal['direction']} | "
            f"Skor: {signal['score']} | "
            f"Giriş: {signal['entry']}\n"
        )

    text += "\nBu liste bilgilendirme amaçlıdır. Detaylı sinyaller üstte gönderildi."
    return text


def main():
    print("Bot başladı...")
    print("Toplam taranan parite:", len(COINS))

    exchange = get_exchange()

    signals = []

    for coin in COINS:
        okx_symbol = to_okx_symbol(coin)

        df = fetch_df(exchange, okx_symbol)

        if df is None:
            print(coin, "veri yok")
            continue

        signal = analyze_signal(coin, df)

        if signal:
            signals.append(signal)
            print(coin, "sinyal bulundu:", signal["direction"], signal["score"])
        else:
            print(coin, "sinyal yok")

        time.sleep(0.2)

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    long_signals = [s for s in signals if s["direction"] == "LONG"]
    short_signals = [s for s in signals if s["direction"] == "SHORT"]

    strong_signals = []

    # En güçlü 3 SHORT
    strong_signals.extend(short_signals[:3])

    # En güçlü 2 LONG
    strong_signals.extend(long_signals[:2])

    # Tekrar skora göre sırala
    strong_signals = sorted(strong_signals, key=lambda x: x["score"], reverse=True)

    # En fazla 5 detaylı sinyal gönder
    strong_signals = strong_signals[:MAX_SIGNALS]

    # Detaylı gönderilmeyen diğer adaylar
    other_signals = [
        s for s in signals
        if s not in strong_signals
    ]

    print("LONG sinyal sayısı:", len(long_signals))
    print("SHORT sinyal sayısı:", len(short_signals))
    print("Gönderilecek detaylı sinyal sayısı:", len(strong_signals))
    print("Diğer aday sayısı:", len(other_signals))

    if strong_signals:
        send_telegram(
            f"✅ Bot çalıştı.\n"
            f"Toplam taranan parite: {len(COINS)}\n"
            f"LONG aday: {len(long_signals)}\n"
            f"SHORT aday: {len(short_signals)}\n"
            f"Detaylı gönderilen sinyal: {len(strong_signals)}\n"
            f"Diğer aday: {len(other_signals)}"
        )

        for signal in strong_signals:
            send_telegram(signal["message"])
            time.sleep(1)

        other_message = format_other_signals(other_signals)

        if other_message:
            send_telegram(other_message)

    else:
        print("Şu an güçlü sinyal yok.")
        send_telegram(
            f"📡 Bot çalıştı.\n\n"
            f"Toplam taranan parite: {len(COINS)}\n"
            f"Şu an güçlü sinyal yok."
        )


if __name__ == "__main__":
    main()
