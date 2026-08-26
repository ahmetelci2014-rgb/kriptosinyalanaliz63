"""Structure-break adapter inspired by the user-provided SmartDCA Pine logic.

Only the market-structure ideas are adapted here: rolling structure breaks,
two-step trend confirmation, trend continuation strength and RSI divergence.
No DCA, pyramiding, position sizing or order logic is copied into the bot.

All live-facing helpers use closed candles only to avoid repaint/lookahead.
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, Optional

import pandas as pd
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange

VERSION = "SMART_STRUCTURE_ADAPTER_V1_2026_08_26"

DEFAULT_ENTRY_PERIOD = int(os.getenv("SMART_STRUCTURE_ENTRY_PERIOD", "60"))
DEFAULT_TREND_PERIOD = int(os.getenv("SMART_STRUCTURE_TREND_PERIOD", "120"))
DEFAULT_RSI_PERIOD = 14
RECENT_DIVERGENCE_BARS = 6


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _clean(df: Any, min_len: int = 30) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy"):
        return None
    frame = df.copy()
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        return None
    for col in required:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    return frame if len(frame) >= min_len else None


def compute_state_frame(
    df: Any,
    *,
    entry_period: int = DEFAULT_ENTRY_PERIOD,
    trend_period: int = DEFAULT_TREND_PERIOD,
    rsi_period: int = DEFAULT_RSI_PERIOD,
) -> pd.DataFrame:
    """Return per-bar non-lookahead structure state.

    The two-step confirmation mirrors the useful idea from the supplied Pine:
    first expansion arms a direction, a later stronger expansion confirms it.
    """
    entry_period = max(5, int(entry_period))
    trend_period = max(entry_period, int(trend_period))
    rsi_period = max(2, int(rsi_period))

    frame = _clean(df, max(trend_period + 5, rsi_period + 5))
    if frame is None:
        return pd.DataFrame()

    frame["rsi"] = RSIIndicator(frame["close"], window=rsi_period).rsi()
    frame["atr"] = AverageTrueRange(
        frame["high"], frame["low"], frame["close"], window=14
    ).average_true_range()

    frame["entry_high"] = frame["high"].rolling(entry_period).max()
    frame["entry_low"] = frame["low"].rolling(entry_period).min()
    frame["rsi_high"] = frame["rsi"].rolling(entry_period).max()
    frame["rsi_low"] = frame["rsi"].rolling(entry_period).min()
    frame["trend_high"] = frame["high"].rolling(trend_period).max()
    frame["trend_low"] = frame["low"].rolling(trend_period).min()

    n = len(frame)
    watch_dir_col = [0] * n
    trend_col = [0] * n
    confirm_long_col = [False] * n
    confirm_short_col = [False] * n
    watch_long_started_col = [False] * n
    watch_short_started_col = [False] * n
    trend_break_count_col = [0] * n

    watch_dir = 0
    base_high = float("nan")
    base_low = float("nan")
    trend = 0
    trend_break_count = 0
    last_break_level = float("nan")
    start = max(trend_period - 1, 2)

    for i in range(start, n):
        high_now = _sf(frame.at[i, "trend_high"], float("nan"))
        low_now = _sf(frame.at[i, "trend_low"], float("nan"))
        high_two = _sf(frame.at[i - 2, "trend_high"], float("nan"))
        low_two = _sf(frame.at[i - 2, "trend_low"], float("nan"))
        if not all(math.isfinite(x) for x in (high_now, low_now, high_two, low_two)):
            watch_dir_col[i] = watch_dir
            trend_col[i] = trend
            trend_break_count_col[i] = trend_break_count
            continue

        if watch_dir == 0:
            if high_now > high_two:
                watch_dir = 1
                base_high = high_now
                watch_long_started_col[i] = True
            elif low_now < low_two:
                watch_dir = -1
                base_low = low_now
                watch_short_started_col[i] = True

        prior_trend = trend

        if watch_dir == 1:
            if math.isfinite(base_high) and high_now > base_high:
                trend = 1
                confirm_long_col[i] = True
                watch_dir = 0
                base_high = float("nan")
            elif low_now < low_two:
                watch_dir = 0
                base_high = float("nan")

        if watch_dir == -1:
            if math.isfinite(base_low) and low_now < base_low:
                trend = -1
                confirm_short_col[i] = True
                watch_dir = 0
                base_low = float("nan")
            elif high_now > high_two:
                watch_dir = 0
                base_low = float("nan")

        if trend != prior_trend:
            trend_break_count = 0
            last_break_level = float("nan")

        if trend == 1:
            if not math.isfinite(last_break_level):
                last_break_level = high_now
            elif high_now > last_break_level:
                trend_break_count += 1
                last_break_level = high_now
        elif trend == -1:
            if not math.isfinite(last_break_level):
                last_break_level = low_now
            elif low_now < last_break_level:
                trend_break_count += 1
                last_break_level = low_now

        watch_dir_col[i] = watch_dir
        trend_col[i] = trend
        trend_break_count_col[i] = trend_break_count

    frame["smart_watch_dir"] = watch_dir_col
    frame["smart_trend"] = trend_col
    frame["smart_confirm_long"] = confirm_long_col
    frame["smart_confirm_short"] = confirm_short_col
    frame["smart_watch_long_started"] = watch_long_started_col
    frame["smart_watch_short_started"] = watch_short_started_col
    frame["smart_trend_break_count"] = trend_break_count_col

    prior_entry_high = frame["entry_high"].shift(1)
    prior_entry_low = frame["entry_low"].shift(1)
    prior_rsi_high = frame["rsi_high"].shift(1)
    prior_rsi_low = frame["rsi_low"].shift(1)

    frame["smart_range_break_long"] = frame["close"] > prior_entry_high
    frame["smart_range_break_short"] = frame["close"] < prior_entry_low
    frame["smart_rsi_div_short"] = (
        (frame["close"] > prior_entry_high) & (frame["rsi"] < prior_rsi_high)
    )
    frame["smart_rsi_div_long"] = (
        (frame["close"] < prior_entry_low) & (frame["rsi"] > prior_rsi_low)
    )
    frame["smart_recent_rsi_div_long"] = (
        frame["smart_rsi_div_long"].rolling(RECENT_DIVERGENCE_BARS, min_periods=1).max().astype(bool)
    )
    frame["smart_recent_rsi_div_short"] = (
        frame["smart_rsi_div_short"].rolling(RECENT_DIVERGENCE_BARS, min_periods=1).max().astype(bool)
    )

    return frame


def latest_features(
    df: Any,
    *,
    entry_period: int = DEFAULT_ENTRY_PERIOD,
    trend_period: int = DEFAULT_TREND_PERIOD,
    exclude_open_candle: bool = True,
) -> Dict[str, Any]:
    frame = _clean(df, max(trend_period + 7, 40))
    if frame is None:
        return {}
    if exclude_open_candle and len(frame) > 1:
        frame = frame.iloc[:-1].copy().reset_index(drop=True)
    states = compute_state_frame(
        frame,
        entry_period=entry_period,
        trend_period=trend_period,
    )
    if states.empty:
        return {}
    row = states.iloc[-1]
    trend = int(_sf(row.get("smart_trend"), 0))
    watch_dir = int(_sf(row.get("smart_watch_dir"), 0))
    return {
        "version": VERSION,
        "entry_period": int(entry_period),
        "trend_period": int(trend_period),
        "trend": trend,
        "watch_dir": watch_dir,
        "confirm_long": bool(row.get("smart_confirm_long")),
        "confirm_short": bool(row.get("smart_confirm_short")),
        "watch_long_started": bool(row.get("smart_watch_long_started")),
        "watch_short_started": bool(row.get("smart_watch_short_started")),
        "range_break_long": bool(row.get("smart_range_break_long")),
        "range_break_short": bool(row.get("smart_range_break_short")),
        "rsi_div_long": bool(row.get("smart_rsi_div_long")),
        "rsi_div_short": bool(row.get("smart_rsi_div_short")),
        "recent_rsi_div_long": bool(row.get("smart_recent_rsi_div_long")),
        "recent_rsi_div_short": bool(row.get("smart_recent_rsi_div_short")),
        "trend_break_count": int(_sf(row.get("smart_trend_break_count"), 0)),
        "rsi": round(_sf(row.get("rsi")), 3),
        "atr": _sf(row.get("atr")),
    }
