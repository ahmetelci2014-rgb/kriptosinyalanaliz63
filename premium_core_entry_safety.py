"""Premium Core-Only scan and pre-signal extension safety.

Two narrow protections:
1) The live Premium scan is restricted to verified crypto USDT perpetuals.
   Commodity/equity/RWA perpetuals (for example NG/TSM/RKLB) fail closed.
2) A 15M_ENTRY cannot be sent after the directional leg has already extended
   materially away from its EMA20 reclaim origin before the signal exists.

This module does not create a new entry route, does not place orders, and does
not promote any shadow/early strategy to live trading.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, Optional

import pandas as pd

import crypto_universe_guard as universe_guard

VERSION = "PREMIUM_CORE_ENTRY_SAFETY_V1_2026_08_26"

# Pre-signal no-chase thresholds. The soft rule requires both age and extension;
# the hard rule catches extreme extensions even a little earlier.
SOFT_MAX_EXTENSION_ATR = 2.20
SOFT_MIN_BARS_SINCE_LAUNCH = 5
HARD_MAX_EXTENSION_ATR = 3.00
HARD_MIN_BARS_SINCE_LAUNCH = 3
AGED_MAX_EXTENSION_ATR = 1.60
AGED_MIN_BARS_SINCE_LAUNCH = 9
EXHAUSTION_MIN_EXTENSION_ATR = 1.60
EXHAUSTION_MIN_BARS = 4
LONG_EXHAUSTION_RSI = 65.0
SHORT_EXHAUSTION_RSI = 35.0
LAUNCH_LOOKBACK_BARS = 32


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _closed_frame(df15m: Any) -> Optional[pd.DataFrame]:
    if df15m is None or not hasattr(df15m, "copy") or len(df15m) < 24:
        return None
    frame = df15m.copy()
    needed = {"open", "high", "low", "close"}
    if not needed.issubset(set(frame.columns)):
        return None
    for col in needed:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=list(needed)).reset_index(drop=True)
    if len(frame) < 24:
        return None

    # The final row is the currently forming 15M candle in the live scanner.
    closed = frame.iloc[:-1].copy().reset_index(drop=True)
    if len(closed) < 22:
        return None

    closed["ema20_guard"] = closed["close"].ewm(span=20, adjust=False).mean()
    prev_close = closed["close"].shift(1)
    tr = pd.concat(
        [
            closed["high"] - closed["low"],
            (closed["high"] - prev_close).abs(),
            (closed["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    closed["atr14_guard"] = tr.rolling(14, min_periods=14).mean()
    return closed


def pre_signal_extension_context(
    direction: str,
    df15m: Any,
    current_price: Any,
    rsi_15m: Any = None,
) -> Dict[str, Any]:
    direction = str(direction or "").upper()
    live = _sf(current_price)
    frame = _closed_frame(df15m)
    if direction not in {"LONG", "SHORT"} or live is None or live <= 0 or frame is None:
        return {"mode": "UNKNOWN", "reason": "PRE_SIGNAL_DATA_YETERSIZ"}

    start = max(1, len(frame) - LAUNCH_LOOKBACK_BARS)
    launch_idx: Optional[int] = None

    for i in range(start, len(frame)):
        prev_close = _sf(frame.iloc[i - 1]["close"])
        prev_ema = _sf(frame.iloc[i - 1]["ema20_guard"])
        close = _sf(frame.iloc[i]["close"])
        ema = _sf(frame.iloc[i]["ema20_guard"])
        if None in {prev_close, prev_ema, close, ema}:
            continue
        if direction == "LONG" and prev_close <= prev_ema and close > ema:
            launch_idx = i
        elif direction == "SHORT" and prev_close >= prev_ema and close < ema:
            launch_idx = i

    if launch_idx is None:
        return {"mode": "UNKNOWN", "reason": "EMA20_LAUNCH_BULUNAMADI"}

    origin_slice = frame.iloc[max(0, launch_idx - 1) : min(len(frame), launch_idx + 2)]
    if origin_slice.empty:
        return {"mode": "UNKNOWN", "reason": "ORIGIN_BULUNAMADI"}

    if direction == "LONG":
        origin = _sf(origin_slice["low"].min())
        extension_abs = (live - origin) if origin is not None else None
    else:
        origin = _sf(origin_slice["high"].max())
        extension_abs = (origin - live) if origin is not None else None

    atr = _sf(frame.iloc[-1]["atr14_guard"])
    if origin is None or origin <= 0 or atr is None or atr <= 0 or extension_abs is None:
        return {"mode": "UNKNOWN", "reason": "ORIGIN_ATR_YETERSIZ"}

    bars_since = max(0, len(frame) - 1 - launch_idx)
    extension_abs = max(0.0, float(extension_abs))
    extension_atr = extension_abs / atr
    extension_pct = extension_abs / origin * 100.0 if origin > 0 else 0.0
    rsi = _sf(rsi_15m)

    exhausted = bool(
        rsi is not None
        and (
            (direction == "LONG" and rsi >= LONG_EXHAUSTION_RSI)
            or (direction == "SHORT" and rsi <= SHORT_EXHAUSTION_RSI)
        )
    )

    reason = "PRE_SIGNAL_EXTENSION_OK"
    mode = "NORMAL"
    if extension_atr >= HARD_MAX_EXTENSION_ATR and bars_since >= HARD_MIN_BARS_SINCE_LAUNCH:
        mode, reason = "BLOCK", "PRE_SIGNAL_HARD_EXTENSION"
    elif extension_atr >= SOFT_MAX_EXTENSION_ATR and bars_since >= SOFT_MIN_BARS_SINCE_LAUNCH:
        mode, reason = "BLOCK", "PRE_SIGNAL_EXTENSION_TOO_LATE"
    elif extension_atr >= AGED_MAX_EXTENSION_ATR and bars_since >= AGED_MIN_BARS_SINCE_LAUNCH:
        mode, reason = "BLOCK", "PRE_SIGNAL_TREND_TOO_OLD"
    elif (
        exhausted
        and extension_atr >= EXHAUSTION_MIN_EXTENSION_ATR
        and bars_since >= EXHAUSTION_MIN_BARS
    ):
        mode, reason = "BLOCK", "PRE_SIGNAL_EXHAUSTED_CHASE"

    return {
        "mode": mode,
        "reason": reason,
        "direction": direction,
        "origin": round(origin, 10),
        "live": round(live, 10),
        "bars_since_launch": int(bars_since),
        "extension_atr": round(extension_atr, 4),
        "extension_percent": round(extension_pct, 4),
        "rsi_15m": round(rsi, 2) if rsi is not None else None,
        "exhausted": exhausted,
        "version": VERSION,
    }


def make_no_chase_analyzer(original: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(original, "_premium_core_no_chase_wrapped", False):
        return original

    def wrapped(
        symbol: str,
        df15m: Any,
        df1h: Any,
        df4h: Any,
        current_price: Any = None,
    ) -> Any:
        signal = original(symbol, df15m, df1h, df4h, current_price)
        if not isinstance(signal, dict):
            return signal
        if str(signal.get("source") or "").upper() != "15M_ENTRY":
            return signal
        if str(signal.get("signal_class") or "").upper() != "TRADE":
            return signal

        context = pre_signal_extension_context(
            str(signal.get("direction") or ""),
            df15m,
            current_price if _sf(current_price) else signal.get("entry"),
            signal.get("rsi_15m"),
        )
        signal["pre_signal_extension_guard"] = context
        if context.get("mode") == "BLOCK":
            print(
                "CORE PRE-SIGNAL NO-CHASE BLOCK:",
                symbol,
                signal.get("direction"),
                context.get("reason"),
                "origin=",
                context.get("origin"),
                "bars=",
                context.get("bars_since_launch"),
                "extensionATR=",
                context.get("extension_atr"),
                "extension%=",
                context.get("extension_percent"),
            )
            return None
        return signal

    wrapped._premium_core_no_chase_wrapped = True  # type: ignore[attr-defined]
    return wrapped


class _FilteredExchangeProxy:
    def __init__(self, exchange: Any, filtered_markets: Dict[str, Any]):
        self._exchange = exchange
        self._filtered_markets = filtered_markets

    def load_markets(self):
        return self._filtered_markets

    def __getattr__(self, name: str) -> Any:
        return getattr(self._exchange, name)


def make_crypto_only_universe(original: Callable[..., Any]) -> Callable[..., Any]:
    if getattr(original, "_premium_core_crypto_only_wrapped", False):
        return original

    def wrapped(
        exchange: Any,
        priority_coins: Any,
        min_quote_volume: float,
        max_scan_coins: int,
    ) -> Any:
        markets = exchange.load_markets()
        universe_guard.refresh_account_tradable_futures_from_env()
        filtered, excluded = universe_guard.filter_crypto_markets(markets)
        proxy = _FilteredExchangeProxy(exchange, filtered)
        symbols = original(
            proxy,
            priority_coins,
            min_quote_volume,
            max_scan_coins,
        )
        symbols = list(symbols or [])
        verified = [
            str(symbol).upper()
            for symbol in symbols
            if universe_guard.is_verified_live_futures_symbol(str(symbol).upper())
        ]
        removed = [str(symbol).upper() for symbol in symbols if str(symbol).upper() not in set(verified)]
        print(
            "PREMIUM CORE CRYPTO-ONLY:",
            "input_markets=",
            len(markets),
            "| filtered_markets=",
            len(filtered),
            "| metadata_excluded=",
            len(excluded),
            "| scanner_removed=",
            removed[:20],
            "| live_symbols=",
            len(verified),
        )
        return verified

    wrapped._premium_core_crypto_only_wrapped = True  # type: ignore[attr-defined]
    return wrapped


def install(base_runner: Any) -> None:
    """Install both narrow safety patches before ``base_runner.run()``."""
    base_runner.bot.analyze_mtf_trade = make_no_chase_analyzer(
        base_runner.bot.analyze_mtf_trade
    )
    base_runner.allcoins.build_scan_universe = make_crypto_only_universe(
        base_runner.allcoins.build_scan_universe
    )
