# pump_radar.py
# Net Pump/Dump Radar v3.3 - TP Takip Fix
# Sadece OKX USDT Futures tarafında güçlü erken PUMP LONG ve erken DUMP SHORT işlem sinyali üretir.
# Spot uyarı, zayıf "takip" mesajı, "işlem açma" aday mesajı yoktur.
# Emir açmaz; Telegram sinyali gönderir ve pump_radar_state.json içinde TP/SL takibi yapar.
#
# DÜZELTME:
# Eski sürüm TP takibinde fetch_ohlcv(limit=8) çağırıyordu.
# Ama fetch_ohlcv fonksiyonu 60 mumdan az veriyi reddettiği için TP/SL takibi çalışmıyordu.
# Bu sürüm TP takibinde 90 mum çeker ve sinyal zamanından sonraki mumları kontrol eder.

import os
import json
import time
import math
import requests
import ccxt
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BOT_NAME = "Net Pump/Dump Radar v3.3 - TP Fix"
STATE_FILE = "pump_radar_state.json"
TR_TZ = timezone(timedelta(hours=3))

# Tarama
MAX_COINS_PER_RUN = 999
MIN_24H_QUOTE_VOLUME = 200_000
MAX_NEW_SIGNALS_PER_RUN = 2
MAX_OPEN_SIGNALS = 3

# Aynı coin tekrar blok
DUPLICATE_SECONDS = 180 * 60

# Pump/Dump sinyali 6 saat içinde TP1 görmezse takipten çıkar
SIGNAL_TTL_SECONDS = 6 * 60 * 60

# Skorlar
MIN_SCORE_BREAKOUT = 82
MIN_SCORE_EARLY = 78

# KIRILIM PUMP LONG şartları
MIN_5M_CHANGE_BREAKOUT = 0.60
MAX_5M_CHANGE_BREAKOUT = 2.20
MIN_15M_CHANGE_BREAKOUT = 0.80
MAX_15M_CHANGE_BREAKOUT = 4.50
MIN_5M_VOLUME_RATIO_BREAKOUT = 3.00
MIN_15M_VOLUME_RATIO_BREAKOUT = 1.80
MAX_RISK_BREAKOUT = 2.00
MAX_EMA20_DISTANCE_BREAKOUT = 1.40

# ERKEN PUMP LONG şartları
MIN_5M_CHANGE_EARLY = 0.20
MAX_5M_CHANGE_EARLY = 1.35
MIN_15M_CHANGE_EARLY = 0.15
MAX_15M_CHANGE_EARLY = 2.80
MIN_5M_VOLUME_RATIO_EARLY = 2.50
MIN_15M_VOLUME_RATIO_EARLY = 1.40
MAX_RISK_EARLY = 1.85
MAX_EMA20_DISTANCE_EARLY = 0.95
MAX_RESISTANCE_DISTANCE_EARLY = 1.35

# SHORT / DUMP için simetrik kontroller
# Amaç: düşüşün ortasında dipten short değil, erken satış baskısını yakalamak.
MIN_5M_DROP_BREAKDOWN = -2.20
MAX_5M_DROP_BREAKDOWN = -0.60
MIN_15M_DROP_BREAKDOWN = -4.50
MAX_15M_DROP_BREAKDOWN = -0.80
MIN_5M_DROP_EARLY = -1.35
MAX_5M_DROP_EARLY = -0.20
MIN_15M_DROP_EARLY = -2.80
MAX_15M_DROP_EARLY = -0.15
MAX_SUPPORT_DISTANCE_EARLY_SHORT = 1.35

# Hareketin ortasına gelmiş pump/dump elemesi
MIN_1H_CHANGE_LONG = 0.00
MAX_1H_CHANGE_BREAKOUT_LONG = 8.00
MAX_1H_CHANGE_EARLY_LONG = 5.00
MIN_2H_CHANGE_LONG = -0.50
MAX_2H_CHANGE_BREAKOUT_LONG = 12.00
MAX_2H_CHANGE_EARLY_LONG = 8.00

MIN_1H_CHANGE_BREAKDOWN_SHORT = -8.00
MAX_1H_CHANGE_SHORT = 0.00
MIN_1H_CHANGE_EARLY_SHORT = -5.00
MIN_2H_CHANGE_BREAKDOWN_SHORT = -12.00
MIN_2H_CHANGE_EARLY_SHORT = -8.00
MAX_2H_CHANGE_SHORT = 0.50

MAX_SAME_COLOR_CANDLES = 4

# RSI güvenliği
MAX_RSI_5M_BREAKOUT_LONG = 75.0
MAX_RSI_15M_BREAKOUT_LONG = 72.0
MAX_RSI_5M_EARLY_LONG = 72.0
MAX_RSI_15M_EARLY_LONG = 68.0

MIN_RSI_5M_BREAKDOWN_SHORT = 25.0
MIN_RSI_15M_BREAKDOWN_SHORT = 30.0
MIN_RSI_5M_EARLY_SHORT = 28.0
MIN_RSI_15M_EARLY_SHORT = 32.0

# Hedefler
TP1_R = 0.65
TP2_R = 1.15
TP3_R = 1.70
SL_BUFFER_PERCENT = 0.12


def now_ts():
    return int(time.time())


def tr_time(ts=None):
    dt = datetime.fromtimestamp(ts or now_ts(), tz=TR_TZ)
    return dt.strftime("%H:%M:%S")


def fnum(v, default=0.0):
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def fmt(v):
    v = fnum(v)
    if v >= 100:
        return f"{v:.2f}"
    if v >= 1:
        return f"{v:.4f}"
    if v >= 0.01:
        return f"{v:.6f}"
    return f"{v:.10f}"


def pct(new, old):
    old = fnum(old)
    new = fnum(new)
    return ((new - old) / old) * 100 if old else 0.0


def ema(values, period):
    vals = [fnum(v) for v in values if fnum(v) > 0]
    if len(vals) < period:
        return None

    k = 2 / (period + 1)
    e = sum(vals[:period]) / period

    for val in vals[period:]:
        e = val * k + e * (1 - k)

    return e


def calc_rsi(values, period=14):
    vals = [fnum(v) for v in values if fnum(v) > 0]

    if len(vals) < period + 1:
        return 50.0

    gains = []
    losses = []

    for i in range(1, period + 1):
        diff = vals[i] - vals[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(vals)):
        diff = vals[i] - vals[i - 1]
        gain = max(diff, 0)
        loss = abs(min(diff, 0))
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def aggregate_15m_closes(ohlcv):
    closes = []
    start = len(ohlcv) % 3

    for i in range(start, len(ohlcv) - 2, 3):
        closes.append(fnum(ohlcv[i + 2][4]))

    return closes


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN / CHAT_ID yok")
        return False

    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(
            url,
            data={"chat_id": CHAT_ID, "text": message},
            timeout=20
        )
        print("Telegram:", r.status_code, r.text[:200])
        return r.status_code == 200
    except Exception as e:
        print("Telegram hata:", e)
        return False


def load_state():
    try:
        if not os.path.exists(STATE_FILE):
            return {}

        txt = open(STATE_FILE, "r", encoding="utf-8").read().strip()
        return json.loads(txt) if txt else {}
    except Exception as e:
        print("state okuma hata:", e)
        return {}


def save_state(state):
    try:
        open(STATE_FILE, "w", encoding="utf-8").write(
            json.dumps(state, indent=2, ensure_ascii=False)
        )
        return True
    except Exception as e:
        print("state yazma hata:", e)
        return False


def make_exchange():
    return ccxt.okx({
        "enableRateLimit": True,
        "options": {"defaultType": "swap"}
    })


def load_okx_futures_symbols(ex):
    markets = ex.load_markets()
    tickers = ex.fetch_tickers()
    rows = []

    for symbol, market in markets.items():
        try:
            if not market.get("swap"):
                continue
            if not market.get("linear"):
                continue
            if market.get("quote") != "USDT":
                continue
            if not market.get("active", True):
                continue

            ticker = tickers.get(symbol, {})
            info = ticker.get("info", {}) or {}

            qv = fnum(
                ticker.get("quoteVolume")
                or info.get("volCcy24h")
                or info.get("volCcyQuote")
                or info.get("vol24h")
                or 0
            )

            if qv < MIN_24H_QUOTE_VOLUME:
                continue

            rows.append((qv, symbol))
        except Exception:
            continue

    rows.sort(reverse=True)
    return [symbol for _, symbol in rows[:MAX_COINS_PER_RUN]]


def fetch_ohlcv(ex, symbol, limit=90):
    try:
        data = ex.fetch_ohlcv(symbol, timeframe="5m", limit=limit)

        if not data or len(data) < 60:
            return None

        return data
    except Exception as e:
        print("fetch hata", symbol, e)
        return None


def color_count(ohlcv, direction):
    count = 0

    for candle in reversed(ohlcv[-7:-1]):
        is_green = fnum(candle[4]) > fnum(candle[1])
        is_red = fnum(candle[4]) < fnum(candle[1])

        if direction == "LONG" and is_green:
            count += 1
        elif direction == "SHORT" and is_red:
            count += 1
        else:
            break

    return count


def candle_close_strength(candle):
    high = fnum(candle[2])
    low = fnum(candle[3])
    close = fnum(candle[4])

    if high <= low:
        return 0.5

    return (close - low) / (high - low)


def common_data(symbol, o):
    close = fnum(o[-1][4])
    open_5m = fnum(o[-1][1])
    high_5m = fnum(o[-1][2])
    low_5m = fnum(o[-1][3])

    if close <= 0 or open_5m <= 0:
        return None

    ch5 = pct(close, open_5m)
    ch15 = pct(close, fnum(o[-3][1]))
    ch1h = pct(close, fnum(o[-12][1]))
    ch2h = pct(close, fnum(o[-24][1]))

    last_vol = fnum(o[-1][5])
    prev_vols = [fnum(x[5]) for x in o[-25:-5]]
    avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    vr5 = last_vol / avg_vol if avg_vol > 0 else 0

    vol15 = sum(fnum(x[5]) for x in o[-3:])
    prev15 = []

    for start in range(6, 36, 3):
        chunk = o[-start:-start + 3]
        if len(chunk) == 3:
            prev15.append(sum(fnum(x[5]) for x in chunk))

    avg15 = sum(prev15) / len(prev15) if prev15 else 0
    vr15 = vol15 / avg15 if avg15 > 0 else 0

    prev_high = max(fnum(x[2]) for x in o[-42:-2])
    prev_low = min(fnum(x[3]) for x in o[-42:-2])

    breakout = close > prev_high
    breakdown = close < prev_low

    resistance_distance = ((prev_high - close) / close) * 100 if close > 0 else 999
    support_distance = ((close - prev_low) / close) * 100 if close > 0 else 999

    if resistance_distance < 0:
        resistance_distance = 0

    if support_distance < 0:
        support_distance = 0

    support = min(fnum(x[3]) for x in o[-18:-2])
    resistance = max(fnum(x[2]) for x in o[-18:-2])

    long_sl = support * (1 - SL_BUFFER_PERCENT / 100)
    long_risk_distance = close - long_sl
    long_risk_percent = (long_risk_distance / close) * 100 if close > 0 else 999

    short_sl = resistance * (1 + SL_BUFFER_PERCENT / 100)
    short_risk_distance = short_sl - close
    short_risk_percent = (short_risk_distance / close) * 100 if close > 0 else 999

    closes = [fnum(x[4]) for x in o]
    closes15 = aggregate_15m_closes(o)

    rsi5 = calc_rsi(closes, 14)
    rsi15 = calc_rsi(closes15, 14)

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    if ema20 is None or ema50 is None:
        return None

    ema20_distance = abs((close - ema20) / close) * 100
    green_count = color_count(o, "LONG")
    red_count = color_count(o, "SHORT")
    close_strength = candle_close_strength(o[-1])
    body_percent = abs(pct(close, open_5m))
    range_percent = pct(high_5m, low_5m)

    return {
        "symbol": symbol,
        "close": close,
        "open_5m": open_5m,
        "high_5m": high_5m,
        "low_5m": low_5m,
        "ch5": ch5,
        "ch15": ch15,
        "ch1h": ch1h,
        "ch2h": ch2h,
        "vr5": vr5,
        "vr15": vr15,
        "prev_high": prev_high,
        "prev_low": prev_low,
        "breakout": breakout,
        "breakdown": breakdown,
        "resistance_distance": resistance_distance,
        "support_distance": support_distance,
        "support": support,
        "resistance": resistance,
        "long_sl": long_sl,
        "long_risk_distance": long_risk_distance,
        "long_risk_percent": long_risk_percent,
        "short_sl": short_sl,
        "short_risk_distance": short_risk_distance,
        "short_risk_percent": short_risk_percent,
        "ema20": ema20,
        "ema50": ema50,
        "ema20_distance": ema20_distance,
        "rsi5": rsi5,
        "rsi15": rsi15,
        "green_count": green_count,
        "red_count": red_count,
        "close_strength": close_strength,
        "body_percent": body_percent,
        "range_percent": range_percent,
    }


def score_long_breakout(d):
    reasons = []
    score = 0

    score += 20
    reasons.append("direnç kırılımı var")

    if d["ch5"] >= 1.0:
        score += 18
        reasons.append(f"5M güçlü hareket %{round(d['ch5'], 2)}")
    else:
        score += 12
        reasons.append(f"5M erken hareket %{round(d['ch5'], 2)}")

    if d["ch15"] >= 1.5:
        score += 18
        reasons.append(f"15M yükseliş %{round(d['ch15'], 2)}")
    else:
        score += 12
        reasons.append(f"15M pozitif %{round(d['ch15'], 2)}")

    if d["vr5"] >= 5:
        score += 22
        reasons.append(f"5M hacim patlaması {round(d['vr5'], 2)}x")
    else:
        score += 16
        reasons.append(f"5M hacim güçlü {round(d['vr5'], 2)}x")

    if d["vr15"] >= 2.5:
        score += 18
        reasons.append(f"15M hacim güçlü {round(d['vr15'], 2)}x")
    else:
        score += 12
        reasons.append(f"15M hacim yeterli {round(d['vr15'], 2)}x")

    if d["close"] > d["ema20"] > d["ema50"]:
        score += 8
        reasons.append("EMA20/EMA50 yapısı olumlu")

    if 0 <= d["ch1h"] <= 4:
        score += 5
        reasons.append("1H çok şişmemiş")

    if d["long_risk_percent"] <= 1.60:
        score += 5
        reasons.append(f"stop mesafesi iyi %{round(d['long_risk_percent'], 2)}")

    if d["close_strength"] >= 0.70:
        score += 4
        reasons.append("son mum güçlü kapanmış")

    return score, reasons


def score_long_early(d):
    reasons = []
    score = 0

    score += 18
    reasons.append("erken pump modu")

    if d["resistance_distance"] <= 0.75:
        score += 16
        reasons.append(f"dirence çok yakın %{round(d['resistance_distance'], 2)}")
    else:
        score += 10
        reasons.append(f"dirence yakın %{round(d['resistance_distance'], 2)}")

    if d["long_risk_percent"] <= 1.35:
        score += 16
        reasons.append(f"stop çok yakın %{round(d['long_risk_percent'], 2)}")
    else:
        score += 10
        reasons.append(f"stop kabul edilebilir %{round(d['long_risk_percent'], 2)}")

    if d["vr5"] >= 4:
        score += 20
        reasons.append(f"5M hacim patlaması {round(d['vr5'], 2)}x")
    else:
        score += 14
        reasons.append(f"5M hacim güçlü {round(d['vr5'], 2)}x")

    if d["vr15"] >= 2:
        score += 16
        reasons.append(f"15M hacim güçlü {round(d['vr15'], 2)}x")
    else:
        score += 10
        reasons.append(f"15M hacim yeterli {round(d['vr15'], 2)}x")

    if d["close"] > d["ema20"] >= d["ema50"]:
        score += 10
        reasons.append("EMA üstü erken dönüş")

    if d["ema20_distance"] <= 0.55:
        score += 7
        reasons.append(f"EMA20 yakın %{round(d['ema20_distance'], 2)}")

    if 0 <= d["ch1h"] <= 3:
        score += 5
        reasons.append("1H şişmemiş")

    if d["close_strength"] >= 0.62:
        score += 4
        reasons.append("mum kapanışı güçlü")

    return score, reasons


def score_short_breakdown(d):
    reasons = []
    score = 0

    score += 20
    reasons.append("destek kırılımı var")

    if d["ch5"] <= -1.0:
        score += 18
        reasons.append(f"5M güçlü düşüş %{round(d['ch5'], 2)}")
    else:
        score += 12
        reasons.append(f"5M erken düşüş %{round(d['ch5'], 2)}")

    if d["ch15"] <= -1.5:
        score += 18
        reasons.append(f"15M satış baskısı %{round(d['ch15'], 2)}")
    else:
        score += 12
        reasons.append(f"15M negatif %{round(d['ch15'], 2)}")

    if d["vr5"] >= 5:
        score += 22
        reasons.append(f"5M satış hacmi patlaması {round(d['vr5'], 2)}x")
    else:
        score += 16
        reasons.append(f"5M satış hacmi güçlü {round(d['vr5'], 2)}x")

    if d["vr15"] >= 2.5:
        score += 18
        reasons.append(f"15M satış hacmi güçlü {round(d['vr15'], 2)}x")
    else:
        score += 12
        reasons.append(f"15M satış hacmi yeterli {round(d['vr15'], 2)}x")

    if d["close"] < d["ema20"] < d["ema50"]:
        score += 8
        reasons.append("EMA20/EMA50 yapısı aşağı")

    if -4 <= d["ch1h"] <= 0:
        score += 5
        reasons.append("1H aşırı düşmemiş")

    if d["short_risk_percent"] <= 1.60:
        score += 5
        reasons.append(f"stop mesafesi iyi %{round(d['short_risk_percent'], 2)}")

    if d["close_strength"] <= 0.30:
        score += 4
        reasons.append("son mum zayıf kapanmış")

    return score, reasons


def score_short_early(d):
    reasons = []
    score = 0

    score += 18
    reasons.append("erken dump modu")

    if d["support_distance"] <= 0.75:
        score += 16
        reasons.append(f"desteğe çok yakın %{round(d['support_distance'], 2)}")
    else:
        score += 10
        reasons.append(f"desteğe yakın %{round(d['support_distance'], 2)}")

    if d["short_risk_percent"] <= 1.35:
        score += 16
        reasons.append(f"stop çok yakın %{round(d['short_risk_percent'], 2)}")
    else:
        score += 10
        reasons.append(f"stop kabul edilebilir %{round(d['short_risk_percent'], 2)}")

    if d["vr5"] >= 4:
        score += 20
        reasons.append(f"5M satış hacmi patlaması {round(d['vr5'], 2)}x")
    else:
        score += 14
        reasons.append(f"5M satış hacmi güçlü {round(d['vr5'], 2)}x")

    if d["vr15"] >= 2:
        score += 16
        reasons.append(f"15M satış hacmi güçlü {round(d['vr15'], 2)}x")
    else:
        score += 10
        reasons.append(f"15M satış hacmi yeterli {round(d['vr15'], 2)}x")

    if d["close"] < d["ema20"] <= d["ema50"]:
        score += 10
        reasons.append("EMA altı erken satış")

    if d["ema20_distance"] <= 0.55:
        score += 7
        reasons.append(f"EMA20 yakın %{round(d['ema20_distance'], 2)}")

    if -3 <= d["ch1h"] <= 0:
        score += 5
        reasons.append("1H aşırı düşmemiş")

    if d["close_strength"] <= 0.38:
        score += 4
        reasons.append("mum kapanışı zayıf")

    return score, reasons


def analyze_long_breakout_mode(d):
    if not d["breakout"]:
        return None

    if d["ch5"] < MIN_5M_CHANGE_BREAKOUT or d["ch5"] > MAX_5M_CHANGE_BREAKOUT:
        return None
    if d["ch15"] < MIN_15M_CHANGE_BREAKOUT or d["ch15"] > MAX_15M_CHANGE_BREAKOUT:
        return None
    if d["ch1h"] < MIN_1H_CHANGE_LONG or d["ch1h"] > MAX_1H_CHANGE_BREAKOUT_LONG:
        return None
    if d["ch2h"] < MIN_2H_CHANGE_LONG or d["ch2h"] > MAX_2H_CHANGE_BREAKOUT_LONG:
        return None
    if d["vr5"] < MIN_5M_VOLUME_RATIO_BREAKOUT:
        return None
    if d["vr15"] < MIN_15M_VOLUME_RATIO_BREAKOUT:
        return None
    if d["long_risk_percent"] > MAX_RISK_BREAKOUT:
        return None
    if d["rsi5"] >= MAX_RSI_5M_BREAKOUT_LONG and d["rsi15"] >= MAX_RSI_15M_BREAKOUT_LONG:
        return None
    if d["ema20_distance"] > MAX_EMA20_DISTANCE_BREAKOUT:
        return None
    if d["close"] < d["ema20"] or d["ema20"] < d["ema50"]:
        return None
    if d["green_count"] > MAX_SAME_COLOR_CANDLES:
        return None
    if d["close_strength"] < 0.58:
        return None

    score, reasons = score_long_breakout(d)

    if score < MIN_SCORE_BREAKOUT:
        return None

    return "KIRILIM PUMP", score, reasons


def analyze_long_early_mode(d):
    if d["breakout"]:
        return None
    if d["ch5"] < MIN_5M_CHANGE_EARLY or d["ch5"] > MAX_5M_CHANGE_EARLY:
        return None
    if d["ch15"] < MIN_15M_CHANGE_EARLY or d["ch15"] > MAX_15M_CHANGE_EARLY:
        return None
    if d["ch1h"] < MIN_1H_CHANGE_LONG or d["ch1h"] > MAX_1H_CHANGE_EARLY_LONG:
        return None
    if d["ch2h"] < MIN_2H_CHANGE_LONG or d["ch2h"] > MAX_2H_CHANGE_EARLY_LONG:
        return None
    if d["vr5"] < MIN_5M_VOLUME_RATIO_EARLY:
        return None
    if d["vr15"] < MIN_15M_VOLUME_RATIO_EARLY:
        return None
    if d["long_risk_percent"] > MAX_RISK_EARLY:
        return None
    if d["rsi5"] >= MAX_RSI_5M_EARLY_LONG and d["rsi15"] >= MAX_RSI_15M_EARLY_LONG:
        return None
    if d["ema20_distance"] > MAX_EMA20_DISTANCE_EARLY:
        return None
    if d["resistance_distance"] > MAX_RESISTANCE_DISTANCE_EARLY:
        return None
    if d["close"] < d["ema20"] or d["ema20"] < d["ema50"]:
        return None
    if d["green_count"] > MAX_SAME_COLOR_CANDLES:
        return None
    if d["close_strength"] < 0.55:
        return None

    score, reasons = score_long_early(d)

    if score < MIN_SCORE_EARLY:
        return None

    return "ERKEN PUMP", score, reasons


def analyze_short_breakdown_mode(d):
    if not d["breakdown"]:
        return None

    if d["ch5"] < MIN_5M_DROP_BREAKDOWN or d["ch5"] > MAX_5M_DROP_BREAKDOWN:
        return None
    if d["ch15"] < MIN_15M_DROP_BREAKDOWN or d["ch15"] > MAX_15M_DROP_BREAKDOWN:
        return None
    if d["ch1h"] < MIN_1H_CHANGE_BREAKDOWN_SHORT or d["ch1h"] > MAX_1H_CHANGE_SHORT:
        return None
    if d["ch2h"] < MIN_2H_CHANGE_BREAKDOWN_SHORT or d["ch2h"] > MAX_2H_CHANGE_SHORT:
        return None
    if d["vr5"] < MIN_5M_VOLUME_RATIO_BREAKOUT:
        return None
    if d["vr15"] < MIN_15M_VOLUME_RATIO_BREAKOUT:
        return None
    if d["short_risk_percent"] > MAX_RISK_BREAKOUT:
        return None
    if d["rsi5"] <= MIN_RSI_5M_BREAKDOWN_SHORT and d["rsi15"] <= MIN_RSI_15M_BREAKDOWN_SHORT:
        return None
    if d["ema20_distance"] > MAX_EMA20_DISTANCE_BREAKOUT:
        return None
    if d["close"] > d["ema20"] or d["ema20"] > d["ema50"]:
        return None
    if d["red_count"] > MAX_SAME_COLOR_CANDLES:
        return None
    if d["close_strength"] > 0.42:
        return None

    score, reasons = score_short_breakdown(d)

    if score < MIN_SCORE_BREAKOUT:
        return None

    return "KIRILIM DUMP", score, reasons


def analyze_short_early_mode(d):
    if d["breakdown"]:
        return None
    if d["ch5"] < MIN_5M_DROP_EARLY or d["ch5"] > MAX_5M_DROP_EARLY:
        return None
    if d["ch15"] < MIN_15M_DROP_EARLY or d["ch15"] > MAX_15M_DROP_EARLY:
        return None
    if d["ch1h"] < MIN_1H_CHANGE_EARLY_SHORT or d["ch1h"] > MAX_1H_CHANGE_SHORT:
        return None
    if d["ch2h"] < MIN_2H_CHANGE_EARLY_SHORT or d["ch2h"] > MAX_2H_CHANGE_SHORT:
        return None
    if d["vr5"] < MIN_5M_VOLUME_RATIO_EARLY:
        return None
    if d["vr15"] < MIN_15M_VOLUME_RATIO_EARLY:
        return None
    if d["short_risk_percent"] > MAX_RISK_EARLY:
        return None
    if d["rsi5"] <= MIN_RSI_5M_EARLY_SHORT and d["rsi15"] <= MIN_RSI_15M_EARLY_SHORT:
        return None
    if d["ema20_distance"] > MAX_EMA20_DISTANCE_EARLY:
        return None
    if d["support_distance"] > MAX_SUPPORT_DISTANCE_EARLY_SHORT:
        return None
    if d["close"] > d["ema20"] or d["ema20"] > d["ema50"]:
        return None
    if d["red_count"] > MAX_SAME_COLOR_CANDLES:
        return None
    if d["close_strength"] > 0.45:
        return None

    score, reasons = score_short_early(d)

    if score < MIN_SCORE_EARLY:
        return None

    return "ERKEN DUMP", score, reasons


def make_signal(symbol, direction, mode, score, reasons, d):
    entry = d["close"]

    if direction == "LONG":
        sl = d["long_sl"]
        risk_distance = d["long_risk_distance"]
        risk_percent = d["long_risk_percent"]
        tp1 = entry + risk_distance * TP1_R
        tp2 = entry + risk_distance * TP2_R
        tp3 = entry + risk_distance * TP3_R
    else:
        sl = d["short_sl"]
        risk_distance = d["short_risk_distance"]
        risk_percent = d["short_risk_percent"]
        tp1 = entry - risk_distance * TP1_R
        tp2 = entry - risk_distance * TP2_R
        tp3 = entry - risk_distance * TP3_R

    if risk_distance <= 0:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "mode": mode,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "score": int(score),
        "created_ts": now_ts(),
        "tp1_hit": False,
        "tp2_hit": False,
        "source": "PUMP_DUMP_TRADE",
        "risk_percent": risk_percent,
        "ch5": d["ch5"],
        "ch15": d["ch15"],
        "ch1h": d["ch1h"],
        "ch2h": d["ch2h"],
        "vr5": d["vr5"],
        "vr15": d["vr15"],
        "support": d["support"],
        "resistance": d["resistance"],
        "prev_high": d["prev_high"],
        "prev_low": d["prev_low"],
        "breakout": d["breakout"],
        "breakdown": d["breakdown"],
        "resistance_distance": d["resistance_distance"],
        "support_distance": d["support_distance"],
        "ema20_distance": d["ema20_distance"],
        "rsi5": d["rsi5"],
        "rsi15": d["rsi15"],
        "green_count": d["green_count"],
        "red_count": d["red_count"],
        "close_strength": d["close_strength"],
        "reasons": reasons,
    }


def analyze_symbol(symbol, o):
    d = common_data(symbol, o)

    if not d:
        return None

    candidates = []

    result = analyze_long_breakout_mode(d) or analyze_long_early_mode(d)

    if result:
        mode, score, reasons = result
        sig = make_signal(symbol, "LONG", mode, score, reasons, d)

        if sig:
            candidates.append(sig)

    result = analyze_short_breakdown_mode(d) or analyze_short_early_mode(d)

    if result:
        mode, score, reasons = result
        sig = make_signal(symbol, "SHORT", mode, score, reasons, d)

        if sig:
            candidates.append(sig)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (x["score"], -x["risk_percent"], x["vr5"], x["vr15"]),
        reverse=True
    )

    return candidates[0]


def duplicate_key(signal):
    # Aynı coin için ters yön dahil 3 saat blok.
    # Amaç: pump/dump whipsaw sinyallerini azaltmak.
    return f"{signal['symbol']}::RADAR"


def open_key(signal):
    return f"{signal['symbol']}::{signal['direction']}"


def is_duplicate(signal, state):
    last = state.get("last_pump_signals", {})
    ts = int(last.get(duplicate_key(signal), 0))
    return now_ts() - ts < DUPLICATE_SECONDS


def mark_signal(signal, state):
    state.setdefault("last_pump_signals", {})[duplicate_key(signal)] = now_ts()

    cutoff = now_ts() - 24 * 3600

    state["last_pump_signals"] = {
        k: v for k, v in state["last_pump_signals"].items()
        if int(v) >= cutoff
    }


def signal_message(s):
    clean_symbol = s["symbol"].replace(":USDT", "")
    direction = s.get("direction", "LONG")

    if direction == "LONG":
        icon = "🚀"
        title = "🟢 PUMP LONG İŞLEM SİNYALİ"
        break_line = f"Direnç kırılımı: {'Evet' if s['breakout'] else 'Hayır'}"
        near_line = f"Yakın direnç: {fmt(s['prev_high'])} | Uzaklık: %{round(s['resistance_distance'], 2)}"
        level_line = f"Yakın destek: {fmt(s['support'])}"
        color_line = f"Yeşil mum: {s['green_count']}"
        note = (
            "\n📍 Erken pump notu:\n"
            f"Direnç henüz kırılmamış olabilir. Yakın direnç: {fmt(s['prev_high'])}\n"
            "Avantajı erken giriştir; dezavantajı fake pump riskidir."
        ) if s.get("mode") == "ERKEN PUMP" else ""
    else:
        icon = "🔻"
        title = "🔴 DUMP SHORT İŞLEM SİNYALİ"
        break_line = f"Destek kırılımı: {'Evet' if s['breakdown'] else 'Hayır'}"
        near_line = f"Yakın destek: {fmt(s['prev_low'])} | Uzaklık: %{round(s['support_distance'], 2)}"
        level_line = f"Yakın direnç: {fmt(s['resistance'])}"
        color_line = f"Kırmızı mum: {s['red_count']}"
        note = (
            "\n📍 Erken dump notu:\n"
            f"Destek henüz kırılmamış olabilir. Yakın destek: {fmt(s['prev_low'])}\n"
            "Avantajı erken giriştir; dezavantajı dipten short / fake dump riskidir."
        ) if s.get("mode") == "ERKEN DUMP" else ""

    return f"""{icon} {BOT_NAME}

{title}
Mod: {s.get('mode', 'RADAR')}
Coin: {clean_symbol}
Yön: {direction}
Skor: %{s['score']}

💰 Giriş: {fmt(s['entry'])}
🛑 Stop: {fmt(s['sl'])} | Risk: %{round(s['risk_percent'], 2)}

🎯 Hedefler:
TP1: {fmt(s['tp1'])}
TP2: {fmt(s['tp2'])}
TP3: {fmt(s['tp3'])}

📊 Güç:
5M: %{round(s['ch5'], 2)}
15M: %{round(s['ch15'], 2)}
1H: %{round(s['ch1h'], 2)}
2H: %{round(s['ch2h'], 2)}
5M Hacim: {round(s['vr5'], 2)}x
15M Hacim: {round(s['vr15'], 2)}x

📌 Filtre:
{break_line}
{near_line}
{level_line}
EMA20 uzaklığı: %{round(s['ema20_distance'], 2)}
RSI 5M / 15M: {round(s.get('rsi5', 50), 2)} / {round(s.get('rsi15', 50), 2)}
{color_line}
Mum kapanış gücü: %{round(s['close_strength'] * 100, 1)}

🧠 Neden geldi:
{', '.join(s['reasons'])}
{note}

📌 Kural:
Bu spot/takip radarı değildir.
Bu sadece OKX Futures güçlü pump/dump işlem sinyalidir.
Girişten uzaklaştıysa girme.
TP1 gelirse %50 kâr al, SL'yi girişe çek.
Stop şarttır. Otomatik emir açmaz.""".strip()


def update_open_signals(ex, state):
    open_signals = state.setdefault("open_pump_signals", {})

    if not open_signals:
        return

    to_delete = []

    for key, sig in list(open_signals.items()):
        try:
            symbol = sig["symbol"]
            direction = sig.get("direction", "LONG")

            # TP TAKİP DÜZELTMESİ:
            # Eski kod burada limit=8 kullanıyordu.
            # fetch_ohlcv fonksiyonu 60 mumdan az veriyi reddettiği için
            # TP/SL takibi hiç çalışmıyordu.
            # Bu yüzden artık 90 mum çekiyoruz.
            candles = fetch_ohlcv(ex, symbol, limit=90)

            if not candles:
                continue

            created_ms = int(sig.get("created_ts", now_ts())) * 1000

            # Sinyal zamanından sonraki mumları kontrol et.
            recent = [
                c for c in candles
                if int(c[0]) >= created_ms - (5 * 60 * 1000)
            ]

            # Saat/veri farkı yüzünden boş kalırsa son 3 mumu kullan.
            if not recent:
                recent = candles[-3:]

            # Çok eski muma bakıp yanlış TP/SL mesajı atmamak için
            # son 12 adet 5M mumu kontrol ediyoruz.
            # 12 adet 5M mum = yaklaşık 1 saat.
            recent = recent[-12:]

            hi = max(fnum(x[2]) for x in recent)
            lo = min(fnum(x[3]) for x in recent)
            close = fnum(candles[-1][4])

            entry = fnum(sig["entry"])
            sl = fnum(sig["sl"])
            tp1 = fnum(sig["tp1"])
            tp2 = fnum(sig["tp2"])
            tp3 = fnum(sig["tp3"])

            clean_symbol = symbol.replace(":USDT", "")
            age = now_ts() - int(sig.get("created_ts", now_ts()))
            is_long = direction == "LONG"

            if age > SIGNAL_TTL_SECONDS and not sig.get("tp1_hit"):
                send_telegram(
                    f"⏳ PUMP/DUMP SİNYAL SÜRESİ DOLDU\n"
                    f"Coin: {clean_symbol}\n"
                    f"Mod: {sig.get('mode', 'RADAR')}\n"
                    f"Yön: {direction}\n"
                    f"Giriş: {fmt(entry)} | Güncel: {fmt(close)}"
                )
                to_delete.append(key)
                continue

            if not sig.get("tp1_hit"):
                stop_hit = lo <= sl if is_long else hi >= sl
                tp1_hit = hi >= tp1 if is_long else lo <= tp1

                if stop_hit:
                    send_telegram(
                        f"❌ PUMP/DUMP STOP OLDU\n"
                        f"Coin: {clean_symbol}\n"
                        f"Mod: {sig.get('mode', 'RADAR')}\n"
                        f"Yön: {direction}\n"
                        f"Giriş: {fmt(entry)}\n"
                        f"SL: {fmt(sl)}\n"
                        f"Güncel: {fmt(close)}"
                    )
                    to_delete.append(key)
                    continue

                if tp1_hit:
                    sig["tp1_hit"] = True
                    sig["sl"] = entry

                    send_telegram(
                        f"✅ PUMP/DUMP TP1 GELDİ\n"
                        f"Coin: {clean_symbol}\n"
                        f"Mod: {sig.get('mode', 'RADAR')}\n"
                        f"Yön: {direction}\n"
                        f"Giriş: {fmt(entry)}\n"
                        f"TP1: {fmt(tp1)}\n"
                        f"Kural: %50 kâr al, SL girişe çek."
                    )
                    continue

            if sig.get("tp1_hit"):
                tp2_hit = hi >= tp2 if is_long else lo <= tp2
                tp3_hit = hi >= tp3 if is_long else lo <= tp3
                be_hit = lo <= entry if is_long else hi >= entry

                if not sig.get("tp2_hit") and tp2_hit:
                    sig["tp2_hit"] = True

                    send_telegram(
                        f"✅ PUMP/DUMP TP2 GELDİ\n"
                        f"Coin: {clean_symbol}\n"
                        f"Mod: {sig.get('mode', 'RADAR')}\n"
                        f"Yön: {direction}\n"
                        f"TP2: {fmt(tp2)}"
                    )
                    continue

                if tp3_hit:
                    send_telegram(
                        f"🏁 PUMP/DUMP TP3 GELDİ\n"
                        f"Coin: {clean_symbol}\n"
                        f"Mod: {sig.get('mode', 'RADAR')}\n"
                        f"Yön: {direction}\n"
                        f"TP3: {fmt(tp3)}"
                    )
                    to_delete.append(key)
                    continue

                if be_hit:
                    send_telegram(
                        f"🟡 PUMP/DUMP GİRİŞTEN KAPANDI\n"
                        f"Coin: {clean_symbol}\n"
                        f"Mod: {sig.get('mode', 'RADAR')}\n"
                        f"Yön: {direction}\n"
                        f"Giriş: {fmt(entry)}\n"
                        f"Güncel: {fmt(close)}"
                    )
                    to_delete.append(key)
                    continue

        except Exception as e:
            print("takip hata", key, e)

    for key in to_delete:
        open_signals.pop(key, None)


def run():
    state = load_state()
    ex = make_exchange()

    update_open_signals(ex, state)

    open_signals = state.setdefault("open_pump_signals", {})

    if len(open_signals) >= MAX_OPEN_SIGNALS:
        print("Maksimum açık pump/dump sinyaline ulaşıldı.")
        save_state(state)
        return

    symbols = load_okx_futures_symbols(ex)

    print(
        "Taranacak OKX Futures coin:",
        len(symbols),
        "| Limit:",
        MAX_COINS_PER_RUN,
        "| Min 24h hacim:",
        MIN_24H_QUOTE_VOLUME
    )

    candidates = []

    for symbol in symbols:
        try:
            candles = fetch_ohlcv(ex, symbol)

            if not candles:
                continue

            signal = analyze_symbol(symbol, candles)

            if not signal:
                continue

            if is_duplicate(signal, state):
                continue

            # Aynı coin için açık yön varsa tekrar gönderme.
            if open_key(signal) in open_signals:
                continue

            # Eski v3.2 state uyumluluğu:
            # Önceden LONG key böyle tutuluyordu.
            legacy_long_key = f"{signal['symbol']}::PUMP_LONG"

            if legacy_long_key in open_signals:
                continue

            candidates.append(signal)

        except Exception as e:
            print("analiz hata", symbol, e)

    candidates.sort(
        key=lambda x: (x["score"], -x["risk_percent"], x["vr5"], x["vr15"]),
        reverse=True
    )

    slots = max(0, MAX_OPEN_SIGNALS - len(open_signals))
    limit = min(MAX_NEW_SIGNALS_PER_RUN, slots)

    sent = 0

    for signal in candidates[:limit]:
        ok = send_telegram(signal_message(signal))

        if ok:
            key = open_key(signal)
            open_signals[key] = signal
            mark_signal(signal, state)
            sent += 1

    print("Yeni pump/dump işlem sinyali:", sent)

    save_state(state)


if __name__ == "__main__":
    run()


# V3_3_PUMP_DUMP_TP_FIX_NOTU:
# Ana bot, coin analiz botu, workflow ve diğer JSON dosyaları değiştirilmedi.
# Sadece pump_radar.py güncellendi.
# Eski LONG pump ve DUMP/SHORT mantığı korunur.
# Düzeltme: TP/SL takibi artık 8 mum değil 90 mum çeker.
# Düzeltme: Takip kontrolü sinyal zamanından sonraki 5M mumlara göre yapılır.
# Spot/takip/radar aday mesajı yoktur; sadece OKX Futures işlem sinyali üretir ve takip eder.