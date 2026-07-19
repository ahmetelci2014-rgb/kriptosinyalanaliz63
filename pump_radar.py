# pump_radar.py
# Net Pump Radar v2
# Sadece OKX USDT Futures tarafında güçlü pump LONG işlem sinyali üretir.
# Spot uyarı, zayıf "erken hacim radarı", sadece takip mesajı yoktur.
# Emir açmaz; Telegram sinyali gönderir ve kendi pump_radar_state.json içinde TP/SL takibi yapar.

import os
import json
import time
import math
import requests
import ccxt
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BOT_NAME = "Net Pump Radar v2"
STATE_FILE = "pump_radar_state.json"
TR_TZ = timezone(timedelta(hours=3))

# Tarama
MAX_COINS_PER_RUN = 140
MIN_24H_QUOTE_VOLUME = 750_000
MAX_NEW_SIGNALS_PER_RUN = 2
MAX_OPEN_SIGNALS = 2

# Aynı coin tekrar blok
DUPLICATE_SECONDS = 180 * 60

# Pump sinyali 6 saat içinde TP1 görmezse takipten çıkar
SIGNAL_TTL_SECONDS = 6 * 60 * 60

# Net pump işlem filtresi
MIN_SCORE = 82

# Çok erken ama boş hareket olmasın / çok geç de kalmasın
MIN_5M_CHANGE = 0.60
MAX_5M_CHANGE = 2.20
MIN_15M_CHANGE = 0.80
MAX_15M_CHANGE = 4.50
MIN_1H_CHANGE = 0.00
MAX_1H_CHANGE = 8.00
MIN_2H_CHANGE = -0.50
MAX_2H_CHANGE = 12.00

# Hacim şartı
MIN_5M_VOLUME_RATIO = 3.00
MIN_15M_VOLUME_RATIO = 1.80

# Risk ve yapı şartı
MAX_SUPPORT_DISTANCE_PERCENT = 2.50
MAX_EMA20_DISTANCE_PERCENT = 1.40
MAX_GREEN_CANDLES = 4

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


def green_count(ohlcv):
    count = 0
    for candle in reversed(ohlcv[-7:-1]):
        if fnum(candle[4]) > fnum(candle[1]):
            count += 1
        else:
            break
    return count


def candle_close_strength(candle):
    high = fnum(candle[2])
    low = fnum(candle[3])
    close = fnum(candle[4])
    if high <= low:
        return 0.0
    return (close - low) / (high - low)


def analyze_symbol(symbol, o):
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
    breakout = close > prev_high

    support = min(fnum(x[3]) for x in o[-18:-2])
    sl = support * (1 - SL_BUFFER_PERCENT / 100)
    risk_distance = close - sl

    if risk_distance <= 0:
        return None

    risk_percent = (risk_distance / close) * 100

    closes = [fnum(x[4]) for x in o]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    if ema20 is None or ema50 is None:
        return None

    ema20_distance = abs((close - ema20) / close) * 100
    greens = green_count(o)
    close_strength = candle_close_strength(o[-1])
    body_percent = abs(pct(close, open_5m))
    range_percent = pct(high_5m, low_5m)

    # Kesin eleme:
    # Gönderdiğin örneklerden:
    # TRUTH -> 15M hacim 1.40x ve destek %3.78 olduğu için elenir.
    # ORDI -> Spot olduğu için bu dosyada zaten taranmaz.
    # BONK -> kırılım yok, 5M hacim zayıf, destek uzak; elenir.
    # CARDS -> Spot ve kırılım yok, 15M negatif; elenir.
    # LAB -> kırılım yok; elenir.
    if not breakout:
        return None

    if ch5 < MIN_5M_CHANGE or ch5 > MAX_5M_CHANGE:
        return None

    if ch15 < MIN_15M_CHANGE or ch15 > MAX_15M_CHANGE:
        return None

    if ch1h < MIN_1H_CHANGE or ch1h > MAX_1H_CHANGE:
        return None

    if ch2h < MIN_2H_CHANGE or ch2h > MAX_2H_CHANGE:
        return None

    if vr5 < MIN_5M_VOLUME_RATIO:
        return None

    if vr15 < MIN_15M_VOLUME_RATIO:
        return None

    if risk_percent > MAX_SUPPORT_DISTANCE_PERCENT:
        return None

    if ema20_distance > MAX_EMA20_DISTANCE_PERCENT:
        return None

    if close < ema20 or ema20 < ema50:
        return None

    if greens > MAX_GREEN_CANDLES:
        return None

    # Son mum kapanışı güçlü olmalı; fitilden dönmüş hareketleri azaltır.
    if close_strength < 0.58:
        return None

    score = 0
    reasons = []

    score += 20
    reasons.append("direnç kırılımı var")

    if ch5 >= 1.0:
        score += 18
        reasons.append(f"5M güçlü hareket %{round(ch5, 2)}")
    else:
        score += 12
        reasons.append(f"5M erken hareket %{round(ch5, 2)}")

    if ch15 >= 1.5:
        score += 18
        reasons.append(f"15M yükseliş %{round(ch15, 2)}")
    else:
        score += 12
        reasons.append(f"15M pozitif %{round(ch15, 2)}")

    if vr5 >= 5:
        score += 22
        reasons.append(f"5M hacim patlaması {round(vr5, 2)}x")
    else:
        score += 16
        reasons.append(f"5M hacim güçlü {round(vr5, 2)}x")

    if vr15 >= 2.5:
        score += 18
        reasons.append(f"15M hacim güçlü {round(vr15, 2)}x")
    else:
        score += 12
        reasons.append(f"15M hacim yeterli {round(vr15, 2)}x")

    if close > ema20 > ema50:
        score += 8
        reasons.append("EMA20/EMA50 yapısı olumlu")

    if 0 <= ch1h <= 4:
        score += 5
        reasons.append("1H çok şişmemiş")

    if risk_percent <= 1.60:
        score += 5
        reasons.append(f"stop mesafesi iyi %{round(risk_percent, 2)}")

    if close_strength >= 0.70:
        score += 4
        reasons.append("son mum güçlü kapanmış")

    if score < MIN_SCORE:
        return None

    tp1 = close + risk_distance * TP1_R
    tp2 = close + risk_distance * TP2_R
    tp3 = close + risk_distance * TP3_R

    return {
        "symbol": symbol,
        "direction": "LONG",
        "entry": close,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "score": int(score),
        "created_ts": now_ts(),
        "tp1_hit": False,
        "tp2_hit": False,
        "source": "PUMP_TRADE",
        "risk_percent": risk_percent,
        "ch5": ch5,
        "ch15": ch15,
        "ch1h": ch1h,
        "ch2h": ch2h,
        "vr5": vr5,
        "vr15": vr15,
        "support": support,
        "prev_high": prev_high,
        "ema20_distance": ema20_distance,
        "greens": greens,
        "close_strength": close_strength,
        "reasons": reasons,
    }


def signal_key(signal):
    return f"{signal['symbol']}::PUMP_LONG"


def is_duplicate(signal, state):
    last = state.get("last_pump_signals", {})
    ts = int(last.get(signal_key(signal), 0))
    return now_ts() - ts < DUPLICATE_SECONDS


def mark_signal(signal, state):
    state.setdefault("last_pump_signals", {})[signal_key(signal)] = now_ts()

    cutoff = now_ts() - 24 * 3600
    state["last_pump_signals"] = {
        k: v for k, v in state["last_pump_signals"].items()
        if int(v) >= cutoff
    }


def signal_message(s):
    clean_symbol = s["symbol"].replace(":USDT", "")

    return f"""🚀 {BOT_NAME}

🟢 PUMP RADAR İŞLEM SİNYALİ
Coin: {clean_symbol}
Yön: LONG
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
Direnç kırılımı: Evet
Kırılan seviye: {fmt(s['prev_high'])}
Yakın destek: {fmt(s['support'])}
EMA20 uzaklığı: %{round(s['ema20_distance'], 2)}
Yeşil mum: {s['greens']}
Mum kapanış gücü: %{round(s['close_strength'] * 100, 1)}

🧠 Neden geldi:
{', '.join(s['reasons'])}

📌 Kural:
Bu spot/takip radarı değildir.
Bu sadece OKX Futures güçlü pump LONG sinyalidir.
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
            candles = fetch_ohlcv(ex, symbol, limit=8)
            if not candles:
                continue

            recent = candles[-3:]
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

            if age > SIGNAL_TTL_SECONDS and not sig.get("tp1_hit"):
                send_telegram(
                    f"⏳ PUMP SİNYAL SÜRESİ DOLDU\n"
                    f"Coin: {clean_symbol}\n"
                    f"Giriş: {fmt(entry)} | Güncel: {fmt(close)}"
                )
                to_delete.append(key)
                continue

            if not sig.get("tp1_hit"):
                if lo <= sl:
                    send_telegram(
                        f"❌ PUMP STOP OLDU\n"
                        f"Coin: {clean_symbol}\n"
                        f"Yön: LONG\n"
                        f"Giriş: {fmt(entry)}\n"
                        f"SL: {fmt(sl)}\n"
                        f"Güncel: {fmt(close)}"
                    )
                    to_delete.append(key)
                    continue

                if hi >= tp1:
                    sig["tp1_hit"] = True
                    sig["sl"] = entry
                    send_telegram(
                        f"✅ PUMP TP1 GELDİ\n"
                        f"Coin: {clean_symbol}\n"
                        f"Yön: LONG\n"
                        f"Giriş: {fmt(entry)}\n"
                        f"TP1: {fmt(tp1)}\n"
                        f"Kural: %50 kâr al, SL girişe çek."
                    )
                    continue

            if sig.get("tp1_hit"):
                if not sig.get("tp2_hit") and hi >= tp2:
                    sig["tp2_hit"] = True
                    send_telegram(
                        f"✅ PUMP TP2 GELDİ\n"
                        f"Coin: {clean_symbol}\n"
                        f"Yön: LONG\n"
                        f"TP2: {fmt(tp2)}"
                    )
                    continue

                if hi >= tp3:
                    send_telegram(
                        f"🏁 PUMP TP3 GELDİ\n"
                        f"Coin: {clean_symbol}\n"
                        f"Yön: LONG\n"
                        f"TP3: {fmt(tp3)}"
                    )
                    to_delete.append(key)
                    continue

                if lo <= entry:
                    send_telegram(
                        f"🟡 PUMP GİRİŞTEN KAPANDI\n"
                        f"Coin: {clean_symbol}\n"
                        f"Yön: LONG\n"
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
        print("Maksimum açık pump sinyaline ulaşıldı.")
        save_state(state)
        return

    symbols = load_okx_futures_symbols(ex)
    print("Taranacak OKX Futures coin:", len(symbols))

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

            if signal_key(signal) in open_signals:
                continue

            candidates.append(signal)

        except Exception as e:
            print("analiz hata", symbol, e)

    candidates.sort(
        key=lambda x: (x["score"], x["vr5"], x["vr15"], -x["risk_percent"]),
        reverse=True
    )

    slots = max(0, MAX_OPEN_SIGNALS - len(open_signals))
    limit = min(MAX_NEW_SIGNALS_PER_RUN, slots)

    sent = 0

    for signal in candidates[:limit]:
        ok = send_telegram(signal_message(signal))
        if ok:
            key = signal_key(signal)
            open_signals[key] = signal
            mark_signal(signal, state)
            sent += 1

    print("Yeni pump işlem sinyali:", sent)
    save_state(state)


if __name__ == "__main__":
    run()
