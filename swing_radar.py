# swing_radar.py
# Swing Radar v2 - Dengeli Canlı Para
#
# OKX USDT perpetual futures için:
# 1D ana trend + 4H yapı + 1H giriş onayı
#
# Emir açmaz. Telegram sinyali gönderir ve TP/SL takibi yapar.
#
# v2 değişiklikleri:
# - Tek çalışmada en fazla 1 yeni Swing sinyali
# - En fazla 3 açık Swing sinyali
# - Maksimum stop mesafesi %3
# - Skor 100 yığılması azaltıldı
# - Eşit adaylarda düşük risk, güçlü ADX ve hacim öncelikli
# - Gönderimden önce giriş bölgesi yeniden kontrol edilir
# - TP1'in geldiği aynı mumda yanlış breakeven kapanışı engellenir
# - Eski state kayıtlarıyla uyumluluk korunur

import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timedelta, timezone

import ccxt
import pandas as pd
import requests


# =========================================================
# GENEL AYARLAR
# =========================================================

BOT_NAME = "Swing Radar v2 - Dengeli Canlı Para"

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "swing_radar_state.json"
TR_TIMEZONE = timezone(timedelta(hours=3))

MAX_SCAN_COINS = 220
MIN_24H_QUOTE_VOLUME = 500_000

MAX_NEW_SIGNALS_PER_RUN = 1
MAX_OPEN_SWING_SIGNALS = 3

DUPLICATE_SECONDS = 18 * 60 * 60

TRACK_TIMEFRAME = "1h"
TRACK_LIMIT = 240
MAX_OPEN_SIGNAL_HOURS = 120

SEND_NO_SIGNAL_REPORT = True
NO_SIGNAL_REPORT_EVERY_MINUTES = 360

MIN_SCORE = 80

MIN_RISK_PERCENT = 0.80
MAX_RISK_PERCENT = 3.00

TP1_R = 0.80
TP2_R = 1.60
TP3_R = 2.50

MAX_DISTANCE_FROM_1H_EMA20_PERCENT = 3.20
MAX_DISTANCE_FROM_4H_EMA20_PERCENT = 5.50

MIN_ADX_1H = 16
MIN_ADX_4H = 15
MIN_VOLUME_RATIO = 0.75

# Gönderim anında fiyat giriş bölgesinden ne kadar taşabilir?
MAX_ENTRY_ZONE_DRIFT_PERCENT = 0.50

D1_LIMIT = 260
H4_LIMIT = 260
H1_LIMIT = 260


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN veya CHAT_ID eksik.")
        return False

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": message,
            },
            timeout=20,
        )
        print("Telegram cevap:", response.status_code)
        return response.status_code == 200
    except Exception as exc:
        print("Telegram gönderim hatası:", exc)
        return False


# =========================================================
# STATE
# =========================================================

def now_ts():
    return int(time.time())


def tr_now_text():
    return datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def normalize_bot_symbol(symbol):
    value = str(symbol or "").upper().strip()
    value = value.replace("/USDT:USDT", "USDT")
    value = value.replace(":USDT", "")
    value = value.replace("/", "")

    if value and not value.endswith("USDT"):
        value += "USDT"

    return value


def empty_stats():
    return {
        "signals": 0,
        "tp1": 0,
        "tp2": 0,
        "tp3": 0,
        "stop": 0,
        "breakeven": 0,
        "expired": 0,
    }


def empty_state():
    return {
        "open_swing_signals": {},
        "last_sent": {},
        "last_no_signal_report": 0,
        "stats": empty_stats(),
    }


def load_state():
    try:
        if not os.path.exists(STATE_FILE):
            return empty_state()

        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()

        if not raw:
            return empty_state()

        state = json.loads(raw)

        if not isinstance(state, dict):
            state = empty_state()

        state.setdefault("open_swing_signals", {})
        state.setdefault("last_sent", {})
        state.setdefault("last_no_signal_report", 0)
        state.setdefault("stats", {})

        for key, value in empty_stats().items():
            state["stats"].setdefault(key, value)

        migrated = {}

        for old_key, signal in state["open_swing_signals"].items():
            if not isinstance(signal, dict):
                continue

            item = dict(signal)
            item["symbol"] = normalize_bot_symbol(item.get("symbol"))

            opened_at = int(
                item.get("opened_at")
                or item.get("created_ts")
                or now_ts()
            )

            item["opened_at"] = opened_at
            item["last_checked_at"] = int(
                item.get("last_checked_at")
                or opened_at
            )

            item.setdefault("tp1_hit", False)
            item.setdefault("tp2_hit", False)
            item.setdefault("tp3_hit", False)
            item.setdefault("closed", False)

            new_key = (
                f"{item.get('symbol', '')}_"
                f"{item.get('direction', '')}_"
                f"{item.get('source', 'SWING_RADAR')}"
            )

            migrated[new_key or old_key] = item

        state["open_swing_signals"] = migrated
        return state

    except Exception as exc:
        print("State okuma hatası:", exc)
        return empty_state()


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(
                state if isinstance(state, dict) else empty_state(),
                handle,
                indent=2,
                ensure_ascii=False,
            )
        return True
    except Exception as exc:
        print("State kaydetme hatası:", exc)
        return False


def increment_stat(state, key):
    state.setdefault("stats", empty_stats())
    state["stats"][key] = int(state["stats"].get(key, 0)) + 1


# =========================================================
# OKX / VERİ
# =========================================================

def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"},
    })


def to_okx_symbol(symbol):
    bot_symbol = normalize_bot_symbol(symbol)
    base = bot_symbol[:-4] if bot_symbol.endswith("USDT") else bot_symbol
    return f"{base}/USDT:USDT"


def okx_symbol_to_bot_symbol(okx_symbol):
    base = str(okx_symbol).split("/")[0]
    return f"{base}USDT".upper()


def safe_quote_volume(ticker):
    try:
        value = ticker.get("quoteVolume")
        if value is not None:
            return float(value)

        info = ticker.get("info", {})

        for key in ("volCcy24h", "volUsd24h", "vol24h"):
            value = info.get(key)
            if value is not None:
                return float(value)

    except Exception:
        pass

    return 0.0


def get_scan_coins(exchange):
    try:
        markets = exchange.load_markets()
        okx_symbols = []

        stable_bases = {
            "USDT",
            "USDC",
            "DAI",
            "FDUSD",
            "TUSD",
            "USDP",
            "USD",
        }

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
            volume = safe_quote_volume(tickers.get(okx_symbol, {}))

            if volume >= MIN_24H_QUOTE_VOLUME:
                rows.append((
                    okx_symbol_to_bot_symbol(okx_symbol),
                    volume,
                ))

        rows.sort(key=lambda item: item[1], reverse=True)

        coins = [
            symbol
            for symbol, _ in rows[:MAX_SCAN_COINS]
        ]

        print("Taranacak swing coin sayısı:", len(coins))
        print("İlk 20:", coins[:20])

        return coins

    except Exception as exc:
        print("Coin tarama hatası:", exc)
        return []


def fetch_df(exchange, symbol, timeframe, limit=200, min_len=60):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            limit=limit,
        )

        if not ohlcv or len(ohlcv) < min_len:
            return None

        frame = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"],
        )

        for column in ("open", "high", "low", "close", "volume"):
            frame[column] = pd.to_numeric(
                frame[column],
                errors="coerce",
            )

        frame = frame.dropna().reset_index(drop=True)

        return frame if len(frame) >= min_len else None

    except Exception as exc:
        print(symbol, timeframe, "veri hatası:", exc)
        return None


def fetch_candles_since(
    exchange,
    symbol,
    timeframe,
    since_seconds,
    limit=240,
):
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
                "volume": float(item[5]),
            }
            for item in ohlcv
        ]

    except Exception as exc:
        print(symbol, "mum takip hatası:", exc)
        return []


def get_current_price(exchange, symbol):
    try:
        ticker = exchange.fetch_ticker(to_okx_symbol(symbol))
        price = ticker.get("last")

        return float(price) if price is not None else None

    except Exception as exc:
        print(symbol, "güncel fiyat hatası:", exc)
        return None


# =========================================================
# HESAPLAMALAR
# =========================================================

def safe_float(value, default=0.0):
    try:
        number = float(value)

        if math.isnan(number) or math.isinf(number):
            return default

        return number

    except Exception:
        return default


def format_price(value):
    number = safe_float(value)

    if number >= 100:
        return f"{number:.2f}"
    if number >= 10:
        return f"{number:.3f}"
    if number >= 1:
        return f"{number:.4f}"
    if number >= 0.1:
        return f"{number:.5f}"
    if number >= 0.01:
        return f"{number:.6f}"

    return f"{number:.10f}"


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def calc_rsi(series, period=14):
    delta = series.diff()

    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    average_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    average_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    rs = average_gain / average_loss.replace(0, 0.0000001)

    return 100 - (100 / (1 + rs))


def calc_atr(frame, period=14):
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    return true_range.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()


def calc_adx(frame, period=14):
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]

    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where(
        (plus_dm > minus_dm) & (plus_dm > 0),
        0.0,
    )

    minus_dm = minus_dm.where(
        (minus_dm > plus_dm) & (minus_dm > 0),
        0.0,
    )

    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    plus_di = (
        100
        * plus_dm.ewm(alpha=1 / period, adjust=False).mean()
        / atr.replace(0, 0.0000001)
    )

    minus_di = (
        100
        * minus_dm.ewm(alpha=1 / period, adjust=False).mean()
        / atr.replace(0, 0.0000001)
    )

    dx = (
        abs(plus_di - minus_di)
        / (plus_di + minus_di).replace(0, 0.0000001)
        * 100
    )

    return dx.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()


def add_indicators(frame):
    if frame is None or frame.empty:
        return None

    data = frame.copy()

    data["ema20"] = ema(data["close"], 20)
    data["ema50"] = ema(data["close"], 50)
    data["ema200"] = ema(data["close"], 200)
    data["rsi"] = calc_rsi(data["close"])
    data["atr"] = calc_atr(data)
    data["adx"] = calc_adx(data)

    data["volume_avg"] = data["volume"].rolling(20).mean()
    data["volume_ratio"] = (
        data["volume"]
        / data["volume_avg"].replace(0, 0.0000001)
    )

    data = data.dropna().reset_index(drop=True)

    return data if len(data) >= 20 else None


def pct(value, reference):
    try:
        if reference == 0:
            return 0.0

        return ((value - reference) / reference) * 100

    except Exception:
        return 0.0


def abs_pct(value, reference):
    return abs(pct(value, reference))


def candle_is_green(row):
    return safe_float(row["close"]) > safe_float(row["open"])


def candle_is_red(row):
    return safe_float(row["close"]) < safe_float(row["open"])


def rolling_support(frame, lookback=80):
    try:
        return float(frame["low"].iloc[-lookback:-2].min())
    except Exception:
        return float(frame["low"].iloc[-20:-2].min())


def rolling_resistance(frame, lookback=80):
    try:
        return float(frame["high"].iloc[-lookback:-2].max())
    except Exception:
        return float(frame["high"].iloc[-20:-2].max())


def clamp(value, minimum, maximum):
    return max(minimum, min(maximum, value))


def build_condition(label, ok):
    return {
        "label": label,
        "ok": bool(ok),
    }


def missing_reasons(conditions):
    return [
        condition["label"]
        for condition in conditions
        if not condition["ok"]
    ]


def entry_zone_distance_percent(current_price, entry_low, entry_high):
    current_price = safe_float(current_price)
    entry_low = safe_float(entry_low)
    entry_high = safe_float(entry_high)

    if current_price <= 0 or entry_low <= 0 or entry_high <= 0:
        return 999.0

    low = min(entry_low, entry_high)
    high = max(entry_low, entry_high)

    if low <= current_price <= high:
        return 0.0

    nearest = low if current_price < low else high
    return abs_pct(current_price, nearest)


def leverage_text(risk_percent):
    risk = safe_float(risk_percent)

    if risk <= 1.50:
        return "1x-2x"

    if risk <= 3.00:
        return "1x"

    return "Pas geç"


# =========================================================
# SKOR / MESAJ
# =========================================================

def calculate_quality_score(
    direction,
    risk_percent,
    rsi_1h,
    adx_4h,
    adx_1h,
    vol_4h,
    vol_1h,
    dist_1h_ema20,
    dist_4h_ema20,
):
    """
    Bütün zorunlu şartları geçen adayları birbirinden ayırır.
    11/11 adayların tamamının 100 olmasını engeller.
    """
    score = 70.0

    # ADX: toplam en fazla 9 puan
    score += clamp((adx_4h - MIN_ADX_4H) * 0.45, 0, 4.5)
    score += clamp((adx_1h - MIN_ADX_1H) * 0.45, 0, 4.5)

    # Hacim: toplam en fazla 8 puan
    score += clamp((vol_4h - MIN_VOLUME_RATIO) * 4.0, 0, 4.0)
    score += clamp((vol_1h - MIN_VOLUME_RATIO) * 4.0, 0, 4.0)

    # RSI: en fazla 5 puan
    if direction == "LONG":
        rsi_quality = 1.0 - min(abs(rsi_1h - 55.0) / 18.0, 1.0)
    else:
        rsi_quality = 1.0 - min(abs(rsi_1h - 45.0) / 18.0, 1.0)

    score += rsi_quality * 5.0

    # Düşük stop: en fazla 6 puan
    risk_quality = (
        MAX_RISK_PERCENT - risk_percent
    ) / max(
        0.0001,
        MAX_RISK_PERCENT - MIN_RISK_PERCENT,
    )

    score += clamp(risk_quality, 0, 1) * 6.0

    # EMA'lara yakınlık: en fazla 2 puan
    distance_quality = 1.0 - min(
        (
            dist_1h_ema20 / MAX_DISTANCE_FROM_1H_EMA20_PERCENT
            + dist_4h_ema20 / MAX_DISTANCE_FROM_4H_EMA20_PERCENT
        ) / 2.0,
        1.0,
    )

    score += distance_quality * 2.0

    return int(round(clamp(score, 0, 99)))


def build_signal_message(signal):
    icon = "🟢" if signal["direction"] == "LONG" else "🔴"

    if signal["score"] >= 90:
        quality = "A+ Swing"
    elif signal["score"] >= 85:
        quality = "A Swing"
    else:
        quality = "B+ Dikkatli Swing"

    return (
        f"📈 {BOT_NAME}\n\n"
        f"{icon} {signal['direction']}\n"
        f"🟡 Coin: {signal['symbol']}\n"
        f"⏱️ Kaynak: {signal['source']}\n"
        f"📌 Kurulum: {signal['setup']}\n\n"
        f"📌 Giriş: {format_price(signal['entry'])}\n"
        f"📍 Giriş Bölgesi: "
        f"{format_price(signal['entry_low'])} - "
        f"{format_price(signal['entry_high'])}\n"
        f"🎯 TP1: {format_price(signal['tp1'])}\n"
        f"🎯 TP2: {format_price(signal['tp2'])}\n"
        f"🎯 TP3: {format_price(signal['tp3'])}\n"
        f"🛑 SL: {format_price(signal['sl'])}\n\n"
        f"📊 Kalite Skoru: %{signal['score']} ({quality})\n"
        f"🛡️ Stop Mesafesi: "
        f"%{round(signal['risk_percent'], 2)}\n"
        f"⚙️ Kaldıraç Önerisi: "
        f"{leverage_text(signal['risk_percent'])}\n\n"
        f"🧭 Çoklu Zaman Dilimi:\n"
        f"• 1D: {signal['d1_note']}\n"
        f"• 4H: {signal['h4_note']}\n"
        f"• 1H: {signal['h1_note']}\n\n"
        f"📊 Göstergeler:\n"
        f"• 1D RSI: {round(signal['rsi_d1'], 2)}\n"
        f"• 4H RSI: {round(signal['rsi_4h'], 2)}\n"
        f"• 1H RSI: {round(signal['rsi_1h'], 2)}\n"
        f"• 4H ADX: {round(signal['adx_4h'], 2)}\n"
        f"• 1H ADX: {round(signal['adx_1h'], 2)}\n"
        f"• 1H Hacim: {round(signal['vol_1h'], 2)}x\n"
        f"• 4H Hacim: {round(signal['vol_4h'], 2)}x\n"
        f"• Destek: {format_price(signal['support'])}\n"
        f"• Direnç: {format_price(signal['resistance'])}\n\n"
        f"📌 İşlem Kuralı:\n"
        f"• Swing sinyalidir; scalp gibi hızlı işlem değildir.\n"
        f"• Giriş bölgesinden uzaklaştıysa işleme girme.\n"
        f"• TP1 gelirse %50 kâr al, SL girişe çek.\n"
        f"• Stop mutlaka girilmeli.\n"
        f"• Marjin: Isolated.\n"
        f"• Kaldıraç düşük tutulmalı.\n\n"
        f"⚠️ Finansal tavsiye değildir. "
        f"Grafikte kontrol etmeden işlem açma."
    )


# =========================================================
# SWING ANALİZİ
# =========================================================

def analyze_direction(
    symbol,
    direction,
    df1d,
    df4h,
    df1h,
    current_price,
):
    try:
        d1 = add_indicators(df1d)
        h4 = add_indicators(df4h)
        h1 = add_indicators(df1h)

        if d1 is None or h4 is None or h1 is None:
            return None, None

        if len(d1) < 220 or len(h4) < 220 or len(h1) < 220:
            return None, None

        last_d1 = d1.iloc[-2]
        last_h4 = h4.iloc[-2]
        last_h1 = h1.iloc[-2]
        prev_h1 = h1.iloc[-3]
        forming_h1 = h1.iloc[-1]

        entry = (
            safe_float(current_price)
            if safe_float(current_price) > 0
            else safe_float(last_h1["close"])
        )

        if entry <= 0:
            return None, None

        atr_4h = safe_float(last_h4["atr"])
        atr_1h = safe_float(last_h1["atr"])

        if atr_4h <= 0 or atr_1h <= 0:
            return None, None

        support = rolling_support(h4, 80)
        resistance = rolling_resistance(h4, 80)

        d_close = safe_float(last_d1["close"])
        d_ema20 = safe_float(last_d1["ema20"])
        d_ema50 = safe_float(last_d1["ema50"])
        d_ema200 = safe_float(last_d1["ema200"])

        h4_close = safe_float(last_h4["close"])
        h4_ema20 = safe_float(last_h4["ema20"])
        h4_ema50 = safe_float(last_h4["ema50"])
        h4_ema200 = safe_float(last_h4["ema200"])

        h1_close = safe_float(last_h1["close"])
        h1_ema20 = safe_float(last_h1["ema20"])
        h1_ema50 = safe_float(last_h1["ema50"])

        rsi_d1 = safe_float(last_d1["rsi"])
        rsi_4h = safe_float(last_h4["rsi"])
        rsi_1h = safe_float(last_h1["rsi"])

        adx_4h = safe_float(last_h4["adx"])
        adx_1h = safe_float(last_h1["adx"])

        vol_4h = safe_float(last_h4["volume_ratio"])
        vol_1h = safe_float(last_h1["volume_ratio"])

        dist_1h_ema20 = abs_pct(entry, h1_ema20)
        dist_4h_ema20 = abs_pct(entry, h4_ema20)

        if direction == "LONG":
            atr_stop = entry - atr_4h * 1.15
            support_stop = support * 0.995

            sl = max(
                min(atr_stop, entry * 0.992),
                support_stop,
            )

            if sl >= entry:
                sl = entry - atr_4h * 1.10

            risk = entry - sl
            risk_percent = risk / entry * 100

            tp1 = entry + risk * TP1_R
            tp2 = entry + risk * TP2_R
            tp3 = entry + risk * TP3_R

            entry_low = entry - atr_1h * 0.35
            entry_high = entry + atr_1h * 0.25

            d1_trend = d_close > d_ema50 and d_ema20 >= d_ema50
            d1_safe = d_close > d_ema200 or d_ema50 > d_ema200

            h4_trend = h4_close > h4_ema50 and h4_ema20 >= h4_ema50
            h4_safe = h4_close > h4_ema200 or h4_ema50 >= h4_ema200

            h1_confirm = (
                h1_close > h1_ema20
                or (
                    candle_is_green(last_h1)
                    and h1_close > h1_ema50
                )
            )

            h1_turn = (
                candle_is_green(last_h1)
                or h1_close > safe_float(prev_h1["close"])
            )

            rsi_ok = (
                42 <= rsi_1h <= 68
                and rsi_4h <= 72
                and rsi_d1 <= 74
            )

            d1_note = (
                "1D trend yukarı"
                if d1_trend
                else "1D trend zayıf"
            )

            h4_note = (
                "4H trend yukarı"
                if h4_trend
                else "4H trend zayıf/karışık"
            )

            h1_note = (
                "1H alış onayı"
                if h1_confirm
                else "1H onay zayıf"
            )

            setup = "1D + 4H trend uyumlu Swing LONG"

            invalidated_before_send = (
                safe_float(forming_h1["low"]) <= sl
            )

        else:
            atr_stop = entry + atr_4h * 1.15
            resistance_stop = resistance * 1.005

            sl = min(
                max(atr_stop, entry * 1.008),
                resistance_stop,
            )

            if sl <= entry:
                sl = entry + atr_4h * 1.10

            risk = sl - entry
            risk_percent = risk / entry * 100

            tp1 = entry - risk * TP1_R
            tp2 = entry - risk * TP2_R
            tp3 = entry - risk * TP3_R

            entry_low = entry - atr_1h * 0.25
            entry_high = entry + atr_1h * 0.35

            d1_trend = d_close < d_ema50 and d_ema20 <= d_ema50
            d1_safe = d_close < d_ema200 or d_ema50 < d_ema200

            h4_trend = h4_close < h4_ema50 and h4_ema20 <= h4_ema50
            h4_safe = h4_close < h4_ema200 or h4_ema50 <= h4_ema200

            h1_confirm = (
                h1_close < h1_ema20
                or (
                    candle_is_red(last_h1)
                    and h1_close < h1_ema50
                )
            )

            h1_turn = (
                candle_is_red(last_h1)
                or h1_close < safe_float(prev_h1["close"])
            )

            rsi_ok = (
                32 <= rsi_1h <= 58
                and rsi_4h >= 25
                and rsi_d1 >= 22
            )

            d1_note = (
                "1D trend aşağı"
                if d1_trend
                else "1D trend zayıf"
            )

            h4_note = (
                "4H trend aşağı"
                if h4_trend
                else "4H trend zayıf/karışık"
            )

            h1_note = (
                "1H satış onayı"
                if h1_confirm
                else "1H onay zayıf"
            )

            setup = "1D + 4H trend uyumlu Swing SHORT"

            invalidated_before_send = (
                safe_float(forming_h1["high"]) >= sl
            )

        adx_ok = (
            adx_4h >= MIN_ADX_4H
            or adx_1h >= MIN_ADX_1H
        )

        volume_ok = (
            vol_1h >= MIN_VOLUME_RATIO
            or vol_4h >= MIN_VOLUME_RATIO
        )

        not_extended = (
            dist_1h_ema20 <= MAX_DISTANCE_FROM_1H_EMA20_PERCENT
            and dist_4h_ema20 <= MAX_DISTANCE_FROM_4H_EMA20_PERCENT
        )

        risk_ok = (
            MIN_RISK_PERCENT
            <= risk_percent
            <= MAX_RISK_PERCENT
        )

        conditions = [
            build_condition("1D trend uyumlu değil", d1_trend),
            build_condition("1D ema200 güvenli değil", d1_safe),
            build_condition("4H trend uyumlu değil", h4_trend),
            build_condition("4H ana yapı zayıf", h4_safe),
            build_condition("1H giriş onayı yok", h1_confirm),
            build_condition("1H dönüş mumu yok", h1_turn),
            build_condition("RSI swing için uygun değil", rsi_ok),
            build_condition("ADX trend gücü düşük", adx_ok),
            build_condition("hacim onayı düşük", volume_ok),
            build_condition("fiyat EMA'lara göre çok uzak", not_extended),
            build_condition("risk uygun değil", risk_ok),
            build_condition(
                "kurulum sinyalden önce stop alanını gördü",
                not invalidated_before_send,
            ),
        ]

        ok_count = sum(
            1
            for condition in conditions
            if condition["ok"]
        )

        total_conditions = len(conditions)

        hard_ok = all(
            condition["ok"]
            for condition in conditions
        )

        score = calculate_quality_score(
            direction=direction,
            risk_percent=risk_percent,
            rsi_1h=rsi_1h,
            adx_4h=adx_4h,
            adx_1h=adx_1h,
            vol_4h=vol_4h,
            vol_1h=vol_1h,
            dist_1h_ema20=dist_1h_ema20,
            dist_4h_ema20=dist_4h_ema20,
        )

        debug = {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "ok_count": ok_count,
            "total_conditions": total_conditions,
            "missing": missing_reasons(conditions),
            "risk_percent": risk_percent,
            "rsi_1h": rsi_1h,
            "adx_4h": adx_4h,
            "adx_1h": adx_1h,
            "vol_1h": vol_1h,
            "vol_4h": vol_4h,
            "dist_1h_ema20": dist_1h_ema20,
            "dist_4h_ema20": dist_4h_ema20,
        }

        if not hard_ok or score < MIN_SCORE:
            return None, debug

        signal = {
            "symbol": normalize_bot_symbol(symbol),
            "direction": direction,
            "source": "SWING_RADAR",
            "setup": setup,
            "entry": entry,
            "entry_low": entry_low,
            "entry_high": entry_high,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "sl": sl,
            "score": score,
            "risk_percent": risk_percent,
            "d1_note": d1_note,
            "h4_note": h4_note,
            "h1_note": h1_note,
            "rsi_d1": rsi_d1,
            "rsi_4h": rsi_4h,
            "rsi_1h": rsi_1h,
            "adx_4h": adx_4h,
            "adx_1h": adx_1h,
            "vol_4h": vol_4h,
            "vol_1h": vol_1h,
            "support": support,
            "resistance": resistance,
            "ok_count": ok_count,
            "total_conditions": total_conditions,
            "missing": [],
        }

        signal["message"] = build_signal_message(signal)

        return signal, debug

    except Exception as exc:
        print(symbol, direction, "swing analiz hatası:", exc)
        return None, None


def analyze_symbol(exchange, symbol):
    current_price = get_current_price(exchange, symbol)

    df1d = fetch_df(
        exchange,
        symbol,
        "1d",
        limit=D1_LIMIT,
        min_len=220,
    )

    df4h = fetch_df(
        exchange,
        symbol,
        "4h",
        limit=H4_LIMIT,
        min_len=220,
    )

    df1h = fetch_df(
        exchange,
        symbol,
        "1h",
        limit=H1_LIMIT,
        min_len=220,
    )

    long_signal, long_debug = analyze_direction(
        symbol,
        "LONG",
        df1d,
        df4h,
        df1h,
        current_price,
    )

    short_signal, short_debug = analyze_direction(
        symbol,
        "SHORT",
        df1d,
        df4h,
        df1h,
        current_price,
    )

    signals = []

    if long_signal is not None:
        signals.append(long_signal)

    if short_signal is not None:
        signals.append(short_signal)

    return signals, long_debug, short_debug


# =========================================================
# TEKRAR / AÇIK SİNYAL
# =========================================================

def duplicate_key(symbol, direction):
    return f"{normalize_bot_symbol(symbol)}_{direction}"


def is_recent_duplicate(state, symbol, direction):
    last_time = int(
        state.get("last_sent", {}).get(
            duplicate_key(symbol, direction),
            0,
        )
    )

    return now_ts() - last_time < DUPLICATE_SECONDS


def mark_sent(state, symbol, direction):
    state.setdefault("last_sent", {})

    state["last_sent"][
        duplicate_key(symbol, direction)
    ] = now_ts()

    cutoff = now_ts() - 7 * 24 * 60 * 60

    state["last_sent"] = {
        key: value
        for key, value in state["last_sent"].items()
        if int(value) >= cutoff
    }

    save_state(state)


def has_open_same_symbol(state, symbol):
    symbol = normalize_bot_symbol(symbol)

    return any(
        normalize_bot_symbol(signal.get("symbol")) == symbol
        for signal in state.get(
            "open_swing_signals",
            {},
        ).values()
    )


def save_open_signal(state, signal):
    key = (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['source']}"
    )

    state.setdefault("open_swing_signals", {})

    state["open_swing_signals"][key] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "source": signal["source"],
        "entry": signal["entry"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "tp3": signal["tp3"],
        "sl": signal["sl"],
        "score": signal["score"],
        "risk_percent": signal["risk_percent"],
        "opened_at": now_ts(),
        "last_checked_at": now_ts(),
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "closed": False,
    }

    increment_stat(state, "signals")
    save_state(state)


# =========================================================
# AÇIK SİNYAL TAKİBİ
# =========================================================

def notify_tp1(state, symbol, direction, entry, tp1):
    send_telegram(
        f"✅ SWING TP1 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"TP1: {format_price(tp1)}\n"
        f"Öneri: %50 kâr al, SL girişe çek."
    )
    increment_stat(state, "tp1")


def notify_tp2(state, symbol, direction, tp2):
    send_telegram(
        f"✅ SWING TP2 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"TP2: {format_price(tp2)}"
    )
    increment_stat(state, "tp2")


def notify_tp3(state, symbol, direction, tp3):
    send_telegram(
        f"🏁 SWING TP3 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"TP3: {format_price(tp3)}\n"
        f"Swing maksimum hedefe ulaştı."
    )
    increment_stat(state, "tp3")


def notify_stop(state, symbol, direction, entry, sl, close):
    send_telegram(
        f"❌ SWING STOP OLDU\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"SL: {format_price(sl)}\n"
        f"Güncel: {format_price(close)}"
    )
    increment_stat(state, "stop")


def notify_breakeven(state, symbol, direction, entry):
    send_telegram(
        f"🟡 SWING KALAN GİRİŞTEN KAPANDI\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}"
    )
    increment_stat(state, "breakeven")


def check_open_signals(exchange, state):
    open_signals = state.get("open_swing_signals", {})

    if not open_signals:
        print("Açık swing sinyali yok.")
        return

    updated = {}
    max_age_seconds = MAX_OPEN_SIGNAL_HOURS * 60 * 60

    for key, signal in open_signals.items():
        try:
            symbol = normalize_bot_symbol(signal["symbol"])
            direction = signal["direction"]

            entry = safe_float(signal["entry"])
            tp1 = safe_float(signal["tp1"])
            tp2 = safe_float(signal["tp2"])
            tp3 = safe_float(signal["tp3"])
            sl = safe_float(signal["sl"])

            opened_at = int(
                signal.get("opened_at")
                or signal.get("created_ts")
                or now_ts()
            )

            last_checked_at = int(
                signal.get("last_checked_at")
                or opened_at
            )

            if signal.get("closed") or signal.get("tp3_hit"):
                continue

            if (
                now_ts() - opened_at > max_age_seconds
                and not signal.get("tp1_hit")
            ):
                send_telegram(
                    f"⏳ SWING SİNYAL SÜRESİ DOLDU\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n\n"
                    f"{MAX_OPEN_SIGNAL_HOURS} saat içinde TP1 "
                    f"gelmediği için takipten çıkarıldı."
                )

                increment_stat(state, "expired")
                continue

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                max(
                    opened_at,
                    last_checked_at - 2 * 60 * 60,
                ),
                TRACK_LIMIT,
            )

            if not candles:
                updated[key] = signal
                continue

            tp1_hit = bool(signal.get("tp1_hit", False))
            tp2_hit = bool(signal.get("tp2_hit", False))
            tp3_hit = bool(signal.get("tp3_hit", False))

            closed = False

            for candle in candles:
                high = safe_float(candle["high"])
                low = safe_float(candle["low"])
                close = safe_float(candle["close"])

                just_hit_tp1 = False

                if direction == "LONG":
                    if not tp1_hit:
                        if low <= sl and high >= tp1:
                            if close >= entry:
                                tp1_hit = True
                                just_hit_tp1 = True
                                notify_tp1(
                                    state,
                                    symbol,
                                    direction,
                                    entry,
                                    tp1,
                                )
                            else:
                                notify_stop(
                                    state,
                                    symbol,
                                    direction,
                                    entry,
                                    sl,
                                    close,
                                )
                                closed = True
                                break

                        elif low <= sl:
                            notify_stop(
                                state,
                                symbol,
                                direction,
                                entry,
                                sl,
                                close,
                            )
                            closed = True
                            break

                        elif high >= tp1:
                            tp1_hit = True
                            just_hit_tp1 = True
                            notify_tp1(
                                state,
                                symbol,
                                direction,
                                entry,
                                tp1,
                            )

                    if tp1_hit and not tp2_hit and high >= tp2:
                        tp2_hit = True
                        notify_tp2(
                            state,
                            symbol,
                            direction,
                            tp2,
                        )

                    if tp1_hit and not tp3_hit and high >= tp3:
                        tp3_hit = True
                        notify_tp3(
                            state,
                            symbol,
                            direction,
                            tp3,
                        )
                        closed = True
                        break

                    if (
                        tp1_hit
                        and not just_hit_tp1
                        and low <= entry
                    ):
                        notify_breakeven(
                            state,
                            symbol,
                            direction,
                            entry,
                        )
                        closed = True
                        break

                else:
                    if not tp1_hit:
                        if high >= sl and low <= tp1:
                            if close <= entry:
                                tp1_hit = True
                                just_hit_tp1 = True
                                notify_tp1(
                                    state,
                                    symbol,
                                    direction,
                                    entry,
                                    tp1,
                                )
                            else:
                                notify_stop(
                                    state,
                                    symbol,
                                    direction,
                                    entry,
                                    sl,
                                    close,
                                )
                                closed = True
                                break

                        elif high >= sl:
                            notify_stop(
                                state,
                                symbol,
                                direction,
                                entry,
                                sl,
                                close,
                            )
                            closed = True
                            break

                        elif low <= tp1:
                            tp1_hit = True
                            just_hit_tp1 = True
                            notify_tp1(
                                state,
                                symbol,
                                direction,
                                entry,
                                tp1,
                            )

                    if tp1_hit and not tp2_hit and low <= tp2:
                        tp2_hit = True
                        notify_tp2(
                            state,
                            symbol,
                            direction,
                            tp2,
                        )

                    if tp1_hit and not tp3_hit and low <= tp3:
                        tp3_hit = True
                        notify_tp3(
                            state,
                            symbol,
                            direction,
                            tp3,
                        )
                        closed = True
                        break

                    if (
                        tp1_hit
                        and not just_hit_tp1
                        and high >= entry
                    ):
                        notify_breakeven(
                            state,
                            symbol,
                            direction,
                            entry,
                        )
                        closed = True
                        break

            if closed:
                continue

            signal["symbol"] = symbol
            signal["opened_at"] = opened_at
            signal["last_checked_at"] = now_ts()
            signal["tp1_hit"] = tp1_hit
            signal["tp2_hit"] = tp2_hit
            signal["tp3_hit"] = tp3_hit

            updated[key] = signal

        except Exception as exc:
            print(key, "swing takip hatası:", exc)
            updated[key] = signal

    state["open_swing_signals"] = updated
    save_state(state)


# =========================================================
# RAPOR
# =========================================================

def top_reasons_text(counter, limit=5):
    if not counter:
        return "Veri yok"

    return "\n".join(
        f"• {reason}: {count}"
        for reason, count in counter.most_common(limit)
    )


def candidate_line(debug):
    if not debug:
        return ""

    missing = debug.get("missing", [])

    missing_text = (
        ", ".join(missing[:3])
        if missing
        else "eksik yok"
    )

    return (
        f"{debug['symbol']} {debug['direction']} | "
        f"şart {debug['ok_count']}/"
        f"{debug['total_conditions']} | "
        f"kalite {debug['score']} | "
        f"risk %{round(debug.get('risk_percent', 0), 2)} | "
        f"ADX 4H/1H "
        f"{round(debug.get('adx_4h', 0), 1)}/"
        f"{round(debug.get('adx_1h', 0), 1)} | "
        f"eksik: {missing_text}"
    )


def build_no_signal_report(
    scanned_count,
    new_signal_count,
    long_counter,
    short_counter,
    top_candidates,
):
    lines = [
        "📊 SWING RADAR v2 RAPORU",
        "",
        f"Bot: {BOT_NAME}",
        f"Zaman: {tr_now_text()}",
        f"Taranan coin: {scanned_count}",
        f"Filtreyi geçen kaliteli aday: {new_signal_count}",
        "",
        "LONG tarafında en çok elenen:",
        top_reasons_text(long_counter),
        "",
        "SHORT tarafında en çok elenen:",
        top_reasons_text(short_counter),
        "",
        "Swing sinyale en yakın adaylar:",
    ]

    if top_candidates:
        for item in top_candidates[:8]:
            lines.append("• " + candidate_line(item))
    else:
        lines.append("• Yakın aday yok")

    lines.extend([
        "",
        "Not: Bu rapor işlem sinyali değildir. "
        "Giriş, TP ve SL içeren gerçek Swing mesajını bekle.",
    ])

    return "\n".join(lines)


def should_send_no_signal_report(state):
    if not SEND_NO_SIGNAL_REPORT:
        return False

    last_report = int(state.get("last_no_signal_report", 0))

    return (
        now_ts() - last_report
        >= NO_SIGNAL_REPORT_EVERY_MINUTES * 60
    )


def mark_no_signal_report_sent(state):
    state["last_no_signal_report"] = now_ts()
    save_state(state)


# =========================================================
# MAIN
# =========================================================

def signal_sort_key(signal):
    """
    Öncelik:
    1) yüksek kalite skoru
    2) düşük stop mesafesi
    3) güçlü ADX
    4) güçlü hacim
    """
    adx_strength = (
        safe_float(signal.get("adx_4h"))
        + safe_float(signal.get("adx_1h"))
    )

    volume_strength = max(
        safe_float(signal.get("vol_4h")),
        safe_float(signal.get("vol_1h")),
    )

    return (
        safe_float(signal.get("score")),
        -safe_float(signal.get("risk_percent"), 999),
        adx_strength,
        volume_strength,
    )


def debug_sort_key(debug):
    if not debug:
        return (0, 0, -999, 0, 0)

    return (
        safe_float(debug.get("ok_count")),
        safe_float(debug.get("score")),
        -safe_float(debug.get("risk_percent"), 999),
        safe_float(debug.get("adx_4h"))
        + safe_float(debug.get("adx_1h")),
        max(
            safe_float(debug.get("vol_4h")),
            safe_float(debug.get("vol_1h")),
        ),
    )


def main():
    print(BOT_NAME, "başladı.")

    state = load_state()
    exchange = get_exchange()

    check_open_signals(exchange, state)

    state = load_state()
    scan_coins = get_scan_coins(exchange)

    open_count = len(
        state.get("open_swing_signals", {})
    )

    available_slots = max(
        0,
        MAX_OPEN_SWING_SIGNALS - open_count,
    )

    print("Açık swing:", open_count)
    print("Boş swing slot:", available_slots)

    all_signals = []
    long_reasons = Counter()
    short_reasons = Counter()
    top_candidates = []

    scanned = 0

    for symbol in scan_coins:
        try:
            scanned += 1

            if has_open_same_symbol(state, symbol):
                print(
                    symbol,
                    "zaten açık swing var, atlandı.",
                )
                continue

            signals, long_debug, short_debug = analyze_symbol(
                exchange,
                symbol,
            )

            if long_debug:
                for reason in long_debug.get("missing", []):
                    long_reasons[reason] += 1

                top_candidates.append(long_debug)

            if short_debug:
                for reason in short_debug.get("missing", []):
                    short_reasons[reason] += 1

                top_candidates.append(short_debug)

            for signal in signals:
                if is_recent_duplicate(
                    state,
                    signal["symbol"],
                    signal["direction"],
                ):
                    print(
                        signal["symbol"],
                        signal["direction"],
                        "duplicate, atlandı.",
                    )
                    continue

                all_signals.append(signal)

            time.sleep(0.08)

        except Exception as exc:
            print(
                symbol,
                "genel swing analiz hatası:",
                exc,
            )

    all_signals.sort(
        key=signal_sort_key,
        reverse=True,
    )

    top_candidates.sort(
        key=debug_sort_key,
        reverse=True,
    )

    selected = []
    max_to_send = min(
        MAX_NEW_SIGNALS_PER_RUN,
        available_slots,
    )

    # Gönderimden hemen önce güncel fiyat giriş bölgesine göre kontrol edilir.
    for signal in all_signals:
        if len(selected) >= max_to_send:
            break

        current_price = get_current_price(
            exchange,
            signal["symbol"],
        )

        if current_price is None:
            continue

        zone_drift = entry_zone_distance_percent(
            current_price,
            signal["entry_low"],
            signal["entry_high"],
        )

        if zone_drift > MAX_ENTRY_ZONE_DRIFT_PERCENT:
            print(
                signal["symbol"],
                "Swing giriş bölgesinden uzaklaştı:",
                round(zone_drift, 3),
                "%",
            )
            continue

        # Güncel fiyat stopu geçmişse artık sinyal gönderilmez.
        if (
            signal["direction"] == "LONG"
            and current_price <= signal["sl"]
        ):
            print(
                signal["symbol"],
                "LONG sinyal gönderilmeden stop alanına indi.",
            )
            continue

        if (
            signal["direction"] == "SHORT"
            and current_price >= signal["sl"]
        ):
            print(
                signal["symbol"],
                "SHORT sinyal gönderilmeden stop alanına çıktı.",
            )
            continue

        signal["current_price"] = current_price
        signal["entry_zone_drift"] = zone_drift
        selected.append(signal)

    print("Bulunan kaliteli swing sinyal:", len(all_signals))
    print("Gönderilecek swing sinyal:", len(selected))

    if selected:
        send_telegram(
            f"📈 {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {scanned}\n"
            f"Kaliteli Swing adayı: {len(all_signals)}\n"
            f"Açık Swing: "
            f"{open_count}/{MAX_OPEN_SWING_SIGNALS}\n"
            f"Gönderilecek sinyal: {len(selected)}"
        )

    for signal in selected:
        extra = (
            f"\n💰 Güncel Fiyat: "
            f"{format_price(signal['current_price'])}\n"
            f"📏 Giriş Bölgesi Sapması: "
            f"%{round(signal['entry_zone_drift'], 3)}\n"
            f"📌 Son Kontrol: Swing giriş bölgesinde ✅"
        )

        if send_telegram(signal["message"] + extra):
            save_open_signal(state, signal)

            mark_sent(
                state,
                signal["symbol"],
                signal["direction"],
            )

            state = load_state()
            time.sleep(1)

    if not selected:
        print("Yeni kaliteli swing sinyali yok.")

        if should_send_no_signal_report(state):
            report = build_no_signal_report(
                scanned_count=scanned,
                new_signal_count=len(all_signals),
                long_counter=long_reasons,
                short_counter=short_reasons,
                top_candidates=top_candidates,
            )

            send_telegram(report)
            mark_no_signal_report_sent(state)

    print(BOT_NAME, "tamamlandı.")


if __name__ == "__main__":
    main()
