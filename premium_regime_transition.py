"""Premium Regime Transition live route.

Captures the kind of move the user actually cares about: a multi-hour / multi-day
directional regime beginning early, and the later LONG<->SHORT reversal when the
old move exhausts.

The route is deliberately independent from the old post-TP3 reversal exception.
It uses the already-running Movement Start V2 direction/stage as the micro trigger
but asks a different question before promoting it live:

1) Is this an early 4H/1H regime start?
2) Or is an extended opposite regime now failing and reversing?

No exchange orders are opened. Promoted candidates still pass the existing
Premium entry validator, cost viability, duplicate/open-risk/portfolio layers.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, Optional, Tuple

import pandas as pd

import strategy


VERSION = "PREMIUM_REGIME_TRANSITION_V1_2026_08_24"
SOURCE = "REGIME_TRANSITION_ENTRY"
STATE_FILE = "premium_regime_transition_state.json"

MODE_TREND_START = "TREND_START"
MODE_REVERSAL = "TREND_REVERSAL"

MIN_BASE_SCORE = 78
MIN_DIRECTION_GAP = 14
MIN_LIVE_SCORE = 96
MIN_RISK_PERCENT = 0.45
MAX_RISK_PERCENT = 2.20
MAX_START_DELAY_PERCENT = 6.0
MAX_REVERSAL_PULLBACK_PERCENT = 6.5
MIN_PRIOR_REVERSAL_MOVE_PERCENT = 4.5
MIN_REVERSAL_EXTENSION_ATR = 1.55
MAX_RECORDS = 1800
KEEP_SECONDS = 21 * 24 * 60 * 60

_STATE: Optional[Dict[str, Any]] = None
_STATE_PATH = STATE_FILE
_DIRTY = False


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".regime_transition.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
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


def _default_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": 0,
        "records": [],
        "summary": {},
    }


def begin(path: str = STATE_FILE) -> None:
    global _STATE, _STATE_PATH, _DIRTY
    _STATE_PATH = path
    _DIRTY = False
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = _default_state()
    except Exception:
        data = _default_state()
    data.setdefault("records", [])
    data.setdefault("summary", {})
    data["version"] = VERSION
    _STATE = data


def _state() -> Dict[str, Any]:
    global _STATE
    if _STATE is None:
        begin()
    return _STATE if isinstance(_STATE, dict) else _default_state()


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1.0 / window, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1.0 / window, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    return (100.0 - 100.0 / (1.0 + rs)).fillna(50.0)


def _atr(frame: pd.DataFrame, window: int = 14) -> pd.Series:
    close = frame["close"]
    previous = close.shift(1)
    tr = pd.concat(
        [
            (frame["high"] - frame["low"]).abs(),
            (frame["high"] - previous).abs(),
            (frame["low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False).mean()


def _enrich(df: Any, min_len: int = 55) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy"):
        return None
    frame = df.copy()
    required = {"open", "high", "low", "close", "volume"}
    if not required.issubset(set(frame.columns)):
        return None
    for col in required:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna().reset_index(drop=True)
    if len(frame) < min_len:
        return None

    frame["ema20"] = frame["close"].ewm(span=20, adjust=False).mean()
    frame["ema50"] = frame["close"].ewm(span=50, adjust=False).mean()
    frame["ema20_slope"] = frame["ema20"] - frame["ema20"].shift(3)
    frame["rsi"] = _rsi(frame["close"], 14)
    frame["atr"] = _atr(frame, 14)
    volume_avg = frame["volume"].rolling(20).mean()
    frame["volume_ratio"] = frame["volume"] / volume_avg.replace(0, float("nan"))
    frame = frame.dropna().reset_index(drop=True)
    return frame if len(frame) >= 20 else None


def _closed_row(frame: pd.DataFrame, offset: int = 1) -> pd.Series:
    # Last row may be the currently forming candle.
    return frame.iloc[-1 - int(offset)]


def _directional_percent(direction: str, start: float, end: float) -> float:
    if min(start, end) <= 0:
        return 0.0
    raw = (end - start) / start * 100.0
    return raw if direction == "LONG" else -raw


def _wick_ratio(row: pd.Series, direction: str) -> float:
    high = float(row["high"])
    low = float(row["low"])
    open_ = float(row["open"])
    close = float(row["close"])
    span = high - low
    if span <= 0:
        return 0.0
    if direction == "SHORT":
        return max(0.0, high - max(open_, close)) / span
    return max(0.0, min(open_, close) - low) / span


def _support_points(frame: pd.DataFrame, direction: str) -> int:
    row = _closed_row(frame, 1)
    prev = _closed_row(frame, 2)
    sign = 1.0 if direction == "LONG" else -1.0
    rsi = float(row["rsi"])
    return sum(
        [
            int(sign * (float(row["close"]) - float(row["ema20"])) > 0),
            int(sign * float(row["ema20_slope"]) > 0),
            int(sign * (float(row["close"]) - float(prev["close"])) > 0),
            int(rsi >= 48.0 if direction == "LONG" else rsi <= 52.0),
        ]
    )


def _four_hour_state(frame: pd.DataFrame, direction: str) -> Dict[str, Any]:
    row = _closed_row(frame, 1)
    prev = _closed_row(frame, 2)
    sign = 1.0 if direction == "LONG" else -1.0
    close = float(row["close"])
    ema20 = float(row["ema20"])
    ema50 = float(row["ema50"])
    slope = float(row["ema20_slope"])
    prev_slope = float(prev["ema20_slope"])
    rsi = float(row["rsi"])
    atr = max(float(row["atr"]), 1e-12)

    aligned = bool(
        sign * (close - ema20) > 0
        and sign * slope > 0
        and (rsi >= 48.0 if direction == "LONG" else rsi <= 52.0)
    )
    turning = bool(
        sign * (close - ema20) > 0
        and sign * (slope - prev_slope) > 0
        and (rsi >= 45.0 if direction == "LONG" else rsi <= 55.0)
    )
    hard_opposing = bool(
        sign * (close - ema20) < 0
        and sign * (ema20 - ema50) < 0
        and sign * slope < 0
        and (rsi < 40.0 if direction == "LONG" else rsi > 60.0)
    )
    return {
        "aligned": aligned,
        "turning": turning,
        "hard_opposing": hard_opposing,
        "close": close,
        "ema20": ema20,
        "ema50": ema50,
        "slope": slope,
        "rsi": rsi,
        "atr": atr,
        "atr_distance": abs(close - ema20) / atr,
    }


def _recent_origin(frame4: pd.DataFrame, direction: str) -> Tuple[float, int]:
    # Use the most recent 48h swing, not an old multi-week extreme. This makes
    # "early" mean early in the current regime, which is what the live signal
    # needs.
    closed = frame4.iloc[:-1].tail(12)
    if closed.empty:
        return 0.0, 0
    column = "low" if direction == "LONG" else "high"
    idx = int(
        closed[column].astype(float).idxmin()
        if direction == "LONG"
        else closed[column].astype(float).idxmax()
    )
    row = frame4.loc[idx]
    timestamp = int(float(row["time"]) / 1000) if "time" in frame4.columns else 0
    return float(row[column]), timestamp


def _opposite_reversal_context(
    frame4: pd.DataFrame,
    direction: str,
    current_price: float,
) -> Optional[Dict[str, Any]]:
    """Find an extended opposite move whose newest extreme is now failing."""
    closed = frame4.iloc[:-1].tail(12).copy()
    if len(closed) < 8 or current_price <= 0:
        return None

    if direction == "SHORT":
        extreme_idx = int(closed["high"].astype(float).idxmax())
        extreme_row = frame4.loc[extreme_idx]
        position = list(closed.index).index(extreme_idx)
        before = closed.iloc[: position + 1]
        if len(before) < 3:
            return None
        origin = float(before["low"].min())
        extreme = float(extreme_row["high"])
        prior_move = (extreme - origin) / origin * 100.0 if origin > 0 else 0.0
        pullback = (extreme - current_price) / extreme * 100.0 if extreme > 0 else 0.0
        previous_direction = "LONG"
    else:
        extreme_idx = int(closed["low"].astype(float).idxmin())
        extreme_row = frame4.loc[extreme_idx]
        position = list(closed.index).index(extreme_idx)
        before = closed.iloc[: position + 1]
        if len(before) < 3:
            return None
        origin = float(before["high"].max())
        extreme = float(extreme_row["low"])
        prior_move = (origin - extreme) / origin * 100.0 if origin > 0 else 0.0
        pullback = (current_price - extreme) / extreme * 100.0 if extreme > 0 else 0.0
        previous_direction = "SHORT"

    bars_since_extreme = len(closed) - 1 - position
    if bars_since_extreme > 3:
        return None

    atr = max(float(extreme_row["atr"]), 1e-12)
    ema20 = float(extreme_row["ema20"])
    if direction == "SHORT":
        extension_atr = max(0.0, (extreme - ema20) / atr)
        pullback_atr = max(0.0, (extreme - current_price) / atr)
    else:
        extension_atr = max(0.0, (ema20 - extreme) / atr)
        pullback_atr = max(0.0, (current_price - extreme) / atr)

    wick = _wick_ratio(extreme_row, direction)
    exhausted = bool(
        prior_move >= MIN_PRIOR_REVERSAL_MOVE_PERCENT
        or extension_atr >= MIN_REVERSAL_EXTENSION_ATR
    )
    rejected = bool(
        pullback_atr >= 0.65
        or (wick >= 0.18 and pullback_atr >= 0.30)
    )
    if not exhausted or not rejected:
        return None

    return {
        "previous_direction": previous_direction,
        "origin_price": origin,
        "extreme_price": extreme,
        "prior_move_percent": prior_move,
        "pullback_percent": pullback,
        "extension_atr": extension_atr,
        "pullback_atr": pullback_atr,
        "rejection_wick_ratio": wick,
        "bars_since_extreme": bars_since_extreme,
        "exhausted": exhausted,
        "rejected": rejected,
    }


def _volume_support(base_result: Dict[str, Any]) -> Tuple[bool, Dict[str, float]]:
    features = base_result.get("features") if isinstance(base_result.get("features"), dict) else {}
    volume_ratio = _sf(features.get("volume_ratio"), 0.0) or 0.0
    volume_wake = _sf(features.get("volume_wake"), 0.0) or 0.0
    supported = bool(volume_ratio >= 1.10 or volume_wake >= 1.10)
    return supported, {
        "volume_ratio": volume_ratio,
        "volume_wake": volume_wake,
    }


def _score_start(
    *,
    stage: str,
    base_score: int,
    gap: int,
    p15: int,
    p1: int,
    state4: Dict[str, Any],
    volume_ok: bool,
    delay_percent: float,
    internal_break: bool,
) -> int:
    score = 82
    score += 4 if stage == "TRIGGER" else 2
    score += min(4, max(0, (base_score - 78) // 5))
    score += 2 if gap >= 20 else 0
    score += 2 if p15 >= 4 else (1 if p15 >= 3 else 0)
    score += 3 if p1 >= 4 else (1 if p1 >= 3 else 0)
    score += 4 if state4.get("aligned") else (2 if state4.get("turning") else 0)
    score += 2 if volume_ok else 0
    score += 2 if delay_percent <= 2.0 else (1 if delay_percent <= 4.0 else 0)
    score += 1 if internal_break else 0
    return max(0, min(100, int(score)))


def _score_reversal(
    *,
    stage: str,
    base_score: int,
    gap: int,
    p15: int,
    p1: int,
    volume_ok: bool,
    reversal: Dict[str, Any],
    internal_break: bool,
) -> int:
    score = 83
    score += 4 if stage == "TRIGGER" else 2
    score += min(4, max(0, (base_score - 78) // 5))
    score += 2 if gap >= 20 else 0
    score += 2 if p15 >= 4 else (1 if p15 >= 3 else 0)
    score += 3 if p1 >= 4 else (1 if p1 >= 3 else 0)
    score += 3 if float(reversal.get("prior_move_percent") or 0.0) >= 7.0 else 2
    score += 2 if float(reversal.get("pullback_atr") or 0.0) >= 0.90 else 1
    score += 2 if float(reversal.get("rejection_wick_ratio") or 0.0) >= 0.22 else 0
    score += 2 if volume_ok else 0
    score += 1 if internal_break else 0
    return max(0, min(100, int(score)))


def _pick_stop(
    direction: str,
    current_price: float,
    base_stop: float,
    frame15: pd.DataFrame,
) -> Tuple[Optional[float], Optional[float]]:
    row15 = _closed_row(frame15, 1)
    atr15 = max(float(row15["atr"]), 1e-12)
    recent = frame15.iloc[:-1].tail(6)
    if direction == "LONG":
        structure = float(recent["low"].min()) - 0.10 * atr15
        raw = [value for value in (base_stop, structure) if 0 < value < current_price]
        candidates = sorted(set(raw))
    else:
        structure = float(recent["high"].max()) + 0.10 * atr15
        raw = [value for value in (base_stop, structure) if value > current_price]
        candidates = sorted(set(raw), reverse=True)

    acceptable = []
    for stop in candidates:
        risk = abs(current_price - stop) / current_price * 100.0
        if MIN_RISK_PERCENT <= risk <= MAX_RISK_PERCENT:
            acceptable.append((stop, risk))
    if not acceptable:
        return None, None

    # Prefer the structurally safer (farther) valid stop while staying inside
    # the live risk envelope.
    stop, risk = max(acceptable, key=lambda item: item[1])
    return float(stop), float(risk)


def _record(
    symbol: str,
    direction: str,
    decision: str,
    reason: str,
    context: Dict[str, Any],
    now: int,
) -> None:
    global _DIRTY
    state = _state()
    records = state.setdefault("records", [])
    records.append(
        {
            "at": now,
            "symbol": str(symbol or "").upper(),
            "direction": direction,
            "decision": decision,
            "reason": reason,
            "mode": context.get("mode"),
            "live_score": context.get("live_score"),
            "base_score": context.get("base_score"),
            "opposite_score": context.get("opposite_score"),
            "stage": context.get("stage"),
            "direction_gap": context.get("direction_gap"),
            "support_15m": context.get("support_15m"),
            "support_1h": context.get("support_1h"),
            "start_delay_percent": context.get("start_delay_percent"),
            "prior_move_percent": context.get("prior_move_percent"),
            "reversal_pullback_percent": context.get("reversal_pullback_percent"),
            "reversal_pullback_atr": context.get("reversal_pullback_atr"),
            "four_hour_aligned": context.get("four_hour_aligned"),
            "four_hour_turning": context.get("four_hour_turning"),
            "volume_ratio": context.get("volume_ratio"),
            "volume_wake": context.get("volume_wake"),
            "risk_percent": context.get("risk_percent"),
        }
    )
    cutoff = now - KEEP_SECONDS
    records[:] = [
        row for row in records
        if isinstance(row, dict) and int(row.get("at") or 0) >= cutoff
    ][-MAX_RECORDS:]
    _DIRTY = True


def analyze_live_candidate(
    symbol: str,
    base_result: Optional[Dict[str, Any]],
    df15m: Any,
    df1h: Any,
    df4h: Any,
    current_price: Any = None,
    *,
    now_ts: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    now = int(now_ts if now_ts is not None else time.time())
    if not isinstance(base_result, dict):
        return None

    direction = str(base_result.get("direction") or "").upper()
    stage = str(base_result.get("stage") or "").upper()
    base_score = int(_sf(base_result.get("score"), 0) or 0)
    opposite_score = int(_sf(base_result.get("opposite_score"), 0) or 0)
    gap = base_score - opposite_score
    price = _sf(current_price, _sf(base_result.get("entry"), 0.0)) or 0.0
    context: Dict[str, Any] = {
        "mode": None,
        "base_score": base_score,
        "opposite_score": opposite_score,
        "stage": stage,
        "direction_gap": gap,
    }

    def reject(reason: str) -> None:
        _record(symbol, direction, "REJECTED", reason, context, now)

    if direction not in {"LONG", "SHORT"}:
        return None
    if stage not in {"ARMED", "TRIGGER"}:
        reject("ASAMA_HENUZ_ERKEN")
        return None
    if base_score < MIN_BASE_SCORE:
        reject("BASE_SKOR_DUSUK")
        return None
    if gap < MIN_DIRECTION_GAP:
        reject("YON_FARKI_ZAYIF")
        return None
    if price <= 0:
        reject("FIYAT_YOK")
        return None

    f15 = _enrich(df15m, 55)
    f1 = _enrich(df1h, 55)
    f4 = _enrich(df4h, 55)
    if f15 is None or f1 is None or f4 is None:
        reject("MTF_VERI_EKSIK")
        return None

    p15 = _support_points(f15, direction)
    p1 = _support_points(f1, direction)
    state4 = _four_hour_state(f4, direction)
    context.update(
        {
            "support_15m": p15,
            "support_1h": p1,
            "four_hour_aligned": bool(state4.get("aligned")),
            "four_hour_turning": bool(state4.get("turning")),
        }
    )
    if p15 < 3 or p1 < 3:
        reject("15M_1H_YON_TEYIDI_YETERSIZ")
        return None

    volume_ok, volume = _volume_support(base_result)
    context.update(volume)
    features = base_result.get("features") if isinstance(base_result.get("features"), dict) else {}
    internal_break = bool(
        features.get("internal_break_long" if direction == "LONG" else "internal_break_short")
        or (base_result.get("conditions") or {}).get("internal_break")
    )

    origin_price, origin_at = _recent_origin(f4, direction)
    start_delay = _directional_percent(direction, origin_price, price) if origin_price > 0 else 999.0
    context["start_delay_percent"] = round(start_delay, 4)

    start_valid = bool(
        0.0 <= start_delay <= MAX_START_DELAY_PERCENT
        and not state4.get("hard_opposing")
        and (state4.get("aligned") or state4.get("turning"))
    )
    start_score = _score_start(
        stage=stage,
        base_score=base_score,
        gap=gap,
        p15=p15,
        p1=p1,
        state4=state4,
        volume_ok=volume_ok,
        delay_percent=start_delay,
        internal_break=internal_break,
    ) if start_valid else 0

    reversal = _opposite_reversal_context(f4, direction, price)
    reversal_score = 0
    if reversal is not None:
        context.update(
            {
                "prior_move_percent": round(float(reversal["prior_move_percent"]), 4),
                "reversal_pullback_percent": round(float(reversal["pullback_percent"]), 4),
                "reversal_pullback_atr": round(float(reversal["pullback_atr"]), 4),
                "reversal_extension_atr": round(float(reversal["extension_atr"]), 4),
                "reversal_wick_ratio": round(float(reversal["rejection_wick_ratio"]), 4),
            }
        )
        if 0.0 <= float(reversal["pullback_percent"]) <= MAX_REVERSAL_PULLBACK_PERCENT:
            reversal_score = _score_reversal(
                stage=stage,
                base_score=base_score,
                gap=gap,
                p15=p15,
                p1=p1,
                volume_ok=volume_ok,
                reversal=reversal,
                internal_break=internal_break,
            )

    if reversal_score >= start_score and reversal_score > 0:
        mode = MODE_REVERSAL
        live_score = reversal_score
    elif start_score > 0:
        mode = MODE_TREND_START
        live_score = start_score
    else:
        reject("4H_REJIM_ERKENLIK_UYUMSUZ")
        return None

    context["mode"] = mode
    context["live_score"] = live_score
    if live_score < MIN_LIVE_SCORE:
        reject("CANLI_SKOR_DUSUK")
        return None

    base_stop = _sf(base_result.get("stop"), 0.0) or 0.0
    stop, risk_percent = _pick_stop(direction, price, base_stop, f15)
    if stop is None or risk_percent is None:
        reject("STOP_RISK_UYGUN_DEGIL")
        return None
    context["risk_percent"] = round(risk_percent, 4)

    targets = strategy.make_targets_from_stop(direction, price, stop)
    if not isinstance(targets, dict):
        reject("HEDEF_URETILEMEDI")
        return None

    tp1 = _sf(targets.get("tp1"))
    tp2 = _sf(targets.get("tp2"))
    tp3 = _sf(targets.get("tp3"))
    if not all(value is not None and value > 0 for value in (tp1, tp2, tp3)):
        reject("HEDEF_FIYATLARI_EKSIK")
        return None

    quality = (
        "A+ REJİM DÖNÜŞÜ"
        if mode == MODE_REVERSAL and live_score >= 98
        else "A+ BÜYÜK HAREKET BAŞLANGICI"
        if live_score >= 98
        else "A REJİM DÖNÜŞÜ"
        if mode == MODE_REVERSAL
        else "A BÜYÜK HAREKET BAŞLANGICI"
    )

    if mode == MODE_REVERSAL:
        prior = str((reversal or {}).get("previous_direction") or "")
        trend_reason = (
            f"{prior} hareketi %{float((reversal or {}).get('prior_move_percent') or 0.0):.2f} "
            f"uzadı; 15M/1H {direction} yapısına döndü."
        )
        entry_reason = (
            f"4H tepe/dipten ters yönde %{float((reversal or {}).get('pullback_percent') or 0.0):.2f}; "
            f"{float((reversal or {}).get('pullback_atr') or 0.0):.2f} ATR geri dönüş."
        )
    else:
        trend_reason = (
            f"4H {'uyumlu' if state4.get('aligned') else 'dönüyor'} + "
            f"1H/15M {direction} teyidi."
        )
        entry_reason = (
            f"Güncel 4H rejim origininden yönsel uzaklık %{start_delay:.2f}; "
            "hareket henüz erken bölgede."
        )

    signal = {
        "symbol": str(symbol or "").upper(),
        "direction": direction,
        "source": SOURCE,
        "signal_class": "TRADE",
        "entry": round(price, 12),
        "ideal_entry": round(price, 12),
        "zone_distance_percent": 0.0,
        "zone_name": (
            "4H/1H rejim dönüş bölgesi"
            if mode == MODE_REVERSAL
            else "4H/1H büyük hareket başlangıç bölgesi"
        ),
        "tp1": round(float(tp1), 12),
        "tp2": round(float(tp2), 12),
        "tp3": round(float(tp3), 12),
        "sl": round(float(stop), 12),
        "risk_percent": round(float(risk_percent), 4),
        "rr_tp1": targets.get("rr_tp1", 0.55),
        "rr_tp2": targets.get("rr_tp2", 1.05),
        "rr_tp3": targets.get("rr_tp3", 1.60),
        "score": int(live_score),
        "quality": quality,
        "quality_note": (
            "Kısa vadeli tek kırılım değil; 4H/1H rejim başlangıcı veya gerçek yön değişimi "
            "Movement Start V2 mikro teyidiyle birlikte doğrulandı."
        ),
        "leverage": "1x-2x",
        "trend_reason": trend_reason,
        "confirm_reason": (
            f"{mode} | V2 {stage} {base_score}/100 | yön farkı {gap} | "
            f"15M {p15}/4 | 1H {p1}/4 | hacim {volume['volume_ratio']:.2f}x"
        ),
        "entry_reason": entry_reason,
        "radar_reason": (
            "RECALL tipi çok saatlik LONG->SHORT veya SHORT->LONG rejim değişimlerini "
            "hareketin büyük kısmı oluşmadan yakalamak için ayrı Premium yol."
        ),
        "regime_transition_version": VERSION,
        "regime_transition_mode": mode,
        "regime_base_score": base_score,
        "regime_opposite_score": opposite_score,
        "regime_direction_gap": gap,
        "regime_support_15m": p15,
        "regime_support_1h": p1,
        "regime_four_hour_aligned": bool(state4.get("aligned")),
        "regime_four_hour_turning": bool(state4.get("turning")),
        "regime_origin_price": round(origin_price, 12) if origin_price > 0 else None,
        "regime_origin_at": origin_at or None,
        "regime_start_delay_percent": round(start_delay, 4) if math.isfinite(start_delay) else None,
        "regime_prior_move_percent": context.get("prior_move_percent"),
        "regime_reversal_pullback_percent": context.get("reversal_pullback_percent"),
        "regime_reversal_pullback_atr": context.get("reversal_pullback_atr"),
        "volume_ratio": round(float(volume["volume_ratio"]), 4),
        "volume_wake": round(float(volume["volume_wake"]), 4),
    }

    _record(symbol, direction, "PROMOTED", "LIVE_REGIME_TRANSITION", context, now)
    return signal


def strong_direct_allowed(
    signal: Dict[str, Any],
    current_price: Any,
    base_validator: Any,
    profit_module: Any,
) -> bool:
    if str(signal.get("source") or "").upper() != SOURCE:
        return False
    if int(_sf(signal.get("score"), 0) or 0) < MIN_LIVE_SCORE:
        return False
    risk = _sf(signal.get("risk_percent"), 999.0) or 999.0
    if not (MIN_RISK_PERCENT <= risk <= MAX_RISK_PERCENT):
        return False
    ok, _ = base_validator(signal, current_price)
    if not ok:
        return False
    try:
        return bool(profit_module.cost_viability(signal).get("ok"))
    except Exception:
        return False


def finish() -> Dict[str, Any]:
    global _DIRTY
    state = _state()
    records = state.get("records") if isinstance(state.get("records"), list) else []
    decisions = Counter(str(row.get("decision") or "UNKNOWN") for row in records if isinstance(row, dict))
    modes = Counter(
        str(row.get("mode") or "NONE")
        for row in records
        if isinstance(row, dict) and row.get("decision") == "PROMOTED"
    )
    reasons = Counter(
        str(row.get("reason") or "UNKNOWN")
        for row in records
        if isinstance(row, dict) and row.get("decision") == "REJECTED"
    )
    state["updated_at"] = int(time.time())
    state["summary"] = {
        "version": VERSION,
        "records": len(records),
        "promoted": int(decisions.get("PROMOTED", 0)),
        "rejected": int(decisions.get("REJECTED", 0)),
        "promoted_by_mode": dict(modes),
        "top_reject_reasons": dict(reasons.most_common(12)),
    }
    _atomic_save(_STATE_PATH, state)
    _DIRTY = False
    return dict(state["summary"])
