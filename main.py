# main.py
# Premium GitHub V4 - Destek Direnç Futures
# Emir açmaz. Telegram sinyali ve takip bildirimi gönderir.

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
    MAX_TRADE_SIGNALS_PER_RUN,
    MAX_WATCH_ALERTS_PER_RUN,
    MAX_OPEN_SIGNALS_NORMAL,
    MAX_OPEN_SIGNALS_RISK,
    SEND_STATUS_EVERY_MINUTES,
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
    WATCH_DUPLICATE_BLOCK_SECONDS,
    BLOCK_STOPPED_COIN_HOURS,
    MAX_OPEN_SIGNAL_HOURS,
    OPEN_SUMMARY_EVERY_MINUTES,
    DAILY_REPORT_HOUR,
    DAILY_REPORT_MINUTE,
    RISK_MODE_STOP_COUNT,
    RISK_MODE_MAX_TRADE_SIGNALS,
    RISK_MODE_MAX_WATCH_ALERTS,
    RISK_MODE_ALLOW_RADAR_TRADE,
)
from strategy import analyze_futures_setup, format_price


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
            data={"chat_id": CHAT_ID, "text": message},
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
        return data if isinstance(data, dict) else {}
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
        "watch": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "expired": 0,
        "coins": {},
        "long": 0,
        "short": 0,
        "sr_trade": 0,
        "direction_stops": {}
    })
    return performance


def update_performance(symbol, result, direction=None, source=None):
    performance = ensure_perf_day(load_performance())
    today = today_key()
    day = performance["days"][today]

    if result == "OPENED":
        day["opened"] += 1
        if direction == "LONG":
            day["long"] += 1
        elif direction == "SHORT":
            day["short"] += 1
        day["sr_trade"] = int(day.get("sr_trade", 0)) + 1
    elif result == "WATCH":
        day["watch"] = int(day.get("watch", 0)) + 1
    elif result in ["TP1", "TP2", "TP3", "SL", "BE", "EXPIRED"]:
        key = result.lower()
        day[key] = int(day.get(key, 0)) + 1
        if result == "SL" and direction in ["LONG", "SHORT"]:
            day.setdefault("direction_stops", {})
            day["direction_stops"][direction] = int(day["direction_stops"].get(direction, 0)) + 1

    day.setdefault("coins", {})
    day["coins"].setdefault(symbol, {
        "opened": 0, "watch": 0, "tp1": 0, "tp2": 0, "tp3": 0, "sl": 0, "be": 0, "expired": 0
    })
    coin = day["coins"][symbol]

    if result == "OPENED":
        coin["opened"] += 1
    elif result == "WATCH":
        coin["watch"] += 1
    elif result in ["TP1", "TP2", "TP3", "SL", "BE", "EXPIRED"]:
        coin[result.lower()] = int(coin.get(result.lower(), 0)) + 1

    performance["last_update"] = now_ts()
    save_performance(performance)


def get_today_sl_count():
    day = load_performance().get("days", {}).get(today_key(), {})
    return int(day.get("sl", 0))


def get_today_direction_stop_count(direction):
    day = load_performance().get("days", {}).get(today_key(), {})
    return int(day.get("direction_stops", {}).get(direction, 0))


def has_coin_recent_stop(symbol):
    performance = load_performance()
    days = performance.get("days", {})
    day = days.get(today_key(), {})
    coin = day.get("coins", {}).get(symbol, {})
    if int(coin.get("sl", 0)) <= 0:
        return False

    # Gün içi basit blok. Saat bazlı kayıt tutulmadığı için aynı gün bloklanır.
    return True


def risk_mode_active():
    return get_today_sl_count() >= RISK_MODE_STOP_COUNT


def get_max_open_signals():
    return MAX_OPEN_SIGNALS_RISK if risk_mode_active() else MAX_OPEN_SIGNALS_NORMAL


def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"}
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
        ohlcv = exchange.fetch_ohlcv(to_okx_symbol(symbol), timeframe=timeframe, limit=limit)
        if not ohlcv or len(ohlcv) < min_len:
            return None
        return pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
    except Exception as e:
        print(symbol, timeframe, "veri hatası:", e)
        return None


def fetch_candles_since(exchange, symbol, timeframe, since_seconds, limit=150):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            since=max(0, int(since_seconds)) * 1000,
            limit=limit
        )
        return [
            {
                "time": int(item[0] / 1000),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4])
            }
            for item in ohlcv
        ]
    except Exception as e:
        print(symbol, "takip mum hatası:", e)
        return []


def get_current_price(exchange, symbol):
    try:
        ticker = exchange.fetch_ticker(to_okx_symbol(symbol))
        price = ticker.get("last")
        return float(price) if price is not None else None
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


def is_duplicate(signal, watch=False):
    last_signals = load_last_signals()
    prefix = "WATCH" if watch else "TRADE"
    key = f"{prefix}_{signal['symbol']}_{signal['direction']}"
    last_time = int(last_signals.get(key, 0))
    wait = WATCH_DUPLICATE_BLOCK_SECONDS if watch else DUPLICATE_BLOCK_SECONDS
    return now_ts() - last_time < wait


def mark_sent(signal, watch=False):
    last_signals = load_last_signals()
    prefix = "WATCH" if watch else "TRADE"
    key = f"{prefix}_{signal['symbol']}_{signal['direction']}"
    last_signals[key] = now_ts()
    save_last_signals(last_signals)


def has_open_same_symbol(symbol):
    for signal in load_open_signals().values():
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
                    f"Giriş: {format_price(entry)}"
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
                    send_telegram(f"✅ TP2 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nTP2: {format_price(tp2)}")
                    signal["tp2_hit"] = True
                    tp2_hit = True
                    update_performance(symbol, "TP2")

                if tp1_hit and not tp3_hit and high >= tp3:
                    send_telegram(f"🏁 TP3 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nTP3: {format_price(tp3)}")
                    update_performance(symbol, "TP3")
                    continue

                if tp1_hit and low <= entry:
                    send_telegram(f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}")
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
                    send_telegram(f"✅ TP2 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nTP2: {format_price(tp2)}")
                    signal["tp2_hit"] = True
                    tp2_hit = True
                    update_performance(symbol, "TP2")

                if tp1_hit and not tp3_hit and low <= tp3:
                    send_telegram(f"🏁 TP3 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nTP3: {format_price(tp3)}")
                    update_performance(symbol, "TP3")
                    continue

                if tp1_hit and high >= entry:
                    send_telegram(f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}")
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

    send_telegram("\n".join(lines))
    performance["last_open_summary"] = now_ts()
    save_performance(performance)


def build_watch_message(signal):
    icon = "🟢" if signal["direction"] == "LONG" else "🔴"
    return f"""
🟡 DESTEK DİRENÇ TAKİP RADARI - İŞLEM AÇMA

{icon} {signal["direction"]}
Coin: {signal["symbol"]}

Bu mesaj işlem sinyali değildir.
Coin destek/direnç sisteminde izlemeye değer görünüyor.

Giriş adayı: {format_price(signal["entry"])}
Destek: {format_price(signal["support"])}
Direnç: {format_price(signal["resistance"])}
Skor: %{signal["score"]}
Hacim: {signal["volume_ratio"]}x
RSI: {signal["rsi_15m"]}
ADX: {signal["adx_15m"]}

A kalite giriş sinyali gelmeden işlem açma.
"""


def build_daily_report():
    performance = load_performance()
    day = performance.get("days", {}).get(today_key(), {})
    opened = int(day.get("opened", 0))
    watch = int(day.get("watch", 0))
    tp1 = int(day.get("tp1", 0))
    tp2 = int(day.get("tp2", 0))
    tp3 = int(day.get("tp3", 0))
    sl = int(day.get("sl", 0))
    be = int(day.get("be", 0))
    expired = int(day.get("expired", 0))
    long_count = int(day.get("long", 0))
    short_count = int(day.get("short", 0))
    open_count = len(load_open_signals())

    closed = tp1 + sl
    success = round((tp1 / closed) * 100, 2) if closed > 0 else 0

    return f"""
📊 GÜNLÜK PERFORMANS RAPORU

📅 Tarih: {today_key()}

📈 Açılan A Kalite Sinyal: {opened}
🟡 Takip Radarı: {watch}
🟢 LONG: {long_count}
🔴 SHORT: {short_count}

✅ TP1 Gelen: {tp1}
✅ TP2 Gelen: {tp2}
✅ TP3 Gelen: {tp3}
🟡 Girişten Kapanan: {be}
❌ Stop Olan: {sl}
⏳ Süresi Dolan: {expired}
📌 Açık Sinyal: {open_count}

📊 TP1 Başarı Oranı: %{success}

📌 Not:
Takip radarları işlem sinyali sayılmaz.
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
    key = f"{signal['symbol']}_{signal['direction']}_SR"

    open_signals[key] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "source": signal.get("source", "SR"),
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

    risk_mode = risk_mode_active()
    if risk_mode:
        print("Riskli piyasa modu aktif. Sistem durmaz, daha seçici çalışır.")
        if should_send_status():
            send_telegram(
                f"🟡 {BOT_NAME} çalıştı.\n\n"
                f"Riskli Piyasa Modu aktif.\n"
                f"Sistem durmadı; sadece daha seçici çalışıyor.\n"
                f"Bugünkü stop: {get_today_sl_count()}"
            )
            mark_status_sent()

    scan_coins = get_scan_coins(exchange)
    open_signals = load_open_signals()

    print("Taranan coin:", len(scan_coins))
    print("Açık sinyal:", len(open_signals))
    print("Risk modu:", risk_mode)

    trade_candidates = []
    watch_candidates = []

    max_open = get_max_open_signals()

    for symbol in scan_coins:
        try:
            if len(load_open_signals()) >= max_open:
                print("Maksimum açık sinyal sınırı doldu.")
                break

            if has_open_same_symbol(symbol):
                print(symbol, "zaten açık sinyal var, atlandı.")
                continue

            if has_coin_recent_stop(symbol):
                print(symbol, "bugün stop olduğu için atlandı.")
                continue

            current_price = get_current_price(exchange, symbol)

            df5m = fetch_df(exchange, symbol, RADAR_TIMEFRAME, RADAR_LIMIT, min_len=35)
            df15m = fetch_df(exchange, symbol, ENTRY_TIMEFRAME, ENTRY_LIMIT, min_len=220)
            df1h = fetch_df(exchange, symbol, CONFIRM_TIMEFRAME, CONFIRM_LIMIT, min_len=220)
            df4h = fetch_df(exchange, symbol, TREND_TIMEFRAME, TREND_LIMIT, min_len=220)

            signal = analyze_futures_setup(symbol, df5m, df15m, df1h, df4h, current_price)

            if signal is None:
                time.sleep(0.10)
                continue

            if signal["direction"] == "LONG" and not ALLOW_LONG:
                continue

            if signal["direction"] == "SHORT" and not ALLOW_SHORT:
                continue

            valid, reason = is_entry_still_valid(signal, current_price)

            if not valid:
                print(symbol, "giriş elendi ->", reason)
                continue

            if signal["signal_class"] == "TRADE":
                if risk_mode and not RISK_MODE_ALLOW_RADAR_TRADE and signal.get("source") != "SR":
                    print(symbol, "risk modunda radar trade kapalı")
                    continue

                if not is_duplicate(signal, watch=False):
                    trade_candidates.append(signal)
                    print(symbol, "A kalite aday:", signal["direction"], signal["score"])
            else:
                if not is_duplicate(signal, watch=True):
                    watch_candidates.append(signal)
                    print(symbol, "takip radarı:", signal["direction"], signal["score"])

            time.sleep(0.10)

        except Exception as e:
            print(symbol, "analiz hatası:", e)

    trade_candidates = sorted(trade_candidates, key=lambda s: s["score"], reverse=True)
    watch_candidates = sorted(watch_candidates, key=lambda s: s["score"], reverse=True)

    max_trade = RISK_MODE_MAX_TRADE_SIGNALS if risk_mode else MAX_TRADE_SIGNALS_PER_RUN
    max_watch = RISK_MODE_MAX_WATCH_ALERTS if risk_mode else MAX_WATCH_ALERTS_PER_RUN

    selected_trade = trade_candidates[:max_trade]
    selected_watch = watch_candidates[:max_watch]

    if selected_trade:
        send_telegram(
            f"✅ {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {len(scan_coins)}\n"
            f"A kalite aday: {len(trade_candidates)}\n"
            f"Gönderilen işlem sinyali: {len(selected_trade)}\n"
            f"Riskli Piyasa Modu: {'AKTİF' if risk_mode else 'Kapalı'}\n"
            f"Sistem: Trend + Destek/Direnç + Hacim + R/R + Kaldıraç."
        )

        for signal in selected_trade:
            current_price = get_current_price(exchange, signal["symbol"])
            valid, reason = is_entry_still_valid(signal, current_price)
            if not valid:
                print(signal["symbol"], "son kontrol elendi:", reason)
                continue

            if send_telegram(signal["message"] + f"\n💰 Güncel Fiyat: {format_price(current_price)}\n📌 Son Kontrol: Girişe yakın ✅"):
                save_open_signal(signal)
                mark_sent(signal, watch=False)
                update_performance(signal["symbol"], "OPENED", direction=signal["direction"], source="SR")
            time.sleep(1)

    if selected_watch:
        send_telegram(
            f"🟡 {BOT_NAME} takip radarı çalıştı.\n"
            f"Takip uyarısı: {len(selected_watch)}\n"
            f"Bu mesajlar işlem sinyali değildir."
        )

        for signal in selected_watch:
            if send_telegram(build_watch_message(signal)):
                mark_sent(signal, watch=True)
                update_performance(signal["symbol"], "WATCH", direction=signal["direction"], source="SR")
            time.sleep(1)

    if not selected_trade and not selected_watch:
        print("Uygun sinyal yok.")
        if should_send_status():
            send_telegram(
                f"📡 {BOT_NAME} çalıştı.\n\n"
                f"Taranan coin: {len(scan_coins)}\n"
                f"Uygun destek/direnç giriş sinyali yok.\n"
                f"Sistem durmadı, taramaya devam ediyor."
            )
            mark_status_sent()

    maybe_send_daily_report()
    print(BOT_NAME, "tamamlandı.")


if __name__ == "__main__":
    main()
