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

# OKX'teki tüm aktif USDT swap/futures paritelerini otomatik tara
AUTO_SCAN_ALL_OKX_USDT = True

# 4H ana trend filtresi
HIGHER_TIMEFRAME = "4h"
HIGHER_LIMIT = 300

OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"

TR_TIMEZONE = timezone(timedelta(hours=3))
DAILY_REPORT_HOUR = 23
DAILY_REPORT_MINUTE = 45

OPEN_SUMMARY_EVERY_MINUTES = 60
DUPLICATE_BLOCK_SECONDS = 45 * 60

STABLE_BASES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD"}


def format_price(value):
    """
    PEPE gibi fiyatı çok küçük coinlerde 3e-06 görünmesini engeller.
    Fiyatları okunabilir şekilde gösterir.
    """
    value = float(value)

    if value >= 100:
        return f"{value:.2f}"
    elif value >= 1:
        return f"{value:.4f}"
    elif value >= 0.01:
        return f"{value:.6f}"
    else:
        return f"{value:.10f}"


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
            print(filename, "dict formatında değil, sıfırlandı.")
            return {}

        return data

    except Exception as e:
        print(filename, "okuma hatası:", e)
        return {}


def save_json_file(filename, data):
    try:
        if not isinstance(data, dict):
            print(filename, "kaydedilecek veri dict değil, {} olarak kaydedildi.")
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


def update_performance(symbol, direction, result):
    performance = load_performance()
    today = get_today_key()

    if "total" not in performance or not isinstance(performance.get("total"), dict):
        performance["total"] = {}

    performance["total"] = ensure_stats_bucket(performance["total"])

    if "coins" not in performance or not isinstance(performance.get("coins"), dict):
        performance["coins"] = {}

    if symbol not in performance["coins"] or not isinstance(performance["coins"].get(symbol), dict):
        performance["coins"][symbol] = {}

    performance["coins"][symbol] = ensure_stats_bucket(performance["coins"][symbol])

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

    if symbol not in performance["days"][today]["coins"] or not isinstance(performance["days"][today]["coins"].get(symbol), dict):
        performance["days"][today]["coins"][symbol] = {}

    performance["days"][today]["coins"][symbol] = ensure_stats_bucket(
        performance["days"][today]["coins"][symbol]
    )

    result_to_field = {
        "OPENED": "signals",
        "TP1": "tp1",
        "TP2": "tp2",
        "TP3": "tp3",
        "SL": "sl",
        "BE": "be"
    }

    field = result_to_field.get(result)

    if not field:
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

    default_day = {
        "signals": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "coins": {}
    }

    day_data = performance.get("days", {}).get(today, default_day)

    if not isinstance(day_data, dict):
        day_data = default_day

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

    coins_data = day_data.get("coins", {})

    if not isinstance(coins_data, dict):
        coins_data = {}

    for coin, data in coins_data.items():
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

    return now - last_summary >= wait_seconds


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


def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap"
        }
    })


def okx_symbol_to_bot_symbol(okx_symbol):
    # Örnek: BTC/USDT:USDT -> BTCUSDT
    base = okx_symbol.split("/")[0]
    return f"{base}USDT".upper()


def get_scan_coins(exchange):
    """
    AUTO_SCAN_ALL_OKX_USDT True ise OKX'teki tüm aktif USDT swap paritelerini çeker.
    Sorun olursa config.py içindeki COINS listesine geri döner.
    """
    if not AUTO_SCAN_ALL_OKX_USDT:
        print("Otomatik tarama kapalı. Config COINS listesi kullanılacak.")
        return COINS

    try:
        markets = exchange.load_markets()
        auto_coins = []

        for market in markets.values():
            try:
                if not market.get("active", True):
                    continue

                if not market.get("swap", False):
                    continue

                if market.get("quote") != "USDT":
                    continue

                if market.get("settle") != "USDT":
                    continue

                okx_symbol = market.get("symbol")

                if not okx_symbol or "/USDT:USDT" not in okx_symbol:
                    continue

                base = str(market.get("base", "")).upper()

                if not base or base in STABLE_BASES:
                    continue

                coin = okx_symbol_to_bot_symbol(okx_symbol)

                if coin not in auto_coins:
                    auto_coins.append(coin)

            except Exception as market_error:
                print("Market filtreleme hatası:", market_error)

        if not auto_coins:
            print("OKX otomatik parite bulunamadı. Config COINS listesi kullanılacak.")
            return COINS

        # Config'teki ana coinleri önce sırala, diğerlerini alfabetik ekle
        priority_coins = [coin for coin in COINS if coin in auto_coins]
        other_coins = sorted([coin for coin in auto_coins if coin not in priority_coins])

        scan_coins = priority_coins + other_coins

        print("OKX otomatik USDT swap parite sayısı:", len(scan_coins))
        return scan_coins

    except Exception as e:
        print("OKX otomatik parite çekme hatası:", e)
        print("Config COINS listesi kullanılacak.")
        return COINS


def to_okx_symbol(symbol):
    base = symbol.replace("USDT", "")
    return f"{base}/USDT:USDT"


def fetch_df_timeframe(exchange, okx_symbol, timeframe, limit):
    try:
        ohlcv = exchange.fetch_ohlcv(
            okx_symbol,
            timeframe=timeframe,
            limit=limit
        )

        if not ohlcv or len(ohlcv) < 200:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )

        return df

    except Exception as e:
        print(okx_symbol, timeframe, "veri hatası:", e)
        return None


def fetch_df(exchange, okx_symbol):
    return fetch_df_timeframe(exchange, okx_symbol, TIMEFRAME, LIMIT)


def get_4h_trend(exchange, symbol):
    """
    4H ana trend filtresi.
    LONG için: fiyat EMA200 üstünde + EMA20 EMA50 üstünde + EMA20 yukarı eğimli.
    SHORT için: fiyat EMA200 altında + EMA20 EMA50 altında + EMA20 aşağı eğimli.
    Diğer durumlarda NEUTRAL döner ve sinyal gönderilmez.
    """
    try:
        okx_symbol = to_okx_symbol(symbol)
        df = fetch_df_timeframe(exchange, okx_symbol, HIGHER_TIMEFRAME, HIGHER_LIMIT)

        if df is None or len(df) < 220:
            print(symbol, "4H trend okunamadı: yetersiz veri")
            return "NEUTRAL"

        df = df.copy()
        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()
        df["ema20_slope"] = df["ema20"] - df["ema20"].shift(3)
        df = df.dropna()

        if df.empty or len(df) < 10:
            print(symbol, "4H trend okunamadı: indikatör yetersiz")
            return "NEUTRAL"

        # Son açık 4H mumu yerine kapanmış son 4H mumu kullanılır.
        last = df.iloc[-2]

        close = float(last["close"])
        ema20 = float(last["ema20"])
        ema50 = float(last["ema50"])
        ema200 = float(last["ema200"])
        slope = float(last["ema20_slope"])

        if close > ema200 and ema20 > ema50 and slope > 0:
            return "LONG"

        if close < ema200 and ema20 < ema50 and slope < 0:
            return "SHORT"

        return "NEUTRAL"

    except Exception as e:
        print(symbol, "4H trend hatası:", e)
        return "NEUTRAL"


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
            trend_4h = signal.get("trend_4h", "-")

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
                status = "Kâr tarafında ✅" if current_price >= entry else "Giriş altında ⚠️"

            else:
                icon = "🔴"
                tp_distance = ((current_price - tp1) / current_price) * 100
                sl_distance = ((sl - current_price) / current_price) * 100
                profit_percent = ((entry - current_price) / entry) * 100
                status = "Kâr tarafında ✅" if current_price <= entry else "Giriş üstünde ⚠️"

            tp_status = []

            if tp1_hit:
                tp_status.append("TP1 ✅")
            if tp2_hit:
                tp_status.append("TP2 ✅")
            if tp3_hit:
                tp_status.append("TP3 ✅")

            tp_text = " / ".join(tp_status) if tp_status else "Henüz TP yok"
            active_sl_text = (
                f"{format_price(entry)} (TP1 sonrası girişe çekildi)"
                if tp1_hit
                else format_price(sl)
            )

            message += (
                f"{icon} {symbol} {direction}\n"
                f"🔥 Giriş: {format_price(entry)}\n"
                f"💰 Güncel: {format_price(current_price)}\n"
                f"🎯 TP1: {format_price(tp1)}\n"
            )

            if tp2 is not None:
                message += f"🎯 TP2: {format_price(float(tp2))}\n"

            if tp3 is not None:
                message += f"🎯 TP3: {format_price(float(tp3))}\n"

            message += (
                f"🔴 Aktif SL: {active_sl_text}\n"
                f"📊 Skor: {score}\n"
                f"📌 Kalite: {quality}\n"
                f"📈 4H Trend: {trend_4h}\n"
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
                        f"Giriş: {format_price(entry)}\n"
                        f"TP1: {format_price(tp1)}\n"
                        f"Mum High: {format_price(high)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}\n\n"
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
                        f"Giriş: {format_price(entry)}\n"
                        f"TP2: {format_price(tp2)}\n"
                        f"Mum High: {format_price(high)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}\n\n"
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
                        f"Giriş: {format_price(entry)}\n"
                        f"TP3: {format_price(tp3)}\n"
                        f"Mum High: {format_price(high)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}\n\n"
                        f"Sonuç: Sinyal maksimum hedefe ulaştı ✅"
                    )

                    update_performance(symbol, direction, "TP3")
                    continue

                if tp1_hit and low <= entry:
                    send_telegram(
                        f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"Mum Low: {format_price(low)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}\n\n"
                        f"TP1 sonrası kalan işlem girişten kapandı."
                    )

                    update_performance(symbol, direction, "BE")
                    continue

                if not tp1_hit and low <= sl:
                    send_telegram(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"SL: {format_price(sl)}\n"
                        f"Mum Low: {format_price(low)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}"
                    )

                    update_performance(symbol, direction, "SL")
                    continue

            elif direction == "SHORT":
                if not tp1_hit and low <= tp1:
                    send_telegram(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"TP1: {format_price(tp1)}\n"
                        f"Mum Low: {format_price(low)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}\n\n"
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
                        f"Giriş: {format_price(entry)}\n"
                        f"TP2: {format_price(tp2)}\n"
                        f"Mum Low: {format_price(low)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}\n\n"
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
                        f"Giriş: {format_price(entry)}\n"
                        f"TP3: {format_price(tp3)}\n"
                        f"Mum Low: {format_price(low)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}\n\n"
                        f"Sonuç: Sinyal maksimum hedefe ulaştı ✅"
                    )

                    update_performance(symbol, direction, "TP3")
                    continue

                if tp1_hit and high >= entry:
                    send_telegram(
                        f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"Mum High: {format_price(high)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}\n\n"
                        f"TP1 sonrası kalan işlem girişten kapandı."
                    )

                    update_performance(symbol, direction, "BE")
                    continue

                if not tp1_hit and high >= sl:
                    send_telegram(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"SL: {format_price(sl)}\n"
                        f"Mum High: {format_price(high)}\n"
                        f"Güncel Fiyat: {format_price(current_price)}"
                    )

                    update_performance(symbol, direction, "SL")
                    continue

            updated_signals[key] = signal

        except Exception as e:
            print(key, "takip hatası:", e)
            updated_signals[key] = signal

    save_open_signals(updated_signals)


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


def main():
    print("Bot başladı...")

    exchange = get_exchange()
    scan_coins = get_scan_coins(exchange)

    print("Toplam taranan parite:", len(scan_coins))
    print("4H ana trend filtresi aktif.")
    print("OKX tüm USDT swap otomatik tarama:", AUTO_SCAN_ALL_OKX_USDT)

    check_open_signals(exchange)
    maybe_send_open_signals_summary(exchange)

    signals = []
    htf_rejected_count = 0

    for coin in scan_coins:
        try:
            okx_symbol = to_okx_symbol(coin)
            df = fetch_df(exchange, okx_symbol)

            if df is None:
                print(coin, "veri yok")
                continue

            signal = analyze_signal(coin, df)

            if signal:
                trend_4h = get_4h_trend(exchange, coin)
                signal["trend_4h"] = trend_4h

                if trend_4h != signal["direction"]:
                    print(
                        coin,
                        "elendi -> 4H trend uyumsuz |",
                        "Sinyal:",
                        signal["direction"],
                        "| 4H:",
                        trend_4h
                    )
                    htf_rejected_count += 1
                    continue

                signal["message"] += f"\n📈 4H Ana Trend: {trend_4h} uyumlu ✅\n"

                signals.append(signal)
                print(
                    coin,
                    "sinyal bulundu:",
                    signal["direction"],
                    signal["score"],
                    "kalite:",
                    signal.get("quality", "-"),
                    "4H:",
                    trend_4h
                )
            else:
                print(coin, "sinyal yok")

            time.sleep(0.2)

        except Exception as e:
            print(coin, "analiz hatası:", e)

    signals = sorted(signals, key=lambda x: x["score"], reverse=True)

    before_quality_count = len(signals)

    signals = [s for s in signals if s.get("quality") == "A"]

    print("4H trend uyumsuz elenen:", htf_rejected_count)
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

    print("LONG sinyal sayısı:", len(long_signals))
    print("SHORT sinyal sayısı:", len(short_signals))
    print("Gönderilecek detaylı sinyal sayısı:", len(strong_signals))
    print("Diğer aday sayısı:", max(len(signals) - len(strong_signals), 0))

    if strong_signals:
        send_telegram(
            f"✅ Bot çalıştı.\n"
            f"Toplam taranan parite: {len(scan_coins)}\n"
            f"4H trend uyumsuz elenen: {htf_rejected_count}\n"
            f"LONG aday: {len(long_signals)}\n"
            f"SHORT aday: {len(short_signals)}\n"
            f"Detaylı gönderilen sinyal: {len(strong_signals)}\n"
            f"OKX tüm USDT swap tarandı.\n"
            f"Sadece A kalite + 4H trend uyumlu sinyaller gönderildi."
        )

        open_signals = load_open_signals()

        for signal in strong_signals:
            try:
                send_telegram(signal["message"])

                key = f"{signal['symbol']}_{signal['direction']}"

                tp2 = get_signal_target(signal, "TP2")
                tp3 = get_signal_target(signal, "TP3")

                open_signals[key] = {
                    "symbol": signal["symbol"],
                    "direction": signal["direction"],
                    "score": signal["score"],
                    "quality": signal.get("quality", "-"),
                    "trend_4h": signal.get("trend_4h", "-"),
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

            except Exception as e:
                print(signal.get("symbol", "-"), "sinyal gönderme/kayıt hatası:", e)

        save_open_signals(open_signals)

    else:
        print("Şu an A kalite ve 4H trend uyumlu sinyal yok.")
        send_telegram(
            f"📡 Bot çalıştı.\n\n"
            f"Toplam taranan parite: {len(scan_coins)}\n"
            f"4H trend uyumsuz elenen: {htf_rejected_count}\n"
            f"Şu an A kalite ve 4H trend uyumlu sinyal yok.\n"
            f"OKX tüm USDT swap tarandı.\n"
            f"B/C kalite veya 4H ters sinyaller gönderilmedi."
        )

    maybe_send_daily_report()
    print("Bot tamamlandı.")


if __name__ == "__main__":
    main()
