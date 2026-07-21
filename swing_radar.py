# swing_radar.py
# Swing Radar
# OKX USDT Futures icin 1D + 4H + 1H swing takip sistemi.
# Emir acmaz. Telegram sinyali ve TP/SL takibi gonderir.
# TOKEN ve CHAT_ID GitHub Secrets icinden okunur.

import os
import time
import json
import math
import requests
from collections import Counter
from datetime import datetime, timezone, timedelta

import ccxt
import pandas as pd


BOT_NAME = "Swing Radar"
TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
STATE_FILE = "swing_radar_state.json"
TR_TIMEZONE = timezone(timedelta(hours=3))

MAX_SCAN_COINS = 220
MIN_24H_QUOTE_VOLUME = 500_000
MAX_NEW_SIGNALS_PER_RUN = 3
MAX_OPEN_SWING_SIGNALS = 5
DUPLICATE_SECONDS = 18 * 60 * 60
TRACK_TIMEFRAME = "1h"
TRACK_LIMIT = 240
MAX_OPEN_SIGNAL_HOURS = 120
SEND_NO_SIGNAL_REPORT = True
NO_SIGNAL_REPORT_EVERY_MINUTES = 360
MIN_SCORE = 78
MIN_RISK_PERCENT = 0.80
MAX_RISK_PERCENT = 5.00
TP1_R = 0.80
TP2_R = 1.60
TP3_R = 2.50
MAX_DISTANCE_FROM_1H_EMA20_PERCENT = 3.20
MAX_DISTANCE_FROM_4H_EMA20_PERCENT = 5.50
MIN_ADX_1H = 16
MIN_ADX_4H = 15
MIN_VOLUME_RATIO = 0.75
D1_LIMIT = 260
H4_LIMIT = 260
H1_LIMIT = 260


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


def empty_state():
    return {"open_swing_signals": {}, "last_sent": {}, "last_no_signal_report": 0}


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
                data = empty_state()
        data.setdefault("open_swing_signals", {})
        data.setdefault("last_sent", {})
        data.setdefault("last_no_signal_report", 0)
        return data
    except Exception as e:
        print("State okuma hatası:", e)
        return empty_state()


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state if isinstance(state, dict) else empty_state(), f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        print("State kaydetme hatası:", e)
        return False


def now_ts():
    return int(time.time())


def tr_now_text():
    return datetime.now(TR_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def get_exchange():
    return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})


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
            volume = safe_quote_volume(tickers.get(okx_symbol, {}))
            if volume >= MIN_24H_QUOTE_VOLUME:
                rows.append((okx_symbol_to_bot_symbol(okx_symbol), volume))
        rows = sorted(rows, key=lambda x: x[1], reverse=True)
        coins = [coin for coin, _ in rows][:MAX_SCAN_COINS]
        print("Taranacak swing coin sayısı:", len(coins))
        print("İlk 20:", coins[:20])
        return coins
    except Exception as e:
        print("Coin tarama hatası:", e)
        return []


def fetch_df(exchange, symbol, timeframe, limit=200, min_len=60):
    try:
        ohlcv = exchange.fetch_ohlcv(to_okx_symbol(symbol), timeframe=timeframe, limit=limit)
        if not ohlcv or len(ohlcv) < min_len:
            return None
        df = pd.DataFrame(ohlcv, columns=["time", "open", "high", "low", "close", "volume"])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna()
        if len(df) < min_len:
            return None
        return df
    except Exception as e:
        print(symbol, timeframe, "veri hatası:", e)
        return None


def fetch_candles_since(exchange, symbol, timeframe, since_seconds, limit=240):
    try:
        ohlcv = exchange.fetch_ohlcv(
            to_okx_symbol(symbol),
            timeframe=timeframe,
            since=max(0, int(since_seconds)) * 1000,
            limit=limit,
        )
        return [
            {"time": int(item[0] / 1000), "open": float(item[1]), "high": float(item[2]), "low": float(item[3]), "close": float(item[4]), "volume": float(item[5])}
            for item in ohlcv
        ]
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


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 0.0000001)
    return 100 - (100 / (1 + rs))


def calc_atr(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def calc_adx(df, period=14):
    high = df["high"]
    low = df["low"]
    close = df["close"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, 0.0000001)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, 0.0000001)
    dx = (abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, 0.0000001)) * 100
    return dx.ewm(alpha=1 / period, adjust=False).mean()


def add_indicators(df):
    df = df.copy()
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["rsi"] = calc_rsi(df["close"])
    df["atr"] = calc_atr(df)
    df["adx"] = calc_adx(df)
    df["volume_avg"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg"].replace(0, 0.0000001)
    return df


def pct(a, b):
    try:
        if b == 0:
            return 0.0
        return ((a - b) / b) * 100
    except Exception:
        return 0.0


def abs_pct(a, b):
    return abs(pct(a, b))


def candle_is_green(row):
    return float(row["close"]) > float(row["open"])


def candle_is_red(row):
    return float(row["close"]) < float(row["open"])


def rolling_support(df, lookback=80):
    try:
        return float(df["low"].iloc[-lookback:-2].min())
    except Exception:
        return float(df["low"].iloc[-20:-2].min())


def rolling_resistance(df, lookback=80):
    try:
        return float(df["high"].iloc[-lookback:-2].max())
    except Exception:
        return float(df["high"].iloc[-20:-2].max())


def build_condition(label, ok):
    return {"label": label, "ok": bool(ok)}


def score_from_conditions(conditions, bonus=0):
    ok_count = sum(1 for c in conditions if c["ok"])
    total = max(1, len(conditions))
    score = int((ok_count / total) * 100) + int(bonus)
    return max(0, min(100, score)), ok_count, total


def missing_reasons(conditions):
    return [c["label"] for c in conditions if not c["ok"]]


def leverage_text(risk_percent):
    try:
        risk_percent = float(risk_percent)
    except Exception:
        return "1x-2x"
    if risk_percent <= 1.50:
        return "1x-2x"
    if risk_percent <= 3.00:
        return "1x"
    return "1x / çok düşük risk"


def build_signal_message(signal):
    icon = "🟢" if signal["direction"] == "LONG" else "🔴"
    quality = "A" if signal["score"] >= 88 else "B"
    return (
        f"📈 {BOT_NAME}\n\n"
        f"{icon} {signal['direction']}\n"
        f"🟡 Coin: {signal['symbol']}\n"
        f"⏱️ Kaynak: {signal['source']}\n"
        f"📌 Kurulum: {signal['setup']}\n\n"
        f"📌 Giriş: {format_price(signal['entry'])}\n"
        f"📍 Giriş Bölgesi: {format_price(signal['entry_low'])} - {format_price(signal['entry_high'])}\n"
        f"🎯 TP1: {format_price(signal['tp1'])}\n"
        f"🎯 TP2: {format_price(signal['tp2'])}\n"
        f"🎯 TP3: {format_price(signal['tp3'])}\n"
        f"🛑 SL: {format_price(signal['sl'])}\n\n"
        f"📊 Skor: %{signal['score']} ({quality} Swing)\n"
        f"🛡️ Stop Mesafesi: %{round(signal['risk_percent'], 2)}\n"
        f"⚙️ Kaldıraç Önerisi: {leverage_text(signal['risk_percent'])}\n\n"
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
        f"• Swing sinyalidir; scalp gibi hızlı gir-çık değildir.\n"
        f"• Giriş bölgesinden uzaklaştıysa acele etme.\n"
        f"• TP1 gelirse %50 kâr al, SL girişe çek.\n"
        f"• Stop mutlaka girilmeli.\n"
        f"• Marjin: Isolated.\n"
        f"• Kaldıraç düşük tutulmalı.\n\n"
        f"⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işlem açma."
    )


def analyze_direction(symbol, direction, df1d, df4h, df1h, current_price):
    try:
        if df1d is None or df4h is None or df1h is None or current_price is None:
            return None, None
        d1 = add_indicators(df1d)
        h4 = add_indicators(df4h)
        h1 = add_indicators(df1h)
        last_d1 = d1.iloc[-2]
        last_h4 = h4.iloc[-2]
        last_h1 = h1.iloc[-2]
        prev_h1 = h1.iloc[-3]
        entry = float(current_price)
        atr_4h = float(last_h4["atr"])
        atr_1h = float(last_h1["atr"])
        if atr_4h <= 0 or atr_1h <= 0 or entry <= 0:
            return None, None
        support = rolling_support(h4, 80)
        resistance = rolling_resistance(h4, 80)
        d_close = float(last_d1["close"])
        d_ema20 = float(last_d1["ema20"])
        d_ema50 = float(last_d1["ema50"])
        d_ema200 = float(last_d1["ema200"])
        h4_close = float(last_h4["close"])
        h4_ema20 = float(last_h4["ema20"])
        h4_ema50 = float(last_h4["ema50"])
        h4_ema200 = float(last_h4["ema200"])
        h1_close = float(last_h1["close"])
        h1_ema20 = float(last_h1["ema20"])
        h1_ema50 = float(last_h1["ema50"])
        rsi_d1 = float(last_d1["rsi"])
        rsi_4h = float(last_h4["rsi"])
        rsi_1h = float(last_h1["rsi"])
        adx_4h = float(last_h4["adx"])
        adx_1h = float(last_h1["adx"])
        vol_4h = float(last_h4["volume_ratio"]) if not math.isnan(float(last_h4["volume_ratio"])) else 0.0
        vol_1h = float(last_h1["volume_ratio"]) if not math.isnan(float(last_h1["volume_ratio"])) else 0.0
        dist_1h_ema20 = abs_pct(entry, h1_ema20)
        dist_4h_ema20 = abs_pct(entry, h4_ema20)

        if direction == "LONG":
            atr_stop = entry - (atr_4h * 1.15)
            support_stop = support * 0.995
            sl = max(min(atr_stop, entry * 0.992), support_stop)
            if sl >= entry:
                sl = entry - atr_4h * 1.10
            risk = entry - sl
            risk_percent = (risk / entry) * 100
            tp1 = entry + risk * TP1_R
            tp2 = entry + risk * TP2_R
            tp3 = entry + risk * TP3_R
            entry_low = entry - atr_1h * 0.35
            entry_high = entry + atr_1h * 0.25
            d1_trend = d_close > d_ema50 and d_ema20 >= d_ema50
            d1_safe = d_close > d_ema200 or d_ema50 > d_ema200
            h4_trend = h4_close > h4_ema50 and h4_ema20 >= h4_ema50
            h4_safe = h4_close > h4_ema200 or h4_ema50 >= h4_ema200
            h1_confirm = h1_close > h1_ema20 or (candle_is_green(last_h1) and h1_close > h1_ema50)
            h1_turn = candle_is_green(last_h1) or h1_close > float(prev_h1["close"])
            rsi_ok = 42 <= rsi_1h <= 68 and rsi_4h <= 72 and rsi_d1 <= 74
            d1_note = "1D trend yukarı" if d1_trend else "1D trend zayıf"
            h4_note = "4H trend yukarı" if h4_trend else "4H trend zayıf/karışık"
            h1_note = "1H alış onayı" if h1_confirm else "1H onay zayıf"
            setup = "1D + 4H trend uyumlu Swing LONG"
        else:
            atr_stop = entry + (atr_4h * 1.15)
            resistance_stop = resistance * 1.005
            sl = min(max(atr_stop, entry * 1.008), resistance_stop)
            if sl <= entry:
                sl = entry + atr_4h * 1.10
            risk = sl - entry
            risk_percent = (risk / entry) * 100
            tp1 = entry - risk * TP1_R
            tp2 = entry - risk * TP2_R
            tp3 = entry - risk * TP3_R
            entry_low = entry - atr_1h * 0.25
            entry_high = entry + atr_1h * 0.35
            d1_trend = d_close < d_ema50 and d_ema20 <= d_ema50
            d1_safe = d_close < d_ema200 or d_ema50 < d_ema200
            h4_trend = h4_close < h4_ema50 and h4_ema20 <= h4_ema50
            h4_safe = h4_close < h4_ema200 or h4_ema50 <= h4_ema200
            h1_confirm = h1_close < h1_ema20 or (candle_is_red(last_h1) and h1_close < h1_ema50)
            h1_turn = candle_is_red(last_h1) or h1_close < float(prev_h1["close"])
            rsi_ok = 32 <= rsi_1h <= 58 and rsi_4h >= 25 and rsi_d1 >= 22
            d1_note = "1D trend aşağı" if d1_trend else "1D trend zayıf"
            h4_note = "4H trend aşağı" if h4_trend else "4H trend zayıf/karışık"
            h1_note = "1H satış onayı" if h1_confirm else "1H onay zayıf"
            setup = "1D + 4H trend uyumlu Swing SHORT"

        adx_ok = adx_4h >= MIN_ADX_4H or adx_1h >= MIN_ADX_1H
        volume_ok = vol_1h >= MIN_VOLUME_RATIO or vol_4h >= MIN_VOLUME_RATIO
        not_extended = dist_1h_ema20 <= MAX_DISTANCE_FROM_1H_EMA20_PERCENT and dist_4h_ema20 <= MAX_DISTANCE_FROM_4H_EMA20_PERCENT
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
            build_condition("risk uygun değil", MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT),
        ]
        bonus = 0
        if adx_4h >= 22:
            bonus += 3
        if adx_1h >= 22:
            bonus += 3
        if vol_1h >= 1.20 or vol_4h >= 1.20:
            bonus += 3
        if direction == "LONG" and 50 <= rsi_1h <= 62:
            bonus += 2
        if direction == "SHORT" and 38 <= rsi_1h <= 52:
            bonus += 2
        score, ok_count, total = score_from_conditions(conditions, bonus=bonus)
        hard_ok = (
            MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT
            and d1_trend and h4_trend and h1_confirm and not_extended
        )
        if direction == "LONG":
            hard_ok = hard_ok and rsi_1h <= 70
        else:
            hard_ok = hard_ok and rsi_1h >= 28
        debug = {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "ok_count": ok_count,
            "total_conditions": total,
            "missing": missing_reasons(conditions),
            "risk_percent": risk_percent,
            "rsi_1h": rsi_1h,
            "adx_4h": adx_4h,
            "adx_1h": adx_1h,
            "vol_1h": vol_1h,
            "vol_4h": vol_4h,
        }
        if score < MIN_SCORE or not hard_ok:
            return None, debug
        signal = {
            "symbol": symbol, "direction": direction, "source": "SWING_RADAR", "setup": setup,
            "entry": entry, "entry_low": entry_low, "entry_high": entry_high,
            "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl,
            "score": score, "risk_percent": risk_percent,
            "d1_note": d1_note, "h4_note": h4_note, "h1_note": h1_note,
            "rsi_d1": rsi_d1, "rsi_4h": rsi_4h, "rsi_1h": rsi_1h,
            "adx_4h": adx_4h, "adx_1h": adx_1h, "vol_4h": vol_4h, "vol_1h": vol_1h,
            "support": support, "resistance": resistance,
            "ok_count": ok_count, "total_conditions": total, "missing": missing_reasons(conditions),
        }
        signal["message"] = build_signal_message(signal)
        return signal, debug
    except Exception as e:
        print(symbol, direction, "swing analiz hatası:", e)
        return None, None


def analyze_symbol(exchange, symbol):
    current_price = get_current_price(exchange, symbol)
    df1d = fetch_df(exchange, symbol, "1d", limit=D1_LIMIT, min_len=220)
    df4h = fetch_df(exchange, symbol, "4h", limit=H4_LIMIT, min_len=220)
    df1h = fetch_df(exchange, symbol, "1h", limit=H1_LIMIT, min_len=220)
    long_signal, long_debug = analyze_direction(symbol, "LONG", df1d, df4h, df1h, current_price)
    short_signal, short_debug = analyze_direction(symbol, "SHORT", df1d, df4h, df1h, current_price)
    signals = []
    if long_signal is not None:
        signals.append(long_signal)
    if short_signal is not None:
        signals.append(short_signal)
    return signals, long_debug, short_debug


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
    for signal in state.get("open_swing_signals", {}).values():
        if signal.get("symbol") == symbol:
            return True
    return False


def save_open_signal(state, signal):
    key = f"{signal['symbol']}_{signal['direction']}_{signal['source']}"
    state.setdefault("open_swing_signals", {})
    state["open_swing_signals"][key] = {
        "symbol": signal["symbol"], "direction": signal["direction"], "source": signal["source"],
        "entry": signal["entry"], "tp1": signal["tp1"], "tp2": signal["tp2"], "tp3": signal["tp3"], "sl": signal["sl"],
        "score": signal["score"], "risk_percent": signal["risk_percent"],
        "opened_at": now_ts(), "last_checked_at": now_ts(),
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False, "closed": False,
    }
    save_state(state)


def check_open_signals(exchange, state):
    open_signals = state.get("open_swing_signals", {})
    if not open_signals:
        print("Açık swing sinyali yok.")
        return
    updated = {}
    max_age_seconds = MAX_OPEN_SIGNAL_HOURS * 60 * 60
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
                    f"⏳ SWING SİNYAL SÜRESİ DOLDU\n\n"
                    f"Coin: {symbol}\nYön: {direction}\nGiriş: {format_price(entry)}\n\n"
                    f"{MAX_OPEN_SIGNAL_HOURS} saat içinde netleşmediği için takipten çıkarıldı."
                )
                continue
            candles = fetch_candles_since(
                exchange, symbol, TRACK_TIMEFRAME,
                since_seconds=max(opened_at, last_checked_at - 2 * 60 * 60),
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
                                send_telegram(f"✅ SWING TP1 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}\nTP1: {format_price(tp1)}\nÖneri: %50 kâr al, SL girişe çek.")
                            else:
                                send_telegram(f"❌ SWING STOP OLDU\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}\nSL: {format_price(sl)}\nGüncel: {format_price(close)}")
                                closed = True
                                break
                        elif low <= sl:
                            send_telegram(f"❌ SWING STOP OLDU\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}\nSL: {format_price(sl)}\nGüncel: {format_price(close)}")
                            closed = True
                            break
                        elif high >= tp1:
                            tp1_hit = True
                            signal["tp1_hit"] = True
                            send_telegram(f"✅ SWING TP1 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}\nTP1: {format_price(tp1)}\nÖneri: %50 kâr al, SL girişe çek.")
                    if tp1_hit and not tp2_hit and high >= tp2:
                        tp2_hit = True
                        signal["tp2_hit"] = True
                        send_telegram(f"✅ SWING TP2 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nTP2: {format_price(tp2)}")
                    if tp1_hit and not tp3_hit and high >= tp3:
                        tp3_hit = True
                        signal["tp3_hit"] = True
                        signal["closed"] = True
                        send_telegram(f"🏁 SWING TP3 GELDİ\n\nCoin: {symbol}\nYön: LONG 🟢\nTP3: {format_price(tp3)}\nSwing maksimum hedefe ulaştı.")
                        closed = True
                        break
                    if tp1_hit and low <= entry:
                        signal["closed"] = True
                        send_telegram(f"🟡 SWING KALAN GİRİŞTEN KAPANDI\n\nCoin: {symbol}\nYön: LONG 🟢\nGiriş: {format_price(entry)}")
                        closed = True
                        break
                else:
                    if not tp1_hit:
                        if high >= sl and low <= tp1:
                            if close <= entry:
                                tp1_hit = True
                                signal["tp1_hit"] = True
                                send_telegram(f"✅ SWING TP1 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}\nTP1: {format_price(tp1)}\nÖneri: %50 kâr al, SL girişe çek.")
                            else:
                                send_telegram(f"❌ SWING STOP OLDU\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}\nSL: {format_price(sl)}\nGüncel: {format_price(close)}")
                                closed = True
                                break
                        elif high >= sl:
                            send_telegram(f"❌ SWING STOP OLDU\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}\nSL: {format_price(sl)}\nGüncel: {format_price(close)}")
                            closed = True
                            break
                        elif low <= tp1:
                            tp1_hit = True
                            signal["tp1_hit"] = True
                            send_telegram(f"✅ SWING TP1 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}\nTP1: {format_price(tp1)}\nÖneri: %50 kâr al, SL girişe çek.")
                    if tp1_hit and not tp2_hit and low <= tp2:
                        tp2_hit = True
                        signal["tp2_hit"] = True
                        send_telegram(f"✅ SWING TP2 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nTP2: {format_price(tp2)}")
                    if tp1_hit and not tp3_hit and low <= tp3:
                        tp3_hit = True
                        signal["tp3_hit"] = True
                        signal["closed"] = True
                        send_telegram(f"🏁 SWING TP3 GELDİ\n\nCoin: {symbol}\nYön: SHORT 🔴\nTP3: {format_price(tp3)}\nSwing maksimum hedefe ulaştı.")
                        closed = True
                        break
                    if tp1_hit and high >= entry:
                        signal["closed"] = True
                        send_telegram(f"🟡 SWING KALAN GİRİŞTEN KAPANDI\n\nCoin: {symbol}\nYön: SHORT 🔴\nGiriş: {format_price(entry)}")
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
            print(key, "swing takip hatası:", e)
            updated[key] = signal
    state["open_swing_signals"] = updated
    save_state(state)


def top_reasons_text(counter, limit=5):
    if not counter:
        return "Veri yok"
    return "\n".join([f"• {reason}: {count}" for reason, count in counter.most_common(limit)])


def candidate_line(debug):
    if not debug:
        return ""
    missing = debug.get("missing", [])
    missing_text = ", ".join(missing[:3]) if missing else "eksik yok"
    return (
        f"{debug['symbol']} {debug['direction']} | şart {debug['ok_count']}/{debug['total_conditions']} | "
        f"skor {debug['score']} | risk %{round(debug.get('risk_percent', 0), 2)} | eksik: {missing_text}"
    )


def build_no_signal_report(scanned_count, new_signal_count, long_counter, short_counter, top_candidates):
    lines = [
        f"📈 SWING RADAR RAPORU\n",
        f"Bot: {BOT_NAME}",
        f"Zaman: {tr_now_text()}",
        f"Taranan coin: {scanned_count}",
        f"Yeni swing sinyal: {new_signal_count}\n",
        f"LONG tarafında en çok elenen:",
        top_reasons_text(long_counter),
        f"\nSHORT tarafında en çok elenen:",
        top_reasons_text(short_counter),
        f"\nSwing sinyale en yakın adaylar:",
    ]
    if top_candidates:
        for item in top_candidates[:8]:
            lines.append("• " + candidate_line(item))
    else:
        lines.append("• Yakın aday yok")
    lines.append("\nNot: Bu rapor sinyal değildir. Hangi filtrelerin swing sinyalini kestiğini görmek için gönderilir.")
    return "\n".join(lines)


def should_send_no_signal_report(state):
    if not SEND_NO_SIGNAL_REPORT:
        return False
    last_report = int(state.get("last_no_signal_report", 0))
    return now_ts() - last_report >= NO_SIGNAL_REPORT_EVERY_MINUTES * 60


def mark_no_signal_report_sent(state):
    state["last_no_signal_report"] = now_ts()
    save_state(state)


def main():
    print(BOT_NAME, "başladı.")
    state = load_state()
    exchange = get_exchange()
    check_open_signals(exchange, state)
    state = load_state()
    scan_coins = get_scan_coins(exchange)
    open_count = len(state.get("open_swing_signals", {}))
    available_slots = max(0, MAX_OPEN_SWING_SIGNALS - open_count)
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
                print(symbol, "zaten açık swing var, atlandı.")
                continue
            signals, long_debug, short_debug = analyze_symbol(exchange, symbol)
            if long_debug:
                for reason in long_debug.get("missing", []):
                    long_reasons[reason] += 1
                top_candidates.append(long_debug)
            if short_debug:
                for reason in short_debug.get("missing", []):
                    short_reasons[reason] += 1
                top_candidates.append(short_debug)
            for signal in signals:
                if is_recent_duplicate(state, signal["symbol"], signal["direction"]):
                    print(signal["symbol"], signal["direction"], "duplicate, atlandı.")
                    continue
                all_signals.append(signal)
            time.sleep(0.08)
        except Exception as e:
            print(symbol, "genel swing analiz hatası:", e)
    all_signals = sorted(all_signals, key=lambda s: s["score"], reverse=True)
    top_candidates = sorted(top_candidates, key=lambda x: (x.get("score", 0), x.get("ok_count", 0)), reverse=True)
    selected = all_signals[:min(MAX_NEW_SIGNALS_PER_RUN, available_slots)]
    print("Bulunan swing sinyal:", len(all_signals))
    print("Gönderilecek swing sinyal:", len(selected))
    if selected:
        send_telegram(
            f"📈 {BOT_NAME} çalıştı.\n"
            f"Taranan coin: {scanned}\n"
            f"Bulunan swing sinyal: {len(all_signals)}\n"
            f"Açık swing: {open_count}/{MAX_OPEN_SWING_SIGNALS}\n"
            f"Gönderilecek sinyal: {len(selected)}"
        )
    for signal in selected:
        current_price = get_current_price(exchange, signal["symbol"])
        extra = f"\n💰 Güncel Fiyat: {format_price(current_price if current_price else signal['entry'])}\n📌 Son Kontrol: Swing giriş bölgesinde ✅"
        if send_telegram(signal["message"] + extra):
            save_open_signal(state, signal)
            mark_sent(state, signal["symbol"], signal["direction"])
            state = load_state()
            time.sleep(1)
    if not selected:
        print("Yeni swing sinyal yok.")
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
