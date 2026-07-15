# main.py
# Sade Premium V1 Telegram sinyal botu
# Otomatik emir açmaz. Sadece Telegram sinyali gönderir ve açık sinyalleri takip eder.

import os
import time
import json
import requests
import pandas as pd
import ccxt
from datetime import datetime, timezone, timedelta

from config import (
    COINS,
    ENTRY_TIMEFRAME,
    CONFIRM_TIMEFRAME,
    TREND_TIMEFRAME,
    ENTRY_LIMIT,
    CONFIRM_LIMIT,
    TREND_LIMIT,
    MAX_SIGNALS,
    ALLOW_LONG_SIGNALS,
    ALLOW_SHORT_SIGNALS,
    MAX_ENTRY_DISTANCE_PERCENT,
    MAX_TP1_PROGRESS_PERCENT,
    DUPLICATE_BLOCK_SECONDS,
    DAILY_REPORT_HOUR,
    DAILY_REPORT_MINUTE,
    OPEN_SUMMARY_EVERY_MINUTES,
    SEND_NO_SIGNAL_MESSAGE
)
from strategy import analyze_signal, format_price


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"

TR_TIMEZONE = timezone(timedelta(hours=3))


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik.")
        return False

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:
        response = requests.post(url, data=data, timeout=20)
        print("Telegram cevap:", response.status_code, response.text)
        return response.status_code == 200
    except Exception as e:
        print("Telegram gönderim hatası:", e)
        return False


def load_json_file(filename):
    try:
        if not os.path.exists(filename):
            return {}

        with open(filename, "r", encoding="utf-8") as f:
            content = f.read().strip()

        if not content:
            return {}

        data = json.loads(content)

        if not isinstance(data, dict):
            print(filename, "dict değil, sıfırlanıyor.")
            return {}

        return data

    except Exception as e:
        print(filename, "okuma hatası:", e)
        return {}


def save_json_file(filename, data):
    try:
        if not isinstance(data, dict):
            data = {}

        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return True
    except Exception as e:
        print(filename, "kaydetme hatası:", e)
        return False


def load_open_signals():
    return load_json_file(OPEN_SIGNALS_FILE)


def save_open_signals(data):
    return save_json_file(OPEN_SIGNALS_FILE, data)


def load_performance():
    return load_json_file(PERFORMANCE_FILE)


def save_performance(data):
    return save_json_file(PERFORMANCE_FILE, data)


def get_today_key():
    return datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d")


def ensure_stats_bucket(bucket):
    if not isinstance(bucket, dict):
        bucket = {}

    for key in ["signals", "tp1", "tp2", "tp3", "sl", "be"]:
        if key not in bucket:
            bucket[key] = 0

    return bucket


def update_performance(symbol, result):
    performance = load_performance()
    today = get_today_key()

    if "total" not in performance or not isinstance(performance.get("total"), dict):
        performance["total"] = {}

    performance["total"] = ensure_stats_bucket(performance["total"])

    if "days" not in performance or not isinstance(performance.get("days"), dict):
        performance["days"] = {}

    if today not in performance["days"] or not isinstance(performance["days"].get(today), dict):
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

    if "coins" not in performance["days"][today] or not isinstance(performance["days"][today].get("coins"), dict):
        performance["days"][today]["coins"] = {}

    if symbol not in performance["days"][today]["coins"]:
        performance["days"][today]["coins"][symbol] = {}

    performance["days"][today]["coins"][symbol] = ensure_stats_bucket(
        performance["days"][today]["coins"][symbol]
    )

    field_map = {
        "OPENED": "signals",
        "TP1": "tp1",
        "TP2": "tp2",
        "TP3": "tp3",
        "SL": "sl",
        "BE": "be"
    }

    field = field_map.get(result)

    if not field:
        return

    performance["total"][field] += 1
    performance["days"][today][field] += 1
    performance["days"][today]["coins"][symbol][field] += 1
    performance["last_update"] = int(time.time())

    save_performance(performance)


def calculate_success_rate(tp1, sl):
    closed = int(tp1) + int(sl)

    if closed <= 0:
        return 0

    return round((int(tp1) / closed) * 100, 2)


def build_daily_report():
    performance = load_performance()
    open_signals = load_open_signals()
    today = get_today_key()

    day = performance.get("days", {}).get(today, {})
    day = ensure_stats_bucket(day)

    signals = int(day.get("signals", 0))
    tp1 = int(day.get("tp1", 0))
    tp2 = int(day.get("tp2", 0))
    tp3 = int(day.get("tp3", 0))
    sl = int(day.get("sl", 0))
    be = int(day.get("be", 0))

    success_rate = calculate_success_rate(tp1, sl)

    best_coin = "Yok"
    worst_coin = "Yok"
    best_rate = -1
    worst_rate = 101

    coins_data = day.get("coins", {})

    if not isinstance(coins_data, dict):
        coins_data = {}

    for coin, stats in coins_data.items():
        stats = ensure_stats_bucket(stats)
        coin_tp1 = int(stats.get("tp1", 0))
        coin_sl = int(stats.get("sl", 0))
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

    return f"""
📊 GÜNLÜK PERFORMANS RAPORU

📅 Tarih: {today}

📈 Bugünkü Sinyal: {signals}
✅ TP1 Gelen: {tp1}
✅ TP2 Gelen: {tp2}
✅ TP3 Gelen: {tp3}
🟡 Girişten Kapanan: {be}
❌ Stop Olan: {sl}
⏳ Açık Sinyal: {len(open_signals)}

📊 TP1 Başarı Oranı: %{success_rate}

🏆 En İyi Coin: {best_coin}
⚠️ En Zayıf Coin: {worst_coin}

📌 Not:
Bu sistem sadece LONG sinyal üretir.
Başarı oranı TP1 veya SL ile sonuçlanan sinyaller üzerinden hesaplanır.
TP1 sonrası kalan işlem için SL giriş fiyatı olarak takip edilir.
"""


def maybe_send_daily_report():
    now = datetime.now(TR_TIMEZONE)
    today = get_today_key()

    if now.hour != DAILY_REPORT_HOUR:
        return

    if now.minute < DAILY_REPORT_MINUTE:
        return

    performance = load_performance()

    if performance.get("last_daily_report") == today:
        print("Günlük rapor bugün zaten gönderildi.")
        return

    send_telegram(build_daily_report())

    performance = load_performance()
    performance["last_daily_report"] = today
    save_performance(performance)

    print("Günlük rapor gönderildi.")


def should_send_open_summary():
    performance = load_performance()
    now = int(time.time())
    last_summary = int(performance.get("last_open_summary", 0))
    wait_seconds = OPEN_SUMMARY_EVERY_MINUTES * 60

    return now - last_summary >= wait_seconds


def mark_open_summary_sent():
    performance = load_performance()
    performance["last_open_summary"] = int(time.time())
    save_performance(performance)


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


def fetch_df(exchange, symbol, timeframe, limit):
    try:
        okx_symbol = to_okx_symbol(symbol)

        ohlcv = exchange.fetch_ohlcv(
            okx_symbol,
            timeframe=timeframe,
            limit=limit
        )

        if not ohlcv or len(ohlcv) < 220:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )

        return df
    except Exception as e:
        print(symbol, timeframe, "veri hatası:", e)
        return None


def get_current_price(exchange, symbol):
    try:
        ticker = exchange.fetch_ticker(to_okx_symbol(symbol))
        price = ticker.get("last")

        if price is None:
            return None

        return float(price)
    except Exception as e:
        print(symbol, "güncel fiyat hatası:", e)
        return None


def timeframe_to_seconds(timeframe):
    if timeframe.endswith("m"):
        return int(timeframe.replace("m", "")) * 60

    if timeframe.endswith("h"):
        return int(timeframe.replace("h", "")) * 60 * 60

    if timeframe.endswith("d"):
        return int(timeframe.replace("d", "")) * 60 * 60 * 24

    return 15 * 60


def get_candles_since_last_check(exchange, symbol, last_checked_at):
    try:
        tf_seconds = timeframe_to_seconds(ENTRY_TIMEFRAME)
        since_ms = max(0, (int(last_checked_at) - tf_seconds * 2) * 1000)

        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=ENTRY_TIMEFRAME,
            since=since_ms,
            limit=100
        )

        if not ohlcv:
            return []

        candles = []

        for item in ohlcv:
            candles.append({
                "time": int(item[0] / 1000),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4])
            })

        return candles
    except Exception as e:
        print(symbol, "mum takip hatası:", e)
        return []


def is_entry_still_valid(signal, current_price):
    try:
        entry = float(signal["entry"])
        tp1 = float(signal["tp1"])
        sl = float(signal["sl"])

        if current_price is None or entry <= 0:
            return False

        entry_distance_percent = abs((current_price - entry) / entry) * 100

        if entry_distance_percent > MAX_ENTRY_DISTANCE_PERCENT:
            print(signal["symbol"], "elendi -> geç giriş:", round(entry_distance_percent, 2))
            return False

        tp1_distance = tp1 - entry
        current_progress = current_price - entry

        if tp1_distance <= 0:
            return False

        progress_percent = (current_progress / tp1_distance) * 100

        if progress_percent >= MAX_TP1_PROGRESS_PERCENT:
            print(signal["symbol"], "elendi -> TP1'e yaklaşmış:", round(progress_percent, 2))
            return False

        if current_price >= tp1:
            print(signal["symbol"], "elendi -> TP1 zaten gelmiş")
            return False

        if current_price <= sl:
            print(signal["symbol"], "elendi -> SL tarafında")
            return False

        return True
    except Exception as e:
        print("Geç giriş kontrol hatası:", e)
        return False


def is_duplicate_signal(signal, open_signals):
    try:
        key = f"{signal['symbol']}_{signal['direction']}"
        old = open_signals.get(key)

        if not old:
            return False

        opened_at = int(old.get("opened_at", 0))
        now = int(time.time())

        if now - opened_at < DUPLICATE_BLOCK_SECONDS:
            print(key, "tekrar sinyal engellendi.")
            return True

        return False
    except Exception as e:
        print("Tekrar sinyal kontrol hatası:", e)
        return False


def check_open_signals(exchange):
    open_signals = load_open_signals()

    if not open_signals:
        print("Takip edilen açık sinyal yok.")
        return

    print("Takip edilen açık sinyal sayısı:", len(open_signals))

    updated = {}

    for key, signal in open_signals.items():
        try:
            symbol = signal["symbol"]
            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            tp2 = float(signal.get("tp2", 0))
            tp3 = float(signal.get("tp3", 0))
            sl = float(signal["sl"])

            tp1_hit = bool(signal.get("tp1_hit", False))
            tp2_hit = bool(signal.get("tp2_hit", False))
            tp3_hit = bool(signal.get("tp3_hit", False))

            last_checked_at = int(
                signal.get(
                    "last_checked_at",
                    signal.get("opened_at", int(time.time()) - 3600)
                )
            )

            candles = get_candles_since_last_check(exchange, symbol, last_checked_at)
            current_price = get_current_price(exchange, symbol)

            if not candles or current_price is None:
                updated[key] = signal
                continue

            high = max(c["high"] for c in candles)
            low = min(c["low"] for c in candles)

            signal["last_checked_at"] = int(time.time())

            # LONG takip.
            # TP1 öncesi aynı aralıkta hem TP1 hem SL varsa temkinli davranıp SL sayıyoruz.
            if not tp1_hit:
                if low <= sl:
                    send_telegram(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"SL: {format_price(sl)}\n"
                        f"En düşük: {format_price(low)}\n"
                        f"Güncel: {format_price(current_price)}"
                    )

                    update_performance(symbol, "SL")
                    continue

                if high >= tp1:
                    send_telegram(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"TP1: {format_price(tp1)}\n"
                        f"En yüksek: {format_price(high)}\n"
                        f"Güncel: {format_price(current_price)}\n\n"
                        f"Öneri: %50 kâr al, kalan işlem için SL giriş fiyatına çekildi."
                    )

                    update_performance(symbol, "TP1")
                    signal["tp1_hit"] = True
                    signal["breakeven_sl"] = entry
                    tp1_hit = True

            if tp1_hit:
                if tp3 and not tp3_hit and high >= tp3:
                    send_telegram(
                        f"🏁 TP3 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"TP3: {format_price(tp3)}\n"
                        f"En yüksek: {format_price(high)}\n"
                        f"Güncel: {format_price(current_price)}"
                    )

                    update_performance(symbol, "TP3")
                    continue

                if tp2 and not tp2_hit and high >= tp2:
                    send_telegram(
                        f"✅ TP2 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"TP2: {format_price(tp2)}\n"
                        f"En yüksek: {format_price(high)}\n"
                        f"Güncel: {format_price(current_price)}"
                    )

                    update_performance(symbol, "TP2")
                    signal["tp2_hit"] = True
                    tp2_hit = True

                if low <= entry:
                    send_telegram(
                        f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"En düşük: {format_price(low)}\n"
                        f"Güncel: {format_price(current_price)}\n\n"
                        f"TP1 sonrası kalan işlem girişten kapandı."
                    )

                    update_performance(symbol, "BE")
                    continue

            updated[key] = signal

        except Exception as e:
            print(key, "açık sinyal takip hatası:", e)
            updated[key] = signal

    save_open_signals(updated)


def build_open_summary(exchange):
    open_signals = load_open_signals()

    if not open_signals:
        return None

    lines = ["📌 AÇIK SİNYAL ÖZETİ\n"]

    count = 0

    for key, signal in open_signals.items():
        try:
            symbol = signal["symbol"]
            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            sl = float(signal["sl"])
            current_price = get_current_price(exchange, symbol)

            if current_price is None:
                continue

            profit_percent = ((current_price - entry) / entry) * 100
            tp1_hit = bool(signal.get("tp1_hit", False))

            lines.append(
                f"🟢 {symbol} LONG\n"
                f"🔥 Giriş: {format_price(entry)}\n"
                f"💰 Güncel: {format_price(current_price)}\n"
                f"🎯 TP1: {format_price(tp1)}\n"
                f"🔴 SL: {format_price(entry if tp1_hit else sl)}\n"
                f"📈 Durum: %{round(profit_percent, 2)}\n"
                f"✅ TP1: {'Geldi' if tp1_hit else 'Gelmedi'}\n"
            )

            count += 1

            if count >= 8:
                lines.append("Devam eden başka açık sinyaller de var.")
                break

        except Exception as e:
            print(key, "özet hatası:", e)

    if count == 0:
        return None

    lines.append("📌 Bilgilendirme amaçlıdır. Grafikte kontrol et.")
    return "\n".join(lines)


def maybe_send_open_summary(exchange):
    if not should_send_open_summary():
        print("Açık sinyal özeti zamanı gelmedi.")
        return

    summary = build_open_summary(exchange)

    if summary:
        send_telegram(summary)
        mark_open_summary_sent()
        print("Açık sinyal özeti gönderildi.")


def main():
    print("Sade Premium V1 bot başladı.")
    print("Coin sayısı:", len(COINS))
    print("LONG açık:", ALLOW_LONG_SIGNALS)
    print("SHORT açık:", ALLOW_SHORT_SIGNALS)
    print("Bot emir açmaz, sadece Telegram sinyali gönderir.")

    exchange = get_exchange()

    check_open_signals(exchange)
    maybe_send_open_summary(exchange)

    candidates = []

    for symbol in COINS:
        try:
            print(symbol, "analiz ediliyor...")

            df15m = fetch_df(exchange, symbol, ENTRY_TIMEFRAME, ENTRY_LIMIT)
            df1h = fetch_df(exchange, symbol, CONFIRM_TIMEFRAME, CONFIRM_LIMIT)
            df4h = fetch_df(exchange, symbol, TREND_TIMEFRAME, TREND_LIMIT)

            signal = analyze_signal(symbol, df15m, df1h, df4h)

            if signal is None:
                time.sleep(0.2)
                continue

            if signal["direction"] == "LONG" and not ALLOW_LONG_SIGNALS:
                print(symbol, "LONG kapalı olduğu için elendi.")
                continue

            if signal["direction"] == "SHORT" and not ALLOW_SHORT_SIGNALS:
                print(symbol, "SHORT kapalı olduğu için elendi.")
                continue

            current_price = get_current_price(exchange, symbol)

            if not is_entry_still_valid(signal, current_price):
                print(symbol, "geç giriş nedeniyle gönderilmedi.")
                continue

            signal["current_price"] = current_price
            candidates.append(signal)

            time.sleep(0.2)

        except Exception as e:
            print(symbol, "analiz hatası:", e)

    candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)

    open_signals = load_open_signals()
    filtered = []

    for signal in candidates:
        if is_duplicate_signal(signal, open_signals):
            continue

        filtered.append(signal)

    strong_signals = filtered[:MAX_SIGNALS]

    print("Aday sinyal:", len(candidates))
    print("Gönderilecek sinyal:", len(strong_signals))

    if strong_signals:
        send_telegram(
            f"✅ Sade Premium V1 bot çalıştı.\n"
            f"Taranan coin: {len(COINS)}\n"
            f"Uygun aday: {len(candidates)}\n"
            f"Gönderilen sinyal: {len(strong_signals)}\n"
            f"Sistem: Sadece LONG + 4H/1H onay + 15M giriş.\n"
            f"Emir açılmadı, sadece sinyal gönderildi."
        )

        open_signals = load_open_signals()

        for signal in strong_signals:
            try:
                extra = (
                    f"\n💰 Güncel Fiyat: {format_price(signal['current_price'])}\n"
                    f"📌 Giriş Farkı: %{round(abs((signal['current_price'] - signal['entry']) / signal['entry']) * 100, 2)}\n"
                )

                send_telegram(signal["message"] + extra)

                key = f"{signal['symbol']}_{signal['direction']}"

                open_signals[key] = {
                    "symbol": signal["symbol"],
                    "direction": signal["direction"],
                    "entry": signal["entry"],
                    "tp1": signal["tp1"],
                    "tp2": signal["tp2"],
                    "tp3": signal["tp3"],
                    "sl": signal["sl"],
                    "score": signal["score"],
                    "risk_percent": signal.get("risk_percent"),
                    "opened_at": int(time.time()),
                    "last_checked_at": int(time.time()),
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "tp3_hit": False,
                    "breakeven_sl": None
                }

                update_performance(signal["symbol"], "OPENED")
                time.sleep(1)

            except Exception as e:
                print(signal.get("symbol", "-"), "sinyal gönderim/kayıt hatası:", e)

        save_open_signals(open_signals)

    else:
        print("Uygun sinyal yok.")

        if SEND_NO_SIGNAL_MESSAGE:
            send_telegram(
                f"📡 Sade Premium V1 bot çalıştı.\n\n"
                f"Taranan coin: {len(COINS)}\n"
                f"Şu an uygun LONG sinyal yok.\n"
                f"Sistem: 4H trend + 1H onay + 15M giriş.\n"
                f"SHORT kapalı."
            )

    maybe_send_daily_report()
    print("Sade Premium V1 bot tamamlandı.")


if __name__ == "__main__":
    main()
