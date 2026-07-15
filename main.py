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
    AUTO_TOP_VOLUME_SCAN,
    MAX_SCAN_COINS,
    MIN_24H_QUOTE_VOLUME,
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
    SEND_NO_SIGNAL_MESSAGE,
    RADAR_ENABLED,
    RADAR_TIMEFRAME,
    RADAR_LIMIT,
    RADAR_MAX_ALERTS,
    RADAR_MIN_MOVE_PERCENT,
    RADAR_MIN_VOLUME_RATIO,
    RADAR_MIN_15M_MOVE_PERCENT,
    RADAR_MIN_RISK_PERCENT,
    RADAR_MAX_RISK_PERCENT,
    RADAR_COOLDOWN_SECONDS
)
from strategy import analyze_signal, format_price


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"
RADAR_FILE = "last_signals.json"

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

    long_open = 0
    short_open = 0

    for signal in open_signals.values():
        if signal.get("direction") == "LONG":
            long_open += 1
        elif signal.get("direction") == "SHORT":
            short_open += 1

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
🟢 Açık LONG: {long_open}
🔴 Açık SHORT: {short_open}

📊 TP1 Başarı Oranı: %{success_rate}

🏆 En İyi Coin: {best_coin}
⚠️ En Zayıf Coin: {worst_coin}

📌 Not:
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


def okx_symbol_to_bot_symbol(okx_symbol):
    # Örnek: BTC/USDT:USDT -> BTCUSDT
    base = okx_symbol.split("/")[0]
    return f"{base}USDT".upper()


def safe_quote_volume(ticker):
    """
    OKX/CCXT bazı paritelerde quoteVolume bilgisini farklı alanlarda döndürebilir.
    Bu fonksiyon hacmi mümkün olduğunca güvenli okur.
    """
    try:
        for key in ["quoteVolume", "quote_volume"]:
            value = ticker.get(key)
            if value is not None:
                return float(value)

        info = ticker.get("info", {})

        # OKX çoğu zaman 24h hacim bilgilerini info içinde verir.
        for key in ["volCcy24h", "volUsd24h", "vol24h"]:
            value = info.get(key)
            if value is not None:
                return float(value)

    except Exception:
        pass

    return 0.0


def get_scan_coins(exchange):
    """
    AUTO_TOP_VOLUME_SCAN True ise:
    OKX'teki aktif USDT swap pariteleri içinden hacmi en yüksek ilk 80 coin taranır.
    Sorun olursa config.py içindeki COINS listesine geri döner.
    """
    if not AUTO_TOP_VOLUME_SCAN:
        print("Top volume tarama kapalı. Sabit COINS listesi kullanılacak.")
        return COINS

    try:
        markets = exchange.load_markets()
        usdt_swap_symbols = []

        stable_bases = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD"}

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

                if not base or base in stable_bases:
                    continue

                usdt_swap_symbols.append(okx_symbol)

            except Exception as market_error:
                print("Market filtreleme hatası:", market_error)

        if not usdt_swap_symbols:
            print("OKX USDT swap paritesi bulunamadı. Sabit COINS listesi kullanılacak.")
            return COINS

        tickers = exchange.fetch_tickers(usdt_swap_symbols)

        volume_rows = []

        for okx_symbol in usdt_swap_symbols:
            try:
                ticker = tickers.get(okx_symbol, {})
                quote_volume = safe_quote_volume(ticker)
                coin = okx_symbol_to_bot_symbol(okx_symbol)

                if quote_volume < MIN_24H_QUOTE_VOLUME:
                    continue

                volume_rows.append((coin, quote_volume))

            except Exception as ticker_error:
                print(okx_symbol, "hacim okuma hatası:", ticker_error)

        if not volume_rows:
            print("Hacim filtresinden geçen coin yok. Sabit COINS listesi kullanılacak.")
            return COINS

        volume_rows = sorted(volume_rows, key=lambda x: x[1], reverse=True)

        # Önce config.py içindeki ana coinleri, listedeyse koru.
        # Sonra hacme göre diğer coinleri ekle.
        all_volume_coins = [coin for coin, _ in volume_rows]
        priority_coins = [coin for coin in COINS if coin in all_volume_coins]
        other_coins = [coin for coin in all_volume_coins if coin not in priority_coins]

        scan_coins = (priority_coins + other_coins)[:MAX_SCAN_COINS]

        print("OKX hacimli USDT swap tarama aktif.")
        print("Hacim filtresinden geçen coin:", len(volume_rows))
        print("Taranacak coin:", len(scan_coins))
        print("İlk 10 coin:", scan_coins[:10])

        return scan_coins

    except Exception as e:
        print("Top volume coin listesi alınamadı:", e)
        print("Sabit COINS listesi kullanılacak.")
        return COINS


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


def fetch_df_loose(exchange, symbol, timeframe, limit, min_len=30):
    try:
        okx_symbol = to_okx_symbol(symbol)

        ohlcv = exchange.fetch_ohlcv(
            okx_symbol,
            timeframe=timeframe,
            limit=limit
        )

        if not ohlcv or len(ohlcv) < min_len:
            return None

        df = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )

        return df
    except Exception as e:
        print(symbol, timeframe, "radar veri hatası:", e)
        return None


def load_radar_state():
    return load_json_file(RADAR_FILE)


def save_radar_state(data):
    return save_json_file(RADAR_FILE, data)


def is_radar_duplicate(symbol, direction):
    try:
        state = load_radar_state()
        key = f"RADAR_{symbol}_{direction}"
        last_time = int(state.get(key, 0))
        now = int(time.time())

        if now - last_time < RADAR_COOLDOWN_SECONDS:
            print(key, "radar tekrar engellendi.")
            return True

        return False
    except Exception as e:
        print("Radar tekrar kontrol hatası:", e)
        return False


def mark_radar_sent(symbol, direction):
    state = load_radar_state()
    key = f"RADAR_{symbol}_{direction}"
    state[key] = int(time.time())
    save_radar_state(state)


def simple_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def build_radar_message(symbol, direction, entry, tp1, tp2, tp3, sl, move_percent, volume_ratio, risk_percent, trend_text, score):
    icon = "🟢" if direction == "LONG" else "🔴"

    return f"""
⚡ ANLIK HAREKET RADARI - HIZLI GİRİŞ ADAYI

{icon} {direction}
🟡 Coin: {symbol}

🔥 Giriş: {format_price(entry)}
🎯 TP1: {format_price(tp1)}
🎯 TP2: {format_price(tp2)}
🎯 TP3: {format_price(tp3)}
🔴 SL: {format_price(sl)}

🚀 5M Hareket: %{round(move_percent, 2)}
📊 Hacim: {round(volume_ratio, 2)}x
🛡️ Stop Mesafesi: %{round(risk_percent, 2)}
🔥 Radar Skoru: %{min(int(score), 100)}

📈 Anlık Durum:
• 5M güçlü hareket yakalandı
• Hacim artışı var
• {trend_text}

📌 Hızlı İşlem Kuralı:
• Fiyat girişe çok yakınsa değerlendir.
• Mum çok uzadıysa işleme girme.
• TP1'e yaklaşmışsa girme.
• Stop mutlaka girilmeli.
• Marjin: Isolated.
• Kaldıraç: 2x - 3x.
• Aynı coinde ikinci işlem açma.

⚠️ Bu radar sinyali hızlıdır ve normal sinyale göre daha risklidir. Grafikte kontrol etmeden işleme girme.
"""


def analyze_momentum_radar(symbol, df5m, df15m, df1h, current_price):
    try:
        if not RADAR_ENABLED:
            return None

        if df5m is None or df15m is None or len(df5m) < 35 or len(df15m) < 20:
            return None

        df5m = df5m.copy()
        df15m = df15m.copy()

        # Kapanmış son mumlar
        last5 = df5m.iloc[-2]
        prev5 = df5m.iloc[-3]
        last15 = df15m.iloc[-2]
        prev15 = df15m.iloc[-3]

        if current_price is None:
            current_price = float(last5["close"])

        open5 = float(last5["open"])
        close5 = float(last5["close"])
        high5 = float(last5["high"])
        low5 = float(last5["low"])

        if open5 <= 0 or close5 <= 0:
            return None

        move_percent = ((close5 - open5) / open5) * 100
        move15_percent = ((float(last15["close"]) - float(prev15["close"])) / float(prev15["close"])) * 100

        volume_avg = float(df5m["volume"].iloc[-22:-2].mean())
        if volume_avg <= 0:
            return None

        volume_ratio = float(last5["volume"]) / volume_avg

        if abs(move_percent) < RADAR_MIN_MOVE_PERCENT:
            return None

        if volume_ratio < RADAR_MIN_VOLUME_RATIO:
            return None

        direction = None

        if move_percent > 0 and move15_percent >= RADAR_MIN_15M_MOVE_PERCENT:
            direction = "LONG"

        if move_percent < 0 and move15_percent <= -RADAR_MIN_15M_MOVE_PERCENT:
            direction = "SHORT"

        if direction is None:
            return None

        # 1H yön çok tersse radar iptal et; nötr ise izin ver.
        trend_text = "1H yön nötr, hareket radarı aktif"

        if df1h is not None and len(df1h) >= 60:
            df1h = df1h.copy()
            df1h["ema20"] = simple_ema(df1h["close"], 20)
            df1h["ema50"] = simple_ema(df1h["close"], 50)
            h1 = df1h.iloc[-2]

            if float(h1["ema20"]) > float(h1["ema50"]):
                h1_direction = "LONG"
            elif float(h1["ema20"]) < float(h1["ema50"]):
                h1_direction = "SHORT"
            else:
                h1_direction = "NEUTRAL"

            if direction == "LONG" and h1_direction == "SHORT":
                print(symbol, "radar LONG elendi -> 1H ters")
                return None

            if direction == "SHORT" and h1_direction == "LONG":
                print(symbol, "radar SHORT elendi -> 1H ters")
                return None

            trend_text = f"1H yön: {h1_direction}"

        # Basit ATR benzeri 5M ortalama hareket
        ranges = (df5m["high"] - df5m["low"]).iloc[-16:-2]
        avg_range = float(ranges.mean())

        if avg_range <= 0:
            avg_range = abs(close5 - open5)

        entry = float(current_price)

        if direction == "LONG":
            swing_sl = min(float(prev5["low"]), low5) - avg_range * 0.20
            sl = min(swing_sl, entry - avg_range * 1.2)
            risk = entry - sl

            if risk <= 0:
                return None

            tp1 = entry + risk * 1.00
            tp2 = entry + risk * 1.60
            tp3 = entry + risk * 2.30

        else:
            swing_sl = max(float(prev5["high"]), high5) + avg_range * 0.20
            sl = max(swing_sl, entry + avg_range * 1.2)
            risk = sl - entry

            if risk <= 0:
                return None

            tp1 = entry - risk * 1.00
            tp2 = entry - risk * 1.60
            tp3 = entry - risk * 2.30

            if tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
                return None

        risk_percent = (risk / entry) * 100

        if risk_percent < RADAR_MIN_RISK_PERCENT:
            return None

        if risk_percent > RADAR_MAX_RISK_PERCENT:
            return None

        score = 60
        score += min(abs(move_percent) * 18, 20)
        score += min(volume_ratio * 8, 20)

        if "LONG" in trend_text and direction == "LONG":
            score += 10

        if "SHORT" in trend_text and direction == "SHORT":
            score += 10

        message = build_radar_message(
            symbol=symbol,
            direction=direction,
            entry=entry,
            tp1=tp1,
            tp2=tp2,
            tp3=tp3,
            sl=sl,
            move_percent=move_percent,
            volume_ratio=volume_ratio,
            risk_percent=risk_percent,
            trend_text=trend_text,
            score=score
        )

        return {
            "symbol": symbol,
            "direction": direction,
            "entry": round(entry, 10),
            "tp1": round(tp1, 10),
            "tp2": round(tp2, 10),
            "tp3": round(tp3, 10),
            "sl": round(sl, 10),
            "score": int(score),
            "risk_percent": round(risk_percent, 3),
            "source": "RADAR",
            "message": message
        }

    except Exception as e:
        print(symbol, "radar analiz hatası:", e)
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
        direction = signal["direction"]
        entry = float(signal["entry"])
        tp1 = float(signal["tp1"])
        sl = float(signal["sl"])

        if current_price is None or entry <= 0:
            return False

        entry_distance_percent = abs((current_price - entry) / entry) * 100

        if entry_distance_percent > MAX_ENTRY_DISTANCE_PERCENT:
            print(signal["symbol"], "elendi -> geç giriş:", round(entry_distance_percent, 2))
            return False

        if direction == "LONG":
            tp1_distance = tp1 - entry
            current_progress = current_price - entry

            if tp1_distance <= 0:
                return False

            progress_percent = (current_progress / tp1_distance) * 100

            if progress_percent >= MAX_TP1_PROGRESS_PERCENT:
                print(signal["symbol"], "elendi -> LONG TP1'e yaklaşmış:", round(progress_percent, 2))
                return False

            if current_price >= tp1:
                print(signal["symbol"], "elendi -> LONG TP1 zaten gelmiş")
                return False

            if current_price <= sl:
                print(signal["symbol"], "elendi -> LONG SL tarafında")
                return False

        elif direction == "SHORT":
            tp1_distance = entry - tp1
            current_progress = entry - current_price

            if tp1_distance <= 0:
                return False

            progress_percent = (current_progress / tp1_distance) * 100

            if progress_percent >= MAX_TP1_PROGRESS_PERCENT:
                print(signal["symbol"], "elendi -> SHORT TP1'e yaklaşmış:", round(progress_percent, 2))
                return False

            if current_price <= tp1:
                print(signal["symbol"], "elendi -> SHORT TP1 zaten gelmiş")
                return False

            if current_price >= sl:
                print(signal["symbol"], "elendi -> SHORT SL tarafında")
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
            direction = signal.get("direction", "LONG")
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

            if direction == "LONG":
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

            elif direction == "SHORT":
                if not tp1_hit:
                    if high >= sl:
                        send_telegram(
                            f"❌ STOP OLDU\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"Giriş: {format_price(entry)}\n"
                            f"SL: {format_price(sl)}\n"
                            f"En yüksek: {format_price(high)}\n"
                            f"Güncel: {format_price(current_price)}"
                        )

                        update_performance(symbol, "SL")
                        continue

                    if low <= tp1:
                        send_telegram(
                            f"✅ TP1 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"Giriş: {format_price(entry)}\n"
                            f"TP1: {format_price(tp1)}\n"
                            f"En düşük: {format_price(low)}\n"
                            f"Güncel: {format_price(current_price)}\n\n"
                            f"Öneri: %50 kâr al, kalan işlem için SL giriş fiyatına çekildi."
                        )

                        update_performance(symbol, "TP1")
                        signal["tp1_hit"] = True
                        signal["breakeven_sl"] = entry
                        tp1_hit = True

                if tp1_hit:
                    if tp3 and not tp3_hit and low <= tp3:
                        send_telegram(
                            f"🏁 TP3 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"Giriş: {format_price(entry)}\n"
                            f"TP3: {format_price(tp3)}\n"
                            f"En düşük: {format_price(low)}\n"
                            f"Güncel: {format_price(current_price)}"
                        )

                        update_performance(symbol, "TP3")
                        continue

                    if tp2 and not tp2_hit and low <= tp2:
                        send_telegram(
                            f"✅ TP2 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"Giriş: {format_price(entry)}\n"
                            f"TP2: {format_price(tp2)}\n"
                            f"En düşük: {format_price(low)}\n"
                            f"Güncel: {format_price(current_price)}"
                        )

                        update_performance(symbol, "TP2")
                        signal["tp2_hit"] = True
                        tp2_hit = True

                    if high >= entry:
                        send_telegram(
                            f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"Giriş: {format_price(entry)}\n"
                            f"En yüksek: {format_price(high)}\n"
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
            direction = signal.get("direction", "LONG")
            entry = float(signal["entry"])
            tp1 = float(signal["tp1"])
            sl = float(signal["sl"])
            current_price = get_current_price(exchange, symbol)

            if current_price is None:
                continue

            tp1_hit = bool(signal.get("tp1_hit", False))

            if direction == "LONG":
                icon = "🟢"
                profit_percent = ((current_price - entry) / entry) * 100
            else:
                icon = "🔴"
                profit_percent = ((entry - current_price) / entry) * 100

            lines.append(
                f"{icon} {symbol} {direction}\n"
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
    print("Sade Premium V1 ANLIK RADAR bot başladı.")

    exchange = get_exchange()
    scan_coins = get_scan_coins(exchange)

    print("Coin sayısı:", len(scan_coins))
    print("Top volume tarama:", AUTO_TOP_VOLUME_SCAN)
    print("Maksimum tarama:", MAX_SCAN_COINS)
    print("LONG açık:", ALLOW_LONG_SIGNALS)
    print("SHORT açık:", ALLOW_SHORT_SIGNALS)
    print("Bot emir açmaz, sadece Telegram sinyali gönderir.")
    print("Anlık radar aktif:", RADAR_ENABLED)
    print("Radar zaman dilimi:", RADAR_TIMEFRAME)

    check_open_signals(exchange)
    maybe_send_open_summary(exchange)

    candidates = []

    for symbol in scan_coins:
        try:
            print(symbol, "analiz ediliyor...")

            df15m = fetch_df(exchange, symbol, ENTRY_TIMEFRAME, ENTRY_LIMIT)
            df1h = fetch_df(exchange, symbol, CONFIRM_TIMEFRAME, CONFIRM_LIMIT)
            df4h = fetch_df(exchange, symbol, TREND_TIMEFRAME, TREND_LIMIT)
            df5m = fetch_df_loose(exchange, symbol, RADAR_TIMEFRAME, RADAR_LIMIT, min_len=35)

            current_price = get_current_price(exchange, symbol)

            # 1) Normal onaylı sinyal
            if df15m is not None and df1h is not None and df4h is not None:
                signal = analyze_signal(symbol, df15m, df1h, df4h)

                if signal is not None:
                    if signal["direction"] == "LONG" and not ALLOW_LONG_SIGNALS:
                        print(symbol, "LONG kapalı olduğu için elendi.")
                    elif signal["direction"] == "SHORT" and not ALLOW_SHORT_SIGNALS:
                        print(symbol, "SHORT kapalı olduğu için elendi.")
                    elif is_entry_still_valid(signal, current_price):
                        signal["current_price"] = current_price
                        signal["source"] = "NORMAL"
                        candidates.append(signal)
                    else:
                        print(symbol, "normal sinyal geç giriş nedeniyle gönderilmedi.")

            # 2) Anlık hareket radarı
            radar_signal = analyze_momentum_radar(symbol, df5m, df15m, df1h, current_price)

            if radar_signal is not None:
                if radar_signal["direction"] == "LONG" and not ALLOW_LONG_SIGNALS:
                    print(symbol, "radar LONG kapalı olduğu için elendi.")
                elif radar_signal["direction"] == "SHORT" and not ALLOW_SHORT_SIGNALS:
                    print(symbol, "radar SHORT kapalı olduğu için elendi.")
                elif not is_radar_duplicate(symbol, radar_signal["direction"]):
                    radar_signal["current_price"] = current_price
                    candidates.append(radar_signal)
                    print(symbol, "RADAR SİNYALİ:", radar_signal["direction"], radar_signal["score"])

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
        long_count = len([s for s in strong_signals if s["direction"] == "LONG"])
        short_count = len([s for s in strong_signals if s["direction"] == "SHORT"])
        radar_count = len([s for s in strong_signals if s.get("source") == "RADAR"])
        normal_count = len([s for s in strong_signals if s.get("source") != "RADAR"])

        send_telegram(
            f"✅ Sade Premium V1 ANLIK RADAR bot çalıştı.\n"
            f"Taranan coin: {len(scan_coins)}\n"
            f"Tarama: Hacimli ilk {MAX_SCAN_COINS} USDT swap coin\n"
            f"Uygun aday: {len(candidates)}\n"
            f"Gönderilen sinyal: {len(strong_signals)}\n"
            f"LONG: {long_count} | SHORT: {short_count}\n"
            f"Normal: {normal_count} | Anlık Radar: {radar_count}\n"
            f"Sistem: 4H/1H onay + 15M giriş + 5M anlık radar.\n"
            f"SHORT kontrollü şekilde tekrar açıldı.\n"
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
                    "source": signal.get("source", "NORMAL"),
                    "opened_at": int(time.time()),
                    "last_checked_at": int(time.time()),
                    "tp1_hit": False,
                    "tp2_hit": False,
                    "tp3_hit": False,
                    "breakeven_sl": None
                }

                if signal.get("source") == "RADAR":
                    mark_radar_sent(signal["symbol"], signal["direction"])

                update_performance(signal["symbol"], "OPENED")
                time.sleep(1)

            except Exception as e:
                print(signal.get("symbol", "-"), "sinyal gönderim/kayıt hatası:", e)

        save_open_signals(open_signals)

    else:
        print("Uygun sinyal yok.")

        if SEND_NO_SIGNAL_MESSAGE:
            send_telegram(
                f"📡 Sade Premium V1 ANLIK RADAR bot çalıştı.\n\n"
                f"Taranan coin: {len(scan_coins)}\n"
                f"Tarama: Hacimli ilk {MAX_SCAN_COINS} USDT swap coin\n"
                f"Şu an uygun LONG/SHORT sinyal yok.\n"
                f"Sistem: 4H trend + 1H onay + 15M giriş + 5M anlık radar.\n"
                f"SHORT kontrollü açık. Radar aktif."
            )

    maybe_send_daily_report()
    print("Sade Premium V1 bot tamamlandı.")


if __name__ == "__main__":
    main()
