"""Genel Piyasa Yön Motoru V1.

- OKX crypto USDT perpetual breadth + BTC/ETH/SOL MTF yapısını ölçer.
- Funding/open-interest verisini teşhis olarak ekler.
- 30 dakikalık arka plan snapshotları tutar.
- Günde en fazla bir kez (Türkiye saati 09:00 sonrası ilk çalışmada) Telegram raporu gönderir.
- Günlük tahminini 6s/24s sonra ölçerek kendi doğruluk geçmişini oluşturur.
- Emir açmaz ve Premium işlem filtrelerini değiştirmez.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from crypto_universe_guard import is_crypto_market
from telegram_delivery import send_telegram_once

VERSION = "MARKET_OUTLOOK_V1_2026_08_23"
STATE_FILE = "market_outlook_state.json"
TR_TZ = timezone(timedelta(hours=3))
REFERENCE_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")
REFERENCE_WEIGHTS = {"BTCUSDT": 0.55, "ETHUSDT": 0.25, "SOLUSDT": 0.20}
REPORT_HOUR_TR = 9
SNAPSHOT_KEEP_DAYS = 14
FORECAST_KEEP_DAYS = 60
MIN_BREADTH_QUOTE_VOLUME = 200_000.0
FLAT_24H_PERCENT = 0.15
FUNDING_CROWDING = 0.0005
HIGH_ATR_4H_PERCENT = 3.0
FORECAST_6H_FLAT_PERCENT = 0.50
FORECAST_24H_FLAT_PERCENT = 1.00


def safe_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def normalize_symbol(symbol: Any) -> str:
    value = str(symbol or "").upper().strip()
    value = value.replace("/USDT:USDT", "USDT").replace(":USDT", "").replace("/", "")
    if value and not value.endswith("USDT"):
        value += "USDT"
    return value


def okx_symbol(bot_symbol: str) -> str:
    value = normalize_symbol(bot_symbol)
    return f"{value[:-4]}/USDT:USDT" if value.endswith("USDT") else value


def atomic_save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".market_outlook.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with open(tmp, "r", encoding="utf-8") as verify:
            if not isinstance(json.load(verify), dict):
                raise ValueError("Market outlook JSON doğrulaması başarısız")
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def empty_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": 0,
        "snapshots": [],
        "forecasts": [],
        "accuracy": {},
        "last_report_date": None,
        "last_report_at": 0,
        "last_regime": None,
    }


def load_state(path: str = STATE_FILE) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        state = data if isinstance(data, dict) else {}
    except Exception:
        state = {}
    base = empty_state()
    base.update(state)
    base["version"] = VERSION
    base["snapshots"] = base.get("snapshots") if isinstance(base.get("snapshots"), list) else []
    base["forecasts"] = base.get("forecasts") if isinstance(base.get("forecasts"), list) else []
    base["accuracy"] = base.get("accuracy") if isinstance(base.get("accuracy"), dict) else {}
    return base


def quote_volume(ticker: Dict[str, Any]) -> float:
    direct = safe_float(ticker.get("quoteVolume"), 0.0) or 0.0
    if direct > 0:
        return direct
    info = ticker.get("info") or {}
    last = safe_float(ticker.get("last") or info.get("last"), 0.0) or 0.0
    base_vol = safe_float(info.get("volCcy24h"), 0.0) or 0.0
    if base_vol > 0 and last > 0:
        return base_vol * last
    return safe_float(info.get("volUsd24h") or info.get("vol24h"), 0.0) or 0.0


def ticker_change24(ticker: Dict[str, Any]) -> Optional[float]:
    value = safe_float(ticker.get("percentage"))
    if value is not None:
        return value
    info = ticker.get("info") or {}
    last = safe_float(ticker.get("last") or info.get("last"))
    open24 = safe_float(ticker.get("open") or info.get("open24h"))
    if last and open24 and open24 > 0:
        return (last - open24) / open24 * 100.0
    return None


def eligible_crypto_markets(exchange: Any) -> List[str]:
    markets = exchange.load_markets()
    result: List[str] = []
    stable = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD"}
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        if not market.get("active", True) or not market.get("swap", False):
            continue
        if str(market.get("quote") or "").upper() != "USDT" or str(market.get("settle") or "").upper() != "USDT":
            continue
        if not is_crypto_market(market):
            continue
        base = str(market.get("base") or "").upper()
        symbol = str(market.get("symbol") or "")
        if not symbol or "/USDT:USDT" not in symbol or not base or base in stable:
            continue
        if symbol not in result:
            result.append(symbol)
    return result


def compute_breadth(exchange: Any) -> Dict[str, Any]:
    symbols = eligible_crypto_markets(exchange)
    tickers = exchange.fetch_tickers(symbols)
    rows: List[Tuple[float, float, str]] = []
    for symbol in symbols:
        ticker = tickers.get(symbol) or {}
        change = ticker_change24(ticker)
        volume = quote_volume(ticker)
        if change is None or volume < MIN_BREADTH_QUOTE_VOLUME:
            continue
        rows.append((change, volume, normalize_symbol(symbol)))
    if not rows:
        return {"eligible": 0, "up_pct": 0.0, "down_pct": 0.0, "flat_pct": 0.0, "median_change": 0.0, "volume_weighted_change": 0.0}
    up = sum(1 for c, _, _ in rows if c > FLAT_24H_PERCENT)
    down = sum(1 for c, _, _ in rows if c < -FLAT_24H_PERCENT)
    flat = len(rows) - up - down
    total_volume = sum(v for _, v, _ in rows)
    weighted = sum(c * v for c, v, _ in rows) / total_volume if total_volume else statistics.mean(c for c, _, _ in rows)
    top = sorted(rows, key=lambda row: row[0], reverse=True)[:5]
    bottom = sorted(rows, key=lambda row: row[0])[:5]
    return {
        "eligible": len(rows),
        "up_pct": round(up / len(rows) * 100.0, 2),
        "down_pct": round(down / len(rows) * 100.0, 2),
        "flat_pct": round(flat / len(rows) * 100.0, 2),
        "median_change": round(float(statistics.median(c for c, _, _ in rows)), 3),
        "volume_weighted_change": round(float(weighted), 3),
        "top": [{"symbol": s, "change": round(c, 2)} for c, _, s in top],
        "bottom": [{"symbol": s, "change": round(c, 2)} for c, _, s in bottom],
    }


def add_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    close, high, low = out["close"], out["high"], out["low"]
    out["ema20"] = close.ewm(span=20, adjust=False).mean()
    out["ema50"] = close.ewm(span=50, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    out["rsi14"] = (100 - 100 / (1 + rs)).fillna(50.0)
    prev = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    out["atr14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return out


def fetch_frame(exchange: Any, symbol: str, timeframe: str, limit: int = 220) -> pd.DataFrame:
    rows = exchange.fetch_ohlcv(okx_symbol(symbol), timeframe=timeframe, limit=limit)
    if not rows or len(rows) < 60:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    if len(frame) > 1:
        frame = frame.iloc[:-1].copy()
    for col in ("open", "high", "low", "close", "volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    return add_indicators(frame) if len(frame) >= 60 else pd.DataFrame()


def frame_trend_score(frame: pd.DataFrame) -> float:
    if frame.empty or len(frame) < 55:
        return 0.0
    row, old = frame.iloc[-1], frame.iloc[-4]
    score = 25.0 if row["close"] > row["ema20"] else -25.0
    score += 25.0 if row["ema20"] > row["ema50"] else -25.0
    score += 20.0 if row["ema20"] > old["ema20"] else -20.0
    rsi = float(row["rsi14"])
    score += 20.0 if rsi >= 55 else (-20.0 if rsi <= 45 else (rsi - 50.0) * 4.0)
    recent = frame.iloc[-5:]
    move = (recent.iloc[-1]["close"] - recent.iloc[0]["close"]) / recent.iloc[0]["close"] * 100.0
    score += max(-10.0, min(10.0, move * 4.0))
    return round(max(-100.0, min(100.0, score)), 2)


def analyze_reference(exchange: Any, symbol: str) -> Dict[str, Any]:
    frames = {tf: fetch_frame(exchange, symbol, tf, 220 if tf != "15m" else 180) for tf in ("15m", "1h", "4h", "1d")}
    if any(frame.empty for frame in frames.values()):
        raise RuntimeError(f"{symbol} MTF verisi eksik")
    scores = {tf: frame_trend_score(frame) for tf, frame in frames.items()}
    f4, fd = frames["4h"], frames["1d"]
    r4 = f4.iloc[-1]
    atr_pct = float(r4["atr14"] / r4["close"] * 100.0) if r4["close"] else 0.0
    return {
        "symbol": symbol,
        "price": round(float(frames["15m"].iloc[-1]["close"]), 10),
        "scores": scores,
        "rsi_4h": round(float(r4["rsi14"]), 2),
        "atr_4h_percent": round(atr_pct, 3),
        "levels": {
            "support1": round(float(f4.iloc[-6:]["low"].min()), 10),
            "support2": round(float(f4.iloc[-20:]["low"].min()), 10),
            "resistance1": round(float(f4.iloc[-6:]["high"].max()), 10),
            "resistance2": round(float(f4.iloc[-20:]["high"].max()), 10),
            "macro_support": round(float(fd.iloc[-20:]["low"].min()), 10),
            "macro_resistance": round(float(fd.iloc[-20:]["high"].max()), 10),
        },
    }


def fetch_derivatives(exchange: Any) -> Dict[str, Any]:
    funding: Dict[str, Optional[float]] = {}
    oi: Dict[str, Optional[float]] = {}
    for symbol in REFERENCE_SYMBOLS:
        try:
            funding[symbol] = safe_float((exchange.fetch_funding_rate(okx_symbol(symbol)) or {}).get("fundingRate"))
        except Exception:
            funding[symbol] = None
        try:
            item = exchange.fetch_open_interest(okx_symbol(symbol)) or {}
            oi[symbol] = safe_float(item.get("openInterestValue") or item.get("openInterestAmount"))
        except Exception:
            oi[symbol] = None
    valid = [value for value in funding.values() if value is not None]
    return {"funding": funding, "funding_average": round(statistics.mean(valid), 8) if valid else None, "open_interest": oi}


def oi_change(state: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Optional[float]]:
    previous = None
    for item in reversed(state.get("snapshots") or []):
        candidate = ((item or {}).get("derivatives") or {}).get("open_interest") if isinstance(item, dict) else None
        if isinstance(candidate, dict):
            previous = candidate
            break
    result: Dict[str, Optional[float]] = {}
    for symbol in REFERENCE_SYMBOLS:
        now_value = safe_float((current.get("open_interest") or {}).get(symbol))
        old_value = safe_float((previous or {}).get(symbol))
        result[symbol] = round((now_value - old_value) / old_value * 100.0, 3) if now_value and old_value and old_value > 0 else None
    return result


def breadth_score(breadth: Dict[str, Any]) -> float:
    up = safe_float(breadth.get("up_pct"), 0.0) or 0.0
    down = safe_float(breadth.get("down_pct"), 0.0) or 0.0
    median = safe_float(breadth.get("median_change"), 0.0) or 0.0
    weighted = safe_float(breadth.get("volume_weighted_change"), 0.0) or 0.0
    raw = (up - down) * 0.9 + max(-20.0, min(20.0, median * 8.0)) + max(-15.0, min(15.0, weighted * 4.0))
    return round(max(-100.0, min(100.0, raw)), 2)


def weighted_reference_score(refs: Dict[str, Dict[str, Any]], tf_weights: Dict[str, float]) -> float:
    total = weight_sum = 0.0
    for symbol, symbol_weight in REFERENCE_WEIGHTS.items():
        scores = (refs.get(symbol) or {}).get("scores") or {}
        for tf, tf_weight in tf_weights.items():
            weight = symbol_weight * tf_weight
            total += (safe_float(scores.get(tf), 0.0) or 0.0) * weight
            weight_sum += weight
    return total / weight_sum if weight_sum else 0.0


def classify(score: float) -> str:
    if score >= 45:
        return "GÜÇLÜ YUKARI"
    if score >= 20:
        return "YUKARI EĞİLİMLİ"
    if score <= -45:
        return "GÜÇLÜ AŞAĞI"
    if score <= -20:
        return "AŞAĞI EĞİLİMLİ"
    return "KARARSIZ / YATAY"


def direction(score: float) -> str:
    return "UP" if score >= 20 else ("DOWN" if score <= -20 else "FLAT")


def confidence(score: float, funding: Optional[float], atr_pct: float) -> int:
    value = 52.0 + abs(score) * 0.38
    if funding is not None and abs(funding) >= FUNDING_CROWDING:
        value -= 8.0
    if atr_pct >= HIGH_ATR_4H_PERCENT:
        value -= 7.0
    return int(round(max(45.0, min(90.0, value))))


def compute_outlook(refs: Dict[str, Dict[str, Any]], breadth: Dict[str, Any], derivatives: Dict[str, Any]) -> Dict[str, Any]:
    bscore = breadth_score(breadth)
    ref6 = weighted_reference_score(refs, {"15m": 0.15, "1h": 0.50, "4h": 0.35})
    ref24 = weighted_reference_score(refs, {"1h": 0.15, "4h": 0.50, "1d": 0.35})
    score6 = max(-100.0, min(100.0, ref6 * 0.78 + bscore * 0.22))
    score24 = max(-100.0, min(100.0, ref24 * 0.82 + bscore * 0.18))
    funding = safe_float(derivatives.get("funding_average"))
    btc_atr = safe_float((refs.get("BTCUSDT") or {}).get("atr_4h_percent"), 0.0) or 0.0
    flags: List[str] = []
    if funding is not None and funding >= FUNDING_CROWDING:
        flags.append("LONG funding kalabalık")
    elif funding is not None and funding <= -FUNDING_CROWDING:
        flags.append("SHORT funding kalabalık")
    if btc_atr >= HIGH_ATR_4H_PERCENT:
        flags.append("BTC 4H volatilitesi yüksek")
    up = safe_float(breadth.get("up_pct"), 0.0) or 0.0
    if score6 >= 20 and up < 45:
        flags.append("Majör yükselişi altcoin geneline tam yayılmıyor")
    if score6 <= -20 and up > 55:
        flags.append("Majör zayıflığa rağmen altcoin breadth dirençli")
    return {
        "score_6h": round(score6, 2), "score_24h": round(score24, 2),
        "bias_6h": classify(score6), "bias_24h": classify(score24),
        "direction_6h": direction(score6), "direction_24h": direction(score24),
        "confidence_6h": confidence(score6, funding, btc_atr),
        "confidence_24h": confidence(score24, funding, btc_atr),
        "breadth_score": bscore,
        "long_suitability": int(round(max(0.0, min(10.0, 5.0 + score6 / 20.0)))),
        "short_suitability": int(round(max(0.0, min(10.0, 5.0 - score6 / 20.0)))),
        "risk_flags": flags,
    }


def actual_direction(return_percent: float, threshold: float) -> str:
    return "UP" if return_percent > threshold else ("DOWN" if return_percent < -threshold else "FLAT")


def evaluate_forecasts(state: Dict[str, Any], btc_price: float, ts: int) -> None:
    for forecast in state.get("forecasts") or []:
        if not isinstance(forecast, dict):
            continue
        entry = safe_float(forecast.get("btc_entry"))
        created = int(forecast.get("created_at") or 0)
        if not entry or entry <= 0 or created <= 0:
            continue
        for horizon, hours, threshold in (("6h", 6, FORECAST_6H_FLAT_PERCENT), ("24h", 24, FORECAST_24H_FLAT_PERCENT)):
            key = f"outcome_{horizon}"
            if isinstance(forecast.get(key), dict) or ts < created + hours * 3600:
                continue
            ret = (btc_price - entry) / entry * 100.0
            actual = actual_direction(ret, threshold)
            expected = str(forecast.get(f"direction_{horizon}") or "FLAT")
            forecast[key] = {"evaluated_at": ts, "btc_price": round(btc_price, 10), "return_percent": round(ret, 3), "actual": actual, "expected": expected, "correct": actual == expected}


def update_accuracy(state: Dict[str, Any]) -> None:
    summary: Dict[str, Any] = {}
    for horizon in ("6h", "24h"):
        rows = [item.get(f"outcome_{horizon}") for item in state.get("forecasts") or [] if isinstance(item, dict) and isinstance(item.get(f"outcome_{horizon}"), dict)]
        correct = sum(1 for row in rows if row.get("correct"))
        summary[horizon] = {"sample": len(rows), "correct": correct, "accuracy_percent": round(correct / len(rows) * 100.0, 2) if rows else None}
    state["accuracy"] = summary


def fmt_price(value: Any) -> str:
    price = safe_float(value, 0.0) or 0.0
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 10:
        return f"{price:.2f}"
    if price >= 1:
        return f"{price:.4f}"
    return f"{price:.8f}".rstrip("0").rstrip(".")


def build_message(snapshot: Dict[str, Any], state: Dict[str, Any]) -> str:
    refs, outlook, breadth = snapshot["references"], snapshot["outlook"], snapshot["breadth"]
    btc, eth, sol = refs["BTCUSDT"], refs["ETHUSDT"], refs["SOLUSDT"]
    levels = btc["levels"]
    funding = safe_float(snapshot["derivatives"].get("funding_average"))
    funding_text = "veri yok" if funding is None else f"{funding * 100:.4f}%"
    flags = outlook.get("risk_flags") or []
    risk_text = "Yok" if not flags else " | ".join(flags[:3])
    acc24 = (state.get("accuracy") or {}).get("24h") or {}
    acc_text = f"%{acc24['accuracy_percent']:.1f} ({acc24['sample']} örnek)" if acc24.get("accuracy_percent") is not None and acc24.get("sample", 0) >= 5 else "veri birikiyor"
    return (
        "🌍 GENEL PİYASA DEĞERLENDİRMESİ\n\n"
        f"🧭 6 Saat: {outlook['bias_6h']} | Güven %{outlook['confidence_6h']}\n"
        f"🗓 24 Saat: {outlook['bias_24h']} | Güven %{outlook['confidence_24h']}\n\n"
        f"₿ BTC: {fmt_price(btc['price'])} | 4H RSI {btc['rsi_4h']:.1f}\n"
        f"Ξ ETH: {fmt_price(eth['price'])}\n"
        f"◎ SOL: {fmt_price(sol['price'])}\n\n"
        f"📊 Breadth: %{breadth['up_pct']:.1f} yükselen / %{breadth['down_pct']:.1f} düşen\n"
        f"📈 Medyan 24S değişim: %{breadth['median_change']:+.2f}\n"
        f"💸 Ortalama funding: {funding_text}\n\n"
        f"🟢 LONG uygunluğu: {outlook['long_suitability']}/10\n"
        f"🔴 SHORT uygunluğu: {outlook['short_suitability']}/10\n\n"
        f"🛡 BTC destek: {fmt_price(levels['support1'])} / {fmt_price(levels['support2'])}\n"
        f"🚧 BTC direnç: {fmt_price(levels['resistance1'])} / {fmt_price(levels['resistance2'])}\n"
        f"⚠️ Risk: {risk_text}\n"
        f"🧪 24S model geçmişi: {acc_text}\n\n"
        "📌 Kesin fiyat tahmini değildir; yön + seviye + senaryo değerlendirmesidir."
    )


def should_send_daily(state: Dict[str, Any], ts: int) -> bool:
    local = datetime.fromtimestamp(ts, TR_TZ)
    return local.hour >= REPORT_HOUR_TR and state.get("last_report_date") != local.strftime("%Y-%m-%d")


def create_forecast(snapshot: Dict[str, Any], ts: int) -> Dict[str, Any]:
    local = datetime.fromtimestamp(ts, TR_TZ)
    outlook = snapshot["outlook"]
    return {
        "id": f"DAILY_{local.strftime('%Y%m%d')}", "created_at": ts, "date_tr": local.strftime("%Y-%m-%d"),
        "btc_entry": snapshot["references"]["BTCUSDT"]["price"],
        "score_6h": outlook["score_6h"], "score_24h": outlook["score_24h"],
        "direction_6h": outlook["direction_6h"], "direction_24h": outlook["direction_24h"],
        "bias_6h": outlook["bias_6h"], "bias_24h": outlook["bias_24h"],
    }


def prune(state: Dict[str, Any], ts: int) -> None:
    state["snapshots"] = [row for row in state.get("snapshots") or [] if isinstance(row, dict) and int(row.get("ts") or 0) >= ts - SNAPSHOT_KEEP_DAYS * 86400]
    state["forecasts"] = [row for row in state.get("forecasts") or [] if isinstance(row, dict) and int(row.get("created_at") or 0) >= ts - FORECAST_KEEP_DAYS * 86400]


def run(exchange: Any, *, state_file: str = STATE_FILE, token: Optional[str] = None, chat_id: Optional[str] = None, current_ts: Optional[int] = None, allow_telegram: bool = True) -> Dict[str, Any]:
    ts = int(current_ts or time.time())
    state = load_state(state_file)
    breadth = compute_breadth(exchange)
    refs = {symbol: analyze_reference(exchange, symbol) for symbol in REFERENCE_SYMBOLS}
    derivatives = fetch_derivatives(exchange)
    derivatives["oi_change_since_last_run_percent"] = oi_change(state, derivatives)
    outlook = compute_outlook(refs, breadth, derivatives)
    snapshot = {
        "ts": ts, "time_tr": datetime.fromtimestamp(ts, TR_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "references": refs, "breadth": breadth, "derivatives": derivatives, "outlook": outlook,
    }
    state.setdefault("snapshots", []).append(snapshot)
    btc_price = safe_float(refs["BTCUSDT"].get("price"), 0.0) or 0.0
    if btc_price > 0:
        evaluate_forecasts(state, btc_price, ts)
    update_accuracy(state)
    sent = False
    if should_send_daily(state, ts):
        forecast = create_forecast(snapshot, ts)
        if not any((row or {}).get("id") == forecast["id"] for row in state.get("forecasts") or []):
            state.setdefault("forecasts", []).append(forecast)
        if allow_telegram and token and chat_id:
            sent = bool(send_telegram_once(message=build_message(snapshot, state), telegram_token=token, chat_id=chat_id, bot_key="MARKET_OUTLOOK", delivery_key=forecast["id"]))
        if sent:
            state["last_report_date"] = forecast["date_tr"]
            state["last_report_at"] = ts
    state["updated_at"] = ts
    state["last_regime"] = {"bias_6h": outlook["bias_6h"], "bias_24h": outlook["bias_24h"], "score_6h": outlook["score_6h"], "score_24h": outlook["score_24h"]}
    prune(state, ts)
    atomic_save(state_file, state)
    print("MARKET OUTLOOK:", outlook["bias_6h"], "/", outlook["bias_24h"], "| breadth up", breadth.get("up_pct"), "| daily_sent", sent)
    return {"snapshot": snapshot, "sent": sent, "accuracy": state.get("accuracy"), "last_report_date": state.get("last_report_date")}
