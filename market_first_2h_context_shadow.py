"""Market First V7 2H Context Shadow.

Observational 2H context for the existing V6 live system.

Goals:
- add 2H trend/EMA/RSI/ADX/Supertrend context only after the ordinary system
  has already produced a plan or decision,
- reuse that same 2H snapshot across plan/decision/signal wrappers,
- write metadata into plan/decision/signal ledgers for later cohort analysis,
- never change score, direction, entry, SL/TP, trade eligibility or Telegram.

This module intentionally does NOT act as a live gate.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Tuple

import pandas as pd

import market_first_entry_plan as entry_plan
import market_first_strategy as strategy
import market_first_supertrend_shadow as supertrend_shadow

VERSION = "MARKET_FIRST_2H_CONTEXT_SHADOW_V1_2026_09_25"
STATE_FILE = "market_first_2h_context_shadow.json"

RSI_PERIOD = 14
ADX_PERIOD = 14
MIN_FETCH_BARS = 70
FETCH_BARS = 110

_INSTALLED = False
_RUN_COUNTS: Dict[str, int] = {}
_EXCHANGE_BY_SYMBOL: Dict[str, Any] = {}
_FRAME_CACHE: Dict[str, Any] = {}


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


def _closed_numeric_frame(df: Any) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy"):
        return None
    required = {"high", "low", "close"}
    if not required.issubset(set(df.columns)):
        return None
    frame = df.copy()
    for column in required:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if "volume" in frame.columns:
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=list(required)).reset_index(drop=True)
    if len(frame) < max(RSI_PERIOD, ADX_PERIOD) + 12:
        return None
    # Last exchange candle may still be open.
    frame = frame.iloc[:-1].reset_index(drop=True)
    return frame if len(frame) >= max(RSI_PERIOD, ADX_PERIOD) + 10 else None


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> float:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    if len(avg_gain) == 0:
        return 0.0
    g = _sf(avg_gain.iloc[-1])
    l = _sf(avg_loss.iloc[-1])
    if l <= 0:
        return 100.0 if g > 0 else 50.0
    rs = g / l
    return 100.0 - (100.0 / (1.0 + rs))


def _adx(frame: pd.DataFrame, period: int = ADX_PERIOD) -> Tuple[float, float, float]:
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    plus_smoothed = plus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    minus_smoothed = minus_dm.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    plus_di = 100.0 * plus_smoothed / atr.replace(0, pd.NA)
    minus_di = 100.0 * minus_smoothed / atr.replace(0, pd.NA)
    denom = (plus_di + minus_di).replace(0, pd.NA)
    dx = ((plus_di - minus_di).abs() / denom) * 100.0
    adx = dx.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    return (
        _sf(adx.iloc[-1]) if len(adx) else 0.0,
        _sf(plus_di.iloc[-1]) if len(plus_di) else 0.0,
        _sf(minus_di.iloc[-1]) if len(minus_di) else 0.0,
    )


def evaluate_context(
    direction: str,
    df2h: Any,
    *,
    current_price: float = 0.0,
    structure_1h: Optional[str] = None,
) -> Dict[str, Any]:
    direction = str(direction or "").upper()
    structure = strategy._structure(df2h, current_price)
    frame = _closed_numeric_frame(df2h)
    supertrend = supertrend_shadow.supertrend_snapshot(df2h)

    if not isinstance(structure, Mapping) or frame is None:
        alignment = "NO_2H_DATA"
        return {
            "context_2h_shadow_version": VERSION,
            "context_2h_shadow_only": True,
            "context_2h_alignment": alignment,
            "context_2h_direction": "UNKNOWN",
            "context_2h_1h_alignment": "UNKNOWN",
        }

    d2h = str(structure.get("direction") or "NEUTRAL").upper()
    d1h = str(structure_1h or "UNKNOWN").upper()
    rsi = _rsi(frame["close"].astype(float))
    adx, plus_di, minus_di = _adx(frame)

    st_direction = str((supertrend or {}).get("direction") or "UNKNOWN").upper()
    if direction not in {"LONG", "SHORT"}:
        alignment = "NO_DIRECTION"
    elif d2h == direction:
        alignment = "ALIGNED"
    elif d2h in {"LONG", "SHORT"} and d2h != direction:
        alignment = "OPPOSED"
    else:
        alignment = "NEUTRAL_2H"

    if d1h in {"LONG", "SHORT"} and d2h in {"LONG", "SHORT"}:
        htf_alignment = "ALIGNED" if d1h == d2h else "MIXED"
    elif d1h == "UNKNOWN":
        htf_alignment = "UNKNOWN"
    else:
        htf_alignment = "NEUTRAL"

    if d2h == "LONG":
        di_alignment = "LONG" if plus_di > minus_di else "SHORT"
    elif d2h == "SHORT":
        di_alignment = "SHORT" if minus_di > plus_di else "LONG"
    else:
        di_alignment = "NEUTRAL"

    strength = "STRONG" if adx >= 25 else "MODERATE" if adx >= 18 else "WEAK"

    return {
        "context_2h_shadow_version": VERSION,
        "context_2h_shadow_only": True,
        "context_2h_direction": d2h,
        "context_2h_alignment": alignment,
        "context_2h_1h_alignment": htf_alignment,
        "context_2h_rsi14": round(rsi, 2),
        "context_2h_adx14": round(adx, 2),
        "context_2h_plus_di14": round(plus_di, 2),
        "context_2h_minus_di14": round(minus_di, 2),
        "context_2h_di_alignment": di_alignment,
        "context_2h_strength": strength,
        "context_2h_ema9": round(_sf(structure.get("ema9")), 10),
        "context_2h_ema20": round(_sf(structure.get("ema20")), 10),
        "context_2h_ema50": round(_sf(structure.get("ema50")), 10),
        "context_2h_atr_percent": round(_sf(structure.get("atr_percent")), 4),
        "context_2h_extension_atr": round(_sf(structure.get("extension_atr")), 4),
        "context_2h_volume_ratio": round(_sf(structure.get("volume_ratio")), 3),
        "context_2h_swing_low_12": round(_sf(structure.get("swing_low_12")), 10),
        "context_2h_swing_high_12": round(_sf(structure.get("swing_high_12")), 10),
        "context_2h_range_low_72": round(_sf(structure.get("range_low_72")), 10),
        "context_2h_range_high_72": round(_sf(structure.get("range_high_72")), 10),
        "context_2h_supertrend": st_direction,
        "context_2h_supertrend_line": round(_sf((supertrend or {}).get("line")), 10),
        "context_2h_supertrend_distance_percent": round(
            _sf((supertrend or {}).get("distance_percent")), 4
        ),
        "context_2h_supertrend_aligned": bool(
            direction in {"LONG", "SHORT"} and st_direction == direction
        ),
    }


def _copy_context(source: Mapping[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in source.items():
        if str(key).startswith("context_2h_"):
            target[key] = value
    return target


def _cohort_summary(bot: Any) -> Dict[str, Any]:
    payload = bot.load_json_file("market_first_entry_plan_ledger.json", {})
    episodes = payload.get("episodes", {}) if isinstance(payload, Mapping) else {}
    groups: Dict[str, Dict[str, Any]] = {}
    if not isinstance(episodes, Mapping):
        return groups

    for episode in episodes.values():
        if not isinstance(episode, Mapping):
            continue
        initial = episode.get("initial")
        if not isinstance(initial, Mapping):
            continue
        alignment = str(initial.get("context_2h_alignment") or "")
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


def install(runner: Any) -> None:
    """Install transparent wrappers around existing V6 analysis."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_candidate_frames = runner._candidate_frames
    original_plan_eval = entry_plan.evaluate_entry_plan
    original_promote = entry_plan.promote_to_decision
    original_analyze = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal

    def candidate_frames_with_2h_exchange(exchange, symbol):
        _EXCHANGE_BY_SYMBOL[str(symbol)] = exchange
        return original_candidate_frames(exchange, symbol)

    def get_frame(symbol: str):
        symbol = str(symbol or "")
        if not symbol:
            return None
        if symbol in _FRAME_CACHE:
            return _FRAME_CACHE.get(symbol)
        exchange = _EXCHANGE_BY_SYMBOL.get(symbol)
        if exchange is None:
            return None
        try:
            frame = runner.bot.fetch_df(
                exchange,
                symbol,
                "2h",
                FETCH_BARS,
                min_len=MIN_FETCH_BARS,
            )
        except Exception as exc:
            print("2H CONTEXT SHADOW veri hatası:", symbol, type(exc).__name__, exc)
            frame = None
        _FRAME_CACHE[symbol] = frame
        return frame

    def attach(item: Mapping[str, Any], df1h: Any = None) -> Dict[str, Any]:
        enriched = dict(item)
        symbol = str(item.get("symbol") or "")
        direction = str(item.get("direction") or "")
        current_price = _sf(item.get("current_price"))
        d1h = str(item.get("structure_1h") or "").upper()
        if not d1h and df1h is not None:
            s1h = strategy._structure(df1h, current_price)
            d1h = str((s1h or {}).get("direction") or "UNKNOWN").upper()
        shadow = evaluate_context(
            direction,
            get_frame(symbol),
            current_price=current_price,
            structure_1h=d1h or "UNKNOWN",
        )
        enriched.update(shadow)
        _inc(str(shadow.get("context_2h_alignment") or "UNKNOWN"))
        return enriched

    def plan_eval_with_2h(*args, **kwargs):
        plan, reason = original_plan_eval(*args, **kwargs)
        if not isinstance(plan, Mapping):
            return plan, reason
        return attach(plan, _arg(args, kwargs, "df1h", 3)), reason

    def promote_with_2h(existing, plan):
        decision = original_promote(existing, plan)
        if isinstance(decision, dict) and isinstance(plan, Mapping):
            _copy_context(plan, decision)
        return decision

    def analyze_with_2h(*args, **kwargs):
        decision, reason = original_analyze(*args, **kwargs)
        if not isinstance(decision, Mapping):
            return decision, reason
        return attach(decision, _arg(args, kwargs, "df1h", 4)), reason

    def signal_with_2h(decision):
        signal = original_decision_to_signal(decision)
        if isinstance(signal, dict) and isinstance(decision, Mapping):
            _copy_context(decision, signal)
        return signal

    runner._candidate_frames = candidate_frames_with_2h_exchange
    entry_plan.evaluate_entry_plan = plan_eval_with_2h
    entry_plan.promote_to_decision = promote_with_2h
    runner.analyze_candidate = analyze_with_2h
    runner.decision_to_signal = signal_with_2h


def finish(bot: Any) -> Dict[str, Any]:
    payload = {
        "version": VERSION,
        "mode": "SHADOW_ONLY_NO_TRADE_EFFECT",
        "timeframe": "2h",
        "uses_closed_candles_only": True,
        "rsi_period": RSI_PERIOD,
        "adx_period": ADX_PERIOD,
        "trade_gate": False,
        "score_effect": False,
        "direction_effect": False,
        "stop_target_effect": False,
        "telegram": False,
        "extra_api_scope": "ONLY_AFTER_PLAN_OR_DECISION_EXISTS",
        "run_counts": dict(_RUN_COUNTS),
        "entry_plan_outcome_cohorts": _cohort_summary(bot),
        "note": (
            "2H context is observational only. Promote to a live gate only after "
            "enough real post-install samples show a stable advantage."
        ),
    }
    if hasattr(bot, "save_json_file"):
        bot.save_json_file(STATE_FILE, payload)
    return payload


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": "SHADOW_ONLY",
        "timeframe": "2h",
        "closed_candles_only": True,
        "captures": [
            "trend",
            "EMA9/20/50",
            "RSI14",
            "ADX14/+DI/-DI",
            "Supertrend(10,3)",
            "2H swing/range",
            "1H alignment",
        ],
        "changes_trade_admission": False,
        "changes_score": False,
        "changes_direction": False,
        "changes_stop_or_targets": False,
        "telegram": False,
        "extra_api_scope": "ONLY_AFTER_PLAN_OR_DECISION_EXISTS",
    }
