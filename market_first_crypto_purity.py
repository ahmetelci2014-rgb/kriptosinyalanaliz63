"""Final crypto-purity gate for the single Market First live universe.

Why this exists
---------------
OKX/CCXT can expose stock, ETF and commodity derivatives through the same USDT
contract catalogue as crypto. Some of those markets are represented by CCXT as
``contract``/``future`` rather than ``swap``. The older guard only applied its
strict category test when ``swap=True``; therefore TradFi contracts could slip
through even though OKX metadata classified them as stocks/commodities.

This module treats *every derivative contract* as a derivative and requires
positive crypto evidence before Market First may scan it. Ambiguous derivative
metadata fails closed. It never places or changes exchange orders.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Tuple

import crypto_universe_guard as guard

CRYPTO_INST_CATEGORY = "1"
NON_CRYPTO_INST_CATEGORIES = {"3", "4", "5", "6"}
CRYPTO_DERIVATIVE_GROUP_IDS = {"4", "5"}
NON_CRYPTO_DERIVATIVE_GROUP_IDS = {"6", "7", "8", "10"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _info(market: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = market.get("info") if isinstance(market, Mapping) else None
    return raw if isinstance(raw, Mapping) else {}


def _category(market: Mapping[str, Any]) -> str:
    info = _info(market)
    return _text(info.get("instCategory") or market.get("instCategory"))


def _group_id(market: Mapping[str, Any]) -> str:
    info = _info(market)
    return _text(info.get("groupId") or market.get("groupId"))


def _state(market: Mapping[str, Any]) -> str:
    info = _info(market)
    return _text(info.get("state") or market.get("state")).lower()


def _is_derivative(market: Mapping[str, Any]) -> bool:
    return bool(
        market.get("swap")
        or market.get("contract")
        or market.get("future")
    )


def crypto_derivative_exclusion_reason(market: Mapping[str, Any]) -> str:
    """Return empty only for a positively identified live crypto USDT contract."""
    if not isinstance(market, Mapping):
        return "INVALID_METADATA"
    if not _is_derivative(market):
        return "NOT_DERIVATIVE"
    if str(market.get("quote") or "").upper() != "USDT":
        return "NOT_USDT_QUOTED"
    if str(market.get("settle") or "").upper() != "USDT":
        return "NOT_USDT_SETTLED"
    if market.get("active") is not True:
        return "NOT_ACTIVE"

    expiry = market.get("expiry")
    if expiry not in (None, 0, ""):
        return "EXPIRING_FUTURE"

    state = _state(market)
    if state and state != "live":
        return f"STATE_{state.upper()}"

    category = _category(market)
    group_id = _group_id(market)

    if category in NON_CRYPTO_INST_CATEGORIES:
        return f"NON_CRYPTO_CATEGORY_{category}"
    if group_id in NON_CRYPTO_DERIVATIVE_GROUP_IDS:
        return f"NON_CRYPTO_GROUP_{group_id}"

    # Positive evidence is mandatory. Category is strongest; group is fallback
    # for OKX rows where instCategory is temporarily absent.
    if category:
        if category != CRYPTO_INST_CATEGORY:
            return "CRYPTO_CATEGORY_NOT_CONFIRMED"
    elif group_id not in CRYPTO_DERIVATIVE_GROUP_IDS:
        return "CRYPTO_METADATA_UNVERIFIED"

    symbol = guard.market_bot_symbol(dict(market))
    if not symbol:
        return "SYMBOL_UNRESOLVED"
    if not guard.is_user_tradable_futures_symbol(symbol):
        return "USER_UNTRADABLE"
    if guard.ACCOUNT_ALLOWLIST_ACTIVE and symbol not in guard.ACCOUNT_TRADABLE_FUTURES_SYMBOLS:
        return "ACCOUNT_UNAVAILABLE"

    return ""


def filter_market_first_universe(
    exchange: Any,
    rows: List[Dict[str, Any]],
    universe: Mapping[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Remove every non-crypto/ambiguous derivative from Market First."""
    markets = exchange.load_markets()
    allowed_symbols = set()
    reasons_by_symbol: Dict[str, str] = {}

    for key, market in (markets or {}).items():
        if not isinstance(market, Mapping) or not _is_derivative(market):
            continue
        symbol = guard.market_bot_symbol(dict(market), key)
        if not symbol:
            continue
        reason = crypto_derivative_exclusion_reason(market)
        if not reason:
            allowed_symbols.add(symbol)
            reasons_by_symbol.pop(symbol, None)
        elif symbol not in allowed_symbols:
            reasons_by_symbol.setdefault(symbol, reason)

    kept_rows = [
        dict(row)
        for row in rows
        if str(row.get("symbol") or "") in allowed_symbols
    ]
    kept_universe = {
        symbol: dict(row)
        for symbol, row in universe.items()
        if symbol in allowed_symbols
    }

    removed_symbols = sorted(
        {
            str(row.get("symbol") or "")
            for row in rows
            if str(row.get("symbol") or "") not in allowed_symbols
        }
    )
    summary = {
        "before": len(rows),
        "after": len(kept_rows),
        "excluded": max(0, len(rows) - len(kept_rows)),
        "sample_excluded": [
            {
                "symbol": symbol,
                "reason": reasons_by_symbol.get(symbol, "NO_POSITIVE_CRYPTO_EVIDENCE"),
            }
            for symbol in removed_symbols[:12]
        ],
    }
    return kept_rows, kept_universe, summary
