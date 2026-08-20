from __future__ import annotations

import json
import math
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Sequence

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator, MACD
from ta.volatility import AverageTrueRange

import all_market_shadow as market_scan

VERSION = "PREMIUM_ALL_COINS_V4_2026_08_20"
STATE_FILE = "premium_all_coins_state.json"

OUTSIDE_DEEP_SCAN_PER_RUN = 60
HOT_OUTSIDE_PER_RUN = 20

MATURE_MIN_CANDLES = 220
YOUNG_MIN_15M = 40
YOUNG_MIN_1H = 24
YOUNG_MIN_SCORE = 96
ULTRA_MIN_1M = 24
ULTRA_MIN_SCORE = 97

YOUNG_MIN_VOLUME_RATIO = 1.20
YOUNG_MIN_ADX = 18.0
YOUNG_MAX_ZONE_DISTANCE_PERCENT = 0.45
YOUNG_MAX_RISK_PERCENT = 2.20
YOUNG_MIN_RISK_PERCENT = 0.60

ULTRA_MIN_VOLUME_RATIO = 1.50
ULTRA_MAX_RISK_PERCENT = 3.00
ULTRA_MIN_RISK_PERCENT = 0.90

TP1_R = 0.55
TP2_R = 1.05
TP3_R = 1.60

_EXCHANGE = None


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _load_state() -> Dict[str, Any]:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {"version": VERSION, "rotation_cursor": 0}


def _save_state(data: Dict[str, Any]) -> bool:
    folder = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
    os.makedirs(folder, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=folder, delete=False,
            prefix=".premium_all_coins.", suffix=".tmp"
        ) as handle:
            tmp = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with open(tmp, "r", encoding="utf-8") as handle:
            checked = json.load(handle)
        if not isinstance(checked, dict):
            raise ValueError("state root")
        os.replace(tmp, STATE_FILE)
        tmp = None
        return True
    except Exception as exc:
        print("Premium all-coins state write:", type(exc).__name__, exc)
        return False
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def build_scan_universe(
    exchange: Any,
    priority_coins: Sequence[str],
    min_quote_volume: float,
    max_scan_coins: int,
) -> List[str]:
    """
    Every eligible OKX USDT perpetual is screened via load_markets + fetch_tickers.
    Existing Premium universe is deep-scanned every run; OUTSIDE300 is rotated with
    hot movers first so all markets receive deep MTF coverage over time.
    """
    global _EXCHANGE
    _EXCHANGE = exchange

    state = _load_state()
    try:
        markets = exchange.load_markets()
        tickers = exchange.fetch_tickers()
        universe = market_scan.build_universe(
            markets=markets,
            tickers=tickers,
            priority_coins=priority_coins,
            min_quote_volume=min_quote_volume,
            max_scan_coins=max_scan_coins,
        )
        outside = universe.get("outside") or []
        chosen, next_cursor = market_scan.select_deep_scan(
            outside,
            int(state.get("rotation_cursor") or 0),
            max_per_run=OUTSIDE_DEEP_SCAN_PER_RUN,
            hot_count=HOT_OUTSIDE_PER_RUN,
        )

        base = list(universe.get("live_reference_symbols") or [])
        extras = [
            str(row.get("symbol") or "").upper()
            for row in chosen
            if isinstance(row, dict) and row.get("symbol")
        ]

        seen = set()
        combined = []
        for symbol in base + extras:
            symbol = str(symbol or "").upper()
            if symbol and symbol not in seen:
                seen.add(symbol)
                combined.append(symbol)

        state.update({
            "version": VERSION,
            "rotation_cursor": next_cursor,
            "last_run_at": int(time.time()),
            "eligible_usdt_swap_total": len(universe.get("eligible") or []),
            "base_deep_count": len(base),
            "outside_total": len(outside),
            "outside_deep_this_run": len(extras),
            "deep_total_this_run": len(combined),
        })
        _save_state(state)

        print(
            "PREMIUM ALL-COINS | eligible:",
            state["eligible_usdt_swap_total"],
            "| core:",
            len(base),
            "| outside deep:",
            len(extras),
            "| total deep:",
            len(combined),
        )
        return combined
    except Exception as exc:
        print("Premium all-coins universe fallback:", type(exc).__name__, exc)
        return []


def make_adaptive_fetcher(original):
    """Keep available young-coin history instead of discarding frames below 120 candles."""
    def wrapped(exchange, symbol, timeframe, limit, min_len=20):
        adjusted = min_len
        if int(min_len or 0) >= 120 and str(timeframe) in {"15m", "1h", "4h"}:
            adjusted = 12
        return original(exchange, symbol, timeframe, limit, min_len=adjusted)
    return wrapped


def _frame(df: Any, fast: int = 20, slow: int = 50) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy") or len(df) < 30:
        return None
    frame = df.copy()
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(frame.columns)):
        return None
    for col in needed:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    if len(frame) < 30:
        return None

    if len(frame) < slow + 10:
        slow = 20
        fast = 10

    frame["ema_fast"] = EMAIndicator(frame["close"], window=fast).ema_indicator()
    frame["ema_slow"] = EMAIndicator(frame["close"], window=slow).ema_indicator()
    frame["rsi"] = RSIIndicator(frame["close"], window=14).rsi()
    frame["adx"] = ADXIndicator(frame["high"], frame["low"], frame["close"], window=14).adx()
    frame["atr"] = AverageTrueRange(frame["high"], frame["low"], frame["close"], window=14).average_true_range()
    macd = MACD(frame["close"])
    frame["macd_hist"] = macd.macd() - macd.macd_signal()
    frame["volume_avg"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_avg"]
    frame["ema_slope"] = frame["ema_fast"] - frame["ema_fast"].shift(3)
    frame = frame.dropna().reset_index(drop=True)
    return frame if len(frame) >= 5 else None


def _close_power(row: Any) -> float:
    high = _sf(row.get("high"), 0.0) or 0.0
    low = _sf(row.get("low"), 0.0) or 0.0
    close = _sf(row.get("close"), 0.0) or 0.0
    width = high - low
    return 50.0 if width <= 0 else max(0.0, min(100.0, (close - low) / width * 100.0))


def _targets(direction: str, entry: float, sl: float) -> Optional[Dict[str, float]]:
    risk = abs(entry - sl)
    if entry <= 0 or sl <= 0 or risk <= 0:
        return None
    rp = risk / entry * 100.0
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
    return {"tp1": tp1, "tp2": tp2, "tp3": tp3, "risk_percent": rp}


def _price(exchange: Any, symbol: str, fallback: Any = None) -> Optional[float]:
    p = _sf(fallback)
    if p and p > 0:
        return p
    try:
        ticker = exchange.fetch_ticker(market_scan.okx_symbol(symbol))
        return _sf(ticker.get("last") or ticker.get("close") or ticker.get("bid") or ticker.get("ask"))
    except Exception:
        return None


def _ultra_new_signal(symbol: str, current_price: Any = None) -> Optional[Dict[str, Any]]:
    exchange = _EXCHANGE
    if exchange is None:
        return None
    try:
        raw = exchange.fetch_ohlcv(market_scan.okx_symbol(symbol), timeframe="1m", limit=180)
    except Exception:
        return None

    if not isinstance(raw, list) or len(raw) < ULTRA_MIN_1M:
        return None

    frame = pd.DataFrame(raw, columns=["time", "open", "high", "low", "close", "volume"])
    ind = _frame(frame, fast=9, slow=20)
    if ind is None or len(ind) < 5:
        return None

    row = ind.iloc[-2]
    prev = ind.iloc[-3]
    entry = _price(exchange, symbol, current_price)
    if entry is None or entry <= 0:
        return None

    close = _sf(row["close"], 0.0) or 0.0
    open_price = _sf(row["open"], 0.0) or 0.0
    ema_fast = _sf(row["ema_fast"], 0.0) or 0.0
    ema_slow = _sf(row["ema_slow"], 0.0) or 0.0
    slope = _sf(row["ema_slope"], 0.0) or 0.0
    rsi = _sf(row["rsi"], 50.0) or 50.0
    adx = _sf(row["adx"], 0.0) or 0.0
    volume_ratio = _sf(row["volume_ratio"], 0.0) or 0.0
    macd_hist = _sf(row["macd_hist"], 0.0) or 0.0
    prev_hist = _sf(prev["macd_hist"], 0.0) or 0.0
    cp = _close_power(row)

    recent = ind.iloc[max(0, len(ind) - 14):-2]
    if recent.empty:
        return None
    recent_high = float(recent["high"].max())
    recent_low = float(recent["low"].min())

    long_ok = (
        close > open_price and close > ema_fast > ema_slow and slope > 0
        and 52 <= rsi <= 72 and adx >= 18 and volume_ratio >= ULTRA_MIN_VOLUME_RATIO
        and macd_hist >= prev_hist and cp >= 62 and close >= recent_high * 0.997
    )
    short_ok = (
        close < open_price and close < ema_fast < ema_slow and slope < 0
        and 28 <= rsi <= 48 and adx >= 18 and volume_ratio >= ULTRA_MIN_VOLUME_RATIO
        and macd_hist <= prev_hist and cp <= 38 and close <= recent_low * 1.003
    )
    if long_ok == short_ok:
        return None
    direction = "LONG" if long_ok else "SHORT"

    atr = _sf(row["atr"], 0.0) or 0.0
    sl = min(recent_low, entry - 1.15 * atr) if direction == "LONG" else max(recent_high, entry + 1.15 * atr)
    plan = _targets(direction, entry, sl)
    if not plan:
        return None
    risk_percent = plan["risk_percent"]
    if not (ULTRA_MIN_RISK_PERCENT <= risk_percent <= ULTRA_MAX_RISK_PERCENT):
        return None

    score = 88
    if volume_ratio >= 2.0:
        score += 4
    elif volume_ratio >= 1.5:
        score += 2
    if adx >= 30:
        score += 4
    elif adx >= 22:
        score += 2
    if (direction == "LONG" and cp >= 75) or (direction == "SHORT" and cp <= 25):
        score += 3
    if abs(macd_hist) >= abs(prev_hist):
        score += 2
    score = min(100, score)
    if score < ULTRA_MIN_SCORE:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "source": "NEW_COIN_ENTRY",
        "signal_class": "TRADE",
        "entry": round(entry, 12),
        "ideal_entry": round(close, 12),
        "zone_distance_percent": round(abs(entry - close) / close * 100.0, 3) if close > 0 else 0.0,
        "zone_name": "1M momentum bölgesi",
        "sl": round(sl, 12),
        "tp1": round(plan["tp1"], 12),
        "tp2": round(plan["tp2"], 12),
        "tp3": round(plan["tp3"], 12),
        "risk_percent": round(risk_percent, 3),
        "rr_tp1": TP1_R,
        "rr_tp2": TP2_R,
        "rr_tp3": TP3_R,
        "score": score,
        "rsi_15m": round(rsi, 2),
        "adx_15m": round(adx, 2),
        "volume_ratio": round(volume_ratio, 2),
        "adx_4h": 0.0,
        "adx_1h": round(adx, 2),
        "quality": "A+ YENİ COİN",
        "quality_note": "Uzun geçmiş yok; 1M momentum + hacim + trend teyidi çok güçlü olduğu için kontrollü yeni-coin girişi.",
        "leverage": "1x",
        "trend_reason": "Yeni coin: uzun 4H geçmişi yok",
        "confirm_reason": f"1M trend/hacim teyidi | ADX {adx:.1f} | hacim {volume_ratio:.2f}x",
        "entry_reason": f"1M kapanış gücü %{cp:.0f} ve momentum teyidi",
        "radar_reason": "Yeni coinlerde düşük kaldıraç ve daha geniş stop zorunlu",
        "message": (
            "⚡ PREMIUM YENİ COİN FIRSATI\n\n"
            f"{'🟢 LONG' if direction == 'LONG' else '🔴 SHORT'}\n"
            f"🟡 Coin: {symbol}\n"
            "⏱️ Kaynak: NEW_COIN_ENTRY\n\n"
            f"📌 Giriş: {entry:.10g}\n"
            f"🎯 TP1: {plan['tp1']:.10g}\n"
            f"🎯 TP2: {plan['tp2']:.10g}\n"
            f"🎯 TP3: {plan['tp3']:.10g}\n"
            f"🛑 SL: {sl:.10g}\n\n"
            f"📊 Skor: {score}/100\n"
            f"📈 Hacim: {volume_ratio:.2f}x | ADX: {adx:.1f} | RSI: {rsi:.1f}\n"
            "⚠️ Yeni coin: oynaklık yüksektir. 1x ve küçük pozisyon önerilir.\n"
            "⚠️ Bot emir açmaz; grafikte kontrol etmeden işlem açma."
        ),
    }


def analyze_young_coin(symbol: str, df15m: Any, df1h: Any, df4h: Any, current_price: Any = None) -> Optional[Dict[str, Any]]:
    counts = {
        "15m": 0 if df15m is None else len(df15m),
        "1h": 0 if df1h is None else len(df1h),
        "4h": 0 if df4h is None else len(df4h),
    }

    if min(counts.values()) >= MATURE_MIN_CANDLES:
        return None

    if counts["15m"] < YOUNG_MIN_15M or counts["1h"] < YOUNG_MIN_1H:
        return _ultra_new_signal(symbol, current_price)

    f15 = _frame(df15m)
    f1 = _frame(df1h)
    f4 = _frame(df4h, fast=10, slow=20) if counts["4h"] >= 30 else None
    if f15 is None or f1 is None:
        return None

    r15 = f15.iloc[-2]
    r1 = f1.iloc[-2]

    def vals(row):
        return {
            "close": _sf(row["close"], 0.0) or 0.0,
            "open": _sf(row["open"], 0.0) or 0.0,
            "fast": _sf(row["ema_fast"], 0.0) or 0.0,
            "slow": _sf(row["ema_slow"], 0.0) or 0.0,
            "rsi": _sf(row["rsi"], 50.0) or 50.0,
            "adx": _sf(row["adx"], 0.0) or 0.0,
            "atr": _sf(row["atr"], 0.0) or 0.0,
            "vol": _sf(row["volume_ratio"], 0.0) or 0.0,
            "hist": _sf(row["macd_hist"], 0.0) or 0.0,
            "slope": _sf(row["ema_slope"], 0.0) or 0.0,
        }

    a15, a1 = vals(r15), vals(r1)
    cp = _close_power(r15)

    long1 = a1["close"] > a1["fast"] > a1["slow"] and a1["slope"] > 0 and a1["hist"] >= 0 and 48 <= a1["rsi"] <= 70 and a1["adx"] >= YOUNG_MIN_ADX
    short1 = a1["close"] < a1["fast"] < a1["slow"] and a1["slope"] < 0 and a1["hist"] <= 0 and 30 <= a1["rsi"] <= 52 and a1["adx"] >= YOUNG_MIN_ADX

    long15 = a15["close"] > a15["open"] and a15["close"] > a15["fast"] and a15["hist"] >= 0 and 45 <= a15["rsi"] <= 68 and a15["adx"] >= YOUNG_MIN_ADX and a15["vol"] >= YOUNG_MIN_VOLUME_RATIO and cp >= 58
    short15 = a15["close"] < a15["open"] and a15["close"] < a15["fast"] and a15["hist"] <= 0 and 32 <= a15["rsi"] <= 55 and a15["adx"] >= YOUNG_MIN_ADX and a15["vol"] >= YOUNG_MIN_VOLUME_RATIO and cp <= 42

    if long1 and long15:
        direction = "LONG"
    elif short1 and short15:
        direction = "SHORT"
    else:
        return None

    four_ok = None
    four_adx = 0.0
    if f4 is not None and len(f4) >= 5:
        a4 = vals(f4.iloc[-2])
        four_adx = a4["adx"]
        four_ok = a4["close"] > a4["fast"] > a4["slow"] and a4["slope"] > 0 if direction == "LONG" else a4["close"] < a4["fast"] < a4["slow"] and a4["slope"] < 0
        if not four_ok:
            return None
    elif a1["adx"] < 25 or a15["vol"] < 1.50:
        return None

    exchange = _EXCHANGE
    entry = _price(exchange, symbol, current_price) if exchange is not None else _sf(current_price)
    if entry is None or entry <= 0:
        return None

    zone_distance = abs(entry - a15["fast"]) / a15["fast"] * 100.0 if a15["fast"] > 0 else 999.0
    if zone_distance > YOUNG_MAX_ZONE_DISTANCE_PERCENT:
        return None

    recent = f15.iloc[max(0, len(f15) - 14):-2]
    if recent.empty:
        return None
    if direction == "LONG":
        swing = float(recent["low"].min())
        sl = min(swing, entry - 1.10 * a15["atr"])
    else:
        swing = float(recent["high"].max())
        sl = max(swing, entry + 1.10 * a15["atr"])

    plan = _targets(direction, entry, sl)
    if not plan:
        return None
    rp = plan["risk_percent"]
    if not (YOUNG_MIN_RISK_PERCENT <= rp <= YOUNG_MAX_RISK_PERCENT):
        return None

    score = 82
    if a1["adx"] >= 25: score += 4
    if a1["adx"] >= 35: score += 2
    if a15["adx"] >= 25: score += 3
    if a15["vol"] >= 1.5: score += 3
    if a15["vol"] >= 2.0: score += 2
    if zone_distance <= 0.25: score += 2
    if four_ok: score += 2
    if (direction == "LONG" and cp >= 70) or (direction == "SHORT" and cp <= 30): score += 2
    score = min(100, score)
    if score < YOUNG_MIN_SCORE:
        return None

    quality = "A+ GENÇ COİN" if score >= 98 else "A GENÇ COİN"
    return {
        "symbol": symbol,
        "direction": direction,
        "source": "YOUNG_COIN_ENTRY",
        "signal_class": "TRADE",
        "entry": round(entry, 12),
        "ideal_entry": round(a15["fast"], 12),
        "zone_distance_percent": round(zone_distance, 3),
        "zone_name": "15M adaptif EMA",
        "sl": round(sl, 12),
        "tp1": round(plan["tp1"], 12),
        "tp2": round(plan["tp2"], 12),
        "tp3": round(plan["tp3"], 12),
        "risk_percent": round(rp, 3),
        "rr_tp1": TP1_R,
        "rr_tp2": TP2_R,
        "rr_tp3": TP3_R,
        "score": score,
        "rsi_15m": round(a15["rsi"], 2),
        "adx_15m": round(a15["adx"], 2),
        "volume_ratio": round(a15["vol"], 2),
        "adx_4h": round(four_adx, 2),
        "adx_1h": round(a1["adx"], 2),
        "quality": quality,
        "quality_note": "EMA200 geçmişi tamamlanmamış coin; adaptif 1H/15M trend, hacim ve momentum filtreleriyle daha yüksek eşikte kabul edildi.",
        "leverage": "1x-2x",
        "trend_reason": "Genç coin adaptif trend" + (" + 4H uyumlu" if four_ok else ""),
        "confirm_reason": f"1H adaptif onay | ADX {a1['adx']:.1f}",
        "entry_reason": f"15M dönüş + hacim {a15['vol']:.2f}x + ADX {a15['adx']:.1f}",
        "radar_reason": "Uzun EMA200 geçmişi olmadığı için daha yüksek skor/hacim şartı",
        "message": (
            "🚀 PREMIUM GENÇ COİN FIRSATI\n\n"
            f"{'🟢 LONG' if direction == 'LONG' else '🔴 SHORT'}\n"
            f"🟡 Coin: {symbol}\n"
            "⏱️ Kaynak: YOUNG_COIN_ENTRY\n\n"
            f"📌 Giriş: {entry:.10g}\n"
            f"📍 Adaptif bölge: {a15['fast']:.10g}\n"
            f"🎯 TP1: {plan['tp1']:.10g}\n"
            f"🎯 TP2: {plan['tp2']:.10g}\n"
            f"🎯 TP3: {plan['tp3']:.10g}\n"
            f"🛑 SL: {sl:.10g}\n\n"
            f"📊 Skor: {score}/100 ({quality})\n"
            f"📈 Hacim: {a15['vol']:.2f}x | 15M ADX: {a15['adx']:.1f} | 1H ADX: {a1['adx']:.1f}\n"
            "⚠️ Genç coin: geçmiş veri olgun coinlerden azdır; pozisyon boyutu düşük tutulmalı.\n"
            "⚠️ Bot emir açmaz; grafikte kontrol etmeden işlem açma."
        ),
    }


def strong_direct_allowed(signal: Dict[str, Any], current_price: Any, base_validator, profit_module) -> bool:
    if str(signal.get("signal_class") or "").upper() != "TRADE":
        return False

    source = str(signal.get("source") or "").upper()
    score = int(_sf(signal.get("score"), 0) or 0)

    if source in {"YOUNG_COIN_ENTRY", "NEW_COIN_ENTRY"}:
        threshold = YOUNG_MIN_SCORE if source == "YOUNG_COIN_ENTRY" else ULTRA_MIN_SCORE
        if score < threshold:
            return False
        ok, _ = base_validator(signal, current_price)
        return bool(ok and profit_module.cost_viability(signal).get("ok"))

    if source != "15M_ENTRY" or score < 99:
        return False

    quality = str(signal.get("quality") or "").upper()
    if "A+" not in quality:
        return False

    volume_ratio = _sf(signal.get("volume_ratio"), 0.0) or 0.0
    adx15 = _sf(signal.get("adx_15m"), 0.0) or 0.0
    adx1 = _sf(signal.get("adx_1h"), 0.0) or 0.0
    adx4 = _sf(signal.get("adx_4h"), 0.0) or 0.0
    zone = abs(_sf(signal.get("zone_distance_percent"), 999.0) or 999.0)
    rsi = _sf(signal.get("rsi_15m"), 50.0) or 50.0
    direction = str(signal.get("direction") or "").upper()

    if volume_ratio < 1.50 or adx15 < 22 or adx1 < 25 or adx4 < 22 or zone > 0.25:
        return False
    if direction == "LONG" and not (45 <= rsi <= 66):
        return False
    if direction == "SHORT" and not (34 <= rsi <= 55):
        return False

    ok, _ = base_validator(signal, current_price)
    return bool(ok and profit_module.cost_viability(signal).get("ok"))
