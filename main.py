import os
import time
import json
import requests
import pandas as pd
import ccxt
from datetime import datetime, timezone, timedelta

from strategy import analyze_signal


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TIMEFRAME = "30m"
LIMIT = 200
MAX_SIGNALS = 5

OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"

TR_TIMEZONE = timezone(timedelta(hours=3))
DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 45

# Açık sinyal özeti kaç dakikada bir gönderilsin
OPEN_SUMMARY_EVERY_MINUTES = 60

# Aynı coin + aynı yön sinyali 2 saat içinde tekrar gönderilmesin
DUPLICATE_BLOCK_SECONDS = 2 * 60 * 60

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


def load_json_file(filename):
    try:
        if not os.path.exists(filename):
            return {}

        with open(filename, "r") as f:
            content = f.read().strip()

            if not content:
                return {}

            return json.loads(content)

    except Exception as e:
        print(filename, "okuma hatası:", e)
        return {}


def save_json_file(filename, data):
    try:
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(filename, "kaydetme hatası:", e)


def load_open_signals():
    return load_json_file(OPEN_SIGNALS_FILE)


def save_open_signals(data):
    save_json_file(OPEN_SIGNALS_FILE, data)


def load_performance():
    return load_json_file(PERFORMANCE_FILE)


def save_performance(data):
    save_json_file(PERFORMANCE_FILE, data)


def get_today_key():
    return datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d")


def update_performance(symbol, direction, result):
    """
    result:
    OPENED = yeni sinyal gönderildi
    TP1 = TP1 geldi
    SL = Stop oldu
    """

    performance = load_performance()
    today = get_today_key()

    if "total" not in performance:
        performance["total"] = {
            "signals": 0,
            "tp1": 0,
            "sl": 0
        }

    if "coins" not in performance:
        performance["coins"] = {}

    if symbol not in performance["coins"]:
        performance["coins"][symbol] = {
            "signals": 0,
            "tp1": 0,
            "sl": 0
        }

    if "days" not in performance:
        performance["days"] = {}

    if today not in performance["days"]:
        performance["days"][today] = {
            "signals": 0,
            "tp1": 0,
            "sl": 0,
            "coins": {}
        }

    if symbol not in performance["days"][today]["coins"]:
        performance["days"][today]["coins"][symbol] = {
            "signals": 0,
            "tp1": 0,
            "sl": 0
        }

    if result == "OPENED":
        performance["total"]["signals"] += 1
        performance["coins"][symbol]["signals"] += 1
        performance["days"][today]["signals"] += 1
        performance["days"][today]["coins"][symbol]["signals"] += 1

    elif result == "TP1":
        performance["total"]["tp1"] += 1
        performance["coins"][symbol]["tp1"] += 1
        performance["days"][today]["tp1"] += 1
        performance["days"][today]["coins"][symbol]["tp1"] += 1

    elif result == "SL":
        performance["total"]["sl"] += 1
        performance["coins"][symbol]["sl"] += 1
        performance["days"][today]["sl"] += 1
        performance["days"][today]["coins"][symbol]["sl"] += 1

    performance["last_update"] = int(time.time())

    save_performance(performance)


def calculate_success_rate(tp1, sl):
    closed = tp1 + sl

    if closed <= 0:
        return 0

    return round((tp1 / closed) * 100, 2)


def build_daily_report():
    performance = load_performance()
    open_signals = load_open_signals()
    today = get_today_key()

    day_data = performance.get("days", {}).get(today, {
        "signals": 0,
        "tp1": 0,
        "sl": 0,
        "coins": {}
    })

    signals = int(day_data.get("signals", 0))
    tp1 = int(day_data.get("tp1", 0))
    sl = int(day_data.get("sl", 0))
    open_count = len(open_signals)

    success_rate = calculate_success_rate(tp1, sl)

    best_coin = "Yok"
    worst_coin = "Yok"
    best_rate = -1
    worst_rate = 101

    for coin, data in day_data.get("coins", {}).items():
        coin_tp1 = int(data.get("tp1", 0))
        coin_sl = int(data.get("sl", 0))
        closed = coin_tp1 + coin_sl

        if closed <= 0:
            continue

        rate = calculate_success_rate(coin_tp1, coin_sl)

        if rate > best_rate:
            best_rate = rate
            best_coin = f"{coin} (%{rate})"

        if rate < worst_rate:
            worst_rate = rate
            worst_coin = f"{coin} (%{rate})"

    message = f"""
📊 GÜNLÜK PERFORMANS RAPORU

📅 Tarih: {today}

📈 Bugünkü Sinyal: {signals}
✅ TP1 Gelen: {tp1}
❌ Stop Olan: {sl}
⏳ Açık Sinyal: {open_count}

📊 Başarı Oranı: %{success_rate}

🏆 En İyi Coin: {best_coin}
⚠️ En Zayıf Coin: {worst_coin}

📌 Not:
Başarı oranı sadece TP1 veya SL ile sonuçlanan sinyaller üzerinden hesaplanır.
"""

    return message


def maybe_send_daily_report():
    now = datetime.now(TR_TIMEZONE)
    today = get_today_key()

    if now.hour != DAILY_REPORT_HOUR:
        return

    if now.minute < DAILY_REPORT_MINUTE:
        return

    performance = load_performance()

    if performance.get("last_daily_report") == today:
        print("Günlük rapor bugün zaten gönderilmiş.")
        return

    report_message = build_daily_report()
    send_telegram(report_message)

    performance = load_performance()
    performance["last_daily_report"] = today
    save_performance(performance)

    print("Günlük performans raporu gönderildi.")


def should_send_open_summary():
    performance = load_performance()
    now = int(time.time())
    last_summary = int(performance.get("last_open_summary", 0))
    wait_seconds = OPEN_SUMMARY_EVERY_MINUTES * 60

    if now - last_summary >= wait_seconds:
        return True

    return False


def mark_open_summary_sent():
    performance = load_performance()
    performance["last_open_summary"] = int(time.time())
    save_performance(performance)


def build_open_signals_summary(exchange):
    open_signals = load_open_signals()

    if not open_signals:
        return None

    message = "📌 AÇIK SİNYAL DURUMU\n\n"

    count = 0

    for key, signal in open_signals.items():
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]
            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            sl = float(signal["sl"])
            score = signal.get("score", "-")

            current_price = get_current_price(exchange, symbol)

            if current_price is None:
                continue

            if direction == "LONG":
                icon = "🟢"
                tp_distance = ((tp1 - current_price) / current_price) * 100
                sl_distance = ((current_price - sl) / current_price) * 100

                if current_price >= entry:
                    status = "Kâr tarafında ✅"
                else:
                    status = "Giriş altında ⚠️"

            else:
                icon = "🔴"
                tp_distance = ((current_price - tp1) / current_price) * 100
                sl_distance = ((sl - current_price) / current_price) * 100

                if current_price <= entry:
                    status = "Kâr tarafında ✅"
                else:
                    status = "Giriş üstünde ⚠️"

            message += (
                f"{icon} {symbol} {direction}\n"
                f"🔥 Giriş: {round(entry, 6)}\n"
                f"💰 Güncel: {round(current_price, 6)}\n"
                f"🎯 TP1: {round(tp1, 6)}\n"
                f"🔴 SL: {round(sl, 6)}\n"
                f"📊 Skor: {score}\n"
                f"📍 Durum: {status}\n"
                f"🎯 TP1 uzaklık: %{round(tp_distance, 2)}\n"
                f"🛡️ SL uzaklık: %{round(sl_distance, 2)}\n\n"
            )

            count += 1

            if count >= 10:
                message += "Devam eden başka açık sinyaller de var.\n"
                break

        except Exception as e:
            print(key, "açık sinyal özet hatası:", e)

    if count == 0:
        return None

    message += "📌 Bu özet bilgilendirme amaçlıdır. İşleme girmeden grafikte kontrol et."

    return message


def maybe_send_open_signals_summary(exchange):
    open_signals = load_open_signals()

    if not open_signals:
        print("Açık sinyal özeti gönderilmedi: açık sinyal yok.")
        return

    if not should_send_open_summary():
        print("Açık sinyal özeti zamanı gelmedi.")
        return

    summary = build_open_signals_summary(exchange)

    if summary:
        send_telegram(summary)
        mark_open_summary_sent()
        print("Açık sinyal özeti gönderildi.")


def is_duplicate_signal(signal, open_signals):
    try:
        key = f"{signal['symbol']}_{signal['direction']}"

        if key not in open_signals:
            return False

        opened_at = int(open_signals[key].get("opened_at", 0))
        now = int(time.time())

        if now - opened_at < DUPLICATE_BLOCK_SECONDS:
            print(key, "2 saat içinde tekrar sinyal olduğu için engellendi.")
            return True

        return False

    except Exception as e:
        print("Tekrar sinyal kontrol hatası:", e)
        return False


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


def get_current_price(exchange, symbol):
    try:
        okx_symbol = to_okx_symbol(symbol)
        ticker = exchange.fetch_ticker(okx_symbol)

        price = ticker.get("last")

        if price is None:
            return None

        return float(price)

    except Exception as e:
        print(symbol, "güncel fiyat hatası:", e)
        return None


def get_last_candle(exchange, symbol):
    try:
        okx_symbol = to_okx_symbol(symbol)

        ohlcv = exchange.fetch_ohlcv(
            okx_symbol,
            timeframe=TIMEFRAME,
            limit=2
        )

        if not ohlcv:
            return None

        last = ohlcv[-1]

        return {
            "open": float(last[1]),
            "high": float(last[2]),
            "low": float(last[3]),
            "close": float(last[4])
        }

    except Exception as e:
        print(symbol, "mum verisi hatası:", e)
        return None


def check_open_signals(exchange):
    open_signals = load_open_signals()

    if not open_signals:
        print("Takip edilen açık sinyal yok.")
        return

    print("Takip edilen açık sinyal sayısı:", len(open_signals))

    updated_signals = {}

    for key, signal in open_signals.items():
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]
            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            sl = float(signal["sl"])

            candle = get_last_candle(exchange, symbol)
            current_price = get_current_price(exchange, symbol)

            if candle is None or current_price is None:
                updated_signals[key] = signal
                continue

            high = candle["high"]
            low = candle["low"]

            if direction == "LONG":
                if high >= tp1:
                    send_telegram(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {entry}\n"
                        f"TP1: {tp1}\n"
                        f"Mum High: {high}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"Öneri: %50 kâr al, SL'yi giriş fiyatına çek."
                    )

                    update_performance(symbol, direction, "TP1")
                    continue

                if low <= sl:
                    send_telegram(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {entry}\n"
                        f"SL: {sl}\n"
                        f"Mum Low: {low}\n"
                        f"Güncel Fiyat: {current_price}"
                    )

                    update_performance(symbol, direction, "SL")
                    continue

            if direction == "SHORT":
                if low <= tp1:
                    send_telegram(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {entry}\n"
                        f"TP1: {tp1}\n"
                        f"Mum Low: {low}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"Öneri: %50 kâr al, SL'yi giriş fiyatına çek."
                    )

                    update_performance(symbol, direction, "TP1")
                    continue

                if high >= sl:
                    send_telegram(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {entry}\n"
                        f"SL: {sl}\n"
                        f"Mum High: {high}\n"
                        f"Güncel Fiyat: {current_price}"
                    )

                    update_performance(symbol, direction, "SL")
                    continue

            updated_signals[key] = signal

        except Exception as e:
            print(key, "takip hatası:", e)
            updated_signals[key] = signal

    save_open_signals(updated_signals)


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

    # Önce eski açık sinyalleri takip et
    check_open_signals(exchange)

    # Açık sinyaller için saatlik kısa özet gönder
    maybe_send_open_signals_summary(exchange)

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

    open_signals_for_duplicate_check = load_open_signals()

    filtered_signals = []

    for signal in signals:
        if not is_duplicate_signal(signal, open_signals_for_duplicate_check):
            filtered_signals.append(signal)

    signals = filtered_signals

    long_signals = [s for s in signals if s["direction"] == "LONG"]
    short_signals = [s for s in signals if s["direction"] == "SHORT"]

    strong_signals = []

    strong_signals.extend(short_signals[:3])
    strong_signals.extend(long_signals[:2])

    strong_signals = sorted(strong_signals, key=lambda x: x["score"], reverse=True)
    strong_signals = strong_signals[:MAX_SIGNALS]

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

        open_signals = load_open_signals()

        for signal in strong_signals:
            send_telegram(signal["message"])

            key = f"{signal['symbol']}_{signal['direction']}"

            open_signals[key] = {
                "symbol": signal["symbol"],
                "direction": signal["direction"],
                "entry": signal["entry"],
                "tp1": signal["tp1"],
                "sl": signal["sl"],
                "score": signal["score"],
                "opened_at": int(time.time())
            }

            update_performance(signal["symbol"], signal["direction"], "OPENED")

            time.sleep(1)

        save_open_signals(open_signals)

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

    maybe_send_daily_report()


if __name__ == "__main__":
    main()
