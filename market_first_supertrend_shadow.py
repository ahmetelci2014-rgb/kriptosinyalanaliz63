"""Observational 15M + 1H Supertrend shadow for Market First V6.

This module is deliberately non-blocking. It annotates Entry Plan / live decision
metadata with Supertrend(10, 3) calculated from CLOSED candles only, then measures
whether Supertrend alignment correlates with later TP1-first / SL-first outcomes.

It never changes score, direction, entry, stop, targets, trade eligibility or
Telegram delivery.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

import market_first_entry_plan as entry_plan

VERSION = "MARKET_FIRST_SUPERTREND_SHADOW_V1_2026_09_22"
STATE_FILE = "market_first_supertrend_shadow.json"

ATR_PERIOD = 10
MULTIPLIER = 3.0

_INSTALLED = False
_RUN_COUNTS: Dict[str, int] = {}


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _inc(key: str) -> None:
    _RUN_COUNTS[key] = int(_RUN_COUNTS.get(key, 0)) + 1


def _arg(args, kwargs, name: str, index: int, default=None):
    if name in kwargs:
        return kwargs.get(name)
    return args[index] if len(args) > index else default


def _closed_frame(df: Any) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy"):
        return None
    needed = {"high", "low", "close"}
    if not needed.issubset(set(df.columns)):
        return None
    frame = df.copy()
    for column in needed:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(needed)).reset_index(drop=True)
    if len(frame) < ATR_PERIOD + 8:
        return None
    # The final exchange candle may still be forming. Shadow evidence must not
    # look ahead through an unfinished candle.
    frame = frame.iloc[:-1].reset_index(drop=True)
    return frame if len(frame) >= ATR_PERIOD + 6 else None


def supertrend_snapshot(df: Any) -> Optional[Dict[str, Any]]:
    frame = _closed_frame(df)
    if frame is None:
        return None

    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder-style ATR.
    atr = tr.ewm(alpha=1.0 / ATR_PERIOD, adjust=False, min_periods=ATR_PERIOD).mean()
    hl2 = (high + low) / 2.0
    basic_upper = hl2 + MULTIPLIER * atr
    basic_lower = hl2 - MULTIPLIER * atr

    valid = atr.notna()
    valid_indices = list(frame.index[valid])
    if len(valid_indices) < 5:
        return None

    first = valid_indices[0]
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    uptrend = pd.Series(index=frame.index, dtype="object")
    uptrend.loc[first] = bool(close.loc[first] >= hl2.loc[first])

    previous = first
    for idx in valid_indices[1:]:
        if basic_upper.loc[idx] < final_upper.loc[previous] or close.loc[previous] > final_upper.loc[previous]:
            final_upper.loc[idx] = basic_upper.loc[idx]
        else:
            final_upper.loc[idx] = final_upper.loc[previous]

        if basic_lower.loc[idx] > final_lower.loc[previous] or close.loc[previous] < final_lower.loc[previous]:
            final_lower.loc[idx] = basic_lower.loc[idx]
        else:
            final_lower.loc[idx] = final_lower.loc[previous]

        if close.loc[idx] > final_upper.loc[previous]:
            current_uptrend = True
        elif close.loc[idx] < final_lower.loc[previous]:
            current_uptrend = False
        else:
            current_uptrend = bool(uptrend.loc[previous])
            if current_uptrend and final_lower.loc[idx] < final_lower.loc[previous]:
                final_lower.loc[idx] = final_lower.loc[previous]
            if not current_uptrend and final_upper.loc[idx] > final_upper.loc[previous]:
                final_upper.loc[idx] = final_upper.loc[previous]

        uptrend.loc[idx] = current_uptrend
        previous = idx

    last = valid_indices[-1]
    is_up = bool(uptrend.loc[last])
    line = _sf(final_lower.loc[last] if is_up else final_upper.loc[last])
    price = _sf(close.loc[last])
    atr_last = _sf(atr.loc[last])
    if min(line, price, atr_last) <= 0:
        return None

    return {
        "direction": "LONG" if is_up else "SHORT",
        "line": round(line, 10),
        "close": round(price, 10),
        "atr": round(atr_last, 10),
        "distance_percent": round(abs(price - line) / price * 100.0, 4),
    }


def evaluate_shadow(direction: str, df15m: Any, df1h: Any) -> Dict[str, Any]:
    direction = str(direction or "").upper()
    s15 = supertrend_snapshot(df15m)
    s1h = supertrend_snapshot(df1h)

    d15 = str((s15 or {}).get("direction") or "UNKNOWN")
    d1h = str((s1h or {}).get("direction") or "UNKNOWN")

    if direction not in {"LONG", "SHORT"}:
        alignment = "NO_DIRECTION"
    elif d15 == direction and d1h == direction:
        alignment = "ALIGNED_BOTH"
    elif d15 in {"LONG", "SHORT"} and d1h in {"LONG", "SHORT"} and d15 != direction and d1h != direction:
        alignment = "OPPOSED_BOTH"
    elif "UNKNOWN" in {d15, d1h}:
        alignment = "PARTIAL_DATA"
    else:
        alignment = "MIXED"

    return {
        "supertrend_shadow_version": VERSION,
        "supertrend_shadow_period": ATR_PERIOD,
        "supertrend_shadow_multiplier": MULTIPLIER,
        "supertrend_15m": d15,
        "supertrend_1h": d1h,
        "supertrend_15m_line": _sf((s15 or {}).get("line")),
        "supertrend_1h_line": _sf((s1h or {}).get("line")),
        "supertrend_15m_distance_percent": _sf((s15 or {}).get("distance_percent")),
        "supertrend_1h_distance_percent": _sf((s1h or {}).get("distance_percent")),
        "supertrend_shadow_alignment": alignment,
        "supertrend_shadow_only": True,
    }


def _attach(item: Mapping[str, Any], df15m: Any, df1h: Any) -> Dict[str, Any]:
    enriched = dict(item)
    shadow = evaluate_shadow(str(item.get("direction") or ""), df15m, df1h)
    enriched.update(shadow)
    _inc(str(shadow.get("supertrend_shadow_alignment") or "UNKNOWN"))
    return enriched


def install(runner: Any) -> None:
    """Install transparent metadata wrappers; no admission or presentation changes."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_plan_eval = entry_plan.evaluate_entry_plan
    original_promote = entry_plan.promote_to_decision
    original_analyze = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal

    def plan_eval_with_supertrend(*args, **kwargs):
        plan, reason = original_plan_eval(*args, **kwargs)
        if not isinstance(plan, Mapping):
            return plan, reason
        return _attach(
            plan,
            _arg(args, kwargs, "df15m", 2),
            _arg(args, kwargs, "df1h", 3),
        ), reason

    def promote_with_supertrend(existing, plan):
        decision = original_promote(existing, plan)
        if isinstance(decision, dict) and isinstance(plan, Mapping):
            for key, value in plan.items():
                if str(key).startswith("supertrend_"):
                    decision[key] = value
        return decision

    def analyze_with_supertrend(*args, **kwargs):
        decision, reason = original_analyze(*args, **kwargs)
        if not isinstance(decision, Mapping):
            return decision, reason
        return _attach(
            decision,
            _arg(args, kwargs, "df15m", 3),
            _arg(args, kwargs, "df1h", 4),
        ), reason

    def decision_to_signal_with_supertrend(decision):
        signal = original_decision_to_signal(decision)
        if isinstance(signal, dict) and isinstance(decision, Mapping):
            for key, value in decision.items():
                if str(key).startswith("supertrend_"):
                    signal[key] = value
        return signal

    entry_plan.evaluate_entry_plan = plan_eval_with_supertrend
    entry_plan.promote_to_decision = promote_with_supertrend
    runner.analyze_candidate = analyze_with_supertrend
    runner.decision_to_signal = decision_to_signal_with_supertrend


def _cohort_summary(bot: Any) -> Dict[str, Any]:
    payload = bot.load_json_file("market_first_entry_plan_ledger.json", {})
    episodes = payload.get("episodes", {}) if isinstance(payload, Mapping) else {}
    groups: Dict[str, Dict[str, Any]] = {}

    if not isinstance(episodes, Mapping):
        episodes = {}

    for episode in episodes.values():
        if not isinstance(episode, Mapping):
            continue
        initial = episode.get("initial")
        if not isinstance(initial, Mapping):
            continue
        alignment = str(initial.get("supertrend_shadow_alignment") or "")
        if not alignment:
            continue

        group = groups.setdefault(
            alignment,
            {
                "total": 0,
                "resolved": 0,
                "entry_signal_sent": 0,
                "tp1_first": 0,
                "sl_first": 0,
                "ambiguous_same_bar": 0,
            },
        )
        group["total"] += 1
        if bool(episode.get("resolved")):
            group["resolved"] += 1
        if bool(episode.get("entry_signal_sent")):
            group["entry_signal_sent"] += 1

        event = str(episode.get("first_decisive_event") or "")
        if event == "TP1_FIRST":
            group["tp1_first"] += 1
        elif event == "SL_FIRST":
            group["sl_first"] += 1
        elif event == "AMBIGUOUS_SAME_BAR":
            group["ambiguous_same_bar"] += 1

    for group in groups.values():
        decided = int(group["tp1_first"]) + int(group["sl_first"])
        group["decided_ex_ambiguous"] = decided
        group["tp1_first_rate_decided_ex_ambiguous"] = (
            round(group["tp1_first"] / decided, 4) if decided else 0.0
        )

    return groups


def finish(bot: Any) -> Dict[str, Any]:
    payload = {
        "version": VERSION,
        "mode": "SHADOW_ONLY_NO_TRADE_EFFECT",
        "atr_period": ATR_PERIOD,
        "multiplier": MULTIPLIER,
        "timeframes": ["15m", "1h"],
        "uses_closed_candles_only": True,
        "trade_gate": False,
        "score_effect": False,
        "telegram": False,
        "run_counts": dict(_RUN_COUNTS),
        "entry_plan_outcome_cohorts": _cohort_summary(bot),
        "note": (
            "Supertrend is observational only. Compare ALIGNED_BOTH/MIXED/"
            "OPPOSED_BOTH TP1-first vs SL-first after enough new samples."
        ),
    }
    if hasattr(bot, "save_json_file"):
        bot.save_json_file(STATE_FILE, payload)
    return payload


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": "SHADOW_ONLY",
        "atr_period": ATR_PERIOD,
        "multiplier": MULTIPLIER,
        "timeframes": ["15m", "1h"],
        "closed_candles_only": True,
        "changes_trade_admission": False,
        "telegram": False,
    }
