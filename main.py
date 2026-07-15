# main.py
# Premium GitHub V2
# GitHub Actions için 5 dakikalık Telegram sinyal botu.
# Emir açmaz. Sadece sinyal gönderir ve sinyal sonuçlarını takip eder.

import os
import time
import json
import requests
import pandas as pd
import ccxt
from datetime import datetime, timezone, timedelta

from config import (
    BOT_NAME,
    AUTO_TOP_VOLUME_SCAN,
    MAX_SCAN_COINS,
    MIN_24H_QUOTE_VOLUME,
    COINS,
    ALLOW_LONG,
    ALLOW_SHORT,
    MAX_SIGNALS_PER_RUN,
    MAX_OPEN_SIGNALS,
    SEND_STATUS_EVERY_MINUTES,
    RADAR_ENABLED,
    RADAR_TIMEFRAME,
    ENTRY_TIMEFRAME,
    CONFIRM_TIMEFRAME,
    TREND_TIMEFRAME,
    RADAR_LIMIT,
    ENTRY_LIMIT,
    CONFIRM_LIMIT,
    TREND_LIMIT,
    MAX_ENTRY_DISTANCE_PERCENT,
    MAX_TP1_PROGRESS_PERCENT,
    DUPLICATE_BLOCK_SECONDS,
    RADAR_DUPLICATE_BLOCK_SECONDS,
    MAX_OPEN_SIGNAL_HOURS,
    OPEN_SUMMARY_EVERY_MINUTES,
    DAILY_REPORT_HOUR,
    DAILY_REPORT_MINUTE,
    MAX_DAILY_STOP_ALERTS,
    BLOCK_COIN_AFTER_DAILY_STOP,
    MARKET_GUARD_ENABLED,
    MARKET_REFERENCE_COINS,
    MARKET_LONG_MIN_OK_COUNT,
    MARKET_SHORT_MIN_OK_COUNT,
    MARKET_MAX_COUNTER_5M_MOVE_PERCENT,
    DAILY_DIRECTION_STOP_LIMIT,
)
from strategy import (
    analyze_normal_signal,
    analyze_radar_signal,
    format_price,
)


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"
LAST_SIGNALS_FILE = "last_signals.json"

TR_TIMEZONE = timezone(timedelta(hours=3))


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik.")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": message
            },
            timeout=20
        )

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
            return {}

        return data

    except Exception as e:
        print(filename, "okuma hatası:", e)
        return {}


def save_json_file(filename, data):
    try:
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data if isinstance(data, dict) else {}, f, indent=2, ensure_ascii=False)

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


def load_last_signals():
    return load_json_file(LAST_SIGNALS_FILE)


def save_last_signals(data):
    return save_json_file(LAST_SIGNALS_FILE, data)


def now_ts():
    return int(time.time())


def today_key():
    return datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d")


def ensure_perf_day(performance):
    today = today_key()

    performance.setdefault("days", {})
    performance["days"].setdefault(today, {
        "opened": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "expired": 0,
        "coins": {},
        "long": 0,
        "short": 0,
        "normal": 0,
        "radar": 0
    })

    return performance


def update_performance(symbol, result, direction=None, source=None):
    performance = load_performance()
    performance = ensure_perf_day(performance)
    today = today_key()
    day = performance["days"][today]

    if result == "OPENED":
        day["opened"] += 1

        if direction == "LONG":
            day["long"] += 1
        elif direction == "SHORT":
            day["short"] += 1

        if source == "RADAR":
            day["radar"] += 1
        else:
            day["normal"] += 1

    elif result in ["TP1", "TP2", "TP3", "SL", "BE", "EXPIRED"]:
        key = result.lower()
        day[key] = day.get(key, 0) + 1

        if result == "SL" and direction in ["LONG", "SHORT"]:
            day.setdefault("direction_stops", {})
            day["direction_stops"][direction] = int(day["direction_stops"].get(direction, 0)) + 1

    day.setdefault("coins", {})
    day["coins"].setdefault(symbol, {
        "opened": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "expired": 0
    })

    coin = day["coins"][symbol]

    if result == "OPENED":
        coin["opened"] += 1
    elif result in ["TP1", "TP2", "TP3", "SL", "BE", "EXPIRED"]:
        coin[result.lower()] = coin.get(result.lower(), 0) + 1

    performance["last_update"] = now_ts()
    save_performance(performance)


def get_today_sl_count():
    performance = load_performance()
    day = performance.get("days", {}).get(today_key(), {})
    return int(day.get("sl", 0))


def has_coin_stop_today(symbol):
    if not BLOCK_COIN_AFTER_DAILY_STOP:
        return False

    performance = load_performance()
    day = performance.get("days", {}).get(today_key(), {})
    coin = day.get("coins", {}).get(symbol, {})

    return int(coin.get("sl", 0)) > 0


def get_today_direction_stop_count(direction):
    performance = load_performance()
    day = performance.get("days", {}).get(today_key(), {})
    direction_stops = day.get("direction_stops", {})

    return int(direction_stops.get(direction, 0))


def direction_stop_limit_reached(direction):
    return get_today_direction_stop_count(direction) >= DAILY_DIRECTION_STOP_LIMIT


def simple_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def get_market_direction_status(exchange):
    if not MARKET_GUARD_ENABLED:
        return {
            "LONG": True,
            "SHORT": True,
            "reason": "Market koruma kapalı"
        }

    long_ok = 0
    short_ok = 0
    hard_red_count = 0
    hard_green_count = 0
    details = []

    for ref_symbol in MARKET_REFERENCE_COINS:
        try:
            df15 = fetch_df(exchange, ref_symbol, ENTRY_TIMEFRAME, 80, min_len=40)
            df5 = fetch_df(exchange, ref_symbol, RADAR_TIMEFRAME, 40, min_len=20)

            if df15 is None or df5 is None:
                continue

            df15 = df15.copy()
            df15["ema20"] = simple_ema(df15["close"], 20)

            last15 = df15.iloc[-2]
            close15 = float(last15["close"])
            ema20 = float(last15["ema20"])

            last5 = df5.iloc[-2]
            move5 = ((float(last5["close"]) - float(last5["open"])) / float(last5["open"])) * 100

            ref_long_ok = close15 >= ema20 and move5 > -MARKET_MAX_COUNTER_5M_MOVE_PERCENT
            ref_short_ok = close15 <= ema20 and move5 < MARKET_MAX_COUNTER_5M_MOVE_PERCENT

            if ref_long_ok:
                long_ok += 1

            if ref_short_ok:
                short_ok += 1

            if move5 <= -MARKET_MAX_COUNTER_5M_MOVE_PERCENT:
                hard_red_count += 1

            if move5 >= MARKET_MAX_COUNTER_5M_MOVE_PERCENT:
                hard_green_count += 1

            details.append(f"{ref_symbol}: 15M {'EMA20 üstü' if close15 >= ema20 else 'EMA20 altı'}, 5M %{round(move5, 2)}")

        except Exception as e:
            print(ref_symbol, "market koruma veri hatası:", e)

    allow_long = long_ok >= MARKET_LONG_MIN_OK_COUNT and hard_red_count < 2
    allow_short = short_ok >= MARKET_SHORT_MIN_OK_COUNT and hard_green_count < 2

    reason = (
        f"Market LONG uygun: {long_ok}/{len(MARKET_REFERENCE_COINS)} | "
        f"SHORT uygun: {short_ok}/{len(MARKET_REFERENCE_COINS)} | "
        f"Sert kırmızı: {hard_red_count} | Sert yeşil: {hard_green_count} | "
        + " | ".join(details)
    )

    print("Market koruma:", reason)

    return {
        "LONG": allow_long,
        "SHORT": allow_short,
        "reason": reason
    }


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


def okx_symbol_to_bot_symbol(okx_symbol):
    base = okx_symbol.split("/")[0]
    return f"{base}USDT".upper()


def safe_quote_volume(ticker):
    try:
        value = ticker.get("quoteVolume")

        if value is not None:
            return float(value)

        info = ticker.get("info", {})

        for key in ["volCcy24h", "volUsd24h", "vol24h"]:
            value = info.get(key)

            if value is not None:
                return float(value)

    except Exception:
        pass

    return 0.0


def get_scan_coins(exchange):
    if not AUTO_TOP_VOLUME_SCAN:
        return COINS

    try:
        markets = exchange.load_markets()
        okx_symbols = []
        stable_bases = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD"}

        for market in markets.values():
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

            if not base or base in stable_bases:
                continue

            okx_symbols.append(okx_symbol)

        tickers = exchange.fetch_tickers(okx_symbols)
        rows = []

        for okx_symbol in okx_symbols:
            ticker = tickers.get(okx_symbol, {})
            volume = safe_quote_volume(ticker)

            if volume < MIN_24H_QUOTE_VOLUME:
                continue

            rows.append((okx_symbol_to_bot_symbol(okx_symbol), volume))

        if not rows:
            print("Hacimli liste alınamadı, yedek coin listesi kullanılacak.")
            return COINS

        rows = sorted(rows, key=lambda x: x[1], reverse=True)

        volume_coins = [coin for coin, _ in rows]
        priority = [coin for coin in COINS if coin in volume_coins]
        others = [coin for coin in volume_coins if coin not in priority]
        scan_coins = (priority + others)[:MAX_SCAN_COINS]

        print("Hacimli coin sayısı:", len(rows))
        print("Taranacak coin:", len(scan_coins))
        print("İlk 10:", scan_coins[:10])

        return scan_coins

    except Exception as e:
        print("Top volume tarama hatası:", e)
        return COINS


def fetch_df(exchange, symbol, timeframe, limit, min_len=30):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            limit=limit
        )

        if not ohlcv or len(ohlcv) < min_len:
            return None

        return pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )

    except Exception as e:
        print(symbol, timeframe, "veri hatası:", e)
        return None


def fetch_candles_since(exchange, symbol, timeframe, since_seconds, limit=150):
    try:
        since_ms = max(0, int(since_seconds) * 1000)
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            since=since_ms,
            limit=limit
        )

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
        print(symbol, "takip mum hatası:", e)
        return []


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


def is_entry_still_valid(signal, current_price):
    try:
        entry = float(signal["entry"])
        tp1 = float(signal["tp1"])
        sl = float(signal["sl"])
        direction = signal["direction"]

        if current_price is None or entry <= 0:
            return False, "güncel fiyat yok"

        entry_distance = abs((current_price - entry) / entry) * 100

        if entry_distance > MAX_ENTRY_DISTANCE_PERCENT:
            return False, f"girişten uzak: %{round(entry_distance, 2)}"

        if direction == "LONG":
            total = tp1 - entry
            progressed = current_price - entry

            if total <= 0:
                return False, "TP1 hatalı"

            progress_percent = (progressed / total) * 100

            if progress_percent >= MAX_TP1_PROGRESS_PERCENT:
                return False, f"TP1'e yaklaşmış: %{round(progress_percent, 2)}"

            if current_price >= tp1:
                return False, "TP1 zaten gelmiş"

            if current_price <= sl:
                return False, "SL tarafında"

        else:
            total = entry - tp1
            progressed = entry - current_price

            if total <= 0:
                return False, "TP1 hatalı"

            progress_percent = (progressed / total) * 100

            if progress_percent >= MAX_TP1_PROGRESS_PERCENT:
                return False, f"TP1'e yaklaşmış: %{round(progress_percent, 2)}"

            if current_price <= tp1:
                return False, "TP1 zaten gelmiş"

            if current_price >= sl:
                return False, "SL tarafında"

        return True, "uygun"

    except Exception as e:
        return False, f"giriş kontrol hatası: {e}"


def is_duplicate(signal):
    last_signals = load_last_signals()
    key = f"{signal.get('source', 'NORMAL')}_{signal['symbol']}_{signal['direction']}"
    last_time = int(last_signals.get(key, 0))
    wait = RADAR_DUPLICATE_BLOCK_SECONDS if signal.get("source") == "RADAR" else DUPLICATE_BLOCK_SECONDS

    if now_ts() - last_time < wait:
        return True

    return False


def mark_signal_sent(signal):
    last_signals = load_last_signals()
    key = f"{signal.get('source', 'NORMAL')}_{signal['symbol']}_{signal['direction']}"
    last_signals[key] = now_ts()
    save_last_signals(last_signals)


def has_open_same_symbol(symbol):
    open_signals = load_open_signals()

    for signal in open_signals.values():
        if signal.get("symbol") == symbol:
            return True

    return False


def check_open_signals(exchange):
    open_signals = load_open_signals()

    if not open_signals:
        print("Açık sinyal yok.")
        return

    updated = {}
    max_age = MAX_OPEN_SIGNAL_HOURS * 60 * 60

    for key, signal in open_signals.items():
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]
            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            tp2 = float(signal["tp2"])
            tp3 = float(signal["tp3"])
            sl = float(signal["sl"])

            opened_at = int(signal.get("opened_at", now_ts()))
            last_checked_at = int(signal.get("last_checked_at", opened_at))

            if now_ts() - opened_at > max_age:
                send_telegram(
                    f"⏳ SİNYAL SÜRESİ DOLDU\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n\n"
                    f"24 saat içinde TP/SL netleşmediği için takipten çıkarıldı."
                )
                update_performance(symbol, "EXPIRED")
                continue

            candles = fetch_candles_since(
                exchange,
                symbol,
                ENTRY_TIMEFRAME,
                since_seconds=max(opened_at, last_checked_at - 20 * 60),
                limit=120
            )

            if not candles:
                updated[key] = signal
                continue

            high = max(c["high"] for c in candles)
            low = min(c["low"] for c in candles)
            current_price = get_current_price(exchange, symbol)
            tp1_hit = bool(signal.get("tp1_hit", False))
            tp2_hit = bool(signal.get("tp2_hit", False))
            tp3_hit = bool(signal.get("tp3_hit", False))

            if direction == "LONG":
                if not tp1_hit and low <= sl:
                    send_telegram(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"SL: {format_price(sl)}\n"
                        f"Güncel: {format_price(current_price or sl)}"
                    )
                    update_performance(symbol, "SL", direction=direction, source=signal.get("source"))
                    continue

                if not tp1_hit and high >= tp1:
                    send_telegram(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"TP1: {format_price(tp1)}\n"
                        f"Öneri: %50 kâr al, kalan işlem için SL girişe çek."
                    )
                    signal["tp1_hit"] = True
                    tp1_hit = True
                    update_performance(symbol, "TP1")

                if tp1_hit and not tp2_hit and high >= tp2:
                    send_telegram(
                        f"✅ TP2 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"TP2: {format_price(tp2)}"
                    )
                    signal["tp2_hit"] = True
                    tp2_hit = True
                    update_performance(symbol, "TP2")

                if tp1_hit and not tp3_hit and high >= tp3:
                    send_telegram(
                        f"🏁 TP3 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"TP3: {format_price(tp3)}\n"
                        f"Sinyal maksimum hedefe ulaştı."
                    )
                    update_performance(symbol, "TP3")
                    continue

                if tp1_hit and low <= entry:
                    send_telegram(
                        f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: LONG 🟢\n"
                        f"Giriş: {format_price(entry)}"
                    )
                    update_performance(symbol, "BE")
                    continue

            else:
                if not tp1_hit and high >= sl:
                    send_telegram(
                        f"❌ STOP OLDU\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"SL: {format_price(sl)}\n"
                        f"Güncel: {format_price(current_price or sl)}"
                    )
                    update_performance(symbol, "SL", direction=direction, source=signal.get("source"))
                    continue

                if not tp1_hit and low <= tp1:
                    send_telegram(
                        f"✅ TP1 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"TP1: {format_price(tp1)}\n"
                        f"Öneri: %50 kâr al, kalan işlem için SL girişe çek."
                    )
                    signal["tp1_hit"] = True
                    tp1_hit = True
                    update_performance(symbol, "TP1")

                if tp1_hit and not tp2_hit and low <= tp2:
                    send_telegram(
                        f"✅ TP2 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"TP2: {format_price(tp2)}"
                    )
                    signal["tp2_hit"] = True
                    tp2_hit = True
                    update_performance(symbol, "TP2")

                if tp1_hit and not tp3_hit and low <= tp3:
                    send_telegram(
                        f"🏁 TP3 GELDİ\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"TP3: {format_price(tp3)}\n"
                        f"Sinyal maksimum hedefe ulaştı."
                    )
                    update_performance(symbol, "TP3")
                    continue

                if tp1_hit and high >= entry:
                    send_telegram(
                        f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: SHORT 🔴\n"
                        f"Giriş: {format_price(entry)}"
                    )
                    update_performance(symbol, "BE")
                    continue

            signal["last_checked_at"] = now_ts()
            updated[key] = signal

        except Exception as e:
            print(key, "açık sinyal takip hatası:", e)
            updated[key] = signal

    save_open_signals(updated)


def should_send_status():
    performance = load_performance()
    last_status = int(performance.get("last_status_message", 0))
    return now_ts() - last_status >= SEND_STATUS_EVERY_MINUTES * 60


def mark_status_sent():
    performance = load_performance()
    performance["last_status_message"] = now_ts()
    save_performance(performance)


def maybe_send_open_summary(exchange):
    performance = load_performance()
    last_summary = int(performance.get("last_open_summary", 0))

    if now_ts() - last_summary < OPEN_SUMMARY_EVERY_MINUTES * 60:
        return

    open_signals = load_open_signals()

    if not open_signals:
        return

    lines = ["📌 AÇIK SİNYAL ÖZETİ\n"]

    for signal in list(open_signals.values())[:10]:
        try:
            symbol = signal["symbol"]
            direction = signal["direction"]
            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            sl = float(signal["sl"])
            current = get_current_price(exchange, symbol)

            if current is None:
                continue

            if direction == "LONG":
                profit = ((current - entry) / entry) * 100
                tp_distance = ((tp1 - current) / current) * 100
                icon = "🟢"
            else:
                profit = ((entry - current) / entry) * 100
                tp_distance = ((current - tp1) / current) * 100
                icon = "🔴"

            lines.append(
                f"{icon} {symbol} {direction}\n"
                f"Giriş: {format_price(entry)} | Güncel: {format_price(current)}\n"
                f"TP1: {format_price(tp1)} | SL: {format_price(sl)}\n"
                f"Durum: %{round(profit, 2)} | TP1 uzaklık: %{round(tp_distance, 2)}\n"
            )

        except Exception as e:
            print("Özet satır hatası:", e)

    lines.append("Bilgilendirme amaçlıdır. Grafikte kontrol et.")

    send_telegram("\n".join(lines))

    performance["last_open_summary"] = now_ts()
    save_performance(performance)


def build_daily_report():
    performance = load_performance()
    day = performance.get("days", {}).get(today_key(), {})

    opened = int(day.get("opened", 0))
    tp1 = int(day.get("tp1", 0))
    tp2 = int(day.get("tp2", 0))
    tp3 = int(day.get("tp3", 0))
    sl = int(day.get("sl", 0))
    be = int(day.get("be", 0))
    expired = int(day.get("expired", 0))
    long_count = int(day.get("long", 0))
    short_count = int(day.get("short", 0))
    normal_count = int(day.get("normal", 0))
    radar_count = int(day.get("radar", 0))
    open_count = len(load_open_signals())

    closed = tp1 + sl
    success = round((tp1 / closed) * 100, 2) if closed > 0 else 0

    coins = day.get("coins", {})
    best_coin = "Yok"
    worst_coin = "Yok"
    best_rate = -1
    worst_rate = 101

    for coin, stats in coins.items():
        c_tp1 = int(stats.get("tp1", 0))
        c_sl = int(stats.get("sl", 0))
        c_closed = c_tp1 + c_sl

        if c_closed <= 0:
            continue

        rate = round((c_tp1 / c_closed) * 100, 2)

        if rate > best_rate:
            best_rate = rate
            best_coin = f"{coin} (%{rate})"

        if rate < worst_rate:
            worst_rate = rate
            worst_coin = f"{coin} (%{rate})"

    return f"""
📊 GÜNLÜK PERFORMANS RAPORU

📅 Tarih: {today_key()}

📈 Açılan Sinyal: {opened}
🟢 LONG: {long_count}
🔴 SHORT: {short_count}
✅ Normal: {normal_count}
⚡ Radar: {radar_count}

✅ TP1 Gelen: {tp1}
✅ TP2 Gelen: {tp2}
✅ TP3 Gelen: {tp3}
🟡 Girişten Kapanan: {be}
❌ Stop Olan: {sl}
⏳ Süresi Dolan: {expired}
📌 Açık Sinyal: {open_count}

📊 TP1 Başarı Oranı: %{success}

🏆 En İyi Coin: {best_coin}
⚠️ En Zayıf Coin: {worst_coin}

📌 Not:
TP1 sonrası kalan işlem için SL giriş fiyatı kabul edilir.
Bu bot emir açmaz, sadece sinyal gönderir.
"""


def maybe_send_daily_report():
    now = datetime.now(TR_TIMEZONE)
    today = today_key()

    if now.hour != DAILY_REPORT_HOUR or now.minute < DAILY_REPORT_MINUTE:
        return

    performance = load_performance()

    if performance.get("last_daily_report") == today:
        return

    send_telegram(build_daily_report())

    performance["last_daily_report"] = today
    save_performance(performance)


def save_open_signal(signal):
    open_signals = load_open_signals()
    key = f"{signal['symbol']}_{signal['direction']}_{signal.get('source', 'NORMAL')}"

    open_signals[key] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "source": signal.get("source", "NORMAL"),
        "entry": signal["entry"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "tp3": signal["tp3"],
        "sl": signal["sl"],
        "score": signal["score"],
        "risk_percent": signal.get("risk_percent"),
        "opened_at": now_ts(),
        "last_checked_at": now_ts(),
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False
    }

    save_open_signals(open_signals)


def main():
    print(BOT_NAME, "başladı.")
    exchange = get_exchange()

    check_open_signals(exchange)
    maybe_send_open_summary(exchange)

    if get_today_sl_count() >= MAX_DAILY_STOP_ALERTS:
        print("Günlük stop limiti doldu, yeni sinyal gönderilmeyecek.")
        if should_send_status():
            send_telegram(
                f"⛔ {BOT_NAME} çalıştı.\n\n"
                f"Bugün stop limiti dolduğu için yeni sinyal gönderilmiyor.\n"
                f"Risk kontrolü aktif."
            )
            mark_status_sent()

        maybe_send_daily_report()
        print(BOT_NAME, "tamamlandı.")
        return

    scan_coins = get_scan_coins(exchange)
    open_signals = load_open_signals()
    market_status = get_market_direction_status(exchange)

    print("Taranan coin:", len(scan_coins))
    print("Açık sinyal:", len(open_signals))
    print("Radar aktif:", RADAR_ENABLED)

    candidates = []

    for symbol in scan_coins:
        try:
            if len(load_open_signals()) >= MAX_OPEN_SIGNALS:
                print("Maksimum açık sinyal sınırı doldu.")
                break

            if has_open_same_symbol(symbol):
                print(symbol, "zaten açık sinyal var, atlandı.")
                continue

            if has_coin_stop_today(symbol):
                print(symbol, "bugün stop olduğu için atlandı.")
                continue

            current_price = get_current_price(exchange, symbol)

            df5m = fetch_df(exchange, symbol, RADAR_TIMEFRAME, RADAR_LIMIT, min_len=35)
            df15m = fetch_df(exchange, symbol, ENTRY_TIMEFRAME, ENTRY_LIMIT, min_len=220)
            df1h = fetch_df(exchange, symbol, CONFIRM_TIMEFRAME, CONFIRM_LIMIT, min_len=220)
            df4h = fetch_df(exchange, symbol, TREND_TIMEFRAME, TREND_LIMIT, min_len=220)

            # Normal daha onaylı sinyal
            normal_signal = None

            if df15m is not None and df1h is not None and df4h is not None:
                normal_signal = analyze_normal_signal(symbol, df15m, df1h, df4h, current_price)

            if normal_signal is not None:
                if normal_signal["direction"] == "LONG" and not ALLOW_LONG:
                    normal_signal = None
                elif normal_signal["direction"] == "SHORT" and not ALLOW_SHORT:
                    normal_signal = None

            if normal_signal is not None:
                if not market_status.get(normal_signal["direction"], True):
                    print(symbol, "normal elendi -> market koruma", normal_signal["direction"])
                    normal_signal = None
                elif direction_stop_limit_reached(normal_signal["direction"]):
                    print(symbol, "normal elendi -> günlük yön stop limiti", normal_signal["direction"])
                    normal_signal = None

            if normal_signal is not None:
                valid, reason = is_entry_still_valid(normal_signal, current_price)

                if valid and not is_duplicate(normal_signal):
                    candidates.append(normal_signal)
                    print(symbol, "NORMAL aday:", normal_signal["direction"], normal_signal["score"])
                else:
                    print(symbol, "normal elendi ->", reason)

            # Radar daha hızlı sinyal
            if RADAR_ENABLED:
                radar_signal = analyze_radar_signal(symbol, df5m, df15m, df1h, current_price)

                if radar_signal is not None:
                    if radar_signal["direction"] == "LONG" and not ALLOW_LONG:
                        radar_signal = None
                    elif radar_signal["direction"] == "SHORT" and not ALLOW_SHORT:
                        radar_signal = None

                if radar_signal is not None:
                    if not market_status.get(radar_signal["direction"], True):
                        print(symbol, "radar elendi -> market koruma", radar_signal["direction"])
                        radar_signal = None
                    elif direction_stop_limit_reached(radar_signal["direction"]):
                        print(symbol, "radar elendi -> günlük yön stop limiti", radar_signal["direction"])
                        radar_signal = None

                if radar_signal is not None:
                    valid, reason = is_entry_still_valid(radar_signal, current_price)

                    if valid and not is_duplicate(radar_signal):
                        candidates.append(radar_signal)
                        print(symbol, "RADAR aday:", radar_signal["direction"], radar_signal["score"])
                    else:
                        print(symbol, "radar elendi ->", reason)

            time.sleep(0.15)

        except Exception as e:
            print(symbol, "analiz hatası:", e)

    # Normal sinyale küçük öncelik, sonra skor.
    def sort_key(signal):
        source_bonus = 8 if signal.get("source") == "NORMAL" else 0
        return signal["score"] + source_bonus

    candidates = sorted(candidates, key=sort_key, reverse=True)
    selected = candidates[:MAX_SIGNALS_PER_RUN]

    if selected:
        long_count = len([s for s in selected if s["direction"] == "LONG"])
        short_count = len([s for s in selected if s["direction"] == "SHORT"])
        normal_count = len([s for s in selected if s.get("source") == "NORMAL"])
        radar_count = len([s for s in selected if s.get("source") == "RADAR"])

        send_telegram(
            f"✅ {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {len(scan_coins)}\n"
            f"Uygun aday: {len(candidates)}\n"
            f"Gönderilen sinyal: {len(selected)}\n"
            f"LONG: {long_count} | SHORT: {short_count}\n"
            f"Normal: {normal_count} | Radar: {radar_count}\n"
            f"Sistem: 5M radar + 15M giriş + 1H/4H onay + stop filtresi + market koruma.\n"
            f"Emir açılmadı, sadece sinyal gönderildi."
        )

        for signal in selected:
            current_price = get_current_price(exchange, signal["symbol"])
            signal["current_price"] = current_price

            valid, reason = is_entry_still_valid(signal, current_price)

            if not valid:
                print(signal["symbol"], "son kontrol elendi:", reason)
                continue

            extra = (
                f"\n💰 Güncel Fiyat: {format_price(current_price)}\n"
                f"📌 Son Kontrol: Girişe yakın ✅\n"
            )

            if send_telegram(signal["message"] + extra):
                save_open_signal(signal)
                mark_signal_sent(signal)
                update_performance(
                    symbol=signal["symbol"],
                    result="OPENED",
                    direction=signal["direction"],
                    source=signal.get("source", "NORMAL")
                )

            time.sleep(1)

    else:
        print("Uygun sinyal yok.")

        if should_send_status():
            send_telegram(
                f"📡 {BOT_NAME} çalıştı.\n\n"
                f"Taranan coin: {len(scan_coins)}\n"
                f"Uygun giriş sinyali yok.\n"
                f"Sistem: 5M radar + 15M giriş + 1H/4H onay + stop filtresi + market koruma.\n"
                f"Geç giriş, TP1'e yaklaşmış, stop riski yükselen ve piyasa yönüne ters sinyaller gönderilmedi."
            )
            mark_status_sent()

    maybe_send_daily_report()
    print(BOT_NAME, "tamamlandı.")


if __name__ == "__main__":
    main()
