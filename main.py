# main.py
# Premium MTF Futures Bot - Akilli Takip v2
# GitHub Actions icin Telegram sinyal botu.
# Emir acmaz. Sinyal gonderir, TP/SL takip eder, istatistik raporu yollar.

import os
import time
import json
import requests
import pandas as pd
import ccxt
from datetime import datetime, timezone, timedelta

from config import (
    BOT_NAME,
    SYSTEM_NOTE,
    AUTO_TOP_VOLUME_SCAN,
    MAX_SCAN_COINS,
    MIN_24H_QUOTE_VOLUME,
    PRIORITY_COINS,
    ALLOW_LONG,
    ALLOW_SHORT,
    MAX_TRADE_SIGNALS_PER_RUN,
    MAX_RADAR_ALERTS_PER_RUN,
    MAX_OPEN_SIGNALS,
    RISK_MODE_STOP_COUNT,
    RISK_MODE_MAX_TRADE_SIGNALS,
    RISK_MODE_MAX_RADAR_ALERTS,
    RISK_MODE_ALLOW_RADAR_TRADE,
    RADAR_TIMEFRAME,
    ENTRY_TIMEFRAME,
    CONFIRM_TIMEFRAME,
    TREND_TIMEFRAME,
    TRACK_TIMEFRAME,
    RADAR_LIMIT,
    ENTRY_LIMIT,
    CONFIRM_LIMIT,
    TREND_LIMIT,
    TRACK_LIMIT,
    MAX_ENTRY_DISTANCE_PERCENT,
    MAX_TP1_PROGRESS_PERCENT,
    MARKET_GUARD_ENABLED,
    MARKET_REFERENCE_COINS,
    MARKET_LONG_MIN_OK_COUNT,
    MARKET_SHORT_MIN_OK_COUNT,
    MARKET_MAX_COUNTER_5M_MOVE_PERCENT,
    TRADE_DUPLICATE_BLOCK_SECONDS,
    RADAR_DUPLICATE_BLOCK_SECONDS,
    STOPPED_COIN_COOLDOWN_HOURS,
    MAX_OPEN_SIGNAL_HOURS,
    SEND_STATUS_EVERY_MINUTES,
    OPEN_SUMMARY_EVERY_MINUTES,
    DAILY_REPORT_HOUR,
    DAILY_REPORT_MINUTE,
)

from strategy import (
    analyze_mtf_trade,
    analyze_5m_radar,
    format_price,
)

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

OPEN_SIGNALS_FILE = "open_signals.json"
PERFORMANCE_FILE = "performance.json"
LAST_SIGNALS_FILE = "last_signals.json"

TR_TIMEZONE = timezone(timedelta(hours=3))

# Yeni takip ayarlari config.py istemeden main.py icinde tutuluyor.
# Stop olan sinyal 30 / 60 / 120 dakika kontrol edilir.
SL_AFTER_CHECKPOINT_MINUTES = [30, 60, 120]
SL_AFTER_MAX_TRACK_MINUTES = 120

# TP3 / BE / kapanan coin ayni gun cok hizli tekrar gelmesin.
# SL icin zaten STOPPED_COIN_COOLDOWN_HOURS var.
RECENT_CLOSED_COIN_COOLDOWN_SECONDS = 4 * 60 * 60


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik.")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message},
            timeout=20,
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
        "radar": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "expired": 0,
        "long": 0,
        "short": 0,
        "normal": 0,
        "radar_trade": 0,
        "coins": {},
        "direction_stops": {},
        "stop_times": {},
        "closed_times": {},
        "closed_results": {},
        "closed_history": [],
        "sl_after_tp1": 0,
        "sl_after_tp2": 0,
        "sl_after_no_return": 0,
    })

    # Eski performance.json dosyalarinda eksik olabilecek alanlari tamamla.
    day = performance["days"][today]
    day.setdefault("coins", {})
    day.setdefault("direction_stops", {})
    day.setdefault("stop_times", {})
    day.setdefault("closed_times", {})
    day.setdefault("closed_results", {})
    day.setdefault("closed_history", [])
    day.setdefault("sl_after_tp1", 0)
    day.setdefault("sl_after_tp2", 0)
    day.setdefault("sl_after_no_return", 0)
    performance.setdefault("sl_after_follow", {})
    return performance


def add_history(day, item):
    day.setdefault("closed_history", [])
    day["closed_history"].append(item)
    if len(day["closed_history"]) > 100:
        day["closed_history"] = day["closed_history"][-100:]


def update_performance(symbol, result, direction=None, source=None, entry=None, exit_price=None, score=None):
    performance = ensure_perf_day(load_performance())
    today = today_key()
    day = performance["days"][today]

    if result == "OPENED":
        day["opened"] += 1
        if direction == "LONG":
            day["long"] += 1
        elif direction == "SHORT":
            day["short"] += 1

        if source == "5M_RADAR":
            day["radar_trade"] += 1
        else:
            day["normal"] += 1

    elif result == "RADAR":
        day["radar"] += 1

    elif result in ["TP1", "TP2", "TP3", "SL", "BE", "EXPIRED"]:
        key = result.lower()
        day[key] = int(day.get(key, 0)) + 1

        if result == "SL" and direction in ["LONG", "SHORT"]:
            day.setdefault("direction_stops", {})
            day["direction_stops"][direction] = int(day["direction_stops"].get(direction, 0)) + 1
            day.setdefault("stop_times", {})
            day["stop_times"][symbol] = now_ts()

        if result in ["TP3", "BE", "EXPIRED"]:
            day.setdefault("closed_times", {})
            day.setdefault("closed_results", {})
            day["closed_times"][symbol] = now_ts()
            day["closed_results"][symbol] = result

        add_history(day, {
            "time": datetime.now(TR_TIMEZONE).strftime("%H:%M:%S"),
            "symbol": symbol,
            "direction": direction,
            "result": result,
            "entry": entry,
            "exit": exit_price,
            "source": source,
            "score": score,
        })

    day.setdefault("coins", {})
    day["coins"].setdefault(symbol, {
        "opened": 0,
        "radar": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "sl": 0,
        "be": 0,
        "expired": 0,
    })

    coin = day["coins"][symbol]
    if result == "OPENED":
        coin["opened"] += 1
    elif result == "RADAR":
        coin["radar"] += 1
    elif result in ["TP1", "TP2", "TP3", "SL", "BE", "EXPIRED"]:
        coin[result.lower()] = int(coin.get(result.lower(), 0)) + 1

    performance["last_update"] = now_ts()
    save_performance(performance)


def get_today_sl_count():
    day = load_performance().get("days", {}).get(today_key(), {})
    return int(day.get("sl", 0))


def risk_mode_active():
    return get_today_sl_count() >= RISK_MODE_STOP_COUNT


def has_recent_stop(symbol):
    day = load_performance().get("days", {}).get(today_key(), {})
    stop_time = int(day.get("stop_times", {}).get(symbol, 0))
    if stop_time <= 0:
        return False
    return now_ts() - stop_time < STOPPED_COIN_COOLDOWN_HOURS * 60 * 60


def has_recent_closed_signal(symbol):
    """TP3 / BE / expired sonrasi ayni coin cok hizli tekrar gelmesin."""
    day = load_performance().get("days", {}).get(today_key(), {})
    closed_time = int(day.get("closed_times", {}).get(symbol, 0))
    if closed_time <= 0:
        return False
    return now_ts() - closed_time < RECENT_CLOSED_COIN_COOLDOWN_SECONDS


def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
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
        return PRIORITY_COINS

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
            return PRIORITY_COINS

        rows = sorted(rows, key=lambda x: x[1], reverse=True)
        volume_coins = [coin for coin, _ in rows]
        priority = [coin for coin in PRIORITY_COINS if coin in volume_coins]
        others = [coin for coin in volume_coins if coin not in priority]
        scan_coins = (priority + others)[:MAX_SCAN_COINS]

        print("Hacimli coin sayısı:", len(rows))
        print("Taranacak coin:", len(scan_coins))
        print("İlk 10:", scan_coins[:10])
        return scan_coins

    except Exception as e:
        print("Top volume tarama hatası:", e)
        return PRIORITY_COINS


def fetch_df(exchange, symbol, timeframe, limit, min_len=30):
    try:
        ohlcv = exchange.fetch_ohlcv(to_okx_symbol(symbol), timeframe=timeframe, limit=limit)
        if not ohlcv or len(ohlcv) < min_len:
            return None
        return pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
    except Exception as e:
        print(symbol, timeframe, "veri hatası:", e)
        return None


def simple_ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def get_market_direction_status(exchange):
    if not MARKET_GUARD_ENABLED:
        return {"LONG": True, "SHORT": True, "reason": "Market koruma kapalı"}

    long_ok = 0
    short_ok = 0
    hard_red = 0
    hard_green = 0
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
                hard_red += 1
            if move5 >= MARKET_MAX_COUNTER_5M_MOVE_PERCENT:
                hard_green += 1

            details.append(
                f"{ref_symbol}: 15M {'EMA20 üstü' if close15 >= ema20 else 'EMA20 altı'}, "
                f"5M %{round(move5, 2)}"
            )
        except Exception as e:
            print(ref_symbol, "market koruma veri hatası:", e)

    allow_long = long_ok >= MARKET_LONG_MIN_OK_COUNT and hard_red < 2
    allow_short = short_ok >= MARKET_SHORT_MIN_OK_COUNT and hard_green < 2

    reason = (
        f"LONG uygun: {long_ok}/{len(MARKET_REFERENCE_COINS)} | "
        f"SHORT uygun: {short_ok}/{len(MARKET_REFERENCE_COINS)} | "
        f"Sert kırmızı: {hard_red} | Sert yeşil: {hard_green} | " + " | ".join(details)
    )
    print("Market koruma:", reason)
    return {"LONG": allow_long, "SHORT": allow_short, "reason": reason}


def fetch_candles_since(exchange, symbol, timeframe, since_seconds, limit=180):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            since=max(0, int(since_seconds)) * 1000,
            limit=limit,
        )
        return [
            {
                "time": int(item[0] / 1000),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
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


def is_duplicate(signal, radar=False):
    last_signals = load_last_signals()
    prefix = "RADAR" if radar else "TRADE"
    key = f"{prefix}_{signal['symbol']}_{signal['direction']}"
    last_time = int(last_signals.get(key, 0))
    wait = RADAR_DUPLICATE_BLOCK_SECONDS if radar else TRADE_DUPLICATE_BLOCK_SECONDS
    return now_ts() - last_time < wait


def mark_sent(signal, radar=False):
    last_signals = load_last_signals()
    prefix = "RADAR" if radar else "TRADE"
    key = f"{prefix}_{signal['symbol']}_{signal['direction']}"
    last_signals[key] = now_ts()
    save_last_signals(last_signals)


def has_open_same_symbol(symbol):
    for signal in load_open_signals().values():
        if signal.get("symbol") == symbol:
            return True
    return False


def count_open_signal_risk():
    """
    TP1 gelmemis acik sinyaller gercek riskli acik islem sayilir.
    TP1 gelmis sinyallerde kullanici kuralina gore %50 kar alinip SL girise cekildigi icin
    bunlar takipte/riski azaltilmis sinyal sayilir ve yeni firsatlari tamamen bloklamaz.
    """
    open_signals = load_open_signals()
    risky = 0
    reduced = 0

    for signal in open_signals.values():
        if bool(signal.get("tp1_hit", False)):
            reduced += 1
        else:
            risky += 1

    return risky, reduced, len(open_signals)


def risk_slot_available():
    risky, _, _ = count_open_signal_risk()
    return risky < MAX_OPEN_SIGNALS


def build_limit_watch_message(signal, current_price=None):
    """Limit doluysa guclu sinyali islem olarak kaydetmeden takip mesaji yapar."""
    try:
        direction_icon = "🟢" if signal.get("direction") == "LONG" else "🔴"
        price_line = ""
        if current_price is not None:
            price_line = f"\n💰 Güncel Fiyat: {format_price(current_price)}"

        return (
            f"⚠️ AÇIK SİNYAL SINIRI DOLU - TAKİP\n\n"
            f"{direction_icon} {signal.get('direction')}\n"
            f"🟡 Coin: {signal.get('symbol')}\n"
            f"⏱️ Kaynak: {signal.get('source')}\n\n"
            f"📌 Giriş: {format_price(float(signal.get('entry')))}\n"
            f"🎯 TP1: {format_price(float(signal.get('tp1')))}\n"
            f"🎯 TP2: {format_price(float(signal.get('tp2')))}\n"
            f"🎯 TP3: {format_price(float(signal.get('tp3')))}\n"
            f"🛑 SL: {format_price(float(signal.get('sl')))}\n\n"
            f"📊 Skor: %{signal.get('score')}\n"
            f"🛡️ Stop Mesafesi: %{signal.get('risk_percent')}"
            f"{price_line}\n\n"
            f"📌 Not: TP1 görmemiş açık işlem sınırı dolu olduğu için bu sinyal işlem olarak kaydedilmedi.\n"
            f"Grafikte sadece takip et. Yeni işlem açma konusunda acele etme.\n"
            f"TP1 görmüş eski işlemler riskten düştükçe bot normal sinyal göndermeye devam eder."
        )
    except Exception:
        return (
            f"⚠️ AÇIK SİNYAL SINIRI DOLU - TAKİP\n\n"
            f"Coin: {signal.get('symbol')}\n"
            f"Yön: {signal.get('direction')}\n"
            f"Skor: {signal.get('score')}\n\n"
            f"Bu sinyal işlem olarak kaydedilmedi, sadece takip uyarısıdır."
        )


def add_sl_after_follow(signal, exit_price):
    """Stop olan sinyali performance.json icinde takip listesine ekler."""
    try:
        performance = ensure_perf_day(load_performance())
        follow = performance.setdefault("sl_after_follow", {})
        stopped_at = now_ts()
        key = f"{signal.get('symbol')}_{signal.get('direction')}_{stopped_at}"
        follow[key] = {
            "symbol": signal.get("symbol"),
            "direction": signal.get("direction"),
            "source": signal.get("source"),
            "entry": signal.get("entry"),
            "tp1": signal.get("tp1"),
            "tp2": signal.get("tp2"),
            "tp3": signal.get("tp3"),
            "sl": signal.get("sl"),
            "score": signal.get("score"),
            "risk_percent": signal.get("risk_percent"),
            "stopped_at": stopped_at,
            "stop_exit": exit_price,
            "reported_checkpoints": [],
            "after_sl_tp1": False,
            "after_sl_tp2": False,
            "after_sl_tp3": False,
            "resolved": False,
        }
        save_performance(performance)
    except Exception as e:
        print("SL sonrası takip ekleme hatası:", e)


def close_signal_result(symbol, signal, result, exit_price):
    update_performance(
        symbol=symbol,
        result=result,
        direction=signal.get("direction"),
        source=signal.get("source"),
        entry=signal.get("entry"),
        exit_price=exit_price,
        score=signal.get("score"),
    )

    if result == "SL":
        add_sl_after_follow(signal, exit_price)


def register_partial_result(symbol, signal, result, exit_price):
    """TP1 / TP2 icin None hatasi olmadan performans kaydi."""
    update_performance(
        symbol=symbol,
        result=result,
        direction=signal.get("direction"),
        source=signal.get("source"),
        entry=signal.get("entry"),
        exit_price=exit_price,
        score=signal.get("score"),
    )


def check_sl_after_follow(exchange):
    """Stop sonrasi fiyat TP1/TP2'ye dondu mu kontrol eder."""
    performance = ensure_perf_day(load_performance())
    follow = performance.setdefault("sl_after_follow", {})
    if not follow:
        return

    changed = False
    for key, item in list(follow.items()):
        try:
            if item.get("resolved"):
                continue

            symbol = item["symbol"]
            direction = item["direction"]
            entry = float(item["entry"])
            tp1 = float(item["tp1"])
            tp2 = float(item["tp2"])
            tp3 = float(item["tp3"])
            sl = float(item["sl"])
            stopped_at = int(item.get("stopped_at", now_ts()))
            age_minutes = int((now_ts() - stopped_at) / 60)

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                since_seconds=stopped_at,
                limit=TRACK_LIMIT,
            )

            after_tp1 = False
            after_tp2 = False
            after_tp3 = False

            for candle in candles:
                high = float(candle["high"])
                low = float(candle["low"])

                if direction == "LONG":
                    if high >= tp1:
                        after_tp1 = True
                    if high >= tp2:
                        after_tp2 = True
                    if high >= tp3:
                        after_tp3 = True
                else:
                    if low <= tp1:
                        after_tp1 = True
                    if low <= tp2:
                        after_tp2 = True
                    if low <= tp3:
                        after_tp3 = True

            # Stop sonrasi TP1/TP2'ye donduyse tek rapor gonder ve kaydet.
            if after_tp1 and not item.get("after_sl_tp1"):
                item["after_sl_tp1"] = True
                item["after_sl_tp2"] = bool(after_tp2)
                item["after_sl_tp3"] = bool(after_tp3)
                item["resolved"] = True
                changed = True

                today = today_key()
                day = performance["days"].setdefault(today, {})
                day["sl_after_tp1"] = int(day.get("sl_after_tp1", 0)) + 1
                if after_tp2:
                    day["sl_after_tp2"] = int(day.get("sl_after_tp2", 0)) + 1

                level_text = "TP3" if after_tp3 else "TP2" if after_tp2 else "TP1"
                send_telegram(
                    f"📊 SL SONRASI TAKİP\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n"
                    f"SL: {format_price(sl)}\n"
                    f"Stop sonrası geçen süre: {age_minutes} dakika\n\n"
                    f"Sonuç: Stop sonrası fiyat {level_text} seviyesine döndü.\n"
                    f"Yorum: Bu sinyalde stop dar kalmış veya fitil stop olmuş olabilir."
                )
                continue

            # 30 / 60 / 120 dakika kontrol mesajlari.
            reported = item.setdefault("reported_checkpoints", [])
            for checkpoint in SL_AFTER_CHECKPOINT_MINUTES:
                if age_minutes >= checkpoint and checkpoint not in reported:
                    reported.append(checkpoint)
                    changed = True

                    if checkpoint >= SL_AFTER_MAX_TRACK_MINUTES:
                        item["resolved"] = True
                        today = today_key()
                        day = performance["days"].setdefault(today, {})
                        day["sl_after_no_return"] = int(day.get("sl_after_no_return", 0)) + 1

                    send_telegram(
                        f"📊 SL SONRASI TAKİP\n\n"
                        f"Coin: {symbol}\n"
                        f"Yön: {direction}\n"
                        f"Giriş: {format_price(entry)}\n"
                        f"SL: {format_price(sl)}\n"
                        f"Kontrol: {checkpoint}. dakika\n\n"
                        f"Sonuç: Stop sonrası henüz TP1 seviyesine dönüş yok.\n"
                        f"Yorum: Şimdilik stop doğru çalışmış görünüyor; takip devam ediyor."
                    )
                    break

        except Exception as e:
            print(key, "SL sonrası takip hatası:", e)

    # Eski / cozulmus takipleri cok buyumesin diye temizle.
    for key, item in list(follow.items()):
        try:
            stopped_at = int(item.get("stopped_at", 0))
            age = now_ts() - stopped_at
            if item.get("resolved") and age > (SL_AFTER_MAX_TRACK_MINUTES + 60) * 60:
                follow.pop(key, None)
                changed = True
        except Exception:
            pass

    if changed:
        performance["last_update"] = now_ts()
        save_performance(performance)


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

            # Eskiden TP3 gormus ama dosyada kalmis sinyal varsa tekrar mesaj atma.
            if bool(signal.get("tp3_hit", False)) or bool(signal.get("closed", False)):
                print(symbol, "zaten kapanmış görünüyor, takipten çıkarıldı.")
                continue

            if now_ts() - opened_at > max_age:
                send_telegram(
                    f"⏳ SİNYAL SÜRESİ DOLDU\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n\n"
                    f"{MAX_OPEN_SIGNAL_HOURS} saat içinde TP/SL netleşmediği için takipten çıkarıldı."
                )
                close_signal_result(symbol, signal, "EXPIRED", None)
                continue

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                since_seconds=max(opened_at, last_checked_at - 10 * 60),
                limit=TRACK_LIMIT,
            )
            if not candles:
                updated[key] = signal
                continue

            tp1_hit = bool(signal.get("tp1_hit", False))
            tp2_hit = bool(signal.get("tp2_hit", False))
            tp3_hit = bool(signal.get("tp3_hit", False))
            closed = False

            for candle in candles:
                high = float(candle["high"])
                low = float(candle["low"])
                close = float(candle["close"])

                if direction == "LONG":
                    if not tp1_hit:
                        if low <= sl and high >= tp1:
                            # Aynı 5M mumda hem TP1 hem SL varsa sıra bilinmez.
                            # Mum kapanışı giriş üstündeyse TP1, altında ise SL kabul edilir.
                            if close >= entry:
                                tp1_hit = True
                                signal["tp1_hit"] = True
                                send_telegram(
                                    f"✅ TP1 GELDİ\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: LONG 🟢\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"TP1: {format_price(tp1)}\n"
                                    f"Öneri: %50 kâr al, SL girişe çek."
                                )
                                register_partial_result(symbol, signal, "TP1", tp1)
                            else:
                                send_telegram(
                                    f"❌ STOP OLDU\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: LONG 🟢\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"SL: {format_price(sl)}\n"
                                    f"Güncel: {format_price(close)}"
                                )
                                close_signal_result(symbol, signal, "SL", close)
                                closed = True
                                break

                        elif low <= sl:
                            send_telegram(
                                f"❌ STOP OLDU\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: LONG 🟢\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"SL: {format_price(sl)}\n"
                                f"Güncel: {format_price(close)}"
                            )
                            close_signal_result(symbol, signal, "SL", close)
                            closed = True
                            break

                        elif high >= tp1:
                            tp1_hit = True
                            signal["tp1_hit"] = True
                            send_telegram(
                                f"✅ TP1 GELDİ\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: LONG 🟢\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"TP1: {format_price(tp1)}\n"
                                f"Öneri: %50 kâr al, SL girişe çek."
                            )
                            register_partial_result(symbol, signal, "TP1", tp1)

                    if tp1_hit and not tp2_hit and high >= tp2:
                        tp2_hit = True
                        signal["tp2_hit"] = True
                        send_telegram(
                            f"✅ TP2 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"TP2: {format_price(tp2)}"
                        )
                        register_partial_result(symbol, signal, "TP2", tp2)

                    if tp1_hit and not tp3_hit and high >= tp3:
                        tp3_hit = True
                        signal["tp3_hit"] = True
                        signal["closed"] = True
                        send_telegram(
                            f"🏁 TP3 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"TP3: {format_price(tp3)}\n"
                            f"Sinyal maksimum hedefe ulaştı."
                        )
                        close_signal_result(symbol, signal, "TP3", tp3)
                        closed = True
                        break

                    if tp1_hit and low <= entry:
                        signal["closed"] = True
                        send_telegram(
                            f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"Giriş: {format_price(entry)}"
                        )
                        close_signal_result(symbol, signal, "BE", entry)
                        closed = True
                        break

                else:
                    if not tp1_hit:
                        if high >= sl and low <= tp1:
                            if close <= entry:
                                tp1_hit = True
                                signal["tp1_hit"] = True
                                send_telegram(
                                    f"✅ TP1 GELDİ\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: SHORT 🔴\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"TP1: {format_price(tp1)}\n"
                                    f"Öneri: %50 kâr al, SL girişe çek."
                                )
                                register_partial_result(symbol, signal, "TP1", tp1)
                            else:
                                send_telegram(
                                    f"❌ STOP OLDU\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: SHORT 🔴\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"SL: {format_price(sl)}\n"
                                    f"Güncel: {format_price(close)}"
                                )
                                close_signal_result(symbol, signal, "SL", close)
                                closed = True
                                break

                        elif high >= sl:
                            send_telegram(
                                f"❌ STOP OLDU\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: SHORT 🔴\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"SL: {format_price(sl)}\n"
                                f"Güncel: {format_price(close)}"
                            )
                            close_signal_result(symbol, signal, "SL", close)
                            closed = True
                            break

                        elif low <= tp1:
                            tp1_hit = True
                            signal["tp1_hit"] = True
                            send_telegram(
                                f"✅ TP1 GELDİ\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: SHORT 🔴\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"TP1: {format_price(tp1)}\n"
                                f"Öneri: %50 kâr al, SL girişe çek."
                            )
                            register_partial_result(symbol, signal, "TP1", tp1)

                    if tp1_hit and not tp2_hit and low <= tp2:
                        tp2_hit = True
                        signal["tp2_hit"] = True
                        send_telegram(
                            f"✅ TP2 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"TP2: {format_price(tp2)}"
                        )
                        register_partial_result(symbol, signal, "TP2", tp2)

                    if tp1_hit and not tp3_hit and low <= tp3:
                        tp3_hit = True
                        signal["tp3_hit"] = True
                        signal["closed"] = True
                        send_telegram(
                            f"🏁 TP3 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"TP3: {format_price(tp3)}\n"
                            f"Sinyal maksimum hedefe ulaştı."
                        )
                        close_signal_result(symbol, signal, "TP3", tp3)
                        closed = True
                        break

                    if tp1_hit and high >= entry:
                        signal["closed"] = True
                        send_telegram(
                            f"🟡 KALAN İŞLEM GİRİŞTEN KAPANDI\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"Giriş: {format_price(entry)}"
                        )
                        close_signal_result(symbol, signal, "BE", entry)
                        closed = True
                        break

            if closed:
                continue

            signal["tp1_hit"] = tp1_hit
            signal["tp2_hit"] = tp2_hit
            signal["tp3_hit"] = tp3_hit
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


def build_daily_report():
    performance = load_performance()
    day = performance.get("days", {}).get(today_key(), {})

    opened = int(day.get("opened", 0))
    radar = int(day.get("radar", 0))
    tp1 = int(day.get("tp1", 0))
    tp2 = int(day.get("tp2", 0))
    tp3 = int(day.get("tp3", 0))
    sl = int(day.get("sl", 0))
    be = int(day.get("be", 0))
    expired = int(day.get("expired", 0))
    long_count = int(day.get("long", 0))
    short_count = int(day.get("short", 0))
    normal_count = int(day.get("normal", 0))
    radar_trade = int(day.get("radar_trade", 0))
    sl_after_tp1 = int(day.get("sl_after_tp1", 0))
    sl_after_tp2 = int(day.get("sl_after_tp2", 0))
    sl_after_no_return = int(day.get("sl_after_no_return", 0))
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

    recent_lines = []
    for item in day.get("closed_history", [])[-8:]:
        recent_lines.append(
            f"{item.get('time')} | {item.get('symbol')} {item.get('direction')} → {item.get('result')}"
        )
    recent_text = "\n".join(recent_lines) if recent_lines else "Henüz kapanan işlem yok."

    return f"""
📊 GÜNLÜK PERFORMANS RAPORU

Tarih: {today_key()}

Açılan İşlem Sinyali: {opened}
Radar Uyarısı: {radar}
LONG: {long_count}
SHORT: {short_count}

✅ 15M Giriş: {normal_count}
⚡ 5M Radar Trade: {radar_trade}

✅ TP1 Gelen: {tp1}
✅ TP2 Gelen: {tp2}
🏁 TP3 Gelen: {tp3}
🟡 Girişten Kapanan: {be}
❌ Stop Olan: {sl}
⏳ Süresi Dolan: {expired}
📌 Açık Sinyal: {open_count}

📊 TP1 Başarı Oranı: %{success}

🧪 SL Sonrası TP1'e Dönen: {sl_after_tp1}
🧪 SL Sonrası TP2'ye Dönen: {sl_after_tp2}
🧪 SL Sonrası Dönmeyen: {sl_after_no_return}

🏆 En İyi Coin: {best_coin}
⚠️ En Zayıf Coin: {worst_coin}

Son Kapananlar:
{recent_text}

Not: Radar uyarıları işlem sinyali sayılmaz.
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
    key = f"{signal['symbol']}_{signal['direction']}_{signal.get('source', 'MTF')}"
    open_signals[key] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "source": signal.get("source", "MTF"),
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
        "tp3_hit": False,
        "closed": False,
    }
    save_open_signals(open_signals)


def main():
    print(BOT_NAME, "başladı.")
    exchange = get_exchange()

    # Önce açık sinyal sonuçlarını ve stop sonrası takipleri kontrol et.
    check_open_signals(exchange)
    check_sl_after_follow(exchange)
    maybe_send_open_summary(exchange)

    risk_mode = risk_mode_active()
    if risk_mode:
        print("Risk modu aktif. Sistem durmadı, daha seçici çalışıyor.")

    scan_coins = get_scan_coins(exchange)
    market_status = get_market_direction_status(exchange)

    risky_open, reduced_open, total_open = count_open_signal_risk()
    print("Taranan coin:", len(scan_coins))
    print("Açık sinyal:", total_open)
    print("Riskli açık sinyal:", risky_open)
    print("TP1 görmüş takipte sinyal:", reduced_open)
    print("Risk modu:", risk_mode)

    trade_candidates = []
    radar_candidates = []

    # Akilli limit: Açık sinyal sayisi dolu olsa bile tarama durmaz.
    # Sadece TP1 gormemis riskli acik sinyal sayisi yeni islem kaydini sınırlar.
    for symbol in scan_coins:
        try:
            if has_open_same_symbol(symbol):
                print(symbol, "zaten açık sinyal var, atlandı.")
                continue

            if has_recent_stop(symbol):
                print(symbol, "yakın zamanda stop olduğu için atlandı.")
                continue

            if has_recent_closed_signal(symbol):
                print(symbol, "yakın zamanda kapandığı için tekrar sinyal atlandı.")
                continue

            current_price = get_current_price(exchange, symbol)

            df15m = fetch_df(exchange, symbol, ENTRY_TIMEFRAME, ENTRY_LIMIT, min_len=120)
            df1h = fetch_df(exchange, symbol, CONFIRM_TIMEFRAME, CONFIRM_LIMIT, min_len=120)
            df4h = fetch_df(exchange, symbol, TREND_TIMEFRAME, TREND_LIMIT, min_len=120)

            normal_signal = analyze_mtf_trade(symbol, df15m, df1h, df4h, current_price)

            signals = []
            if normal_signal is not None:
                signals.append(normal_signal)

            df5m = fetch_df(exchange, symbol, RADAR_TIMEFRAME, RADAR_LIMIT, min_len=50)
            radar_signal = analyze_5m_radar(symbol, df5m, df15m, df1h, df4h, current_price)
            if radar_signal is not None:
                signals.append(radar_signal)

            for signal in signals:
                if signal["direction"] == "LONG" and not ALLOW_LONG:
                    continue
                if signal["direction"] == "SHORT" and not ALLOW_SHORT:
                    continue

                # Market ters ise trade sinyalini radar uyarısına düşür.
                if signal.get("signal_class") == "TRADE" and not market_status.get(signal["direction"], True):
                    print(symbol, "market yönü ters olduğu için trade -> radar:", signal["direction"])
                    signal["signal_class"] = "RADAR"

                # Risk modunda radar kaynaklı trade açma.
                if (
                    risk_mode
                    and signal.get("source") == "5M_RADAR"
                    and signal.get("signal_class") == "TRADE"
                    and not RISK_MODE_ALLOW_RADAR_TRADE
                ):
                    signal["signal_class"] = "RADAR"

                valid, reason = is_entry_still_valid(signal, current_price)
                if not valid:
                    print(symbol, "giriş elendi ->", reason)
                    continue

                if signal["signal_class"] == "TRADE":
                    if not is_duplicate(signal, radar=False):
                        trade_candidates.append(signal)
                        print(symbol, "A kalite aday:", signal["source"], signal["direction"], signal["score"])
                else:
                    if not is_duplicate(signal, radar=True):
                        radar_candidates.append(signal)
                        print(symbol, "radar adayı:", signal["source"], signal["direction"], signal["score"])

            time.sleep(0.10)

        except Exception as e:
            print(symbol, "analiz hatası:", e)

    trade_candidates = sorted(
        trade_candidates,
        key=lambda s: (s["score"], 1 if s.get("source") == "15M_ENTRY" else 0),
        reverse=True,
    )
    radar_candidates = sorted(radar_candidates, key=lambda s: s["score"], reverse=True)

    max_trade = RISK_MODE_MAX_TRADE_SIGNALS if risk_mode else MAX_TRADE_SIGNALS_PER_RUN
    max_radar = RISK_MODE_MAX_RADAR_ALERTS if risk_mode else MAX_RADAR_ALERTS_PER_RUN

    risky_open, reduced_open, total_open = count_open_signal_risk()
    available_trade_slots = max(0, MAX_OPEN_SIGNALS - risky_open)
    allowed_trade_count = min(max_trade, available_trade_slots)

    selected_trade = trade_candidates[:allowed_trade_count]
    selected_limit_watch = []
    if available_trade_slots <= 0 and trade_candidates:
        # Limit doluysa en guclu 1 adayi islem olarak kaydetmeden takip uyarisi yap.
        selected_limit_watch = trade_candidates[:1]

    selected_radar = radar_candidates[:max_radar]

    if selected_trade:
        send_telegram(
            f"✅ {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {len(scan_coins)}\n"
            f"A kalite aday: {len(trade_candidates)}\n"
            f"Riskli açık sinyal: {risky_open}/{MAX_OPEN_SIGNALS}\n"
            f"TP1 görmüş takipte sinyal: {reduced_open}\n"
            f"Gönderilen işlem sinyali: {len(selected_trade)}\n"
            f"Risk Modu: {'AKTİF' if risk_mode else 'Kapalı'}\n"
            f"Sistem: {SYSTEM_NOTE}"
        )

    if selected_limit_watch:
        send_telegram(
            f"⚠️ {BOT_NAME} güçlü aday buldu ama riskli açık sinyal sınırı dolu.\n"
            f"Riskli açık sinyal: {risky_open}/{MAX_OPEN_SIGNALS}\n"
            f"TP1 görmüş takipte sinyal: {reduced_open}\n"
            f"Bu yüzden yeni işlem kaydı açılmadı; sadece takip uyarısı gönderilecek."
        )

    for signal in selected_trade:
        current_price = get_current_price(exchange, signal["symbol"])
        valid, reason = is_entry_still_valid(signal, current_price)
        if not valid:
            print(signal["symbol"], "son kontrol elendi:", reason)
            continue

        extra = f"\n💰 Güncel Fiyat: {format_price(current_price)}\n📌 Son Kontrol: Girişe yakın ✅"
        if send_telegram(signal["message"] + extra):
            save_open_signal(signal)
            mark_sent(signal, radar=False)
            update_performance(
                signal["symbol"],
                "OPENED",
                direction=signal["direction"],
                source=signal.get("source"),
                entry=signal.get("entry"),
                score=signal.get("score"),
            )
            time.sleep(1)

    for signal in selected_limit_watch:
        current_price = get_current_price(exchange, signal["symbol"])
        valid, reason = is_entry_still_valid(signal, current_price)
        if not valid:
            print(signal["symbol"], "limit takip son kontrol elendi:", reason)
            continue

        watch_message = build_limit_watch_message(signal, current_price=current_price)
        if send_telegram(watch_message):
            mark_sent(signal, radar=True)
            time.sleep(1)

    if selected_radar:
        send_telegram(
            f"📡 {BOT_NAME} radar çalıştı.\n"
            f"Radar uyarısı: {len(selected_radar)}\n"
            f"Bu mesajlar işlem sinyali değildir. A kalite giriş beklenir."
        )

    for signal in selected_radar:
        radar_message = signal["message"].replace(
            "A KALİTE MTF FUTURES SİNYALİ",
            "5M / 15M RADAR - İŞLEM AÇMA",
        )
        if send_telegram(radar_message):
            mark_sent(signal, radar=True)
            update_performance(
                signal["symbol"],
                "RADAR",
                direction=signal["direction"],
                source=signal.get("source"),
                entry=signal.get("entry"),
                score=signal.get("score"),
            )
            time.sleep(1)

    if not selected_trade and not selected_radar and not selected_limit_watch:
        print("Uygun sinyal yok.")
        if should_send_status():
            risky_open, reduced_open, _ = count_open_signal_risk()
            send_telegram(
                f"📡 {BOT_NAME} çalıştı.\n\n"
                f"Taranan coin: {len(scan_coins)}\n"
                f"Uygun MTF sinyali yok.\n"
                f"Riskli açık sinyal: {risky_open}/{MAX_OPEN_SIGNALS}\n"
                f"TP1 görmüş takipte sinyal: {reduced_open}\n"
                f"Risk Modu: {'AKTİF' if risk_mode else 'Kapalı'}\n"
                f"Sistem durmadı, taramaya devam ediyor."
            )
            mark_status_sent()

    maybe_send_daily_report()
    print(BOT_NAME, "tamamlandı.")


if __name__ == "__main__":
    main()
