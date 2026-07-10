import os
import time
import json
import re
import requests
import pandas as pd
import ccxt
from datetime import datetime, timezone, timedelta

from strategy import analyze_signal
from config import COINS, INTERVAL, LIMIT


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

TIMEFRAME = INTERVAL
MAX_SIGNALS = 3

OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"

TR_TIMEZONE = timezone(timedelta(hours=3))
DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 45

# Açık sinyal özeti kaç dakikada bir gönderilsin
OPEN_SUMMARY_EVERY_MINUTES = 60

# Aynı coin + aynı yön sinyali 45 dakika içinde tekrar gönderilmesin
DUPLICATE_BLOCK_SECONDS = 45 * 60


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


def ensure_stats_bucket(bucket):
    for key in ["signals", "tp1", "tp2", "tp3", "sl", "be"]:
        if key not in bucket:
            bucket[key] = 0

    return bucket


def update_performance(symbol, direction, result):
    """
    result:
    OPENED = yeni sinyal gönderildi
    TP1 = TP1 geldi
    TP2 = TP2 geldi
    TP3 = TP3 geldi
    SL = Stop oldu
    BE = TP1 sonrası kalan işlem girişten kapandı
    """

    performance = load_performance()
    today = get_today_key()

    if "total" not in performance:
        performance["total"] = {}

    performance["total"] = ensure_stats_bucket(performance["total"])

    if "coins" not in performance:
        performance["coins"] = {}

    if symbol not in performance["coins"]:
        performance["coins"][symbol] = {}

    performance["coins"][symbol] = ensure_stats_bucket(performance["coins"][symbol])

    if "days" not in performance:
        performance["days"] = {}

    if today not in performance["days"]:
        performance["days"][today] = {
            "signals": 0,
            "tp1": 0,
            "tp2": 0,
            "tp3": 0,
            "sl": 0,
            "be": 0,
            "coins": {}
        }

    performance["days"][today] = ensure_stats_bucket(performance["days"][today])

    if "coins" not in performance["days"][today]:
        performance["days"][today]["coins"] = {}

    if symbol not in performance["days"][today]["coins"]:
        performance["days"][today]["coins"][symbol] = {}

    performance["days"][today]["coins"][symbol] = ensure_stats_bucket(
        performance["days"][today]["coins"][symbol]
    )

    if result == "OPENED":
        field = "signals"
    elif result == "TP1":
        field = "tp1"
    elif result == "TP2":
        field = "tp2"
    elif result == "TP3":
        field = "tp3"
    elif result == "SL":
        field = "sl"
    elif result == "BE":
        field = "be"
    else:
        return

    performance["total"][field] += 1
    performance["coins"][symbol][field] += 1
    performance["days"][today][field] += 1
    performance["days"][today]["coins"][symbol][field] += 1

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
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "coins": {}
    })

    day_data = ensure_stats_bucket(day_data)

    signals = int(day_data.get("signals", 0))
    tp1 = int(day_data.get("tp1", 0))
    tp2 = int(day_data.get("tp2", 0))
    tp3 = int(day_data.get("tp3", 0))
    sl = int(day_data.get("sl", 0))
    be = int(day_data.get("be", 0))
    open_count = len(open_signals)

    success_rate = calculate_success_rate(tp1, sl)

    best_coin = "Yok"
    worst_coin = "Yok"
    best_rate = -1
    worst_rate = 101

    for coin, data in day_data.get("coins", {}).items():
        data = ensure_stats_bucket(data)

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
✅ TP2 Gelen: {tp2}
✅ TP3 Gelen: {tp3}
🟡 Girişten Kapanan: {be}
❌ Stop Olan: {sl}
⏳ Açık Sinyal: {open_count}

📊 TP1 Başarı Oranı: %{success_rate}

🏆 En İyi Coin: {best_coin}
⚠️ En Zayıf Coin: {worst_coin}

📌 Not:
Başarı oranı sadece TP1 veya SL ile sonuçlanan sinyaller üzerinden hesaplanır.
TP1 sonrası kalan işlem için SL giriş fiyatı olarak takip edilir.
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


def extract_target_from_message(message, target_name):
    try:
        pattern = rf"{target_name}:\s*([0-9]+(?:\.[0-9]+)?)"
        match = re.search(pattern, message)

        if not match:
            return None

        return float(match.group(1))

    except Exception as e:
        print(target_name, "mesajdan okunamadı:", e)
        return None


def get_signal_target(signal, target_name):
    value = signal.get(target_name.lower())

    if value is not None:
        return float(value)

    return extract_target_from_message(signal.get("message", ""), target_name)


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
            tp2 = signal.get("tp2")
            tp3 = signal.get("tp3")
            sl = float(signal["sl"])
            score = signal.get("score", "-")
            quality = signal.get("quality", "-")

            tp1_hit = bool(signal.get("tp1_hit", False))
            tp2_hit = bool(signal.get("tp2_hit", False))
            tp3_hit = bool(signal.get("tp3_hit", False))

            current_price = get_current_price(exchange, symbol)

            if current_price is None:
                continue

            if direction == "LONG":
                icon = "🟢"
                tp_distance = ((tp1 - current_price) / current_price) * 100
                sl_distance = ((current_price - sl) / current_price) * 100
                profit_percent = ((current_price - entry) / entry) * 100

                if current_price >= entry:
                    status = "Kâr tarafında ✅"
                else:
                    status = "Giriş altında ⚠️"

            else:
                icon = "🔴"
                tp_distance = ((current_price - tp1) / current_price) * 100
                sl_distance = ((sl - current_price) / current_price) * 100
                profit_percent = ((entry - current_price) / entry) * 100

                if current_price <= entry:
                    status = "Kâr tarafında ✅"
                else:
                    status = "Giriş üstünde ⚠️"

            tp_status = []

            if tp1_hit:
                tp_status.append("TP1 ✅")
            if tp2_hit:
                tp_status.append("TP2 ✅")
            if tp3_hit:
                tp_status.append("TP3 ✅")

            if not tp_status:
                tp_text = "Henüz TP yok"
            else:
                tp_text = " / ".join(tp_status)

            active_sl_text = f"{round(entry, 6)} (TP1 sonrası girişe çekildi)" if tp1_hit else round(sl, 6)

            message += (
                f"{icon} {symbol} {direction}\n"
                f"🔥 Giriş: {round(entry, 6)}\n"
                f"💰 Güncel: {round(current_price, 6)}\n"
                f"🎯 TP1: {round(tp1, 6)}\n"
            )

            if tp2 is not None:
                message += f"🎯 TP2: {round(float(tp2), 6)}\n"

            if tp3 is not None:
                message += f"🎯 TP3: {round(float(tp3), 6)}\n"

            message += (
                f"🔴 Aktif SL: {active_sl_text}\n"
                f"📊 Skor: {score}\n"
                f"📌 Kalite: {quality}\n"
                f"📍 Durum: {status}\n"
                f"📈 Anlık Durum: %{round(profit_percent, 2)}\n"
                f"✅ TP Durumu: {tp_text}\n"
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
            print(key, "45 dakika içinde tekrar sinyal olduğu için engellendi.")
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
            tp2 = signal.get("tp2")
            tp3 = signal.get("tp3")
            sl = float(signal["sl"])

            tp2 = float(tp2) if tp2 is not None else None
            tp3 = float(tp3) if tp3 is not None else None

            tp1_hit = bool(signal.get("tp1_hit", False))
            tp2_hit = bool(signal.get("tp2_hit", False))
            tp3_hit = bool(signal.get("tp3_hit", False))

            candle = get_last_candle(exchange, symbol)
            current_price = get_current_price(exchange, symbol)

            if candle is None or current_price is None:
                updated_signals[key] = signal
                continue

            high = candle["high"]
            low = candle["low"]

            if direction == "LONG":
                if not tp1_hit and high >= tp1:
                    send_telegram(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {entry}\n"
                        f"TP1: {tp1}\n"
                        f"Mum High: {high}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"Öneri: %50 kâr al, kalan işlem için SL giriş fiyatına çekildi."
                    )

                    update_performance(symbol, direction, "TP1")
                    signal["tp1_hit"] = True
                    signal["breakeven_sl"] = entry
                    tp1_hit = True

                if tp2 is not None and not tp2_hit and high >= tp2:
                    send_telegram(
                        f"✅ TP2 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {entry}\n"
                        f"TP2: {tp2}\n"
                        f"Mum High: {high}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"Öneri: Kârın bir kısmı daha alınabilir. Kalan işlem takip edilebilir."
                    )

                    update_performance(symbol, direction, "TP2")
                    signal["tp2_hit"] = True
                    tp2_hit = True

                if tp3 is not None and not tp3_hit and high >= tp3:
                    send_telegram(
                        f"🏁 TP3 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {entry}\n"
                        f"TP3: {tp3}\n"
                        f"Mum High: {high}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"Sonuç: Sinyal maksimum hedefe ulaştı ✅"
                    )

                    update_performance(symbol, direction, "TP3")
                    continue

                if tp1_hit and low <= entry:
                    send_telegram(
                        f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {entry}\n"
                        f"Mum Low: {low}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"TP1 sonrası kalan işlem girişten kapandı."
                    )

                    update_performance(symbol, direction, "BE")
                    continue

                if not tp1_hit and low <= sl:
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
                if not tp1_hit and low <= tp1:
                    send_telegram(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {entry}\n"
                        f"TP1: {tp1}\n"
                        f"Mum Low: {low}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"Öneri: %50 kâr al, kalan işlem için SL giriş fiyatına çekildi."
                    )

                    update_performance(symbol, direction, "TP1")
                    signal["tp1_hit"] = True
                    signal["breakeven_sl"] = entry
                    tp1_hit = True

                if tp2 is not None and not tp2_hit and low <= tp2:
                    send_telegram(
                        f"✅ TP2 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {entry}\n"
                        f"TP2: {tp2}\n"
                        f"Mum Low: {low}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"Öneri: Kârın bir kısmı daha alınabilir. Kalan işlem takip edilebilir."
                    )

                    update_performance(symbol, direction, "TP2")
                    signal["tp2_hit"] = True
                    tp2_hit = True

                if tp3 is not None and not tp3_hit and low <= tp3:
                    send_telegram(
                        f"🏁 TP3 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {entry}\n"
                        f"TP3: {tp3}\n"
                        f"Mum Low: {low}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"Sonuç: Sinyal maksimum hedefe ulaştı ✅"
                    )

                    update_performance(symbol, direction, "TP3")
                    continue

                if tp1_hit and high >= entry:
                    send_telegram(
                        f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {entry}\n"
                        f"Mum High: {high}\n"
                        f"Güncel Fiyat: {current_price}\n\n"
                        f"TP1 sonrası kalan işlem girişten kapandı."
                    )

                    update_performance(symbol, direction, "BE")
                    continue

                if not tp1_hit and high >= sl:
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
            f"Kalite: {signal.get('quality', '-') } | "
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
            print(
                coin,
                "sinyal bulundu:",
                signal["direction"],
                signal["score"],
                "kalite:",
                signal.get("quality", "-")
            )
        else:
            print(coin, "sinyal yok")

        time.sleep(0.2)

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    before_quality_count = len(signals)

    # B/C kalite sinyaller gönderilmesin
    signals = [s for s in signals if s.get("quality") == "A"]

    print("Kalite filtresi öncesi aday:", before_quality_count)
    print("A kalite sonrası aday:", len(signals))
    print("B/C kalite elenen:", before_quality_count - len(signals))

    open_signals_for_duplicate_check = load_open_signals()

    filtered_signals = []

    for signal in signals:
        if not is_duplicate_signal(signal, open_signals_for_duplicate_check):
            filtered_signals.append(signal)

    signals = filtered_signals

    long_signals = [s for s in signals if s["direction"] == "LONG"]
    short_signals = [s for s in signals if s["direction"] == "SHORT"]

    strong_signals = []

    strong_signals.extend(short_signals[:2])
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
            f"b/C kalite sinyaller gönderilmedi."
        )

        open_signals = load_open_signals()

        for signal in strong_signals:
            send_telegram(signal["message"])

            key = f"{signal['symbol']}_{signal['direction']}"

            tp2 = get_signal_target(signal, "TP2")
            tp3 = get_signal_target(signal, "TP3")

            open_signals[key] = {
                "symbol": signal["symbol"],
                "direction": signal["direction"],
                "score": signal["score"],
                "quality": signal.get("quality", "-"),
                "entry": signal["entry"],
                "tp1": signal["tp1"],
                "tp2": tp2,
                "tp3": tp3,
                "sl": signal["sl"],
                "opened_at": int(time.time()),
                "tp1_hit": False,
                "tp2_hit": False,
                "tp3_hit": False,
                "breakeven_sl": None
            }

            update_performance(signal["symbol"], signal["direction"], "OPENED")

            time.sleep(1)

        save_open_signals(open_signals)

        # Diğer aday listesi çok kalabalık yaptığı için kapatıldı.
        # other_message = format_other_signals(other_signals)
        # if other_message:
        #     send_telegram(other_message)

    else:
        print("Şu an güçlü A kalite sinyal yok.")
        send_telegram(
            f"📡 Bot çalıştı.\n\n"
            f"Toplam taranan parite: {len(COINS)}\n"
            f"Şu an güçlü A kalite sinyal yok.\n"
            f"B/C kalite sinyaller gönderilmedi."
        )

    maybe_send_daily_report()


if __name__ == "__main__":
    main()
