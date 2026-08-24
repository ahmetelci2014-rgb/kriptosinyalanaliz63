"""Strict crypto + user-tradable futures universe guard for Premium live scanning.

Safety goal
-----------
The live Telegram bot must never promote a symbol merely because global/public
OKX data happens to expose price history. A live candidate is accepted only
when its current market metadata positively identifies an active, live, USDT-
settled perpetual crypto contract. Ambiguous derivative metadata fails closed.

Account/region note
-------------------
Public/global OKX metadata cannot prove every account-specific regional product
restriction. User-confirmed unavailable symbols are therefore also blocked.
When read-only account instrument credentials are connected in the future,
/api/v5/account/instruments?instType=SWAP should be treated as the strongest
allowlist. This module is intentionally order-free and credential-free today.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Dict, List, Set, Tuple

VERSION = "PREMIUM_FUTURES_UNIVERSE_GUARD_V2_2026_08_24"

CRYPTO_INST_CATEGORY = "1"
NON_CRYPTO_INST_CATEGORIES = {"3", "4", "5", "6"}
CRYPTO_SWAP_GROUP_IDS = {"4", "5"}
RWA_SWAP_GROUP_IDS = {"6", "7"}
LIVE_OKX_STATES = {"live"}

# Confirmed from the user's actual OKX interface. Keep this evidence-based.
USER_UNTRADABLE_FUTURES_SYMBOLS = {
    "ETHWUSDT",
}

# Populated every time the current OKX market universe is filtered. This is a
# runtime safety registry only; it is never persisted as a trading signal.
VERIFIED_LIVE_FUTURES_SYMBOLS: Set[str] = set()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _info(market: Dict[str, Any]) -> Dict[str, Any]:
    raw = market.get("info") if isinstance(market, dict) else None
    return raw if isinstance(raw, dict) else {}


def market_asset_category(market: Dict[str, Any]) -> str:
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
    if not isinstance(market, dict):
        return ""
    info = _info(market)
    return _text(info.get("state") or market.get("state")).lower()


def canonical_bot_symbol(value: Any) -> str:
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


def has_positive_crypto_swap_evidence(market: Dict[str, Any]) -> bool:
    """Require positive crypto evidence instead of the old fail-open behavior.

    OKX documents crypto perpetual fee groups as 4/5 and RWA swap groups as 6/7.
    instCategory=1 is also accepted when exposed. If neither positive signal is
    present, the derivative is ambiguous and is excluded from live Telegram.
    """
    category = market_asset_category(market)
    group_id = market_group_id(market)

    if category:
        return category == CRYPTO_INST_CATEGORY
    if group_id:
        return group_id in CRYPTO_SWAP_GROUP_IDS
    return False


def is_crypto_market(market: Dict[str, Any]) -> bool:
    if not isinstance(market, dict):
        return False

    category = market_asset_category(market)
    if category in NON_CRYPTO_INST_CATEGORIES:
        return False

    group_id = market_group_id(market)
    if group_id in RWA_SWAP_GROUP_IDS:
        return False

    # Spot/non-swap markets are not part of the live futures decision here.
    if not market.get("swap", False):
        return True

    return has_positive_crypto_swap_evidence(market)


def market_exclusion_reason(market: Dict[str, Any], fallback: Any = "") -> str:
    """Return a strict live-universe rejection code, or empty when allowed."""
    if not isinstance(market, dict):
        return "INVALID_MARKET_METADATA"

    if not market.get("swap", False):
        return ""  # downstream eligible_markets ignores spot itself

    if str(market.get("quote") or "").upper() != "USDT":
        return "NOT_USDT_QUOTED_SWAP"
    if str(market.get("settle") or "").upper() != "USDT":
        return "NOT_USDT_SETTLED_SWAP"

    # Fail closed: active must be positively true, not merely 'not false'.
    if market.get("active") is not True:
        return "ACTIVE_SWAP_NOT_CONFIRMED"

    # Fail closed: current OKX raw instrument state must explicitly be live.
    state = market_state(market)
    if state not in LIVE_OKX_STATES:
        return "OKX_STATE_NOT_CONFIRMED" if not state else f"OKX_STATE_{state.upper()}"

    if not is_crypto_market(market):
        if market_asset_category(market) in NON_CRYPTO_INST_CATEGORIES:
            return "NON_CRYPTO_CATEGORY"
        if market_group_id(market) in RWA_SWAP_GROUP_IDS:
            return "RWA_SWAP_GROUP"
        return "CRYPTO_SWAP_METADATA_UNVERIFIED"

    bot_symbol = market_bot_symbol(market, fallback)
    if not bot_symbol:
        return "BOT_SYMBOL_UNRESOLVED"
    if bot_symbol in USER_UNTRADABLE_FUTURES_SYMBOLS:
        return "USER_INTERFACE_UNTRADABLE"

    return ""


def filter_crypto_markets(
    markets: Dict[str, Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, str]]]:
    """Filter and refresh the exact runtime futures allow-registry."""
    kept: Dict[str, Dict[str, Any]] = {}
    excluded: List[Dict[str, str]] = []
    verified: Set[str] = set()

    for key, market in (markets or {}).items():
        if not isinstance(market, dict):
            excluded.append({
                "key": _text(key),
                "symbol": _text(key),
                "bot_symbol": canonical_bot_symbol(key),
                "inst_category": "",
                "group_id": "",
                "state": "",
                "reason": "INVALID_MARKET_METADATA",
            })
            continue

        reason = market_exclusion_reason(market, key)
        if not reason:
            kept[key] = market
            if market.get("swap", False):
                symbol = market_bot_symbol(market, key)
                if symbol:
                    verified.add(symbol)
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

    VERIFIED_LIVE_FUTURES_SYMBOLS.clear()
    VERIFIED_LIVE_FUTURES_SYMBOLS.update(verified)
    return kept, excluded


def is_verified_live_futures_symbol(symbol: Any) -> bool:
    canonical = canonical_bot_symbol(symbol)
    return bool(
        canonical
        and canonical not in USER_UNTRADABLE_FUTURES_SYMBOLS
        and canonical in VERIFIED_LIVE_FUTURES_SYMBOLS
    )


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
                "PREMIUM STRICT FUTURES GUARD | excluded:",
                len(excluded),
                "| verified live crypto USDT swaps:",
                len(VERIFIED_LIVE_FUTURES_SYMBOLS),
                "|",
                symbols + suffix,
            )
        return original(filtered)

    market_scan_module.eligible_markets = guarded
    market_scan_module._premium_crypto_only_guard_installed = True
