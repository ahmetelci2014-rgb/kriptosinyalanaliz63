"""All-market live impulse memory for OKX USDT perpetuals.

One bulk ticker snapshot per Pump run is retained for a short rolling window.
The layer is deliberately independent from trade signals: it finds coins that
are accelerating now, prioritises them for deep Pump analysis, and gives Scalp
a recent market-regime guard so counter-trend reaction setups do not fight a
strong live impulse. It never opens exchange orders.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

STATE_FILE = "market_impulse_state.json"
VERSION = "MARKET_IMPULSE_V1_2026_08_19"
HISTORY_KEEP_MINUTES = 45
MAX_HISTORY_PER_SYMBOL = 12
MAX_IMPULSES = 80
MAX_PRIORITY_SYMBOLS = 24

# Minimum liquidity for a coin to override the normal top-volume deep scan.
MIN_PRIORITY_QUOTE_VOLUME = 300_000.0

# Candidate impulse: enough to force deep analysis.
IMPULSE_5M_PERCENT = 0.65
IMPULSE_15M_PERCENT = 1.05
IMPULSE_30M_PERCENT = 1.70

# Strong impulse: enough to block a counter-trend TEPKI_SCALP for a short time.
STRONG_5M_PERCENT = 0.90
STRONG_15M_PERCENT = 1.35
STRONG_30M_PERCENT = 2.20
STRONG_IMPULSE_MAX_AGE_MINUTES = 20

# Very strong moves may be surfaced once as an early warning by the Pump wrapper.
VERY_STRONG_5M_PERCENT = 1.20
VERY_STRONG_15M_PERCENT = 2.00
VERY_STRONG_30M_PERCENT = 3.00


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return default
        return number
    except Exception:
        return default


def normalize_bot_symbol(symbol: Any) -> str:
    value = str(symbol or "").upper().strip()
    value = value.replace("/USDT:USDT", "USDT").replace(":USDT", "").replace("/", "")
    if value and not value.endswith("USDT"):
        value += "USDT"
    return value


def atomic_save_json(path: str, data: Dict[str, Any]) -> bool:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, prefix=".impulse.", suffix=".tmp", delete=False
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        return True
    except Exception as exc:
        print("Market impulse state yazma hatası:", type(exc).__name__)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def empty_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "updated_at": 0,
        "history": {},
        "impulses": [],
        "current_universe": [],
        "last_alert_sent": {},
    }


def load_state(path: str = STATE_FILE) -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return empty_state()
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        state = raw if isinstance(raw, dict) else {}
    except Exception:
        return empty_state()

    state.setdefault("version", VERSION)
    state.setdefault("updated_at", 0)
    state.setdefault("history", {})
    state.setdefault("impulses", [])
    state.setdefault("current_universe", [])
    state.setdefault("last_alert_sent", {})
    return state


def quote_volume(ticker: Dict[str, Any]) -> float:
    value = safe_float(ticker.get("quoteVolume"))
    if value > 0:
        return value
    info = ticker.get("info") or {}
    for key in ("volCcy24h", "volUsd24h", "vol24h"):
        value = safe_float(info.get(key))
        if value > 0:
            return value
    return 0.0


def active_usdt_swap_symbols(exchange: Any) -> List[str]:
    markets = exchange.load_markets()
    result: List[str] = []
    stable_bases = {"USDT", "USDC", "DAI", "FDUSD", "TUSD", "USDP", "USD"}
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        if not market.get("active", True) or not market.get("swap", False):
            continue
        if market.get("quote") != "USDT" or market.get("settle") != "USDT":
            continue
        symbol = str(market.get("symbol") or "")
        base = str(market.get("base") or "").upper()
        if not symbol or "/USDT:USDT" not in symbol or not base or base in stable_bases:
            continue
        result.append(symbol)
    return result


def _reference_snapshot(history: List[Dict[str, Any]], now: int, minutes: int) -> Optional[Dict[str, Any]]:
    target = now - minutes * 60
    eligible = [item for item in history if int(item.get("ts") or 0) <= target]
    if not eligible:
        return None
    chosen = max(eligible, key=lambda item: int(item.get("ts") or 0))
    age = now - int(chosen.get("ts") or 0)
    # Do not compare 5m against a stale 25m snapshot after workflow gaps.
    if age > (minutes + 8) * 60:
        return None
    return chosen


def _move(current: float, reference: Optional[Dict[str, Any]]) -> Optional[float]:
    if not reference:
        return None
    old = safe_float(reference.get("price"))
    if current <= 0 or old <= 0:
        return None
    return (current - old) / old * 100.0


def _direction_and_strength(move5: Optional[float], move15: Optional[float], move30: Optional[float]) -> Tuple[Optional[str], bool, bool, float]:
    values = [value for value in (move5, move15, move30) if value is not None]
    if not values:
        return None, False, False, 0.0

    long_candidate = (
        (move5 is not None and move5 >= IMPULSE_5M_PERCENT)
        or (move15 is not None and move15 >= IMPULSE_15M_PERCENT)
        or (move30 is not None and move30 >= IMPULSE_30M_PERCENT)
    )
    short_candidate = (
        (move5 is not None and move5 <= -IMPULSE_5M_PERCENT)
        or (move15 is not None and move15 <= -IMPULSE_15M_PERCENT)
        or (move30 is not None and move30 <= -IMPULSE_30M_PERCENT)
    )
    if not long_candidate and not short_candidate:
        return None, False, False, 0.0

    directional_score = (
        (safe_float(move5) / max(IMPULSE_5M_PERCENT, 0.01)) * 0.45
        + (safe_float(move15) / max(IMPULSE_15M_PERCENT, 0.01)) * 0.35
        + (safe_float(move30) / max(IMPULSE_30M_PERCENT, 0.01)) * 0.20
    )
    direction = "LONG" if directional_score >= 0 else "SHORT"

    if direction == "LONG":
        strong = (
            (move5 is not None and move5 >= STRONG_5M_PERCENT)
            or (move15 is not None and move15 >= STRONG_15M_PERCENT)
            or (move30 is not None and move30 >= STRONG_30M_PERCENT)
        )
        very_strong = (
            (move5 is not None and move5 >= VERY_STRONG_5M_PERCENT)
            or (move15 is not None and move15 >= VERY_STRONG_15M_PERCENT)
            or (move30 is not None and move30 >= VERY_STRONG_30M_PERCENT)
        )
    else:
        strong = (
            (move5 is not None and move5 <= -STRONG_5M_PERCENT)
            or (move15 is not None and move15 <= -STRONG_15M_PERCENT)
            or (move30 is not None and move30 <= -STRONG_30M_PERCENT)
        )
        very_strong = (
            (move5 is not None and move5 <= -VERY_STRONG_5M_PERCENT)
            or (move15 is not None and move15 <= -VERY_STRONG_15M_PERCENT)
            or (move30 is not None and move30 <= -VERY_STRONG_30M_PERCENT)
        )

    magnitude = max(abs(value) for value in values)
    return direction, strong, very_strong, magnitude


def update_market_impulse_state(exchange: Any, path: str = STATE_FILE, now: Optional[int] = None) -> Dict[str, Any]:
    now_ts = int(now or time.time())
    state = load_state(path)
    try:
        exchange_symbols = active_usdt_swap_symbols(exchange)
        tickers = exchange.fetch_tickers(exchange_symbols)
    except Exception as exc:
        print("Tüm piyasa impuls snapshot hatası:", type(exc).__name__)
        return state

    cutoff = now_ts - HISTORY_KEEP_MINUTES * 60
    history = state.setdefault("history", {})
    universe: List[Dict[str, Any]] = []
    impulses: List[Dict[str, Any]] = []

    for exchange_symbol in exchange_symbols:
        ticker = tickers.get(exchange_symbol) or {}
        price = safe_float(ticker.get("last"))
        if price <= 0:
            continue
        bot_symbol = normalize_bot_symbol(exchange_symbol)
        volume = quote_volume(ticker)
        records = [
            item for item in (history.get(bot_symbol) or [])
            if isinstance(item, dict) and int(item.get("ts") or 0) >= cutoff
        ]

        move5 = _move(price, _reference_snapshot(records, now_ts, 5))
        move15 = _move(price, _reference_snapshot(records, now_ts, 15))
        move30 = _move(price, _reference_snapshot(records, now_ts, 30))
        direction, strong, very_strong, magnitude = _direction_and_strength(move5, move15, move30)

        records.append({"ts": now_ts, "price": price})
        history[bot_symbol] = records[-MAX_HISTORY_PER_SYMBOL:]
        universe.append({
            "symbol": bot_symbol,
            "exchange_symbol": exchange_symbol,
            "price": price,
            "quote_volume": volume,
        })

        if direction and volume >= MIN_PRIORITY_QUOTE_VOLUME:
            impulses.append({
                "symbol": bot_symbol,
                "exchange_symbol": exchange_symbol,
                "direction": direction,
                "detected_at": now_ts,
                "price": price,
                "quote_volume": volume,
                "move5_percent": None if move5 is None else round(move5, 4),
                "move15_percent": None if move15 is None else round(move15, 4),
                "move30_percent": None if move30 is None else round(move30, 4),
                "strong": bool(strong),
                "very_strong": bool(very_strong),
                "magnitude": round(magnitude, 4),
            })

    # remove symbols no longer in the active universe
    active_bot_symbols = {item["symbol"] for item in universe}
    state["history"] = {key: value for key, value in history.items() if key in active_bot_symbols}
    impulses.sort(
        key=lambda item: (
            1 if item.get("very_strong") else 0,
            1 if item.get("strong") else 0,
            safe_float(item.get("magnitude")),
            safe_float(item.get("quote_volume")),
        ),
        reverse=True,
    )
    universe.sort(key=lambda item: safe_float(item.get("quote_volume")), reverse=True)
    state["version"] = VERSION
    state["updated_at"] = now_ts
    state["impulses"] = impulses[:MAX_IMPULSES]
    state["current_universe"] = universe
    state.setdefault("last_alert_sent", {})
    atomic_save_json(path, state)
    print(
        "Tüm piyasa impuls:", len(universe), "swap | aday", len(impulses),
        "| güçlü", sum(1 for item in impulses if item.get("strong")),
    )
    return state


def priority_symbols(state: Dict[str, Any], limit: int = MAX_PRIORITY_SYMBOLS) -> List[str]:
    rows = [item for item in state.get("impulses", []) if isinstance(item, dict)]
    rows.sort(
        key=lambda item: (
            1 if item.get("strong") else 0,
            safe_float(item.get("magnitude")),
            safe_float(item.get("quote_volume")),
        ),
        reverse=True,
    )
    result: List[str] = []
    for item in rows:
        symbol = normalize_bot_symbol(item.get("symbol"))
        if symbol and symbol not in result:
            result.append(symbol)
        if len(result) >= limit:
            break
    return result


def scan_universe_from_state(
    state: Dict[str, Any],
    normal_min_quote_volume: float,
    normal_max_scan_coins: int,
) -> List[str]:
    normal = [
        normalize_bot_symbol(item.get("symbol"))
        for item in state.get("current_universe", [])
        if isinstance(item, dict)
        and safe_float(item.get("quote_volume")) >= safe_float(normal_min_quote_volume)
    ][: max(0, int(normal_max_scan_coins))]
    priority = priority_symbols(state)
    merged: List[str] = []
    # Priority first: time-sensitive moves are analysed before the normal volume list.
    for symbol in priority + normal:
        if symbol and symbol not in merged:
            merged.append(symbol)
    return merged


def recent_impulse(
    symbol: str,
    path: str = STATE_FILE,
    now: Optional[int] = None,
    max_age_minutes: int = STRONG_IMPULSE_MAX_AGE_MINUTES,
) -> Optional[Dict[str, Any]]:
    state = load_state(path)
    now_ts = int(now or time.time())
    normalized = normalize_bot_symbol(symbol)
    candidates = [
        item for item in state.get("impulses", [])
        if isinstance(item, dict)
        and normalize_bot_symbol(item.get("symbol")) == normalized
        and now_ts - int(item.get("detected_at") or 0) <= max_age_minutes * 60
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda item: int(item.get("detected_at") or 0))


def recent_opposing_strong_impulse(
    symbol: str,
    candidate_direction: str,
    path: str = STATE_FILE,
    now: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    item = recent_impulse(symbol, path=path, now=now)
    if not item or not item.get("strong"):
        return None
    direction = str(candidate_direction or "").upper()
    impulse_direction = str(item.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"} or impulse_direction not in {"LONG", "SHORT"}:
        return None
    if direction == impulse_direction:
        return None
    return item


def strongest_very_strong_impulse(state: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    candidates = [
        item for item in state.get("impulses", [])
        if isinstance(item, dict) and item.get("very_strong")
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (safe_float(item.get("magnitude")), safe_float(item.get("quote_volume"))),
    )
