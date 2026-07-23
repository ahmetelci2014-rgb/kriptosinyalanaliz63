# pump_radar.py
# Erken Pump/Dump Radar v2 - Dengeli Canlı Para + Sessiz Trend Gözlemi
#
# OKX USDT perpetual futures paritelerini tarar.
# Emir açmaz; Telegram uyarısı gönderir ve TP/SL takibi yapar.
#
# Bu sürümün amacı:
# 1) Aşırı satımda geç SHORT ve aşırı alımda geç LONG sinyallerini azaltmak.
# 2) 1M hacim patlamasının tek başına sinyal üretmesini engellemek.
# 3) 5M hacim ve gerçek momentum şartlarını zorunlu yapmak.
# 4) Çok geniş stoplu ve girişten uzaklaşmış adayları elemek.
# 5) Eski pump_radar_state.json yapısıyla uyumlu çalışmak.
# 6) XLM benzeri büyük ama hacim patlamasız hareketleri sessizce ölçmek.

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

BOT_NAME = "Erken Pump/Dump Radar v2 - Dengeli Canlı Para"

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "pump_radar_state.json"
TR_TIMEZONE = timezone(timedelta(hours=3))

# Hacmi yüksek uygun OKX USDT futures pariteleri taranır.
MAX_SCAN_COINS = 300
MIN_24H_QUOTE_VOLUME = 1_000_000

# Bir anda çok sayıda yüksek riskli radar işlemi birikmesin.
MAX_NEW_SIGNALS_PER_RUN = 1
MAX_OPEN_SIGNALS = 2

DUPLICATE_SECONDS = 2 * 60 * 60

TRACK_TIMEFRAME = "1m"
TRACK_LIMIT = 240
MAX_OPEN_SIGNAL_MINUTES = 240

SEND_NO_SIGNAL_REPORT = True
NO_SIGNAL_REPORT_EVERY_MINUTES = 30
TOP_NEAR_CANDIDATES = 8

# Gönderim anında fiyat eski girişten fazla uzaklaştıysa sinyal iptal edilir.
MAX_ENTRY_DRIFT_PERCENT = 0.25


# =========================================================
# TP / SL / RİSK
# =========================================================

TP1_R = 0.75
TP2_R = 1.35
TP3_R = 2.00

SL_BUFFER_PERCENT = 0.08

MIN_RISK_PERCENT = 0.25
MAX_RISK_PERCENT = 1.50

MIN_SCORE = 84


# =========================================================
# HAREKET / HACİM / RSI FİLTRELERİ
# =========================================================

# Erken hareket için 1M ve 15M yön şartı.
MIN_1M_MOVE = 0.12
MIN_5M_MOVE = 0.35
MIN_15M_MOVE = 0.15

# 1M ve 5M hacim ayrı ayrı zorunludur.
MIN_1M_VOLUME_RATIO = 1.50
MIN_5M_VOLUME_RATIO = 1.15

BREAKOUT_LOOKBACK_5M = 24
BREAKOUT_TOLERANCE_PERCENT = 0.08

PUMP_MIN_CLOSE_POWER_1M = 58
PUMP_MIN_CLOSE_POWER_5M = 52

DUMP_MAX_CLOSE_POWER_1M = 42
DUMP_MAX_CLOSE_POWER_5M = 48

# Geç kalmış hareketleri engeller.
# LONG: RSI 72 üzerindeyse aşırı alım riski.
# SHORT: RSI 34 altındaysa aşırı satım / tepki riski.
PUMP_RSI_5M_MIN = 45
PUMP_RSI_5M_MAX = 72

DUMP_RSI_5M_MIN = 34
DUMP_RSI_5M_MAX = 56

# =========================================================
# SESSİZ TREND DEVAM GÖZLEMİ
# =========================================================
# Bu mod yeni Telegram işlem sinyali göndermez.
# XLM benzeri, hacim patlaması olmadan devam eden büyük hareketleri
# pump_radar_state.json içinde kaydeder.
SHADOW_TREND_ENABLED = True

# Son 15 veya 30 dakikadaki minimum hareket.
SHADOW_MIN_15M_MOVE_PERCENT = 0.60
SHADOW_MIN_30M_MOVE_PERCENT = 0.90

# Aynı coin/yön için yeni gözlem kaydı aralığı.
SHADOW_DUPLICATE_MINUTES = 30

# State dosyasında en fazla kaç kayıt saklansın.
SHADOW_MAX_RECORDS = 300
SHADOW_KEEP_DAYS = 7

# Trend devam kalitesi için yumuşak ama ölçülebilir eşikler.
SHADOW_MIN_5M_VOLUME_RATIO = 0.70
SHADOW_MAX_EMA20_DISTANCE_PERCENT = 0.55
SHADOW_LONG_RSI_MIN = 50
SHADOW_LONG_RSI_MAX = 74
SHADOW_SHORT_RSI_MIN = 26
SHADOW_SHORT_RSI_MAX = 50


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

    if not value:
        return value

    if not value.endswith("USDT"):
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


def default_state():
    return {
        "open_signals": {},
        "open_pump_signals": {},
        "last_sent": {},
        "last_no_signal_report": 0,
        "stats": empty_stats(),
        "shadow_moves": [],
        "shadow_last_seen": {},
        "shadow_stats": {
            "recorded": 0,
            "ready": 0,
            "not_ready": 0,
        },
    }


def load_state():
    try:
        if not os.path.exists(STATE_FILE):
            return default_state()

        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            raw = handle.read().strip()

        if not raw:
            return default_state()

        state = json.loads(raw)

        if not isinstance(state, dict):
            state = default_state()

        state.setdefault("open_signals", {})
        state.setdefault("open_pump_signals", {})

        if (
            state.get("open_pump_signals")
            and not state.get("open_signals")
        ):
            state["open_signals"] = state["open_pump_signals"]

        state.setdefault("last_sent", {})
        state.setdefault("last_no_signal_report", 0)
        state.setdefault("stats", {})
        state.setdefault("shadow_moves", [])
        state.setdefault("shadow_last_seen", {})
        state.setdefault("shadow_stats", {})

        for key, value in {
            "recorded": 0,
            "ready": 0,
            "not_ready": 0,
        }.items():
            state["shadow_stats"].setdefault(key, value)

        for key, value in empty_stats().items():
            state["stats"].setdefault(key, value)

        # Eski state kayıtlarını yeni yapıya dönüştür.
        migrated = {}

        for old_key, signal in state["open_signals"].items():
            if not isinstance(signal, dict):
                continue

            item = dict(signal)
            item["symbol"] = normalize_bot_symbol(
                item.get("symbol")
            )

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
                f"{item.get('source', 'PUMP_DUMP')}"
            )

            migrated[new_key or old_key] = item

        state["open_signals"] = migrated
        state["open_pump_signals"] = migrated

        return state

    except Exception as exc:
        print("State okuma hatası:", exc)
        return default_state()


def save_state(state):
    try:
        state["open_pump_signals"] = state.get(
            "open_signals",
            {},
        )

        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(
                state,
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
    state["stats"][key] = int(
        state["stats"].get(key, 0)
    ) + 1


# =========================================================
# OKX / VERİ
# =========================================================

def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {
            "defaultType": "swap",
        },
    })


def to_okx_symbol(symbol):
    bot_symbol = normalize_bot_symbol(symbol)
    base = (
        bot_symbol[:-4]
        if bot_symbol.endswith("USDT")
        else bot_symbol
    )
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

        for key in (
            "volCcy24h",
            "volUsd24h",
            "vol24h",
        ):
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

            if (
                not okx_symbol
                or "/USDT:USDT" not in okx_symbol
            ):
                continue

            base = str(
                market.get("base", "")
            ).upper()

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

            rows.append((
                okx_symbol_to_bot_symbol(okx_symbol),
                volume,
            ))

        rows.sort(
            key=lambda row: row[1],
            reverse=True,
        )

        coins = [
            symbol
            for symbol, _ in rows[:MAX_SCAN_COINS]
        ]

        print("Taranacak coin sayısı:", len(coins))
        print("İlk 20 coin:", coins[:20])

        return coins

    except Exception as exc:
        print("Coin tarama hatası:", exc)
        return []


def fetch_df(
    exchange,
    symbol,
    timeframe,
    limit=120,
    min_len=40,
):
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
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

        for column in (
            "open",
            "high",
            "low",
            "close",
            "volume",
        ):
            frame[column] = pd.to_numeric(
                frame[column],
                errors="coerce",
            )

        frame = frame.dropna().reset_index(drop=True)

        if len(frame) < min_len:
            return None

        return frame

    except Exception as exc:
        print(
            symbol,
            timeframe,
            "veri hatası:",
            exc,
        )
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
            since=max(
                0,
                int(since_seconds),
            ) * 1000,
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
        print(
            symbol,
            "mum takip hatası:",
            exc,
        )
        return []


def get_current_price(exchange, symbol):
    try:
        ticker = exchange.fetch_ticker(
            to_okx_symbol(symbol)
        )
        value = ticker.get("last")

        return (
            float(value)
            if value is not None
            else None
        )

    except Exception as exc:
        print(
            symbol,
            "güncel fiyat hatası:",
            exc,
        )
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


def percent_distance(current, reference):
    current = safe_float(current)
    reference = safe_float(reference)

    if reference <= 0:
        return 999.0

    return (
        abs(current - reference)
        / reference
        * 100
    )


def calc_rsi(series, period=14):
    delta = series.diff()

    gain = delta.where(
        delta > 0,
        0.0,
    )

    loss = -delta.where(
        delta < 0,
        0.0,
    )

    average_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    average_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    rs = average_gain / average_loss.replace(
        0,
        0.0000001,
    )

    return 100 - (100 / (1 + rs))


def volume_ratio(frame, index=-2, period=20):
    try:
        average = frame["volume"].rolling(
            period
        ).mean().iloc[index]

        volume = frame["volume"].iloc[index]

        if average <= 0 or math.isnan(average):
            return 0.0

        return float(volume / average)

    except Exception:
        return 0.0


def candle_move_percent(row):
    open_price = safe_float(row["open"])
    close_price = safe_float(row["close"])

    if open_price <= 0:
        return 0.0

    return (
        (close_price - open_price)
        / open_price
        * 100
    )


def close_power_percent(row):
    high = safe_float(row["high"])
    low = safe_float(row["low"])
    close_price = safe_float(row["close"])

    candle_range = high - low

    if candle_range <= 0:
        return 50.0

    return (
        (close_price - low)
        / candle_range
        * 100
    )


def upper_wick_percent(row):
    high = safe_float(row["high"])
    low = safe_float(row["low"])
    open_price = safe_float(row["open"])
    close_price = safe_float(row["close"])

    candle_range = high - low

    if candle_range <= 0:
        return 0.0

    wick = high - max(
        open_price,
        close_price,
    )

    return max(
        0.0,
        wick / candle_range * 100,
    )


def lower_wick_percent(row):
    high = safe_float(row["high"])
    low = safe_float(row["low"])
    open_price = safe_float(row["open"])
    close_price = safe_float(row["close"])

    candle_range = high - low

    if candle_range <= 0:
        return 0.0

    wick = min(
        open_price,
        close_price,
    ) - low

    return max(
        0.0,
        wick / candle_range * 100,
    )


def recent_resistance(frame):
    try:
        if len(frame) < BREAKOUT_LOOKBACK_5M + 5:
            return None

        return float(
            frame["high"].iloc[
                -BREAKOUT_LOOKBACK_5M - 2:-2
            ].max()
        )

    except Exception:
        return None


def recent_support(frame):
    try:
        if len(frame) < BREAKOUT_LOOKBACK_5M + 5:
            return None

        return float(
            frame["low"].iloc[
                -BREAKOUT_LOOKBACK_5M - 2:-2
            ].min()
        )

    except Exception:
        return None


def condition(label, ok):
    return {
        "label": label,
        "ok": bool(ok),
    }


def missing_reasons(conditions):
    return [
        item["label"]
        for item in conditions
        if not item["ok"]
    ]


def score_from_conditions(conditions, bonus=0):
    ok_count = sum(
        1
        for item in conditions
        if item["ok"]
    )

    total = max(
        1,
        len(conditions),
    )

    score = int(
        ok_count / total * 100
    ) + int(bonus)

    return (
        max(0, min(100, score)),
        ok_count,
        total,
    )


# =========================================================
# TEKRAR / AÇIK SİNYAL
# =========================================================

def duplicate_key(symbol, direction):
    return (
        f"{normalize_bot_symbol(symbol)}_"
        f"{direction}"
    )


def is_recent_duplicate(state, symbol, direction):
    last_time = int(
        state.get(
            "last_sent",
            {},
        ).get(
            duplicate_key(symbol, direction),
            0,
        )
    )

    return (
        now_ts() - last_time
        < DUPLICATE_SECONDS
    )


def mark_sent(state, symbol, direction):
    state.setdefault("last_sent", {})

    state["last_sent"][
        duplicate_key(symbol, direction)
    ] = now_ts()

    cutoff = now_ts() - 24 * 60 * 60

    state["last_sent"] = {
        key: value
        for key, value
        in state["last_sent"].items()
        if int(value) >= cutoff
    }

    save_state(state)


def has_open_same_symbol(state, symbol):
    symbol = normalize_bot_symbol(symbol)

    return any(
        normalize_bot_symbol(
            signal.get("symbol")
        ) == symbol
        for signal
        in state.get(
            "open_signals",
            {},
        ).values()
    )


# =========================================================
# MESAJ
# =========================================================

def build_signal_message(signal):
    icon = (
        "🟢"
        if signal["direction"] == "LONG"
        else "🔴"
    )

    return (
        f"🚨 ERKEN PUMP/DUMP RADAR v2\n\n"
        f"{icon} {signal['direction']}\n"
        f"🟡 Coin: {signal['symbol']}\n"
        f"⏱️ Kaynak: {signal['source']}\n"
        f"📌 Kurulum: {signal['setup_name']}\n\n"
        f"📌 Giriş: {format_price(signal['entry'])}\n"
        f"🎯 TP1: {format_price(signal['tp1'])}\n"
        f"🎯 TP2: {format_price(signal['tp2'])}\n"
        f"🎯 TP3: {format_price(signal['tp3'])}\n"
        f"🛑 SL: {format_price(signal['sl'])}\n\n"
        f"📊 Skor: %{signal['score']}\n"
        f"🛡️ Stop Mesafesi: "
        f"%{round(signal['risk_percent'], 3)}\n\n"
        f"📊 Radar Verileri:\n"
        f"• 1M Hareket: "
        f"%{round(signal['move1'], 2)}\n"
        f"• 5M Hareket: "
        f"%{round(signal['move5'], 2)}\n"
        f"• 15M Hareket: "
        f"%{round(signal['move15'], 2)}\n"
        f"• 1M Hacim: "
        f"{round(signal['vol1'], 2)}x\n"
        f"• 5M Hacim: "
        f"{round(signal['vol5'], 2)}x\n"
        f"• 5M RSI: "
        f"{round(signal['rsi5'], 2)}\n"
        f"• 1M Kapanış Gücü: "
        f"%{round(signal['close_power1'], 1)}\n"
        f"• 5M Kapanış Gücü: "
        f"%{round(signal['close_power5'], 1)}\n"
        f"• Kırılım Seviyesi: "
        f"{format_price(signal['break_level'])}\n\n"
        f"📌 İşlem Kuralı:\n"
        f"• Erken pump/dump radarıdır; "
        f"ana MTF sinyali değildir.\n"
        f"• Girişten %{MAX_ENTRY_DRIFT_PERCENT} "
        f"fazla uzaklaştıysa girme.\n"
        f"• TP1 gelirse %50 kâr al, "
        f"SL girişe çek.\n"
        f"• Stop mutlaka girilmeli.\n"
        f"• Marjin: Isolated.\n"
        f"• Kaldıraç düşük tutulmalı.\n\n"
        f"⚠️ Finansal tavsiye değildir. "
        f"Grafikte kontrol etmeden işlem açma."
    )


# =========================================================
# SİNYAL ÜRETİMİ
# =========================================================

def make_targets(direction, entry, sl):
    if direction == "LONG":
        risk = entry - sl

        if risk <= 0:
            return None

        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R

    else:
        risk = sl - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

        if min(tp1, tp2, tp3) <= 0:
            return None

    risk_percent = risk / entry * 100

    if not (
        MIN_RISK_PERCENT
        <= risk_percent
        <= MAX_RISK_PERCENT
    ):
        return None

    return {
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_percent": risk_percent,
    }


def build_long_signal(
    symbol,
    current_price,
    df1,
    df5,
    df15,
):
    try:
        frame1 = df1.copy()
        frame5 = df5.copy()

        frame5["rsi"] = calc_rsi(
            frame5["close"]
        )

        candle1 = frame1.iloc[-2]
        candle5 = frame5.iloc[-2]
        candle15 = df15.iloc[-2]

        entry = float(current_price)

        if entry <= 0:
            return None, None

        move1 = candle_move_percent(candle1)
        move5 = candle_move_percent(candle5)
        move15 = candle_move_percent(candle15)

        vol1 = volume_ratio(
            frame1,
            index=-2,
            period=20,
        )

        vol5 = volume_ratio(
            frame5,
            index=-2,
            period=20,
        )

        rsi5 = float(
            frame5["rsi"].iloc[-2]
        )

        close_power1 = close_power_percent(
            candle1
        )

        close_power5 = close_power_percent(
            candle5
        )

        lower_wick1 = lower_wick_percent(
            candle1
        )

        resistance = recent_resistance(
            frame5
        )

        if resistance is None:
            return None, None

        breakout = (
            float(candle5["close"])
            >= resistance
            * (
                1
                - BREAKOUT_TOLERANCE_PERCENT
                / 100
            )
            or float(candle5["high"])
            >= resistance
            * (
                1
                - BREAKOUT_TOLERANCE_PERCENT
                / 100
            )
            or entry
            >= resistance
            * (
                1
                - BREAKOUT_TOLERANCE_PERCENT
                / 100
            )
        )

        raw_sl = min(
            float(candle1["low"]),
            float(candle5["low"]),
            resistance * 0.995,
        )

        sl = raw_sl * (
            1
            - SL_BUFFER_PERCENT
            / 100
        )

        targets = make_targets(
            "LONG",
            entry,
            sl,
        )

        risk_percent = (
            targets["risk_percent"]
            if targets
            else 999.0
        )

        conditions = [
            condition(
                "PUMP: 1M yeşil atak yetersiz",
                move1 >= MIN_1M_MOVE,
            ),
            condition(
                "PUMP: 5M hareket veya direnç kırılımı yok",
                (
                    move5 >= MIN_5M_MOVE and breakout
                ),
            ),
            condition(
                "PUMP: 15M yön desteği yetersiz",
                move15 >= MIN_15M_MOVE,
            ),
            condition(
                "PUMP: 1M hacim düşük",
                vol1 >= MIN_1M_VOLUME_RATIO,
            ),
            condition(
                "PUMP: 5M hacim düşük",
                vol5 >= MIN_5M_VOLUME_RATIO,
            ),
            condition(
                "PUMP: direnç kırılımı yok",
                breakout,
            ),
            condition(
                "PUMP: 1M kapanış gücü zayıf",
                close_power1
                >= PUMP_MIN_CLOSE_POWER_1M,
            ),
            condition(
                "PUMP: 5M kapanış gücü zayıf",
                close_power5
                >= PUMP_MIN_CLOSE_POWER_5M,
            ),
            condition(
                "PUMP: 5M RSI uygun değil",
                PUMP_RSI_5M_MIN
                <= rsi5
                <= PUMP_RSI_5M_MAX,
            ),
            condition(
                "PUMP: risk uygun değil",
                targets is not None,
            ),
        ]

        bonus = 0

        if vol1 >= 2.0:
            bonus += 3

        if vol5 >= 1.80:
            bonus += 3

        if move5 >= 0.80:
            bonus += 3

        if (
            close_power1 >= 72
            and close_power5 >= 62
        ):
            bonus += 2

        if lower_wick1 <= 15:
            bonus += 1

        score, ok_count, total = (
            score_from_conditions(
                conditions,
                bonus=bonus,
            )
        )

        hard_ok = (
            targets is not None
            and move1 >= MIN_1M_MOVE
            and (
                move5 >= MIN_5M_MOVE and breakout
            )
            and move15 >= MIN_15M_MOVE
            and vol1 >= MIN_1M_VOLUME_RATIO
            and vol5 >= MIN_5M_VOLUME_RATIO
            and close_power1
            >= PUMP_MIN_CLOSE_POWER_1M
            and close_power5
            >= PUMP_MIN_CLOSE_POWER_5M
            and PUMP_RSI_5M_MIN
            <= rsi5
            <= PUMP_RSI_5M_MAX
        )

        debug = {
            "symbol": symbol,
            "direction": "LONG",
            "score": score,
            "ok_count": ok_count,
            "total_conditions": total,
            "missing": missing_reasons(
                conditions
            ),
            "move1": move1,
            "move5": move5,
            "move15": move15,
            "vol1": vol1,
            "vol5": vol5,
            "rsi5": rsi5,
            "risk_percent": risk_percent,
        }

        signal = None

        if (
            score >= MIN_SCORE
            and hard_ok
        ):
            signal = {
                "symbol": normalize_bot_symbol(
                    symbol
                ),
                "direction": "LONG",
                "source": "ERKEN_PUMP",
                "setup_name": (
                    "Filtreli Erken Pump LONG"
                ),
                "entry": entry,
                "tp1": targets["tp1"],
                "tp2": targets["tp2"],
                "tp3": targets["tp3"],
                "sl": sl,
                "score": score,
                "risk_percent": (
                    targets["risk_percent"]
                ),
                "move1": move1,
                "move5": move5,
                "move15": move15,
                "vol1": vol1,
                "vol5": vol5,
                "rsi5": rsi5,
                "close_power1": close_power1,
                "close_power5": close_power5,
                "break_level": resistance,
                "ok_count": ok_count,
                "total_conditions": total,
                "missing": missing_reasons(
                    conditions
                ),
            }

            signal["message"] = (
                build_signal_message(signal)
            )

        return signal, debug

    except Exception as exc:
        print(
            symbol,
            "pump long analiz hatası:",
            exc,
        )
        return None, None


def build_short_signal(
    symbol,
    current_price,
    df1,
    df5,
    df15,
):
    try:
        frame1 = df1.copy()
        frame5 = df5.copy()

        frame5["rsi"] = calc_rsi(
            frame5["close"]
        )

        candle1 = frame1.iloc[-2]
        candle5 = frame5.iloc[-2]
        candle15 = df15.iloc[-2]

        entry = float(current_price)

        if entry <= 0:
            return None, None

        move1 = candle_move_percent(candle1)
        move5 = candle_move_percent(candle5)
        move15 = candle_move_percent(candle15)

        vol1 = volume_ratio(
            frame1,
            index=-2,
            period=20,
        )

        vol5 = volume_ratio(
            frame5,
            index=-2,
            period=20,
        )

        rsi5 = float(
            frame5["rsi"].iloc[-2]
        )

        close_power1 = close_power_percent(
            candle1
        )

        close_power5 = close_power_percent(
            candle5
        )

        upper_wick1 = upper_wick_percent(
            candle1
        )

        support = recent_support(
            frame5
        )

        if support is None:
            return None, None

        breakdown = (
            float(candle5["close"])
            <= support
            * (
                1
                + BREAKOUT_TOLERANCE_PERCENT
                / 100
            )
            or float(candle5["low"])
            <= support
            * (
                1
                + BREAKOUT_TOLERANCE_PERCENT
                / 100
            )
            or entry
            <= support
            * (
                1
                + BREAKOUT_TOLERANCE_PERCENT
                / 100
            )
        )

        raw_sl = max(
            float(candle1["high"]),
            float(candle5["high"]),
            support * 1.005,
        )

        sl = raw_sl * (
            1
            + SL_BUFFER_PERCENT
            / 100
        )

        targets = make_targets(
            "SHORT",
            entry,
            sl,
        )

        risk_percent = (
            targets["risk_percent"]
            if targets
            else 999.0
        )

        conditions = [
            condition(
                "DUMP: 1M kırmızı atak yetersiz",
                move1 <= -MIN_1M_MOVE,
            ),
            condition(
                "DUMP: 5M hareket veya destek kırılımı yok",
                (
                    move5 <= -MIN_5M_MOVE and breakdown
                ),
            ),
            condition(
                "DUMP: 15M yön desteği yetersiz",
                move15 <= -MIN_15M_MOVE,
            ),
            condition(
                "DUMP: 1M hacim düşük",
                vol1 >= MIN_1M_VOLUME_RATIO,
            ),
            condition(
                "DUMP: 5M hacim düşük",
                vol5 >= MIN_5M_VOLUME_RATIO,
            ),
            condition(
                "DUMP: destek kırılımı yok",
                breakdown,
            ),
            condition(
                "DUMP: 1M kapanış gücü zayıf",
                close_power1
                <= DUMP_MAX_CLOSE_POWER_1M,
            ),
            condition(
                "DUMP: 5M kapanış gücü zayıf",
                close_power5
                <= DUMP_MAX_CLOSE_POWER_5M,
            ),
            condition(
                "DUMP: 5M RSI uygun değil",
                DUMP_RSI_5M_MIN
                <= rsi5
                <= DUMP_RSI_5M_MAX,
            ),
            condition(
                "DUMP: risk uygun değil",
                targets is not None,
            ),
        ]

        bonus = 0

        if vol1 >= 2.0:
            bonus += 3

        if vol5 >= 1.80:
            bonus += 3

        if move5 <= -0.80:
            bonus += 3

        if (
            close_power1 <= 28
            and close_power5 <= 38
        ):
            bonus += 2

        if upper_wick1 <= 15:
            bonus += 1

        score, ok_count, total = (
            score_from_conditions(
                conditions,
                bonus=bonus,
            )
        )

        hard_ok = (
            targets is not None
            and move1 <= -MIN_1M_MOVE
            and (
                move5 <= -MIN_5M_MOVE and breakdown
            )
            and move15 <= -MIN_15M_MOVE
            and vol1 >= MIN_1M_VOLUME_RATIO
            and vol5 >= MIN_5M_VOLUME_RATIO
            and close_power1
            <= DUMP_MAX_CLOSE_POWER_1M
            and close_power5
            <= DUMP_MAX_CLOSE_POWER_5M
            and DUMP_RSI_5M_MIN
            <= rsi5
            <= DUMP_RSI_5M_MAX
        )

        debug = {
            "symbol": symbol,
            "direction": "SHORT",
            "score": score,
            "ok_count": ok_count,
            "total_conditions": total,
            "missing": missing_reasons(
                conditions
            ),
            "move1": move1,
            "move5": move5,
            "move15": move15,
            "vol1": vol1,
            "vol5": vol5,
            "rsi5": rsi5,
            "risk_percent": risk_percent,
        }

        signal = None

        if (
            score >= MIN_SCORE
            and hard_ok
        ):
            signal = {
                "symbol": normalize_bot_symbol(
                    symbol
                ),
                "direction": "SHORT",
                "source": "ERKEN_DUMP",
                "setup_name": (
                    "Filtreli Erken Dump SHORT"
                ),
                "entry": entry,
                "tp1": targets["tp1"],
                "tp2": targets["tp2"],
                "tp3": targets["tp3"],
                "sl": sl,
                "score": score,
                "risk_percent": (
                    targets["risk_percent"]
                ),
                "move1": move1,
                "move5": move5,
                "move15": move15,
                "vol1": vol1,
                "vol5": vol5,
                "rsi5": rsi5,
                "close_power1": close_power1,
                "close_power5": close_power5,
                "break_level": support,
                "ok_count": ok_count,
                "total_conditions": total,
                "missing": missing_reasons(
                    conditions
                ),
            }

            signal["message"] = (
                build_signal_message(signal)
            )

        return signal, debug

    except Exception as exc:
        print(
            symbol,
            "dump short analiz hatası:",
            exc,
        )
        return None, None



def ema_series(series, span):
    return series.ewm(
        span=span,
        adjust=False,
    ).mean()


def signed_move_percent(current, reference):
    current = safe_float(current)
    reference = safe_float(reference)

    if current <= 0 or reference <= 0:
        return 0.0

    return (
        (current - reference)
        / reference
        * 100
    )


def build_shadow_trend_events(
    symbol,
    current_price,
    frame1,
    frame5,
    long_debug,
    short_debug,
    real_signals,
):
    """
    Büyük ama mevcut ani Pump/Dump filtresini geçmeyen hareketleri
    sessizce kaydeder. Telegram sinyali üretmez.
    """
    if not SHADOW_TREND_ENABLED:
        return []

    try:
        data = frame5.copy()

        if len(data) < 55:
            return []

        data["ema20_shadow"] = ema_series(
            data["close"],
            20,
        )
        data["ema50_shadow"] = ema_series(
            data["close"],
            50,
        )
        data["rsi_shadow"] = calc_rsi(
            data["close"],
        )

        # Son satır oluşan mum; -2 son kapanmış 5M mumdur.
        last_index = len(data) - 2
        start_15_index = last_index - 3
        start_30_index = last_index - 6

        if start_30_index < 0:
            return []

        last = data.iloc[last_index]
        previous = data.iloc[last_index - 1]

        close_now = safe_float(last["close"])
        close_15_ago = safe_float(
            data.iloc[start_15_index]["close"]
        )
        close_30_ago = safe_float(
            data.iloc[start_30_index]["close"]
        )

        move15_window = signed_move_percent(
            close_now,
            close_15_ago,
        )
        move30_window = signed_move_percent(
            close_now,
            close_30_ago,
        )

        last_four = data.iloc[
            last_index - 3:last_index + 1
        ]

        green_count = int(
            (
                last_four["close"]
                > last_four["open"]
            ).sum()
        )
        red_count = int(
            (
                last_four["close"]
                < last_four["open"]
            ).sum()
        )

        ema20_now = safe_float(
            last["ema20_shadow"]
        )
        ema50_now = safe_float(
            last["ema50_shadow"]
        )
        ema20_old = safe_float(
            data.iloc[last_index - 3][
                "ema20_shadow"
            ]
        )

        ema20_slope = signed_move_percent(
            ema20_now,
            ema20_old,
        )
        ema_distance = percent_distance(
            current_price,
            ema20_now,
        )

        rsi5 = safe_float(
            last["rsi_shadow"]
        )
        vol5 = volume_ratio(
            data,
            index=-2,
            period=20,
        )
        vol1 = volume_ratio(
            frame1,
            index=-2,
            period=20,
        )

        last_green = (
            safe_float(last["close"])
            > safe_float(last["open"])
        )
        last_red = (
            safe_float(last["close"])
            < safe_float(last["open"])
        )
        previous_green = (
            safe_float(previous["close"])
            > safe_float(previous["open"])
        )
        previous_red = (
            safe_float(previous["close"])
            < safe_float(previous["open"])
        )

        previous_touched_ema = (
            safe_float(previous["low"])
            <= safe_float(
                previous["ema20_shadow"]
            ) * 1.003
            and safe_float(previous["high"])
            >= safe_float(
                previous["ema20_shadow"]
            ) * 0.997
        )

        long_resume = (
            last_green
            and safe_float(last["close"])
            >= safe_float(previous["high"])
            and (
                previous_red
                or previous_touched_ema
            )
        )
        short_resume = (
            last_red
            and safe_float(last["close"])
            <= safe_float(previous["low"])
            and (
                previous_green
                or previous_touched_ema
            )
        )

        real_directions = {
            str(item.get("direction", ""))
            for item in real_signals
        }

        events = []

        long_big_move = (
            move15_window
            >= SHADOW_MIN_15M_MOVE_PERCENT
            or move30_window
            >= SHADOW_MIN_30M_MOVE_PERCENT
        )
        short_big_move = (
            move15_window
            <= -SHADOW_MIN_15M_MOVE_PERCENT
            or move30_window
            <= -SHADOW_MIN_30M_MOVE_PERCENT
        )

        if long_big_move and "LONG" not in real_directions:
            long_checks = {
                "EMA20 EMA50 üstünde değil": (
                    ema20_now > ema50_now
                ),
                "EMA20 eğimi yukarı değil": (
                    ema20_slope >= 0.03
                ),
                "Son dört 5M mumun üçü yeşil değil": (
                    green_count >= 3
                ),
                "İlk geri çekilme sonrası devam onayı yok": (
                    long_resume
                ),
                "5M RSI trend için uygun değil": (
                    SHADOW_LONG_RSI_MIN
                    <= rsi5
                    <= SHADOW_LONG_RSI_MAX
                ),
                "5M hacim çok düşük": (
                    vol5
                    >= SHADOW_MIN_5M_VOLUME_RATIO
                ),
                "Fiyat EMA20'den fazla uzak": (
                    ema_distance
                    <= SHADOW_MAX_EMA20_DISTANCE_PERCENT
                ),
            }

            long_ready = all(
                long_checks.values()
            )

            events.append({
                "recorded_at": now_ts(),
                "time_tr": tr_now_text(),
                "symbol": symbol,
                "direction": "LONG",
                "source": "SHADOW_TREND_CONTINUATION",
                "shadow_ready": long_ready,
                "move15_percent": round(
                    move15_window,
                    4,
                ),
                "move30_percent": round(
                    move30_window,
                    4,
                ),
                "price": safe_float(
                    current_price,
                ),
                "ema20": ema20_now,
                "ema50": ema50_now,
                "ema20_slope_percent": round(
                    ema20_slope,
                    4,
                ),
                "ema20_distance_percent": round(
                    ema_distance,
                    4,
                ),
                "green_5m_count": green_count,
                "red_5m_count": red_count,
                "resume_confirmed": long_resume,
                "rsi5": round(rsi5, 4),
                "vol1": round(vol1, 4),
                "vol5": round(vol5, 4),
                "existing_filter_missing": (
                    list(
                        (long_debug or {}).get(
                            "missing",
                            [],
                        )
                    )[:6]
                ),
                "trend_missing": [
                    label
                    for label, ok
                    in long_checks.items()
                    if not ok
                ],
            })

        if short_big_move and "SHORT" not in real_directions:
            short_checks = {
                "EMA20 EMA50 altında değil": (
                    ema20_now < ema50_now
                ),
                "EMA20 eğimi aşağı değil": (
                    ema20_slope <= -0.03
                ),
                "Son dört 5M mumun üçü kırmızı değil": (
                    red_count >= 3
                ),
                "İlk tepki sonrası devam onayı yok": (
                    short_resume
                ),
                "5M RSI trend için uygun değil": (
                    SHADOW_SHORT_RSI_MIN
                    <= rsi5
                    <= SHADOW_SHORT_RSI_MAX
                ),
                "5M hacim çok düşük": (
                    vol5
                    >= SHADOW_MIN_5M_VOLUME_RATIO
                ),
                "Fiyat EMA20'den fazla uzak": (
                    ema_distance
                    <= SHADOW_MAX_EMA20_DISTANCE_PERCENT
                ),
            }

            short_ready = all(
                short_checks.values()
            )

            events.append({
                "recorded_at": now_ts(),
                "time_tr": tr_now_text(),
                "symbol": symbol,
                "direction": "SHORT",
                "source": "SHADOW_TREND_CONTINUATION",
                "shadow_ready": short_ready,
                "move15_percent": round(
                    move15_window,
                    4,
                ),
                "move30_percent": round(
                    move30_window,
                    4,
                ),
                "price": safe_float(
                    current_price,
                ),
                "ema20": ema20_now,
                "ema50": ema50_now,
                "ema20_slope_percent": round(
                    ema20_slope,
                    4,
                ),
                "ema20_distance_percent": round(
                    ema_distance,
                    4,
                ),
                "green_5m_count": green_count,
                "red_5m_count": red_count,
                "resume_confirmed": short_resume,
                "rsi5": round(rsi5, 4),
                "vol1": round(vol1, 4),
                "vol5": round(vol5, 4),
                "existing_filter_missing": (
                    list(
                        (short_debug or {}).get(
                            "missing",
                            [],
                        )
                    )[:6]
                ),
                "trend_missing": [
                    label
                    for label, ok
                    in short_checks.items()
                    if not ok
                ],
            })

        return events

    except Exception as exc:
        print(
            symbol,
            "sessiz trend gözlem hatası:",
            exc,
        )
        return []


def save_shadow_events(state, events):
    if not SHADOW_TREND_ENABLED or not events:
        return 0

    state.setdefault("shadow_moves", [])
    state.setdefault("shadow_last_seen", {})
    state.setdefault("shadow_stats", {
        "recorded": 0,
        "ready": 0,
        "not_ready": 0,
    })

    cutoff = (
        now_ts()
        - SHADOW_KEEP_DAYS
        * 24
        * 60
        * 60
    )
    duplicate_seconds = (
        SHADOW_DUPLICATE_MINUTES
        * 60
    )

    cleaned = [
        item
        for item in state["shadow_moves"]
        if int(
            item.get("recorded_at", 0)
        ) >= cutoff
    ]

    added = 0

    for event in events:
        key = (
            f"{event['symbol']}_"
            f"{event['direction']}"
        )
        last_seen = int(
            state["shadow_last_seen"].get(
                key,
                0,
            )
        )

        if (
            now_ts() - last_seen
            < duplicate_seconds
        ):
            continue

        cleaned.append(event)
        state["shadow_last_seen"][key] = (
            now_ts()
        )

        state["shadow_stats"]["recorded"] = (
            int(
                state["shadow_stats"].get(
                    "recorded",
                    0,
                )
            )
            + 1
        )

        stat_key = (
            "ready"
            if event.get("shadow_ready")
            else "not_ready"
        )
        state["shadow_stats"][stat_key] = (
            int(
                state["shadow_stats"].get(
                    stat_key,
                    0,
                )
            )
            + 1
        )

        added += 1

    cleaned.sort(
        key=lambda item: int(
            item.get("recorded_at", 0)
        )
    )
    state["shadow_moves"] = (
        cleaned[-SHADOW_MAX_RECORDS:]
    )

    state["shadow_last_seen"] = {
        key: value
        for key, value
        in state["shadow_last_seen"].items()
        if int(value) >= cutoff
    }

    if added:
        save_state(state)

    return added


def analyze_symbol(exchange, symbol):
    current_price = get_current_price(
        exchange,
        symbol,
    )

    if current_price is None:
        return [], None, None, []

    frame1 = fetch_df(
        exchange,
        symbol,
        "1m",
        limit=100,
        min_len=60,
    )

    frame5 = fetch_df(
        exchange,
        symbol,
        "5m",
        limit=120,
        min_len=70,
    )

    frame15 = fetch_df(
        exchange,
        symbol,
        "15m",
        limit=90,
        min_len=50,
    )

    if (
        frame1 is None
        or frame5 is None
        or frame15 is None
    ):
        return [], None, None, []

    long_signal, long_debug = build_long_signal(
        symbol,
        current_price,
        frame1,
        frame5,
        frame15,
    )

    short_signal, short_debug = build_short_signal(
        symbol,
        current_price,
        frame1,
        frame5,
        frame15,
    )

    signals = []

    if long_signal is not None:
        signals.append(long_signal)

    if short_signal is not None:
        signals.append(short_signal)

    signals.sort(
        key=lambda item: (
            item["score"],
            -item["risk_percent"],
            item["vol5"],
            item["vol1"],
        ),
        reverse=True,
    )

    shadow_events = build_shadow_trend_events(
        symbol,
        current_price,
        frame1,
        frame5,
        long_debug,
        short_debug,
        signals,
    )

    return (
        signals[:1],
        long_debug,
        short_debug,
        shadow_events,
    )


# =========================================================
# AÇIK SİNYAL TAKİBİ
# =========================================================

def save_open_signal(state, signal):
    key = (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['source']}"
    )

    state.setdefault("open_signals", {})

    state["open_signals"][key] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "source": signal["source"],
        "setup_name": signal.get(
            "setup_name"
        ),
        "entry": signal["entry"],
        "tp1": signal["tp1"],
        "tp2": signal["tp2"],
        "tp3": signal["tp3"],
        "sl": signal["sl"],
        "score": signal["score"],
        "risk_percent": (
            signal["risk_percent"]
        ),
        "opened_at": now_ts(),
        "last_checked_at": now_ts(),
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "closed": False,
    }

    increment_stat(state, "signals")
    save_state(state)


def notify_tp1(
    state,
    signal_type,
    symbol,
    direction,
    entry,
    tp1,
):
    send_telegram(
        f"✅ {signal_type} TP1 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"TP1: {format_price(tp1)}\n"
        f"Öneri: %50 kâr al, SL girişe çek."
    )

    increment_stat(state, "tp1")


def notify_tp2(
    state,
    signal_type,
    symbol,
    direction,
    tp2,
):
    send_telegram(
        f"✅ {signal_type} TP2 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"TP2: {format_price(tp2)}"
    )

    increment_stat(state, "tp2")


def notify_tp3(
    state,
    signal_type,
    symbol,
    direction,
    tp3,
):
    send_telegram(
        f"🏁 {signal_type} TP3 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"TP3: {format_price(tp3)}\n"
        f"Sinyal maksimum hedefe ulaştı."
    )

    increment_stat(state, "tp3")


def notify_stop(
    state,
    signal_type,
    symbol,
    direction,
    entry,
    sl,
    close,
):
    send_telegram(
        f"❌ {signal_type} STOP OLDU\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"SL: {format_price(sl)}\n"
        f"Güncel: {format_price(close)}"
    )

    increment_stat(state, "stop")


def notify_breakeven(
    state,
    signal_type,
    symbol,
    direction,
    entry,
):
    send_telegram(
        f"🟡 {signal_type} KALAN "
        f"GİRİŞTEN KAPANDI\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}"
    )

    increment_stat(state, "breakeven")


def check_open_signals(exchange, state):
    open_signals = state.get(
        "open_signals",
        {},
    )

    if not open_signals:
        print("Açık pump/dump sinyali yok.")
        return

    updated = {}
    max_age = MAX_OPEN_SIGNAL_MINUTES * 60

    for key, signal in open_signals.items():
        try:
            symbol = normalize_bot_symbol(
                signal["symbol"]
            )

            direction = signal["direction"]

            entry = safe_float(
                signal["entry"]
            )

            tp1 = safe_float(
                signal["tp1"]
            )

            tp2 = safe_float(
                signal["tp2"]
            )

            tp3 = safe_float(
                signal["tp3"]
            )

            sl = safe_float(
                signal["sl"]
            )

            opened_at = int(
                signal.get("opened_at")
                or signal.get("created_ts")
                or now_ts()
            )

            last_checked_at = int(
                signal.get("last_checked_at")
                or opened_at
            )

            signal_type = (
                "PUMP"
                if direction == "LONG"
                else "DUMP"
            )

            if (
                signal.get("closed")
                or signal.get("tp3_hit")
            ):
                continue

            if (
                now_ts() - opened_at
                > max_age
                and not signal.get("tp1_hit")
            ):
                send_telegram(
                    f"⏳ PUMP/DUMP SİNYAL "
                    f"SÜRESİ DOLDU\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n\n"
                    f"{MAX_OPEN_SIGNAL_MINUTES} dakika "
                    f"içinde TP1 gelmediği için "
                    f"takipten çıkarıldı."
                )

                increment_stat(
                    state,
                    "expired",
                )

                continue

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                max(
                    opened_at,
                    last_checked_at - 120,
                ),
                TRACK_LIMIT,
            )

            if not candles:
                updated[key] = signal
                continue

            tp1_hit = bool(
                signal.get("tp1_hit", False)
            )

            tp2_hit = bool(
                signal.get("tp2_hit", False)
            )

            tp3_hit = bool(
                signal.get("tp3_hit", False)
            )

            closed = False

            for candle in candles:
                high = safe_float(
                    candle["high"]
                )

                low = safe_float(
                    candle["low"]
                )

                close = safe_float(
                    candle["close"]
                )

                just_hit_tp1 = False

                if direction == "LONG":
                    if not tp1_hit:
                        if low <= sl and high >= tp1:
                            if close >= entry:
                                tp1_hit = True
                                just_hit_tp1 = True

                                notify_tp1(
                                    state,
                                    signal_type,
                                    symbol,
                                    direction,
                                    entry,
                                    tp1,
                                )
                            else:
                                notify_stop(
                                    state,
                                    signal_type,
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
                                signal_type,
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
                                signal_type,
                                symbol,
                                direction,
                                entry,
                                tp1,
                            )

                    if (
                        tp1_hit
                        and not tp2_hit
                        and high >= tp2
                    ):
                        tp2_hit = True

                        notify_tp2(
                            state,
                            signal_type,
                            symbol,
                            direction,
                            tp2,
                        )

                    if (
                        tp1_hit
                        and not tp3_hit
                        and high >= tp3
                    ):
                        tp3_hit = True

                        notify_tp3(
                            state,
                            signal_type,
                            symbol,
                            direction,
                            tp3,
                        )

                        closed = True
                        break

                    # TP1 ilk görüldüğü aynı mumun eski düşüğü,
                    # yanlışlıkla breakeven sayılmaz.
                    if (
                        tp1_hit
                        and not just_hit_tp1
                        and low <= entry
                    ):
                        notify_breakeven(
                            state,
                            signal_type,
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
                                    signal_type,
                                    symbol,
                                    direction,
                                    entry,
                                    tp1,
                                )
                            else:
                                notify_stop(
                                    state,
                                    signal_type,
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
                                signal_type,
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
                                signal_type,
                                symbol,
                                direction,
                                entry,
                                tp1,
                            )

                    if (
                        tp1_hit
                        and not tp2_hit
                        and low <= tp2
                    ):
                        tp2_hit = True

                        notify_tp2(
                            state,
                            signal_type,
                            symbol,
                            direction,
                            tp2,
                        )

                    if (
                        tp1_hit
                        and not tp3_hit
                        and low <= tp3
                    ):
                        tp3_hit = True

                        notify_tp3(
                            state,
                            signal_type,
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
                            signal_type,
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
            print(
                key,
                "açık sinyal takip hatası:",
                exc,
            )

            updated[key] = signal

    state["open_signals"] = updated
    save_state(state)


# =========================================================
# RAPOR
# =========================================================

def top_reasons_text(counter, limit=6):
    if not counter:
        return "Veri yok"

    return "\n".join(
        f"• {reason}: {count}"
        for reason, count
        in counter.most_common(limit)
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
        f"{debug['symbol']} "
        f"{debug['direction']} | "
        f"şart "
        f"{debug['ok_count']}/"
        f"{debug['total_conditions']} | "
        f"skor {debug['score']} | "
        f"risk "
        f"%{round(debug.get('risk_percent', 0), 2)} | "
        f"eksik: {missing_text}"
    )


def build_no_signal_report(
    scanned_count,
    candidate_count,
    pump_counter,
    dump_counter,
    top_candidates,
):
    lines = [
        "🚨 ERKEN PUMP/DUMP RADAR v2 RAPORU",
        "",
        f"Bot: {BOT_NAME}",
        f"Zaman: {tr_now_text()}",
        f"Taranan coin: {scanned_count}",
        f"Filtreyi geçen aday: {candidate_count}",
        "",
        "PUMP tarafında en çok elenen:",
        top_reasons_text(pump_counter),
        "",
        "DUMP tarafında en çok elenen:",
        top_reasons_text(dump_counter),
        "",
        "Sinyale en yakın adaylar:",
    ]

    if top_candidates:
        for item in top_candidates[
            :TOP_NEAR_CANDIDATES
        ]:
            lines.append(
                "• " + candidate_line(item)
            )
    else:
        lines.append("• Yakın aday yok")

    lines.extend([
        "",
        "Not: Bu rapor işlem sinyali değildir. "
        "Kalite filtrelerinin neden sinyal "
        "üretmediğini gösterir.",
    ])

    return "\n".join(lines)


def should_send_no_signal_report(state):
    if not SEND_NO_SIGNAL_REPORT:
        return False

    last_report = int(
        state.get(
            "last_no_signal_report",
            0,
        )
    )

    return (
        now_ts() - last_report
        >= NO_SIGNAL_REPORT_EVERY_MINUTES
        * 60
    )


# =========================================================
# MAIN
# =========================================================

def main():
    print(BOT_NAME, "başladı.")

    state = load_state()
    exchange = get_exchange()

    check_open_signals(
        exchange,
        state,
    )

    state = load_state()
    scan_coins = get_scan_coins(exchange)

    open_count = len(
        state.get(
            "open_signals",
            {},
        )
    )

    available_slots = max(
        0,
        MAX_OPEN_SIGNALS - open_count,
    )

    print("Açık pump/dump:", open_count)
    print("Boş pump/dump slot:", available_slots)

    all_signals = []
    pump_counter = Counter()
    dump_counter = Counter()
    top_candidates = []
    shadow_events = []

    scanned = 0

    for symbol in scan_coins:
        try:
            scanned += 1

            if has_open_same_symbol(
                state,
                symbol,
            ):
                print(
                    symbol,
                    "zaten açık pump/dump var, atlandı.",
                )
                continue

            (
                signals,
                long_debug,
                short_debug,
                symbol_shadow_events,
            ) = analyze_symbol(
                exchange,
                symbol,
            )

            shadow_events.extend(
                symbol_shadow_events
            )

            if long_debug:
                for reason in long_debug.get(
                    "missing",
                    [],
                ):
                    pump_counter[reason] += 1

                top_candidates.append(
                    long_debug
                )

            if short_debug:
                for reason in short_debug.get(
                    "missing",
                    [],
                ):
                    dump_counter[reason] += 1

                top_candidates.append(
                    short_debug
                )

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
                "genel analiz hatası:",
                exc,
            )

    added_shadow_count = save_shadow_events(
        state,
        shadow_events,
    )

    ready_shadow_count = sum(
        1
        for item in shadow_events
        if item.get("shadow_ready")
    )

    print(
        "Sessiz büyük hareket gözlemi:",
        len(shadow_events),
        "| yeni kayıt:",
        added_shadow_count,
        "| trend devam hazır:",
        ready_shadow_count,
    )

    for event in sorted(
        shadow_events,
        key=lambda item: max(
            abs(
                safe_float(
                    item.get(
                        "move15_percent",
                        0,
                    )
                )
            ),
            abs(
                safe_float(
                    item.get(
                        "move30_percent",
                        0,
                    )
                )
            ),
        ),
        reverse=True,
    )[:5]:
        print(
            "SHADOW",
            event.get("symbol"),
            event.get("direction"),
            "15M:",
            event.get("move15_percent"),
            "30M:",
            event.get("move30_percent"),
            "hazır:",
            event.get("shadow_ready"),
            "eksik:",
            ", ".join(
                event.get(
                    "trend_missing",
                    [],
                )[:3]
            ),
        )

    all_signals.sort(
        key=lambda item: (
            item["score"],
            -item["risk_percent"],
            item["vol5"],
            item["vol1"],
        ),
        reverse=True,
    )

    top_candidates.sort(
        key=lambda item: (
            item.get("score", 0),
            item.get("ok_count", 0),
            -item.get("risk_percent", 999),
        ),
        reverse=True,
    )

    selected = []

    max_to_send = min(
        MAX_NEW_SIGNALS_PER_RUN,
        available_slots,
    )

    for signal in all_signals:
        if len(selected) >= max_to_send:
            break

        current_price = get_current_price(
            exchange,
            signal["symbol"],
        )

        if current_price is None:
            continue

        drift = percent_distance(
            current_price,
            signal["entry"],
        )

        if drift > MAX_ENTRY_DRIFT_PERCENT:
            print(
                signal["symbol"],
                "girişten uzaklaştı:",
                round(drift, 3),
                "%",
            )
            continue

        signal["current_price"] = current_price
        signal["entry_drift_percent"] = drift
        selected.append(signal)

    print(
        "Bulunan kaliteli pump/dump sinyal:",
        len(all_signals),
    )

    print(
        "Gönderilecek pump/dump sinyal:",
        len(selected),
    )

    if selected:
        send_telegram(
            f"🚨 {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {scanned}\n"
            f"Kaliteli aday: {len(all_signals)}\n"
            f"Açık pump/dump: "
            f"{open_count}/{MAX_OPEN_SIGNALS}\n"
            f"Gönderilecek sinyal: "
            f"{len(selected)}"
        )

    for signal in selected:
        extra = (
            f"\n💰 Güncel Fiyat: "
            f"{format_price(signal['current_price'])}\n"
            f"📏 Giriş Sapması: "
            f"%{round(signal['entry_drift_percent'], 3)}\n"
            f"📌 Son Kontrol: Girişe yakın ✅"
        )

        if send_telegram(
            signal["message"] + extra
        ):
            save_open_signal(
                state,
                signal,
            )

            mark_sent(
                state,
                signal["symbol"],
                signal["direction"],
            )

            state = load_state()
            time.sleep(1)

    if not selected:
        print(
            "Yeni kaliteli pump/dump sinyali yok."
        )

        if should_send_no_signal_report(state):
            send_telegram(
                build_no_signal_report(
                    scanned,
                    len(all_signals),
                    pump_counter,
                    dump_counter,
                    top_candidates,
                )
            )

            state["last_no_signal_report"] = (
                now_ts()
            )

            save_state(state)

    print(BOT_NAME, "tamamlandı.")


if __name__ == "__main__":
    main()
