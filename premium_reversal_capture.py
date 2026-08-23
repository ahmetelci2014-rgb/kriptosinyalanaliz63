"""Premium post-TP3 reversal capture.

Purpose
-------
The legacy live loop blocks a symbol for four hours after TP3/BE/EXPIRED. That
protects against immediate re-entry, but it also hides a genuinely new move in
the opposite direction. This module keeps the same-direction cooldown intact
and opens a narrow exception only after a completed TP3 when fresh Pump/Dump
shadow structure confirms a strong reversal.

Safety / scope
--------------
- No exchange orders are opened.
- Signals still pass the existing Premium entry validator, cost viability,
  duplicate protection and portfolio-risk gate before Telegram delivery.
- Same-direction re-entry remains blocked during the legacy cooldown.
- Only opposite-direction reversals after TP3 are eligible in V1.
"""
from __future__ import annotations

import json
import math
import time
from typing import Any, Dict, Optional

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import ADXIndicator, EMAIndicator
from ta.volatility import AverageTrueRange


VERSION = "PREMIUM_POST_TP3_REVERSAL_V1_2026_08_23"
SOURCE = "REVERSAL"
PUMP_STATE_FILE = "pump_radar_state.json"

# Keep this aligned with the legacy recent-closed protection. The exception is
# directional, not a removal of the cooldown.
MAX_REVERSAL_WINDOW_SECONDS = 4 * 60 * 60
MIN_SECONDS_AFTER_TP3 = 5 * 60
MAX_SHADOW_EVENT_AGE_SECONDS = 45 * 60

MIN_MOVE15_PERCENT = 0.45
MAX_MOVE15_PERCENT = 1.80
MAX_EVENT_DRIFT_PERCENT = 0.60
MAX_EVENT_EMA_DISTANCE_PERCENT = 0.95
MIN_SCORE = 96
MIN_RISK_PERCENT = 0.45
MAX_RISK_PERCENT = 1.80
STOP_BUFFER_PERCENT = 0.15
TP1_R = 0.55
TP2_R = 1.05
TP3_R = 1.60

# A low 5M average can still be acceptable if 1M has clearly woken up. This is
# exactly the pattern that appeared in the FARTCOIN post-TP3 reversal example.
MIN_VOL5_NORMAL = 0.85
MIN_VOL5_WITH_1M_WAKE = 0.35
MIN_VOL1_WAKE = 1.50

_PERFORMANCE_CACHE: Optional[Dict[str, Any]] = None
_SHADOW_CACHE: Optional[Dict[str, Any]] = None
_INSTALLED = False


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def reset_caches() -> None:
    """Useful for tests; production runs are one process per Premium cycle."""
    global _PERFORMANCE_CACHE, _SHADOW_CACHE
    _PERFORMANCE_CACHE = None
    _SHADOW_CACHE = None


def _performance(bot: Any) -> Dict[str, Any]:
    global _PERFORMANCE_CACHE
    if _PERFORMANCE_CACHE is None:
        try:
            data = bot.load_performance()
        except Exception:
            data = {}
        _PERFORMANCE_CACHE = data if isinstance(data, dict) else {}
    return _PERFORMANCE_CACHE


def _shadow_state(state_file: str = PUMP_STATE_FILE) -> Dict[str, Any]:
    global _SHADOW_CACHE
    if state_file == PUMP_STATE_FILE and _SHADOW_CACHE is not None:
        return _SHADOW_CACHE
    data = _load_json(state_file)
    if state_file == PUMP_STATE_FILE:
        _SHADOW_CACHE = data
    return data


def recent_tp3_context(
    bot: Any,
    symbol: str,
    *,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """Return the newest TP3 close for symbol if it is still inside cooldown.

    We search all stored days instead of only today so reversals around midnight
    are not lost. closed_times already stores epoch seconds, so this is safe.
    """
    now = int(now_ts if now_ts is not None else time.time())
    wanted = str(symbol or "").upper()
    days = (_performance(bot).get("days") or {})
    if not isinstance(days, dict):
        return None

    best_day: Optional[Dict[str, Any]] = None
    best_closed_at = 0
    best_result = ""

    for day in days.values():
        if not isinstance(day, dict):
            continue
        closed_at = int(_sf((day.get("closed_times") or {}).get(wanted), 0) or 0)
        if closed_at <= best_closed_at:
            continue
        result = str((day.get("closed_results") or {}).get(wanted) or "").upper()
        best_closed_at = closed_at
        best_result = result
        best_day = day

    if best_day is None or best_closed_at <= 0 or best_result != "TP3":
        return None

    age = now - best_closed_at
    if age < MIN_SECONDS_AFTER_TP3 or age >= MAX_REVERSAL_WINDOW_SECONDS:
        return None

    direction = ""
    history_row: Dict[str, Any] = {}
    history = best_day.get("closed_history") or []
    if isinstance(history, list):
        for row in reversed(history):
            if not isinstance(row, dict):
                continue
            if str(row.get("symbol") or "").upper() != wanted:
                continue
            if str(row.get("result") or "").upper() != "TP3":
                continue
            candidate_direction = str(row.get("direction") or "").upper()
            if candidate_direction not in {"LONG", "SHORT"}:
                continue
            direction = candidate_direction
            history_row = row
            break

    if direction not in {"LONG", "SHORT"}:
        return None

    return {
        "symbol": wanted,
        "closed_at": best_closed_at,
        "age_seconds": age,
        "result": "TP3",
        "direction": direction,
        "opposite_direction": "SHORT" if direction == "LONG" else "LONG",
        "entry": _sf(history_row.get("entry")),
        "exit": _sf(history_row.get("exit")),
        "score": int(_sf(history_row.get("score"), 0) or 0),
        "source": str(history_row.get("source") or ""),
    }


def latest_opposite_shadow_event(
    symbol: str,
    direction: str,
    closed_at: int,
    *,
    state_file: str = PUMP_STATE_FILE,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    now = int(now_ts if now_ts is not None else time.time())
    wanted = str(symbol or "").upper()
    wanted_direction = str(direction or "").upper()
    rows = _shadow_state(state_file).get("shadow_moves") or []
    if not isinstance(rows, list):
        return None

    for row in reversed(rows):
        if not isinstance(row, dict):
            continue
        if str(row.get("symbol") or "").upper() != wanted:
            continue
        if str(row.get("direction") or "").upper() != wanted_direction:
            continue
        recorded_at = int(_sf(row.get("recorded_at"), 0) or 0)
        if recorded_at <= int(closed_at or 0):
            continue
        if now - recorded_at > MAX_SHADOW_EVENT_AGE_SECONDS:
            continue
        return row
    return None


def should_probe_reversal(
    bot: Any,
    symbol: str,
    *,
    state_file: str = PUMP_STATE_FILE,
    now_ts: Optional[int] = None,
) -> bool:
    context = recent_tp3_context(bot, symbol, now_ts=now_ts)
    if context is None:
        return False
    event = latest_opposite_shadow_event(
        symbol,
        context["opposite_direction"],
        context["closed_at"],
        state_file=state_file,
        now_ts=now_ts,
    )
    return event is not None


def make_recent_closed_prefilter(bot: Any, original: Any):
    """Keep legacy cooldown except when a real opposite TP3 reversal is brewing."""
    def wrapped(symbol: str) -> bool:
        blocked = bool(original(symbol))
        if not blocked:
            return False
        if should_probe_reversal(bot, symbol):
            print(symbol, "TP3 sonrası ters yön adayı var; yön-duyarlı cooldown istisnası açıldı.")
            return False
        return True

    return wrapped


def _frame(df: Any) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy") or len(df) < 60:
        return None
    frame = df.copy()
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(frame.columns)):
        return None
    for col in needed:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    if len(frame) < 60:
        return None

    frame["ema20"] = EMAIndicator(frame["close"], window=20).ema_indicator()
    frame["ema50"] = EMAIndicator(frame["close"], window=50).ema_indicator()
    frame["rsi"] = RSIIndicator(frame["close"], window=14).rsi()
    frame["adx"] = ADXIndicator(frame["high"], frame["low"], frame["close"], window=14).adx()
    frame["atr"] = AverageTrueRange(
        frame["high"], frame["low"], frame["close"], window=14
    ).average_true_range()
    frame["ema20_slope"] = frame["ema20"] - frame["ema20"].shift(3)
    frame["volume_avg"] = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / frame["volume_avg"]
    frame = frame.dropna().reset_index(drop=True)
    return frame if len(frame) >= 10 else None


def _hard_opposing(row: pd.Series, direction: str, *, four_hour: bool) -> bool:
    adx_limit = 32 if four_hour else 28
    if direction == "LONG":
        return bool(
            row["close"] < row["ema20"] < row["ema50"]
            and row["ema20_slope"] < 0
            and row["adx"] >= adx_limit
            and row["rsi"] < (40 if four_hour else 42)
        )
    return bool(
        row["close"] > row["ema20"] > row["ema50"]
        and row["ema20_slope"] > 0
        and row["adx"] >= adx_limit
        and row["rsi"] > (60 if four_hour else 58)
    )


def _entry_support_points(row: pd.Series, direction: str, entry: float) -> int:
    if direction == "LONG":
        return sum(
            [
                int(entry > row["ema20"]),
                int(row["ema20_slope"] > 0),
                int(row["rsi"] >= 47),
                int(row["adx"] >= 15),
            ]
        )
    return sum(
        [
            int(entry < row["ema20"]),
            int(row["ema20_slope"] < 0),
            int(row["rsi"] <= 53),
            int(row["adx"] >= 15),
        ]
    )


def _shadow_event_quality(event: Dict[str, Any], direction: str) -> bool:
    move15 = _sf(event.get("move15_percent"), 0.0) or 0.0
    abs_move15 = abs(move15)
    if not (MIN_MOVE15_PERCENT <= abs_move15 <= MAX_MOVE15_PERCENT):
        return False
    if direction == "LONG" and move15 <= 0:
        return False
    if direction == "SHORT" and move15 >= 0:
        return False

    price = _sf(event.get("price"))
    ema20 = _sf(event.get("ema20"))
    slope = _sf(event.get("ema20_slope_percent"), 0.0) or 0.0
    distance = abs(_sf(event.get("ema20_distance_percent"), 999.0) or 999.0)
    if not price or not ema20 or price <= 0 or ema20 <= 0:
        return False
    if distance > MAX_EVENT_EMA_DISTANCE_PERCENT:
        return False
    if direction == "LONG" and not (price > ema20 and slope > 0):
        return False
    if direction == "SHORT" and not (price < ema20 and slope < 0):
        return False

    rsi5 = _sf(event.get("rsi5"), 50.0) or 50.0
    if direction == "LONG" and not (52 <= rsi5 <= 69):
        return False
    if direction == "SHORT" and not (31 <= rsi5 <= 48):
        return False

    directional_count = int(
        _sf(event.get("green_5m_count" if direction == "LONG" else "red_5m_count"), 0)
        or 0
    )
    if directional_count < 2:
        return False

    if not (bool(event.get("resume_confirmed")) or bool(event.get("shadow_ready"))):
        return False

    trend_missing = event.get("trend_missing")
    if not isinstance(trend_missing, list) or trend_missing:
        return False

    vol1 = _sf(event.get("vol1"), 0.0) or 0.0
    vol5 = _sf(event.get("vol5"), 0.0) or 0.0
    volume_wake = vol5 >= MIN_VOL5_NORMAL or (
        vol1 >= MIN_VOL1_WAKE and vol5 >= MIN_VOL5_WITH_1M_WAKE
    )
    return bool(volume_wake)


def _score(
    context: Dict[str, Any],
    event: Dict[str, Any],
    support_points: int,
) -> int:
    score = 93
    score += 2 if context.get("result") == "TP3" else 0
    score += 2 if bool(event.get("resume_confirmed")) else 0
    score += 1 if bool(event.get("shadow_ready")) else 0
    score += 2 if not (event.get("trend_missing") or []) else 0

    vol1 = _sf(event.get("vol1"), 0.0) or 0.0
    vol5 = _sf(event.get("vol5"), 0.0) or 0.0
    if vol5 >= 1.25 or vol1 >= 2.0:
        score += 2
    elif vol5 >= MIN_VOL5_NORMAL or vol1 >= MIN_VOL1_WAKE:
        score += 1

    direction = str(event.get("direction") or "").upper()
    count = int(
        _sf(event.get("green_5m_count" if direction == "LONG" else "red_5m_count"), 0)
        or 0
    )
    if count >= 3:
        score += 1
    if support_points >= 3:
        score += 1
    if abs(_sf(event.get("ema20_distance_percent"), 999.0) or 999.0) <= 0.65:
        score += 1
    if 0.55 <= abs(_sf(event.get("move15_percent"), 0.0) or 0.0) <= 1.10:
        score += 1
    return min(100, score)


def analyze_reversal(
    bot: Any,
    symbol: str,
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any = None,
    *,
    state_file: str = PUMP_STATE_FILE,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    now = int(now_ts if now_ts is not None else time.time())
    context = recent_tp3_context(bot, symbol, now_ts=now)
    if context is None:
        return None

    direction = context["opposite_direction"]
    event = latest_opposite_shadow_event(
        symbol,
        direction,
        context["closed_at"],
        state_file=state_file,
        now_ts=now,
    )
    if event is None or not _shadow_event_quality(event, direction):
        return None

    f15, f1, f4 = _frame(df15m), _frame(df1h), _frame(df4h)
    if f15 is None or f1 is None or f4 is None:
        return None
    r15, r1, r4 = f15.iloc[-2], f1.iloc[-2], f4.iloc[-2]
    if _hard_opposing(r1, direction, four_hour=False):
        return None
    if _hard_opposing(r4, direction, four_hour=True):
        return None

    event_price = _sf(event.get("price"))
    event_ema20 = _sf(event.get("ema20"))
    entry = _sf(current_price, _sf(f15.iloc[-1]["close"]))
    if not event_price or not event_ema20 or not entry or min(event_price, event_ema20, entry) <= 0:
        return None

    drift = abs(entry - event_price) / event_price * 100.0
    if drift > MAX_EVENT_DRIFT_PERCENT:
        return None

    support_points = _entry_support_points(r15, direction, entry)
    if support_points < 2:
        return None

    atr15 = _sf(r15["atr"], 0.0) or 0.0
    if atr15 <= 0:
        return None
    if direction == "LONG":
        sl = min(
            event_ema20 * (1.0 - STOP_BUFFER_PERCENT / 100.0),
            entry - 0.65 * atr15,
        )
        risk = entry - sl
    else:
        sl = max(
            event_ema20 * (1.0 + STOP_BUFFER_PERCENT / 100.0),
            entry + 0.65 * atr15,
        )
        risk = sl - entry
    if risk <= 0:
        return None
    risk_percent = risk / entry * 100.0
    if not (MIN_RISK_PERCENT <= risk_percent <= MAX_RISK_PERCENT):
        return None

    score = _score(context, event, support_points)
    if score < MIN_SCORE:
        return None

    if direction == "LONG":
        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R
    else:
        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R
    if min(tp1, tp2, tp3, sl) <= 0:
        return None

    quality = "A+ TERS DÖNÜŞ" if score >= 98 else "A TERS DÖNÜŞ"
    move15 = _sf(event.get("move15_percent"), 0.0) or 0.0
    rsi5 = _sf(event.get("rsi5"), 50.0) or 50.0
    vol5 = _sf(event.get("vol5"), 0.0) or 0.0
    vol1 = _sf(event.get("vol1"), 0.0) or 0.0
    prior = context["direction"]

    return {
        "symbol": str(symbol or "").upper(),
        "direction": direction,
        "source": SOURCE,
        "signal_class": "TRADE",
        "entry": round(entry, 12),
        "ideal_entry": round(event_ema20, 12),
        "zone_distance_percent": round(abs(entry - event_ema20) / event_ema20 * 100.0, 3),
        "zone_name": "TP3 sonrası 5M ters yön dönüş bölgesi",
        "tp1": round(tp1, 12),
        "tp2": round(tp2, 12),
        "tp3": round(tp3, 12),
        "sl": round(sl, 12),
        "risk_percent": round(risk_percent, 3),
        "rr_tp1": TP1_R,
        "rr_tp2": TP2_R,
        "rr_tp3": TP3_R,
        "score": score,
        "rsi_15m": round(float(r15["rsi"]), 2),
        "adx_15m": round(float(r15["adx"]), 2),
        "volume_ratio": round(float(r15["volume_ratio"]), 2),
        "adx_1h": round(float(r1["adx"]), 2),
        "adx_4h": round(float(r4["adx"]), 2),
        "quality": quality,
        "quality_note": f"Önceki {prior} işlem TP3 ile bitti; güçlü {direction} dönüşü 5M yapı + hacim + 15M teyitle doğrulandı.",
        "leverage": "1x-2x",
        "trend_reason": f"TP3 sonrası {prior} -> {direction} yön değişimi; 1H/4H sert karşı trend yok",
        "confirm_reason": f"5M resume={bool(event.get('resume_confirmed'))} | vol1 {vol1:.2f}x | vol5 {vol5:.2f}x | RSI5 {rsi5:.1f}",
        "entry_reason": f"Ters yön gölge olayından fiyat sapması %{drift:.2f}",
        "radar_reason": f"Önceki {prior} TP3 sonrası ters yön fırsatını 4 saatlik coin cooldown içinde kaybetmemek",
        "reversal_version": VERSION,
        "reversal_previous_direction": prior,
        "reversal_previous_result": "TP3",
        "reversal_previous_closed_at": context["closed_at"],
        "reversal_event_at": int(_sf(event.get("recorded_at"), 0) or 0),
        "message": (
            "🔄 PREMIUM TERS YÖN SİNYALİ\n\n"
            f"{direction} | {str(symbol or '').upper()}\n"
            f"Önceki {prior} işlem TP3 sonrası güçlü ters yön yakalandı.\n"
            f"15M hareket: %{move15:+.2f} | 5M RSI: {rsi5:.1f}"
        ),
    }


def _promote_existing_reversal(
    signal: Optional[Dict[str, Any]],
    context: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    if not isinstance(signal, dict):
        return None
    direction = str(signal.get("direction") or "").upper()
    if direction != context.get("opposite_direction"):
        return None
    score = int(_sf(signal.get("score"), 0) or 0)
    risk = _sf(signal.get("risk_percent"), 999.0) or 999.0
    if score < MIN_SCORE or not (MIN_RISK_PERCENT <= risk <= MAX_RISK_PERCENT):
        return None

    promoted = dict(signal)
    original_source = str(promoted.get("source") or "")
    promoted["source"] = SOURCE
    promoted["quality"] = "A+ TERS DÖNÜŞ" if score >= 98 else "A TERS DÖNÜŞ"
    promoted["quality_note"] = (
        f"Önceki {context['direction']} TP3 sonrası mevcut Premium kurulumu ters yönde yeniden doğrulandı."
    )
    promoted["reversal_version"] = VERSION
    promoted["reversal_original_source"] = original_source
    promoted["reversal_previous_direction"] = context["direction"]
    promoted["reversal_previous_result"] = "TP3"
    promoted["reversal_previous_closed_at"] = context["closed_at"]
    return promoted


def make_pending_analyzer_factory(runner: Any, base_factory: Any):
    """Wrap Premium analyzer so the legacy cooldown becomes direction-aware."""
    def factory(original: Any, pending_gate: Any):
        base_analyzer = base_factory(original, pending_gate)

        def wrapped(
            symbol: str,
            df15m: Any,
            df1h: Any,
            df4h: Any,
            current_price: Any = None,
        ) -> Any:
            context = recent_tp3_context(runner.bot, symbol)
            base_signal = base_analyzer(
                symbol,
                df15m,
                df1h,
                df4h,
                current_price,
            )

            if context is None:
                return base_signal

            # During the legacy cooldown the previous direction stays blocked.
            promoted = _promote_existing_reversal(base_signal, context)
            if promoted is not None:
                print(
                    "PREMIUM REVERSAL EXISTING:",
                    symbol,
                    context["direction"],
                    "->",
                    promoted.get("direction"),
                    "score=",
                    promoted.get("score"),
                )
                return promoted

            reversal = analyze_reversal(
                runner.bot,
                symbol,
                df15m,
                df1h,
                df4h,
                current_price,
            )
            if reversal is not None:
                print(
                    "PREMIUM REVERSAL CAPTURE:",
                    symbol,
                    context["direction"],
                    "->",
                    reversal.get("direction"),
                    "score=",
                    reversal.get("score"),
                )
                return reversal

            return None

        return wrapped

    return factory


def strong_direct_allowed(
    signal: Dict[str, Any],
    current_price: Any,
    base_validator: Any,
    profit_module: Any,
) -> bool:
    if str(signal.get("source") or "").upper() != SOURCE:
        return False
    if int(_sf(signal.get("score"), 0) or 0) < MIN_SCORE:
        return False
    if str(signal.get("signal_class") or "").upper() != "TRADE":
        return False
    risk = _sf(signal.get("risk_percent"), 999.0) or 999.0
    zone = abs(_sf(signal.get("zone_distance_percent"), 999.0) or 999.0)
    if not (MIN_RISK_PERCENT <= risk <= MAX_RISK_PERCENT):
        return False
    if zone > MAX_EVENT_EMA_DISTANCE_PERCENT:
        return False
    ok, _ = base_validator(signal, current_price)
    if not ok:
        return False
    try:
        return bool(profit_module.cost_viability(signal).get("ok"))
    except Exception:
        return False


def make_profit_gate_factory(runner: Any, base_factory: Any):
    def factory(original: Any, gate: Any, pending_gate: Any):
        base_gate = base_factory(original, gate, pending_gate)

        def wrapped(signal: Dict[str, Any], current_price: Any):
            if str(signal.get("source") or "").upper() == SOURCE:
                direction = str(signal.get("direction") or "").upper()
                evidence = gate.profiles.get(direction, {}) if isinstance(gate.profiles, dict) else {}
                if bool(evidence.get("live_allowed")) and strong_direct_allowed(
                    signal,
                    current_price,
                    original,
                    runner.profit,
                ):
                    signal["premium_confirmation"] = {
                        "version": VERSION,
                        "status": "REVERSAL_DIRECT",
                        "confirmed_at": runner.bot.now_ts(),
                    }
                    signal["profit_mode_v2"] = {
                        "version": runner.profit.VERSION,
                        "decision": "PREMIUM_V4_REVERSAL_DIRECT",
                        "timing": {"mode": "REVERSAL_DIRECT"},
                        "evidence": evidence,
                        "confirmation": signal.get("premium_confirmation"),
                    }
                    print(
                        "PREMIUM V4 REVERSAL DIREKT:",
                        signal.get("symbol"),
                        signal.get("direction"),
                        "score=",
                        signal.get("score"),
                    )
                    return True, "Premium V4 güçlü TP3 sonrası ters yön giriş"

            return base_gate(signal, current_price)

        return wrapped

    return factory


def install(runner: Any) -> None:
    """Install the live reversal route into premium_profit_runner before run()."""
    global _INSTALLED
    if _INSTALLED or getattr(runner, "_premium_reversal_capture_installed", False):
        return

    bot = runner.bot
    bot.has_recent_closed_signal = make_recent_closed_prefilter(
        bot,
        bot.has_recent_closed_signal,
    )
    runner._make_pending_analyzer = make_pending_analyzer_factory(
        runner,
        runner._make_pending_analyzer,
    )
    runner._make_profit_gate = make_profit_gate_factory(
        runner,
        runner._make_profit_gate,
    )

    runner._premium_reversal_capture_installed = True
    _INSTALLED = True
    print(
        "Premium Reversal Capture:",
        VERSION,
        "| TP3 sonrası ters yön: CANLI PREMIUM | aynı yön cooldown: KORUNUYOR | emir: YOK",
    )
