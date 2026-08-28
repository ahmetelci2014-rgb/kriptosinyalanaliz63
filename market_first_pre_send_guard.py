"""Final market-safety recheck for the single Market First system.

The main Market First scan already decides direction from BTC/ETH/SOL + breadth.
This module adds only a last-moment safety check immediately before Telegram:
if the majors suddenly move hard against the candidate while the scan is still
running, the signal is withheld. Rejected signals are tracked in a small shadow
audit so we can later measure whether the guard prevented losses or hid winners.

This is not a second strategy and it never creates a trade on its own.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping

import main as bot
from market_first_strategy import MAJOR_WEIGHTS

SHADOW_FILE = "market_first_guard_shadow.json"
MAX_SHADOW_ITEMS = 500
SHADOW_MAX_AGE_SECONDS = 6 * 60 * 60

# A guard is deliberately conservative. A normal pullback must not cancel a
# good altcoin setup. We block only on a fresh, broad major-coin reversal or a
# very sharp BTC move confirmed by the weighted major basket.
OPPOSING_MAJOR_MOVE_PERCENT = 0.25
WEIGHTED_SHOCK_PERCENT = 0.45
BTC_SHOCK_PERCENT = 0.70
BTC_SHOCK_WEIGHTED_CONFIRM_PERCENT = 0.35


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _pct(start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    return (end / start - 1.0) * 100.0


def evaluate_pre_send_market(direction: str, major_moves: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a fail-open final market guard decision.

    `major_moves` contains the current 5m-candle move for BTC/ETH/SOL. Positive
    alignment means the move supports the candidate direction; negative means
    it is against it.
    """
    direction = str(direction or "").upper()
    sign = 1.0 if direction == "LONG" else -1.0

    available: Dict[str, float] = {}
    weighted = 0.0
    weight_total = 0.0
    for symbol, weight in MAJOR_WEIGHTS.items():
        if symbol not in major_moves:
            continue
        move = _sf(major_moves.get(symbol), float("nan"))
        if not math.isfinite(move):
            continue
        available[symbol] = move
        weighted += move * weight
        weight_total += weight

    if len(available) < 2 or weight_total <= 0:
        return {
            "blocked": False,
            "reason": "INSUFFICIENT_FRESH_MAJOR_DATA",
            "direction": direction,
            "major_moves": available,
            "weighted_move_percent": 0.0,
            "directional_alignment_percent": 0.0,
            "opposing_major_count": 0,
        }

    weighted /= weight_total
    alignment = weighted * sign
    opposing_count = sum(
        1
        for move in available.values()
        if move * sign <= -OPPOSING_MAJOR_MOVE_PERCENT
    )
    btc_alignment = _sf(available.get("BTCUSDT"), 0.0) * sign

    broad_shock = alignment <= -WEIGHTED_SHOCK_PERCENT and opposing_count >= 2
    btc_shock = (
        "BTCUSDT" in available
        and btc_alignment <= -BTC_SHOCK_PERCENT
        and alignment <= -BTC_SHOCK_WEIGHTED_CONFIRM_PERCENT
    )
    blocked = bool(broad_shock or btc_shock)
    if broad_shock:
        reason = "FRESH_MAJOR_REVERSAL"
    elif btc_shock:
        reason = "FRESH_BTC_SHOCK"
    else:
        reason = "MARKET_STILL_ACCEPTABLE"

    return {
        "blocked": blocked,
        "reason": reason,
        "direction": direction,
        "major_moves": {key: round(value, 4) for key, value in available.items()},
        "weighted_move_percent": round(weighted, 4),
        "directional_alignment_percent": round(alignment, 4),
        "opposing_major_count": opposing_count,
    }


def fetch_fresh_major_moves(exchange: Any) -> Dict[str, float]:
    """Fetch only the data needed for the final safety recheck."""
    moves: Dict[str, float] = {}
    for symbol in MAJOR_WEIGHTS:
        try:
            frame = bot.fetch_df(exchange, symbol, "5m", 20, min_len=8)
            if frame is None or len(frame) < 2:
                continue
            current = bot.get_current_price(exchange, symbol)
            if current is None or _sf(current) <= 0:
                current = _sf(frame.iloc[-1]["close"])
            open_price = _sf(frame.iloc[-1]["open"])
            if open_price <= 0 or _sf(current) <= 0:
                continue
            moves[symbol] = round(_pct(open_price, _sf(current)), 5)
        except Exception as exc:
            print("Pre-send major recheck veri hatası:", symbol, type(exc).__name__)
    return moves


def _load_shadow() -> Dict[str, Any]:
    data = bot.load_json_file(SHADOW_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("version", 1)
    data.setdefault("items", {})
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    return data


def _summary(items: Mapping[str, Any]) -> Dict[str, Any]:
    counts = {"OPEN": 0, "OBSERVED_TP1": 0, "OBSERVED_SL": 0, "EXPIRED": 0}
    for item in items.values():
        if not isinstance(item, Mapping):
            continue
        status = str(item.get("status") or "OPEN")
        counts[status] = counts.get(status, 0) + 1
    decided = counts.get("OBSERVED_TP1", 0) + counts.get("OBSERVED_SL", 0)
    win_rate = counts.get("OBSERVED_TP1", 0) / decided if decided else None
    return {
        **counts,
        "decided": decided,
        "observed_tp1_rate": round(win_rate, 4) if win_rate is not None else None,
    }


def _save_shadow(data: Dict[str, Any]) -> None:
    items = data.setdefault("items", {})
    if len(items) > MAX_SHADOW_ITEMS:
        unresolved = [
            (key, item)
            for key, item in items.items()
            if str((item or {}).get("status") or "OPEN") == "OPEN"
        ]
        resolved = sorted(
            [
                (key, item)
                for key, item in items.items()
                if str((item or {}).get("status") or "OPEN") != "OPEN"
            ],
            key=lambda pair: int((pair[1] or {}).get("resolved_at") or (pair[1] or {}).get("rejected_at") or 0),
            reverse=True,
        )
        keep_resolved = max(0, MAX_SHADOW_ITEMS - len(unresolved))
        data["items"] = dict(unresolved + resolved[:keep_resolved])
    data["summary"] = _summary(data.get("items", {}))
    data["updated_at"] = bot.now_ts()
    bot.save_json_file(SHADOW_FILE, data)


def register_shadow_rejection(signal: Mapping[str, Any], guard: Mapping[str, Any], now: int) -> None:
    """Record a blocked, otherwise-sendable signal for filter-quality auditing."""
    data = _load_shadow()
    items = data.setdefault("items", {})
    symbol = str(signal.get("symbol") or "")
    direction = str(signal.get("direction") or "")
    entry = _sf(signal.get("entry"))
    if not symbol or entry <= 0:
        return

    # Avoid duplicate audit rows if a workflow retry reaches the same signal.
    for item in items.values():
        if not isinstance(item, Mapping):
            continue
        if (
            str(item.get("symbol") or "") == symbol
            and str(item.get("direction") or "") == direction
            and abs(int(item.get("rejected_at") or 0) - int(now)) <= 10 * 60
            and str(item.get("status") or "OPEN") == "OPEN"
        ):
            return

    key = f"{int(now)}:{symbol}:{direction}"
    items[key] = {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": _sf(signal.get("sl")),
        "tp1": _sf(signal.get("tp1")),
        "tp2": _sf(signal.get("tp2")),
        "tp3": _sf(signal.get("tp3")),
        "score": int(signal.get("score") or 0),
        "rejected_at": int(now),
        "status": "OPEN",
        "last_price": entry,
        "best_price": entry,
        "worst_price": entry,
        "guard": dict(guard),
        "market_regime_at_candidate": str(signal.get("market_regime") or ""),
        "market_label_at_candidate": str(signal.get("market_label") or ""),
    }
    _save_shadow(data)


def update_shadow_results(universe: Mapping[str, Mapping[str, Any]], now: int) -> Dict[str, Any]:
    """Update rejected-signal outcomes from the same 5-minute universe snapshot.

    Outcomes are intentionally labelled OBSERVED_* because a 5-minute polling
    snapshot can miss an intrabar touch. These rows audit the guard; they are not
    fed directly into the live Random Forest training set.
    """
    data = _load_shadow()
    items = data.setdefault("items", {})
    changed = False

    for item in items.values():
        if not isinstance(item, dict) or str(item.get("status") or "OPEN") != "OPEN":
            continue
        rejected_at = int(item.get("rejected_at") or 0)
        symbol = str(item.get("symbol") or "")
        row = universe.get(symbol)
        current = _sf((row or {}).get("price")) if isinstance(row, Mapping) else 0.0

        if current > 0:
            entry = _sf(item.get("entry"))
            direction = str(item.get("direction") or "").upper()
            item["last_price"] = current
            item["best_price"] = (
                max(_sf(item.get("best_price"), entry), current)
                if direction == "LONG"
                else min(_sf(item.get("best_price"), entry) or entry, current)
            )
            item["worst_price"] = (
                min(_sf(item.get("worst_price"), entry) or entry, current)
                if direction == "LONG"
                else max(_sf(item.get("worst_price"), entry), current)
            )

            tp1 = _sf(item.get("tp1"))
            sl = _sf(item.get("sl"))
            if direction == "LONG":
                hit_tp1 = tp1 > entry and current >= tp1
                hit_sl = 0 < sl < entry and current <= sl
            else:
                hit_tp1 = 0 < tp1 < entry and current <= tp1
                hit_sl = sl > entry and current >= sl

            if hit_tp1:
                item["status"] = "OBSERVED_TP1"
                item["resolved_at"] = int(now)
                changed = True
            elif hit_sl:
                item["status"] = "OBSERVED_SL"
                item["resolved_at"] = int(now)
                changed = True

        if (
            str(item.get("status") or "OPEN") == "OPEN"
            and rejected_at
            and now - rejected_at >= SHADOW_MAX_AGE_SECONDS
        ):
            item["status"] = "EXPIRED"
            item["resolved_at"] = int(now)
            changed = True

    # Save every live update so last/best/worst observations survive workflow runs.
    if items or changed:
        _save_shadow(data)
    return dict(data.get("summary") or _summary(items))
