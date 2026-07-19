# scalp_radar.py
# Hızlı Scalp Radar v1
# Ana MTF bot ve Pump/Dump Radar'dan tamamen ayrı çalışır.
# Emir açmaz; sadece Telegram sinyali gönderir ve kendi state dosyasında takip eder.

import os, json, time, math
from datetime import datetime, timezone, timedelta
import requests
import ccxt

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

BOT_NAME = "Hızlı Scalp Radar v1"
STATE_FILE = "scalp_radar_state.json"
TR_TZ = timezone(timedelta(hours=3))

MAX_COINS_PER_RUN = 300
MIN_24H_QUOTE_VOLUME = 500_000
MAX_NEW_SIGNALS_PER_RUN = 2
MAX_OPEN_SIGNALS = 3
DUPLICATE_SECONDS = 90 * 60
SIGNAL_TTL_SECONDS = 3 * 60 * 60

# LONG: hızlı düşüş + tepki
LONG_DROP_5M = -1.20
LONG_DROP_15M = -0.60
LONG_RSI_1M_MIN = 18.0
LONG_RSI_1M_MAX = 38.0
LONG_RSI_5M_MAX = 45.0
LONG_MIN_LOWER_WICK = 0.42
LONG_MIN_CLOSE_STRENGTH = 0.50

# SHORT: hızlı yükseliş + red
SHORT_RISE_5M = 1.20
SHORT_RISE_15M = 0.60
SHORT_RSI_1M_MIN = 62.0
SHORT_RSI_5M_MIN = 55.0
SHORT_MIN_UPPER_WICK = 0.42
SHORT_MAX_CLOSE_STRENGTH = 0.50

MIN_VOLUME_RATIO_1M = 2.00
MIN_VOLUME_RATIO_5M = 1.30
MAX_RISK_PERCENT = 1.35
MAX_ABS_1H_CHANGE = 8.0
MIN_SCORE = 78

TP1_R = 0.55
TP2_R = 0.95
TP3_R = 1.35
SL_BUFFER_PERCENT = 0.08


def now_ts():
    return int(time.time())


def fmt(v):
    v = fnum(v)
    if v >= 100:
        return f"{v:.2f}"
    if v >= 1:
        return f"{v:.4f}"
    if v >= 0.01:
        return f"{v:.6f}"
    return f"{v:.10f}"


def fnum(v, default=0.0):
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return default
        return x
    except Exception:
        return default


def pct(new, old):
    old, new = fnum(old), fnum(new)
    return ((new - old) / old) * 100 if old else 0.0


def calc_rsi(values, period=14):
    vals = [fnum(v) for v in values if fnum(v) > 0]
    if len(vals) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = vals[i] - vals[i - 1]
        gains.append(max(diff, 0))
        losses.append(abs(min(diff, 0)))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period + 1, len(vals)):
        diff = vals[i] - vals[i - 1]
        avg_gain = ((avg_gain * (period - 1)) + max(diff, 0)) / period
        avg_loss = ((avg_loss * (period - 1)) + abs(min(diff, 0))) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def aggregate_closes(ohlcv, n):
    closes = []
    start = len(ohlcv) % n
    for i in range(start, len(ohlcv) - n + 1, n):
        closes.append(fnum(ohlcv[i + n - 1][4]))
    return closes


def candle_stats(candle):
    o, h, l, c = fnum(candle[1]), fnum(candle[2]), fnum(candle[3]), fnum(candle[4])
    if h <= l:
        return {"close_strength": 0.5, "lower_wick": 0.0, "upper_wick": 0.0}
    rng = h - l
    body_high, body_low = max(o, c), min(o, c)
    return {
        "close_strength": (c - l) / rng,
        "lower_wick": (body_low - l) / rng,
        "upper_wick": (h - body_high) / rng,
    }


def send_telegram(message):
    if not TOKEN or not CHAT_ID:
        print("TOKEN / CHAT_ID yok")
        return False
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        r = requests.post(url, data={"chat_id": CHAT_ID, "text": message}, timeout=20)
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
        open(STATE_FILE, "w", encoding="utf-8").write(json.dumps(state, indent=2, ensure_ascii=False))
    except Exception as e:
        print("state yazma hata:", e)


def make_exchange():
    return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def load_symbols(ex):
    markets = ex.load_markets()
    tickers = ex.fetch_tickers()
    rows = []
    for symbol, market in markets.items():
        try:
            if not market.get("swap") or not market.get("linear"):
                continue
            if market.get("quote") != "USDT" or not market.get("active", True):
                continue
            ticker = tickers.get(symbol, {})
            info = ticker.get("info", {}) or {}
            qv = fnum(ticker.get("quoteVolume") or info.get("volCcy24h") or info.get("volCcyQuote") or info.get("vol24h") or 0)
            if qv >= MIN_24H_QUOTE_VOLUME:
                rows.append((qv, symbol))
        except Exception:
            continue
    rows.sort(reverse=True)
    return [s for _, s in rows[:MAX_COINS_PER_RUN]]


def fetch_ohlcv(ex, symbol, limit=90):
    try:
        data = ex.fetch_ohlcv(symbol, timeframe="1m", limit=limit)
        if not data or len(data) < 60:
            return None
        return data
    except Exception as e:
        print("fetch hata", symbol, e)
        return None


def vol_ratio(o, bars):
    current = sum(fnum(x[5]) for x in o[-bars:])
    prev = [fnum(x[5]) for x in o[-35:-5]]
    avg = (sum(prev) / len(prev)) * bars if prev else 0
    return current / avg if avg > 0 else 0


def common(symbol, o):
    entry = fnum(o[-1][4])
    if entry <= 0:
        return None
    highs = [fnum(x[2]) for x in o[-35:-2]]
    lows = [fnum(x[3]) for x in o[-35:-2]]
    resistance = max(highs)
    support = min(lows)
    closes1 = [fnum(x[4]) for x in o]
    closes5 = aggregate_closes(o, 5)
    last = candle_stats(o[-1])
    prev = candle_stats(o[-2])
    long_sl = min(support, fnum(o[-1][3]), fnum(o[-2][3])) * (1 - SL_BUFFER_PERCENT / 100)
    short_sl = max(resistance, fnum(o[-1][2]), fnum(o[-2][2])) * (1 + SL_BUFFER_PERCENT / 100)
    long_risk = entry - long_sl
    short_risk = short_sl - entry
    return {
        "symbol": symbol,
        "entry": entry,
        "ch1": pct(entry, fnum(o[-2][4])),
        "ch3": pct(entry, fnum(o[-3][1])),
        "ch5": pct(entry, fnum(o[-5][1])),
        "ch15": pct(entry, fnum(o[-15][1])),
        "ch1h": pct(entry, fnum(o[-60][1])),
        "vr1": vol_ratio(o, 1),
        "vr5": vol_ratio(o, 5),
        "rsi1": calc_rsi(closes1, 14),
        "rsi5": calc_rsi(closes5, 14),
        "support": support,
        "resistance": resistance,
        "support_distance": max(0, ((entry - support) / entry) * 100),
        "resistance_distance": max(0, ((resistance - entry) / entry) * 100),
        "last_close_strength": last["close_strength"],
        "lower_wick": max(last["lower_wick"], prev["lower_wick"]),
        "upper_wick": max(last["upper_wick"], prev["upper_wick"]),
        "long_sl": long_sl,
        "short_sl": short_sl,
        "long_risk": long_risk,
        "short_risk": short_risk,
        "long_risk_pct": (long_risk / entry) * 100 if entry else 999,
        "short_risk_pct": (short_risk / entry) * 100 if entry else 999,
    }


def make_signal(d, direction, mode, score, reasons):
    entry = d["entry"]
    if direction == "LONG":
        sl, risk, risk_pct = d["long_sl"], d["long_risk"], d["long_risk_pct"]
        tp1, tp2, tp3 = entry + risk * TP1_R, entry + risk * TP2_R, entry + risk * TP3_R
    else:
        sl, risk, risk_pct = d["short_sl"], d["short_risk"], d["short_risk_pct"]
        tp1, tp2, tp3 = entry - risk * TP1_R, entry - risk * TP2_R, entry - risk * TP3_R
    if risk <= 0 or risk_pct > MAX_RISK_PERCENT:
        return None
    return {
        "symbol": d["symbol"], "direction": direction, "mode": mode,
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "risk_percent": risk_pct, "score": min(100, int(score)), "created_ts": now_ts(),
        "tp1_hit": False, "tp2_hit": False,
        "ch1": d["ch1"], "ch3": d["ch3"], "ch5": d["ch5"], "ch15": d["ch15"], "ch1h": d["ch1h"],
        "vr1": d["vr1"], "vr5": d["vr5"], "rsi1": d["rsi1"], "rsi5": d["rsi5"],
        "support": d["support"], "resistance": d["resistance"],
        "support_distance": d["support_distance"], "resistance_distance": d["resistance_distance"],
        "close_strength": d["last_close_strength"], "lower_wick": d["lower_wick"], "upper_wick": d["upper_wick"],
        "reasons": reasons, "source": "SCALP_RADAR",
    }


def analyze(symbol, o):
    d = common(symbol, o)
    if not d or abs(d["ch1h"]) > MAX_ABS_1H_CHANGE:
        return None
    candidates = []

    long_ok = (
        d["ch5"] <= LONG_DROP_5M and d["ch15"] <= LONG_DROP_15M and
        LONG_RSI_1M_MIN <= d["rsi1"] <= LONG_RSI_1M_MAX and d["rsi5"] <= LONG_RSI_5M_MAX and
        d["vr1"] >= MIN_VOLUME_RATIO_1M and d["vr5"] >= MIN_VOLUME_RATIO_5M and
        d["lower_wick"] >= LONG_MIN_LOWER_WICK and d["last_close_strength"] >= LONG_MIN_CLOSE_STRENGTH and
        d["long_risk_pct"] <= MAX_RISK_PERCENT
    )
    if long_ok:
        score = 35 + min(20, int(abs(d["ch5"]) * 8)) + min(20, int(d["vr1"] * 3)) + min(15, int(d["vr5"] * 4))
        score += 10 if d["support_distance"] <= 0.45 else 0
        reasons = [
            f"5M hızlı düşüş %{round(d['ch5'], 2)}",
            f"15M geri çekilme %{round(d['ch15'], 2)}",
            f"RSI düşük {round(d['rsi1'], 2)} / {round(d['rsi5'], 2)}",
            f"hacim {round(d['vr1'], 2)}x / {round(d['vr5'], 2)}x",
            "aşağı fitil ve toparlanma var",
        ]
        if score >= MIN_SCORE:
            candidates.append(make_signal(d, "LONG", "HIZLI TEPKİ LONG", score, reasons))

    short_ok = (
        d["ch5"] >= SHORT_RISE_5M and d["ch15"] >= SHORT_RISE_15M and
        d["rsi1"] >= SHORT_RSI_1M_MIN and d["rsi5"] >= SHORT_RSI_5M_MIN and
        d["vr1"] >= MIN_VOLUME_RATIO_1M and d["vr5"] >= MIN_VOLUME_RATIO_5M and
        d["upper_wick"] >= SHORT_MIN_UPPER_WICK and d["last_close_strength"] <= SHORT_MAX_CLOSE_STRENGTH and
        d["short_risk_pct"] <= MAX_RISK_PERCENT
    )
    if short_ok:
        score = 35 + min(20, int(abs(d["ch5"]) * 8)) + min(20, int(d["vr1"] * 3)) + min(15, int(d["vr5"] * 4))
        score += 10 if d["resistance_distance"] <= 0.45 else 0
        reasons = [
            f"5M hızlı yükseliş %{round(d['ch5'], 2)}",
            f"15M yükseliş %{round(d['ch15'], 2)}",
            f"RSI yüksek {round(d['rsi1'], 2)} / {round(d['rsi5'], 2)}",
            f"hacim {round(d['vr1'], 2)}x / {round(d['vr5'], 2)}x",
            "üst fitil ve red var",
        ]
        if score >= MIN_SCORE:
            candidates.append(make_signal(d, "SHORT", "HIZLI RED SHORT", score, reasons))

    candidates = [c for c in candidates if c]
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x["score"], -x["risk_percent"], x["vr1"]), reverse=True)
    return candidates[0]


def signal_message(s):
    clean = s["symbol"].replace(":USDT", "")
    if s["direction"] == "LONG":
        icon = "⚡🟢"
        title = "HIZLI SCALP LONG"
        level = f"Yakın destek: {fmt(s['support'])} | Uzaklık: %{round(s['support_distance'], 2)}"
        wick = f"Alt fitil/tepki: %{round(s['lower_wick'] * 100, 1)}"
        note = "Hızlı düşüş sonrası tepki arar. Fake tepki riski vardır."
    else:
        icon = "⚡🔴"
        title = "HIZLI SCALP SHORT"
        level = f"Yakın direnç: {fmt(s['resistance'])} | Uzaklık: %{round(s['resistance_distance'], 2)}"
        wick = f"Üst fitil/red: %{round(s['upper_wick'] * 100, 1)}"
        note = "Hızlı yükseliş sonrası red arar. Fake red riski vardır."
    return f"""{icon} {BOT_NAME}

{title}
Mod: {s['mode']}
Coin: {clean}
Yön: {s['direction']}
Skor: %{s['score']}

💰 Giriş: {fmt(s['entry'])}
🛑 Stop: {fmt(s['sl'])} | Risk: %{round(s['risk_percent'], 2)}

🎯 Hedefler:
TP1: {fmt(s['tp1'])}
TP2: {fmt(s['tp2'])}
TP3: {fmt(s['tp3'])}

📊 Hızlı hareket:
1M: %{round(s['ch1'], 2)}
3M: %{round(s['ch3'], 2)}
5M: %{round(s['ch5'], 2)}
15M: %{round(s['ch15'], 2)}
1H: %{round(s['ch1h'], 2)}

📊 Hacim / RSI:
1M Hacim: {round(s['vr1'], 2)}x
5M Hacim: {round(s['vr5'], 2)}x
RSI 1M / 5M: {round(s['rsi1'], 2)} / {round(s['rsi5'], 2)}

📌 Seviye:
{level}
{wick}
Mum kapanış gücü: %{round(s['close_strength'] * 100, 1)}

🧠 Neden geldi:
{', '.join(s['reasons'])}

📌 Kural:
Bu ayrı hızlı scalp radarıdır.
Ana MTF bot ve Pump/Dump Radar ile karışmaz.
Girişten uzaklaştıysa girme.
TP1 gelirse %50 kâr al, SL'yi girişe çek.
Stop şarttır. Otomatik emir açmaz.
Not: {note}""".strip()


def open_key(sig):
    return f"{sig['symbol']}::{sig['direction']}"


def duplicate_key(sig):
    return f"{sig['symbol']}::{sig['direction']}::SCALP"


def is_duplicate(sig, state):
    ts = int(state.get("last_scalp_signals", {}).get(duplicate_key(sig), 0))
    return now_ts() - ts < DUPLICATE_SECONDS


def mark(sig, state):
    state.setdefault("last_scalp_signals", {})[duplicate_key(sig)] = now_ts()
    cutoff = now_ts() - 24 * 3600
    state["last_scalp_signals"] = {k: v for k, v in state["last_scalp_signals"].items() if int(v) >= cutoff}


def update_open(ex, state):
    open_sigs = state.setdefault("open_scalp_signals", {})
    if not open_sigs:
        return
    remove = []
    for key, sig in list(open_sigs.items()):
        try:
            o = fetch_ohlcv(ex, sig["symbol"], limit=8)
            if not o:
                continue
            hi = max(fnum(x[2]) for x in o[-5:])
            lo = min(fnum(x[3]) for x in o[-5:])
            close = fnum(o[-1][4])
            is_long = sig["direction"] == "LONG"
            entry, sl, tp1, tp2, tp3 = map(fnum, [sig["entry"], sig["sl"], sig["tp1"], sig["tp2"], sig["tp3"]])
            clean = sig["symbol"].replace(":USDT", "")
            age = now_ts() - int(sig.get("created_ts", now_ts()))

            if age > SIGNAL_TTL_SECONDS and not sig.get("tp1_hit"):
                send_telegram(f"⏳ SCALP SİNYAL SÜRESİ DOLDU\nCoin: {clean}\nYön: {sig['direction']}\nGiriş: {fmt(entry)} | Güncel: {fmt(close)}")
                remove.append(key); continue

            if not sig.get("tp1_hit"):
                stop_hit = lo <= sl if is_long else hi >= sl
                tp1_hit = hi >= tp1 if is_long else lo <= tp1
                if stop_hit:
                    send_telegram(f"❌ SCALP STOP OLDU\nCoin: {clean}\nYön: {sig['direction']}\nGiriş: {fmt(entry)}\nSL: {fmt(sl)}\nGüncel: {fmt(close)}")
                    remove.append(key); continue
                if tp1_hit:
                    sig["tp1_hit"] = True
                    sig["sl"] = entry
                    send_telegram(f"✅ SCALP TP1 GELDİ\nCoin: {clean}\nYön: {sig['direction']}\nGiriş: {fmt(entry)}\nTP1: {fmt(tp1)}\nKural: %50 kâr al, SL girişe çek.")
                    continue

            if sig.get("tp1_hit"):
                tp2_hit = hi >= tp2 if is_long else lo <= tp2
                tp3_hit = hi >= tp3 if is_long else lo <= tp3
                be_hit = lo <= entry if is_long else hi >= entry
                if not sig.get("tp2_hit") and tp2_hit:
                    sig["tp2_hit"] = True
                    send_telegram(f"✅ SCALP TP2 GELDİ\nCoin: {clean}\nYön: {sig['direction']}\nTP2: {fmt(tp2)}")
                    continue
                if tp3_hit:
                    send_telegram(f"🏁 SCALP TP3 GELDİ\nCoin: {clean}\nYön: {sig['direction']}\nTP3: {fmt(tp3)}")
                    remove.append(key); continue
                if be_hit:
                    send_telegram(f"🟡 SCALP GİRİŞTEN KAPANDI\nCoin: {clean}\nYön: {sig['direction']}\nGiriş: {fmt(entry)}\nGüncel: {fmt(close)}")
                    remove.append(key); continue
        except Exception as e:
            print("takip hata", key, e)
    for key in remove:
        open_sigs.pop(key, None)


def run():
    state = load_state()
    ex = make_exchange()
    update_open(ex, state)
    open_sigs = state.setdefault("open_scalp_signals", {})
    if len(open_sigs) >= MAX_OPEN_SIGNALS:
        print("Maksimum açık scalp sinyaline ulaşıldı.")
        save_state(state)
        return
    symbols = load_symbols(ex)
    print("Taranacak OKX Futures coin:", len(symbols), "| Limit:", MAX_COINS_PER_RUN, "| Min 24h hacim:", MIN_24H_QUOTE_VOLUME)
    candidates = []
    for symbol in symbols:
        try:
            o = fetch_ohlcv(ex, symbol)
            if not o:
                continue
            sig = analyze(symbol, o)
            if not sig or is_duplicate(sig, state) or open_key(sig) in open_sigs:
                continue
            candidates.append(sig)
        except Exception as e:
            print("analiz hata", symbol, e)
    candidates.sort(key=lambda x: (x["score"], -x["risk_percent"], x["vr1"], x["vr5"]), reverse=True)
    slots = max(0, MAX_OPEN_SIGNALS - len(open_sigs))
    sent = 0
    for sig in candidates[:min(MAX_NEW_SIGNALS_PER_RUN, slots)]:
        if send_telegram(signal_message(sig)):
            open_sigs[open_key(sig)] = sig
            mark(sig, state)
            sent += 1
    print("Yeni hızlı scalp sinyali:", sent)
    save_state(state)


if __name__ == "__main__":
    run()

# HIZLI_SCALP_RADAR_V1_NOTU:
# Ana bot, pump_radar.py, config.py, main.py ve mevcut JSON dosyaları değiştirilmez.
# Ayrı dosya: scalp_radar.py
# Ayrı state: scalp_radar_state.json
# Ayrı workflow: .github/workflows/scalp-radar.yml
