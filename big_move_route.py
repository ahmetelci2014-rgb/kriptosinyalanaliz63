#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Büyük Hareket / Fiyat Rotası V1.

Amaç:
- OKX'teki bütün aktif USDT perpetual swap piyasalarını her tur keşfetmek.
- Düşük likiditelileri sinyal vermeden elemek.
- 1D + 4H ana trend, 1H pullback/retest, 15M giriş teyidi ve yapısal stop ile
  yalnız büyük hareket potansiyeli olan kurulumları seçmek.
- Telegram'a yalnız giriş ONAYLANDI sinyali ve sonradan TP/SL sonuçları göndermek.
- Gerçek emir AÇMAMAK; performansı ayrı state/ledger ile ölçmek.

Bu sistem Premium/Scalp/Pump canlı kurallarını değiştirmez.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

import position_trend_shadow as core
from telegram_delivery import send_telegram_once

VERSION = "BIG_MOVE_PRICE_ROUTE_V1_2026_08_18"
MODE = "TELEGRAM_SIGNAL_NO_ORDERS_WITH_VIRTUAL_OUTCOME_TRACKING"
BOT_KEY = "BIG_MOVE_ROUTE"

STATE_FILE = "big_move_route_state.json"
LEDGER_FILE = "big_move_route_ledger.json"

# Bütün aktif USDT swap coinler keşfedilir. Bu sınır sadece SİNYAL güvenliği içindir.
MIN_LIQUIDITY_NOTIONAL_USDT = 2_000_000.0
MIN_SCORE = 92
MIN_MAIN_ROUTE_R = 3.0
MIN_POTENTIAL_PERCENT = 2.0
MIN_RISK_PERCENT = 1.0
MAX_RISK_PERCENT = 3.0
MAX_ENTRY_DRIFT_PERCENT = 0.30
MAX_NEW_SIGNALS_PER_RUN = 2
MAX_OPEN_ROUTES = 6
SAME_DIRECTION_COOLDOWN_HOURS = 72
MAX_HOLD_HOURS = 168

M15_LIMIT = 220
H4_ROUTE_LOOKBACK = 96

TP1_FRACTION = 0.25
TP2_FRACTION = 0.25
RUNNER_FRACTION = 0.50
AFTER_TP1_STOP_R = -0.25
AFTER_TP2_STOP_R = 0.50


def now_ts() -> int:
    return int(time.time())


def safe_float(value: Any, default: float = 0.0) -> float:
    return core.safe_float(value, default)


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return json.loads(json.dumps(default))
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else json.loads(json.dumps(default))
    except Exception:
        return json.loads(json.dumps(default))


def empty_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "open_routes": {},
        "last_signal_by_symbol_direction": {},
        "last_run": None,
        "universe": {},
        "run_stats": {},
    }


def empty_ledger() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "closed_routes": [],
        "summary": {},
        "last_update": None,
    }


def load_state() -> Dict[str, Any]:
    data = load_json(STATE_FILE, empty_state())
    data["version"] = VERSION
    data["mode"] = MODE
    data.setdefault("open_routes", {})
    data.setdefault("last_signal_by_symbol_direction", {})
    data.setdefault("universe", {})
    data.setdefault("run_stats", {})
    return data


def load_ledger() -> Dict[str, Any]:
    data = load_json(LEDGER_FILE, empty_ledger())
    data["version"] = VERSION
    data["mode"] = MODE
    data.setdefault("closed_routes", [])
    data.setdefault("summary", {})
    return data


def body_strength(row: pd.Series) -> float:
    span = max(1e-12, safe_float(row.get("high")) - safe_float(row.get("low")))
    return abs(safe_float(row.get("close")) - safe_float(row.get("open"))) / span


def build_full_universe(exchange: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    okx_symbols, bot_to_okx = core.eligible_markets(exchange)
    tickers = exchange.fetch_tickers(okx_symbols)
    rows: List[Dict[str, Any]] = []

    for bot, okx_symbol in bot_to_okx.items():
        ticker = tickers.get(okx_symbol, {}) or {}
        notional = core.corrected_quote_notional_24h(ticker)
        last = safe_float(ticker.get("last") or (ticker.get("info") or {}).get("last"))
        rows.append({
            "symbol": bot,
            "okx_symbol": okx_symbol,
            "notional_usdt": notional,
            "last": last,
        })

    rows.sort(key=lambda row: safe_float(row.get("notional_usdt")), reverse=True)
    metadata = {
        "eligible_total": len(rows),
        "liquid_for_deep_scan": sum(
            1 for row in rows
            if safe_float(row.get("notional_usdt")) >= MIN_LIQUIDITY_NOTIONAL_USDT
        ),
        "low_liquidity_seen_but_not_signaled": sum(
            1 for row in rows
            if safe_float(row.get("notional_usdt")) < MIN_LIQUIDITY_NOTIONAL_USDT
        ),
        "min_signal_liquidity_usdt": MIN_LIQUIDITY_NOTIONAL_USDT,
        "top10": [row["symbol"] for row in rows[:10]],
    }
    return rows, metadata


def confirm_15m(frame: pd.DataFrame, direction: str) -> Tuple[bool, Dict[str, Any]]:
    if frame is None or frame.empty or len(frame) < 60:
        return False, {"reason": "NO_15M_HISTORY"}

    row = frame.iloc[-1]
    close = safe_float(row.get("close"))
    open_ = safe_float(row.get("open"))
    ema20 = safe_float(row.get("ema20"))
    atr = max(1e-12, safe_float(row.get("atr14")))
    rsi = safe_float(row.get("rsi14"))
    adx = safe_float(row.get("adx14"))
    volume_ratio = safe_float(row.get("volume_ratio"))
    strength = body_strength(row)
    ema_distance_atr = abs(close - ema20) / atr

    if direction == "LONG":
        candle_ok = close > open_ and close > ema20
        rsi_ok = 48 <= rsi <= 69
    else:
        candle_ok = close < open_ and close < ema20
        rsi_ok = 31 <= rsi <= 52

    checks = {
        "candle_direction": candle_ok,
        "rsi": rsi_ok,
        "adx": adx >= 16.0,
        "volume": volume_ratio >= 0.80,
        "body": strength >= 0.30,
        "not_overextended": ema_distance_atr <= 0.90,
    }
    ok = all(checks.values())
    return ok, {
        "reason": "OK" if ok else next((k for k, v in checks.items() if not v), "15M_FAIL"),
        "checks": checks,
        "close": close,
        "atr": atr,
        "rsi": round(rsi, 2),
        "adx": round(adx, 2),
        "volume_ratio": round(volume_ratio, 3),
        "body_strength": round(strength, 3),
        "ema_distance_atr": round(ema_distance_atr, 3),
        "candle_ts": int(safe_float(row.get("ts"))),
    }


def _dedupe_levels(levels: Sequence[float], tolerance: float) -> List[float]:
    result: List[float] = []
    for level in sorted(float(x) for x in levels if safe_float(x) > 0):
        if not result or abs(level - result[-1]) > tolerance:
            result.append(level)
    return result


def h4_swing_levels(h4: pd.DataFrame, direction: str, entry: float) -> Tuple[List[float], float]:
    if h4 is None or h4.empty or len(h4) < 10:
        return [], 0.0

    frame = h4.tail(H4_ROUTE_LOOKBACK).reset_index(drop=True)
    atr = max(1e-12, safe_float(frame.iloc[-1].get("atr14")))
    levels: List[float] = []

    for index in range(2, len(frame) - 2):
        row = frame.iloc[index]
        high = safe_float(row.get("high"))
        low = safe_float(row.get("low"))
        if direction == "LONG":
            if high >= safe_float(frame.iloc[index - 1].get("high")) and high >= safe_float(frame.iloc[index + 1].get("high")):
                if high > entry:
                    levels.append(high)
        else:
            if low <= safe_float(frame.iloc[index - 1].get("low")) and low <= safe_float(frame.iloc[index + 1].get("low")):
                if 0 < low < entry:
                    levels.append(low)

    levels = _dedupe_levels(levels, tolerance=atr * 0.35)
    if direction == "SHORT":
        levels = list(reversed(levels))
    return levels, atr


def _level_r(direction: str, entry: float, risk: float, level: float) -> float:
    if risk <= 0:
        return 0.0
    return (level - entry) / risk if direction == "LONG" else (entry - level) / risk


def _fallback_level(direction: str, entry: float, risk: float, target_r: float) -> float:
    return entry + target_r * risk if direction == "LONG" else entry - target_r * risk


def build_route_projection(
    h4: pd.DataFrame,
    direction: str,
    entry: float,
    stop: float,
) -> Optional[Dict[str, Any]]:
    risk = abs(entry - stop)
    if entry <= 0 or risk <= 0:
        return None

    levels, atr4h = h4_swing_levels(h4, direction, entry)
    h4_adx = safe_float(h4.iloc[-1].get("adx14")) if h4 is not None and not h4.empty else 0.0

    level_rows = [
        (level, _level_r(direction, entry, risk, level))
        for level in levels
    ]
    level_rows = [(level, r) for level, r in level_rows if r > 0]
    level_rows.sort(key=lambda item: item[1])

    # Çok yakın ilk 4H engeli varsa büyük hareket için kötü yerden giriş sayılır.
    nearest_obstacle_r = level_rows[0][1] if level_rows else None
    if nearest_obstacle_r is not None and nearest_obstacle_r < 1.15:
        return None

    def pick(min_r: float, fallback_r: float) -> Tuple[float, float, str]:
        for level, r_value in level_rows:
            if r_value >= min_r:
                return level, r_value, "4H_SWING"
        return _fallback_level(direction, entry, risk, fallback_r), fallback_r, "R_EXTENSION"

    tp1, tp1_r, tp1_basis = pick(1.30, 1.50)
    main_target, main_r, main_basis = pick(MIN_MAIN_ROUTE_R, 3.00)
    extended, extended_r, extended_basis = pick(4.20, 5.00)

    # Tarihsel 4H direnç/destek yoksa 3R uzatma ancak güçlü trendde kabul edilir.
    if main_basis == "R_EXTENSION" and h4_adx < 26.0:
        return None
    if main_r < MIN_MAIN_ROUTE_R:
        return None

    zone_half = max(atr4h * 0.15, entry * 0.0015)
    potential_percent = abs(main_target - entry) / entry * 100.0
    if potential_percent < MIN_POTENTIAL_PERCENT:
        return None

    return {
        "tp1": tp1,
        "tp2": main_target,
        "tp3": extended,
        "tp1_r": round(tp1_r, 3),
        "main_target_r": round(main_r, 3),
        "extended_target_r": round(extended_r, 3),
        "tp1_basis": tp1_basis,
        "main_basis": main_basis,
        "extended_basis": extended_basis,
        "main_target_zone_low": main_target - zone_half,
        "main_target_zone_high": main_target + zone_half,
        "potential_percent": round(potential_percent, 2),
        "nearest_obstacle_r": round(nearest_obstacle_r, 3) if nearest_obstacle_r is not None else None,
        "h4_adx_route": round(h4_adx, 2),
    }


def build_entry_zone(entry: float, atr15: float) -> Tuple[float, float]:
    half = max(atr15 * 0.18, entry * 0.0012)
    return entry - half, entry + half


def analyze_big_move(
    exchange: Any,
    row: Dict[str, Any],
    market_regime: Dict[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str]:
    symbol = str(row.get("symbol") or "")
    notional = safe_float(row.get("notional_usdt"))
    if not symbol:
        return None, "INVALID_SYMBOL"
    if notional < MIN_LIQUIDITY_NOTIONAL_USDT:
        return None, "LOW_LIQUIDITY"

    try:
        d1 = core.fetch_df(exchange, symbol, "1d", core.D1_LIMIT)
        if d1.empty:
            return None, "NO_D1_HISTORY"
        if max(core.direction_points(d1, "LONG"), core.direction_points(d1, "SHORT")) < 4:
            return None, "NO_D1_TREND"

        h4 = core.fetch_df(exchange, symbol, "4h", core.H4_LIMIT)
        if h4.empty:
            return None, "NO_H4_HISTORY"
        direction = core.qualified_direction(d1, h4)
        if not direction:
            return None, "NO_D1_H4_ALIGNMENT"

        regime = str(market_regime.get("regime") or "NEUTRAL")
        if regime not in {"NEUTRAL", direction}:
            return None, "MARKET_REGIME_OPPOSITE"

        h1 = core.fetch_df(exchange, symbol, "1h", core.H1_LIMIT)
        if h1.empty:
            return None, "NO_H1_HISTORY"
        setup_type = core.detect_h1_setup(h1, direction)
        if not setup_type:
            return None, "NO_H1_ENTRY_SETUP"

        m15 = core.fetch_df(exchange, symbol, "15m", M15_LIMIT)
        confirmed, confirm = confirm_15m(m15, direction)
        if not confirmed:
            return None, f"NO_15M_CONFIRM:{confirm.get('reason')}"

        funding = core.fetch_funding(exchange, symbol)
        funding_block, funding_points = core.funding_effect(
            direction,
            safe_float(funding.get("rate")),
        )
        if funding_block:
            return None, "EXTREME_ADVERSE_FUNDING"

        structural = core.structural_levels(h1, direction)
        if not structural:
            return None, "STRUCTURAL_LEVELS_FAIL"
        stop = safe_float(structural.get("stop"))

        ticker = exchange.fetch_ticker(core.to_okx_symbol(symbol)) or {}
        current_price = safe_float(ticker.get("last"))
        if current_price <= 0:
            current_price = safe_float(confirm.get("close"))
        confirm_close = safe_float(confirm.get("close"))
        if current_price <= 0 or confirm_close <= 0:
            return None, "NO_CURRENT_PRICE"

        entry_drift = abs(current_price - confirm_close) / confirm_close * 100.0
        if entry_drift > MAX_ENTRY_DRIFT_PERCENT:
            return None, "ENTRY_DRIFT_HIGH"

        risk = abs(current_price - stop)
        risk_percent = risk / current_price * 100.0 if current_price > 0 else 999.0
        if not (MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT):
            return None, "STRUCTURAL_RISK_OUT_OF_RANGE"

        route = build_route_projection(h4, direction, current_price, stop)
        if not route:
            return None, "NO_BIG_ROUTE_ROOM"

        base_score = core.score_candidate(
            d1,
            h4,
            h1,
            direction,
            setup_type,
            regime,
            funding_points,
        )
        quality_bonus = 0
        if safe_float(confirm.get("volume_ratio")) >= 1.10:
            quality_bonus += 2
        if safe_float(confirm.get("adx")) >= 22:
            quality_bonus += 2
        if safe_float(route.get("main_target_r")) >= 4.0:
            quality_bonus += 2
        score = min(100, int(base_score + quality_bonus))
        if score < MIN_SCORE:
            return None, "SCORE_BELOW_MIN"

        oi = core.fetch_open_interest(exchange, symbol)
        entry_zone_low, entry_zone_high = build_entry_zone(
            current_price,
            safe_float(confirm.get("atr")),
        )

        signal = {
            "version": VERSION,
            "symbol": symbol,
            "direction": direction,
            "setup_type": setup_type,
            "score": score,
            "entry": current_price,
            "entry_zone_low": entry_zone_low,
            "entry_zone_high": entry_zone_high,
            "stop": stop,
            "risk": risk,
            "risk_percent": risk_percent,
            "tp1": safe_float(route.get("tp1")),
            "tp2": safe_float(route.get("tp2")),
            "tp3": safe_float(route.get("tp3")),
            "tp1_r": route.get("tp1_r"),
            "main_target_r": route.get("main_target_r"),
            "extended_target_r": route.get("extended_target_r"),
            "main_target_zone_low": route.get("main_target_zone_low"),
            "main_target_zone_high": route.get("main_target_zone_high"),
            "potential_percent": route.get("potential_percent"),
            "route_basis": {
                "tp1": route.get("tp1_basis"),
                "main": route.get("main_basis"),
                "extended": route.get("extended_basis"),
            },
            "nearest_obstacle_r": route.get("nearest_obstacle_r"),
            "market_regime": regime,
            "market_detail": market_regime.get("detail", {}),
            "funding_rate_entry": safe_float(funding.get("rate")),
            "oi_usd_entry": safe_float(oi.get("oi_usd")),
            "notional_24h_usdt": notional,
            "d1_rsi": safe_float(d1.iloc[-1].get("rsi14")),
            "d1_adx": safe_float(d1.iloc[-1].get("adx14")),
            "h4_rsi": safe_float(h4.iloc[-1].get("rsi14")),
            "h4_adx": safe_float(h4.iloc[-1].get("adx14")),
            "h1_rsi": safe_float(h1.iloc[-1].get("rsi14")),
            "h1_adx": safe_float(h1.iloc[-1].get("adx14")),
            "h1_volume_ratio": safe_float(h1.iloc[-1].get("volume_ratio")),
            "m15_rsi": confirm.get("rsi"),
            "m15_adx": confirm.get("adx"),
            "m15_volume_ratio": confirm.get("volume_ratio"),
            "m15_body_strength": confirm.get("body_strength"),
            "entry_drift_percent": round(entry_drift, 4),
            "entry_candle_ts": int(confirm.get("candle_ts") or 0),
            "created_at": now_ts(),
            "created_at_text": core.utc_text(),
        }
        return signal, "BIG_MOVE_SETUP"
    except Exception as exc:
        return None, f"ERROR:{type(exc).__name__}"


def route_key(symbol: str, direction: str) -> str:
    return f"{symbol}_{direction}"


def cooldown_ok(state: Dict[str, Any], symbol: str, direction: str) -> bool:
    key = route_key(symbol, direction)
    last = int(state.get("last_signal_by_symbol_direction", {}).get(key, 0) or 0)
    return (now_ts() - last) >= SAME_DIRECTION_COOLDOWN_HOURS * 3600


def make_trade_id(signal: Dict[str, Any]) -> str:
    return f"{signal['symbol']}_{signal['direction']}_BIGMOVE_{int(signal['created_at'])}"


def open_route(state: Dict[str, Any], signal: Dict[str, Any]) -> str:
    trade_id = make_trade_id(signal)
    trade = dict(signal)
    trade.update({
        "trade_id": trade_id,
        "opened_at": int(signal["created_at"]),
        "last_checked_candle_ts": int(signal.get("entry_candle_ts") or int(signal["created_at"]) * 1000),
        "active_stop": safe_float(signal["stop"]),
        "remaining_fraction": 1.0,
        "realized_price_r": 0.0,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "best_r": 0.0,
        "worst_r": 0.0,
        "closed": False,
    })
    state.setdefault("open_routes", {})[trade_id] = trade
    state.setdefault("last_signal_by_symbol_direction", {})[
        route_key(signal["symbol"], signal["direction"])
    ] = int(signal["created_at"])
    return trade_id


def price_to_r(trade: Dict[str, Any], price: float) -> float:
    risk = safe_float(trade.get("risk"))
    entry = safe_float(trade.get("entry"))
    if risk <= 0:
        return 0.0
    return (price - entry) / risk if trade.get("direction") == "LONG" else (entry - price) / risk


def stop_from_r(trade: Dict[str, Any], stop_r: float) -> float:
    entry = safe_float(trade.get("entry"))
    risk = safe_float(trade.get("risk"))
    return entry + stop_r * risk if trade.get("direction") == "LONG" else entry - stop_r * risk


def close_at_stop(trade: Dict[str, Any], reason: str) -> Dict[str, Any]:
    stop_r = price_to_r(trade, safe_float(trade.get("active_stop")))
    trade["realized_price_r"] = safe_float(trade.get("realized_price_r")) + safe_float(trade.get("remaining_fraction")) * stop_r
    trade["remaining_fraction"] = 0.0
    trade["closed"] = True
    trade["close_reason"] = reason
    trade["closed_at"] = now_ts()
    return trade


def close_at_market(trade: Dict[str, Any], price: float, reason: str) -> Dict[str, Any]:
    current_r = price_to_r(trade, price)
    trade["realized_price_r"] = safe_float(trade.get("realized_price_r")) + safe_float(trade.get("remaining_fraction")) * current_r
    trade["remaining_fraction"] = 0.0
    trade["closed"] = True
    trade["close_reason"] = reason
    trade["close_price"] = price
    trade["closed_at"] = now_ts()
    return trade


def process_route_bar(trade: Dict[str, Any], high: float, low: float) -> Tuple[Dict[str, Any], List[str]]:
    events: List[str] = []
    high_r = price_to_r(trade, high)
    low_r = price_to_r(trade, low)
    trade["best_r"] = max(safe_float(trade.get("best_r")), high_r, low_r)
    trade["worst_r"] = min(safe_float(trade.get("worst_r")), high_r, low_r)

    direction = str(trade.get("direction"))
    stop = safe_float(trade.get("active_stop"))
    stop_hit = low <= stop if direction == "LONG" else high >= stop

    if not trade.get("tp1_hit"):
        target = safe_float(trade.get("tp1"))
        target_name = "TP1"
    elif not trade.get("tp2_hit"):
        target = safe_float(trade.get("tp2"))
        target_name = "ANA_HEDEF"
    elif not trade.get("tp3_hit"):
        target = safe_float(trade.get("tp3"))
        target_name = "UZATILMIS_HEDEF"
    else:
        target = 0.0
        target_name = ""

    target_hit = (high >= target if direction == "LONG" else low <= target) if target > 0 else False
    if stop_hit and target_hit:
        return close_at_stop(trade, "AMBIGUOUS_BAR_STOP_FIRST"), ["SL"]
    if stop_hit:
        reason = "INITIAL_SL"
        if trade.get("tp2_hit"):
            reason = "AFTER_MAIN_TARGET_TRAIL"
        elif trade.get("tp1_hit"):
            reason = "AFTER_TP1_PROTECT"
        return close_at_stop(trade, reason), ["SL"]

    if target_hit and target_name == "TP1":
        target_r = price_to_r(trade, target)
        trade["tp1_hit"] = True
        trade["realized_price_r"] += TP1_FRACTION * target_r
        trade["remaining_fraction"] = 1.0 - TP1_FRACTION
        trade["active_stop"] = stop_from_r(trade, AFTER_TP1_STOP_R)
        events.append("TP1")
    elif target_hit and target_name == "ANA_HEDEF":
        target_r = price_to_r(trade, target)
        trade["tp2_hit"] = True
        trade["realized_price_r"] += TP2_FRACTION * target_r
        trade["remaining_fraction"] = RUNNER_FRACTION
        trade["active_stop"] = stop_from_r(trade, AFTER_TP2_STOP_R)
        events.append("ANA_HEDEF")
    elif target_hit and target_name == "UZATILMIS_HEDEF":
        target_r = price_to_r(trade, target)
        trade["tp3_hit"] = True
        trade["realized_price_r"] += RUNNER_FRACTION * target_r
        trade["remaining_fraction"] = 0.0
        trade["closed"] = True
        trade["close_reason"] = "UZATILMIS_HEDEF"
        trade["closed_at"] = now_ts()
        events.append("UZATILMIS_HEDEF")

    return trade, events


def format_price(value: Any) -> str:
    number = safe_float(value)
    if number >= 1000:
        return f"{number:,.2f}"
    if number >= 1:
        return f"{number:.4f}"
    if number >= 0.01:
        return f"{number:.6f}"
    return f"{number:.8f}"


def signal_message(signal: Dict[str, Any]) -> str:
    arrow = "📈 LONG" if signal.get("direction") == "LONG" else "📉 SHORT"
    route = (
        f"{format_price(signal.get('entry'))} → "
        f"{format_price(signal.get('tp1'))} → "
        f"{format_price(signal.get('tp2'))} → "
        f"{format_price(signal.get('tp3'))}"
    )
    return (
        "🚀 BÜYÜK HAREKET / FİYAT ROTASI\n"
        f"{signal.get('symbol')} • {arrow}\n\n"
        "✅ GİRİŞ ONAYLANDI\n"
        f"Giriş bölgesi: {format_price(signal.get('entry_zone_low'))} - {format_price(signal.get('entry_zone_high'))}\n"
        f"Referans giriş: {format_price(signal.get('entry'))}\n"
        f"Yapısal SL: {format_price(signal.get('stop'))}  (risk %{safe_float(signal.get('risk_percent')):.2f})\n\n"
        f"1. rota: {format_price(signal.get('tp1'))}  (~{safe_float(signal.get('tp1_r')):.1f}R)\n"
        f"ANA hedef: {format_price(signal.get('main_target_zone_low'))} - {format_price(signal.get('main_target_zone_high'))}  (~{safe_float(signal.get('main_target_r')):.1f}R)\n"
        f"Uzatılmış hedef: {format_price(signal.get('tp3'))}  (~{safe_float(signal.get('extended_target_r')):.1f}R)\n"
        f"Beklenen ana hareket: ~%{safe_float(signal.get('potential_percent')):.1f}\n\n"
        f"Beklenen rota: {route}\n"
        f"Setup: {signal.get('setup_type')} | Skor: {int(safe_float(signal.get('score')))}\n"
        f"4H ADX: {safe_float(signal.get('h4_adx')):.1f} | 15M ADX: {safe_float(signal.get('m15_adx')):.1f} | 15M hacim: {safe_float(signal.get('m15_volume_ratio')):.2f}x\n"
        "⏳ Taşıma fikri: saatler / birkaç gün. Ana trend bozulursa senaryo geçersiz.\n"
        "⚠️ Bu bir olasılıklı fiyat rotasıdır; kesin fiyat garantisi değildir."
    )


def event_message(trade: Dict[str, Any], event: str) -> str:
    labels = {
        "TP1": "✅ 1. ROTA GÖRÜLDÜ",
        "ANA_HEDEF": "🎯 ANA HEDEF GÖRÜLDÜ",
        "UZATILMIS_HEDEF": "🏆 UZATILMIŞ HEDEF GÖRÜLDÜ",
        "SL": "🛑 SENARYO GEÇERSİZ / STOP",
        "H4_TREND_BREAK": "⚠️ 4H TREND BOZULDU",
        "MAX_HOLD": "⌛ MAKSİMUM TAŞIMA SÜRESİ",
    }
    return (
        f"{labels.get(event, event)}\n"
        f"🚀 {trade.get('symbol')} • {trade.get('direction')}\n"
        f"Giriş: {format_price(trade.get('entry'))}\n"
        f"Gerçekleşen R: {safe_float(trade.get('realized_price_r')):.3f}R"
    )


def send_signal(signal: Dict[str, Any]) -> bool:
    token = str(os.getenv("TOKEN") or "").strip()
    chat_id = str(os.getenv("CHAT_ID") or "").strip()
    delivery_key = (
        f"SIGNAL|{signal.get('symbol')}|{signal.get('direction')}|"
        f"{int(signal.get('entry_candle_ts') or signal.get('created_at') or 0)}"
    )
    return send_telegram_once(
        signal_message(signal),
        token,
        chat_id,
        BOT_KEY,
        delivery_key=delivery_key,
    )


def send_event(trade: Dict[str, Any], event: str) -> bool:
    token = str(os.getenv("TOKEN") or "").strip()
    chat_id = str(os.getenv("CHAT_ID") or "").strip()
    return send_telegram_once(
        event_message(trade, event),
        token,
        chat_id,
        BOT_KEY,
        delivery_key=f"{trade.get('trade_id')}|{event}",
    )


def manage_open_routes(exchange: Any, state: Dict[str, Any], ledger: Dict[str, Any]) -> None:
    open_routes = state.setdefault("open_routes", {})
    closed_ids: List[str] = []

    for trade_id, trade in list(open_routes.items()):
        try:
            h1 = core.fetch_df(exchange, trade["symbol"], "1h", 220)
            if h1.empty:
                continue

            last_seen = int(trade.get("last_checked_candle_ts") or 0)
            for _, row in h1.iterrows():
                candle_ts = int(row["ts"])
                if candle_ts <= last_seen:
                    continue
                before_closed = bool(trade.get("closed"))
                trade, events = process_route_bar(
                    trade,
                    safe_float(row.get("high")),
                    safe_float(row.get("low")),
                )
                trade["last_checked_candle_ts"] = candle_ts
                for event in events:
                    send_event(trade, event)
                if trade.get("closed") and not before_closed:
                    break

            if not trade.get("closed"):
                held_hours = (now_ts() - int(trade.get("opened_at", now_ts()))) / 3600.0
                if held_hours >= MAX_HOLD_HOURS:
                    trade = close_at_market(
                        trade,
                        safe_float(h1.iloc[-1].get("close")),
                        "MAX_HOLD_7D",
                    )
                    send_event(trade, "MAX_HOLD")
                else:
                    broken, price = core.trend_broken(exchange, trade)
                    if broken and price > 0:
                        trade = close_at_market(trade, price, "H4_TREND_BREAK")
                        send_event(trade, "H4_TREND_BREAK")

            open_routes[trade_id] = trade
            if trade.get("closed"):
                trade = core.finalize_closed_trade(exchange, trade)
                ledger.setdefault("closed_routes", []).append(trade)
                closed_ids.append(trade_id)
        except Exception as exc:
            print("Büyük hareket açık rota takip hatası:", trade_id, type(exc).__name__)

    for trade_id in closed_ids:
        open_routes.pop(trade_id, None)


def rebuild_summary(ledger: Dict[str, Any]) -> Dict[str, Any]:
    records = ledger.get("closed_routes", [])
    values = [safe_float(row.get("net_r_after_costs")) for row in records]
    gross_profit = sum(value for value in values if value > 0)
    gross_loss = abs(sum(value for value in values if value < 0))
    summary = {
        "total_closed": len(records),
        "wins": sum(1 for value in values if value > 0),
        "losses": sum(1 for value in values if value < 0),
        "win_rate_percent": round(sum(1 for value in values if value > 0) / len(values) * 100.0, 2) if values else 0.0,
        "net_r_after_costs": round(sum(values), 6),
        "avg_r_after_costs": round(sum(values) / len(values), 6) if values else 0.0,
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss > 0 else None,
        "tp3": sum(1 for row in records if row.get("close_reason") == "UZATILMIS_HEDEF"),
        "initial_sl": sum(1 for row in records if row.get("close_reason") == "INITIAL_SL"),
        "h4_trend_break": sum(1 for row in records if row.get("close_reason") == "H4_TREND_BREAK"),
        "avg_hold_hours": round(sum(safe_float(row.get("hold_hours")) for row in records) / len(records), 2) if records else 0.0,
    }
    ledger["summary"] = summary
    ledger["last_update"] = core.utc_text()
    return summary


def run() -> None:
    import ccxt

    exchange = ccxt.okx({
        "enableRateLimit": True,
        "timeout": 20000,
        "options": {"defaultType": "swap"},
    })

    state = load_state()
    ledger = load_ledger()

    print("=== BÜYÜK HAREKET / FİYAT ROTASI ===")
    print("Version:", VERSION)
    print("Mode:", MODE)

    manage_open_routes(exchange, state, ledger)
    all_rows, universe_meta = build_full_universe(exchange)
    market_regime = core.get_market_regime(exchange)

    reasons: Dict[str, int] = {}
    candidates: List[Dict[str, Any]] = []
    open_symbols = {trade.get("symbol") for trade in state.get("open_routes", {}).values()}

    if len(state.get("open_routes", {})) < MAX_OPEN_ROUTES:
        for row in all_rows:
            symbol = str(row.get("symbol") or "")
            if symbol in open_symbols:
                reasons["ALREADY_OPEN"] = reasons.get("ALREADY_OPEN", 0) + 1
                continue
            # Tüm aktif coinler görüldü; düşük likiditeli coin burada güvenlik nedeniyle derin analize girmez.
            if safe_float(row.get("notional_usdt")) < MIN_LIQUIDITY_NOTIONAL_USDT:
                reasons["LOW_LIQUIDITY"] = reasons.get("LOW_LIQUIDITY", 0) + 1
                continue

            # Aynı coin yönünde tekrar tekrar Telegram üretme.
            # Yön henüz bilinmediği için mevcut LONG/SHORT cooldown'larından biri açıksa analiz yine yapılabilir;
            # nihai candidate geldiğinde yön bazlı son kontrol yapılır.
            signal, reason = analyze_big_move(exchange, row, market_regime)
            reasons[reason] = reasons.get(reason, 0) + 1
            if signal and cooldown_ok(state, signal["symbol"], signal["direction"]):
                candidates.append(signal)
            elif signal:
                reasons["SIGNAL_COOLDOWN"] = reasons.get("SIGNAL_COOLDOWN", 0) + 1

    candidates.sort(
        key=lambda item: (
            safe_float(item.get("score")),
            safe_float(item.get("main_target_r")),
            safe_float(item.get("potential_percent")),
            safe_float(item.get("notional_24h_usdt")),
        ),
        reverse=True,
    )

    slots = max(0, MAX_OPEN_ROUTES - len(state.get("open_routes", {})))
    selected = candidates[: min(MAX_NEW_SIGNALS_PER_RUN, slots)]
    opened = 0
    for signal in selected:
        if send_signal(signal):
            trade_id = open_route(state, signal)
            opened += 1
            print(
                "TELEGRAM BIG MOVE:",
                trade_id,
                "score",
                signal["score"],
                "mainR",
                signal["main_target_r"],
                "potential%",
                signal["potential_percent"],
            )
        else:
            print("Telegram gönderilemedi; rota açılmadı:", signal.get("symbol"))

    summary = rebuild_summary(ledger)
    state["last_run"] = core.utc_text()
    state["universe"] = {
        **universe_meta,
        "market_regime": market_regime,
    }
    state["run_stats"] = {
        "eligible_seen": len(all_rows),
        "deep_scan_liquid": universe_meta.get("liquid_for_deep_scan"),
        "candidate_count": len(candidates),
        "telegram_opened": opened,
        "open_routes": len(state.get("open_routes", {})),
        "reason_counts": dict(sorted(reasons.items())),
    }

    core.atomic_save_json(STATE_FILE, state)
    core.atomic_save_json(LEDGER_FILE, ledger)

    print("Eligible seen:", len(all_rows))
    print("Liquid deep scan:", universe_meta.get("liquid_for_deep_scan"))
    print("Candidates:", len(candidates))
    print("Telegram opened:", opened)
    print("Open routes:", len(state.get("open_routes", {})))
    print("Closed routes:", summary.get("total_closed"))
    print("Net R:", summary.get("net_r_after_costs"))
    print("Gerçek emir: KAPALI")


if __name__ == "__main__":
    run()
