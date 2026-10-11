"""V7 independent trend/retest entry engine.

Unlike V6, this engine does not require a 1m acceleration spike, a 2.5% predicted
move, or a 90+ uncalibrated score. It observes the SAME candles already fetched
by Market First and sends only manual-entry Telegram trade ideas, via the
existing duplicate, portfolio, delivery and lifecycle system.

This is a new forward strategy, not an assertion of a positive backtested edge.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping

import market_first_strategy as strategy

VERSION = "MARKET_FIRST_V7_TREND_RETEST_2026_10_11"
STATE_FILE = Path("market_first_v7_state.json")
MAX_DAILY_SIGNALS = 2
MAX_RISKY_OPEN = 2
MAX_SIGNALS_PER_SCAN = 1
MIN_QUOTE_VOLUME = 1_500_000.0
MIN_RISK_PERCENT = 0.50
MAX_RISK_PERCENT = 1.55
MIN_5M_VOLUME = 0.65
MAX_SPREAD_PERCENT = 0.18
MAX_ENTRY_DRIFT_PERCENT = 0.22
ROUND_TRIP_COST_RESERVE_PERCENT = 0.15
COOLDOWN_SECONDS = 4 * 60 * 60

_INSTALLED = False
_RUNNER: Any = None
_CANDIDATES: dict[str, dict[str, Any]] = {}
_REJECTED: Counter[str] = Counter()
_ORIGINAL_FORMAT: Any = None


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        val = float(value)
        return val if math.isfinite(val) else default
    except (ValueError, TypeError):
        return default


def _price(direction: str, entry: float, risk: float, multiples: float) -> float:
    return entry + (risk * multiples if direction == "LONG" else -risk * multiples)


def evaluate(
    *, symbol: str, df5m: Any, df15m: Any, df1h: Any,
    current_price: float, quote_volume_24h: float, context: Any,
) -> tuple[dict[str, Any] | None, str]:
    """Use only fully closed 5m, 15m and 1h candles; no peeking at the open bar."""
    if _sf(current_price) <= 0 or _sf(quote_volume_24h) < MIN_QUOTE_VOLUME:
        return None, "PRICE_OR_LIQUIDITY"

    s5 = strategy._structure(df5m, current_price)
    s15 = strategy._structure(df15m, current_price)
    s1h = strategy._structure(df1h, current_price)
    if not s5 or not s15 or not s1h:
        return None, "MTF_DATA"

    direction = str(s15.get("direction") or "")
    if direction not in {"LONG", "SHORT"} or s1h.get("direction") != direction:
        return None, "15M_1H_TREND_CONFLICT"

    regime = str(getattr(context, "regime", "") or "").upper()
    market_preferred = str(getattr(context, "preferred_direction", "") or "").upper()
    if regime.startswith("SHOCK") or (
        regime in {"BULL_STRONG", "BEAR_STRONG"} and
        market_preferred in {"LONG", "SHORT"} and market_preferred != direction
    ):
        return None, "MARKET_OPPOSITE"

    if df5m is None or len(df5m) < 65:
        return None, "5M_HISTORY"
    closed = df5m.iloc[:-1]
    last = closed.iloc[-1]
    prev = closed.iloc[-2]
    recent = closed.iloc[-9:-1]
    entry = _sf(last.get("close"))
    atr5 = _sf(s5.get("atr"))
    ema9 = _sf(s5.get("ema9"))
    ema20 = _sf(s5.get("ema20"))
    v5 = _sf(s5.get("volume_ratio"))
    v15 = _sf(s15.get("volume_ratio"))
    if min(entry, atr5, ema9, ema20) <= 0:
        return None, "VOLATILITY_DATA"

    # Confirm the latest fully closed bar is still representative of current
    # price; do not turn an old close into a chase entry.
    drift = abs(current_price / entry - 1) * 100
    if drift > MAX_ENTRY_DRIFT_PERCENT:
        return None, "STALE_5M_CLOSE"
    atr_percent = 100 * atr5 / entry
    if not (0.15 <= atr_percent <= 2.50):
        return None, "VOLATILITY_UNSUITABLE"
    if v5 < MIN_5M_VOLUME or v15 < 0.45:
        return None, "VOLUME_LOW"

    sign = 1 if direction == "LONG" else -1
    if sign * (entry - ema20) <= 0 or sign * (entry - ema9) <= 0:
        return None, "5M_NOT_RECLAIMED"
    if abs(entry - ema20) / atr5 > 1.35:
        return None, "ENTRY_EXTENDED"
    candle_body = sign * (_sf(last.get("close")) - _sf(last.get("open")))
    if candle_body < 0.12 * atr5:
        return None, "WEAK_CONFIRMATION_CANDLE"
    if (_sf(last.get("high")) - _sf(last.get("low"))) > 2.3 * atr5:
        return None, "LATE_SPIKE"

    if direction == "LONG":
        touched = _sf(recent["low"].min()) <= ema20 + 0.40 * atr5
        breakout = entry > _sf(recent.tail(4)["high"].max())
        improved = entry >= _sf(prev.get("close"))
        stop = _sf(closed.tail(8)["low"].min()) - 0.12 * atr5
    else:
        touched = _sf(recent["high"].max()) >= ema20 - 0.40 * atr5
        breakout = entry < _sf(recent.tail(4)["low"].min())
        improved = entry <= _sf(prev.get("close"))
        stop = _sf(closed.tail(8)["high"].max()) + 0.12 * atr5

    # Two understandable setups: a completed EMA20 retest with renewed
    # momentum, or a non-extended 5m consolidation breakout.
    setup = "PULLBACK" if touched and improved else (
        "CONTINUATION" if breakout and v5 >= 1.1 else ""
    )
    if not setup:
        return None, "NO_RETEST_OR_BREAKOUT"

    risk = abs(entry - stop)
    risk_percent = 100 * risk / entry
    if stop <= 0 or not (MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT):
        return None, "STOP_GEOMETRY"
    if risk_percent - ROUND_TRIP_COST_RESERVE_PERCENT < 0.30:
        return None, "COST_TOO_HIGH_FOR_TP1"

    tps = [_price(direction, entry, risk, x) for x in (1.0, 1.75, 2.5)]
    if min(tps) <= 0:
        return None, "TARGET_GEOMETRY"
    score = min(94, 76 + (5 if setup == "PULLBACK" else 2)
                + (4 if v5 >= 1.1 else 0)
                + (3 if v15 >= 0.9 else 0)
                + (4 if s5.get("direction") == direction else 0)
                + (2 if quote_volume_24h >= 10_000_000 else 0))
    signal: dict[str, Any] = {
        "symbol": str(symbol), "direction": direction,
        "source": "MARKET_FIRST_V7", "entry_type": "MARKET_FIRST_V7",
        "v7_version": VERSION, "v7_setup": setup,
        "signal_class": "TRADE", "entry": round(entry, 12),
        "ideal_entry": round(entry, 12), "sl": round(stop, 12),
        "tp1": round(tps[0], 12), "tp2": round(tps[1], 12),
        "tp3": round(tps[2], 12),
        "risk_percent": round(risk_percent, 4), "rr_tp1": 1.0,
        "rr_tp2": 1.75, "rr_tp3": 2.5, "score": score,
        "quality": "V7 TREND/RETEST (FORWARD TEST)",
        "quality_note": "1H+15M uyumu ve kapalı 5M retest/teyit",
        "zone_name": setup, "zone_distance_percent": 0,
        "market_regime": regime, "market_label": strategy.market_label(context),
        "trend_1h": direction, "volume_5m": round(v5, 3),
        "volume_ratio": round(v5, 3),
        "v7_volume_15m": round(v15, 3),
        "v7_extension_atr": round(abs(entry - ema20) / atr5, 3),
        "v7_atr_percent": round(atr_percent, 3),
        "v7_cost_reserve_percent": ROUND_TRIP_COST_RESERVE_PERCENT,
        "v7_closed_5m_time": int(_sf(last.get("time")) / 1000),
    }
    return signal, "OK"


def validate_live_signal(signal: Mapping[str, Any] | None) -> tuple[bool, str, dict[str, Any]]:
    """Recalculate V7 risk geometry independently at final-send time."""
    if not isinstance(signal, Mapping):
        return False, "INVALID", {}
    direction = str(signal.get("direction") or "")
    entry, sl = _sf(signal.get("entry")), _sf(signal.get("sl"))
    levels = [_sf(signal.get(k)) for k in ("tp1", "tp2", "tp3")]
    risk = abs(entry - sl)
    risk_percent = 100 * risk / entry if entry > 0 else 0
    evidence = {"risk_percent": round(risk_percent, 4), "v7_setup": signal.get("v7_setup")}
    if signal.get("v7_version") != VERSION or signal.get("source") != "MARKET_FIRST_V7":
        return False, "NOT_V7", evidence
    if direction not in {"LONG", "SHORT"} or min(entry, sl, *levels) <= 0:
        return False, "PRICE", evidence
    if signal.get("v7_setup") not in {"PULLBACK", "CONTINUATION"}:
        return False, "SETUP", evidence
    if not MIN_RISK_PERCENT - 0.01 <= risk_percent <= MAX_RISK_PERCENT + 0.01:
        return False, "STOP", evidence
    sign = 1 if direction == "LONG" else -1
    if sign * (entry - sl) <= 0:
        return False, "STOP_SIDE", evidence
    for level, multiple in zip(levels, (1.0, 1.75, 2.5)):
        if abs(sign * (level - entry) / risk - multiple) > 0.03:
            return False, "TARGET_R", evidence
    if _sf(signal.get("v7_extension_atr"), 99) > 1.35:
        return False, "CHASED", evidence
    return True, "OK", evidence


def _load_state() -> dict[str, Any]:
    try:
        obj = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_state(state: dict[str, Any]) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, STATE_FILE)


def _day(now: int) -> str:
    return datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")


def _fresh_spread(exchange: Any, symbol: str) -> tuple[bool, str]:
    try:
        ticker = exchange.fetch_ticker(_RUNNER.bot.to_okx_symbol(symbol))
        bid, ask = _sf(ticker.get("bid")), _sf(ticker.get("ask"))
        if bid <= 0 or ask <= bid:
            return False, "NO_BID_ASK"
        spread = (ask - bid) / ((ask + bid) / 2) * 100
        return (spread <= MAX_SPREAD_PERCENT), f"SPREAD_{spread:.3f}P"
    except Exception as exc:
        return False, "QUOTE_ERROR_" + type(exc).__name__


def _format(signal: Mapping[str, Any]) -> str:
    if signal.get("v7_version") != VERSION:
        return _ORIGINAL_FORMAT(signal)
    b = _RUNNER.bot
    return (
        "🚨 KALİTELİ KRİPTO İŞLEM\n\n"
        "🔄 Yeni V7 — Trend + Geri Çekilme (deneme sürümü)\n"
        f"🪙 {signal['symbol']} | {'🟢' if signal['direction'] == 'LONG' else '🔴'} {signal['direction']}\n"
        f"📍 Giriş: {b.format_price(signal['entry'])}\n"
        f"🛑 Stop: {b.format_price(signal['sl'])} (~%{signal['risk_percent']:.2f})\n"
        f"🎯 TP1: {b.format_price(signal['tp1'])} (1R)\n"
        f"🎯 TP2: {b.format_price(signal['tp2'])} (1.75R)\n"
        f"🎯 TP3: {b.format_price(signal['tp3'])} (2.5R)\n"
        f"⚙️ Kurulum: {signal['v7_setup']} | Maliyet rezervi: %{ROUND_TRIP_COST_RESERVE_PERCENT:.2f}\n"
        "⚠️ Yeni strateji; kârlılığı doğrulanmadı. İşlem manueldir, emir açılmaz."
    )


def install(runner: Any) -> None:
    global _INSTALLED, _RUNNER, _ORIGINAL_FORMAT
    if _INSTALLED:
        return
    _INSTALLED = True
    _RUNNER = runner
    _ORIGINAL_FORMAT = runner._format_trade_message
    original_analyze = runner.analyze_candidate

    def analyze_with_v7(*args: Any, **kwargs: Any):
        result = original_analyze(*args, **kwargs)
        def arg(key: str, i: int, default: Any = None) -> Any:
            return kwargs.get(key, args[i] if len(args) > i else default)
        try:
            candidate, reason = evaluate(
                symbol=arg("symbol", 0, ""),
                df5m=arg("df5m", 2),
                df15m=arg("df15m", 3),
                df1h=arg("df1h", 4),
                current_price=arg("current_price", 5, 0),
                quote_volume_24h=arg("quote_volume_24h", 6, 0),
                context=arg("context", 7),
            )
            _REJECTED[reason] += 1
            if candidate is not None:
                symbol = candidate["symbol"]
                if symbol not in _CANDIDATES or candidate["score"] > _CANDIDATES[symbol]["score"]:
                    _CANDIDATES[symbol] = candidate
        except Exception as exc:
            _REJECTED["EVALUATION_ERROR_" + type(exc).__name__] += 1
        return result

    runner.analyze_candidate = analyze_with_v7
    runner._format_trade_message = _format


def finish() -> dict[str, Any]:
    """Send one well-defined MANUAL signal through the existing guarded flow."""
    if _RUNNER is None:
        return {"status": "NOT_INSTALLED"}
    bot = _RUNNER.bot
    now = int(bot.now_ts())
    state = _load_state()
    today = _day(now)
    day_count = int(state.get("day_count") or 0) if state.get("day") == today else 0
    sent = []
    exclusions = Counter()
    candidates = sorted(_CANDIDATES.values(), key=lambda s: s["score"], reverse=True)
    is_live = _RUNNER._is_live_run()
    if not is_live:
        exclusions["NOT_MAIN_BRANCH"] += 1
    elif bot.get_today_sl_count() >= 2:
        exclusions["DAILY_STOP_LIMIT"] += 1
    elif day_count >= MAX_DAILY_SIGNALS:
        exclusions["DAILY_SIGNAL_LIMIT"] += 1
    else:
        risky, _, _ = bot.count_open_signal_risk()
        if risky >= MAX_RISKY_OPEN:
            exclusions["MAX_RISKY_OPEN"] += 1
        else:
            exchange = bot.get_exchange()
            for candidate in candidates:
                if len(sent) >= MAX_SIGNALS_PER_SCAN or day_count >= MAX_DAILY_SIGNALS:
                    break
                symbol = candidate["symbol"]
                key = symbol + ":" + candidate["direction"]
                last = int((state.get("last_sent_by_symbol") or {}).get(key, 0))
                if now - last < COOLDOWN_SECONDS:
                    exclusions["V7_4H_COOLDOWN"] += 1
                    continue
                if bot.has_open_same_symbol(symbol) or bot.has_recent_stop(symbol):
                    exclusions["SAME_SYMBOL_OR_STOP"] += 1
                    continue
                if bot.is_duplicate(candidate, radar=False):
                    exclusions["LEGACY_DUPLICATE"] += 1
                    continue
                valid, reason = _fresh_spread(exchange, symbol)
                if not valid:
                    exclusions[reason] += 1
                    continue
                if _RUNNER._send_trade(exchange, dict(candidate), _RUNNER.load_ml_store()):
                    day_count += 1
                    sent.append(key)
                    state.setdefault("last_sent_by_symbol", {})[key] = now
                else:
                    exclusions["FINAL_RISK_OR_DELIVERY_REJECT"] += 1

    state.update({
        "version": VERSION, "day": today, "day_count": day_count,
        "last_run_at": now, "observed_candidates": len(candidates),
        "last_candidates": [{"symbol": x["symbol"], "direction": x["direction"],
                             "score": x["score"], "setup": x["v7_setup"],
                             "risk_percent": x["risk_percent"]}
                            for x in candidates[:8]],
        "last_rejections": dict(_REJECTED),
        "last_send_blocks": dict(exclusions),
        "last_sent": sent,
        "note": "New live-manual forward strategy. No claimed backtested edge, no automatic exchange order.",
    })
    state["last_sent_by_symbol"] = {
        k: v for k, v in (state.get("last_sent_by_symbol") or {}).items()
        if isinstance(v, (int, float)) and now - int(v) <= COOLDOWN_SECONDS
    }
    _save_state(state)
    print("MARKET FIRST V7:", state["observed_candidates"],
          "candidate(s); sent=", sent, "blocked=", dict(exclusions),
          "reasons=", dict(_REJECTED.most_common(8)))
    return state
