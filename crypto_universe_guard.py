"""Crypto-only and user-tradable market universe guard for Premium live scanning.

OKX public/global market metadata can expose instruments that are not actually
available in the user's trading interface/region. The Premium bot must only send
signals for USDT perpetuals the user can manually open.

This module therefore applies three defensive layers before the existing market
scanner runs:
1) reject explicit non-crypto/RWA instruments,
2) reject inactive/non-live swap metadata when OKX exposes that state,
3) reject user-confirmed unavailable futures symbols.

The explicit unavailable-symbol set is intentionally small and evidence based.
A symbol is added only after its absence from the user's actual futures interface
is confirmed; this avoids broadly shrinking the live universe from assumptions.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Dict, List, Tuple

CRYPTO_INST_CATEGORY = "1"
NON_CRYPTO_INST_CATEGORIES = {"3", "4", "5", "6"}  # stocks, commodities, forex, bonds
RWA_SWAP_GROUP_IDS = {"6", "7"}

# User-confirmed futures that are not available in the actual OKX trading
# interface used for manual execution. Public/global OKX data may still expose
# market metadata or historical/live-looking data for these instruments.
USER_UNTRADABLE_FUTURES_SYMBOLS = {
    "ETHWUSDT",
}

LIVE_OKX_STATES = {"live"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _info(market: Dict[str, Any]) -> Dict[str, Any]:
    raw = market.get("info") if isinstance(market, dict) else None
    return raw if isinstance(raw, dict) else {}


def market_asset_category(market: Dict[str, Any]) -> str:
    """Return OKX instCategory when CCXT/raw metadata exposes it."""
    if not isinstance(market, dict):
        return ""
    info = _info(market)
    return _text(info.get("instCategory") or market.get("instCategory"))


def market_group_id(market: Dict[str, Any]) -> str:
    if not isinstance(market, dict):
        return ""
    info = _info(market)
    return _text(info.get("groupId") or market.get("groupId"))


def market_state(market: Dict[str, Any]) -> str:
    """Return raw OKX instrument state when available."""
    if not isinstance(market, dict):
        return ""
    info = _info(market)
    return _text(info.get("state") or market.get("state")).lower()


def canonical_bot_symbol(value: Any) -> str:
    """Convert CCXT/OKX/bot symbol shapes to e.g. ETHWUSDT."""
    raw = _text(value).upper()
    if not raw:
        return ""
    if "/" in raw:
        base = raw.split("/", 1)[0]
        return f"{base}USDT"
    compact = raw.replace("-", "").replace(":", "")
    if compact.endswith("USDTUSDT"):
        compact = compact[:-4]
    return compact


def market_bot_symbol(market: Dict[str, Any], fallback: Any = "") -> str:
    if not isinstance(market, dict):
        return canonical_bot_symbol(fallback)
    info = _info(market)
    symbol = market.get("symbol") or info.get("instId") or fallback
    return canonical_bot_symbol(symbol)


def is_user_tradable_futures_symbol(symbol: Any) -> bool:
    canonical = canonical_bot_symbol(symbol)
    return bool(canonical and canonical not in USER_UNTRADABLE_FUTURES_SYMBOLS)


def is_crypto_market(market: Dict[str, Any]) -> bool:
    """Keep crypto markets; reject explicitly identified OKX non-crypto/RWA swaps.

    instCategory is authoritative when present:
      1 crypto, 3 stocks, 4 commodities, 5 forex, 6 bonds.

    Some adapters may temporarily omit instCategory. In that case OKX swap RWA
    fee groups 6/7 are used as a defensive fallback. If neither field exists we
    keep the market so legacy crypto symbols are not accidentally removed.
    """
    category = market_asset_category(market)
    if category:
        return category == CRYPTO_INST_CATEGORY

    group_id = market_group_id(market)
    if group_id in RWA_SWAP_GROUP_IDS:
        return False

    return True


def market_exclusion_reason(market: Dict[str, Any], fallback: Any = "") -> str:
    """Return a live-universe rejection code, or empty string when allowed."""
    if not isinstance(market, dict):
        return ""

    if not is_crypto_market(market):
        return "NON_CRYPTO_OR_RWA"

    # Tradability checks matter only for derivatives. Spot markets are left
    # untouched because the downstream eligible_markets function ignores them.
    if not market.get("swap", False):
        return ""

    if market.get("active") is False:
        return "INACTIVE_SWAP"

    state = market_state(market)
    if state and state not in LIVE_OKX_STATES:
        return f"OKX_STATE_{state.upper()}"

    bot_symbol = market_bot_symbol(market, fallback)
    if bot_symbol in USER_UNTRADABLE_FUTURES_SYMBOLS:
        return "USER_INTERFACE_UNTRADABLE"

    return ""


def filter_crypto_markets(
    markets: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    """Filter to crypto markets that are also usable by the live futures bot."""
    kept: Dict[str, Dict[str, Any]] = {}
    excluded: List[Dict[str, str]] = []

    for key, market in (markets or {}).items():
        if not isinstance(market, dict):
            kept[key] = market
            continue

        reason = market_exclusion_reason(market, key)
        if not reason:
            kept[key] = market
            continue

        info = _info(market)
        excluded.append({
            "key": _text(key),
            "symbol": _text(market.get("symbol") or info.get("instId") or key),
            "bot_symbol": market_bot_symbol(market, key),
            "inst_category": market_asset_category(market),
            "group_id": market_group_id(market),
            "state": market_state(market),
            "reason": reason,
        })

    return kept, excluded


def install_crypto_only_guard(market_scan_module: Any) -> None:
    """Patch all_market_shadow.eligible_markets for this Premium process only."""
    if getattr(market_scan_module, "_premium_crypto_only_guard_installed", False):
        return

    original = getattr(market_scan_module, "eligible_markets")

    @wraps(original)
    def guarded(markets: Dict[str, Dict[str, Any]]):
        filtered, excluded = filter_crypto_markets(markets)
        if excluded:
            symbols = ", ".join(
                f"{row['symbol']}[{row['reason']}]"
                for row in excluded[:12]
            )
            suffix = " ..." if len(excluded) > 12 else ""
            print(
                "PREMIUM FUTURES UNIVERSE GUARD | excluded:",
                len(excluded),
                "|",
                symbols + suffix,
            )
        return original(filtered)

    market_scan_module.eligible_markets = guarded
    market_scan_module._premium_crypto_only_guard_installed = True
