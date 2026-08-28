"""Audit-driven safety and early-capture fixes for the single Market First system.

This module does not create a second strategy. It patches only four weaknesses
found in the live audit:
- keep non-crypto/RWA contracts out of the Market First universe,
- prioritize moderate fresh movers before already-extended leaders,
- stop treating every EMA/ATR extension as a late move,
- add a final spread/depth guard and profit-after-cost ML labelling.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import crypto_universe_guard as universe_guard
from market_first_strategy import MAJOR_WEIGHTS, MIN_ALERT_SCORE

MAX_AUDITED_DEEP_SCAN = 64
EARLY_SAMPLE_MIN_PERCENT = 0.18
EARLY_SAMPLE_MAX_PERCENT = 1.35
EARLY_SAMPLE_COUNT = 30
MIN_DEEP_SCAN_QUOTE_VOLUME = 250_000.0

# Manual-signal quality guard. It is deliberately tolerant enough not to reject
# normal altcoin noise, but it blocks obviously poor execution conditions.
MAX_SPREAD_BPS = 35.0
MIN_NEAR_SIDE_DEPTH_QUOTE = 10_000.0
DEPTH_DISTANCE_PERCENT = 0.50
ORDER_BOOK_LIMIT = 30

# Populated whenever the strict universe is refreshed, so the final liquidity
# check can call CCXT with the exact unified symbol.
LIVE_CCXT_SYMBOLS: Dict[str, str] = {}


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def strict_crypto_universe(
    exchange: Any,
    rows: List[Dict[str, Any]],
    universe: Mapping[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Apply the repository's strict crypto/account guard to Market First.

    The legacy runner only checked generic USDT perpetual metadata. OKX can expose
    tokenized stocks/RWA contracts through the same market feed, so Market First
    must require positive crypto evidence as well.
    """
    markets = exchange.load_markets()
    try:
        universe_guard.refresh_account_tradable_futures_from_env()
    except Exception:
        # Public metadata remains the strict fallback; never broaden the universe.
        pass

    filtered, excluded = universe_guard.filter_crypto_markets(markets)
    allowed: Dict[str, str] = {}
    for key, market in filtered.items():
        if not isinstance(market, Mapping):
            continue
        if not (market.get("swap") or market.get("contract")):
            continue
        label = universe_guard.market_bot_symbol(dict(market), key)
        ccxt_symbol = str(market.get("symbol") or "").strip()
        if label and ccxt_symbol:
            allowed[label] = ccxt_symbol

    LIVE_CCXT_SYMBOLS.clear()
    LIVE_CCXT_SYMBOLS.update(allowed)

    kept_rows = [row for row in rows if str(row.get("symbol") or "") in allowed]
    kept_universe = {
        symbol: dict(row)
        for symbol, row in universe.items()
        if symbol in allowed
    }
    for symbol, row in kept_universe.items():
        row["ccxt_symbol"] = allowed[symbol]

    summary = {
        "before": len(rows),
        "after": len(kept_rows),
        "excluded": max(0, len(rows) - len(kept_rows)),
        "metadata_excluded": len(excluded),
        "account_allowlist": bool(universe_guard.ACCOUNT_ALLOWLIST_ACTIVE),
    }
    return kept_rows, kept_universe, summary


def select_deep_scan(
    rows: List[Dict[str, Any]],
    sample_moves: Mapping[str, float],
    state: Dict[str, Any],
    original_selector: Callable[[List[Dict[str, Any]], Mapping[str, float], Dict[str, Any]], List[str]],
) -> List[str]:
    """Prefer fresh moderate moves, then preserve the original ranking/rotation.

    The old selection mostly favored the largest movers, which meant the deep
    analysis often started after the move had already become late. A dedicated
    moderate-move lane gives the system a better chance to inspect acceleration
    while it is still forming.
    """
    liquid_rows = [
        row for row in rows
        if _sf(row.get("quote_volume")) >= MIN_DEEP_SCAN_QUOTE_VOLUME
    ]
    selected: List[str] = []
    seen = set(MAJOR_WEIGHTS)

    def add(symbol: str) -> None:
        if symbol and symbol not in seen and len(selected) < MAX_AUDITED_DEEP_SCAN:
            seen.add(symbol)
            selected.append(symbol)

    fresh = []
    for row in liquid_rows:
        symbol = str(row.get("symbol") or "")
        move = abs(_sf(sample_moves.get(symbol)))
        if EARLY_SAMPLE_MIN_PERCENT <= move <= EARLY_SAMPLE_MAX_PERCENT:
            volume = max(MIN_DEEP_SCAN_QUOTE_VOLUME, _sf(row.get("quote_volume")))
            # Favour movement first, with only a mild liquidity boost.
            priority = move * (1.0 + min(2.0, math.log10(volume / MIN_DEEP_SCAN_QUOTE_VOLUME + 1.0)))
            fresh.append((priority, symbol))

    for _, symbol in sorted(fresh, reverse=True)[:EARLY_SAMPLE_COUNT]:
        add(symbol)

    for symbol in original_selector(liquid_rows, sample_moves, state):
        add(str(symbol))

    # Use any remaining budget on the strongest still-liquid sample movers.
    for row in sorted(
        liquid_rows,
        key=lambda item: abs(_sf(sample_moves.get(str(item.get("symbol") or "")))),
        reverse=True,
    ):
        add(str(row.get("symbol") or ""))
        if len(selected) >= MAX_AUDITED_DEEP_SCAN:
            break

    return selected


def revise_late_decision(
    decision: Optional[Dict[str, Any]],
    reason: str,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Separate a fresh breakout from a merely stretched EMA baseline.

    A large 5m/3m move remains late. But if the only late signal is high EMA/ATR
    extension while the newly observed move itself is still small, a strong
    breakout may be surfaced as EARLY instead of being discarded. It is never
    promoted straight to a live trade; risk geometry must be rebuilt on a later
    confirmed scan.
    """
    if not isinstance(decision, dict) or str(decision.get("stage") or "") != "LATE":
        return decision, reason

    move3 = abs(_sf(decision.get("move_3m_percent")))
    move5 = abs(_sf(decision.get("move_5m_percent")))
    score = int(decision.get("score") or 0)
    breakout = bool(decision.get("breakout_20m"))

    hard_late = move5 >= 4.50 or move3 >= 3.20
    if hard_late:
        return decision, reason

    fresh_breakout = breakout and move5 <= 1.35 and move3 <= 1.15
    if fresh_breakout and score >= MIN_ALERT_SCORE:
        rescued = dict(decision)
        rescued["stage"] = "EARLY"
        rescued["alert_eligible"] = True
        rescued["trade_eligible"] = False
        rescued["late_rescued"] = True
        rescued["late_rescue_reason"] = "FRESH_BREAKOUT_LOW_PROGRESS"
        return rescued, "OK"

    # Small movement + high old-trend extension is stale context, not proof that
    # the current impulse itself has already run too far.
    if move5 < 1.00 and move3 < 0.85:
        return None, "STALE_EXTENSION"

    return decision, reason


def evaluate_liquidity(exchange: Any, symbol: str) -> Dict[str, Any]:
    """Check spread and near-price two-sided depth immediately before Telegram."""
    ccxt_symbol = LIVE_CCXT_SYMBOLS.get(str(symbol))
    if not ccxt_symbol:
        return {
            "blocked": False,
            "reason": "NO_CCXT_SYMBOL",
            "available": False,
        }

    try:
        book = exchange.fetch_order_book(ccxt_symbol, limit=ORDER_BOOK_LIMIT)
        bids = list((book or {}).get("bids") or [])
        asks = list((book or {}).get("asks") or [])
        if not bids or not asks:
            return {"blocked": False, "reason": "BOOK_EMPTY", "available": False}

        best_bid = _sf(bids[0][0] if len(bids[0]) >= 2 else 0.0)
        best_ask = _sf(asks[0][0] if len(asks[0]) >= 2 else 0.0)
        if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
            return {"blocked": False, "reason": "BOOK_BAD_TOP", "available": False}

        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 10_000.0

        def depth(levels: List[Any], is_bid: bool) -> float:
            total = 0.0
            for level in levels:
                if not isinstance(level, (list, tuple)) or len(level) < 2:
                    continue
                price = _sf(level[0])
                amount = _sf(level[1])
                if price <= 0 or amount <= 0:
                    continue
                distance = ((mid - price) if is_bid else (price - mid)) / mid * 100.0
                if 0.0 <= distance <= DEPTH_DISTANCE_PERCENT:
                    total += price * amount
            return total

        bid_depth = depth(bids, True)
        ask_depth = depth(asks, False)
        min_side = min(bid_depth, ask_depth)
        blocked = spread_bps > MAX_SPREAD_BPS or min_side < MIN_NEAR_SIDE_DEPTH_QUOTE
        reason = (
            "SPREAD_TOO_WIDE" if spread_bps > MAX_SPREAD_BPS
            else "DEPTH_TOO_THIN" if min_side < MIN_NEAR_SIDE_DEPTH_QUOTE
            else "LIQUIDITY_OK"
        )
        return {
            "blocked": bool(blocked),
            "reason": reason,
            "available": True,
            "spread_bps": round(spread_bps, 3),
            "bid_depth_quote": round(bid_depth, 2),
            "ask_depth_quote": round(ask_depth, 2),
            "min_side_depth_quote": round(min_side, 2),
        }
    except Exception as exc:
        # Missing public depth should not silently become a directional opinion.
        return {
            "blocked": False,
            "reason": f"BOOK_ERROR_{type(exc).__name__}",
            "available": False,
        }


def reconcile_samples_net_r(store: Dict[str, Any], ledger: Mapping[str, Any]) -> int:
    """Label Market First samples by profit after costs when that result exists.

    The previous target was essentially "TP1 before clean stop". That is useful
    for hit-rate research but can mark a barely-profitable or cost-negative path
    as a success. The audit target is now positive net R after costs, with a
    conservative fallback for older rows that do not yet contain net-R fields.
    """
    trades = ledger.get("trades", {}) if isinstance(ledger, Mapping) else {}
    if not isinstance(trades, Mapping):
        return 0

    changed = 0
    samples = store.setdefault("samples", {})
    for trade_id, sample in samples.items():
        if not isinstance(sample, dict) or sample.get("resolved"):
            continue
        trade = trades.get(trade_id)
        if not isinstance(trade, Mapping):
            continue
        result = str(trade.get("final_result") or "").upper()
        if not result:
            continue
        if result.startswith("INVALID_") or str(trade.get("status") or "").upper() == "INVALID":
            sample["label"] = None
            sample["resolved"] = True
            sample["ignored_reason"] = "INVALID_MARKET"
            sample["resolved_result"] = result
            changed += 1
            continue

        net_r = trade.get("net_r_after_costs")
        gross_r = trade.get("r_result")
        if net_r not in (None, ""):
            outcome = _sf(net_r)
            sample["label"] = 1 if outcome > 0.0 else 0
            sample["resolved_net_r"] = round(outcome, 6)
            sample["label_target"] = "NET_R_AFTER_COSTS_POSITIVE"
        elif gross_r not in (None, ""):
            outcome = _sf(gross_r)
            sample["label"] = 1 if outcome > 0.0 else 0
            sample["resolved_net_r"] = None
            sample["label_target"] = "R_RESULT_POSITIVE_FALLBACK"
        elif result == "SL":
            sample["label"] = 0
            sample["resolved_net_r"] = None
            sample["label_target"] = "SL_FALLBACK"
        elif bool(trade.get("tp1_hit")):
            sample["label"] = 1
            sample["resolved_net_r"] = None
            sample["label_target"] = "TP1_FALLBACK"
        else:
            sample["label"] = None
            sample["ignored_reason"] = "AMBIGUOUS_NO_R"

        sample["resolved"] = True
        sample["resolved_result"] = result
        sample["resolved_at"] = int(trade.get("closed_at") or 0)
        changed += 1

    return changed
