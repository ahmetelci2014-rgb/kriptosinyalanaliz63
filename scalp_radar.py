# scalp_radar.py
# Hızlı Scalp Radar
# OKX USDT Futures tarar. Emir açmaz, sadece Telegram sinyali gönderir.
# Mantık:
# 1) Tepki scalp: sert düşüş/yükseliş sonrası hızlı tepki
# 2) Atak scalp: güçlü momentum/kırılım ile hızlı yön hareketi
# TOKEN ve CHAT_ID GitHub Secrets içinden okunur.

import os
import time
import json
import math
import requests
from collections import Counter
from datetime import datetime, timezone, timedelta

import ccxt
import pandas as pd


# =========================
# GENEL AYARLAR
# =========================

BOT_NAME = "Hızlı Scalp Radar"

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

STATE_FILE = "scalp_radar_state.json"
TR_TIMEZONE = timezone(timedelta(hours=3))

# Tüm uygun OKX USDT futures coinleri taransın.
AUTO_ALL_OKX_USDT_FUTURES = True
MAX_SCAN_COINS = 9999
MIN_24H_QUOTE_VOLUME = 100_000

# Sinyal limitleri
MAX_NEW_SIGNALS_PER_RUN = 3
MAX_OPEN_SCALP_SIGNALS = 3
DUPLICATE_SECONDS = 90 * 60

# Açık scalp takip süresi
MAX_OPEN_SIGNAL_MINUTES = 180
TRACK_TIMEFRAME = "1m"
TRACK_LIMIT = 180

# Sinyal yok raporu
SEND_NO_SIGNAL_REPORT = True
NO_SIGNAL_REPORT_EVERY_MINUTES = 20

# Scalp TP/SL
TP1_R = 0.65
TP2_R = 1.15
TP3_R = 1.70
SL_BUFFER_PERCENT = 0.08

# Risk
MIN_RISK_PERCENT = 0.20
MAX_RISK_PERCENT = 1.65

# Genel skor
MIN_SCORE = 76

# =========================
# TEPKİ SCALP AYARLARI
# =========================
# LONG: hızlı düşüş sonrası tepki
REACTION_LONG_MIN_5M_DROP = 0.65
REACTION_LONG_MIN_15M_DROP = 0.25
REACTION_LONG_RSI_1M_MIN = 16
REACTION_LONG_RSI_1M_MAX = 46
REACTION_LONG_RSI_5M_MAX = 53

# SHORT: hızlı yükseliş sonrası red
REACTION_SHORT_MIN_5M_PUMP = 0.65
REACTION_SHORT_MIN_15M_PUMP = 0.25
REACTION_SHORT_RSI_1M_MIN = 54
REACTION_SHORT_RSI_1M_MAX = 86
REACTION_SHORT_RSI_5M_MIN = 49

# Tepki hacim / fitil
REACTION_MIN_1M_VOLUME_RATIO = 1.35
REACTION_MIN_5M_VOLUME_RATIO = 1.00
REACTION_MIN_WICK_PERCENT = 25
REACTION_LONG_MIN_CLOSE_POWER = 43
REACTION_SHORT_MAX_CLOSE_POWER = 57


# =========================
# ATAK / MOMENTUM SCALP AYARLARI
# =========================
# LONG: yükseliş atağı / kırılım
ATTACK_LONG_MIN_1M_MOVE = 0.12
ATTACK_LONG_MIN_5M_MOVE = 0.35
ATTACK_LONG_MIN_15M_MOVE = 0.15
ATTACK_LONG_RSI_1M_MIN = 48
ATTACK_LONG_RSI_1M_MAX = 78
ATTACK_LONG_RSI_5M_MIN = 47
ATTACK_LONG_RSI_5M_MAX = 76
ATTACK_LONG_MIN_CLOSE_POWER = 62

# SHORT: düşüş atağı / kırılım
ATTACK_SHORT_MIN_1M_MOVE = 0.12
ATTACK_SHORT_MIN_5M_MOVE = 0.35
ATTACK_SHORT_MIN_15M_MOVE = 0.15
ATTACK_SHORT_RSI_1M_MIN = 22
ATTACK_SHORT_RSI_1M_MAX = 52
ATTACK_SHORT_RSI_5M_MIN = 24
ATTACK_SHORT_RSI_5M_MAX = 53
ATTACK_SHORT_MAX_CLOSE_POWER = 38

# Atak hacim / kırılım
ATTACK_MIN_1M_VOLUME_RATIO = 1.35
ATTACK_MIN_5M_VOLUME_RATIO = 1.00
ATTACK_BREAKOUT_LOOKBACK_1M = 20
ATTACK_BREAKOUT_LOOKBACK_5M = 12


# =========================
# TELEGRAM
# =========================

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


# =========================
# JSON STATE
# =========================

def empty_state():
    return {
        "open_scalp_signals": {},
        "last_sent": {},
        "last_no_signal_report": 0,
        "stats": {},
    }


def load_state():
    try:
        if not os.path.exists(STATE_FILE):
            return empty_state()

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return empty_state()

            data = json.loads(content)
            if not isinstance(data, dict):
                data = {}

            data.setdefault("open_scalp_signals", {})
            data.setdefault("last_sent", {})
            data.setdefault("last_no_signal_report", 0)
            data.setdefault("stats", {})
            return data

    except Exception as e:
        print("State okuma hatası:", e)
        return empty_state()


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print("State kaydetme hatası:", e)
        return False


def now_ts():
    return int(time.time())


def tr_now_text():
    return datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


# =========================
# OKX / DATA
# =========================

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

        rows = sorted(rows, key=lambda x: x[1], reverse=True)
        coins = [coin for coin, _ in rows]

        if MAX_SCAN_COINS and MAX_SCAN_COINS > 0:
            coins = coins[:MAX_SCAN_COINS]

        print("Taranacak coin sayısı:", len(coins))
        print("İlk 20 coin:", coins[:20])
        return coins

    except Exception as e:
        print("Coin tarama hatası:", e)
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

        df = pd.DataFrame(
            ohlcv,
            columns=["time", "open", "high", "low", "close", "volume"]
        )

        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.dropna()
        if len(df) < min_len:
            return None

        return df

    except Exception as e:
        print(symbol, timeframe, "veri hatası:", e)
        return None


def fetch_candles_since(exchange, symbol, timeframe, since_seconds, limit=180):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            since=max(0, int(since_seconds)) * 1000,
            limit=limit,
        )

        candles = []
        for item in ohlcv:
            candles.append({
                "time": int(item[0] / 1000),
                "open": float(item[1]),
                "high": float(item[2]),
                "low": float(item[3]),
                "close": float(item[4]),
                "volume": float(item[5]),
            })

        return candles

    except Exception as e:
        print(symbol, "mum takip hatası:", e)
        return []


def get_current_price(exchange, symbol):
    try:
        ticker = exchange.fetch_ticker(to_okx_symbol(symbol))
        price = ticker.get("last")
        return float(price) if price is not None else None
    except Exception as e:
        print(symbol, "güncel fiyat hatası:", e)
        return None


# =========================
# HESAPLAMALAR
# =========================

def format_price(value):
    try:
        value = float(value)
    except Exception:
        return str(value)

    if value >= 100:
        return f"{value:.2f}"
    if value >= 10:
        return f"{value:.3f}"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.1:
        return f"{value:.5f}"
    if value >= 0.01:
        return f"{value:.6f}"
    return f"{value:.10f}"


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, 0.0000001)
    return 100 - (100 / (1 + rs))


def volume_ratio(df, index=-2, period=20):
    try:
        avg = df["volume"].rolling(period).mean().iloc[index]
        vol = df["volume"].iloc[index]
        if avg <= 0 or math.isnan(avg):
            return 0.0
        return float(vol / avg)
    except Exception:
        return 0.0


def candle_move_percent(row):
    try:
        open_price = float(row["open"])
        close_price = float(row["close"])
        if open_price <= 0:
            return 0.0
        return ((close_price - open_price) / open_price) * 100
    except Exception:
        return 0.0


def lower_wick_percent(row):
    try:
        high = float(row["high"])
        low = float(row["low"])
        open_price = float(row["open"])
        close_price = float(row["close"])
        rng = high - low
        if rng <= 0:
            return 0.0
        wick = min(open_price, close_price) - low
        return max(0.0, (wick / rng) * 100)
    except Exception:
        return 0.0


def upper_wick_percent(row):
    try:
        high = float(row["high"])
        low = float(row["low"])
        open_price = float(row["open"])
        close_price = float(row["close"])
        rng = high - low
        if rng <= 0:
            return 0.0
        wick = high - max(open_price, close_price)
        return max(0.0, (wick / rng) * 100)
    except Exception:
        return 0.0


def close_power_percent(row):
    try:
        high = float(row["high"])
        low = float(row["low"])
        close_price = float(row["close"])
        rng = high - low
        if rng <= 0:
            return 50.0
        return ((close_price - low) / rng) * 100
    except Exception:
        return 50.0


def rolling_previous_high(df, lookback):
    try:
        # Son kapanmış mum -2. Öncesindeki lookback mumların en yüksek seviyesi.
        start = max(0, len(df) - lookback - 2)
        end = len(df) - 2
        if end <= start:
            return None
        return float(df["high"].iloc[start:end].max())
    except Exception:
        return None


def rolling_previous_low(df, lookback):
    try:
        start = max(0, len(df) - lookback - 2)
        end = len(df) - 2
        if end <= start:
            return None
        return float(df["low"].iloc[start:end].min())
    except Exception:
        return None


def is_recent_duplicate(state, symbol, direction):
    key = f"{symbol}_{direction}"
    last_time = int(state.get("last_sent", {}).get(key, 0))
    return now_ts() - last_time < DUPLICATE_SECONDS


def mark_sent(state, symbol, direction):
    key = f"{symbol}_{direction}"
    state.setdefault("last_sent", {})
    state["last_sent"][key] = now_ts()
    save_state(state)


def has_open_same_symbol(state, symbol):
    for signal in state.get("open_scalp_signals", {}).values():
        if signal.get("symbol") == symbol:
            return True
    return False


# =========================
# SCALP ANALİZ YARDIMCILARI
# =========================

def build_condition_result(label, ok):
    return {"label": label, "ok": bool(ok)}


def score_from_conditions(conditions, bonus=0):
    ok_count = sum(1 for c in conditions if c["ok"])
    total = max(1, len(conditions))
    score = int((ok_count / total) * 100) + int(bonus)
    return max(0, min(100, score)), ok_count, total


def missing_reasons(conditions):
    return [c["label"] for c in conditions if not c["ok"]]


def build_signal_message(signal):
    icon = "🟢" if signal["direction"] == "LONG" else "🔴"
    setup_name = signal.get("setup", "SCALP")

    return (
        f"⚡ HIZLI SCALP RADAR\n\n"
        f"{icon} {signal['direction']}\n"
        f"🟡 Coin: {signal['symbol']}\n"
        f"⏱️ Kaynak: {signal['source']}\n"
        f"📌 Kurulum: {setup_name}\n\n"
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
        f"• Hızlı scalp sinyalidir, risk yüksektir.\n"
        f"• TP1 gelirse %50 kâr al, SL girişe çek.\n"
        f"• Stop mutlaka girilmeli.\n"
        f"• Marjin: Isolated.\n"
        f"• Kaldıraç düşük tutulmalı.\n\n"
        f"⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işlem açma."
    )


def make_signal(symbol, direction, source, setup, entry, sl, score, risk_percent, market_data, ok_count, total, missing):
    if direction == "LONG":
        risk = entry - sl
        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R
    else:
        risk = sl - entry
        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    signal = {
        "symbol": symbol,
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


def build_debug(symbol, direction, setup, score, ok_count, total, missing, market_data, risk_percent):
    return {
        "symbol": symbol,
        "direction": direction,
        "setup": setup,
        "score": score,
        "ok_count": ok_count,
        "total_conditions": total,
        "missing": missing,
        "risk_percent": risk_percent,
        **market_data,
    }


# =========================
# TEPKİ SCALP
# =========================

def analyze_reaction_side(symbol, direction, df1, df5, df15, current_price, market_data):
    try:
        if df1 is None or df5 is None or df15 is None or current_price is None:
            return None, None

        c1 = df1.iloc[-2]
        c5 = df5.iloc[-2]
        entry = float(current_price)

        rsi1 = market_data["rsi1"]
        rsi5 = market_data["rsi5"]
        vol1 = market_data["vol1"]
        vol5 = market_data["vol5"]
        move5 = market_data["move5"]
        move15 = market_data["move15"]
        lw = market_data["lower_wick"]
        uw = market_data["upper_wick"]
        cp = market_data["close_power"]

        if direction == "LONG":
            raw_sl = min(float(c1["low"]), float(c5["low"]))
            sl = raw_sl * (1 - SL_BUFFER_PERCENT / 100)
            risk = entry - sl
            if risk <= 0:
                return None, None

            risk_percent = (risk / entry) * 100

            conditions = [
                build_condition_result("TEPKİ: 5M düşüş yetersiz", move5 <= -REACTION_LONG_MIN_5M_DROP),
                build_condition_result("TEPKİ: 15M düşüş yetersiz", move15 <= -REACTION_LONG_MIN_15M_DROP),
                build_condition_result("TEPKİ: 1M RSI uygun değil", REACTION_LONG_RSI_1M_MIN <= rsi1 <= REACTION_LONG_RSI_1M_MAX),
                build_condition_result("TEPKİ: 5M RSI yüksek", rsi5 <= REACTION_LONG_RSI_5M_MAX),
                build_condition_result("TEPKİ: 1M hacim düşük", vol1 >= REACTION_MIN_1M_VOLUME_RATIO),
                build_condition_result("TEPKİ: 5M hacim düşük", vol5 >= REACTION_MIN_5M_VOLUME_RATIO),
                build_condition_result("TEPKİ: alt fitil yetersiz", lw >= REACTION_MIN_WICK_PERCENT),
                build_condition_result("TEPKİ: kapanış gücü zayıf", cp >= REACTION_LONG_MIN_CLOSE_POWER),
                build_condition_result("TEPKİ: risk uygun değil", MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT),
            ]

            bonus = 0
            if vol1 >= 2.0:
                bonus += 3
            if lw >= 40:
                bonus += 3
            if cp >= 55:
                bonus += 2

            score, ok_count, total = score_from_conditions(conditions, bonus=bonus)
            missing = missing_reasons(conditions)

            hard_ok = (
                MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT
                and (vol1 >= REACTION_MIN_1M_VOLUME_RATIO or vol5 >= REACTION_MIN_5M_VOLUME_RATIO)
                and (move5 <= -REACTION_LONG_MIN_5M_DROP or lw >= REACTION_MIN_WICK_PERCENT)
            )

            signal = None
            if score >= MIN_SCORE and hard_ok:
                signal = make_signal(
                    symbol, "LONG", "TEPKI_SCALP", "Tepki Scalp LONG",
                    entry, sl, score, risk_percent, market_data, ok_count, total, missing
                )

            debug = build_debug(symbol, "LONG", "Tepki Scalp LONG", score, ok_count, total, missing, market_data, risk_percent)
            return signal, debug

        else:
            raw_sl = max(float(c1["high"]), float(c5["high"]))
            sl = raw_sl * (1 + SL_BUFFER_PERCENT / 100)
            risk = sl - entry
            if risk <= 0:
                return None, None

            risk_percent = (risk / entry) * 100

            conditions = [
                build_condition_result("TEPKİ: 5M yükseliş yetersiz", move5 >= REACTION_SHORT_MIN_5M_PUMP),
                build_condition_result("TEPKİ: 15M yükseliş yetersiz", move15 >= REACTION_SHORT_MIN_15M_PUMP),
                build_condition_result("TEPKİ: 1M RSI uygun değil", REACTION_SHORT_RSI_1M_MIN <= rsi1 <= REACTION_SHORT_RSI_1M_MAX),
                build_condition_result("TEPKİ: 5M RSI düşük", rsi5 >= REACTION_SHORT_RSI_5M_MIN),
                build_condition_result("TEPKİ: 1M hacim düşük", vol1 >= REACTION_MIN_1M_VOLUME_RATIO),
                build_condition_result("TEPKİ: 5M hacim düşük", vol5 >= REACTION_MIN_5M_VOLUME_RATIO),
                build_condition_result("TEPKİ: üst fitil yetersiz", uw >= REACTION_MIN_WICK_PERCENT),
                build_condition_result("TEPKİ: kapanış gücü short için zayıf", cp <= REACTION_SHORT_MAX_CLOSE_POWER),
                build_condition_result("TEPKİ: risk uygun değil", MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT),
            ]

            bonus = 0
            if vol1 >= 2.0:
                bonus += 3
            if uw >= 40:
                bonus += 3
            if cp <= 45:
                bonus += 2

            score, ok_count, total = score_from_conditions(conditions, bonus=bonus)
            missing = missing_reasons(conditions)

            hard_ok = (
                MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT
                and (vol1 >= REACTION_MIN_1M_VOLUME_RATIO or vol5 >= REACTION_MIN_5M_VOLUME_RATIO)
                and (move5 >= REACTION_SHORT_MIN_5M_PUMP or uw >= REACTION_MIN_WICK_PERCENT)
            )

            signal = None
            if score >= MIN_SCORE and hard_ok:
                signal = make_signal(
                    symbol, "SHORT", "TEPKI_SCALP", "Tepki Scalp SHORT",
                    entry, sl, score, risk_percent, market_data, ok_count, total, missing
                )

            debug = build_debug(symbol, "SHORT", "Tepki Scalp SHORT", score, ok_count, total, missing, market_data, risk_percent)
            return signal, debug

    except Exception as e:
        print(symbol, direction, "tepki analiz hatası:", e)
        return None, None


# =========================
# ATAK / MOMENTUM SCALP
# =========================

def analyze_attack_side(symbol, direction, df1, df5, df15, current_price, market_data):
    try:
        if df1 is None or df5 is None or df15 is None or current_price is None:
            return None, None

        c1 = df1.iloc[-2]
        entry = float(current_price)

        rsi1 = market_data["rsi1"]
        rsi5 = market_data["rsi5"]
        vol1 = market_data["vol1"]
        vol5 = market_data["vol5"]
        move1 = market_data["move1"]
        move5 = market_data["move5"]
        move15 = market_data["move15"]
        cp = market_data["close_power"]

        prev_high_1m = rolling_previous_high(df1, ATTACK_BREAKOUT_LOOKBACK_1M)
        prev_high_5m = rolling_previous_high(df5, ATTACK_BREAKOUT_LOOKBACK_5M)
        prev_low_1m = rolling_previous_low(df1, ATTACK_BREAKOUT_LOOKBACK_1M)
        prev_low_5m = rolling_previous_low(df5, ATTACK_BREAKOUT_LOOKBACK_5M)

        close1 = float(c1["close"])

        breakout_long = False
        breakdown_short = False

        if prev_high_1m is not None and close1 >= prev_high_1m:
            breakout_long = True
        if prev_high_5m is not None and close1 >= prev_high_5m:
            breakout_long = True

        if prev_low_1m is not None and close1 <= prev_low_1m:
            breakdown_short = True
        if prev_low_5m is not None and close1 <= prev_low_5m:
            breakdown_short = True

        if direction == "LONG":
            recent_low = min(
                float(df1["low"].iloc[-6:-1].min()),
                float(df5["low"].iloc[-3:-1].min()),
            )
            sl = recent_low * (1 - SL_BUFFER_PERCENT / 100)
            risk = entry - sl
            if risk <= 0:
                return None, None

            risk_percent = (risk / entry) * 100

            conditions = [
                build_condition_result("ATAK: 1M yeşil güç yetersiz", move1 >= ATTACK_LONG_MIN_1M_MOVE),
                build_condition_result("ATAK: 5M yukarı momentum yok", move5 >= ATTACK_LONG_MIN_5M_MOVE),
                build_condition_result("ATAK: 15M yukarı momentum zayıf", move15 >= ATTACK_LONG_MIN_15M_MOVE),
                build_condition_result("ATAK: 1M RSI atak aralığında değil", ATTACK_LONG_RSI_1M_MIN <= rsi1 <= ATTACK_LONG_RSI_1M_MAX),
                build_condition_result("ATAK: 5M RSI atak aralığında değil", ATTACK_LONG_RSI_5M_MIN <= rsi5 <= ATTACK_LONG_RSI_5M_MAX),
                build_condition_result("ATAK: 1M hacim düşük", vol1 >= ATTACK_MIN_1M_VOLUME_RATIO),
                build_condition_result("ATAK: 5M hacim düşük", vol5 >= ATTACK_MIN_5M_VOLUME_RATIO),
                build_condition_result("ATAK: kırılım yok", breakout_long),
                build_condition_result("ATAK: kapanış gücü zayıf", cp >= ATTACK_LONG_MIN_CLOSE_POWER),
                build_condition_result("ATAK: risk uygun değil", MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT),
            ]

            bonus = 0
            if vol1 >= 2.0:
                bonus += 3
            if vol5 >= 1.6:
                bonus += 2
            if breakout_long:
                bonus += 4
            if cp >= 72:
                bonus += 2

            score, ok_count, total = score_from_conditions(conditions, bonus=bonus)
            missing = missing_reasons(conditions)

            hard_ok = (
                MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT
                and (vol1 >= ATTACK_MIN_1M_VOLUME_RATIO or vol5 >= ATTACK_MIN_5M_VOLUME_RATIO)
                and (move5 >= ATTACK_LONG_MIN_5M_MOVE or breakout_long)
                and cp >= 55
            )

            signal = None
            if score >= MIN_SCORE and hard_ok:
                signal = make_signal(
                    symbol, "LONG", "ATAK_SCALP", "Atak Momentum LONG",
                    entry, sl, score, risk_percent, market_data, ok_count, total, missing
                )

            debug = build_debug(symbol, "LONG", "Atak Momentum LONG", score, ok_count, total, missing, market_data, risk_percent)
            return signal, debug

        else:
            recent_high = max(
                float(df1["high"].iloc[-6:-1].max()),
                float(df5["high"].iloc[-3:-1].max()),
            )
            sl = recent_high * (1 + SL_BUFFER_PERCENT / 100)
            risk = sl - entry
            if risk <= 0:
                return None, None

            risk_percent = (risk / entry) * 100

            conditions = [
                build_condition_result("ATAK: 1M kırmızı güç yetersiz", move1 <= -ATTACK_SHORT_MIN_1M_MOVE),
                build_condition_result("ATAK: 5M aşağı momentum yok", move5 <= -ATTACK_SHORT_MIN_5M_MOVE),
                build_condition_result("ATAK: 15M aşağı momentum zayıf", move15 <= -ATTACK_SHORT_MIN_15M_MOVE),
                build_condition_result("ATAK: 1M RSI short aralığında değil", ATTACK_SHORT_RSI_1M_MIN <= rsi1 <= ATTACK_SHORT_RSI_1M_MAX),
                build_condition_result("ATAK: 5M RSI short aralığında değil", ATTACK_SHORT_RSI_5M_MIN <= rsi5 <= ATTACK_SHORT_RSI_5M_MAX),
                build_condition_result("ATAK: 1M hacim düşük", vol1 >= ATTACK_MIN_1M_VOLUME_RATIO),
                build_condition_result("ATAK: 5M hacim düşük", vol5 >= ATTACK_MIN_5M_VOLUME_RATIO),
                build_condition_result("ATAK: aşağı kırılım yok", breakdown_short),
                build_condition_result("ATAK: kapanış gücü short için zayıf", cp <= ATTACK_SHORT_MAX_CLOSE_POWER),
                build_condition_result("ATAK: risk uygun değil", MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT),
            ]

            bonus = 0
            if vol1 >= 2.0:
                bonus += 3
            if vol5 >= 1.6:
                bonus += 2
            if breakdown_short:
                bonus += 4
            if cp <= 28:
                bonus += 2

            score, ok_count, total = score_from_conditions(conditions, bonus=bonus)
            missing = missing_reasons(conditions)

            hard_ok = (
                MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT
                and (vol1 >= ATTACK_MIN_1M_VOLUME_RATIO or vol5 >= ATTACK_MIN_5M_VOLUME_RATIO)
                and (move5 <= -ATTACK_SHORT_MIN_5M_MOVE or breakdown_short)
                and cp <= 45
            )

            signal = None
            if score >= MIN_SCORE and hard_ok:
                signal = make_signal(
                    symbol, "SHORT", "ATAK_SCALP", "Atak Momentum SHORT",
                    entry, sl, score, risk_percent, market_data, ok_count, total, missing
                )

            debug = build_debug(symbol, "SHORT", "Atak Momentum SHORT", score, ok_count, total, missing, market_data, risk_percent)
            return signal, debug

    except Exception as e:
        print(symbol, direction, "atak analiz hatası:", e)
        return None, None


def analyze_symbol(exchange, symbol):
    current_price = get_current_price(exchange, symbol)

    df1 = fetch_df(exchange, symbol, "1m", limit=100, min_len=60)
    df5 = fetch_df(exchange, symbol, "5m", limit=100, min_len=60)
    df15 = fetch_df(exchange, symbol, "15m", limit=80, min_len=40)

    if df1 is None or df5 is None or df15 is None or current_price is None:
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
        "vol1": volume_ratio(df1, index=-2, period=20),
        "vol5": volume_ratio(df5, index=-2, period=20),
        "move1": candle_move_percent(c1),
        "move5": candle_move_percent(c5),
        "move15": candle_move_percent(c15),
        "lower_wick": lower_wick_percent(c1),
        "upper_wick": upper_wick_percent(c1),
        "close_power": close_power_percent(c1),
    }

    signals = []
    debug_items = []

    analyzers = [
        analyze_reaction_side,
        analyze_attack_side,
    ]

    for analyzer in analyzers:
        for direction in ["LONG", "SHORT"]:
            signal, debug = analyzer(symbol, direction, df1, df5, df15, current_price, market_data)
            if signal is not None:
                signals.append(signal)
            if debug is not None:
                debug_items.append(debug)

    # Aynı coin içinde birden fazla sinyal oluşursa en yüksek skorlu yön/kurulum kalsın.
    signals = sorted(signals, key=lambda s: s["score"], reverse=True)
    if signals:
        signals = [signals[0]]

    return signals, debug_items


# =========================
# AÇIK SCALP TAKİBİ
# =========================

def save_open_signal(state, signal):
    key = f"{signal['symbol']}_{signal['direction']}_{signal['source']}"
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
    save_state(state)


def check_open_signals(exchange, state):
    open_signals = state.get("open_scalp_signals", {})
    if not open_signals:
        print("Açık scalp sinyali yok.")
        return

    updated = {}
    max_age_seconds = MAX_OPEN_SIGNAL_MINUTES * 60

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

            if signal.get("closed") or signal.get("tp3_hit"):
                continue

            if now_ts() - opened_at > max_age_seconds:
                send_telegram(
                    f"⏳ SCALP SİNYAL SÜRESİ DOLDU\n\n"
                    f"Coin: {symbol}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {format_price(entry)}\n\n"
                    f"{MAX_OPEN_SIGNAL_MINUTES} dakika içinde netleşmediği için takipten çıkarıldı."
                )
                continue

            candles = fetch_candles_since(
                exchange,
                symbol,
                TRACK_TIMEFRAME,
                since_seconds=max(opened_at, last_checked_at - 120),
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
                            if close >= entry:
                                tp1_hit = True
                                signal["tp1_hit"] = True
                                send_telegram(
                                    f"✅ SCALP TP1 GELDİ\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: LONG 🟢\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"TP1: {format_price(tp1)}\n"
                                    f"Öneri: %50 kâr al, SL girişe çek."
                                )
                            else:
                                send_telegram(
                                    f"❌ SCALP STOP OLDU\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: LONG 🟢\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"SL: {format_price(sl)}\n"
                                    f"Güncel: {format_price(close)}"
                                )
                                closed = True
                                break

                        elif low <= sl:
                            send_telegram(
                                f"❌ SCALP STOP OLDU\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: LONG 🟢\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"SL: {format_price(sl)}\n"
                                f"Güncel: {format_price(close)}"
                            )
                            closed = True
                            break

                        elif high >= tp1:
                            tp1_hit = True
                            signal["tp1_hit"] = True
                            send_telegram(
                                f"✅ SCALP TP1 GELDİ\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: LONG 🟢\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"TP1: {format_price(tp1)}\n"
                                f"Öneri: %50 kâr al, SL girişe çek."
                            )

                    if tp1_hit and not tp2_hit and high >= tp2:
                        tp2_hit = True
                        signal["tp2_hit"] = True
                        send_telegram(
                            f"✅ SCALP TP2 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"TP2: {format_price(tp2)}"
                        )

                    if tp1_hit and not tp3_hit and high >= tp3:
                        tp3_hit = True
                        signal["tp3_hit"] = True
                        signal["closed"] = True
                        send_telegram(
                            f"🏁 SCALP TP3 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"TP3: {format_price(tp3)}\n"
                            f"Scalp maksimum hedefe ulaştı."
                        )
                        closed = True
                        break

                    if tp1_hit and low <= entry:
                        signal["closed"] = True
                        send_telegram(
                            f"🟡 SCALP KALAN GİRİŞTEN KAPANDI\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: LONG 🟢\n"
                            f"Giriş: {format_price(entry)}"
                        )
                        closed = True
                        break

                else:
                    if not tp1_hit:
                        if high >= sl and low <= tp1:
                            if close <= entry:
                                tp1_hit = True
                                signal["tp1_hit"] = True
                                send_telegram(
                                    f"✅ SCALP TP1 GELDİ\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: SHORT 🔴\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"TP1: {format_price(tp1)}\n"
                                    f"Öneri: %50 kâr al, SL girişe çek."
                                )
                            else:
                                send_telegram(
                                    f"❌ SCALP STOP OLDU\n\n"
                                    f"Coin: {symbol}\n"
                                    f"Yön: SHORT 🔴\n"
                                    f"Giriş: {format_price(entry)}\n"
                                    f"SL: {format_price(sl)}\n"
                                    f"Güncel: {format_price(close)}"
                                )
                                closed = True
                                break

                        elif high >= sl:
                            send_telegram(
                                f"❌ SCALP STOP OLDU\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: SHORT 🔴\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"SL: {format_price(sl)}\n"
                                f"Güncel: {format_price(close)}"
                            )
                            closed = True
                            break

                        elif low <= tp1:
                            tp1_hit = True
                            signal["tp1_hit"] = True
                            send_telegram(
                                f"✅ SCALP TP1 GELDİ\n\n"
                                f"Coin: {symbol}\n"
                                f"Yön: SHORT 🔴\n"
                                f"Giriş: {format_price(entry)}\n"
                                f"TP1: {format_price(tp1)}\n"
                                f"Öneri: %50 kâr al, SL girişe çek."
                            )

                    if tp1_hit and not tp2_hit and low <= tp2:
                        tp2_hit = True
                        signal["tp2_hit"] = True
                        send_telegram(
                            f"✅ SCALP TP2 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"TP2: {format_price(tp2)}"
                        )

                    if tp1_hit and not tp3_hit and low <= tp3:
                        tp3_hit = True
                        signal["tp3_hit"] = True
                        signal["closed"] = True
                        send_telegram(
                            f"🏁 SCALP TP3 GELDİ\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"TP3: {format_price(tp3)}\n"
                            f"Scalp maksimum hedefe ulaştı."
                        )
                        closed = True
                        break

                    if tp1_hit and high >= entry:
                        signal["closed"] = True
                        send_telegram(
                            f"🟡 SCALP KALAN GİRİŞTEN KAPANDI\n\n"
                            f"Coin: {symbol}\n"
                            f"Yön: SHORT 🔴\n"
                            f"Giriş: {format_price(entry)}"
                        )
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
            print(key, "scalp takip hatası:", e)
            updated[key] = signal

    state["open_scalp_signals"] = updated
    save_state(state)


# =========================
# RAPOR
# =========================

def top_reasons_text(counter, limit=6):
    if not counter:
        return "Veri yok"

    lines = []
    for reason, count in counter.most_common(limit):
        lines.append(f"• {reason}: {count}")

    return "\n".join(lines)


def candidate_line(debug):
    if not debug:
        return ""

    missing = debug.get("missing", [])
    missing_text = ", ".join(missing[:3]) if missing else "eksik yok"

    return (
        f"{debug['symbol']} {debug['direction']} | "
        f"{debug.get('setup', 'SCALP')} | "
        f"şart {debug['ok_count']}/{debug['total_conditions']} | "
        f"skor {debug['score']} | "
        f"eksik: {missing_text}"
    )


def build_no_signal_report(scanned_count, new_signal_count, reason_counter, top_candidates):
    lines = [
        f"⚡ HIZLI SCALP RADAR RAPORU\n",
        f"Bot: {BOT_NAME}",
        f"Zaman: {tr_now_text()}",
        f"Taranan coin: {scanned_count}",
        f"Yeni scalp sinyal: {new_signal_count}\n",
        f"En çok elenen şartlar:",
        top_reasons_text(reason_counter),
        f"\nSinyale en yakın adaylar:",
    ]

    if top_candidates:
        for item in top_candidates[:10]:
            lines.append("• " + candidate_line(item))
    else:
        lines.append("• Yakın aday yok")

    lines.append(
        "\nNot: Bu rapor sinyal değildir. "
        "Tepki scalp ve atak momentum scalp filtrelerinin neden sinyal üretmediğini gösterir."
    )

    return "\n".join(lines)


def should_send_no_signal_report(state):
    if not SEND_NO_SIGNAL_REPORT:
        return False

    last_report = int(state.get("last_no_signal_report", 0))
    return now_ts() - last_report >= NO_SIGNAL_REPORT_EVERY_MINUTES * 60


def mark_no_signal_report_sent(state):
    state["last_no_signal_report"] = now_ts()
    save_state(state)


# =========================
# MAIN
# =========================

def main():
    print(BOT_NAME, "başladı.")

    state = load_state()
    exchange = get_exchange()

    # Önce açık scalp sinyallerini takip et.
    check_open_signals(exchange, state)
    state = load_state()

    scan_coins = get_scan_coins(exchange)

    open_count = len(state.get("open_scalp_signals", {}))
    available_slots = max(0, MAX_OPEN_SCALP_SIGNALS - open_count)

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

            signals, debug_items = analyze_symbol(exchange, symbol)

            for debug in debug_items:
                for reason in debug.get("missing", []):
                    reason_counter[reason] += 1
                top_candidates.append(debug)

            for signal in signals:
                if is_recent_duplicate(state, signal["symbol"], signal["direction"]):
                    print(signal["symbol"], signal["direction"], "duplicate, atlandı.")
                    continue

                all_signals.append(signal)

            time.sleep(0.08)

        except Exception as e:
            print(symbol, "genel analiz hatası:", e)

    all_signals = sorted(all_signals, key=lambda s: s["score"], reverse=True)

    top_candidates = sorted(
        top_candidates,
        key=lambda x: (x.get("score", 0), x.get("ok_count", 0)),
        reverse=True,
    )

    selected = all_signals[:min(MAX_NEW_SIGNALS_PER_RUN, available_slots)]

    print("Bulunan scalp sinyal:", len(all_signals))
    print("Gönderilecek scalp sinyal:", len(selected))

    if selected:
        send_telegram(
            f"⚡ {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {scanned}\n"
            f"Bulunan scalp sinyal: {len(all_signals)}\n"
            f"Açık scalp: {open_count}/{MAX_OPEN_SCALP_SIGNALS}\n"
            f"Gönderilecek sinyal: {len(selected)}"
        )

    for signal in selected:
        extra = (
            f"\n💰 Güncel Fiyat: {format_price(signal['entry'])}\n"
            f"📌 Son Kontrol: Scalp girişe yakın ✅"
        )

        if send_telegram(signal["message"] + extra):
            save_open_signal(state, signal)
            mark_sent(state, signal["symbol"], signal["direction"])
            state = load_state()
            time.sleep(1)

    if not selected:
        print("Yeni scalp sinyal yok.")

        if should_send_no_signal_report(state):
            report = build_no_signal_report(
                scanned_count=scanned,
                new_signal_count=len(all_signals),
                reason_counter=reason_counter,
                top_candidates=top_candidates,
            )
            send_telegram(report)
            mark_no_signal_report_sent(state)

    print(BOT_NAME, "tamamlandı.")


if __name__ == "__main__":
    main()
