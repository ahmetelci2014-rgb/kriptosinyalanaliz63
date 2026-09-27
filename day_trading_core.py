"""Day Trading Core V1.

A deliberately small intraday signal engine:
- 2H context = direction
- 15M setup = trend + pullback/reclaim
- 5M trigger = breakout + volume + momentum
- structural ATR/swing stop
- fixed-R targets and partial management
- max 2 simultaneous trades, max 1 per direction
- daily stop/net-R circuit breakers
- no exchange orders; Telegram signal only

This module keeps its own state/ledger so historical Market First data cannot
pollute the new day-trading measurements.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import os
import tempfile
import time
from typing import Any, Dict, Mapping, Optional, Tuple

import ccxt
import pandas as pd

from telegram_delivery import send_telegram_once

VERSION = "DAY_TRADING_CORE_V1_2026_09_27"
STATE_FILE = "day_trading_state.json"
LEDGER_FILE = "day_trading_ledger.json"
SUMMARY_FILE = "day_trading_summary.json"
DIAGNOSTICS_FILE = "day_trading_diagnostics.json"

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
TR_TZ = timezone(timedelta(hours=3))

# Universe / cadence
MAX_SCAN_COINS = 50
MIN_24H_QUOTE_VOLUME = 2_000_000.0
EXCLUDED_BASES = {"USDT", "USDC", "DAI", "FDUSD", "TUSD"}

# Signal limits / risk circuit breakers
MAX_OPEN_TRADES = 2
MAX_SAME_DIRECTION_OPEN = 1
MAX_NEW_SIGNALS_PER_DAY = 4
MAX_NEW_SIGNALS_PER_RUN = 2
MAX_FULL_STOPS_PER_DAY = 2
DAILY_NET_R_FLOOR = -1.50
SIGNAL_COOLDOWN_SECONDS = 120 * 60
MAX_HOLD_MINUTES = 360

# Setup thresholds
MIN_RISK_PERCENT = 0.45
MAX_RISK_PERCENT = 1.25
MAX_2H_EXTENSION_ATR = 2.20
MAX_15M_EXTENSION_ATR = 1.10
MAX_5M_EXTENSION_ATR = 0.85
MIN_5M_VOLUME_RATIO = 1.10
MIN_5M_BODY_ATR = 0.18
MAX_5M_BODY_ATR = 1.10
MAX_LIVE_DRIFT_R = 0.35

# R model
TP1_R = 1.20
TP2_R = 2.00
TP3_R = 3.00
TP1_SIZE = 0.35
TP2_SIZE = 0.35
TP3_SIZE = 0.30
POST_TP1_STOP_R = 0.10
POST_TP2_STOP_R = 1.00
ESTIMATED_COST_R = 0.15

MIN_REQUIRED_BARS = 70


def _now() -> int:
    return int(time.time())


def _today() -> str:
    return datetime.now(TR_TZ).strftime("%Y-%m-%d")


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _atomic_save(path: str, data: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(data), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return dict(default)
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else dict(default)
    except Exception:
        return dict(default)


def _default_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "last_signal": {},
        "open_trades": {},
        "days": {},
        "updated_at": 0,
    }


def _default_ledger() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "trades": {},
        "updated_at": 0,
    }


def _get_exchange() -> Any:
    return ccxt.okx(
        {
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )


def _is_usdt_swap(market: Mapping[str, Any]) -> bool:
    if market.get("active") is False:
        return False
    if not (market.get("swap") or market.get("contract")):
        return False
    if market.get("linear") is False:
        return False
    if str(market.get("quote") or "").upper() != "USDT":
        return False
    if str(market.get("settle") or "USDT").upper() != "USDT":
        return False
    if market.get("expiry") not in (None, "", 0):
        return False
    base = str(market.get("base") or "").upper()
    return bool(base and base not in EXCLUDED_BASES)


def _quote_volume(ticker: Mapping[str, Any]) -> float:
    direct = _sf(ticker.get("quoteVolume"))
    if direct > 0:
        return direct
    base = _sf(ticker.get("baseVolume"))
    last = _sf(ticker.get("last") or ticker.get("close"))
    return base * last if base > 0 and last > 0 else 0.0


def _load_universe(exchange: Any) -> list[Dict[str, Any]]:
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()
    best: Dict[str, Dict[str, Any]] = {}

    for market in markets.values():
        if not _is_usdt_swap(market):
            continue
        ccxt_symbol = str(market.get("symbol") or "")
        base = str(market.get("base") or "").upper()
        if not ccxt_symbol or not base:
            continue
        ticker = tickers.get(ccxt_symbol) or {}
        last = _sf(ticker.get("last") or ticker.get("close"))
        volume = _quote_volume(ticker)
        if last <= 0 or volume < MIN_24H_QUOTE_VOLUME:
            continue
        label = f"{base}USDT"
        row = {
            "symbol": label,
            "ccxt_symbol": ccxt_symbol,
            "last": last,
            "quote_volume": volume,
        }
        old = best.get(label)
        if old and _sf(old.get("quote_volume")) >= volume:
            continue
        best[label] = row

    return sorted(
        best.values(),
        key=lambda row: _sf(row.get("quote_volume")),
        reverse=True,
    )[:MAX_SCAN_COINS]


def _fetch_df(exchange: Any, ccxt_symbol: str, timeframe: str, limit: int = 120) -> pd.DataFrame:
    rows = exchange.fetch_ohlcv(ccxt_symbol, timeframe=timeframe, limit=limit)
    if len(rows) < MIN_REQUIRED_BARS:
        raise ValueError(f"{timeframe}: insufficient bars ({len(rows)})")
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().reset_index(drop=True)
    return _indicators(df)


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi.fillna(50.0)


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            (df["high"] - df["low"]).abs(),
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def _indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["ema9"] = out["close"].ewm(span=9, adjust=False).mean()
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()
    out["rsi14"] = _rsi(out["close"], 14)
    out["atr14"] = _atr(out, 14)
    previous_volume_mean = out["volume"].rolling(20).mean().shift(1)
    out["volume_ratio"] = out["volume"] / previous_volume_mean.replace(0, float("nan"))
    out["body_atr"] = (out["close"] - out["open"]).abs() / out["atr14"].replace(0, float("nan"))
    return out


def _completed(df: pd.DataFrame) -> pd.Series:
    # Last row can be the still-forming candle.
    return df.iloc[-2]


def _direction_2h(df: pd.DataFrame) -> Tuple[Optional[str], Dict[str, float]]:
    c = _completed(df)
    close = _sf(c["close"])
    ema20 = _sf(c["ema20"])
    ema50 = _sf(c["ema50"])
    atr = _sf(c["atr14"])
    rsi = _sf(c["rsi14"], 50.0)
    extension = abs(close - ema20) / atr if atr > 0 else 99.0

    metrics = {
        "rsi_2h": round(rsi, 2),
        "extension_atr_2h": round(extension, 3),
    }
    if extension > MAX_2H_EXTENSION_ATR:
        return None, metrics
    if close > ema20 > ema50 and 50.0 <= rsi <= 70.0:
        return "LONG", metrics
    if close < ema20 < ema50 and 30.0 <= rsi <= 50.0:
        return "SHORT", metrics
    return None, metrics


def _setup_15m(df: pd.DataFrame, direction: str) -> Tuple[bool, str, Dict[str, float]]:
    c = _completed(df)
    close = _sf(c["close"])
    ema20 = _sf(c["ema20"])
    ema50 = _sf(c["ema50"])
    atr = _sf(c["atr14"])
    rsi = _sf(c["rsi14"], 50.0)
    if atr <= 0:
        return False, "15M_ATR", {}

    extension = abs(close - ema20) / atr
    recent = df.iloc[-6:-2]
    if recent.empty:
        return False, "15M_HISTORY", {}

    metrics = {
        "rsi_15m": round(rsi, 2),
        "extension_atr_15m": round(extension, 3),
    }
    if extension > MAX_15M_EXTENSION_ATR:
        return False, "15M_LATE", metrics

    if direction == "LONG":
        trend_ok = close > ema20 > ema50 and 47.0 <= rsi <= 69.0
        pullback = _sf(recent["low"].min()) <= ema20 + 0.30 * atr
        reclaim = close > ema20
    else:
        trend_ok = close < ema20 < ema50 and 31.0 <= rsi <= 53.0
        pullback = _sf(recent["high"].max()) >= ema20 - 0.30 * atr
        reclaim = close < ema20

    if not trend_ok:
        return False, "15M_TREND", metrics
    if not pullback:
        return False, "15M_NO_PULLBACK", metrics
    if not reclaim:
        return False, "15M_NO_RECLAIM", metrics
    return True, "OK", metrics


def _trigger_5m(df: pd.DataFrame, direction: str) -> Tuple[bool, str, Dict[str, float]]:
    c = _completed(df)
    previous = df.iloc[-4:-2]
    close = _sf(c["close"])
    open_ = _sf(c["open"])
    ema9 = _sf(c["ema9"])
    ema20 = _sf(c["ema20"])
    atr = _sf(c["atr14"])
    rsi = _sf(c["rsi14"], 50.0)
    volume_ratio = _sf(c["volume_ratio"])
    body_atr = _sf(c["body_atr"])
    if atr <= 0 or previous.empty:
        return False, "5M_ATR", {}

    extension = abs(close - ema20) / atr
    metrics = {
        "rsi_5m": round(rsi, 2),
        "volume_ratio_5m": round(volume_ratio, 3),
        "body_atr_5m": round(body_atr, 3),
        "extension_atr_5m": round(extension, 3),
        "signal_bar_ts": int(_sf(c["ts"])),
    }

    if volume_ratio < MIN_5M_VOLUME_RATIO:
        return False, "5M_VOLUME", metrics
    if body_atr < MIN_5M_BODY_ATR:
        return False, "5M_WEAK_BODY", metrics
    if body_atr > MAX_5M_BODY_ATR:
        return False, "5M_SPIKE", metrics
    if extension > MAX_5M_EXTENSION_ATR:
        return False, "5M_LATE", metrics

    if direction == "LONG":
        momentum = close > open_ and close > ema9 > ema20 and 51.0 <= rsi <= 72.0
        breakout = close > _sf(previous["high"].max())
    else:
        momentum = close < open_ and close < ema9 < ema20 and 28.0 <= rsi <= 49.0
        breakout = close < _sf(previous["low"].min())

    if not momentum:
        return False, "5M_MOMENTUM", metrics
    if not breakout:
        return False, "5M_NO_BREAK", metrics
    return True, "OK", metrics


def _build_trade(
    symbol: str,
    ccxt_symbol: str,
    live_price: float,
    direction: str,
    df15: pd.DataFrame,
    df5: pd.DataFrame,
    metrics: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str]:
    c5 = _completed(df5)
    signal_close = _sf(c5["close"])
    atr15 = _sf(_completed(df15)["atr14"])
    if min(signal_close, live_price, atr15) <= 0:
        return None, "PRICE_DATA"

    recent15 = df15.iloc[-8:-2]
    if direction == "LONG":
        raw_stop = _sf(recent15["low"].min()) - 0.15 * atr15
        signal_risk = signal_close - raw_stop
    else:
        raw_stop = _sf(recent15["high"].max()) + 0.15 * atr15
        signal_risk = raw_stop - signal_close

    if signal_risk <= 0:
        return None, "STOP_STRUCTURE"

    drift_r = abs(live_price - signal_close) / signal_risk
    if drift_r > MAX_LIVE_DRIFT_R:
        return None, "LIVE_PRICE_DRIFT"

    entry = live_price
    if direction == "LONG":
        risk_abs = entry - raw_stop
    else:
        risk_abs = raw_stop - entry

    if risk_abs <= 0:
        return None, "STOP_INVALID"
    risk_percent = risk_abs / entry * 100.0
    if risk_percent < MIN_RISK_PERCENT:
        return None, "RISK_TOO_TIGHT"
    if risk_percent > MAX_RISK_PERCENT:
        return None, "RISK_TOO_WIDE"

    sign = 1.0 if direction == "LONG" else -1.0
    tp1 = entry + sign * risk_abs * TP1_R
    tp2 = entry + sign * risk_abs * TP2_R
    tp3 = entry + sign * risk_abs * TP3_R
    now = _now()
    trade_id = f"{symbol}_{direction}_{now}"

    trade = {
        "trade_id": trade_id,
        "version": VERSION,
        "symbol": symbol,
        "ccxt_symbol": ccxt_symbol,
        "direction": direction,
        "entry": round(entry, 12),
        "initial_stop": round(raw_stop, 12),
        "current_stop": round(raw_stop, 12),
        "risk_abs": round(risk_abs, 12),
        "risk_percent": round(risk_percent, 4),
        "tp1": round(tp1, 12),
        "tp2": round(tp2, 12),
        "tp3": round(tp3, 12),
        "tp1_r": TP1_R,
        "tp2_r": TP2_R,
        "tp3_r": TP3_R,
        "opened_at": now,
        "opened_day": _today(),
        "expires_at": now + MAX_HOLD_MINUTES * 60,
        "last_check_ms": int(now * 1000),
        "stage": 0,
        "remaining": 1.0,
        "realized_r": 0.0,
        "stop_r": -1.0,
        "status": "OPEN",
        "events": [{"at": now, "event": "OPEN", "price": round(entry, 12)}],
        "metrics": metrics,
    }
    return trade, "OK"


def _score(metrics: Mapping[str, Any], risk_percent: float) -> int:
    # Display-only score. Admission is determined by the hard day-trading rules.
    score = 70
    vol = _sf(metrics.get("volume_ratio_5m"))
    body = _sf(metrics.get("body_atr_5m"))
    ext2 = _sf(metrics.get("extension_atr_2h"), 99.0)
    ext5 = _sf(metrics.get("extension_atr_5m"), 99.0)
    if vol >= 1.5:
        score += 8
    elif vol >= 1.25:
        score += 5
    else:
        score += 3
    if 0.25 <= body <= 0.75:
        score += 6
    if ext2 <= 1.2:
        score += 5
    if ext5 <= 0.45:
        score += 5
    if 0.60 <= risk_percent <= 1.0:
        score += 6
    return min(score, 100)


def _format_price(value: float) -> str:
    if value >= 100:
        return f"{value:.3f}"
    if value >= 1:
        return f"{value:.5f}"
    if value >= 0.01:
        return f"{value:.6f}"
    return f"{value:.9f}"


def _trade_message(trade: Mapping[str, Any]) -> str:
    direction = str(trade["direction"])
    icon = "🟢" if direction == "LONG" else "🔴"
    metrics = trade.get("metrics") if isinstance(trade.get("metrics"), Mapping) else {}
    score = int(trade.get("score") or 0)
    return (
        f"⚡ DAY TRADE | {trade['symbol']}\n"
        f"{icon} {direction} | Kalite {score}/100\n\n"
        f"📍 Giriş: {_format_price(_sf(trade['entry']))}\n"
        f"🛑 Stop: {_format_price(_sf(trade['initial_stop']))} "
        f"(%{_sf(trade['risk_percent']):.2f})\n"
        f"🎯 TP1: {_format_price(_sf(trade['tp1']))} ({TP1_R:.1f}R)\n"
        f"🎯 TP2: {_format_price(_sf(trade['tp2']))} ({TP2_R:.1f}R)\n"
        f"🎯 TP3: {_format_price(_sf(trade['tp3']))} ({TP3_R:.1f}R)\n\n"
        f"🧭 2H yön + 15M pullback + 5M kırılım\n"
        f"🔊 5M hacim: {_sf(metrics.get('volume_ratio_5m')):.2f}x\n"
        f"⏳ Maks. takip: {MAX_HOLD_MINUTES // 60} saat\n"
        f"📌 TP1 sonrası stop +0.1R, TP2 sonrası +1R\n"
        f"⚠️ Otomatik emir açılmaz."
    )


def _send_trade(trade: Mapping[str, Any]) -> bool:
    text = _trade_message(trade)
    if str(os.getenv("GITHUB_REF_NAME") or "") != "main":
        print("DAY TRADING TEST | Telegram disabled:\n", text)
        return True
    return send_telegram_once(
        message=text,
        telegram_token=TOKEN,
        chat_id=CHAT_ID,
        bot_key="DAY_TRADING_CORE_V1",
        delivery_key=f"{trade['trade_id']}|OPEN",
    )


def _day_row(state: Dict[str, Any], day: Optional[str] = None) -> Dict[str, Any]:
    day = day or _today()
    days = state.setdefault("days", {})
    row = days.setdefault(
        day,
        {
            "signals": 0,
            "closed": 0,
            "full_stops": 0,
            "tp1": 0,
            "tp2": 0,
            "tp3": 0,
            "time_exit": 0,
            "gross_r": 0.0,
            "net_r": 0.0,
        },
    )
    return row


def _can_open(state: Dict[str, Any], direction: str) -> Tuple[bool, str]:
    open_trades = state.setdefault("open_trades", {})
    if len(open_trades) >= MAX_OPEN_TRADES:
        return False, "MAX_OPEN"
    same = sum(
        1
        for trade in open_trades.values()
        if isinstance(trade, Mapping) and trade.get("direction") == direction
    )
    if same >= MAX_SAME_DIRECTION_OPEN:
        return False, "MAX_SAME_DIRECTION"

    day = _day_row(state)
    if int(day.get("signals") or 0) >= MAX_NEW_SIGNALS_PER_DAY:
        return False, "DAILY_SIGNAL_LIMIT"
    if int(day.get("full_stops") or 0) >= MAX_FULL_STOPS_PER_DAY:
        return False, "DAILY_STOP_LIMIT"
    if _sf(day.get("net_r")) <= DAILY_NET_R_FLOOR:
        return False, "DAILY_NET_R_FLOOR"
    return True, "OK"


def _cooldown_ok(state: Mapping[str, Any], symbol: str, direction: str) -> bool:
    last_map = state.get("last_signal") if isinstance(state.get("last_signal"), Mapping) else {}
    last = int(_sf(last_map.get(f"{symbol}:{direction}")))
    return _now() - last >= SIGNAL_COOLDOWN_SECONDS


def _aligned_r(trade: Mapping[str, Any], price: float) -> float:
    entry = _sf(trade.get("entry"))
    risk = _sf(trade.get("risk_abs"))
    if risk <= 0:
        return 0.0
    raw = (price - entry) / risk
    return raw if trade.get("direction") == "LONG" else -raw


def _close_trade(
    state: Dict[str, Any],
    ledger: Dict[str, Any],
    trade: Dict[str, Any],
    gross_r: float,
    result: str,
    exit_price: float,
    at: int,
) -> None:
    net_r = gross_r - ESTIMATED_COST_R
    trade["status"] = "CLOSED"
    trade["final_result"] = result
    trade["gross_r"] = round(gross_r, 4)
    trade["estimated_cost_r"] = ESTIMATED_COST_R
    trade["net_r"] = round(net_r, 4)
    trade["exit_price"] = round(exit_price, 12)
    trade["closed_at"] = at
    trade["closed_day"] = _today()
    trade.setdefault("events", []).append(
        {"at": at, "event": result, "price": round(exit_price, 12)}
    )

    ledger.setdefault("trades", {})[trade["trade_id"]] = dict(trade)
    state.setdefault("open_trades", {}).pop(trade["trade_id"], None)

    day = _day_row(state)
    day["closed"] = int(day.get("closed") or 0) + 1
    day["gross_r"] = round(_sf(day.get("gross_r")) + gross_r, 4)
    day["net_r"] = round(_sf(day.get("net_r")) + net_r, 4)
    if result == "SL":
        day["full_stops"] = int(day.get("full_stops") or 0) + 1
    if result == "TP3":
        day["tp3"] = int(day.get("tp3") or 0) + 1
    if result == "TIME_EXIT":
        day["time_exit"] = int(day.get("time_exit") or 0) + 1


def _apply_bar(
    state: Dict[str, Any],
    ledger: Dict[str, Any],
    trade: Dict[str, Any],
    bar: Mapping[str, Any],
) -> bool:
    high = _sf(bar.get("high"))
    low = _sf(bar.get("low"))
    ts = int(_sf(bar.get("ts")) / 1000)
    direction = str(trade.get("direction"))
    stage = int(trade.get("stage") or 0)
    stop = _sf(trade.get("current_stop"))

    stop_hit = low <= stop if direction == "LONG" else high >= stop
    # Conservative same-candle ordering: active stop is checked before the next target.
    if stop_hit:
        gross = _sf(trade.get("realized_r")) + _sf(trade.get("remaining")) * _sf(trade.get("stop_r"), -1.0)
        result = "SL" if stage == 0 else f"PROTECTED_AFTER_TP{stage}"
        _close_trade(state, ledger, trade, gross, result, stop, ts)
        return True

    target_key = {0: "tp1", 1: "tp2", 2: "tp3"}.get(stage)
    if not target_key:
        return False
    target = _sf(trade.get(target_key))
    target_hit = high >= target if direction == "LONG" else low <= target
    if not target_hit:
        return False

    if stage == 0:
        trade["realized_r"] = round(_sf(trade.get("realized_r")) + TP1_SIZE * TP1_R, 4)
        trade["remaining"] = round(1.0 - TP1_SIZE, 4)
        trade["stage"] = 1
        trade["stop_r"] = POST_TP1_STOP_R
        sign = 1.0 if direction == "LONG" else -1.0
        trade["current_stop"] = round(_sf(trade["entry"]) + sign * _sf(trade["risk_abs"]) * POST_TP1_STOP_R, 12)
        day = _day_row(state)
        day["tp1"] = int(day.get("tp1") or 0) + 1
        event = "TP1"
    elif stage == 1:
        trade["realized_r"] = round(_sf(trade.get("realized_r")) + TP2_SIZE * TP2_R, 4)
        trade["remaining"] = round(1.0 - TP1_SIZE - TP2_SIZE, 4)
        trade["stage"] = 2
        trade["stop_r"] = POST_TP2_STOP_R
        sign = 1.0 if direction == "LONG" else -1.0
        trade["current_stop"] = round(_sf(trade["entry"]) + sign * _sf(trade["risk_abs"]) * POST_TP2_STOP_R, 12)
        day = _day_row(state)
        day["tp2"] = int(day.get("tp2") or 0) + 1
        event = "TP2"
    else:
        gross = _sf(trade.get("realized_r")) + TP3_SIZE * TP3_R
        _close_trade(state, ledger, trade, gross, "TP3", target, ts)
        return True

    trade.setdefault("events", []).append({"at": ts, "event": event, "price": round(target, 12)})
    return False


def _track_open(exchange: Any, state: Dict[str, Any], ledger: Dict[str, Any]) -> None:
    for trade_id, trade in list(state.setdefault("open_trades", {}).items()):
        if not isinstance(trade, dict):
            continue
        try:
            rows = exchange.fetch_ohlcv(str(trade["ccxt_symbol"]), timeframe="5m", limit=100)
            bars = [
                {"ts": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4]}
                for row in rows
                if int(row[0]) > int(trade.get("last_check_ms") or 0)
            ]
            closed = False
            for bar in bars:
                if _apply_bar(state, ledger, trade, bar):
                    closed = True
                    break
                trade["last_check_ms"] = int(bar["ts"])

            if closed:
                continue

            now = _now()
            if now >= int(trade.get("expires_at") or 0):
                last_price = _sf(rows[-1][4]) if rows else _sf(trade.get("entry"))
                current_r = _aligned_r(trade, last_price)
                gross = _sf(trade.get("realized_r")) + _sf(trade.get("remaining")) * current_r
                _close_trade(state, ledger, trade, gross, "TIME_EXIT", last_price, now)
            else:
                state["open_trades"][trade_id] = trade
                ledger.setdefault("trades", {})[trade_id] = dict(trade)
        except Exception as exc:
            print("Open trade tracking error:", trade_id, exc)


def _build_summary(ledger: Mapping[str, Any], state: Mapping[str, Any]) -> Dict[str, Any]:
    trades_map = ledger.get("trades") if isinstance(ledger.get("trades"), Mapping) else {}
    trades = [row for row in trades_map.values() if isinstance(row, Mapping) and row.get("status") == "CLOSED"]
    net_values = [_sf(row.get("net_r")) for row in trades]
    wins = sum(1 for value in net_values if value > 0)
    losses = sum(1 for value in net_values if value < 0)
    neutral = len(net_values) - wins - losses
    total_net = sum(net_values)
    return {
        "version": VERSION,
        "generated_at": _now(),
        "closed_trades": len(trades),
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "win_rate": round(wins / len(trades), 4) if trades else 0.0,
        "total_net_r": round(total_net, 4),
        "expectancy_net_r": round(total_net / len(trades), 4) if trades else 0.0,
        "open_trades": len(state.get("open_trades") or {}),
        "today": dict((state.get("days") or {}).get(_today()) or {}),
        "cost_model": {"estimated_cost_r_per_closed_trade": ESTIMATED_COST_R},
        "note": "Telegram/manual execution system; metrics are signal-model tracking, not guaranteed realised account PnL.",
    }


def run() -> None:
    state = _load_json(STATE_FILE, _default_state())
    ledger = _load_json(LEDGER_FILE, _default_ledger())
    state["version"] = VERSION
    ledger["version"] = VERSION

    exchange = _get_exchange()
    diagnostics = Counter()

    # First manage existing day trades; new risk is considered only afterwards.
    _track_open(exchange, state, ledger)

    can_long, long_reason = _can_open(state, "LONG")
    can_short, short_reason = _can_open(state, "SHORT")
    if not can_long:
        diagnostics[f"RISK_LONG:{long_reason}"] += 1
    if not can_short:
        diagnostics[f"RISK_SHORT:{short_reason}"] += 1

    universe = _load_universe(exchange)
    print(f"DAY TRADING CORE | scanning {len(universe)} liquid OKX USDT swaps")

    new_signals = 0
    for row in universe:
        if new_signals >= MAX_NEW_SIGNALS_PER_RUN:
            break
        symbol = str(row["symbol"])
        ccxt_symbol = str(row["ccxt_symbol"])
        live_price = _sf(row["last"])
        try:
            df2h = _fetch_df(exchange, ccxt_symbol, "2h")
            direction, metrics2h = _direction_2h(df2h)
            if direction is None:
                diagnostics["2H_NO_DIRECTION"] += 1
                continue

            allowed, risk_reason = _can_open(state, direction)
            if not allowed:
                diagnostics[f"RISK:{risk_reason}"] += 1
                continue
            if not _cooldown_ok(state, symbol, direction):
                diagnostics["COOLDOWN"] += 1
                continue

            df15 = _fetch_df(exchange, ccxt_symbol, "15m")
            ok15, reason15, metrics15 = _setup_15m(df15, direction)
            if not ok15:
                diagnostics[reason15] += 1
                continue

            df5 = _fetch_df(exchange, ccxt_symbol, "5m")
            ok5, reason5, metrics5 = _trigger_5m(df5, direction)
            if not ok5:
                diagnostics[reason5] += 1
                continue

            metrics = {**metrics2h, **metrics15, **metrics5}
            trade, trade_reason = _build_trade(
                symbol=symbol,
                ccxt_symbol=ccxt_symbol,
                live_price=live_price,
                direction=direction,
                df15=df15,
                df5=df5,
                metrics=metrics,
            )
            if trade is None:
                diagnostics[trade_reason] += 1
                continue

            trade["score"] = _score(metrics, _sf(trade["risk_percent"]))
            if not _send_trade(trade):
                diagnostics["TELEGRAM_SEND_FAILED"] += 1
                continue

            state.setdefault("open_trades", {})[trade["trade_id"]] = trade
            state.setdefault("last_signal", {})[f"{symbol}:{direction}"] = _now()
            day = _day_row(state)
            day["signals"] = int(day.get("signals") or 0) + 1
            ledger.setdefault("trades", {})[trade["trade_id"]] = dict(trade)
            new_signals += 1
            diagnostics["SIGNAL_SENT"] += 1
            print("DAY TRADE SENT:", symbol, direction, trade["score"], trade["risk_percent"])

        except Exception as exc:
            diagnostics["DATA_OR_ANALYSIS_ERROR"] += 1
            print("Day trade scan error:", symbol, exc)

    state["updated_at"] = _now()
    ledger["updated_at"] = _now()
    _atomic_save(STATE_FILE, state)
    _atomic_save(LEDGER_FILE, ledger)
    summary = _build_summary(ledger, state)
    _atomic_save(SUMMARY_FILE, summary)
    _atomic_save(
        DIAGNOSTICS_FILE,
        {
            "version": VERSION,
            "generated_at": _now(),
            "scanned": len(universe),
            "new_signals": new_signals,
            "rejections": dict(diagnostics.most_common()),
            "open_trades": len(state.get("open_trades") or {}),
            "today": dict(_day_row(state)),
        },
    )
    print("DAY TRADING SUMMARY:", summary)


if __name__ == "__main__":
    run()
