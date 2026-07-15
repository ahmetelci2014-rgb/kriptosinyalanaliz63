# strategy.py
# Sade Premium V1 - Kontrollü LONG/SHORT strateji
# LONG: 4H yukarı + 1H yukarı + 15M geri çekilme sonrası toparlanma.
# SHORT: 4H aşağı + 1H aşağı + 15M tepki sonrası aşağı dönüş.
# Emir açmaz; main.py sadece Telegram sinyali gönderir.

from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange

from config import (
    MIN_SCORE,
    SHORT_MIN_SCORE,
    MIN_ADX_4H,
    MIN_ADX_1H,
    MIN_VOLUME_RATIO,
    MIN_RISK_PERCENT,
    MAX_RISK_PERCENT
)


def format_price(value):
    value = float(value)

    if value >= 100:
        return f"{value:.2f}"
    elif value >= 1:
        return f"{value:.4f}"
    elif value >= 0.01:
        return f"{value:.6f}"
    else:
        return f"{value:.10f}"


def add_indicators(df):
    if df is None or df.empty:
        return None

    df = df.copy()

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()

    df["atr"] = AverageTrueRange(
        df["high"],
        df["low"],
        df["close"],
        window=14
    ).average_true_range()

    df["adx"] = ADXIndicator(
        df["high"],
        df["low"],
        df["close"],
        window=14
    ).adx()

    df["volume_avg"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg"]
    df["ema20_slope"] = df["ema20"] - df["ema20"].shift(3)

    df = df.dropna().reset_index(drop=True)

    if df.empty:
        return None

    return df


def reject(symbol, reason):
    print(f"{symbol}: elendi -> {reason}")
    return None


def get_closed_row(df):
    if df is None or len(df) < 3:
        return None

    return df.iloc[-2]


def build_message(symbol, direction, price, tp1, tp2, tp3, sl, rsi, adx15, volume_ratio, risk_percent, score, notes):
    icon = "🟢" if direction == "LONG" else "🔴"
    trend_text = "yukarı" if direction == "LONG" else "aşağı"
    entry_text = "geri çekilme sonrası toparlanma" if direction == "LONG" else "tepki sonrası aşağı dönüş"
    notes_text = "\n".join([f"• {n}" for n in notes])

    return f"""
🚀 SADE PREMIUM V1 FUTURES SİNYALİ

{icon} {direction}
🟡 Coin: {symbol}

🔥 Giriş: {format_price(price)}
🎯 TP1: {format_price(tp1)}
🎯 TP2: {format_price(tp2)}
🎯 TP3: {format_price(tp3)}
🔴 SL: {format_price(sl)}

📊 15M RSI: {round(rsi, 2)}
💪 15M ADX: {round(adx15, 2)}
📊 Hacim: {round(volume_ratio, 2)}x
🛡️ Stop Mesafesi: %{round(risk_percent, 2)}
🔥 Skor: %{min(int(score), 100)}

📈 Sistem Onayı:
• 4H ana trend {trend_text} ✅
• 1H onay {trend_text} ✅
• 15M {entry_text} ✅
• LONG/SHORT kontrollü açık ✅

📋 Kalite Notları:
{notes_text}

📌 İşlem Kuralı:
• Fiyat girişe yakınsa değerlendir.
• TP1'e yaklaşmışsa işleme girme.
• TP1 gelirse %50 kâr al, SL'yi girişe çek.
• Stop mutlaka girilmeli.
• Marjin: Isolated.
• Kaldıraç: 2x - 3x.

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işleme girme.
"""


def build_long(symbol, df15m, df1h, df4h):
    last_15 = get_closed_row(df15m)
    prev_15 = df15m.iloc[-3]
    last_1h = get_closed_row(df1h)
    last_4h = get_closed_row(df4h)

    if float(last_4h["close"]) <= float(last_4h["ema200"]):
        return None
    if float(last_4h["ema20"]) <= float(last_4h["ema50"]):
        return None
    if float(last_4h["ema20_slope"]) <= 0:
        return None
    if float(last_4h["adx"]) < MIN_ADX_4H:
        return None

    if float(last_1h["close"]) <= float(last_1h["ema200"]):
        return None
    if float(last_1h["ema20"]) <= float(last_1h["ema50"]):
        return None
    if float(last_1h["macd"]) <= float(last_1h["macd_signal"]):
        return None
    if float(last_1h["rsi"]) < 50:
        return None
    if float(last_1h["adx"]) < MIN_ADX_1H:
        return None

    price = float(last_15["close"])
    atr = float(last_15["atr"])

    if price <= 0 or atr <= 0:
        return None

    if float(last_15["close"]) <= float(last_15["ema200"]):
        return None

    ema20_distance = float(last_15["close"]) - float(last_15["ema20"])

    if ema20_distance < 0:
        return None
    if ema20_distance > atr * 1.20:
        return None

    pullback_happened = (
        float(prev_15["close"]) <= float(prev_15["ema20"])
        or float(prev_15["low"]) <= float(prev_15["ema20"]) * 1.003
    )

    if not pullback_happened:
        return None
    if float(last_15["close"]) <= float(prev_15["close"]):
        return None

    rsi = float(last_15["rsi"])

    if not (45 <= rsi <= 64):
        return None
    if float(last_15["macd"]) <= float(last_15["macd_signal"]):
        return None

    volume_ratio = float(last_15["volume_ratio"])

    if volume_ratio < MIN_VOLUME_RATIO:
        return None

    recent_low = float(df15m["low"].iloc[-10:-1].min())
    sl_atr = price - (atr * 1.8)
    sl_swing = recent_low - (atr * 0.20)
    sl = min(sl_atr, sl_swing)
    risk = price - sl

    if risk <= 0:
        return None

    risk_percent = (risk / price) * 100

    if risk_percent < MIN_RISK_PERCENT or risk_percent > MAX_RISK_PERCENT:
        return None

    tp1 = price + risk * 1.00
    tp2 = price + risk * 1.70
    tp3 = price + risk * 2.50

    score = 0
    notes = []

    if float(last_4h["adx"]) >= 25:
        score += 25
        notes.append("4H trend güçlü")
    else:
        score += 15
        notes.append("4H trend orta")

    if float(last_1h["adx"]) >= 25:
        score += 20
        notes.append("1H onay güçlü")
    else:
        score += 10
        notes.append("1H onay orta")

    if 48 <= rsi <= 58:
        score += 20
        notes.append("15M RSI ideal")
    else:
        score += 10
        notes.append("15M RSI kabul edilebilir")

    if volume_ratio >= 1.20:
        score += 20
        notes.append("Hacim güçlü")
    else:
        score += 10
        notes.append("Hacim yeterli")

    if float(last_15["adx"]) >= 20:
        score += 15
        notes.append("15M hareket güçlü")
    else:
        score += 10
        notes.append("15M hareket orta")

    if score < MIN_SCORE:
        return None

    message = build_message(
        symbol=symbol,
        direction="LONG",
        price=price,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        sl=sl,
        rsi=rsi,
        adx15=float(last_15["adx"]),
        volume_ratio=volume_ratio,
        risk_percent=risk_percent,
        score=score,
        notes=notes
    )

    return {
        "symbol": symbol,
        "direction": "LONG",
        "entry": round(price, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "sl": round(sl, 10),
        "score": int(score),
        "risk_percent": round(risk_percent, 3),
        "message": message
    }


def build_short(symbol, df15m, df1h, df4h):
    last_15 = get_closed_row(df15m)
    prev_15 = df15m.iloc[-3]
    last_1h = get_closed_row(df1h)
    last_4h = get_closed_row(df4h)

    # SHORT tarafı eski backtestte zayıf çıktığı için daha sıkı tutulur.
    if float(last_4h["close"]) >= float(last_4h["ema200"]):
        return None
    if float(last_4h["ema20"]) >= float(last_4h["ema50"]):
        return None
    if float(last_4h["ema20_slope"]) >= 0:
        return None
    if float(last_4h["adx"]) < (MIN_ADX_4H + 2):
        return None

    if float(last_1h["close"]) >= float(last_1h["ema200"]):
        return None
    if float(last_1h["ema20"]) >= float(last_1h["ema50"]):
        return None
    if float(last_1h["macd"]) >= float(last_1h["macd_signal"]):
        return None
    if float(last_1h["rsi"]) > 50:
        return None
    if float(last_1h["adx"]) < (MIN_ADX_1H + 2):
        return None

    price = float(last_15["close"])
    atr = float(last_15["atr"])

    if price <= 0 or atr <= 0:
        return None

    if float(last_15["close"]) >= float(last_15["ema200"]):
        return None

    ema20_distance = float(last_15["ema20"]) - float(last_15["close"])

    if ema20_distance < 0:
        return None
    if ema20_distance > atr * 1.20:
        return None

    pullback_happened = (
        float(prev_15["close"]) >= float(prev_15["ema20"])
        or float(prev_15["high"]) >= float(prev_15["ema20"]) * 0.997
    )

    if not pullback_happened:
        return None
    if float(last_15["close"]) >= float(prev_15["close"]):
        return None

    rsi = float(last_15["rsi"])

    if not (36 <= rsi <= 55):
        return None
    if float(last_15["macd"]) >= float(last_15["macd_signal"]):
        return None

    volume_ratio = float(last_15["volume_ratio"])

    if volume_ratio < (MIN_VOLUME_RATIO + 0.10):
        return None

    recent_high = float(df15m["high"].iloc[-10:-1].max())
    sl_atr = price + (atr * 1.8)
    sl_swing = recent_high + (atr * 0.20)
    sl = max(sl_atr, sl_swing)
    risk = sl - price

    if risk <= 0:
        return None

    risk_percent = (risk / price) * 100

    if risk_percent < MIN_RISK_PERCENT or risk_percent > MAX_RISK_PERCENT:
        return None

    tp1 = price - risk * 1.00
    tp2 = price - risk * 1.70
    tp3 = price - risk * 2.50

    if tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
        return None

    score = 0
    notes = []

    if float(last_4h["adx"]) >= 25:
        score += 25
        notes.append("4H düşüş trendi güçlü")
    else:
        score += 15
        notes.append("4H düşüş trendi orta")

    if float(last_1h["adx"]) >= 25:
        score += 20
        notes.append("1H satış onayı güçlü")
    else:
        score += 10
        notes.append("1H satış onayı orta")

    if 40 <= rsi <= 50:
        score += 20
        notes.append("15M RSI short için ideal")
    else:
        score += 10
        notes.append("15M RSI short için kabul edilebilir")

    if volume_ratio >= 1.30:
        score += 20
        notes.append("Satış hacmi güçlü")
    else:
        score += 10
        notes.append("Hacim yeterli")

    if float(last_15["adx"]) >= 22:
        score += 15
        notes.append("15M düşüş hareketi güçlü")
    else:
        score += 10
        notes.append("15M düşüş hareketi orta")

    if score < SHORT_MIN_SCORE:
        return None

    message = build_message(
        symbol=symbol,
        direction="SHORT",
        price=price,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        sl=sl,
        rsi=rsi,
        adx15=float(last_15["adx"]),
        volume_ratio=volume_ratio,
        risk_percent=risk_percent,
        score=score,
        notes=notes
    )

    return {
        "symbol": symbol,
        "direction": "SHORT",
        "entry": round(price, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "sl": round(sl, 10),
        "score": int(score),
        "risk_percent": round(risk_percent, 3),
        "message": message
    }


def analyze_signal(symbol, df15m, df1h, df4h):
    df15m = add_indicators(df15m)
    df1h = add_indicators(df1h)
    df4h = add_indicators(df4h)

    if df15m is None or len(df15m) < 30:
        return reject(symbol, "15M veri/indikatör yetersiz")
    if df1h is None or len(df1h) < 30:
        return reject(symbol, "1H veri/indikatör yetersiz")
    if df4h is None or len(df4h) < 30:
        return reject(symbol, "4H veri/indikatör yetersiz")

    long_signal = build_long(symbol, df15m, df1h, df4h)
    short_signal = build_short(symbol, df15m, df1h, df4h)

    candidates = [s for s in [long_signal, short_signal] if s is not None]

    if not candidates:
        return reject(symbol, "LONG/SHORT uygun sinyal yok")

    best = sorted(candidates, key=lambda x: x["score"], reverse=True)[0]

    print(
        f"{symbol}: SİNYAL -> {best['direction']} | skor: {best['score']} | "
        f"risk: %{best['risk_percent']}"
    )

    return best
