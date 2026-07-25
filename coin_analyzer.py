# coin_analyzer.py
# Tek coin detay analiz programı - Futures odaklı güvenli sürüm
#
# Kullanım:
#   python coin_analyzer.py BTCUSDT
# veya GitHub Actions > Coin Detay Analizi > Run workflow.
#
# Emir açmaz. Yalnızca analiz raporu üretir ve TOKEN / CHAT_ID varsa
# Telegram'a gönderir.
#
# Veri önceliği:
#   1) OKX USDT Perpetual Futures (Swap)
#   2) Binance USDT Perpetual Futures
#
# Spot veri özellikle kullanılmaz. Futures işlemi için futures fiyatı,
# hacmi ve mum yapısı esas alınır.

from __future__ import annotations

import os
import sys
from typing import Any

import ccxt
import pandas as pd
import requests
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange


TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SYMBOL = os.getenv("SYMBOL") or (sys.argv[1] if len(sys.argv) > 1 else "BTCUSDT")

TIMEFRAMES = {
    "5M": ("5m", 300),
    "15M": ("15m", 350),
    "1H": ("1h", 350),
    "4H": ("4h", 350),
}

MIN_TRADE_SCORE = 78
MIN_STOP_PERCENT = 0.15
MAX_STOP_PERCENT = 2.50
MAX_LATE_ENTRY_ATR = 0.40
INVALIDATION_ATR = 0.20


def send_telegram(message: str) -> bool:
    if not TOKEN or not CHAT_ID:
        print("TOKEN / CHAT_ID yok. Telegram gönderilmedi.")
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
        print("Telegram hatası:", exc)
        return False


def normalize_symbol(symbol: str) -> str:
    normalized = (
        str(symbol)
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace("_", "")
        .replace(":", "")
        .strip()
    )
    if not normalized.endswith("USDT"):
        normalized += "USDT"
    return normalized


def base_from_symbol(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    return normalized[:-4]


def pct(current: float, reference: float) -> float:
    if reference == 0:
        return 0.0
    return ((current - reference) / reference) * 100.0


def abs_pct(current: float, reference: float) -> float:
    return abs(pct(current, reference))


def make_exchange(exchange_id: str, market_type: str) -> ccxt.Exchange:
    common = {
        "enableRateLimit": True,
        "timeout": 30000,
        "options": {"defaultType": market_type},
    }

    if exchange_id == "okx":
        return ccxt.okx(common)
    if exchange_id == "binance":
        return ccxt.binance(common)
    raise RuntimeError(f"Bilinmeyen borsa: {exchange_id}")


def find_linear_usdt_swap(exchange: ccxt.Exchange, base: str) -> str | None:
    markets = exchange.load_markets()
    candidates: list[str] = []

    for market_symbol, market in markets.items():
        try:
            if not market.get("active", True):
                continue
            if str(market.get("base", "")).upper() != base:
                continue
            if str(market.get("quote", "")).upper() != "USDT":
                continue
            if not bool(market.get("swap", False)):
                continue
            if market.get("linear") is False:
                continue

            settle = str(market.get("settle", "USDT") or "USDT").upper()
            if settle != "USDT":
                continue

            candidates.append(market_symbol)
        except Exception:
            continue

    if not candidates:
        return None

    # CCXT'de genellikle BASE/USDT:USDT biçimi gelir.
    preferred = f"{base}/USDT:USDT"
    if preferred in candidates:
        return preferred
    return sorted(candidates)[0]


def resolve_data_source(symbol: str) -> dict[str, Any]:
    normalized = normalize_symbol(symbol)
    base = base_from_symbol(normalized)

    # Spot bilerek yoktur. Kullanıcı futures işlem yaptığı için aynı piyasanın
    # futures mumları, futures hacmi ve futures son fiyatı kullanılmalıdır.
    sources = [
        {
            "name": "OKX USDT Futures",
            "exchange_id": "okx",
            "market_type": "swap",
            "trade_note": "OKX perpetual futures verisi kullanılıyor.",
            "execution_note": "OKX üzerinde aynı futures kontratıyla karşılaştırılabilir.",
        },
        {
            "name": "Binance USDT Futures",
            "exchange_id": "binance",
            "market_type": "future",
            "trade_note": "OKX futures bulunamadığı için Binance perpetual futures verisi kullanılıyor.",
            "execution_note": "OKX'te aynı kontrat yoksa bu raporla OKX işlemi açma.",
        },
    ]

    errors: list[str] = []
    for source in sources:
        try:
            exchange = make_exchange(source["exchange_id"], source["market_type"])
            market_symbol = find_linear_usdt_swap(exchange, base)
            if market_symbol:
                return {
                    "exchange": exchange,
                    "market_symbol": market_symbol,
                    "source_name": source["name"],
                    "trade_note": source["trade_note"],
                    "execution_note": source["execution_note"],
                    "symbol": normalized,
                }
        except Exception as exc:
            errors.append(f"{source['name']}: {type(exc).__name__}")

    error_text = ", ".join(errors) if errors else "market bulunamadı"
    raise RuntimeError(
        f"{normalized} için OKX veya Binance üzerinde aktif lineer USDT perpetual futures "
        f"kontratı bulunamadı. Spot veriye düşülmedi. Kontrol: {error_text}"
    )


def fetch_df(
    exchange: ccxt.Exchange,
    market_symbol: str,
    timeframe: str,
    limit: int,
) -> pd.DataFrame | None:
    ohlcv = exchange.fetch_ohlcv(market_symbol, timeframe=timeframe, limit=limit)
    if not ohlcv or len(ohlcv) < 220:
        return None

    df = pd.DataFrame(
        ohlcv,
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    numeric_columns = ["open", "high", "low", "close", "volume"]
    df[numeric_columns] = df[numeric_columns].apply(pd.to_numeric, errors="coerce")
    df = df.dropna(subset=numeric_columns).reset_index(drop=True)
    return df if len(df) >= 220 else None


def add_indicators(df: pd.DataFrame | None) -> pd.DataFrame | None:
    if df is None or df.empty or len(df) < 220:
        return None

    result = df.copy().reset_index(drop=True)
    result["rsi"] = RSIIndicator(result["close"], window=14).rsi()
    result["ema20"] = EMAIndicator(result["close"], window=20).ema_indicator()
    result["ema50"] = EMAIndicator(result["close"], window=50).ema_indicator()
    result["ema100"] = EMAIndicator(result["close"], window=100).ema_indicator()
    result["ema200"] = EMAIndicator(result["close"], window=200).ema_indicator()

    macd = MACD(result["close"])
    result["macd"] = macd.macd()
    result["macd_signal"] = macd.macd_signal()
    result["macd_hist"] = result["macd"] - result["macd_signal"]

    result["atr"] = AverageTrueRange(
        result["high"], result["low"], result["close"], window=14
    ).average_true_range()
    result["adx"] = ADXIndicator(
        result["high"], result["low"], result["close"], window=14
    ).adx()

    result["volume_avg"] = result["volume"].rolling(20).mean()
    result["volume_ratio"] = result["volume"] / result["volume_avg"]
    result["ema20_slope"] = result["ema20"] - result["ema20"].shift(3)

    required = [
        "rsi",
        "ema20",
        "ema50",
        "ema100",
        "ema200",
        "macd",
        "macd_signal",
        "macd_hist",
        "atr",
        "adx",
        "volume_avg",
        "volume_ratio",
        "ema20_slope",
    ]
    result = result.dropna(subset=required).reset_index(drop=True)
    return result if len(result) >= 5 else None


def trend_status(df: pd.DataFrame | None, label: str) -> dict[str, Any]:
    if df is None or len(df) < 3:
        return {
            "direction": "NEUTRAL",
            "text": f"{label}: veri yetersiz",
            "rsi": "-",
            "adx": "-",
            "volume_ratio": "-",
            "ema20": 0.0,
            "ema50": 0.0,
            "ema200": 0.0,
        }

    row = df.iloc[-2]  # Son kapanmış mum
    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    ema200 = float(row["ema200"])
    slope = float(row["ema20_slope"])
    macd = float(row["macd"])
    macd_signal = float(row["macd_signal"])

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
        "rsi": round(float(row["rsi"]), 2),
        "adx": round(float(row["adx"]), 2),
        "volume_ratio": round(float(row["volume_ratio"]), 2),
        "ema20": ema20,
        "ema50": ema50,
        "ema200": ema200,
    }


def candle_signal_15m(df: pd.DataFrame | None) -> tuple[str, str]:
    if df is None or len(df) < 4:
        return "NEUTRAL", "15M veri yetersiz"

    last = df.iloc[-2]
    previous = df.iloc[-3]

    close = float(last["close"])
    open_price = float(last["open"])
    ema20 = float(last["ema20"])
    rsi = float(last["rsi"])
    macd_hist = float(last["macd_hist"])
    previous_macd_hist = float(previous["macd_hist"])

    green = close > open_price
    red = close < open_price

    long_reclaim = (
        green
        and close >= ema20
        and close > float(previous["close"])
        and macd_hist >= previous_macd_hist
        and 40 <= rsi <= 70
    )
    short_reject = (
        red
        and close <= ema20
        and close < float(previous["close"])
        and macd_hist <= previous_macd_hist
        and 30 <= rsi <= 60
    )

    if long_reclaim:
        return "LONG", "15M yeşil dönüş / EMA20 üstü"
    if short_reject:
        return "SHORT", "15M kırmızı dönüş / EMA20 altı"
    return "NEUTRAL", "15M net giriş dönüşü yok"


def radar_5m(df: pd.DataFrame | None) -> tuple[str, str]:
    if df is None or len(df) < 25:
        return "NEUTRAL", "5M veri yetersiz"

    last = df.iloc[-2]
    move = pct(float(last["close"]), float(last["open"]))
    volume_average = float(df["volume"].iloc[-22:-2].mean())
    volume_ratio = float(last["volume"]) / volume_average if volume_average > 0 else 0.0

    if move >= 0.30 and volume_ratio >= 1.15:
        return "LONG", f"5M yukarı hareket %{move:.2f} / hacim {volume_ratio:.2f}x"
    if move <= -0.30 and volume_ratio >= 1.15:
        return "SHORT", f"5M aşağı hareket %{move:.2f} / hacim {volume_ratio:.2f}x"
    return "NEUTRAL", f"5M sakin / hareket %{move:.2f} / hacim {volume_ratio:.2f}x"


def score_direction(
    direction: str,
    s4h: dict[str, Any],
    s1h: dict[str, Any],
    entry15: tuple[str, str],
    radar5: tuple[str, str],
    df15: pd.DataFrame,
) -> tuple[int, list[str]]:
    """0-100 aralığında kalite skoru üretir; başarı yüzdesi değildir."""
    row = df15.iloc[-2]
    rsi = float(row["rsi"])
    adx = float(row["adx"])
    volume_ratio = float(row["volume_ratio"])

    score = 0
    reasons: list[str] = []

    if s4h["direction"] == direction:
        score += 25
        reasons.append("4H aynı yön")
    elif s4h["direction"] not in ("NEUTRAL", direction):
        reasons.append("4H ters")

    if s1h["direction"] == direction:
        score += 25
        reasons.append("1H aynı yön")
    elif s1h["direction"] not in ("NEUTRAL", direction):
        reasons.append("1H ters")

    if entry15[0] == direction:
        score += 20
        reasons.append("15M giriş onayı")

    if radar5[0] == direction:
        score += 10
        reasons.append("5M momentum destekli")
    elif radar5[0] not in ("NEUTRAL", direction):
        reasons.append("5M ters momentum")

    if volume_ratio >= 1.30:
        score += 8
        reasons.append("15M hacim güçlü")
    elif volume_ratio >= 0.75:
        score += 5
        reasons.append("15M hacim yeterli")
    else:
        reasons.append("15M hacim zayıf")

    if adx >= 25:
        score += 6
        reasons.append("15M ADX güçlü")
    elif adx >= 15:
        score += 3
        reasons.append("15M ADX orta")
    else:
        reasons.append("15M ADX zayıf")

    if direction == "LONG":
        if 42 <= rsi <= 68:
            score += 6
            reasons.append("RSI LONG için uygun")
        elif rsi > 72:
            reasons.append("RSI şişmiş")
        else:
            reasons.append("RSI LONG için zayıf")
    else:
        if 32 <= rsi <= 58:
            score += 6
            reasons.append("RSI SHORT için uygun")
        elif rsi < 28:
            reasons.append("RSI çok dip")
        else:
            reasons.append("RSI SHORT için zayıf")

    return max(0, min(100, int(score))), reasons


def final_verdict(
    long_score: int,
    short_score: int,
    s4h: dict[str, Any],
    s1h: dict[str, Any],
    entry15: tuple[str, str],
    radar5: tuple[str, str],
) -> tuple[str, str]:
    # Canlı para için ana yön zorunluluğu.
    if s4h["direction"] == "NEUTRAL" or s1h["direction"] == "NEUTRAL":
        return "WAIT", "4H ve 1H yönü net değil"

    if s4h["direction"] != s1h["direction"]:
        return "WAIT", "4H ve 1H aynı yönde değil"

    direction = s4h["direction"]
    selected_score = long_score if direction == "LONG" else short_score
    opposite_score = short_score if direction == "LONG" else long_score

    if entry15[0] != direction:
        return "WAIT", f"Ana yön {direction}, fakat 15M giriş onayı yok"

    if radar5[0] not in ("NEUTRAL", direction):
        return "WAIT", "5M momentum ana yöne ters"

    if selected_score < MIN_TRADE_SCORE:
        return "WAIT", f"{direction} kalite skoru yetersiz: {selected_score}/100"

    if selected_score < opposite_score + 10:
        return "WAIT", "LONG ve SHORT tarafı yeterince ayrışmadı"

    return direction, f"4H + 1H + 15M {direction} uyumu var"


def _cluster_levels(levels: list[float], tolerance: float) -> list[float]:
    if not levels:
        return []

    ordered = sorted(levels)
    clusters: list[list[float]] = [[ordered[0]]]
    for level in ordered[1:]:
        current_average = sum(clusters[-1]) / len(clusters[-1])
        if abs(level - current_average) <= tolerance:
            clusters[-1].append(level)
        else:
            clusters.append([level])

    # Daha çok dokunulan kümeler önce; eşitlikte fiyat sırası korunur.
    weighted = sorted(
        ((sum(cluster) / len(cluster), len(cluster)) for cluster in clusters),
        key=lambda item: (-item[1], item[0]),
    )
    return [item[0] for item in weighted]


def pivot_support_resistance(
    df: pd.DataFrame | None,
    price: float,
    lookback: int = 120,
) -> dict[str, float]:
    if df is None or len(df) < 15:
        return {
            "support1": price,
            "support2": price,
            "resistance1": price,
            "resistance2": price,
            "support_distance": 0.0,
            "resistance_distance": 0.0,
        }

    # Tamamlanmamış son mumu dışarıda bırak.
    recent = df.iloc[:-1].tail(lookback).reset_index(drop=True)
    atr = float(recent["atr"].iloc[-1])
    tolerance = max(atr * 0.25, price * 0.0003)

    pivot_lows: list[float] = []
    pivot_highs: list[float] = []

    for index in range(2, len(recent) - 2):
        window = recent.iloc[index - 2 : index + 3]
        low = float(recent.iloc[index]["low"])
        high = float(recent.iloc[index]["high"])
        if low <= float(window["low"].min()):
            pivot_lows.append(low)
        if high >= float(window["high"].max()):
            pivot_highs.append(high)

    supports = [level for level in _cluster_levels(pivot_lows, tolerance) if level < price]
    resistances = [level for level in _cluster_levels(pivot_highs, tolerance) if level > price]

    supports = sorted(supports, reverse=True)
    resistances = sorted(resistances)

    fallback_low = float(recent["low"].min())
    fallback_high = float(recent["high"].max())

    support1 = supports[0] if supports else min(fallback_low, price)
    support2 = supports[1] if len(supports) > 1 else support1
    resistance1 = resistances[0] if resistances else max(fallback_high, price)
    resistance2 = resistances[1] if len(resistances) > 1 else resistance1

    return {
        "support1": support1,
        "support2": support2,
        "resistance1": resistance1,
        "resistance2": resistance2,
        "support_distance": abs_pct(price, support1),
        "resistance_distance": abs_pct(resistance1, price),
    }


def leverage_suggestion(risk_percent: float) -> str:
    if risk_percent <= 0.85:
        return "3x"
    if risk_percent <= 1.60:
        return "2x"
    if risk_percent <= 2.50:
        return "1x-2x"
    return "PAS GEÇ"


def build_trade_plan(
    direction: str,
    price: float,
    df15: pd.DataFrame,
) -> tuple[dict[str, float | str] | None, str | None]:
    if len(df15) < 20:
        return None, "15M işlem planı için veri yetersiz"

    row = df15.iloc[-2]
    signal_close = float(row["close"])
    ema20 = float(row["ema20"])
    atr = float(row["atr"])
    recent = df15.iloc[-14:-2]

    if recent.empty or atr <= 0 or price <= 0:
        return None, "ATR veya fiyat verisi geçersiz"

    if direction == "LONG":
        if price > signal_close + atr * MAX_LATE_ENTRY_ATR:
            return None, "LONG girişi kaçmış; fiyat 15M sinyal mumundan fazla uzaklaştı"
        if price < ema20 - atr * INVALIDATION_ATR:
            return None, "LONG kurulumu bozulmuş; fiyat EMA20 altına indi"

        swing_low = float(recent["low"].min())
        stop = min(swing_low - atr * 0.10, price - atr * 1.10)
        risk = price - stop
        tp1 = price + risk * 0.75
        tp2 = price + risk * 1.35
        tp3 = price + risk * 2.00

    elif direction == "SHORT":
        if price < signal_close - atr * MAX_LATE_ENTRY_ATR:
            return None, "SHORT girişi kaçmış; fiyat 15M sinyal mumundan fazla uzaklaştı"
        if price > ema20 + atr * INVALIDATION_ATR:
            return None, "SHORT kurulumu bozulmuş; fiyat EMA20 üstüne çıktı"

        swing_high = float(recent["high"].max())
        stop = max(swing_high + atr * 0.10, price + atr * 1.10)
        risk = stop - price
        tp1 = price - risk * 0.75
        tp2 = price - risk * 1.35
        tp3 = price - risk * 2.00

        if min(tp1, tp2, tp3) <= 0:
            return None, "Hedef fiyatlardan biri geçersiz"
    else:
        return None, "Yön geçersiz"

    if risk <= 0:
        return None, "Stop mesafesi geçersiz"

    risk_percent = (risk / price) * 100.0
    if risk_percent < MIN_STOP_PERCENT:
        return None, f"Stop çok dar: %{risk_percent:.2f}"
    if risk_percent > MAX_STOP_PERCENT:
        return None, f"Stop çok geniş: %{risk_percent:.2f}"

    return {
        "entry": price,
        "sl": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_percent": risk_percent,
        "rr1": 0.75,
        "rr2": 1.35,
        "rr3": 2.00,
        "leverage": leverage_suggestion(risk_percent),
    }, None


def format_price(exchange: ccxt.Exchange, market_symbol: str, value: float) -> str:
    try:
        return exchange.price_to_precision(market_symbol, float(value))
    except Exception:
        number = float(value)
        if number >= 100:
            return f"{number:.2f}"
        if number >= 1:
            return f"{number:.4f}"
        if number >= 0.01:
            return f"{number:.6f}"
        return f"{number:.10f}"


def plan_text(
    direction: str,
    plan: dict[str, float | str],
    exchange: ccxt.Exchange,
    market_symbol: str,
) -> str:
    icon = "🟢" if direction == "LONG" else "🔴"
    return f"""
{icon} ONAYLI {direction} İŞLEM PLANI
Giriş: {format_price(exchange, market_symbol, float(plan['entry']))}
TP1: {format_price(exchange, market_symbol, float(plan['tp1']))}
TP2: {format_price(exchange, market_symbol, float(plan['tp2']))}
TP3: {format_price(exchange, market_symbol, float(plan['tp3']))}
SL: {format_price(exchange, market_symbol, float(plan['sl']))}
Stop Mesafesi: %{float(plan['risk_percent']):.2f}
R/R TP1: {float(plan['rr1']):.2f}
R/R TP2: {float(plan['rr2']):.2f}
R/R TP3: {float(plan['rr3']):.2f}
Kaldıraç Önerisi: {plan['leverage']}
"""


def waiting_conditions(
    s4h: dict[str, Any],
    s1h: dict[str, Any],
    entry15: tuple[str, str],
    radar5: tuple[str, str],
) -> str:
    conditions: list[str] = []

    if s4h["direction"] == "NEUTRAL":
        conditions.append("4H yönünün netleşmesi")
    if s1h["direction"] == "NEUTRAL":
        conditions.append("1H yönünün netleşmesi")
    if (
        s4h["direction"] != "NEUTRAL"
        and s1h["direction"] != "NEUTRAL"
        and s4h["direction"] != s1h["direction"]
    ):
        conditions.append("4H ve 1H yönlerinin aynı tarafa dönmesi")
    if entry15[0] == "NEUTRAL":
        conditions.append("15M kapanmış mum giriş onayı")
    if radar5[0] == "NEUTRAL":
        conditions.append("5M hacimli momentum desteği")

    if not conditions:
        conditions.append("skor, giriş uzaklığı ve stop mesafesinin uygunlaşması")

    return "\n".join(f"• {condition}" for condition in conditions[:4])


def analyze_coin(symbol: str) -> str:
    normalized = normalize_symbol(symbol)
    source = resolve_data_source(normalized)

    exchange: ccxt.Exchange = source["exchange"]
    market_symbol: str = source["market_symbol"]

    dfs: dict[str, pd.DataFrame] = {}
    for label, (timeframe, limit) in TIMEFRAMES.items():
        raw_df = fetch_df(exchange, market_symbol, timeframe, limit)
        if raw_df is None:
            raise RuntimeError(
                f"{normalized} için {label} futures verisi yetersiz. En az 220 mum gerekli."
            )

        df = add_indicators(raw_df)
        if df is None:
            raise RuntimeError(
                f"{normalized} için {label} indikatör verisi üretilemedi. Coin çok yeni olabilir."
            )
        dfs[label] = df

    ticker = exchange.fetch_ticker(market_symbol)
    price_value = ticker.get("last")
    if price_value is None:
        raise RuntimeError(f"{normalized} futures güncel fiyatı alınamadı.")
    price = float(price_value)

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

    sr15 = pivot_support_resistance(df15, price, lookback=120)
    sr1h = pivot_support_resistance(df1h, price, lookback=120)
    sr4h = pivot_support_resistance(df4h, price, lookback=120)

    long_score, long_reasons = score_direction("LONG", s4h, s1h, entry15, radar5, df15)
    short_score, short_reasons = score_direction("SHORT", s4h, s1h, entry15, radar5, df15)

    verdict, verdict_reason = final_verdict(
        long_score,
        short_score,
        s4h,
        s1h,
        entry15,
        radar5,
    )

    plan: dict[str, float | str] | None = None
    plan_error: str | None = None
    if verdict in ("LONG", "SHORT"):
        plan, plan_error = build_trade_plan(verdict, price, df15)
        if plan is None:
            verdict = "WAIT"
            verdict_reason = plan_error or "Giriş veya stop şartı uygun değil"

    row15 = df15.iloc[-2]
    report = f"""
📊 TEK COIN DETAY ANALİZİ

Coin: {normalized}
Veri Kaynağı: {source['source_name']}
Market: {market_symbol}
Güncel Futures Fiyatı: {format_price(exchange, market_symbol, price)}
Not: {source['trade_note']}
Uygulama: {source['execution_note']}

🧭 Çoklu Zaman Dilimi
• {s4h['text']} | RSI: {s4h['rsi']} | ADX: {s4h['adx']}
• {s1h['text']} | RSI: {s1h['rsi']} | ADX: {s1h['adx']}
• {s15['text']} | RSI: {s15['rsi']} | ADX: {s15['adx']}
• {s5['text']} | RSI: {s5['rsi']} | ADX: {s5['adx']}

📌 Giriş / Radar
• 15M: {entry15[1]}
• 5M: {radar5[1]}

📊 Hacim
• 15M Hacim Oranı: {float(row15['volume_ratio']):.2f}x

🟢 Destek Bölgeleri
• 15M Destek 1: {format_price(exchange, market_symbol, sr15['support1'])} | Uzaklık: %{sr15['support_distance']:.2f}
• 15M Destek 2: {format_price(exchange, market_symbol, sr15['support2'])}
• 1H Destek: {format_price(exchange, market_symbol, sr1h['support1'])}
• 4H Destek: {format_price(exchange, market_symbol, sr4h['support1'])}

🔴 Direnç Bölgeleri
• 15M Direnç 1: {format_price(exchange, market_symbol, sr15['resistance1'])} | Uzaklık: %{sr15['resistance_distance']:.2f}
• 15M Direnç 2: {format_price(exchange, market_symbol, sr15['resistance2'])}
• 1H Direnç: {format_price(exchange, market_symbol, sr1h['resistance1'])}
• 4H Direnç: {format_price(exchange, market_symbol, sr4h['resistance1'])}

🟢 LONG Kalite Skoru: {long_score}/100
Nedenler: {', '.join(long_reasons) if long_reasons else 'Yeterli neden yok'}

🔴 SHORT Kalite Skoru: {short_score}/100
Nedenler: {', '.join(short_reasons) if short_reasons else 'Yeterli neden yok'}

📌 Genel Karar
{verdict} → {verdict_reason}
"""

    if verdict in ("LONG", "SHORT") and plan is not None:
        report += plan_text(verdict, plan, exchange, market_symbol)
    else:
        report += f"""

⏳ İŞLEM PLANI ÜRETİLMEDİ
WAIT kararında giriş, TP ve SL gösterilmez.

Beklenen Onaylar
{waiting_conditions(s4h, s1h, entry15, radar5)}
"""

    report += """

📌 Risk Notu
Bu rapor işlem emri değildir.
Grafikte kontrol etmeden işlem açma.
Fiyat girişten uzaklaşmışsa peşinden koşma.
TP1 gelirse yaklaşık %50 kâr alıp kalan stopu girişe çekmek daha güvenlidir.
"""

    return report.strip()


def main() -> None:
    symbol = normalize_symbol(SYMBOL)
    print("Futures coin analizi yapılıyor:", symbol)

    try:
        report = analyze_coin(symbol)
        print(report)
        send_telegram(report)
    except Exception as exc:
        message = f"❌ Coin analiz hatası\n\nCoin: {symbol}\nHata: {exc}"
        print(message)
        send_telegram(message)
        raise


if __name__ == "__main__":
    main()
