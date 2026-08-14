from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

try:
    import ccxt
except ImportError:
    ccxt = None

try:
    import pandas as pd
except ImportError:
    pd = None


VERSION = "SWING_SHADOW_V4_REGIME_PULLBACK_DIAGNOSTICS_2026_08_14"
LEDGER_FILE = "swing_shadow_v4_ledger.json"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS"

MAX_SCAN_COINS = 60
MIN_QUOTE_VOLUME_USDT = 2_000_000.0
MAX_NEW_PER_RUN = 2
MAX_OPEN_POSITIONS = 6
MAX_CLOSED_RECORDS = 500
MAX_HOLD_HOURS = 120
DUPLICATE_HOURS = 24
MAX_DIRECTION_SHARE = 0.70
MAX_NEAR_MISSES = 12

MIN_SCORE = 82
MIN_RISK_PERCENT = 0.80
MAX_RISK_PERCENT = 2.50
TP1_R = 0.80
TP2_R = 1.60
TP3_R = 2.50
ESTIMATED_COST_R = 0.08

TARGETS = {
    "stop_rate_max": 35.0,
    "tp3_rate_min": 25.0,
    "positive_close_rate_min": 65.0,
    "direction_share_max": 70.0,
    "minimum_closed": 30,
}


def now_ts() -> int:
    return int(time.time())


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return default if number != number else number
    except (TypeError, ValueError):
        return default


def empty_ledger() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "open_positions": {},
        "closed_positions": [],
        "latest_candidates": [],
        "latest_near_misses": [],
        "rejections": {},
        "summary": {},
        "last_update": 0,
        "last_cycle": {},
    }


def load_json(path: str) -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return empty_ledger()
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return empty_ledger()
        base = empty_ledger()
        base.update(data)
        base["version"] = VERSION
        base["mode"] = MODE
        return base
    except Exception as exc:
        print("Swing V4 ledger okuma hatasi:", exc)
        return empty_ledger()


def atomic_save(path: str, data: Dict[str, Any]) -> bool:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=".swing_v4.", suffix=".tmp", delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except Exception as exc:
        print("Swing V4 ledger kaydetme hatasi:", exc)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def create_exchange() -> Any:
    if ccxt is None:
        raise RuntimeError("ccxt kurulu degil")
    return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").replace("/", "").replace(":USDT", "").upper()


def to_okx_symbol(symbol: str) -> str:
    base = normalize_symbol(symbol)
    if base.endswith("USDT"):
        base = base[:-4]
    return f"{base}/USDT:USDT"


def safe_quote_volume(ticker: Dict[str, Any]) -> float:
    value = ticker.get("quoteVolume")
    if value is not None:
        return safe_float(value)
    info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
    for key in ("volCcy24h", "volUsd24h", "vol24h"):
        if info.get(key) is not None:
            return safe_float(info.get(key))
    return 0.0


def get_universe(exchange: Any) -> List[str]:
    markets = exchange.load_markets()
    okx_symbols = []
    for market in markets.values():
        if not (
            market.get("swap", False) and market.get("active", True)
            and market.get("quote") == "USDT" and market.get("settle") == "USDT"
        ):
            continue
        symbol = market.get("symbol")
        if symbol and "/USDT:USDT" in symbol:
            okx_symbols.append(symbol)
    tickers = exchange.fetch_tickers(okx_symbols)
    rows = []
    for symbol in okx_symbols:
        ticker = tickers.get(symbol) or {}
        volume = safe_quote_volume(ticker)
        if volume < MIN_QUOTE_VOLUME_USDT:
            continue
        rows.append((volume, normalize_symbol(symbol)))
    rows.sort(reverse=True)
    return [symbol for _, symbol in rows[:MAX_SCAN_COINS]]


def frame_from_rows(rows: List[List[float]]) -> Any:
    if pd is None or not rows:
        return None
    frame = pd.DataFrame(
        rows, columns=["timestamp", "open", "high", "low", "close", "volume"]
    )
    for name in ("open", "high", "low", "close", "volume"):
        frame[name] = pd.to_numeric(frame[name], errors="coerce")
    return frame.dropna().reset_index(drop=True)


def add_indicators(frame: Any) -> Any:
    if frame is None or len(frame) < 60:
        return None
    result = frame.copy()
    close = result["close"]
    result["ema20"] = close.ewm(span=20, adjust=False).mean()
    result["ema50"] = close.ewm(span=50, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    result["rsi"] = (100 - 100 / (1 + rs)).fillna(50.0)

    previous = close.shift(1)
    tr = pd.concat(
        [result["high"] - result["low"],
         (result["high"] - previous).abs(),
         (result["low"] - previous).abs()], axis=1,
    ).max(axis=1)
    result["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    up = result["high"].diff()
    down = -result["low"].diff()
    plus_dm = up.where((up > down) & (up > 0), 0.0)
    minus_dm = down.where((down > up) & (down > 0), 0.0)
    atr = result["atr"].replace(0, float("nan"))
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, float("nan"))
    result["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0.0)
    result["volume_ratio"] = result["volume"] / result["volume"].rolling(20).mean()
    return result.dropna().reset_index(drop=True)


def fetch_frame(exchange: Any, symbol: str, timeframe: str, limit: int = 220) -> Any:
    rows = exchange.fetch_ohlcv(to_okx_symbol(symbol), timeframe=timeframe, limit=limit)
    frame = frame_from_rows(rows)
    if frame is None or len(frame) < 70:
        return None
    return add_indicators(frame.iloc[:-1].copy())


def row_values(row: Any) -> Dict[str, float]:
    return {name: safe_float(row[name]) for name in (
        "timestamp", "open", "high", "low", "close", "volume",
        "ema20", "ema50", "rsi", "atr", "adx", "volume_ratio",
    )}


def body_strength(row: Dict[str, float]) -> float:
    width = max(1e-12, row["high"] - row["low"])
    return abs(row["close"] - row["open"]) / width


def evaluate_setup(
    symbol: str, d1: Any, h4: Any, h1: Any, m15: Any,
    diagnostic_sink: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    diagnostic_sink = diagnostic_sink if diagnostic_sink is not None else {}
    if any(frame is None or len(frame) < 3 for frame in (d1, h4, h1, m15)):
        diagnostic_sink.update({
            "symbol": symbol, "direction": "UNKNOWN",
            "first_failure": "DATA_MISSING", "passed_checks": 0,
            "total_checks": 9, "missing_checks": ["DATA_MISSING"],
        })
        return None, "DATA_MISSING"
    day = row_values(d1.iloc[-1])
    four = row_values(h4.iloc[-1])
    hour = row_values(h1.iloc[-1])
    hour_prev = row_values(h1.iloc[-2])
    trigger = row_values(m15.iloc[-1])

    long_regime = day["close"] > day["ema50"] and day["ema20"] > day["ema50"] and 48 <= day["rsi"] <= 70
    short_regime = day["close"] < day["ema50"] and day["ema20"] < day["ema50"] and 30 <= day["rsi"] <= 52
    if long_regime == short_regime:
        diagnostic_sink.update({
            "symbol": symbol, "direction": "NEUTRAL",
            "first_failure": "D1_REGIME_NEUTRAL", "passed_checks": 0,
            "total_checks": 9, "missing_checks": ["D1_REGIME_NEUTRAL"],
            "metrics": {"d1_rsi": round(day["rsi"], 2)},
        })
        return None, "D1_REGIME_NEUTRAL"
    direction = "LONG" if long_regime else "SHORT"

    if direction == "LONG":
        h4_ok = four["close"] > four["ema20"] > four["ema50"]
        pullback = min(hour["low"], hour_prev["low"]) <= hour["ema20"] * 1.006 and hour["close"] >= hour["ema20"]
        hour_rsi_ok = 45 <= hour["rsi"] <= 64
        trigger_ok = trigger["close"] > trigger["open"] and trigger["close"] > trigger["ema20"] and 47 <= trigger["rsi"] <= 67
    else:
        h4_ok = four["close"] < four["ema20"] < four["ema50"]
        pullback = max(hour["high"], hour_prev["high"]) >= hour["ema20"] * 0.994 and hour["close"] <= hour["ema20"]
        hour_rsi_ok = 36 <= hour["rsi"] <= 55
        trigger_ok = trigger["close"] < trigger["open"] and trigger["close"] < trigger["ema20"] and 33 <= trigger["rsi"] <= 53

    checks = {
        "H4_TREND": h4_ok,
        "H4_ADX": four["adx"] >= 17,
        "H4_VOLUME": four["volume_ratio"] >= 0.60,
        "H1_PULLBACK_RECLAIM": pullback,
        "H1_RSI": hour_rsi_ok,
        "H1_ADX": hour["adx"] >= 14,
        "M15_TRIGGER": trigger_ok,
        "M15_VOLUME": trigger["volume_ratio"] >= 0.70,
        "M15_BODY": body_strength(trigger) >= 0.35,
    }
    missing = [name for name, passed in checks.items() if not passed]
    setup_metrics = {
        "d1_rsi": round(day["rsi"], 2), "h4_adx": round(four["adx"], 2),
        "h1_adx": round(hour["adx"], 2), "h1_rsi": round(hour["rsi"], 2),
        "m15_rsi": round(trigger["rsi"], 2),
        "m15_volume_ratio": round(trigger["volume_ratio"], 3),
        "m15_body_strength": round(body_strength(trigger), 3),
    }
    diagnostic_sink.update({
        "symbol": symbol, "direction": direction,
        "first_failure": missing[0] if missing else None,
        "passed_checks": len(checks) - len(missing),
        "total_checks": len(checks), "missing_checks": missing,
        "metrics": setup_metrics,
    })
    if missing:
        return None, missing[0]

    entry = trigger["close"]
    atr_distance = max(hour["atr"] * 1.25, entry * MIN_RISK_PERCENT / 100)
    risk_percent = min(MAX_RISK_PERCENT, atr_distance / entry * 100)
    risk_distance = entry * risk_percent / 100
    if direction == "LONG":
        sl = entry - risk_distance
        tp1, tp2, tp3 = entry + TP1_R * risk_distance, entry + TP2_R * risk_distance, entry + TP3_R * risk_distance
    else:
        sl = entry + risk_distance
        tp1, tp2, tp3 = entry - TP1_R * risk_distance, entry - TP2_R * risk_distance, entry - TP3_R * risk_distance

    score = 75.0
    score += min(7.0, max(0.0, (four["adx"] - 17) * 0.45))
    score += min(5.0, max(0.0, (hour["adx"] - 14) * 0.35))
    score += min(4.0, max(0.0, (trigger["volume_ratio"] - 0.7) * 4))
    score += min(4.0, body_strength(trigger) * 5)
    score += 3.0 if risk_percent <= 1.8 else 1.0
    score = int(round(min(99.0, score)))
    diagnostic_sink["score"] = score
    if score < MIN_SCORE:
        diagnostic_sink["first_failure"] = "SCORE_LOW"
        diagnostic_sink["missing_checks"] = ["SCORE_LOW"]
        return None, "SCORE_LOW"

    return {
        "symbol": symbol, "direction": direction, "score": score,
        "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2, "tp3": tp3,
        "risk_percent": round(risk_percent, 4),
        "signal_candle_ms": int(trigger["timestamp"]),
        "setup": "D1_REGIME_4H_TREND_1H_PULLBACK_15M_TRIGGER",
        "diagnostics": setup_metrics,
    }, "PASS"


def rank_near_misses(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def rank(item: Dict[str, Any]) -> Tuple[float, int, float]:
        passed = int(item.get("passed_checks") or 0)
        total = max(1, int(item.get("total_checks") or 1))
        return passed / total, passed, safe_float(item.get("score"))

    return sorted(items, key=rank, reverse=True)[:MAX_NEAR_MISSES]


def direction_allowed(ledger: Dict[str, Any], direction: str) -> bool:
    records = list(ledger.get("closed_positions") or [])[-100:]
    records.extend((ledger.get("open_positions") or {}).values())
    if len(records) < 4:
        return True
    counts = Counter(str(item.get("direction") or "") for item in records)
    projected = counts[direction] + 1
    return projected / (len(records) + 1) <= MAX_DIRECTION_SHARE


def recent_duplicate(ledger: Dict[str, Any], symbol: str, direction: str, current_ts: int) -> bool:
    for item in (ledger.get("open_positions") or {}).values():
        if item.get("symbol") == symbol and item.get("direction") == direction:
            return True
    for item in reversed(ledger.get("closed_positions") or []):
        if item.get("symbol") != symbol or item.get("direction") != direction:
            continue
        return current_ts - int(item.get("opened_at") or 0) < DUPLICATE_HOURS * 3600
    return False


def build_position(candidate: Dict[str, Any], current_ts: Optional[int] = None) -> Dict[str, Any]:
    opened_at = int(current_ts or now_ts())
    item = dict(candidate)
    item.update({
        "id": f"{candidate['symbol']}_{candidate['direction']}_{opened_at}",
        "status": "OPEN", "opened_at": opened_at,
        "tp1_hit": False, "tp2_hit": False, "tp3_hit": False,
        "milestones": [], "best_favorable_r": 0.0, "worst_adverse_r": 0.0,
    })
    return item


def directional_r(position: Dict[str, Any], price: float) -> float:
    entry = safe_float(position.get("entry"))
    risk = abs(entry - safe_float(position.get("sl")))
    if risk <= 0:
        return 0.0
    raw = (price - entry) / risk
    return raw if position.get("direction") == "LONG" else -raw


def candle_hits(position: Dict[str, Any], candle: Dict[str, float], level: float) -> bool:
    return candle["high"] >= level if position.get("direction") == "LONG" else candle["low"] <= level


def stop_hits(position: Dict[str, Any], candle: Dict[str, float], level: float) -> bool:
    return candle["low"] <= level if position.get("direction") == "LONG" else candle["high"] >= level


def close_position(position: Dict[str, Any], result: str, gross_r: float, candle: Dict[str, float]) -> Dict[str, Any]:
    position["status"] = "CLOSED"
    position["final_result"] = result
    position["gross_r"] = round(gross_r, 4)
    position["net_r"] = round(gross_r - ESTIMATED_COST_R, 4)
    position["closed_at"] = int(candle["timestamp"] / 1000)
    position["exit_price"] = candle["close"]
    return position


def simulate_position(position: Dict[str, Any], candles: List[Dict[str, float]], current_ts: Optional[int] = None) -> Dict[str, Any]:
    for candle in candles:
        if int(candle["timestamp"]) <= int(position.get("signal_candle_ms") or 0):
            continue
        best_price = candle["high"] if position["direction"] == "LONG" else candle["low"]
        worst_price = candle["low"] if position["direction"] == "LONG" else candle["high"]
        position["best_favorable_r"] = round(max(safe_float(position.get("best_favorable_r")), directional_r(position, best_price)), 4)
        position["worst_adverse_r"] = round(max(safe_float(position.get("worst_adverse_r")), -directional_r(position, worst_price)), 4)

        active_stop = position["entry"] if position.get("tp1_hit") else position["sl"]
        if stop_hits(position, candle, active_stop):
            if position.get("tp2_hit"):
                return close_position(position, "TP2_SONRASI_BE", 0.80, candle)
            if position.get("tp1_hit"):
                return close_position(position, "TP1_SONRASI_BE", 0.40, candle)
            return close_position(position, "SL", -1.0, candle)

        if not position.get("tp1_hit") and candle_hits(position, candle, position["tp1"]):
            position["tp1_hit"] = True
            position["milestones"].append({"result": "TP1", "time": int(candle["timestamp"] / 1000)})
        if position.get("tp1_hit") and not position.get("tp2_hit") and candle_hits(position, candle, position["tp2"]):
            position["tp2_hit"] = True
            position["milestones"].append({"result": "TP2", "time": int(candle["timestamp"] / 1000)})
        if position.get("tp2_hit") and candle_hits(position, candle, position["tp3"]):
            position["tp3_hit"] = True
            position["milestones"].append({"result": "TP3", "time": int(candle["timestamp"] / 1000)})
            return close_position(position, "TP3", 1.425, candle)

    current_ts = int(current_ts or now_ts())
    if current_ts - int(position.get("opened_at") or current_ts) >= MAX_HOLD_HOURS * 3600 and candles:
        value = directional_r(position, candles[-1]["close"])
        return close_position(position, "EXPIRED", value, candles[-1])
    return position


def fetch_tracking_candles(exchange: Any, position: Dict[str, Any]) -> List[Dict[str, float]]:
    rows = exchange.fetch_ohlcv(
        to_okx_symbol(position["symbol"]), timeframe="15m",
        since=max(0, int(position.get("opened_at") or 0) - 900) * 1000,
        limit=500,
    )
    return [
        {"timestamp": int(r[0]), "open": float(r[1]), "high": float(r[2]),
         "low": float(r[3]), "close": float(r[4])}
        for r in (rows or [])[:-1]
    ]


def update_open_positions(exchange: Any, ledger: Dict[str, Any]) -> int:
    opened = ledger.get("open_positions") or {}
    closed = ledger.get("closed_positions") or []
    resolved = 0
    for key, position in list(opened.items()):
        try:
            result = simulate_position(position, fetch_tracking_candles(exchange, position))
            if result.get("status") == "CLOSED":
                closed.append(result)
                opened.pop(key, None)
                resolved += 1
            else:
                opened[key] = result
        except Exception as exc:
            print(position.get("symbol"), "Swing V4 takip hatasi:", exc)
    ledger["open_positions"] = opened
    ledger["closed_positions"] = closed[-MAX_CLOSED_RECORDS:]
    return resolved


def calculate_summary(ledger: Dict[str, Any]) -> Dict[str, Any]:
    closed = ledger.get("closed_positions") or []
    outcomes = Counter(str(item.get("final_result") or "") for item in closed)
    directions = Counter(str(item.get("direction") or "") for item in closed)
    total = len(closed)
    positive = sum(safe_float(item.get("net_r")) > 0 for item in closed)
    stop_rate = round(outcomes["SL"] * 100 / total, 2) if total else 0.0
    tp3_rate = round(outcomes["TP3"] * 100 / total, 2) if total else 0.0
    positive_rate = round(positive * 100 / total, 2) if total else 0.0
    max_share = round(max(directions.values(), default=0) * 100 / total, 2) if total else 0.0
    ready = total >= TARGETS["minimum_closed"]
    gates = {
        "minimum_sample": ready,
        "stop_rate": ready and stop_rate <= TARGETS["stop_rate_max"],
        "tp3_rate": ready and tp3_rate >= TARGETS["tp3_rate_min"],
        "positive_close_rate": ready and positive_rate >= TARGETS["positive_close_rate_min"],
        "direction_balance": ready and max_share <= TARGETS["direction_share_max"],
    }
    return {
        "closed": total, "open": len(ledger.get("open_positions") or {}),
        "outcomes": dict(outcomes), "directions": dict(directions),
        "stop_rate_percent": stop_rate, "tp3_rate_percent": tp3_rate,
        "positive_close_rate_percent": positive_rate,
        "max_direction_share_percent": max_share,
        "net_r_after_costs": round(sum(safe_float(item.get("net_r")) for item in closed), 4),
        "targets": TARGETS, "gates": gates,
        "live_candidate": ready and all(gates.values()),
    }


def run_cycle(filename: str = LEDGER_FILE) -> Dict[str, Any]:
    ledger = load_json(filename)
    exchange = create_exchange()
    current_ts = now_ts()
    resolved = update_open_positions(exchange, ledger)
    universe = get_universe(exchange)
    if not universe:
        raise RuntimeError("Swing V4 tarama evreni bos; OKX sembol/hacim verisi alinamadi")
    candidates: List[Dict[str, Any]] = []
    near_misses: List[Dict[str, Any]] = []
    rejections = Counter()
    scanned = 0

    for symbol in universe:
        try:
            scanned += 1
            d1 = fetch_frame(exchange, symbol, "1d")
            h4 = fetch_frame(exchange, symbol, "4h")
            if d1 is None or h4 is None:
                rejections["DATA_MISSING"] += 1
                continue
            day = row_values(d1.iloc[-1])
            four = row_values(h4.iloc[-1])
            rough_long = day["close"] > day["ema50"] and day["ema20"] > day["ema50"] and four["close"] > four["ema50"]
            rough_short = day["close"] < day["ema50"] and day["ema20"] < day["ema50"] and four["close"] < four["ema50"]
            if not (rough_long or rough_short):
                rejections["REGIME_PREFILTER"] += 1
                continue
            h1 = fetch_frame(exchange, symbol, "1h")
            m15 = fetch_frame(exchange, symbol, "15m")
            setup_diagnostic: Dict[str, Any] = {}
            candidate, reason = evaluate_setup(
                symbol, d1, h4, h1, m15, diagnostic_sink=setup_diagnostic,
            )
            if candidate is None:
                rejections[reason] += 1
                near_misses.append(setup_diagnostic)
                continue
            if recent_duplicate(ledger, symbol, candidate["direction"], current_ts):
                rejections["DUPLICATE"] += 1
                setup_diagnostic["first_failure"] = "DUPLICATE"
                setup_diagnostic["missing_checks"] = ["DUPLICATE"]
                near_misses.append(setup_diagnostic)
                continue
            if not direction_allowed(ledger, candidate["direction"]):
                rejections["DIRECTION_BALANCE_GATE"] += 1
                setup_diagnostic["first_failure"] = "DIRECTION_BALANCE_GATE"
                setup_diagnostic["missing_checks"] = ["DIRECTION_BALANCE_GATE"]
                near_misses.append(setup_diagnostic)
                continue
            candidates.append(candidate)
        except Exception as exc:
            rejections[f"ERROR_{type(exc).__name__}"] += 1

    candidates.sort(key=lambda item: item["score"], reverse=True)
    open_positions = ledger.get("open_positions") or {}
    available = max(0, MAX_OPEN_POSITIONS - len(open_positions))
    selected = candidates[:min(MAX_NEW_PER_RUN, available)]
    for candidate in selected:
        position = build_position(candidate, current_ts)
        open_positions[position["id"]] = position
    ledger["open_positions"] = open_positions
    ledger["latest_candidates"] = candidates[:20]
    ledger["latest_near_misses"] = rank_near_misses(near_misses)
    ledger["rejections"] = dict(rejections)
    ledger["summary"] = calculate_summary(ledger)
    ledger["last_update"] = current_ts
    ledger["last_cycle"] = {
        "scanned": scanned, "qualified": len(candidates), "opened": len(selected),
        "resolved": resolved, "universe": len(universe),
        "near_misses": len(near_misses),
    }
    if not atomic_save(filename, ledger):
        raise RuntimeError("Swing V4 ledger kaydedilemedi")
    print("Swing Shadow V4 tamamlandi | taranan:", scanned, "| aday:", len(candidates), "| yeni:", len(selected))
    print("Telegram: KAPALI | Emir: KAPALI | Canli kural: DEGISMEDI")
    return ledger


if __name__ == "__main__":
    try:
        run_cycle()
    except Exception as exc:
        print("Swing Shadow V4 genel hata:", exc)
        raise
