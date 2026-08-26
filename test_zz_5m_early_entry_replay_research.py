from __future__ import annotations

import json
import math
import time
import warnings
from collections import defaultdict
from statistics import median

import ccxt
import pandas as pd

import strategy
from crypto_universe_guard import filter_crypto_markets


SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT")
EVAL_DAYS = 7
DUPLICATE_MS = 90 * 60 * 1000
TF_MS = {
    "5m": 5 * 60 * 1000,
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
}


def _frame(rows):
    df = pd.DataFrame(rows, columns=["time", "open", "high", "low", "close", "volume"])
    for col in ("time", "open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return (
        df.dropna()
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )


def _fetch(exchange, market_symbol: str, timeframe: str, since_ms: int, until_ms: int):
    rows = {}
    cursor = int(since_ms)
    loops = 0
    while cursor < until_ms and loops < 250:
        loops += 1
        batch = exchange.fetch_ohlcv(
            market_symbol,
            timeframe=timeframe,
            since=cursor,
            limit=300,
        )
        if not batch:
            break
        for row in batch:
            if not isinstance(row, (list, tuple)) or len(row) < 6:
                continue
            ts = int(row[0])
            if since_ms <= ts <= until_ms:
                rows[ts] = list(row[:6])
        nxt = int(batch[-1][0]) + TF_MS[timeframe]
        if nxt <= cursor:
            break
        cursor = nxt
        if len(batch) < 2:
            break
    return _frame(rows[key] for key in sorted(rows))


def _market_map(exchange):
    markets = exchange.load_markets()
    filtered, _ = filter_crypto_markets(markets)
    result = {}
    for market in filtered.values():
        if not isinstance(market, dict):
            continue
        if not market.get("swap") or market.get("active") is False:
            continue
        if str(market.get("quote") or "").upper() != "USDT":
            continue
        if str(market.get("settle") or "USDT").upper() != "USDT":
            continue
        base = str(market.get("base") or "").upper()
        symbol = str(market.get("symbol") or "")
        if base and symbol:
            result[f"{base}USDT"] = symbol
    return result


def _at(df: pd.DataFrame, ts: int, timeframe: str, keep: int = 280):
    duration = TF_MS[timeframe]
    closed = df[(df["time"] + duration) <= ts].tail(keep).copy()
    if len(closed) < 240:
        return None
    last_close = float(closed.iloc[-1]["close"])
    dummy = pd.DataFrame(
        [[ts, last_close, last_close, last_close, last_close, 0.0]],
        columns=["time", "open", "high", "low", "close", "volume"],
    )
    return pd.concat([closed, dummy], ignore_index=True)


def _outcome(signal: dict, df5: pd.DataFrame, ts: int):
    direction = str(signal.get("direction") or "").upper()
    entry = float(signal["entry"])
    sl = float(signal["sl"])
    tp1 = float(signal["tp1"])
    tp2 = float(signal["tp2"])
    tp3 = float(signal["tp3"])
    risk = abs(entry - sl)
    if risk <= 0:
        return {"result": "INVALID", "r": 0.0, "mfe_r": 0.0, "mae_r": 0.0}

    horizon = ts + 18 * 60 * 60 * 1000
    future = df5[(df5["time"] >= ts) & (df5["time"] < horizon)]
    stage = 0
    mfe_r = 0.0
    mae_r = 0.0

    for _, row in future.iterrows():
        high = float(row["high"])
        low = float(row["low"])
        if direction == "LONG":
            favorable = (high - entry) / risk
            adverse = (entry - low) / risk
            hard_stop = low <= sl
            be_stop = low <= entry
            hit1 = high >= tp1
            hit2 = high >= tp2
            hit3 = high >= tp3
        else:
            favorable = (entry - low) / risk
            adverse = (high - entry) / risk
            hard_stop = high >= sl
            be_stop = high >= entry
            hit1 = low <= tp1
            hit2 = low <= tp2
            hit3 = low <= tp3

        mfe_r = max(mfe_r, favorable)
        mae_r = max(mae_r, adverse)

        # Conservative intrabar ordering. Before TP1, stop wins a same-bar tie.
        if stage == 0:
            if hard_stop:
                return {"result": "SL_FIRST", "r": -1.0, "mfe_r": round(max(0.0, mfe_r), 3), "mae_r": round(max(0.0, mae_r), 3)}
            if hit3:
                return {"result": "TP3", "r": 1.60, "mfe_r": round(max(0.0, mfe_r), 3), "mae_r": round(max(0.0, mae_r), 3)}
            if hit2:
                stage = 2
            elif hit1:
                stage = 1
        elif stage == 1:
            # After TP1 bot rule moves residual stop to breakeven. If BE and a
            # higher target share a candle, assume BE first.
            if be_stop:
                return {"result": "TP1_BE", "r": 0.275, "mfe_r": round(max(0.0, mfe_r), 3), "mae_r": round(max(0.0, mae_r), 3)}
            if hit3:
                return {"result": "TP3", "r": 1.60, "mfe_r": round(max(0.0, mfe_r), 3), "mae_r": round(max(0.0, mae_r), 3)}
            if hit2:
                stage = 2
        else:
            if be_stop:
                return {"result": "TP2_BE", "r": 0.80, "mfe_r": round(max(0.0, mfe_r), 3), "mae_r": round(max(0.0, mae_r), 3)}
            if hit3:
                return {"result": "TP3", "r": 1.60, "mfe_r": round(max(0.0, mfe_r), 3), "mae_r": round(max(0.0, mae_r), 3)}

    if stage >= 2:
        result, r = "TP2_TIMEOUT", 0.80
    elif stage == 1:
        result, r = "TP1_TIMEOUT", 0.275
    else:
        result, r = "TIMEOUT", 0.0
    return {"result": result, "r": r, "mfe_r": round(max(0.0, mfe_r), 3), "mae_r": round(max(0.0, mae_r), 3)}


def _stats(events):
    if not events:
        return {"sample": 0}
    results = defaultdict(int)
    rs = []
    for event in events:
        results[event["outcome"]["result"]] += 1
        rs.append(float(event["outcome"]["r"]))
    wins = sum(value for key, value in results.items() if key != "SL_FIRST" and key != "TIMEOUT")
    stops = results["SL_FIRST"]
    tp3 = results["TP3"]
    gross_win = sum(r for r in rs if r > 0)
    gross_loss = abs(sum(r for r in rs if r < 0))
    return {
        "sample": len(events),
        "positive_rate_percent": round(wins / len(events) * 100.0, 2),
        "stop_rate_percent": round(stops / len(events) * 100.0, 2),
        "tp3_rate_percent": round(tp3 / len(events) * 100.0, 2),
        "net_r": round(sum(rs), 3),
        "avg_r": round(sum(rs) / len(rs), 4),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else 999.0,
        "results": dict(results),
    }


def test_existing_5m_early_trade_no_lookahead_replay():
    exchange = ccxt.okx({"enableRateLimit": True, "timeout": 30000})
    market_map = _market_map(exchange)
    now_ms = int(time.time() * 1000)
    eval_start = now_ms - EVAL_DAYS * 24 * 60 * 60 * 1000

    # Reproduce the path that config says should be active, even though the
    # Premium runtime currently forces it off.
    strategy.ENABLE_5M_EARLY_TRADE = True
    strategy.MAX_LATE_ENTRY_DISTANCE_PERCENT = 0.25

    early_events = []
    normal_events = []
    errors = []

    for symbol in SYMBOLS:
        market_symbol = market_map.get(symbol)
        if not market_symbol:
            errors.append(f"{symbol}:market_not_found")
            continue
        try:
            datasets = {
                "5m": _fetch(exchange, market_symbol, "5m", eval_start - 3 * 24 * 60 * 60 * 1000, now_ms),
                "15m": _fetch(exchange, market_symbol, "15m", eval_start - 5 * 24 * 60 * 60 * 1000, now_ms),
                "1h": _fetch(exchange, market_symbol, "1h", eval_start - 14 * 24 * 60 * 60 * 1000, now_ms),
                "4h": _fetch(exchange, market_symbol, "4h", eval_start - 45 * 24 * 60 * 60 * 1000, now_ms),
            }
            df5 = datasets["5m"].copy()
            if len(df5) < 500:
                errors.append(f"{symbol}:5m_short")
                continue
            df5["move_pct"] = (df5["close"] - df5["open"]) / df5["open"] * 100.0
            df5["vol_avg"] = df5["volume"].rolling(20).mean()
            df5["vol_ratio"] = df5["volume"] / df5["vol_avg"]

            last_early = {}
            last_normal = {}
            eval_rows = df5[(df5["time"] >= eval_start) & (df5["time"] <= now_ms)].reset_index(drop=True)

            for pos in range(1, len(eval_rows)):
                current = eval_rows.iloc[pos]
                ts = int(current["time"])
                prior = eval_rows.iloc[pos - 1]
                current_price = float(current["open"])

                frames = None

                # Existing 5M path can only pass these cheap preconditions.
                move = abs(float(prior.get("move_pct") or 0.0))
                vol = float(prior.get("vol_ratio") or 0.0)
                if math.isfinite(move) and math.isfinite(vol) and 0.10 <= move <= 1.35 and vol >= 1.15:
                    frames = {
                        tf: _at(datasets[tf], ts, tf)
                        for tf in ("5m", "15m", "1h", "4h")
                    }
                    if all(frame is not None for frame in frames.values()):
                        signal = strategy.analyze_5m_radar(
                            symbol,
                            frames["5m"],
                            frames["15m"],
                            frames["1h"],
                            frames["4h"],
                            current_price,
                        )
                        if isinstance(signal, dict) and signal.get("signal_class") == "TRADE":
                            key = str(signal.get("direction"))
                            if ts - int(last_early.get(key, 0)) >= DUPLICATE_MS:
                                last_early[key] = ts
                                early_events.append({
                                    "symbol": symbol,
                                    "ts": ts,
                                    "direction": key,
                                    "entry": float(signal["entry"]),
                                    "score": int(signal.get("score") or 0),
                                    "outcome": _outcome(signal, df5, ts),
                                })

                # Compare with the normal 15M path at each new 15M boundary.
                if ts % TF_MS["15m"] == 0:
                    if frames is None:
                        frames = {
                            tf: _at(datasets[tf], ts, tf)
                            for tf in ("5m", "15m", "1h", "4h")
                        }
                    if all(frame is not None for frame in frames.values()):
                        signal15 = strategy.analyze_mtf_trade(
                            symbol,
                            frames["15m"],
                            frames["1h"],
                            frames["4h"],
                            current_price,
                        )
                        if isinstance(signal15, dict) and signal15.get("signal_class") == "TRADE":
                            key = str(signal15.get("direction"))
                            if ts - int(last_normal.get(key, 0)) >= DUPLICATE_MS:
                                last_normal[key] = ts
                                normal_events.append({
                                    "symbol": symbol,
                                    "ts": ts,
                                    "direction": key,
                                    "entry": float(signal15["entry"]),
                                    "score": int(signal15.get("score") or 0),
                                    "outcome": _outcome(signal15, df5, ts),
                                })
        except Exception as exc:
            errors.append(f"{symbol}:{type(exc).__name__}:{exc}")

    pairs = []
    for normal in normal_events:
        candidates = [
            early for early in early_events
            if early["symbol"] == normal["symbol"]
            and early["direction"] == normal["direction"]
            and 0 < normal["ts"] - early["ts"] <= 4 * 60 * 60 * 1000
        ]
        if not candidates:
            continue
        early = max(candidates, key=lambda row: row["ts"])
        lead_min = (normal["ts"] - early["ts"]) / 60000.0
        if normal["direction"] == "LONG":
            advantage = (normal["entry"] - early["entry"]) / early["entry"] * 100.0
        else:
            advantage = (early["entry"] - normal["entry"]) / early["entry"] * 100.0
        pairs.append({"lead_min": lead_min, "price_advantage_percent": advantage})

    summary = {
        "symbols": list(SYMBOLS),
        "eval_days": EVAL_DAYS,
        "early_5m": _stats(early_events),
        "normal_15m": _stats(normal_events),
        "paired": {
            "sample": len(pairs),
            "median_lead_minutes": round(median([p["lead_min"] for p in pairs]), 2) if pairs else None,
            "median_price_advantage_percent": round(median([p["price_advantage_percent"] for p in pairs]), 4) if pairs else None,
            "positive_price_advantage_rate_percent": round(
                sum(p["price_advantage_percent"] > 0 for p in pairs) / len(pairs) * 100.0, 2
            ) if pairs else None,
        },
        "errors": errors,
    }

    warnings.warn("EARLY_REPLAY_RESULT=" + json.dumps(summary, separators=(",", ":")), RuntimeWarning)
    assert not errors, summary
    assert summary["early_5m"]["sample"] >= 5, summary
