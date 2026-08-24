"""Strict crypto + user-tradable futures universe guard for Premium live scanning.

Safety goal
-----------
The live Telegram bot must never promote a symbol merely because global/public
OKX data happens to expose price history. A live candidate is accepted only
when its current market metadata positively identifies an active, live, USDT-
settled perpetual crypto contract. Ambiguous derivative metadata fails closed.

Account/region safety
---------------------
OKX public/global data can still expose a perpetual that is unavailable to a
specific account/region. If read-only OKX credentials are present in environment
variables, this module also reads /api/v5/account/instruments?instType=SWAP and
uses that account-specific list as the strongest allowlist. It never places,
changes or cancels orders.
"""
from __future__ import annotations

import os
import time
from functools import wraps
from typing import Any, Dict, List, Set, Tuple

VERSION = "PREMIUM_FUTURES_UNIVERSE_GUARD_V3_2026_08_24"

CRYPTO_INST_CATEGORY = "1"
NON_CRYPTO_INST_CATEGORIES = {"3", "4", "5", "6"}
CRYPTO_SWAP_GROUP_IDS = {"4", "5"}
RWA_SWAP_GROUP_IDS = {"6", "7"}
LIVE_OKX_STATES = {"live"}
ACCOUNT_ALLOWLIST_REFRESH_SECONDS = 10 * 60

# Confirmed from the user's actual OKX interface. Keep this evidence-based.
USER_UNTRADABLE_FUTURES_SYMBOLS = {
    "ETHWUSDT",
}

# Runtime-only registries. They are never trading orders/signals.
VERIFIED_LIVE_FUTURES_SYMBOLS: Set[str] = set()
ACCOUNT_TRADABLE_FUTURES_SYMBOLS: Set[str] = set()
ACCOUNT_ALLOWLIST_ACTIVE = False
ACCOUNT_ALLOWLIST_LAST_REFRESH = 0


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

    # Raw OKX account/public instrument id: BTC-USDT-SWAP.
    if raw.endswith("-USDT-SWAP"):
        base = raw[: -len("-USDT-SWAP")]
        return f"{base}USDT" if base else ""

    # Unified CCXT market id: BTC/USDT:USDT.
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


def _positive_crypto_fields(category: str, group_id: str) -> bool:
    if category:
        return category == CRYPTO_INST_CATEGORY
    if group_id:
        return group_id in CRYPTO_SWAP_GROUP_IDS
    return False


def has_positive_crypto_swap_evidence(market: Dict[str, Any]) -> bool:
    """Require positive crypto evidence instead of old fail-open behavior."""
    return _positive_crypto_fields(
        market_asset_category(market),
        market_group_id(market),
    )


def _parse_account_instruments(payload: Any) -> Set[str]:
    """Parse OKX account/instruments SWAP response into bot symbols."""
    if not isinstance(payload, dict):
        return set()
    rows = payload.get("data")
    if not isinstance(rows, list):
        return set()

    out: Set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("instType") or "").upper() != "SWAP":
            continue
        if str(row.get("state") or "").lower() != "live":
            continue
        inst_id = str(row.get("instId") or "").upper()
        if not inst_id.endswith("-USDT-SWAP"):
            continue
        category = _text(row.get("instCategory"))
        group_id = _text(row.get("groupId"))
        if not _positive_crypto_fields(category, group_id):
            continue
        symbol = canonical_bot_symbol(inst_id)
        if symbol and symbol not in USER_UNTRADABLE_FUTURES_SYMBOLS:
            out.add(symbol)
    return out


def set_account_tradable_futures(symbols: Set[str] | List[str] | Tuple[str, ...]) -> None:
    """Test/runtime helper: activate an explicit account-level allowlist."""
    global ACCOUNT_ALLOWLIST_ACTIVE, ACCOUNT_ALLOWLIST_LAST_REFRESH
    normalized = {
        canonical_bot_symbol(symbol)
        for symbol in symbols
        if canonical_bot_symbol(symbol)
    }
    ACCOUNT_TRADABLE_FUTURES_SYMBOLS.clear()
    ACCOUNT_TRADABLE_FUTURES_SYMBOLS.update(normalized)
    ACCOUNT_ALLOWLIST_ACTIVE = bool(normalized)
    ACCOUNT_ALLOWLIST_LAST_REFRESH = int(time.time()) if normalized else 0


def clear_account_tradable_futures() -> None:
    global ACCOUNT_ALLOWLIST_ACTIVE, ACCOUNT_ALLOWLIST_LAST_REFRESH
    ACCOUNT_TRADABLE_FUTURES_SYMBOLS.clear()
    ACCOUNT_ALLOWLIST_ACTIVE = False
    ACCOUNT_ALLOWLIST_LAST_REFRESH = 0


def refresh_account_tradable_futures_from_env(*, force: bool = False) -> bool:
    """Load exact account-available SWAP instruments when read-only keys exist.

    Required environment variables:
      OKX_API_KEY, OKX_SECRET_KEY, OKX_PASSPHRASE

    Failure never opens the universe. It simply leaves account allowlisting
    inactive and the strict public-metadata gate remains in force.
    """
    global ACCOUNT_ALLOWLIST_ACTIVE, ACCOUNT_ALLOWLIST_LAST_REFRESH

    now = int(time.time())
    if (
        not force
        and ACCOUNT_ALLOWLIST_ACTIVE
        and ACCOUNT_ALLOWLIST_LAST_REFRESH > 0
        and now - ACCOUNT_ALLOWLIST_LAST_REFRESH < ACCOUNT_ALLOWLIST_REFRESH_SECONDS
    ):
        return True

    api_key = _text(os.getenv("OKX_API_KEY"))
    secret = _text(os.getenv("OKX_SECRET_KEY"))
    passphrase = _text(os.getenv("OKX_PASSPHRASE"))
    if not (api_key and secret and passphrase):
        return False

    try:
        import ccxt

        exchange = ccxt.okx({
            "apiKey": api_key,
            "secret": secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        method = getattr(exchange, "privateGetAccountInstruments", None)
        if method is None:
            method = getattr(exchange, "private_get_account_instruments", None)
        if method is None:
            raise RuntimeError("CCXT account instruments endpoint unavailable")

        payload = method({"instType": "SWAP"})
        symbols = _parse_account_instruments(payload)
        if not symbols:
            raise RuntimeError("Account SWAP allowlist returned empty")

        ACCOUNT_TRADABLE_FUTURES_SYMBOLS.clear()
        ACCOUNT_TRADABLE_FUTURES_SYMBOLS.update(symbols)
        ACCOUNT_ALLOWLIST_ACTIVE = True
        ACCOUNT_ALLOWLIST_LAST_REFRESH = now
        print(
            "PREMIUM ACCOUNT FUTURES ALLOWLIST | verified:",
            len(symbols),
        )
        return True
    except Exception as exc:
        print(
            "PREMIUM ACCOUNT FUTURES ALLOWLIST | unavailable, strict public fallback:",
            type(exc).__name__,
            str(exc)[:180],
        )
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

    if not market.get("swap", False):
        return True

    return has_positive_crypto_swap_evidence(market)


def market_exclusion_reason(market: Dict[str, Any], fallback: Any = "") -> str:
    """Return a strict live-universe rejection code, or empty when allowed."""
    if not isinstance(market, dict):
        return "INVALID_MARKET_METADATA"

    if not market.get("swap", False):
        return ""

    if str(market.get("quote") or "").upper() != "USDT":
        return "NOT_USDT_QUOTED_SWAP"
    if str(market.get("settle") or "").upper() != "USDT":
        return "NOT_USDT_SETTLED_SWAP"
    if market.get("active") is not True:
        return "ACTIVE_SWAP_NOT_CONFIRMED"

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
    if ACCOUNT_ALLOWLIST_ACTIVE and bot_symbol not in ACCOUNT_TRADABLE_FUTURES_SYMBOLS:
        return "ACCOUNT_FUTURES_UNAVAILABLE"

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
    if not canonical or canonical in USER_UNTRADABLE_FUTURES_SYMBOLS:
        return False
    if canonical not in VERIFIED_LIVE_FUTURES_SYMBOLS:
        return False
    if ACCOUNT_ALLOWLIST_ACTIVE and canonical not in ACCOUNT_TRADABLE_FUTURES_SYMBOLS:
        return False
    return True


def install_crypto_only_guard(market_scan_module: Any) -> None:
    """Patch all_market_shadow.eligible_markets for this Premium process only."""
    if getattr(market_scan_module, "_premium_crypto_only_guard_installed", False):
        return

    original = getattr(market_scan_module, "eligible_markets")

    @wraps(original)
    def guarded(markets: Dict[str, Dict[str, Any]]):
        # If read-only account credentials exist, account availability wins over
        # global/public availability. Otherwise strict public metadata is used.
        refresh_account_tradable_futures_from_env()
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
                "| account_allowlist:",
                "ON" if ACCOUNT_ALLOWLIST_ACTIVE else "OFF",
                "|",
                symbols + suffix,
            )
        return original(filtered)

    market_scan_module.eligible_markets = guarded
    market_scan_module._premium_crypto_only_guard_installed = True
