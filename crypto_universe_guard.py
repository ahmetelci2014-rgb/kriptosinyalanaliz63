"""Crypto-only market universe guard for Premium live scanning.

OKX now exposes USDT perpetuals for non-crypto assets such as stocks/RWA.
The Premium crypto bot must not mix those instruments into its live universe.

This module deliberately fails open when old/legacy CCXT market metadata does not
contain an asset category, but it rejects explicit non-crypto categories and the
OKX RWA swap fee groups as a fallback.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Dict, List, Tuple

CRYPTO_INST_CATEGORY = "1"
NON_CRYPTO_INST_CATEGORIES = {"3", "4", "5", "6"}  # stocks, commodities, forex, bonds
RWA_SWAP_GROUP_IDS = {"6", "7"}


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


def filter_crypto_markets(
    markets: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    kept: Dict[str, Dict[str, Any]] = {}
    excluded: List[Dict[str, str]] = []

    for key, market in (markets or {}).items():
        if not isinstance(market, dict):
            kept[key] = market
            continue

        if is_crypto_market(market):
            kept[key] = market
            continue

        info = _info(market)
        excluded.append({
            "key": _text(key),
            "symbol": _text(market.get("symbol") or info.get("instId") or key),
            "inst_category": market_asset_category(market),
            "group_id": market_group_id(market),
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
            symbols = ", ".join(row["symbol"] for row in excluded[:12])
            suffix = " ..." if len(excluded) > 12 else ""
            print(
                "PREMIUM CRYPTO-ONLY GUARD | non-crypto excluded:",
                len(excluded),
                "|",
                symbols + suffix,
            )
        return original(filtered)

    market_scan_module.eligible_markets = guarded
    market_scan_module._premium_crypto_only_guard_installed = True
