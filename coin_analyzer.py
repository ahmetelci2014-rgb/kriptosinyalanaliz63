# coin_analyzer.py
# Tek coin detay analiz programı - FIX sürüm
# Kullanım:
#   python coin_analyzer.py BTCUSDT
# veya GitHub Actions Run workflow ekranında SYMBOL alanına BTCUSDT yaz.
#
# Emir açmaz. Sadece analiz raporu üretir ve TOKEN/CHAT_ID varsa Telegram'a gönderir.

import os
import sys
import requests
import pandas as pd
import ccxt

from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD, ADXIndicator
from ta.volatility import AverageTrueRange


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = os.getenv("SYMBOL") or (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT")

TIMEFRAMES = {
    "5M": ("5m", 220),
    "15M": ("15m", 320),
    "1H": ("1h", 320),
    "4H": ("4h", 320),
}


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN / CHAT_ID yok. Telegram gönderilmedi.")
        return False

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message},
            timeout=20
        )
        print("Telegram cevap:", r.status_code, r.text)
        return r.status_code == 200
    except Exception as e:
        print("Telegram hatası:", e)
        return False


def normalize_symbol(symbol):
    symbol = str(symbol).upper().replace("/", "").replace("-", "").replace("_", "").strip()

    if not symbol.endswith("USDT"):
        symbol = symbol + "USDT"

    return symbol


def to_okx_symbol(symbol):
    base = symbol.replace("USDT", "")
    return f"{base}/USDT:USDT"


def get_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"}
    })


def resolve_okx_swap_symbol(exchange, symbol):
    """
    Kullanıcı BTCUSDT yazar.
    OKX/ccxt tarafında bu BTC/USDT:USDT olarak aranır.
    Coin OKX USDT swap tarafında yoksa düzgün hata mesajı verir.
    """
    symbol = normalize_symbol(symbol)
    wanted = to_okx_symbol(symbol)

    markets = exchange.load_markets()

    if wanted in markets:
        market = markets[wanted]
        if market.get("swap") and market.get("quote") == "USDT":
            return wanted

    base = symbol.replace("USDT", "")
    candidates = []

    for market_symbol, market in markets.items():
        try:
            if not market.get("active", True):
                continue
            if not market.get("swap", False):
                continue
            if market.get("quote") != "USDT":
                continue
            if market.get("settle") != "USDT":
                continue
            if str(market.get("base", "")).upper() == base:
                candidates.append(market_symbol)
        except Exception:
            continue

    if candidates:
        return candidates[0]

    raise RuntimeError(
        f"{symbol} OKX USDT futures/swap tarafında bulunamadı. "
        f"Bu program sadece OKX USDT perpetual/futures coinlerini analiz eder. "
        f"Coin spotta olabilir ama futures tarafında olmayabilir."
    )


def fetch_df(exchange, market_symbol, timeframe, limit):
    ohlcv = exchange.fetch_ohlcv(
        market_symbol,
        timeframe=timeframe,
        limit=limit
    )

    if not ohlcv or len(ohlcv) < 60:
        return None

    return pd.DataFrame(
        ohlcv,
        columns=["time", "open", "high", "low", "close", "volume"]
    )


def add_indicators(df):
    if df is None or df.empty or len(df) < 60:
        return None

    df = df.copy()

    df["rsi"] = RSIIndicator(df["close"], window=14).rsi()

    df["ema20"] = EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = EMAIndicator(df["close"], window=50).ema_indicator()
    df["ema100"] = EMAIndicator(df["close"], window=100).ema_indicator()
    df["ema200"] = EMAIndicator(df["close"], window=200).ema_indicator()

    macd = MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = df["macd"] - df["macd_signal"]

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

    if len(df) < 30:
        return None

    return df


def fmt(value):
    try:
        value = float(value)
    except Exception:
        return "-"

    if value >= 100:
        return f"{value:.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.10f}"


def pct(a, b):
    if b == 0:
        return 0
    return ((a - b) / b) * 100


def abs_pct(a, b):
    return abs(pct(a, b))


def nearest_support_resistance(df, price, lookback=80):
    if df is None or len(df) < 5:
        return {
            "support1": price,
            "support2": price,
            "resistance1": price,
            "resistance2": price,
            "support_distance": 0,
            "resistance_distance": 0,
        }

    usable_lookback = min(lookback, max(5, len(df) - 2))
    recent = df.iloc[-usable_lookback - 1:-1].copy()

    if recent.empty:
        recent = df.copy()

    lows = sorted([float(x) for x in recent["low"] if float(x) < price], reverse=True)
    highs = sorted([float(x) for x in recent["high"] if float(x) > price])

    support1 = lows[0] if len(lows) >= 1 else float(recent["low"].min())
    support2 = lows[1] if len(lows) >= 2 else support1

    resistance1 = highs[0] if len(highs) >= 1 else float(recent["high"].max())
    resistance2 = highs[1] if len(highs) >= 2 else resistance1

    return {
        "support1": support1,
        "support2": support2,
        "resistance1": resistance1,
        "resistance2": resistance2,
        "support_distance": abs_pct(price, support1),
        "resistance_distance": abs_pct(resistance1, price),
    }


def trend_status(df, label):
    if df is None or len(df) < 2:
        return {
            "direction": "NEUTRAL",
            "text": f"{label}: veri yetersiz",
            "close": 0,
            "rsi": "-",
            "adx": "-",
            "volume_ratio": "-",
            "ema20": 0,
            "ema50": 0,
            "ema200": 0,
            "macd_ok": False,
        }

    row = df.iloc[-2]

    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    ema200 = float(row["ema200"])
    slope = float(row["ema20_slope"])
    rsi = float(row["rsi"])
    adx = float(row["adx"])
    macd = float(row["macd"])
    macd_signal = float(row["macd_signal"])
    volume_ratio = float(row["volume_ratio"])

    if close > ema200 and ema20 > ema50 and slope > 0 and macd >= macd_signal:
        direction = "LONG"
        text = f"{label}: Yukarı eğilim"
    elif close < ema200 and ema20 < ema50 and slope < 0 and macd <= macd_signal:
        direction = "SHORT"
        text = f"{label}: Aşağı eğilim"
    else:
        direction = "NEUTRAL"
        text = f"{label}: Kararsız"

    return {
        "direction": direction,
        "text": text,
        "close": close,
        "rsi": round(rsi, 2),
        "adx": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2),
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
        "macd_ok": macd >= macd_signal,
    }


def candle_signal_15m(df):
    if df is None or len(df) < 3:
        return "NEUTRAL", "15M veri yetersiz"

    last = df.iloc[-2]
    prev = df.iloc[-3]

    close = float(last["close"])
    open_ = float(last["open"])
    ema20 = float(last["ema20"])
    rsi = float(last["rsi"])
    macd_hist = float(last["macd_hist"])
    prev_macd_hist = float(prev["macd_hist"])

    green = close > open_
    red = close < open_

    long_reclaim = green and close >= ema20 and close > float(prev["close"]) and macd_hist >= prev_macd_hist
    short_reject = red and close <= ema20 and close < float(prev["close"]) and macd_hist <= prev_macd_hist

    if long_reclaim and 40 <= rsi <= 70:
        return "LONG", "15M yeşil dönüş / EMA20 üstü"
    if short_reject and 30 <= rsi <= 60:
        return "SHORT", "15M kırmızı dönüş / EMA20 altı"

    return "NEUTRAL", "15M net giriş dönüşü yok"


def radar_5m(df):
    if df is None or len(df) < 25:
        return "NEUTRAL", "5M veri yetersiz"

    last = df.iloc[-2]

    move = pct(float(last["close"]), float(last["open"]))
    vol_avg = float(df["volume"].iloc[-22:-2].mean())
    vol_ratio = float(last["volume"]) / vol_avg if vol_avg > 0 else 0

    if move >= 0.30 and vol_ratio >= 1.15:
        return "LONG", f"5M yukarı hareket %{round(move, 2)} / hacim {round(vol_ratio, 2)}x"

    if move <= -0.30 and vol_ratio >= 1.15:
        return "SHORT", f"5M aşağı hareket %{round(move, 2)} / hacim {round(vol_ratio, 2)}x"

    return "NEUTRAL", f"5M sakin / hareket %{round(move, 2)} / hacim {round(vol_ratio, 2)}x"


def leverage_suggestion(risk_percent):
    if risk_percent <= 0.85:
        return "3x"
    if risk_percent <= 1.60:
        return "2x"
    if risk_percent <= 2.40:
        return "1x-2x"

    return "1x veya pas geç"


def build_trade_plan(direction, price, df15):
    if df15 is None or len(df15) < 20:
        return None

    row = df15.iloc[-2]
    atr = float(row["atr"])

    recent = df15.iloc[-14:-2]

    if recent.empty or atr <= 0:
        return None

    if direction == "LONG":
        swing_low = float(recent["low"].min())
        sl = min(swing_low - atr * 0.10, price - atr * 1.10)
        risk = price - sl

        if risk <= 0:
            return None

        tp1 = price + risk * 0.75
        tp2 = price + risk * 1.35
        tp3 = price + risk * 2.00

    elif direction == "SHORT":
        swing_high = float(recent["high"].max())
        sl = max(swing_high + atr * 0.10, price + atr * 1.10)
        risk = sl - price

        if risk <= 0:
            return None

        tp1 = price - risk * 0.75
        tp2 = price - risk * 1.35
        tp3 = price - risk * 2.00

        if tp1 <= 0 or tp2 <= 0 or tp3 <= 0:
            return None

    else:
        return None

    risk_percent = (risk / price) * 100

    rr1 = abs(tp1 - price) / risk
    rr2 = abs(tp2 - price) / risk
    rr3 = abs(tp3 - price) / risk

    return {
        "entry": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_percent": risk_percent,
        "rr1": rr1,
        "rr2": rr2,
        "rr3": rr3,
        "leverage": leverage_suggestion(risk_percent),
    }


def score_direction(direction, s4h, s1h, entry15, radar5, df15):
    score = 0
    reasons = []

    if df15 is None or len(df15) < 2:
        return score, ["veri yetersiz"]

    row = df15.iloc[-2]
    rsi = float(row["rsi"])
    adx = float(row["adx"])
    volume_ratio = float(row["volume_ratio"])

    if direction == s4h["direction"]:
        score += 25
        reasons.append("4H aynı yön")
    elif s4h["direction"] != "NEUTRAL":
        score -= 15
        reasons.append("4H ters")

    if direction == s1h["direction"]:
        score += 25
        reasons.append("1H aynı yön")
    elif s1h["direction"] != "NEUTRAL":
        score -= 15
        reasons.append("1H ters")

    if direction == entry15[0]:
        score += 20
        reasons.append("15M giriş onayı")

    if direction == radar5[0]:
        score += 10
        reasons.append("5M radar destekli")

    if volume_ratio >= 1.30:
        score += 12
        reasons.append("hacim güçlü")
    elif volume_ratio >= 0.75:
        score += 7
        reasons.append("hacim yeterli")

    if adx >= 25:
        score += 8
        reasons.append("ADX güçlü")
    elif adx >= 15:
        score += 4
        reasons.append("ADX orta")

    if direction == "LONG":
        if 42 <= rsi <= 68:
            score += 8
            reasons.append("RSI LONG için uygun")
        elif rsi > 72:
            score -= 10
            reasons.append("RSI şişmiş")
    else:
        if 32 <= rsi <= 58:
            score += 8
            reasons.append("RSI SHORT için uygun")
        elif rsi < 28:
            score -= 10
            reasons.append("RSI çok dip")

    return score, reasons


def final_verdict(long_score, short_score):
    if long_score >= 70 and long_score >= short_score + 10:
        return "LONG", "LONG tarafı daha güçlü"
    if short_score >= 70 and short_score >= long_score + 10:
        return "SHORT", "SHORT tarafı daha güçlü"
    if max(long_score, short_score) >= 55:
        return "WAIT", "Takip et, tam işlem onayı zayıf"

    return "WAIT", "Net işlem şartı yok"


def plan_text(title, plan):
    if not plan:
        return f"\n{title}\nPlan üretilemedi."

    return f"""
{title}
Giriş: {fmt(plan["entry"])}
TP1: {fmt(plan["tp1"])}
TP2: {fmt(plan["tp2"])}
TP3: {fmt(plan["tp3"])}
SL: {fmt(plan["sl"])}
Risk: %{round(plan["risk_percent"], 2)}
R/R TP1: {round(plan["rr1"], 2)}
R/R TP2: {round(plan["rr2"], 2)}
Kaldıraç Önerisi: {plan["leverage"]}
"""


def analyze_coin(symbol):
    symbol = normalize_symbol(symbol)
    exchange = get_exchange()
    market_symbol = resolve_okx_swap_symbol(exchange, symbol)

    dfs = {}

    for label, (tf, limit) in TIMEFRAMES.items():
        raw_df = fetch_df(exchange, market_symbol, tf, limit)

        if raw_df is None:
            raise RuntimeError(f"{symbol} için {label} verisi alınamadı veya veri yetersiz.")

        df = add_indicators(raw_df)

        if df is None:
            raise RuntimeError(f"{symbol} için {label} indikatör verisi yetersiz.")

        dfs[label] = df

    ticker = exchange.fetch_ticker(market_symbol)
    price = ticker.get("last")

    if price is None:
        raise RuntimeError(f"{symbol} güncel fiyat alınamadı.")

    price = float(price)

    df5 = dfs["5M"]
    df15 = dfs["15M"]
    df1h = dfs["1H"]
    df4h = dfs["4H"]

    s4h = trend_status(df4h, "4H")
    s1h = trend_status(df1h, "1H")
    s15 = trend_status(df15, "15M")
    s5 = trend_status(df5, "5M")

    entry15 = candle_signal_15m(df15)
    radar5 = radar_5m(df5)

    sr15 = nearest_support_resistance(df15, price, lookback=80)
    sr1h = nearest_support_resistance(df1h, price, lookback=80)
    sr4h = nearest_support_resistance(df4h, price, lookback=80)

    long_score, long_reasons = score_direction("LONG", s4h, s1h, entry15, radar5, df15)
    short_score, short_reasons = score_direction("SHORT", s4h, s1h, entry15, radar5, df15)

    verdict, verdict_reason = final_verdict(long_score, short_score)

    long_plan = build_trade_plan("LONG", price, df15)
    short_plan = build_trade_plan("SHORT", price, df15)

    row15 = df15.iloc[-2]

    report = f"""
📊 TEK COIN DETAY ANALİZİ

Coin: {symbol}
OKX Market: {market_symbol}
Güncel Fiyat: {fmt(price)}

🧭 Çoklu Zaman Dilimi:
• {s4h["text"]} | RSI: {s4h["rsi"]} | ADX: {s4h["adx"]}
• {s1h["text"]} | RSI: {s1h["rsi"]} | ADX: {s1h["adx"]}
• {s15["text"]} | RSI: {s15["rsi"]} | ADX: {s15["adx"]}
• {s5["text"]} | RSI: {s5["rsi"]} | ADX: {s5["adx"]}

📌 Giriş / Radar:
• 15M: {entry15[1]}
• 5M: {radar5[1]}

📊 Hacim:
• 15M Hacim Oranı: {round(float(row15["volume_ratio"]), 2)}x

🟢 Destekler:
• 15M Destek 1: {fmt(sr15["support1"])} | Uzaklık: %{round(sr15["support_distance"], 2)}
• 15M Destek 2: {fmt(sr15["support2"])}
• 1H Destek: {fmt(sr1h["support1"])}
• 4H Destek: {fmt(sr4h["support1"])}

🔴 Dirençler:
• 15M Direnç 1: {fmt(sr15["resistance1"])} | Uzaklık: %{round(sr15["resistance_distance"], 2)}
• 15M Direnç 2: {fmt(sr15["resistance2"])}
• 1H Direnç: {fmt(sr1h["resistance1"])}
• 4H Direnç: {fmt(sr4h["resistance1"])}

🟢 LONG Skoru: %{long_score}
Nedenler: {", ".join(long_reasons) if long_reasons else "Yeterli neden yok"}

🔴 SHORT Skoru: %{short_score}
Nedenler: {", ".join(short_reasons) if short_reasons else "Yeterli neden yok"}

📌 Genel Karar:
{verdict} → {verdict_reason}
"""

    report += plan_text("🟢 LONG Senaryosu:", long_plan)
    report += plan_text("🔴 SHORT Senaryosu:", short_plan)

    report += """

📌 Not:
Bu rapor işlem emri değildir.
Grafikte kontrol etmeden işlem açma.
TP1 gelirse %50 kâr alıp SL'yi girişe çekmek daha güvenlidir.
"""

    return report.strip()


def main():
    symbol = normalize_symbol(SYMBOL)
    print("Analiz ediliyor:", symbol)

    try:
        report = analyze_coin(symbol)
        print(report)
        send_telegram(report)

    except Exception as e:
        message = f"❌ Coin analiz hatası\n\nCoin: {symbol}\nHata: {e}"
        print(message)
        send_telegram(message)
        raise


if __name__ == "__main__":
    main()
