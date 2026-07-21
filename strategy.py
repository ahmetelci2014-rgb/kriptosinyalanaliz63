# strategy.py
# Premium MTF TP Odaklı v2 - Akıllı Kalite v2
# Ana mantık:
# 4H = ana trend
# 1H = yön onayı
# 15M = giriş mumu / pullback / dönüş
# 5M = erken radar / momentum uyarısı
#
# Bu sürüm sinyal üretme mantığını bozmaz.
# Eklenen ana yenilik: A+ / A / A- / TP1 odaklı akıllı kalite ayrımı.

from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange

from config import (
    MIN_SCORE_TRADE,
    MIN_SCORE_RADAR,
    MIN_ADX_4H,
    MIN_ADX_1H,
    MIN_VOLUME_RATIO_15M,
    LONG_RSI_MIN,
    LONG_RSI_MAX,
    SHORT_RSI_MIN,
    SHORT_RSI_MAX,
    RADAR_MIN_5M_MOVE_PERCENT,
    RADAR_MAX_5M_MOVE_PERCENT,
    RADAR_MIN_VOLUME_RATIO,
    RADAR_TRADE_MIN_SCORE,
    RADAR_TRADE_MIN_VOLUME_RATIO,
    MIN_RISK_PERCENT,
    MAX_RISK_PERCENT,
    TP1_R_MULTIPLIER,
    TP2_R_MULTIPLIER,
    TP3_R_MULTIPLIER,
    LEVERAGE_RISK_3X_MAX,
    LEVERAGE_RISK_2X_MAX,
    LEVERAGE_RISK_1X2X_MAX,
)


def format_price(value):
    value = float(value)

    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.10f}"


def safe_float(value, default=0.0):
    try:
        if value in [None, "", "-"]:
            return default
        return float(value)
    except Exception:
        return default


def add_indicators(df):
    if df is None or df.empty:
        return None

    df = df.copy()

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()
    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

    df["atr"] = AverageTrueRange(
        df["high"],
        df["low"],
        df["close"],
        window=14,
    ).average_true_range()

    df["adx"] = ADXIndicator(
        df["high"],
        df["low"],
        df["close"],
        window=14,
    ).adx()

    df["volume_avg"] = df["volume"].rolling(20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_avg"]
    df["ema20_slope"] = df["ema20"] - df["ema20"].shift(3)

    return df.dropna().reset_index(drop=True)


def get_4h_trend(df4h):
    df = add_indicators(df4h)

    if df is None or len(df) < 20:
        return "NEUTRAL", "4H veri yetersiz", {}

    row = df.iloc[-2]

    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    ema200 = float(row["ema200"])
    slope = float(row["ema20_slope"])
    adx = float(row["adx"])
    rsi = float(row["rsi"])

    info = {
        "adx_4h": round(adx, 2),
        "rsi_4h": round(rsi, 2),
        "close_4h": round(close, 8),
    }

    if close > ema200 and ema20 > ema50 and slope > 0 and adx >= MIN_ADX_4H:
        return "LONG", "4H ana trend yukarı", info

    if close < ema200 and ema20 < ema50 and slope < 0 and adx >= MIN_ADX_4H:
        return "SHORT", "4H ana trend aşağı", info

    if close > ema200 and ema20 > ema50:
        return "LONG_WEAK", "4H yukarı eğilim ama güç orta", info

    if close < ema200 and ema20 < ema50:
        return "SHORT_WEAK", "4H aşağı eğilim ama güç orta", info

    return "NEUTRAL", "4H kararsız", info


def get_1h_confirm(df1h):
    df = add_indicators(df1h)

    if df is None or len(df) < 20:
        return "NEUTRAL", "1H veri yetersiz", {}

    row = df.iloc[-2]

    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    ema200 = float(row["ema200"])
    macd_value = float(row["macd"])
    macd_signal = float(row["macd_signal"])
    adx = float(row["adx"])
    rsi = float(row["rsi"])

    info = {
        "adx_1h": round(adx, 2),
        "rsi_1h": round(rsi, 2),
        "close_1h": round(close, 8),
    }

    if close > ema200 and ema20 > ema50 and macd_value >= macd_signal and rsi >= 46 and adx >= MIN_ADX_1H:
        return "LONG", "1H alım onayı", info

    if close < ema200 and ema20 < ema50 and macd_value <= macd_signal and rsi <= 54 and adx >= MIN_ADX_1H:
        return "SHORT", "1H satış onayı", info

    if close > ema20 and ema20 >= ema50 and macd_value >= macd_signal:
        return "LONG_WEAK", "1H hafif alım eğilimi", info

    if close < ema20 and ema20 <= ema50 and macd_value <= macd_signal:
        return "SHORT_WEAK", "1H hafif satış eğilimi", info

    return "NEUTRAL", "1H kararsız", info


def trend_supports_direction(direction, trend, confirm, strict=True):
    if direction == "LONG":
        if strict:
            return trend == "LONG" and confirm == "LONG"

        return trend in ["LONG", "LONG_WEAK"] and confirm in ["LONG", "LONG_WEAK", "NEUTRAL"]

    if direction == "SHORT":
        if strict:
            return trend == "SHORT" and confirm == "SHORT"

        return trend in ["SHORT", "SHORT_WEAK"] and confirm in ["SHORT", "SHORT_WEAK", "NEUTRAL"]

    return False


def leverage_suggestion(risk_percent):
    risk_percent = float(risk_percent)

    if risk_percent <= LEVERAGE_RISK_3X_MAX:
        return "3x"
    if risk_percent <= LEVERAGE_RISK_2X_MAX:
        return "2x"
    if risk_percent <= LEVERAGE_RISK_1X2X_MAX:
        return "1x-2x"

    return "1x veya pas geç"


def is_weak_trade_block(direction, volume_ratio, adx_15m, adx_4h, adx_1h):
    """Çok zayıf sinyalleri işlem sinyali olmadan eler.

    Bu bölüm özellikle WLFI tipi zayıf shortları engellemek için eklendi.
    Kör filtre değildir; düşük hacimli ama güçlü ADX'li INJ/RAY/ZIL tarzı sinyalleri silmez.
    """
    volume_ratio = safe_float(volume_ratio)
    adx_15m = safe_float(adx_15m)
    adx_4h = safe_float(adx_4h)
    adx_1h = safe_float(adx_1h)

    if volume_ratio < 0.65 and adx_4h < 12:
        return True, "hacim çok düşük + 4H ADX çok zayıf"

    if direction == "SHORT" and volume_ratio < 0.75 and adx_15m < 18 and adx_4h < 12:
        return True, "zayıf short: düşük hacim + zayıf 15M/4H ADX"

    return False, ""


def smart_score_adjustment(direction, score, volume_ratio, rsi, adx_15m, adx_4h, adx_1h, risk_percent):
    """Canlı sonuçlardan öğrenilen riskleri skora yumuşak şekilde yansıtır."""
    score = int(score)
    notes = []

    volume_ratio = safe_float(volume_ratio)
    rsi = safe_float(rsi)
    adx_15m = safe_float(adx_15m)
    adx_4h = safe_float(adx_4h)
    adx_1h = safe_float(adx_1h)
    risk_percent = safe_float(risk_percent)

    # PENDLE / ikinci ENA tarzı: RSI yüksek + stop genişse A+ olmasın.
    if direction == "LONG":
        if rsi >= 67 and risk_percent >= 1.50:
            score -= 12
            notes.append("RSI yüksek + stop geniş; TP1 odaklı dikkat")
        elif rsi >= 65:
            score -= 5
            notes.append("RSI yüksek; devam gücü kontrol edilmeli")

    # SHORT tarafında çok düşük RSI bazen devam edebilir; sadece zayıf HTF varsa uyar.
    if direction == "SHORT":
        if rsi <= 33 and risk_percent >= 1.60 and adx_4h < 25:
            score -= 6
            notes.append("SHORT RSI düşük + stop geniş; geç giriş riski")

        # GALA / BIGTIME tipi: hacim düşük, HTF tam güçlü değilse A+ olmasın.
        if volume_ratio < 0.80 and adx_4h < 22 and adx_1h < 22:
            score -= 10
            notes.append("SHORT hacim düşük + HTF güç sınırlı")

    # Genel zayıflık: hacim düşük ve yüksek zaman dilimi çok güçlü değilse kalite düşer.
    if volume_ratio < 0.80 and adx_4h < 18 and adx_1h < 18:
        score -= 7
        notes.append("hacim düşük + 1H/4H destek sınırlı")

    # AZTEC tipi yakın TP1 dönüş riskine karşı: 15M çok güçlü değil ve hacim düşükse dikkat.
    if volume_ratio < 0.90 and adx_15m < 20:
        score -= 6
        notes.append("15M devam gücü zayıf; TP1 öncesi dönüş riski")

    # UP / ADA / LINK / ENA ilk sinyal tipi: sağlıklı RSI + güçlü ADX + makul stop.
    healthy_long_rsi = direction == "LONG" and 52 <= rsi <= 63
    healthy_short_rsi = direction == "SHORT" and 37 <= rsi <= 54
    strong_structure = volume_ratio >= 1.25 and adx_15m >= 25 and adx_1h >= 20 and adx_4h >= 18

    if strong_structure and risk_percent <= 1.50 and (healthy_long_rsi or healthy_short_rsi):
        score += 5
        notes.append("sağlıklı RSI + güçlü ADX + makul stop")

    # Dar stop + güçlü momentum avantajdır ama tek başına yeterli değildir.
    if risk_percent <= 0.85 and adx_15m >= 25 and adx_1h >= 20:
        score += 3
        notes.append("dar stop + güçlü momentum")

    score = max(0, min(100, int(score)))
    return score, notes


def smart_quality_label(signal_class, direction, score, volume_ratio, rsi, adx_15m, adx_4h, adx_1h, risk_percent):
    if signal_class != "TRADE":
        return "RADAR", "İşlem değil, sadece takip radarı."

    volume_ratio = safe_float(volume_ratio)
    rsi = safe_float(rsi)
    adx_15m = safe_float(adx_15m)
    adx_4h = safe_float(adx_4h)
    adx_1h = safe_float(adx_1h)
    risk_percent = safe_float(risk_percent)
    score = int(score)

    healthy_long_rsi = direction == "LONG" and 52 <= rsi <= 63
    healthy_short_rsi = direction == "SHORT" and 37 <= rsi <= 54
    strong_adx = adx_15m >= 25 and adx_1h >= 20 and adx_4h >= 18
    good_volume = volume_ratio >= 1.25
    good_risk = risk_percent <= 1.50

    caution_count = 0
    cautions = []

    if volume_ratio < 0.80:
        caution_count += 1
        cautions.append("hacim düşük")

    if adx_4h < 18:
        caution_count += 1
        cautions.append("4H ADX sınırda")

    if adx_1h < 18:
        caution_count += 1
        cautions.append("1H ADX sınırda")

    if direction == "LONG" and rsi >= 65:
        caution_count += 1
        cautions.append("LONG RSI yüksek")

    if direction == "SHORT" and rsi <= 34:
        caution_count += 1
        cautions.append("SHORT RSI düşük")

    if risk_percent >= 1.60:
        caution_count += 1
        cautions.append("stop geniş")

    if score >= 92 and good_volume and strong_adx and good_risk and (healthy_long_rsi or healthy_short_rsi):
        return "A+ ANA", "Ana aday profili: güçlü ADX, yeterli hacim, sağlıklı RSI ve makul stop."

    if score >= 88 and caution_count <= 1:
        note = "Güçlü sinyal ama tam A+ değil."
        if cautions:
            note += " Dikkat: " + ", ".join(cautions) + "."
        return "A DİKKATLİ", note

    if score >= 72:
        note = "TP1 odaklı dikkatli sinyal."
        if cautions:
            note += " Dikkat: " + ", ".join(cautions) + "."
        return "A- TP1", note

    return "TAKİP", "Skor/kalite zayıf; işlem yerine takip daha mantıklı."


def make_targets(direction, entry, atr, df15):
    recent = df15.iloc[-14:-2]

    if direction == "LONG":
        swing_low = float(recent["low"].min())
        sl = min(swing_low - atr * 0.10, entry - atr * 1.10)
        risk = entry - sl

        if risk <= 0:
            return None

        tp1 = entry + risk * TP1_R_MULTIPLIER
        tp2 = entry + risk * TP2_R_MULTIPLIER
        tp3 = entry + risk * TP3_R_MULTIPLIER

    else:
        swing_high = float(recent["high"].max())
        sl = max(swing_high + atr * 0.10, entry + atr * 1.10)
        risk = sl - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * TP1_R_MULTIPLIER
        tp2 = entry - risk * TP2_R_MULTIPLIER
        tp3 = entry - risk * TP3_R_MULTIPLIER

        if tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
            return None

    risk_percent = (risk / entry) * 100

    if risk_percent < MIN_RISK_PERCENT or risk_percent > MAX_RISK_PERCENT:
        return None

    return {
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_percent": risk_percent,
        "rr_tp1": abs(tp1 - entry) / risk,
        "rr_tp2": abs(tp2 - entry) / risk,
        "rr_tp3": abs(tp3 - entry) / risk,
    }


def build_signal_message(signal):
    direction = signal["direction"]
    icon = "🟢" if direction == "LONG" else "🔴"
    quality = signal.get("quality", "")

    if signal["signal_class"] == "TRADE":
        if str(quality).startswith("A+"):
            title = "A+ ANA MTF FUTURES SİNYALİ"
        elif str(quality).startswith("A-"):
            title = "A- TP1 ODAKLI MTF FUTURES SİNYALİ"
        elif str(quality).startswith("TAKİP"):
            title = "MTF TAKİP SİNYALİ - DİKKATLİ OL"
        else:
            title = "A KALİTE DİKKATLİ MTF FUTURES SİNYALİ"
    else:
        title = "5M / 15M RADAR - İŞLEM AÇMA"

    return f"""
🚀 {title}

{icon} {direction}
🟡 Coin: {signal["symbol"]}
⏱️ Kaynak: {signal["source"]}

📌 Giriş: {format_price(signal["entry"])}
🎯 TP1: {format_price(signal["tp1"])}
🎯 TP2: {format_price(signal["tp2"])}
🎯 TP3: {format_price(signal["tp3"])}
🛑 SL: {format_price(signal["sl"])}

📊 Skor: %{signal["score"]} ({signal["quality"]})
🧠 Kalite Notu: {signal.get("quality_note", "-")}
📈 R/R TP1: {round(signal["rr_tp1"], 2)}
📈 R/R TP2: {round(signal["rr_tp2"], 2)}
🛡️ Stop Mesafesi: %{round(signal["risk_percent"], 2)}
⚙️ Kaldıraç Önerisi: {signal["leverage"]}

🧭 Çoklu Zaman Dilimi:
• 4H: {signal["trend_reason"]}
• 1H: {signal["confirm_reason"]}
• 15M: {signal["entry_reason"]}
• 5M: {signal["radar_reason"]}

📊 Göstergeler:
• Hacim: {signal["volume_ratio"]}x
• RSI 15M: {signal["rsi_15m"]}
• ADX 15M: {signal["adx_15m"]}
• 4H ADX: {signal["adx_4h"]}
• 1H ADX: {signal["adx_1h"]}

📌 İşlem Kuralı:
• Girişten uzaklaştıysa girme.
• TP1'e yaklaşmışsa girme.
• TP1 gelirse %50 kâr al, SL'yi girişe çek.
• Stop mutlaka girilmeli.
• Marjin: Isolated.
• Kaldıraç düşük tutulmalı.

⚠️ Finansal tavsiye değildir. Grafikte kontrol etmeden işlem açma.
"""


def analyze_mtf_trade(symbol, df15m, df1h, df4h, current_price=None):
    trend, trend_reason, trend_info = get_4h_trend(df4h)
    confirm, confirm_reason, confirm_info = get_1h_confirm(df1h)

    df15 = add_indicators(df15m)

    if df15 is None or len(df15) < 50:
        return None

    last = df15.iloc[-2]
    prev = df15.iloc[-3]
    recent = df15.iloc[-8:-2]

    entry = float(current_price) if current_price is not None and current_price > 0 else float(last["close"])
    atr = float(last["atr"])
    rsi = float(last["rsi"])
    adx = float(last["adx"])
    volume_ratio = float(last["volume_ratio"])

    close = float(last["close"])
    open_ = float(last["open"])
    ema20 = float(last["ema20"])
    ema50 = float(last["ema50"])
    macd_value = float(last["macd"])
    macd_signal = float(last["macd_signal"])
    macd_hist = float(last["macd_hist"])
    prev_macd_hist = float(prev["macd_hist"])

    if entry <= 0 or atr <= 0:
        return None

    direction = None
    entry_reason = ""

    touched_ema_long = (
        float(recent["low"].min()) <= float(recent["ema20"].iloc[-1]) * 1.006
        or float(recent["low"].min()) <= float(recent["ema50"].iloc[-1]) * 1.006
    )

    touched_ema_short = (
        float(recent["high"].max()) >= float(recent["ema20"].iloc[-1]) * 0.994
        or float(recent["high"].max()) >= float(recent["ema50"].iloc[-1]) * 0.994
    )

    bullish_reclaim = close > open_ and close >= ema20 and close > float(prev["close"])
    bearish_reject = close < open_ and close <= ema20 and close < float(prev["close"])

    macd_long_ok = macd_value >= macd_signal or macd_hist > prev_macd_hist
    macd_short_ok = macd_value <= macd_signal or macd_hist < prev_macd_hist

    if (
        trend_supports_direction("LONG", trend, confirm, strict=False)
        and touched_ema_long
        and bullish_reclaim
        and macd_long_ok
        and LONG_RSI_MIN <= rsi <= LONG_RSI_MAX
    ):
        direction = "LONG"
        entry_reason = "15M EMA pullback sonrası yeşil dönüş"

    if (
        trend_supports_direction("SHORT", trend, confirm, strict=False)
        and touched_ema_short
        and bearish_reject
        and macd_short_ok
        and SHORT_RSI_MIN <= rsi <= SHORT_RSI_MAX
    ):
        direction = "SHORT"
        entry_reason = "15M EMA pullback sonrası kırmızı dönüş"

    if direction is None:
        return None

    targets = make_targets(direction, entry, atr, df15)

    if targets is None:
        return None

    adx_4h = safe_float(trend_info.get("adx_4h", 0))
    adx_1h = safe_float(confirm_info.get("adx_1h", 0))

    weak_block, weak_reason = is_weak_trade_block(
        direction,
        volume_ratio,
        adx,
        adx_4h,
        adx_1h,
    )
    if weak_block:
        print(symbol, "akıllı kalite elendi ->", weak_reason)
        return None

    score = 40

    if trend_supports_direction(direction, trend, confirm, strict=True):
        score += 24
    elif trend_supports_direction(direction, trend, confirm, strict=False):
        score += 14

    if volume_ratio >= 1.30:
        score += 14
    elif volume_ratio >= MIN_VOLUME_RATIO_15M:
        score += 8

    if adx >= 25:
        score += 12
    elif adx >= 18:
        score += 8
    elif adx >= 12:
        score += 4

    if direction == "LONG":
        if 46 <= rsi <= 62:
            score += 10
        else:
            score += 5
    else:
        if 38 <= rsi <= 54:
            score += 10
        else:
            score += 5

    if targets["rr_tp2"] >= 1.30:
        score += 8
    elif targets["rr_tp2"] >= 1.10:
        score += 5

    score, score_notes = smart_score_adjustment(
        direction,
        score,
        volume_ratio,
        rsi,
        adx,
        adx_4h,
        adx_1h,
        targets["risk_percent"],
    )

    strict_trade_ok = (
        (
            trend_supports_direction(direction, trend, confirm, strict=True)
            or (
                trend_supports_direction(direction, trend, confirm, strict=False)
                and score >= MIN_SCORE_TRADE + 6
                and volume_ratio >= 1.00
            )
        )
        and volume_ratio >= MIN_VOLUME_RATIO_15M
        and score >= MIN_SCORE_TRADE
    )

    signal_class = "TRADE" if strict_trade_ok else "RADAR"

    # Radar kapalıysa sadece RADAR sınıfını kapatır.
    # TRADE sinyaller MIN_SCORE_RADAR=999 yüzünden yanlışlıkla elenmez.
    if signal_class == "RADAR" and score < MIN_SCORE_RADAR:
        return None

    if signal_class == "TRADE" and score < MIN_SCORE_TRADE:
        return None

    quality, quality_note = smart_quality_label(
        signal_class,
        direction,
        score,
        volume_ratio,
        rsi,
        adx,
        adx_4h,
        adx_1h,
        targets["risk_percent"],
    )

    if score_notes:
        quality_note = quality_note + " | " + " / ".join(score_notes[:3])

    signal = {
        "symbol": symbol,
        "direction": direction,
        "source": "15M_ENTRY",
        "signal_class": signal_class,
        "entry": round(entry, 10),
        "tp1": round(targets["tp1"], 10),
        "tp2": round(targets["tp2"], 10),
        "tp3": round(targets["tp3"], 10),
        "sl": round(targets["sl"], 10),
        "risk_percent": round(targets["risk_percent"], 3),
        "rr_tp1": round(targets["rr_tp1"], 3),
        "rr_tp2": round(targets["rr_tp2"], 3),
        "rr_tp3": round(targets["rr_tp3"], 3),
        "score": int(score),
        "trend_reason": trend_reason,
        "confirm_reason": confirm_reason,
        "entry_reason": entry_reason,
        "radar_reason": "5M radar gerekli değil, 15M giriş oluştu",
        "rsi_15m": round(rsi, 2),
        "adx_15m": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2),
        "adx_4h": trend_info.get("adx_4h", "-"),
        "adx_1h": confirm_info.get("adx_1h", "-"),
        "quality": quality,
        "quality_note": quality_note,
    }

    signal["leverage"] = leverage_suggestion(signal["risk_percent"])
    signal["message"] = build_signal_message(signal)

    return signal


def analyze_5m_radar(symbol, df5m, df15m, df1h, df4h, current_price=None):
    if df5m is None or df15m is None or len(df5m) < 50:
        return None

    trend, trend_reason, trend_info = get_4h_trend(df4h)
    confirm, confirm_reason, confirm_info = get_1h_confirm(df1h)
    df15 = add_indicators(df15m)

    if df15 is None or len(df15) < 50:
        return None

    last5 = df5m.iloc[-2]
    entry = float(current_price) if current_price is not None and current_price > 0 else float(last5["close"])

    move5 = ((float(last5["close"]) - float(last5["open"])) / float(last5["open"])) * 100
    vol5_avg = float(df5m["volume"].iloc[-22:-2].mean())
    vol5_ratio = float(last5["volume"]) / vol5_avg if vol5_avg > 0 else 0

    if abs(move5) < RADAR_MIN_5M_MOVE_PERCENT:
        return None

    if abs(move5) > RADAR_MAX_5M_MOVE_PERCENT:
        return None

    if vol5_ratio < RADAR_MIN_VOLUME_RATIO:
        return None

    last15 = df15.iloc[-2]
    atr = float(last15["atr"])
    rsi = float(last15["rsi"])
    adx = float(last15["adx"])
    volume_ratio = max(float(last15["volume_ratio"]), vol5_ratio)

    if move5 > 0:
        direction = "LONG"

        if rsi > LONG_RSI_MAX:
            return None

        if trend == "SHORT" or confirm == "SHORT":
            return None

    else:
        direction = "SHORT"

        if rsi < SHORT_RSI_MIN:
            return None

        if trend == "LONG" or confirm == "LONG":
            return None

    targets = make_targets(direction, entry, atr, df15)

    if targets is None:
        return None

    adx_4h = safe_float(trend_info.get("adx_4h", 0))
    adx_1h = safe_float(confirm_info.get("adx_1h", 0))

    score = 42

    if trend_supports_direction(direction, trend, confirm, strict=True):
        score += 22
    elif trend_supports_direction(direction, trend, confirm, strict=False):
        score += 10

    if vol5_ratio >= 2.0:
        score += 16
    elif vol5_ratio >= 1.50:
        score += 12
    else:
        score += 8

    if adx >= 25:
        score += 10
    elif adx >= 18:
        score += 7
    elif adx >= 12:
        score += 4

    if abs(move5) <= 0.85:
        score += 8
    else:
        score += 4

    score, score_notes = smart_score_adjustment(
        direction,
        score,
        volume_ratio,
        rsi,
        adx,
        adx_4h,
        adx_1h,
        targets["risk_percent"],
    )

    can_be_trade = (
        score >= RADAR_TRADE_MIN_SCORE
        and vol5_ratio >= RADAR_TRADE_MIN_VOLUME_RATIO
        and trend_supports_direction(direction, trend, confirm, strict=True)
    )

    signal_class = "TRADE" if can_be_trade else "RADAR"

    # Radar tarafı kapalıysa sadece RADAR olanları kapatır.
    # İleride 5M radar trade açılırsa TRADE sinyalini yanlışlıkla engellemez.
    if signal_class == "RADAR" and score < MIN_SCORE_RADAR:
        return None

    if signal_class == "TRADE" and score < RADAR_TRADE_MIN_SCORE:
        return None

    quality, quality_note = smart_quality_label(
        signal_class,
        direction,
        score,
        volume_ratio,
        rsi,
        adx,
        adx_4h,
        adx_1h,
        targets["risk_percent"],
    )

    if score_notes:
        quality_note = quality_note + " | " + " / ".join(score_notes[:3])

    signal = {
        "symbol": symbol,
        "direction": direction,
        "source": "5M_RADAR",
        "signal_class": signal_class,
        "entry": round(entry, 10),
        "tp1": round(targets["tp1"], 10),
        "tp2": round(targets["tp2"], 10),
        "tp3": round(targets["tp3"], 10),
        "sl": round(targets["sl"], 10),
        "risk_percent": round(targets["risk_percent"], 3),
        "rr_tp1": round(targets["rr_tp1"], 3),
        "rr_tp2": round(targets["rr_tp2"], 3),
        "rr_tp3": round(targets["rr_tp3"], 3),
        "score": int(score),
        "trend_reason": trend_reason,
        "confirm_reason": confirm_reason,
        "entry_reason": "15M henüz tam giriş değil, 5M erken hareket var",
        "radar_reason": f"5M hareket %{round(move5, 2)} | hacim {round(vol5_ratio, 2)}x",
        "rsi_15m": round(rsi, 2),
        "adx_15m": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2),
        "adx_4h": trend_info.get("adx_4h", "-"),
        "adx_1h": confirm_info.get("adx_1h", "-"),
        "quality": quality,
        "quality_note": quality_note,
    }

    signal["leverage"] = leverage_suggestion(signal["risk_percent"])
    signal["message"] = build_signal_message(signal)

    return signal
