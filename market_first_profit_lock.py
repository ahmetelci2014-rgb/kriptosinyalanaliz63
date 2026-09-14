"""Pre-TP1 profit lock for manually executed Market First trades.

The live strategy can now use wider structural TP targets. A consequence is that
a trade may travel several R in the correct direction without touching TP1, then
return all the way to the original stop. This module protects that path without
moving the initial stop wider and without placing exchange orders.

When a still-open pre-TP1 trade:
- reaches at least +1.50R, and
- the trigger candle closes at least +0.75R in profit, and
- that candle did not also hit TP1 or the original SL,
we send one Telegram instruction to move the manual SL to entry. The protection
is effective from the next candle, avoiding same-candle look-ahead. If a later
candle returns to entry before TP1, the internal tracker records a neutral
PROFIT_LOCK_BE instead of a full stop.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

import main as bot

VERSION = "MARKET_FIRST_PROFIT_LOCK_V1_2026_09_14"
TRIGGER_R = 1.50
MIN_TRIGGER_CLOSE_R = 0.75
_INSTALLED = False


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except Exception:
        return default


def _timeframe_seconds(value: Any) -> int:
    text = str(value or "5m").strip().lower()
    try:
        if text.endswith("m"):
            return max(60, int(float(text[:-1])) * 60)
        if text.endswith("h"):
            return max(3600, int(float(text[:-1])) * 3600)
    except Exception:
        pass
    return 300


def _original_sl(signal: Mapping[str, Any]) -> Optional[float]:
    return _sf(signal.get("profit_lock_original_sl"), _sf(signal.get("sl")))


def directional_r(
    direction: str,
    entry: float,
    original_sl: float,
    price: float,
) -> Optional[float]:
    risk = abs(entry - original_sl)
    if entry <= 0 or risk <= 0:
        return None
    if str(direction).upper() == "LONG":
        return (price - entry) / risk
    if str(direction).upper() == "SHORT":
        return (entry - price) / risk
    return None


def trigger_levels(signal: Mapping[str, Any]) -> Optional[Dict[str, float]]:
    entry = _sf(signal.get("entry"))
    sl = _original_sl(signal)
    direction = str(signal.get("direction") or "").upper()
    if entry is None or sl is None or entry <= 0 or direction not in {"LONG", "SHORT"}:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    sign = 1.0 if direction == "LONG" else -1.0
    return {
        "entry": entry,
        "original_sl": sl,
        "risk": risk,
        "trigger_price": entry + sign * TRIGGER_R * risk,
        "min_close_price": entry + sign * MIN_TRIGGER_CLOSE_R * risk,
    }


def candle_can_arm(signal: Mapping[str, Any], candle: Mapping[str, Any]) -> bool:
    levels = trigger_levels(signal)
    if not levels or bool(signal.get("tp1_hit")):
        return False
    direction = str(signal.get("direction") or "").upper()
    high = _sf(candle.get("high"))
    low = _sf(candle.get("low"))
    close = _sf(candle.get("close"))
    tp1 = _sf(signal.get("tp1"))
    if None in {high, low, close, tp1}:
        return False

    entry = levels["entry"]
    original_sl = levels["original_sl"]
    trigger = levels["trigger_price"]
    min_close = levels["min_close_price"]

    if direction == "LONG":
        trigger_hit = high >= trigger
        close_confirmed = close >= min_close
        tp1_same_candle = high >= tp1
        original_sl_same_candle = low <= original_sl
    else:
        trigger_hit = low <= trigger
        close_confirmed = close <= min_close
        tp1_same_candle = low <= tp1
        original_sl_same_candle = high >= original_sl

    # If TP1 or original SL also exists inside the trigger candle, ordering cannot
    # be proven from OHLC. Existing lifecycle logic remains authoritative.
    return bool(
        trigger_hit
        and close_confirmed
        and not tp1_same_candle
        and not original_sl_same_candle
        and entry > 0
    )


def protected_candle_outcome(
    signal: Mapping[str, Any],
    candle: Mapping[str, Any],
) -> Optional[str]:
    """Outcome after protection is effective; same-candle ambiguity is preserved."""
    if bool(signal.get("tp1_hit")):
        return "TP1"
    entry = _sf(signal.get("entry"))
    tp1 = _sf(signal.get("tp1"))
    high = _sf(candle.get("high"))
    low = _sf(candle.get("low"))
    if None in {entry, tp1, high, low}:
        return None
    direction = str(signal.get("direction") or "").upper()
    if direction == "LONG":
        tp1_hit = high >= tp1
        entry_hit = low <= entry
    elif direction == "SHORT":
        tp1_hit = low <= tp1
        entry_hit = high >= entry
    else:
        return None
    if tp1_hit and entry_hit:
        return "AMBIGUOUS"
    if tp1_hit:
        return "TP1"
    if entry_hit:
        return "BE"
    return None


def _correct_profit_lock_ledger(signal: Mapping[str, Any]) -> None:
    try:
        ledger, trade_id, trade = bot.get_ledger_trade_for_signal(signal)
        if not isinstance(trade, dict):
            return
        trade["final_result"] = "PROFIT_LOCK_BE"
        trade["r_result"] = 0.0
        trade["profit_lock_version"] = VERSION
        trade["profit_lock_trigger_r"] = TRIGGER_R
        trade["profit_lock_armed_at"] = signal.get("profit_lock_armed_at")
        trade["profit_lock_effective_at"] = signal.get("profit_lock_effective_at")
        trade["diagnosis"] = {
            "version": VERSION,
            "primary": "KAR_KORUMA_BE",
            "confidence": "YUKSEK",
            "provisional": False,
            "factors": [
                f"TP1 öncesi en az {TRIGGER_R:.2f}R lehe hareket görüldü.",
                "Manuel koruma talimatı sonrası kalan işlem girişten kapandı.",
            ],
        }
        # Keep the standard BE event for durable duplicate protection, but attach
        # an explicit semantic event for later cohort analysis.
        events = trade.setdefault("events", [])
        if not any(str(item.get("event") or "").upper() == "PROFIT_LOCK_BE" for item in events):
            events.append({
                "time": bot.now_ts(),
                "event": "PROFIT_LOCK_BE",
                "price": _sf(signal.get("entry")),
            })
        bot.save_trade_ledger(ledger)
        print("PROFIT LOCK LEDGER:", trade_id, "-> PROFIT_LOCK_BE")
    except Exception as exc:
        print("Profit lock ledger düzeltme hatası:", exc)


def _close_at_profit_lock_be(
    symbol: str,
    signal: Dict[str, Any],
    original_close,
) -> bool:
    entry = _sf(signal.get("entry"))
    if entry is None:
        return False
    signal["profit_lock_closed"] = True
    signal["closed"] = True
    recorded = bool(original_close(symbol, signal, "BE", entry))
    if not recorded:
        return False
    _correct_profit_lock_ledger(signal)
    bot.send_telegram(
        "🟡 KÂR KORUMA / BE\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {signal.get('direction')}\n"
        f"Giriş: {bot.format_price(entry)}\n"
        f"İşlem TP1 öncesi +{TRIGGER_R:.2f}R gördüğü için koruma girişteydi.\n"
        "Kalan işlem girişten kapandı; tam stop zararı oluşmadı.",
        delivery_key=f"{bot.build_trade_id(signal)}|PROFIT_LOCK_BE",
    )
    return True


def _arm_message(symbol: str, signal: Mapping[str, Any]) -> str:
    entry = _sf(signal.get("entry"), 0.0) or 0.0
    return (
        "🛡️ KÂR KORUMA AKTİF\n\n"
        f"Coin: {symbol}\n"
        f"Yön: {signal.get('direction')}\n"
        f"Giriş: {bot.format_price(entry)}\n"
        f"İşlem TP1 gelmeden en az +{TRIGGER_R:.2f}R lehe hareket gördü.\n"
        "📌 Manuel işlemde SL'yi GİRİŞ fiyatına çek.\n"
        "Bu koruma TP1 hedefini değiştirmez; yalnız tam stopa geri dönüşü engeller."
    )


def _new_candles(signal: Mapping[str, Any], candles: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    last_checked = int(_sf(signal.get("last_checked_at"), _sf(signal.get("opened_at"), 0.0)) or 0)
    return sorted(
        [
            item for item in candles
            if int(_sf(item.get("time"), 0.0) or 0) > last_checked
        ],
        key=lambda item: int(_sf(item.get("time"), 0.0) or 0),
    )


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_check = bot.check_open_signals
    original_close = bot.close_signal_result

    def check_open_signals_with_profit_lock(exchange):
        open_signals = bot.load_open_signals()
        if not isinstance(open_signals, dict) or not open_signals:
            return original_check(exchange)

        updated = dict(open_signals)
        changed = False
        removed = set()
        timeframe_seconds = _timeframe_seconds(getattr(bot, "TRACK_TIMEFRAME", "5m"))

        for key, raw_signal in list(updated.items()):
            if not isinstance(raw_signal, dict):
                continue
            signal = raw_signal
            if bool(signal.get("closed")) or bool(signal.get("tp1_hit")):
                continue

            symbol = str(signal.get("symbol") or "")
            direction = str(signal.get("direction") or "").upper()
            entry = _sf(signal.get("entry"))
            original_sl = _original_sl(signal)
            if not symbol or direction not in {"LONG", "SHORT"} or entry is None or original_sl is None:
                continue

            opened_at = int(_sf(signal.get("opened_at"), bot.now_ts()) or bot.now_ts())
            last_checked = int(_sf(signal.get("last_checked_at"), opened_at) or opened_at)
            candles = bot.fetch_candles_since(
                exchange,
                symbol,
                getattr(bot, "TRACK_TIMEFRAME", "5m"),
                since_seconds=max(opened_at, last_checked - 10 * 60),
                limit=getattr(bot, "TRACK_LIMIT", 120),
            )
            fresh = _new_candles(signal, candles or [])
            if not fresh:
                continue

            effective_at = int(_sf(signal.get("profit_lock_effective_at"), 0.0) or 0)
            active = bool(signal.get("profit_lock_active")) and effective_at > 0

            if active:
                for candle in fresh:
                    candle_time = int(_sf(candle.get("time"), 0.0) or 0)
                    if candle_time < effective_at:
                        continue
                    outcome = protected_candle_outcome(signal, candle)
                    if outcome == "BE":
                        if _close_at_profit_lock_be(symbol, signal, original_close):
                            removed.add(key)
                            updated.pop(key, None)
                            changed = True
                        break
                    if outcome in {"TP1", "AMBIGUOUS"}:
                        # Existing TP1/same-candle resolver remains authoritative.
                        break
                continue

            # Find a fresh, confirmed trigger. Protection starts with the next
            # candle, never retroactively inside the trigger candle.
            arm_index = None
            for idx, candle in enumerate(fresh):
                if candle_can_arm(signal, candle):
                    arm_index = idx
                    break
            if arm_index is None:
                continue

            arm_candle = fresh[arm_index]
            armed_at = int(_sf(arm_candle.get("time"), bot.now_ts()) or bot.now_ts())
            delivery_key = f"{bot.build_trade_id(signal)}|PROFIT_LOCK_ARM"
            sent = bot.send_telegram(_arm_message(symbol, signal), delivery_key=delivery_key)
            if not sent:
                # Manual execution cannot be assumed when the instruction was not delivered.
                continue

            signal["profit_lock_version"] = VERSION
            signal["profit_lock_active"] = True
            signal["profit_lock_original_sl"] = original_sl
            signal["profit_lock_trigger_r"] = TRIGGER_R
            signal["profit_lock_armed_at"] = armed_at
            signal["profit_lock_effective_at"] = armed_at + timeframe_seconds
            signal["profit_lock_notified"] = True
            changed = True
            print("PROFIT LOCK AKTİF:", symbol, direction, f"{TRIGGER_R:.2f}R")

            # If already-fetched later candles returned to entry, resolve them now
            # before the legacy SL logic sees the original stop.
            for candle in fresh[arm_index + 1:]:
                candle_time = int(_sf(candle.get("time"), 0.0) or 0)
                if candle_time < int(signal["profit_lock_effective_at"]):
                    continue
                outcome = protected_candle_outcome(signal, candle)
                if outcome == "BE":
                    if _close_at_profit_lock_be(symbol, signal, original_close):
                        removed.add(key)
                        updated.pop(key, None)
                        changed = True
                    break
                if outcome in {"TP1", "AMBIGUOUS"}:
                    break

        if changed:
            bot.save_json_file(bot.OPEN_SIGNALS_FILE, updated)

        # The original lifecycle remains authoritative for every non-profit-lock
        # event and for all still-open signals.
        return original_check(exchange)

    bot.check_open_signals = check_open_signals_with_profit_lock


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "trigger_r": TRIGGER_R,
        "min_trigger_close_r": MIN_TRIGGER_CLOSE_R,
        "pre_tp1_only": True,
        "effective_from_next_candle": True,
        "manual_sl_instruction": True,
        "exchange_orders": False,
        "stop_widening": False,
    }
