# scalp_radar.py
# Hızlı Scalp Radar v2 - Dengeli Canlı Para
#
# Ana MTF, Swing ve Pump/Dump sistemlerinden tamamen ayrı çalışır.
# OKX USDT perpetual futures paritelerini tarar.
# Emir açmaz; yalnızca Telegram sinyali gönderir ve kendi state dosyasında takip eder.
#
# Temel amaç:
# 1) Aşırı satımda geç SHORT ve aşırı alımda geç LONG sinyallerini azaltmak.
# 2) Düşük hacimli ve geniş stoplu scalp işlemlerini elemek.
# 3) Aynı anda çok fazla hızlı işlem birikmesini önlemek.
# 4) Eski state kayıtlarıyla uyumluluğu korumak.

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

BOT_NAME = "Hızlı Scalp Radar v2 - Dengeli Canlı Para"

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "scalp_radar_state.json"
TR_TIMEZONE = timezone(timedelta(hours=3))

# Hacmi en yüksek uygun OKX USDT futures pariteleri.
MAX_SCAN_COINS = 300
MIN_24H_QUOTE_VOLUME = 500_000

# Aynı anda işlem yığılmasını önler.
MAX_NEW_SIGNALS_PER_RUN = 1
MAX_OPEN_SCALP_SIGNALS = 2

DUPLICATE_SECONDS = 120 * 60
MAX_OPEN_SIGNAL_MINUTES = 180

TRACK_TIMEFRAME = "1m"
TRACK_LIMIT = 180

SEND_NO_SIGNAL_REPORT = True
NO_SIGNAL_REPORT_EVERY_MINUTES = 30

# Sinyal gönderilene kadar fiyat çok uzaklaşmışsa iptal edilir.
MAX_ENTRY_DRIFT_PERCENT = 0.25


# =========================================================
# TP / SL / RİSK
# =========================================================

TP1_R = 0.65
TP2_R = 1.15
TP3_R = 1.70

SL_BUFFER_PERCENT = 0.08

MIN_RISK_PERCENT = 0.20
MAX_RISK_PERCENT = 1.25

MIN_SCORE = 84


# =========================================================
# TEPKİ SCALP
# =========================================================

# LONG: hızlı düşüş sonrası teyitli yukarı tepki
REACTION_LONG_MIN_5M_DROP = 0.65
REACTION_LONG_MIN_15M_DROP = 0.25
REACTION_LONG_RSI_1M_MIN = 18
REACTION_LONG_RSI_1M_MAX = 42
REACTION_LONG_RSI_5M_MAX = 50

# SHORT: hızlı yükseliş sonrası teyitli aşağı red
REACTION_SHORT_MIN_5M_PUMP = 0.65
REACTION_SHORT_MIN_15M_PUMP = 0.25
REACTION_SHORT_RSI_1M_MIN = 58
REACTION_SHORT_RSI_1M_MAX = 84
REACTION_SHORT_RSI_5M_MIN = 52

REACTION_MIN_1M_VOLUME_RATIO = 1.40
REACTION_MIN_5M_VOLUME_RATIO = 1.10
REACTION_MIN_WICK_PERCENT = 28
REACTION_LONG_MIN_CLOSE_POWER = 48
REACTION_SHORT_MAX_CLOSE_POWER = 52


# =========================================================
# ATAK / MOMENTUM SCALP
# =========================================================

# LONG: yükseliş atağı; aşırı alımda geç LONG engellenir.
ATTACK_LONG_MIN_1M_MOVE = 0.12
ATTACK_LONG_MIN_5M_MOVE = 0.35
ATTACK_LONG_MIN_15M_MOVE = 0.15
ATTACK_LONG_RSI_1M_MIN = 50
ATTACK_LONG_RSI_1M_MAX = 70
ATTACK_LONG_RSI_5M_MIN = 48
ATTACK_LONG_RSI_5M_MAX = 68
ATTACK_LONG_MIN_CLOSE_POWER = 62

# SHORT: düşüş atağı; aşırı satımda geç SHORT engellenir.
# Eski alt sınırlar 22 / 24 olduğu için RSI 25 civarında SHORT gelebiliyordu.
ATTACK_SHORT_MIN_1M_MOVE = 0.12
ATTACK_SHORT_MIN_5M_MOVE = 0.35
ATTACK_SHORT_MIN_15M_MOVE = 0.15
ATTACK_SHORT_RSI_1M_MIN = 34
ATTACK_SHORT_RSI_1M_MAX = 50
ATTACK_SHORT_RSI_5M_MIN = 38
ATTACK_SHORT_RSI_5M_MAX = 52
ATTACK_SHORT_MAX_CLOSE_POWER = 38

# Atak sinyalinde hem 1M hem 5M hacim şartı zorunludur.
ATTACK_MIN_1M_VOLUME_RATIO = 1.50
ATTACK_MIN_5M_VOLUME_RATIO = 1.15

ATTACK_BREAKOUT_LOOKBACK_1M = 20
ATTACK_BREAKOUT_LOOKBACK_5M = 12


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
            data={"chat_id": CHAT_ID, "text": message},
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


def empty_state():
    return {
        "open_scalp_signals": {},
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
            state = {}

        state.setdefault("open_scalp_signals", {})
        state.setdefault("last_sent", {})
        state.setdefault("last_no_signal_report", 0)
        state.setdefault("stats", {})

        for key, value in empty_stats().items():
            state["stats"].setdefault(key, value)

        # Eski sürümlerde created_ts kullanılmış olabilir.
        migrated = {}
        for old_key, signal in state["open_scalp_signals"].items():
            if not isinstance(signal, dict):
                continue

            signal = dict(signal)
            signal["symbol"] = normalize_bot_symbol(signal.get("symbol"))

            opened_at = int(
                signal.get("opened_at")
                or signal.get("created_ts")
                or now_ts()
            )
            signal["opened_at"] = opened_at
            signal["last_checked_at"] = int(
                signal.get("last_checked_at") or opened_at
            )

            signal.setdefault("tp1_hit", False)
            signal.setdefault("tp2_hit", False)
            signal.setdefault("tp3_hit", False)
            signal.setdefault("closed", False)

            new_key = (
                f"{signal.get('symbol', '')}_"
                f"{signal.get('direction', '')}_"
                f"{signal.get('source', 'SCALP')}"
            )
            migrated[new_key or old_key] = signal

        state["open_scalp_signals"] = migrated
        return state

    except Exception as exc:
        print("State okuma hatası:", exc)
        return empty_state()


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, ensure_ascii=False)
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
            "USDT", "USDC", "DAI", "FDUSD",
            "TUSD", "USDP", "USD",
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
            if volume < MIN_24H_QUOTE_VOLUME:
                continue

            rows.append((
                okx_symbol_to_bot_symbol(okx_symbol),
                volume,
            ))

        rows.sort(key=lambda item: item[1], reverse=True)
        coins = [symbol for symbol, _ in rows[:MAX_SCAN_COINS]]

        print("Taranacak coin sayısı:", len(coins))
        print("İlk 20 coin:", coins[:20])
        return coins

    except Exception as exc:
        print("Coin tarama hatası:", exc)
        return []


def fetch_df(exchange, symbol, timeframe, limit=120, min_len=40):
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
    limit=180,
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
        value = ticker.get("last")
        return float(value) if value is not None else None
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


def percent_distance(current, reference):
    current = safe_float(current)
    reference = safe_float(reference)
    if reference <= 0:
        return 999.0
    return abs(current - reference) / reference * 100


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()
    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss.replace(0, 0.0000001)
    return 100 - (100 / (1 + rs))


def volume_ratio(frame, index=-2, period=20):
    try:
        average = frame["volume"].rolling(period).mean().iloc[index]
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

    return ((close_price - open_price) / open_price) * 100


def lower_wick_percent(row):
    high = safe_float(row["high"])
    low = safe_float(row["low"])
    open_price = safe_float(row["open"])
    close_price = safe_float(row["close"])

    candle_range = high - low
    if candle_range <= 0:
        return 0.0

    wick = min(open_price, close_price) - low
    return max(0.0, wick / candle_range * 100)


def upper_wick_percent(row):
    high = safe_float(row["high"])
    low = safe_float(row["low"])
    open_price = safe_float(row["open"])
    close_price = safe_float(row["close"])

    candle_range = high - low
    if candle_range <= 0:
        return 0.0

    wick = high - max(open_price, close_price)
    return max(0.0, wick / candle_range * 100)


def close_power_percent(row):
    high = safe_float(row["high"])
    low = safe_float(row["low"])
    close_price = safe_float(row["close"])

    candle_range = high - low
    if candle_range <= 0:
        return 50.0

    return (close_price - low) / candle_range * 100


def rolling_previous_high(frame, lookback):
    try:
        start = max(0, len(frame) - lookback - 2)
        end = len(frame) - 2
        if end <= start:
            return None
        return float(frame["high"].iloc[start:end].max())
    except Exception:
        return None


def rolling_previous_low(frame, lookback):
    try:
        start = max(0, len(frame) - lookback - 2)
        end = len(frame) - 2
        if end <= start:
            return None
        return float(frame["low"].iloc[start:end].min())
    except Exception:
        return None


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
    state["last_sent"][duplicate_key(symbol, direction)] = now_ts()

    cutoff = now_ts() - 24 * 60 * 60
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
        for signal in state.get("open_scalp_signals", {}).values()
    )


# =========================================================
# SİNYAL YARDIMCILARI
# =========================================================

def build_condition_result(label, ok):
    return {"label": label, "ok": bool(ok)}


def score_from_conditions(conditions, bonus=0):
    ok_count = sum(1 for condition in conditions if condition["ok"])
    total = max(1, len(conditions))
    score = int(ok_count / total * 100) + int(bonus)
    return max(0, min(100, score)), ok_count, total


def missing_reasons(conditions):
    return [
        condition["label"]
        for condition in conditions
        if not condition["ok"]
    ]


def build_signal_message(signal):
    icon = "🟢" if signal["direction"] == "LONG" else "🔴"

    return (
        f"⚡ HIZLI SCALP RADAR v2\n\n"
        f"{icon} {signal['direction']}\n"
        f"🟡 Coin: {signal['symbol']}\n"
        f"⏱️ Kaynak: {signal['source']}\n"
        f"📌 Kurulum: {signal['setup']}\n\n"
        f"📌 Giriş: {format_price(signal['entry'])}\n"
        f"🎯 TP1: {format_price(signal['tp1'])}\n"
        f"🎯 TP2: {format_price(signal['tp2'])}\n"
        f"🎯 TP3: {format_price(signal['tp3'])}\n"
        f"🛑 SL: {format_price(signal['sl'])}\n\n"
        f"📊 Skor: %{signal['score']}\n"
        f"🛡️ Stop Mesafesi: %{round(signal['risk_percent'], 3)}\n\n"
        f"📊 Scalp Verileri:\n"
        f"• 1M RSI: {round(signal['rsi1'], 2)}\n"
        f"• 5M RSI: {round(signal['rsi5'], 2)}\n"
        f"• 1M Hacim: {round(signal['vol1'], 2)}x\n"
        f"• 5M Hacim: {round(signal['vol5'], 2)}x\n"
        f"• 1M Hareket: %{round(signal['move1'], 2)}\n"
        f"• 5M Hareket: %{round(signal['move5'], 2)}\n"
        f"• 15M Hareket: %{round(signal['move15'], 2)}\n"
        f"• Alt Fitil: %{round(signal['lower_wick'], 1)}\n"
        f"• Üst Fitil: %{round(signal['upper_wick'], 1)}\n"
        f"• Kapanış Gücü: %{round(signal['close_power'], 1)}\n\n"
        f"📌 İşlem Kuralı:\n"
        f"• Hızlı scalp sinyalidir; ana MTF sinyali değildir.\n"
        f"• Girişten %{MAX_ENTRY_DRIFT_PERCENT} fazla uzaklaştıysa girme.\n"
        f"• TP1 gelirse %50 kâr al, SL girişe çek.\n"
        f"• Stop mutlaka girilmeli.\n"
        f"• Marjin: Isolated.\n"
        f"• Kaldıraç düşük tutulmalı.\n\n"
        f"⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işlem açma."
    )


def make_signal(
    symbol,
    direction,
    source,
    setup,
    entry,
    sl,
    score,
    risk_percent,
    market_data,
    ok_count,
    total,
    missing,
):
    risk = entry - sl if direction == "LONG" else sl - entry

    if risk <= 0:
        return None

    if direction == "LONG":
        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R
    else:
        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

        if min(tp1, tp2, tp3) <= 0:
            return None

    signal = {
        "symbol": normalize_bot_symbol(symbol),
        "direction": direction,
        "source": source,
        "setup": setup,
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl,
        "score": score,
        "risk_percent": risk_percent,
        "ok_count": ok_count,
        "total_conditions": total,
        "missing": missing,
        **market_data,
    }

    signal["message"] = build_signal_message(signal)
    return signal


def build_debug(
    symbol,
    direction,
    setup,
    score,
    ok_count,
    total,
    missing,
    market_data,
    risk_percent,
):
    return {
        "symbol": normalize_bot_symbol(symbol),
        "direction": direction,
        "setup": setup,
        "score": score,
        "ok_count": ok_count,
        "total_conditions": total,
        "missing": missing,
        "risk_percent": risk_percent,
        **market_data,
    }


# =========================================================
# TEPKİ ANALİZİ
# =========================================================

def analyze_reaction_side(
    symbol,
    direction,
    df1,
    df5,
    df15,
    current_price,
    market_data,
):
    try:
        c1 = df1.iloc[-2]
        c5 = df5.iloc[-2]
        entry = float(current_price)

        rsi1 = market_data["rsi1"]
        rsi5 = market_data["rsi5"]
        vol1 = market_data["vol1"]
        vol5 = market_data["vol5"]
        move5 = market_data["move5"]
        move15 = market_data["move15"]
        lower_wick = market_data["lower_wick"]
        upper_wick = market_data["upper_wick"]
        close_power = market_data["close_power"]

        if direction == "LONG":
            raw_sl = min(float(c1["low"]), float(c5["low"]))
            sl = raw_sl * (1 - SL_BUFFER_PERCENT / 100)
            risk = entry - sl

            if risk <= 0:
                return None, None

            risk_percent = risk / entry * 100

            conditions = [
                build_condition_result(
                    "TEPKİ: 5M düşüş yetersiz",
                    move5 <= -REACTION_LONG_MIN_5M_DROP,
                ),
                build_condition_result(
                    "TEPKİ: 15M düşüş yetersiz",
                    move15 <= -REACTION_LONG_MIN_15M_DROP,
                ),
                build_condition_result(
                    "TEPKİ: 1M RSI uygun değil",
                    REACTION_LONG_RSI_1M_MIN
                    <= rsi1
                    <= REACTION_LONG_RSI_1M_MAX,
                ),
                build_condition_result(
                    "TEPKİ: 5M RSI yüksek",
                    rsi5 <= REACTION_LONG_RSI_5M_MAX,
                ),
                build_condition_result(
                    "TEPKİ: 1M hacim düşük",
                    vol1 >= REACTION_MIN_1M_VOLUME_RATIO,
                ),
                build_condition_result(
                    "TEPKİ: 5M hacim düşük",
                    vol5 >= REACTION_MIN_5M_VOLUME_RATIO,
                ),
                build_condition_result(
                    "TEPKİ: alt fitil yetersiz",
                    lower_wick >= REACTION_MIN_WICK_PERCENT,
                ),
                build_condition_result(
                    "TEPKİ: kapanış gücü zayıf",
                    close_power >= REACTION_LONG_MIN_CLOSE_POWER,
                ),
                build_condition_result(
                    "TEPKİ: risk uygun değil",
                    MIN_RISK_PERCENT
                    <= risk_percent
                    <= MAX_RISK_PERCENT,
                ),
            ]

            bonus = 0
            if vol1 >= 2.0:
                bonus += 3
            if lower_wick >= 40:
                bonus += 3
            if close_power >= 58:
                bonus += 2

            score, ok_count, total = score_from_conditions(
                conditions,
                bonus,
            )
            missing = missing_reasons(conditions)

            # Scalp için temel şartlar puanla telafi edilemez.
            hard_ok = (
                MIN_RISK_PERCENT
                <= risk_percent
                <= MAX_RISK_PERCENT
                and move5 <= -REACTION_LONG_MIN_5M_DROP
                and move15 <= -REACTION_LONG_MIN_15M_DROP
                and REACTION_LONG_RSI_1M_MIN
                <= rsi1
                <= REACTION_LONG_RSI_1M_MAX
                and rsi5 <= REACTION_LONG_RSI_5M_MAX
                and vol1 >= REACTION_MIN_1M_VOLUME_RATIO
                and vol5 >= REACTION_MIN_5M_VOLUME_RATIO
                and lower_wick >= REACTION_MIN_WICK_PERCENT
                and close_power >= REACTION_LONG_MIN_CLOSE_POWER
            )

            signal = None
            if score >= MIN_SCORE and hard_ok:
                signal = make_signal(
                    symbol,
                    "LONG",
                    "TEPKI_SCALP",
                    "Teyitli Tepki LONG",
                    entry,
                    sl,
                    score,
                    risk_percent,
                    market_data,
                    ok_count,
                    total,
                    missing,
                )

            return signal, build_debug(
                symbol,
                "LONG",
                "Teyitli Tepki LONG",
                score,
                ok_count,
                total,
                missing,
                market_data,
                risk_percent,
            )

        raw_sl = max(float(c1["high"]), float(c5["high"]))
        sl = raw_sl * (1 + SL_BUFFER_PERCENT / 100)
        risk = sl - entry

        if risk <= 0:
            return None, None

        risk_percent = risk / entry * 100

        conditions = [
            build_condition_result(
                "TEPKİ: 5M yükseliş yetersiz",
                move5 >= REACTION_SHORT_MIN_5M_PUMP,
            ),
            build_condition_result(
                "TEPKİ: 15M yükseliş yetersiz",
                move15 >= REACTION_SHORT_MIN_15M_PUMP,
            ),
            build_condition_result(
                "TEPKİ: 1M RSI uygun değil",
                REACTION_SHORT_RSI_1M_MIN
                <= rsi1
                <= REACTION_SHORT_RSI_1M_MAX,
            ),
            build_condition_result(
                "TEPKİ: 5M RSI düşük",
                rsi5 >= REACTION_SHORT_RSI_5M_MIN,
            ),
            build_condition_result(
                "TEPKİ: 1M hacim düşük",
                vol1 >= REACTION_MIN_1M_VOLUME_RATIO,
            ),
            build_condition_result(
                "TEPKİ: 5M hacim düşük",
                vol5 >= REACTION_MIN_5M_VOLUME_RATIO,
            ),
            build_condition_result(
                "TEPKİ: üst fitil yetersiz",
                upper_wick >= REACTION_MIN_WICK_PERCENT,
            ),
            build_condition_result(
                "TEPKİ: kapanış gücü yetersiz",
                close_power <= REACTION_SHORT_MAX_CLOSE_POWER,
            ),
            build_condition_result(
                "TEPKİ: risk uygun değil",
                MIN_RISK_PERCENT
                <= risk_percent
                <= MAX_RISK_PERCENT,
            ),
        ]

        bonus = 0
        if vol1 >= 2.0:
            bonus += 3
        if upper_wick >= 40:
            bonus += 3
        if close_power <= 42:
            bonus += 2

        score, ok_count, total = score_from_conditions(
            conditions,
            bonus,
        )
        missing = missing_reasons(conditions)

        hard_ok = (
            MIN_RISK_PERCENT
            <= risk_percent
            <= MAX_RISK_PERCENT
            and move5 >= REACTION_SHORT_MIN_5M_PUMP
            and move15 >= REACTION_SHORT_MIN_15M_PUMP
            and REACTION_SHORT_RSI_1M_MIN
            <= rsi1
            <= REACTION_SHORT_RSI_1M_MAX
            and rsi5 >= REACTION_SHORT_RSI_5M_MIN
            and vol1 >= REACTION_MIN_1M_VOLUME_RATIO
            and vol5 >= REACTION_MIN_5M_VOLUME_RATIO
            and upper_wick >= REACTION_MIN_WICK_PERCENT
            and close_power <= REACTION_SHORT_MAX_CLOSE_POWER
        )

        signal = None
        if score >= MIN_SCORE and hard_ok:
            signal = make_signal(
                symbol,
                "SHORT",
                "TEPKI_SCALP",
                "Teyitli Tepki SHORT",
                entry,
                sl,
                score,
                risk_percent,
                market_data,
                ok_count,
                total,
                missing,
            )

        return signal, build_debug(
            symbol,
            "SHORT",
            "Teyitli Tepki SHORT",
            score,
            ok_count,
            total,
            missing,
            market_data,
            risk_percent,
        )

    except Exception as exc:
        print(symbol, direction, "tepki analiz hatası:", exc)
        return None, None


# =========================================================
# ATAK ANALİZİ
# =========================================================

def analyze_attack_side(
    symbol,
    direction,
    df1,
    df5,
    df15,
    current_price,
    market_data,
):
    try:
        c1 = df1.iloc[-2]
        entry = float(current_price)

        rsi1 = market_data["rsi1"]
        rsi5 = market_data["rsi5"]
        vol1 = market_data["vol1"]
        vol5 = market_data["vol5"]
        move1 = market_data["move1"]
        move5 = market_data["move5"]
        move15 = market_data["move15"]
        close_power = market_data["close_power"]

        previous_high_1m = rolling_previous_high(
            df1,
            ATTACK_BREAKOUT_LOOKBACK_1M,
        )
        previous_high_5m = rolling_previous_high(
            df5,
            ATTACK_BREAKOUT_LOOKBACK_5M,
        )
        previous_low_1m = rolling_previous_low(
            df1,
            ATTACK_BREAKOUT_LOOKBACK_1M,
        )
        previous_low_5m = rolling_previous_low(
            df5,
            ATTACK_BREAKOUT_LOOKBACK_5M,
        )

        close1 = float(c1["close"])

        breakout_long = (
            (
                previous_high_1m is not None
                and close1 >= previous_high_1m
            )
            or (
                previous_high_5m is not None
                and close1 >= previous_high_5m
            )
        )

        breakdown_short = (
            (
                previous_low_1m is not None
                and close1 <= previous_low_1m
            )
            or (
                previous_low_5m is not None
                and close1 <= previous_low_5m
            )
        )

        if direction == "LONG":
            recent_low = min(
                float(df1["low"].iloc[-6:-1].min()),
                float(df5["low"].iloc[-3:-1].min()),
            )
            sl = recent_low * (1 - SL_BUFFER_PERCENT / 100)
            risk = entry - sl

            if risk <= 0:
                return None, None

            risk_percent = risk / entry * 100

            conditions = [
                build_condition_result(
                    "ATAK: 1M yeşil güç yetersiz",
                    move1 >= ATTACK_LONG_MIN_1M_MOVE,
                ),
                build_condition_result(
                    "ATAK: 5M yukarı momentum yok",
                    move5 >= ATTACK_LONG_MIN_5M_MOVE,
                ),
                build_condition_result(
                    "ATAK: 15M yukarı momentum zayıf",
                    move15 >= ATTACK_LONG_MIN_15M_MOVE,
                ),
                build_condition_result(
                    "ATAK: 1M RSI uygun değil",
                    ATTACK_LONG_RSI_1M_MIN
                    <= rsi1
                    <= ATTACK_LONG_RSI_1M_MAX,
                ),
                build_condition_result(
                    "ATAK: 5M RSI uygun değil",
                    ATTACK_LONG_RSI_5M_MIN
                    <= rsi5
                    <= ATTACK_LONG_RSI_5M_MAX,
                ),
                build_condition_result(
                    "ATAK: 1M hacim düşük",
                    vol1 >= ATTACK_MIN_1M_VOLUME_RATIO,
                ),
                build_condition_result(
                    "ATAK: 5M hacim düşük",
                    vol5 >= ATTACK_MIN_5M_VOLUME_RATIO,
                ),
                build_condition_result(
                    "ATAK: kırılım yok",
                    breakout_long,
                ),
                build_condition_result(
                    "ATAK: kapanış gücü zayıf",
                    close_power >= ATTACK_LONG_MIN_CLOSE_POWER,
                ),
                build_condition_result(
                    "ATAK: risk uygun değil",
                    MIN_RISK_PERCENT
                    <= risk_percent
                    <= MAX_RISK_PERCENT,
                ),
            ]

            bonus = 0
            if vol1 >= 2.0:
                bonus += 3
            if vol5 >= 1.60:
                bonus += 2
            if breakout_long:
                bonus += 4
            if close_power >= 72:
                bonus += 2

            score, ok_count, total = score_from_conditions(
                conditions,
                bonus,
            )
            missing = missing_reasons(conditions)

            hard_ok = (
                MIN_RISK_PERCENT
                <= risk_percent
                <= MAX_RISK_PERCENT
                and move1 >= ATTACK_LONG_MIN_1M_MOVE
                and (
                    move5 >= ATTACK_LONG_MIN_5M_MOVE
                    or breakout_long
                )
                and move15 >= ATTACK_LONG_MIN_15M_MOVE
                and ATTACK_LONG_RSI_1M_MIN
                <= rsi1
                <= ATTACK_LONG_RSI_1M_MAX
                and ATTACK_LONG_RSI_5M_MIN
                <= rsi5
                <= ATTACK_LONG_RSI_5M_MAX
                and vol1 >= ATTACK_MIN_1M_VOLUME_RATIO
                and vol5 >= ATTACK_MIN_5M_VOLUME_RATIO
                and close_power >= ATTACK_LONG_MIN_CLOSE_POWER
            )

            signal = None
            if score >= MIN_SCORE and hard_ok:
                signal = make_signal(
                    symbol,
                    "LONG",
                    "ATAK_SCALP",
                    "Filtreli Atak Momentum LONG",
                    entry,
                    sl,
                    score,
                    risk_percent,
                    market_data,
                    ok_count,
                    total,
                    missing,
                )

            return signal, build_debug(
                symbol,
                "LONG",
                "Filtreli Atak Momentum LONG",
                score,
                ok_count,
                total,
                missing,
                market_data,
                risk_percent,
            )

        recent_high = max(
            float(df1["high"].iloc[-6:-1].max()),
            float(df5["high"].iloc[-3:-1].max()),
        )
        sl = recent_high * (1 + SL_BUFFER_PERCENT / 100)
        risk = sl - entry

        if risk <= 0:
            return None, None

        risk_percent = risk / entry * 100

        conditions = [
            build_condition_result(
                "ATAK: 1M kırmızı güç yetersiz",
                move1 <= -ATTACK_SHORT_MIN_1M_MOVE,
            ),
            build_condition_result(
                "ATAK: 5M aşağı momentum yok",
                move5 <= -ATTACK_SHORT_MIN_5M_MOVE,
            ),
            build_condition_result(
                "ATAK: 15M aşağı momentum zayıf",
                move15 <= -ATTACK_SHORT_MIN_15M_MOVE,
            ),
            build_condition_result(
                "ATAK: 1M RSI uygun değil",
                ATTACK_SHORT_RSI_1M_MIN
                <= rsi1
                <= ATTACK_SHORT_RSI_1M_MAX,
            ),
            build_condition_result(
                "ATAK: 5M RSI uygun değil",
                ATTACK_SHORT_RSI_5M_MIN
                <= rsi5
                <= ATTACK_SHORT_RSI_5M_MAX,
            ),
            build_condition_result(
                "ATAK: 1M hacim düşük",
                vol1 >= ATTACK_MIN_1M_VOLUME_RATIO,
            ),
            build_condition_result(
                "ATAK: 5M hacim düşük",
                vol5 >= ATTACK_MIN_5M_VOLUME_RATIO,
            ),
            build_condition_result(
                "ATAK: aşağı kırılım yok",
                breakdown_short,
            ),
            build_condition_result(
                "ATAK: kapanış gücü zayıf",
                close_power <= ATTACK_SHORT_MAX_CLOSE_POWER,
            ),
            build_condition_result(
                "ATAK: risk uygun değil",
                MIN_RISK_PERCENT
                <= risk_percent
                <= MAX_RISK_PERCENT,
            ),
        ]

        bonus = 0
        if vol1 >= 2.0:
            bonus += 3
        if vol5 >= 1.60:
            bonus += 2
        if breakdown_short:
            bonus += 4
        if close_power <= 28:
            bonus += 2

        score, ok_count, total = score_from_conditions(
            conditions,
            bonus,
        )
        missing = missing_reasons(conditions)

        hard_ok = (
            MIN_RISK_PERCENT
            <= risk_percent
            <= MAX_RISK_PERCENT
            and move1 <= -ATTACK_SHORT_MIN_1M_MOVE
            and (
                move5 <= -ATTACK_SHORT_MIN_5M_MOVE
                or breakdown_short
            )
            and move15 <= -ATTACK_SHORT_MIN_15M_MOVE
            and ATTACK_SHORT_RSI_1M_MIN
            <= rsi1
            <= ATTACK_SHORT_RSI_1M_MAX
            and ATTACK_SHORT_RSI_5M_MIN
            <= rsi5
            <= ATTACK_SHORT_RSI_5M_MAX
            and vol1 >= ATTACK_MIN_1M_VOLUME_RATIO
            and vol5 >= ATTACK_MIN_5M_VOLUME_RATIO
            and close_power <= ATTACK_SHORT_MAX_CLOSE_POWER
        )

        signal = None
        if score >= MIN_SCORE and hard_ok:
            signal = make_signal(
                symbol,
                "SHORT",
                "ATAK_SCALP",
                "Filtreli Atak Momentum SHORT",
                entry,
                sl,
                score,
                risk_percent,
                market_data,
                ok_count,
                total,
                missing,
            )

        return signal, build_debug(
            symbol,
            "SHORT",
            "Filtreli Atak Momentum SHORT",
            score,
            ok_count,
            total,
            missing,
            market_data,
            risk_percent,
        )

    except Exception as exc:
        print(symbol, direction, "atak analiz hatası:", exc)
        return None, None


def analyze_symbol(exchange, symbol):
    current_price = get_current_price(exchange, symbol)

    df1 = fetch_df(exchange, symbol, "1m", limit=100, min_len=60)
    df5 = fetch_df(exchange, symbol, "5m", limit=100, min_len=60)
    df15 = fetch_df(exchange, symbol, "15m", limit=80, min_len=40)

    if (
        df1 is None
        or df5 is None
        or df15 is None
        or current_price is None
    ):
        return [], []

    df1 = df1.copy()
    df5 = df5.copy()

    df1["rsi"] = calc_rsi(df1["close"])
    df5["rsi"] = calc_rsi(df5["close"])

    c1 = df1.iloc[-2]
    c5 = df5.iloc[-2]
    c15 = df15.iloc[-2]

    market_data = {
        "rsi1": float(df1["rsi"].iloc[-2]),
        "rsi5": float(df5["rsi"].iloc[-2]),
        "vol1": volume_ratio(df1, -2, 20),
        "vol5": volume_ratio(df5, -2, 20),
        "move1": candle_move_percent(c1),
        "move5": candle_move_percent(c5),
        "move15": candle_move_percent(c15),
        "lower_wick": lower_wick_percent(c1),
        "upper_wick": upper_wick_percent(c1),
        "close_power": close_power_percent(c1),
    }

    signals = []
    debug_items = []

    for analyzer in (
        analyze_reaction_side,
        analyze_attack_side,
    ):
        for direction in ("LONG", "SHORT"):
            signal, debug = analyzer(
                symbol,
                direction,
                df1,
                df5,
                df15,
                current_price,
                market_data,
            )

            if signal is not None:
                signals.append(signal)
            if debug is not None:
                debug_items.append(debug)

    signals.sort(
        key=lambda item: (
            item["score"],
            -item["risk_percent"],
            item["vol5"],
            item["vol1"],
        ),
        reverse=True,
    )

    return signals[:1], debug_items


# =========================================================
# AÇIK SİNYAL TAKİBİ
# =========================================================

def save_open_signal(state, signal):
    key = (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['source']}"
    )

    state.setdefault("open_scalp_signals", {})
    state["open_scalp_signals"][key] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "source": signal["source"],
        "setup": signal.get("setup"),
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


def notify_tp1(state, symbol, direction, entry, tp1):
    send_telegram(
        f"✅ SCALP TP1 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"TP1: {format_price(tp1)}\n"
        f"Öneri: %50 kâr al, SL girişe çek."
    )
    increment_stat(state, "tp1")


def notify_tp2(state, symbol, direction, tp2):
    send_telegram(
        f"✅ SCALP TP2 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"TP2: {format_price(tp2)}"
    )
    increment_stat(state, "tp2")


def notify_tp3(state, symbol, direction, tp3):
    send_telegram(
        f"🏁 SCALP TP3 GELDİ\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"TP3: {format_price(tp3)}\n"
        f"Scalp maksimum hedefe ulaştı."
    )
    increment_stat(state, "tp3")


def notify_stop(state, symbol, direction, entry, sl, close):
    send_telegram(
        f"❌ SCALP STOP OLDU\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}\n"
        f"SL: {format_price(sl)}\n"
        f"Güncel: {format_price(close)}"
    )
    increment_stat(state, "stop")


def notify_breakeven(state, symbol, direction, entry):
    send_telegram(
        f"🟡 SCALP KALAN GİRİŞTEN KAPANDI\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {direction}\n"
        f"Giriş: {format_price(entry)}"
    )
    increment_stat(state, "breakeven")


def check_open_signals(exchange, state):
    open_signals = state.get("open_scalp_signals", {})

    if not open_signals:
        print("Açık scalp sinyali yok.")
        return

    updated = {}
    max_age_seconds = MAX_OPEN_SIGNAL_MINUTES * 60

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
                    f"⏳ SCALP SİNYAL SÜRESİ DOLDU\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n\n"
                    f"{MAX_OPEN_SIGNAL_MINUTES} dakika içinde "
                    f"TP1 gelmediği için takipten çıkarıldı."
                )
                increment_stat(state, "expired")
                continue

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                max(opened_at, last_checked_at - 120),
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

                    # TP1'in ilk görüldüğü aynı mumda eski düşük fiyat
                    # yüzünden yanlış BE kapanışı yapılmaz.
                    if tp1_hit and not just_hit_tp1 and low <= entry:
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

                    if tp1_hit and not just_hit_tp1 and high >= entry:
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
            print(key, "scalp takip hatası:", exc)
            updated[key] = signal

    state["open_scalp_signals"] = updated
    save_state(state)


# =========================================================
# RAPOR
# =========================================================

def top_reasons_text(counter, limit=6):
    if not counter:
        return "Veri yok"

    return "\n".join(
        f"• {reason}: {count}"
        for reason, count in counter.most_common(limit)
    )


def candidate_line(debug):
    missing = debug.get("missing", [])
    missing_text = (
        ", ".join(missing[:3])
        if missing
        else "eksik yok"
    )

    return (
        f"{debug['symbol']} {debug['direction']} | "
        f"{debug.get('setup', 'SCALP')} | "
        f"şart {debug['ok_count']}/{debug['total_conditions']} | "
        f"skor {debug['score']} | "
        f"eksik: {missing_text}"
    )


def build_no_signal_report(
    scanned_count,
    candidate_count,
    reason_counter,
    top_candidates,
):
    lines = [
        "⚡ HIZLI SCALP RADAR v2 RAPORU",
        "",
        f"Bot: {BOT_NAME}",
        f"Zaman: {tr_now_text()}",
        f"Taranan coin: {scanned_count}",
        f"Filtreyi geçen aday: {candidate_count}",
        "",
        "En çok elenen şartlar:",
        top_reasons_text(reason_counter),
        "",
        "Sinyale en yakın adaylar:",
    ]

    if top_candidates:
        for item in top_candidates[:8]:
            lines.append("• " + candidate_line(item))
    else:
        lines.append("• Yakın aday yok")

    lines.extend([
        "",
        "Not: Bu rapor işlem sinyali değildir. "
        "Kalite filtrelerinin neden sinyal üretmediğini gösterir.",
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


# =========================================================
# MAIN
# =========================================================

def main():
    print(BOT_NAME, "başladı.")

    state = load_state()
    exchange = get_exchange()

    check_open_signals(exchange, state)
    state = load_state()

    scan_coins = get_scan_coins(exchange)

    open_count = len(state.get("open_scalp_signals", {}))
    available_slots = max(
        0,
        MAX_OPEN_SCALP_SIGNALS - open_count,
    )

    print("Açık scalp:", open_count)
    print("Boş scalp slot:", available_slots)

    all_signals = []
    reason_counter = Counter()
    top_candidates = []
    scanned = 0

    for symbol in scan_coins:
        try:
            scanned += 1

            if has_open_same_symbol(state, symbol):
                print(symbol, "zaten açık scalp var, atlandı.")
                continue

            signals, debug_items = analyze_symbol(
                exchange,
                symbol,
            )

            for debug in debug_items:
                for reason in debug.get("missing", []):
                    reason_counter[reason] += 1
                top_candidates.append(debug)

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
            print(symbol, "genel analiz hatası:", exc)

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
        ),
        reverse=True,
    )

    selected = []
    max_to_send = min(
        MAX_NEW_SIGNALS_PER_RUN,
        available_slots,
    )

    # Seçilen aday gönderilmeden hemen önce fiyat sapması kontrol edilir.
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

    print("Bulunan kaliteli scalp sinyal:", len(all_signals))
    print("Gönderilecek scalp sinyal:", len(selected))

    if selected:
        send_telegram(
            f"⚡ {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {scanned}\n"
            f"Kaliteli scalp adayı: {len(all_signals)}\n"
            f"Açık scalp: {open_count}/{MAX_OPEN_SCALP_SIGNALS}\n"
            f"Gönderilecek sinyal: {len(selected)}"
        )

    for signal in selected:
        extra = (
            f"\n💰 Güncel Fiyat: "
            f"{format_price(signal['current_price'])}\n"
            f"📏 Giriş Sapması: "
            f"%{round(signal['entry_drift_percent'], 3)}\n"
            f"📌 Son Kontrol: Scalp girişe yakın ✅"
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
        print("Yeni kaliteli scalp sinyali yok.")

        if should_send_no_signal_report(state):
            send_telegram(
                build_no_signal_report(
                    scanned,
                    len(all_signals),
                    reason_counter,
                    top_candidates,
                )
            )
            state["last_no_signal_report"] = now_ts()
            save_state(state)

    print(BOT_NAME, "tamamlandı.")


if __name__ == "__main__":
    main()
