"""Capture a genuine opposite move after a Market First EARLY alert has failed.

This is a narrow recovery layer for cases such as SOPHUSDT: Market First detects
an early direction, that alert later becomes DEAD, and a fresh move starts in the
opposite direction.  Previously DEAD alerts stopped receiving scan priority, so
the new move could be missed even while the whole-universe scanner kept rotating.

Safety principles
-----------------
- a prior DEAD alert is only context, never a reason to flip by itself;
- the old direction must have moved materially against its alert price;
- fresh 1m acceleration must point opposite, with 5m structure confirmation;
- 15m must be opposite or neutral; a still-opposing 1h needs exceptional evidence;
- normal Market First stop/room geometry must be valid;
- the ordinary ML, duplicate, recent-stop, portfolio, live-flow, BTC/ETH/SOL and
  liquidity guards still run after this module promotes a candidate;
- no exchange order is placed by this module.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import market_first_strategy as strategy

VERSION = "MARKET_FIRST_REVERSAL_CAPTURE_V2_2026_09_08"

REVERSAL_WINDOW_SECONDS = 90 * 60
MAX_ALERT_FIRST_AGE_SECONDS = 3 * 60 * 60
MAX_FORCED_SYMBOLS = 8
PROMOTION_COOLDOWN_SECONDS = 20 * 60
MAX_HISTORY = 300

MIN_OLD_DIRECTION_FAILURE_PERCENT = 0.65
MIN_MOVE_3M_PERCENT = 0.18
MIN_MOVE_5M_PERCENT = 0.28
MIN_VOLUME_RATIO_1M = 0.70
MIN_REVERSAL_SCORE = 82
MAX_EXTENSION_ATR = 1.45

_INSTALLED = False


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _opposite(direction: str) -> str:
    return "SHORT" if str(direction).upper() == "LONG" else "LONG"


def _aligned(direction: str, value: Any) -> float:
    raw = _sf(value)
    return raw if str(direction).upper() == "LONG" else -raw


def _failure_percent(old_direction: str, alert_price: float, current_price: float) -> float:
    """Positive when price moved against the old alert direction."""
    if min(alert_price, current_price) <= 0:
        return 0.0
    raw = (current_price / alert_price - 1.0) * 100.0
    return -raw if str(old_direction).upper() == "LONG" else raw


def _terminal_at(item: Mapping[str, Any]) -> int:
    return int(_sf(item.get("updated_at")) or _sf(item.get("last_checked_at")) or _sf(item.get("first_at")))


def recent_failed_alert(
    state: Mapping[str, Any] | None,
    symbol: str,
    *,
    now: int,
) -> Optional[Dict[str, Any]]:
    """Newest recent DEAD alert for symbol, if it is still worth re-checking."""
    if not isinstance(state, Mapping):
        return None
    active = state.get("active_alerts")
    if not isinstance(active, Mapping):
        return None

    wanted = str(symbol or "").upper()
    best: Optional[Dict[str, Any]] = None
    best_terminal = 0
    for raw in active.values():
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("symbol") or "").upper() != wanted:
            continue
        if str(raw.get("status") or "").upper() != "DEAD":
            continue
        old_direction = str(raw.get("direction") or "").upper()
        if old_direction not in {"LONG", "SHORT"}:
            continue
        first_at = int(_sf(raw.get("first_at")))
        terminal_at = _terminal_at(raw)
        if first_at <= 0 or terminal_at <= 0:
            continue
        if now - first_at > MAX_ALERT_FIRST_AGE_SECONDS:
            continue
        if now < terminal_at or now - terminal_at > REVERSAL_WINDOW_SECONDS:
            continue
        if terminal_at > best_terminal:
            best = dict(raw)
            best["terminal_at"] = terminal_at
            best_terminal = terminal_at
    return best


def reversal_priority_symbols(
    state: Mapping[str, Any] | None,
    rows: Sequence[Mapping[str, Any]],
    *,
    now: int,
) -> list[str]:
    available = {
        str(row.get("symbol") or "").upper()
        for row in rows
        if isinstance(row, Mapping) and row.get("symbol")
    }
    if not available or not isinstance(state, Mapping):
        return []

    active = state.get("active_alerts")
    if not isinstance(active, Mapping):
        return []

    candidates: list[Tuple[int, str]] = []
    seen = set()
    for raw in active.values():
        if not isinstance(raw, Mapping):
            continue
        symbol = str(raw.get("symbol") or "").upper()
        if not symbol or symbol not in available or symbol in seen:
            continue
        failed = recent_failed_alert(state, symbol, now=now)
        if not failed:
            continue
        seen.add(symbol)
        candidates.append((int(failed.get("terminal_at") or 0), symbol))

    candidates.sort(reverse=True)
    return [symbol for _, symbol in candidates[:MAX_FORCED_SYMBOLS]]


def _history_key(alert: Mapping[str, Any]) -> str:
    return (
        f"{str(alert.get('symbol') or '').upper()}:"
        f"{str(alert.get('direction') or '').upper()}:"
        f"{int(_sf(alert.get('first_at')))}"
    )


def _promotion_on_cooldown(state: Mapping[str, Any] | None, alert: Mapping[str, Any], now: int) -> bool:
    if not isinstance(state, Mapping):
        return False
    history = state.get("reversal_capture_history")
    if not isinstance(history, Mapping):
        return False
    row = history.get(_history_key(alert))
    if not isinstance(row, Mapping):
        return False
    promoted_at = int(_sf(row.get("promoted_at")))
    return promoted_at > 0 and now - promoted_at < PROMOTION_COOLDOWN_SECONDS


def _record_promotion(
    state: Dict[str, Any] | None,
    alert: Mapping[str, Any],
    promoted: Mapping[str, Any],
    now: int,
) -> None:
    if not isinstance(state, dict):
        return
    history = state.setdefault("reversal_capture_history", {})
    if not isinstance(history, dict):
        history = {}
        state["reversal_capture_history"] = history
    history[_history_key(alert)] = {
        "symbol": promoted.get("symbol"),
        "old_direction": alert.get("direction"),
        "new_direction": promoted.get("direction"),
        "old_alert_price": _sf(alert.get("alert_price")),
        "entry_price": _sf(promoted.get("current_price")),
        "failure_percent": _sf(promoted.get("reversal_old_failure_percent")),
        "reversal_score": int(_sf(promoted.get("reversal_capture_score"))),
        "promoted_at": int(now),
        "version": VERSION,
    }
    if len(history) > MAX_HISTORY:
        ordered = sorted(
            history.items(),
            key=lambda kv: int(_sf((kv[1] or {}).get("promoted_at"))),
            reverse=True,
        )
        state["reversal_capture_history"] = dict(ordered[:MAX_HISTORY])


def _score(
    *,
    base_score: int,
    failure_percent: float,
    move3: float,
    move5: float,
    volume: float,
    breakout: bool,
    s15_direction: str,
    s1h_direction: str,
    new_direction: str,
    market_preferred: str,
    relative_strength: float,
) -> int:
    score = max(58, int(base_score))
    score += 10 if failure_percent >= 3.0 else 8 if failure_percent >= 1.5 else 5
    score += 8 if breakout else 0
    score += 5 if move3 >= 0.35 else 2
    score += 5 if move5 >= 0.55 else 2
    score += 6 if volume >= 1.20 else 3
    score += 8 if s15_direction == new_direction else 3
    if s1h_direction == new_direction:
        score += 5
    elif s1h_direction == "NEUTRAL":
        score += 2
    else:
        score -= 6
    if market_preferred == new_direction:
        score += 6
    elif not market_preferred:
        score += 2
    else:
        score -= 4
    if relative_strength >= 0.75:
        score += 5
    elif relative_strength >= 0.30:
        score += 3
    return max(0, min(100, int(round(score))))


def evaluate_reversal(
    *,
    state: Dict[str, Any] | None,
    symbol: str,
    df1m: Any,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
    quote_volume_24h: float,
    context: strategy.MarketContext,
    existing_decision: Optional[Mapping[str, Any]],
    existing_reason: str,
    now: int,
) -> Tuple[Optional[Dict[str, Any]], str, Dict[str, Any]]:
    diagnostics: Dict[str, Any] = {"promoted": False, "version": VERSION}
    if current_price <= 0 or context is None:
        diagnostics["reason"] = "REVERSAL_CONTEXT_MISSING"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    alert = recent_failed_alert(state, symbol, now=now)
    if not alert:
        diagnostics["reason"] = "REVERSAL_NO_RECENT_DEAD_ALERT"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics
    if _promotion_on_cooldown(state, alert, now):
        diagnostics["reason"] = "REVERSAL_PROMOTION_COOLDOWN"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    old_direction = str(alert.get("direction") or "").upper()
    new_direction = _opposite(old_direction)
    alert_price = _sf(alert.get("alert_price"))
    failure = _failure_percent(old_direction, alert_price, current_price)
    diagnostics.update({
        "old_direction": old_direction,
        "new_direction": new_direction,
        "old_alert_price": alert_price,
        "failure_percent": round(failure, 4),
    })
    if failure < MIN_OLD_DIRECTION_FAILURE_PERCENT:
        diagnostics["reason"] = "REVERSAL_OLD_DIRECTION_NOT_FAILED_ENOUGH"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    if isinstance(existing_decision, Mapping) and bool(existing_decision.get("trade_eligible")):
        diagnostics["reason"] = "REVERSAL_EXISTING_TRADE_READY"
        return dict(existing_decision), existing_reason, diagnostics

    acceleration = strategy._acceleration(df1m, current_price)
    if not isinstance(acceleration, Mapping):
        diagnostics["reason"] = "REVERSAL_NO_FRESH_ACCELERATION"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics
    if str(acceleration.get("direction") or "").upper() != new_direction:
        diagnostics["reason"] = "REVERSAL_ACCELERATION_NOT_OPPOSITE"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    move3 = _aligned(new_direction, acceleration.get("move_3m_percent"))
    move5 = _aligned(new_direction, acceleration.get("move_5m_percent"))
    volume = _sf(acceleration.get("volume_ratio"))
    breakout = bool(acceleration.get("breakout"))
    diagnostics.update({
        "move3": round(move3, 4),
        "move5": round(move5, 4),
        "volume": round(volume, 3),
        "breakout": breakout,
    })
    if move3 < MIN_MOVE_3M_PERCENT or move5 < MIN_MOVE_5M_PERCENT:
        diagnostics["reason"] = "REVERSAL_MOMENTUM_WEAK"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics
    if volume < MIN_VOLUME_RATIO_1M:
        diagnostics["reason"] = "REVERSAL_VOLUME_WEAK"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics
    if not breakout and move3 < 0.30 and move5 < 0.45:
        diagnostics["reason"] = "REVERSAL_NO_BREAK_OR_PROGRESS"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    s5 = strategy._structure(df5m, current_price)
    s15 = strategy._structure(df15m, current_price)
    s1h = strategy._structure(df1h, current_price)
    if s5 is None or s15 is None or s1h is None:
        diagnostics["reason"] = "REVERSAL_STRUCTURE_DATA"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    d5 = str(s5.get("direction") or "").upper()
    d15 = str(s15.get("direction") or "").upper()
    d1h = str(s1h.get("direction") or "").upper()
    diagnostics["structures"] = {"5m": d5, "15m": d15, "1h": d1h}
    if d5 != new_direction:
        diagnostics["reason"] = "REVERSAL_5M_NOT_FLIPPED"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics
    if d15 not in {new_direction, "NEUTRAL"}:
        diagnostics["reason"] = "REVERSAL_15M_STILL_OLD_DIRECTION"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics
    if d15 == "NEUTRAL" and not breakout and move5 < 0.50:
        diagnostics["reason"] = "REVERSAL_15M_NEUTRAL_NEEDS_STRENGTH"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    relative = strategy._relative_strength(
        new_direction,
        _sf(acceleration.get("move_5m_percent")),
        context.major_move_5m_percent,
    )
    _, market_allowed = strategy._market_component(new_direction, context)
    exceptional_flip = bool(
        failure >= 1.50
        and breakout
        and move5 >= 0.75
        and relative >= 0.50
    )
    if d1h == old_direction and not exceptional_flip:
        diagnostics["reason"] = "REVERSAL_1H_STILL_OLD_DIRECTION"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics
    if not market_allowed and not exceptional_flip:
        diagnostics["reason"] = "REVERSAL_MARKET_HARD_OPPOSED"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    extension = _sf(s5.get("extension_atr"))
    if extension > MAX_EXTENSION_ATR:
        diagnostics["reason"] = "REVERSAL_TOO_EXTENDED"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    base_score = 0
    if isinstance(existing_decision, Mapping) and str(existing_decision.get("direction") or "").upper() == new_direction:
        base_score = int(_sf(existing_decision.get("score")))
    market_preferred = str(context.preferred_direction or "").upper()
    reversal_score = _score(
        base_score=base_score,
        failure_percent=failure,
        move3=move3,
        move5=move5,
        volume=volume,
        breakout=breakout,
        s15_direction=d15,
        s1h_direction=d1h,
        new_direction=new_direction,
        market_preferred=market_preferred,
        relative_strength=relative,
    )
    diagnostics["score"] = reversal_score
    diagnostics["relative_strength"] = round(relative, 4)
    if reversal_score < MIN_REVERSAL_SCORE:
        diagnostics["reason"] = "REVERSAL_SCORE_WEAK"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    risk, risk_reason = strategy._risk_plan(new_direction, current_price, s5, s15)
    if risk is None:
        diagnostics["reason"] = f"REVERSAL_{risk_reason}"
        return dict(existing_decision) if isinstance(existing_decision, Mapping) else None, existing_reason, diagnostics

    promoted = dict(existing_decision or {})
    promoted.update({
        "symbol": str(symbol),
        "direction": new_direction,
        "source": strategy.SOURCE,
        "score": reversal_score,
        "stage": "READY",
        "current_price": round(current_price, 10),
        "quote_volume_24h": round(_sf(quote_volume_24h), 2),
        "market_regime": context.regime,
        "market_label": strategy.market_label(context),
        "market_score": context.score,
        "market_strength": context.strength,
        "market_preferred_direction": context.preferred_direction,
        "market_breadth_5m": context.breadth_5m,
        "major_move_5m_percent": context.major_move_5m_percent,
        "independent_move": exceptional_flip,
        "move_1m_percent": _sf(acceleration.get("move_1m_percent")),
        "move_3m_percent": _sf(acceleration.get("move_3m_percent")),
        "move_5m_percent": _sf(acceleration.get("move_5m_percent")),
        "volume_ratio_1m": volume,
        "breakout_20m": breakout,
        "relative_strength_5m": round(relative, 4),
        "extension_atr_5m": round(extension, 3),
        "structure_5m": d5,
        "structure_15m": d15,
        "structure_1h": d1h,
        "alert_eligible": False,
        "trade_eligible": True,
        "risk_reject_reason": None,
        "reversal_capture": True,
        "reversal_capture_version": VERSION,
        "reversal_old_direction": old_direction,
        "reversal_old_alert_price": alert_price,
        "reversal_old_failure_percent": round(failure, 4),
        "reversal_capture_score": reversal_score,
    })
    promoted.update(risk)
    diagnostics["promoted"] = True
    diagnostics["reason"] = "REVERSAL_READY"
    diagnostics["risk_percent"] = promoted.get("risk_percent")
    diagnostics["room_r"] = promoted.get("room_r")
    _record_promotion(state, alert, promoted, now)
    return promoted, "OK", diagnostics


def install(runner: Any) -> None:
    """Install outside the normal tracking stack so failed alerts stay visible."""
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    original_select = runner._select_deep_scan
    original_analyze = runner.analyze_candidate
    original_decision_to_signal = runner.decision_to_signal
    runtime: Dict[str, Any] = {"state": None}

    def select_with_reversal_priority(rows, sample_moves, state):
        runtime["state"] = state
        selected = list(original_select(rows, sample_moves, state))
        forced = reversal_priority_symbols(state, rows, now=runner.bot.now_ts())
        if not forced:
            return selected
        merged = []
        seen = set()
        for symbol in forced + selected:
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            merged.append(symbol)
        limit = max(len(selected), len(forced))
        merged = merged[:limit]
        print("REVERSAL CAPTURE TAKİP ÖNCELİĞİ:", forced)
        return merged

    def analyze_with_reversal(*args, **kwargs):
        decision, reason = original_analyze(*args, **kwargs)

        def arg(name: str, index: int, default=None):
            if name in kwargs:
                return kwargs.get(name)
            return args[index] if len(args) > index else default

        symbol = str(arg("symbol", 0, "") or "")
        current_price = _sf(arg("current_price", 5, 0.0))
        context = arg("context", 7)
        if not symbol or current_price <= 0 or context is None:
            return decision, reason

        promoted, promoted_reason, diag = evaluate_reversal(
            state=runtime.get("state"),
            symbol=symbol,
            df1m=arg("df1m", 1),
            df5m=arg("df5m", 2),
            df15m=arg("df15m", 3),
            df1h=arg("df1h", 4),
            current_price=current_price,
            quote_volume_24h=_sf(arg("quote_volume_24h", 6, 0.0)),
            context=context,
            existing_decision=decision,
            existing_reason=str(reason or ""),
            now=runner.bot.now_ts(),
        )
        if diag.get("promoted"):
            print(
                "DEAD -> TERS YÖN İŞLEM ADAYI:",
                symbol,
                diag.get("old_direction"), "->", diag.get("new_direction"),
                "| failure=", diag.get("failure_percent"),
                "| score=", diag.get("score"),
                "| 3m/5m=", diag.get("move3"), diag.get("move5"),
                "| volume=", diag.get("volume"),
            )
        return promoted, promoted_reason

    def decision_to_signal_with_reversal(decision):
        signal = original_decision_to_signal(decision)
        if not isinstance(signal, dict) or not isinstance(decision, Mapping):
            return signal
        if not bool(decision.get("reversal_capture")):
            return signal
        signal.update({
            "entry_type": "MARKET_FIRST_REVERSAL_CAPTURE",
            "quality": "A REVERSAL CAPTURE",
            "quality_note": "Önceki erken yön öldü; ters 5M momentum/yapı kontrollü biçimde teyit edildi.",
            "reversal_capture": True,
            "reversal_capture_version": decision.get("reversal_capture_version"),
            "reversal_old_direction": decision.get("reversal_old_direction"),
            "reversal_old_alert_price": decision.get("reversal_old_alert_price"),
            "reversal_old_failure_percent": decision.get("reversal_old_failure_percent"),
            "reversal_capture_score": decision.get("reversal_capture_score"),
        })
        return signal

    runner._select_deep_scan = select_with_reversal_priority
    runner.analyze_candidate = analyze_with_reversal
    runner.decision_to_signal = decision_to_signal_with_reversal


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "window_minutes": REVERSAL_WINDOW_SECONDS // 60,
        "min_old_failure_percent": MIN_OLD_DIRECTION_FAILURE_PERCENT,
        "min_move_3m_percent": MIN_MOVE_3M_PERCENT,
        "min_move_5m_percent": MIN_MOVE_5M_PERCENT,
        "min_volume_ratio_1m": MIN_VOLUME_RATIO_1M,
        "min_reversal_score": MIN_REVERSAL_SCORE,
        "max_extension_atr": MAX_EXTENSION_ATR,
        "max_forced_symbols": MAX_FORCED_SYMBOLS,
        "safety_guards_bypassed": False,
    }
