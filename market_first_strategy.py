"""Market First V5 strategy.

Fresh decision logic built around:
1) major-coin market regime (BTC/ETH/SOL),
2) market breadth,
3) altcoin relative strength/acceleration,
4) extension control,
5) simple alert lifecycle.

It never places exchange orders. The runner can reuse existing tracking/Telegram
plumbing, but the market decision logic in this module is independent from the
legacy Premium/Simple Core admission rules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Dict, Mapping, Optional

import pandas as pd

VERSION = "MARKET_FIRST_V5_2026_08_28"
SOURCE = "MARKET_FIRST_V5"

MAJOR_WEIGHTS = {
    "BTCUSDT": 0.50,
    "ETHUSDT": 0.30,
    "SOLUSDT": 0.20,
}

MIN_ALERT_SCORE = 66
MIN_TRADE_SCORE = 78

MIN_RISK_PERCENT = 0.40
MAX_RISK_PERCENT = 1.80
MIN_ROOM_R = 1.60

TP1_R = 0.75
TP2_R = 1.25
TP3_R = 2.00

ALERT_CONTINUE_PERCENT = 0.80
ALERT_LATE_PERCENT = 2.50
ALERT_FAIL_PERCENT = -0.90
ALERT_MAX_AGE_MINUTES = 120


@dataclass(frozen=True)
class MajorSnapshot:
    symbol: str
    score: float
    trend_4h: int
    trend_1h: int
    trend_15m: int
    move_5m_percent: float


@dataclass(frozen=True)
class MarketContext:
    regime: str
    preferred_direction: Optional[str]
    score: float
    strength: float
    breadth_5m: float
    breadth_24h: float
    major_move_5m_percent: float
    allow_countertrend: bool
    majors: Dict[str, Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pct(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return (end / start - 1.0) * 100.0


def _direction_sign(direction: str) -> int:
    return 1 if str(direction).upper() == "LONG" else -1


def _normalize_frame(df: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if df is None or not hasattr(df, "copy"):
        return None
    needed = {"open", "high", "low", "close", "volume"}
    if not needed.issubset(set(df.columns)):
        return None
    frame = df.copy()
    for col in needed:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    frame = frame.dropna(subset=list(needed)).reset_index(drop=True)
    return frame if len(frame) >= 8 else None


def _closed_frame(df: Optional[pd.DataFrame], min_len: int = 24) -> Optional[pd.DataFrame]:
    frame = _normalize_frame(df)
    if frame is None or len(frame) < min_len + 1:
        return None
    # CCXT's last candle can still be forming. Trend/structure must use closed bars.
    return frame.iloc[:-1].reset_index(drop=True)


def _with_indicators(frame: Optional[pd.DataFrame]) -> Optional[pd.DataFrame]:
    if frame is None or len(frame) < 24:
        return None
    out = frame.copy()
    out["ema9"] = out["close"].ewm(span=9, adjust=False).mean()
    out["ema20"] = out["close"].ewm(span=20, adjust=False).mean()
    out["ema50"] = out["close"].ewm(span=50, adjust=False).mean()

    prev_close = out["close"].shift(1)
    tr = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - prev_close).abs(),
            (out["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr14"] = tr.rolling(14).mean()
    out["volume_median20"] = out["volume"].rolling(20).median()
    out["volume_ratio"] = out["volume"] / out["volume_median20"].replace(0, pd.NA)
    out = out.dropna().reset_index(drop=True)
    return out if len(out) >= 5 else None


def _trend_vote(df: Optional[pd.DataFrame]) -> int:
    frame = _with_indicators(_closed_frame(df, min_len=55))
    if frame is None or len(frame) < 4:
        return 0

    last = frame.iloc[-1]
    past = frame.iloc[-4]
    close = _sf(last["close"])
    ema20 = _sf(last["ema20"])
    ema50 = _sf(last["ema50"])
    ema20_past = _sf(past["ema20"])

    if close > ema20 > ema50 and ema20 > ema20_past:
        return 1
    if close < ema20 < ema50 and ema20 < ema20_past:
        return -1
    return 0


def _live_move(df: Optional[pd.DataFrame], current_price: float, bars: int = 1) -> float:
    frame = _normalize_frame(df)
    if frame is None or len(frame) < max(2, bars):
        return 0.0
    price = current_price if current_price > 0 else _sf(frame.iloc[-1]["close"])
    start_index = max(0, len(frame) - bars)
    start = _sf(frame.iloc[start_index]["open"])
    return _pct(start, price) if min(start, price) > 0 else 0.0


def _major_snapshot(symbol: str, payload: Mapping[str, Any]) -> MajorSnapshot:
    current_price = _sf(payload.get("current_price"))
    trend_4h = _trend_vote(payload.get("4h"))
    trend_1h = _trend_vote(payload.get("1h"))
    trend_15m = _trend_vote(payload.get("15m"))
    move_5m = _live_move(payload.get("5m"), current_price, bars=1)

    momentum_vote = math.tanh(move_5m / 0.55)
    score = (
        35.0 * trend_4h
        + 35.0 * trend_1h
        + 20.0 * trend_15m
        + 10.0 * momentum_vote
    )
    return MajorSnapshot(
        symbol=symbol,
        score=round(score, 3),
        trend_4h=trend_4h,
        trend_1h=trend_1h,
        trend_15m=trend_15m,
        move_5m_percent=round(move_5m, 4),
    )


def build_market_context(
    major_payloads: Mapping[str, Mapping[str, Any]],
    breadth_5m: float = 0.50,
    breadth_24h: float = 0.50,
) -> MarketContext:
    """Classify market first; candidate analysis comes afterwards."""
    snapshots: Dict[str, MajorSnapshot] = {}
    weighted_major_score = 0.0
    weight_total = 0.0
    weighted_move = 0.0

    for symbol, weight in MAJOR_WEIGHTS.items():
        payload = major_payloads.get(symbol)
        if not payload:
            continue
        snap = _major_snapshot(symbol, payload)
        snapshots[symbol] = snap
        weighted_major_score += snap.score * weight
        weighted_move += snap.move_5m_percent * weight
        weight_total += weight

    if weight_total > 0:
        weighted_major_score /= weight_total
        weighted_move /= weight_total

    b5 = _clip(_sf(breadth_5m, 0.50), 0.0, 1.0)
    b24 = _clip(_sf(breadth_24h, 0.50), 0.0, 1.0)
    breadth5_score = (b5 - 0.50) * 200.0
    breadth24_score = (b24 - 0.50) * 200.0

    score = (
        weighted_major_score * 0.80
        + breadth5_score * 0.15
        + breadth24_score * 0.05
    )
    score = _clip(score, -100.0, 100.0)

    aligned_up = sum(1 for snap in snapshots.values() if snap.move_5m_percent >= 0.25)
    aligned_down = sum(1 for snap in snapshots.values() if snap.move_5m_percent <= -0.25)

    if weighted_move >= 0.65 and aligned_up >= 2:
        regime = "SHOCK_UP"
        preferred = "LONG"
        allow_countertrend = False
    elif weighted_move <= -0.65 and aligned_down >= 2:
        regime = "SHOCK_DOWN"
        preferred = "SHORT"
        allow_countertrend = False
    elif score >= 35.0 and b5 >= 0.53:
        regime = "BULL_STRONG"
        preferred = "LONG"
        allow_countertrend = False
    elif score <= -35.0 and b5 <= 0.47:
        regime = "BEAR_STRONG"
        preferred = "SHORT"
        allow_countertrend = False
    elif score >= 15.0:
        regime = "BULL"
        preferred = "LONG"
        allow_countertrend = True
    elif score <= -15.0:
        regime = "BEAR"
        preferred = "SHORT"
        allow_countertrend = True
    else:
        regime = "CHOP"
        preferred = None
        allow_countertrend = True

    majors = {
        symbol: asdict(snapshot)
        for symbol, snapshot in snapshots.items()
    }
    return MarketContext(
        regime=regime,
        preferred_direction=preferred,
        score=round(score, 2),
        strength=round(abs(score), 2),
        breadth_5m=round(b5, 4),
        breadth_24h=round(b24, 4),
        major_move_5m_percent=round(weighted_move, 4),
        allow_countertrend=allow_countertrend,
        majors=majors,
    )


def market_label(context: MarketContext) -> str:
    if context.regime in {"SHOCK_UP", "BULL_STRONG", "BULL"}:
        return "YUKARI"
    if context.regime in {"SHOCK_DOWN", "BEAR_STRONG", "BEAR"}:
        return "AŞAĞI"
    return "KARIŞIK"


def _acceleration(
    df1m: Optional[pd.DataFrame],
    current_price: float,
) -> Optional[Dict[str, Any]]:
    frame = _normalize_frame(df1m)
    if frame is None or len(frame) < 26:
        return None

    price = current_price if current_price > 0 else _sf(frame.iloc[-1]["close"])
    if price <= 0:
        return None

    current = frame.iloc[-1]
    open_1m = _sf(current["open"])
    open_3m = _sf(frame.iloc[-3]["open"])
    open_5m = _sf(frame.iloc[-5]["open"])
    if min(open_1m, open_3m, open_5m) <= 0:
        return None

    move_1m = _pct(open_1m, price)
    move_3m = _pct(open_3m, price)
    move_5m = _pct(open_5m, price)

    baseline = frame.iloc[-24:-4]
    baseline_vol = [
        _sf(value)
        for value in baseline["volume"].tolist()
        if _sf(value) > 0
    ]
    median_volume = float(pd.Series(baseline_vol).median()) if baseline_vol else 0.0
    current_volume = _sf(current["volume"])
    volume_ratio = current_volume / median_volume if median_volume > 0 else 0.0

    prior20 = frame.iloc[-24:-4]
    prior_high = _sf(prior20["high"].max())
    prior_low = _sf(prior20["low"].min())
    breakout_up = price > prior_high > 0
    breakout_down = price < prior_low and prior_low > 0

    source_move = move_3m if abs(move_3m) >= 0.15 else move_5m
    if abs(source_move) < 0.12:
        return None
    direction = "LONG" if source_move > 0 else "SHORT"
    breakout = breakout_up if direction == "LONG" else breakout_down

    same_direction = (
        move_1m > 0 and move_3m > 0 and move_5m > 0
        if direction == "LONG"
        else move_1m < 0 and move_3m < 0 and move_5m < 0
    )

    high = _sf(current["high"])
    low = _sf(current["low"])
    range_1m = (high - low) / open_1m * 100.0 if open_1m > 0 else 0.0

    return {
        "direction": direction,
        "move_1m_percent": round(move_1m, 4),
        "move_3m_percent": round(move_3m, 4),
        "move_5m_percent": round(move_5m, 4),
        "volume_ratio": round(volume_ratio, 3),
        "breakout": bool(breakout),
        "same_direction": bool(same_direction),
        "range_1m_percent": round(range_1m, 4),
    }


def _structure(
    df: Optional[pd.DataFrame],
    current_price: float = 0.0,
) -> Optional[Dict[str, Any]]:
    frame = _with_indicators(_closed_frame(df, min_len=55))
    if frame is None or len(frame) < 8:
        return None

    last = frame.iloc[-1]
    past = frame.iloc[-4]
    close = _sf(last["close"])
    ema9 = _sf(last["ema9"])
    ema20 = _sf(last["ema20"])
    ema50 = _sf(last["ema50"])
    ema20_past = _sf(past["ema20"])
    atr = _sf(last["atr14"])
    price = current_price if current_price > 0 else close

    if close > ema9 > ema20 and ema20 >= ema20_past:
        direction = "LONG"
    elif close < ema9 < ema20 and ema20 <= ema20_past:
        direction = "SHORT"
    elif close > ema20 > ema50 and ema20 >= ema20_past:
        direction = "LONG"
    elif close < ema20 < ema50 and ema20 <= ema20_past:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    extension_atr = abs(price - ema20) / atr if atr > 0 else 0.0
    atr_percent = atr / price * 100.0 if price > 0 and atr > 0 else 0.0
    volume_ratio = _sf(last.get("volume_ratio"), 0.0)

    recent12 = frame.tail(12)
    recent72 = frame.tail(72)
    return {
        "direction": direction,
        "close": close,
        "ema9": ema9,
        "ema20": ema20,
        "ema50": ema50,
        "atr": atr,
        "atr_percent": round(atr_percent, 4),
        "extension_atr": round(extension_atr, 4),
        "volume_ratio": round(volume_ratio, 3),
        "swing_low_12": _sf(recent12["low"].min()),
        "swing_high_12": _sf(recent12["high"].max()),
        "range_low_72": _sf(recent72["low"].min()),
        "range_high_72": _sf(recent72["high"].max()),
    }


def _relative_strength(
    direction: str,
    coin_move_5m: float,
    market_move_5m: float,
) -> float:
    if direction == "LONG":
        return coin_move_5m - market_move_5m
    return market_move_5m - coin_move_5m


def _market_component(
    direction: str,
    context: MarketContext,
) -> tuple[int, bool]:
    preferred = context.preferred_direction
    if preferred is None:
        return 6, True
    if direction == preferred:
        if context.regime in {"SHOCK_UP", "SHOCK_DOWN", "BULL_STRONG", "BEAR_STRONG"}:
            return 20, True
        return 14, True

    if context.regime in {"SHOCK_UP", "SHOCK_DOWN", "BULL_STRONG", "BEAR_STRONG"}:
        return -18, False
    return -8, True


def _risk_plan(
    direction: str,
    entry: float,
    structure_5m: Dict[str, Any],
    structure_15m: Dict[str, Any],
) -> tuple[Optional[Dict[str, float]], str]:
    atr5 = _sf(structure_5m.get("atr"))
    if entry <= 0 or atr5 <= 0:
        return None, "RISK_DATA"

    if direction == "LONG":
        raw_sl = _sf(structure_5m.get("swing_low_12")) - atr5 * 0.10
        risk = entry - raw_sl
    else:
        raw_sl = _sf(structure_5m.get("swing_high_12")) + atr5 * 0.10
        risk = raw_sl - entry

    if risk <= 0:
        return None, "RISK_GEOMETRY"

    risk_percent = risk / entry * 100.0
    if risk_percent < MIN_RISK_PERCENT:
        risk = entry * MIN_RISK_PERCENT / 100.0
        raw_sl = entry - risk if direction == "LONG" else entry + risk
        risk_percent = MIN_RISK_PERCENT

    if risk_percent > MAX_RISK_PERCENT:
        return None, "RISK_WIDE"

    if direction == "LONG":
        opposing = _sf(structure_15m.get("range_high_72"))
        room = opposing - entry if opposing > entry else 0.0
    else:
        opposing = _sf(structure_15m.get("range_low_72"))
        room = entry - opposing if 0 < opposing < entry else 0.0

    room_r = room / risk if risk > 0 and room > 0 else 99.0
    if 0 < room_r < MIN_ROOM_R:
        return None, "NO_ROOM"

    if direction == "LONG":
        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R
    else:
        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    if min(raw_sl, tp1, tp2, tp3) <= 0:
        return None, "RISK_GEOMETRY"

    return {
        "sl": round(raw_sl, 10),
        "tp1": round(tp1, 10),
        "tp2": round(tp2, 10),
        "tp3": round(tp3, 10),
        "risk_percent": round(risk_percent, 3),
        "room_r": round(room_r, 2),
    }, "OK"


def analyze_candidate(
    symbol: str,
    df1m: Optional[pd.DataFrame],
    df5m: Optional[pd.DataFrame],
    df15m: Optional[pd.DataFrame],
    df1h: Optional[pd.DataFrame],
    current_price: float,
    quote_volume_24h: float,
    context: MarketContext,
) -> tuple[Optional[Dict[str, Any]], str]:
    """Return one simple candidate decision or a rejection reason."""
    acceleration = _acceleration(df1m, current_price)
    if acceleration is None:
        return None, "NO_ACCELERATION"

    direction = str(acceleration["direction"])
    move1 = abs(_sf(acceleration["move_1m_percent"]))
    move3 = abs(_sf(acceleration["move_3m_percent"]))
    move5 = abs(_sf(acceleration["move_5m_percent"]))

    s5 = _structure(df5m, current_price)
    s15 = _structure(df15m, current_price)
    s1h = _structure(df1h, current_price)
    if s5 is None or s15 is None or s1h is None:
        return None, "STRUCTURE_DATA"

    relative = _relative_strength(
        direction,
        _sf(acceleration["move_5m_percent"]),
        context.major_move_5m_percent,
    )

    market_points, market_allowed = _market_component(direction, context)

    independent = bool(
        acceleration.get("breakout")
        and relative >= 0.75
        and move5 >= 1.00
        and (
            _sf(acceleration.get("volume_ratio")) >= 0.75
            or move3 >= 1.20
        )
    )
    if not market_allowed and not independent:
        return None, "MARKET_OPPOSED"

    score = 0
    score += 5 if move1 >= 0.35 else 2 if move1 >= 0.20 else 0
    score += 6 if move3 >= 0.70 else 3 if move3 >= 0.40 else 0
    score += 7 if move5 >= 1.00 else 3 if move5 >= 0.60 else 0
    score += 12 if acceleration.get("breakout") else 0

    volume_ratio = _sf(acceleration.get("volume_ratio"))
    if volume_ratio >= 2.0:
        score += 8
    elif volume_ratio >= 1.20:
        score += 6
    elif volume_ratio >= 0.80:
        score += 3

    if acceleration.get("same_direction"):
        score += 4

    for structure, good, neutral, bad in (
        (s5, 10, 2, -5),
        (s15, 8, 1, -4),
        (s1h, 5, 1, -3),
    ):
        if structure["direction"] == direction:
            score += good
        elif structure["direction"] == "NEUTRAL":
            score += neutral
        else:
            score += bad

    score += market_points

    if relative >= 1.00:
        score += 8
    elif relative >= 0.50:
        score += 6
    elif relative >= 0.20:
        score += 3
    elif relative <= -0.50:
        score -= 3

    if quote_volume_24h >= 20_000_000:
        score += 4
    elif quote_volume_24h >= 5_000_000:
        score += 3
    elif quote_volume_24h >= 1_000_000:
        score += 1
    elif quote_volume_24h < 250_000:
        score -= 4

    extension_atr = _sf(s5.get("extension_atr"))
    if extension_atr >= 2.00:
        score -= 24
    elif extension_atr >= 1.60:
        score -= 16
    elif extension_atr >= 1.30:
        score -= 9
    elif extension_atr >= 1.00:
        score -= 4

    if move5 >= 5.0:
        score -= 15
    elif move5 >= 3.5:
        score -= 8

    score = int(round(_clip(score, 0, 100)))

    late = bool(
        extension_atr >= 1.80
        or move5 >= 4.50
        or move3 >= 3.20
    )

    if late:
        stage = "LATE"
    elif score >= MIN_TRADE_SCORE and (
        acceleration.get("breakout") or move3 >= 1.00
    ):
        stage = "READY"
    elif score >= MIN_ALERT_SCORE:
        stage = "EARLY"
    else:
        return None, "LOW_SCORE"

    decision: Dict[str, Any] = {
        "symbol": symbol,
        "direction": direction,
        "source": SOURCE,
        "score": score,
        "stage": stage,
        "current_price": round(current_price, 10),
        "quote_volume_24h": round(quote_volume_24h, 2),
        "market_regime": context.regime,
        "market_label": market_label(context),
        "market_score": context.score,
        "market_strength": context.strength,
        "market_preferred_direction": context.preferred_direction,
        "market_breadth_5m": context.breadth_5m,
        "major_move_5m_percent": context.major_move_5m_percent,
        "independent_move": independent,
        "move_1m_percent": acceleration["move_1m_percent"],
        "move_3m_percent": acceleration["move_3m_percent"],
        "move_5m_percent": acceleration["move_5m_percent"],
        "volume_ratio_1m": acceleration["volume_ratio"],
        "breakout_20m": bool(acceleration.get("breakout")),
        "relative_strength_5m": round(relative, 4),
        "extension_atr_5m": round(extension_atr, 3),
        "structure_5m": s5["direction"],
        "structure_15m": s15["direction"],
        "structure_1h": s1h["direction"],
        "alert_eligible": stage in {"EARLY", "READY"},
        "trade_eligible": False,
        "risk_reject_reason": None,
    }

    if stage == "READY":
        risk, risk_reason = _risk_plan(
            direction,
            current_price,
            s5,
            s15,
        )
        if risk is not None:
            decision["trade_eligible"] = True
            decision.update(risk)
        else:
            decision["risk_reject_reason"] = risk_reason

    return decision, "OK"


def decision_to_signal(decision: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    if not decision.get("trade_eligible"):
        return None
    required = ("symbol", "direction", "current_price", "sl", "tp1", "tp2", "tp3")
    if any(decision.get(key) in (None, "") for key in required):
        return None

    direction = str(decision["direction"])
    score = int(decision.get("score") or 0)
    return {
        "symbol": str(decision["symbol"]),
        "direction": direction,
        "source": SOURCE,
        "signal_class": "TRADE",
        "entry": _sf(decision["current_price"]),
        "ideal_entry": _sf(decision["current_price"]),
        "sl": _sf(decision["sl"]),
        "tp1": _sf(decision["tp1"]),
        "tp2": _sf(decision["tp2"]),
        "tp3": _sf(decision["tp3"]),
        "risk_percent": _sf(decision.get("risk_percent")),
        "rr_tp1": TP1_R,
        "rr_tp2": TP2_R,
        "rr_tp3": TP3_R,
        "score": score,
        "quality": "A+ MARKET FIRST" if score >= 88 else "A MARKET FIRST",
        "quality_note": "Önce piyasa yönü, sonra coin ivmesi ve göreli güç.",
        "entry_type": "MARKET_FIRST",
        "zone_name": "Market-first momentum",
        "zone_distance_percent": 0.0,
        "room_r": _sf(decision.get("room_r"), 99.0),
        "volume_5m": _sf(decision.get("volume_ratio_1m")),
        "volume_ratio": _sf(decision.get("volume_ratio_1m")),
        "trend_1h": str(decision.get("structure_1h") or ""),
        "market_regime": str(decision.get("market_regime") or ""),
        "market_label": str(decision.get("market_label") or ""),
        "major_move_5m_percent": _sf(decision.get("major_move_5m_percent")),
        "relative_strength_5m": _sf(decision.get("relative_strength_5m")),
        "extension_atr_5m": _sf(decision.get("extension_atr_5m")),
        "independent_move": bool(decision.get("independent_move")),
    }


def lifecycle_update(
    direction: str,
    alert_price: float,
    best_price: float,
    current_price: float,
    current_status: str,
    age_minutes: float,
) -> Dict[str, Any]:
    """Update a radar alert into only three user-facing states."""
    direction = str(direction).upper()
    if min(alert_price, current_price) <= 0:
        return {
            "status": current_status,
            "best_price": best_price,
            "favorable_percent": 0.0,
            "best_favorable_percent": 0.0,
            "changed": False,
        }

    if direction == "LONG":
        new_best = max(best_price or alert_price, current_price)
        favorable = _pct(alert_price, current_price)
        best_favorable = _pct(alert_price, new_best)
    else:
        baseline_best = best_price if best_price > 0 else alert_price
        new_best = min(baseline_best, current_price)
        favorable = _pct(current_price, alert_price)
        best_favorable = _pct(new_best, alert_price)

    status = current_status or "NEW"

    dead_after_run = bool(
        favorable <= ALERT_FAIL_PERCENT
        or age_minutes >= ALERT_MAX_AGE_MINUTES
        or (
            best_favorable >= 1.20
            and favorable <= max(0.15, best_favorable * 0.40)
        )
    )

    if dead_after_run:
        target = "DEAD"
    elif best_favorable >= ALERT_LATE_PERCENT or favorable >= ALERT_LATE_PERCENT:
        target = "LATE"
    elif favorable >= ALERT_CONTINUE_PERCENT:
        target = "CONTINUE"
    else:
        target = status

    # Do not downgrade LATE back to CONTINUE. DEAD is terminal.
    if status == "DEAD":
        target = "DEAD"
    elif status == "LATE" and target == "CONTINUE":
        target = "LATE"

    return {
        "status": target,
        "best_price": round(new_best, 10),
        "favorable_percent": round(favorable, 3),
        "best_favorable_percent": round(best_favorable, 3),
        "changed": target != status,
    }
