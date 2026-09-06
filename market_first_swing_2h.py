"""Early 2H swing-preparation layer for the single Market First system.

This module never opens or promotes a trade by itself.  It reuses the 1H candles
already fetched by Market First, aggregates them locally into 2H candles, and
warns when a 2H+1H swing direction is forming before the normal 15M+1H entry-plan
engine is ready.

Goals:
- surface AAVE/APT/ARB/XPLUS-like swing opportunities earlier,
- add zero extra exchange requests per candidate,
- keep actual entries under the existing 15M/5M, live-flow, liquidity, cooldown
  and portfolio guards,
- keep hypothetical swing performance separate from realised trade P&L.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import pandas as pd

import market_first_entry_plan as entry_plan
import market_first_strategy as strategy

VERSION = "MARKET_FIRST_SWING_2H_V1_2026_09_06"
STATE_FILE = "market_first_swing_2h_state.json"
LEDGER_FILE = "market_first_swing_2h_ledger.json"
SUMMARY_FILE = "market_first_swing_2h_summary.json"

MIN_QUOTE_VOLUME_24H = 750_000.0
MIN_SWING_SCORE = 74
MAX_ZONE_DISTANCE_PERCENT = 1.80
MAX_EXTENSION_2H_ATR = 1.55
MAX_EXTENSION_1H_ATR = 1.65
REPEAT_SECONDS = 6 * 60 * 60
ACTIVE_TRACK_SECONDS = 36 * 60 * 60
MAX_ACTIVE_PRIORITY = 16
MAX_LEDGER_EPISODES = 1200


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def aggregate_1h_to_2h(df1h: Any) -> Optional[pd.DataFrame]:
    """Aggregate existing 1H OHLCV into 2H without another exchange request."""
    if df1h is None or not hasattr(df1h, "copy"):
        return None
    needed = ["open", "high", "low", "close", "volume"]
    if not all(column in df1h.columns for column in needed):
        return None

    frame = df1h.copy()
    for column in needed:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=needed).reset_index(drop=True)
    if len(frame) < 112:
        return None

    # Keep an even number of the most recent rows so the fallback aggregation is
    # deterministic. If a timestamp is available, align to real UTC 2H buckets.
    timestamp_column = None
    for candidate in ("timestamp", "datetime", "time"):
        if candidate in frame.columns:
            timestamp_column = candidate
            break

    if timestamp_column is not None:
        raw = frame[timestamp_column]
        if pd.api.types.is_numeric_dtype(raw):
            unit = "ms" if _sf(raw.dropna().iloc[-1] if len(raw.dropna()) else 0) > 10_000_000_000 else "s"
            dt = pd.to_datetime(raw, unit=unit, utc=True, errors="coerce")
        else:
            dt = pd.to_datetime(raw, utc=True, errors="coerce")
        usable = frame.assign(_dt=dt).dropna(subset=["_dt"]).set_index("_dt")
        if len(usable) >= 112:
            result = usable.resample("2h", label="right", closed="right").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna().reset_index(drop=True)
            if len(result) >= 56:
                return result

    even_len = (len(frame) // 2) * 2
    work = frame.iloc[-even_len:].copy().reset_index(drop=True)
    work["_pair"] = work.index // 2
    result = work.groupby("_pair", sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index(drop=True)
    return result if len(result) >= 56 else None


def _score(
    direction: str,
    s2h: Mapping[str, Any],
    s1h: Mapping[str, Any],
    s15: Mapping[str, Any],
    s5: Mapping[str, Any],
    zone_distance: float,
    context: strategy.MarketContext,
) -> int:
    score = 58
    d15 = str(s15.get("direction") or "").upper()
    d5 = str(s5.get("direction") or "").upper()

    if d15 == direction:
        score += 10
    elif d15 == "NEUTRAL":
        score += 6
    else:
        # Opposing 15M is acceptable for an early swing preparation: it can be
        # the pullback that produces the later entry, but it receives no bonus.
        score += 1

    if d5 == direction:
        score += 5
    elif d5 == "NEUTRAL":
        score += 4
    else:
        score += 2

    v15 = _sf(s15.get("volume_ratio"))
    v5 = _sf(s5.get("volume_ratio"))
    if v15 >= 1.00:
        score += 5
    elif v15 >= 0.65:
        score += 3
    if v5 >= 0.80:
        score += 3
    elif v5 >= 0.50:
        score += 2

    if zone_distance == 0:
        score += 5
    elif zone_distance <= 0.45:
        score += 4
    elif zone_distance <= 0.90:
        score += 2

    ext2 = _sf(s2h.get("extension_atr"))
    ext1 = _sf(s1h.get("extension_atr"))
    if ext2 <= 0.80:
        score += 4
    elif ext2 <= 1.20:
        score += 2
    if ext1 <= 0.90:
        score += 3

    preferred = str(context.preferred_direction or "").upper()
    if preferred == direction:
        score += 4
    elif preferred not in {"LONG", "SHORT"}:
        score += 2

    return int(max(0, min(100, round(score))))


def evaluate_swing_preparation(
    *,
    symbol: str,
    df5m: Any,
    df15m: Any,
    df1h: Any,
    current_price: float,
    quote_volume_24h: float,
    context: strategy.MarketContext,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return an observational 2H SWING_PREP; never a live trade decision."""
    if current_price <= 0:
        return None, "SWING_NO_PRICE"
    if quote_volume_24h < MIN_QUOTE_VOLUME_24H:
        return None, "SWING_LOW_VOLUME"

    df2h = aggregate_1h_to_2h(df1h)
    if df2h is None:
        return None, "SWING_2H_DATA"

    s2h = strategy._structure(df2h, current_price)
    s1h = strategy._structure(df1h, current_price)
    s15 = strategy._structure(df15m, current_price)
    s5 = strategy._structure(df5m, current_price)
    if not all(isinstance(item, Mapping) for item in (s2h, s1h, s15, s5)):
        return None, "SWING_STRUCTURE_DATA"

    d2h = str(s2h.get("direction") or "").upper()
    d1h = str(s1h.get("direction") or "").upper()
    if d2h not in {"LONG", "SHORT"} or d2h != d1h:
        return None, "SWING_2H_1H_NOT_ALIGNED"
    direction = d2h

    _, market_allowed = strategy._market_component(direction, context)
    if not market_allowed:
        return None, "SWING_MARKET_OPPOSED"

    if _sf(s2h.get("extension_atr")) > MAX_EXTENSION_2H_ATR:
        return None, "SWING_2H_EXTENDED"
    if _sf(s1h.get("extension_atr")) > MAX_EXTENSION_1H_ATR:
        return None, "SWING_1H_EXTENDED"

    atr15 = _sf(s15.get("atr"))
    anchor = entry_plan._choose_anchor(direction, current_price, s15, s1h)
    zone = entry_plan._zone(direction, current_price, anchor, atr15)
    if not zone:
        return None, "SWING_ZONE_DATA"

    zone_low = _sf(zone.get("low"))
    zone_high = _sf(zone.get("high"))
    distance = entry_plan._distance_to_zone_percent(current_price, zone_low, zone_high)
    if distance > MAX_ZONE_DISTANCE_PERCENT:
        return None, "SWING_TOO_FAR"

    geometry_entry = current_price if zone_low <= current_price <= zone_high else _sf(zone.get("anchor"))
    risk, risk_reason = entry_plan._risk_geometry(direction, geometry_entry, s15, s1h)
    if risk is None:
        return None, f"SWING_{risk_reason}"

    score = _score(direction, s2h, s1h, s15, s5, distance, context)
    if score < MIN_SWING_SCORE:
        return None, "SWING_LOW_SCORE"

    result: Dict[str, Any] = {
        "version": VERSION,
        "symbol": str(symbol),
        "direction": direction,
        "status": "SWING_PREP",
        "score": score,
        "current_price": round(current_price, 10),
        "quote_volume_24h": round(_sf(quote_volume_24h), 2),
        "zone_low": zone_low,
        "zone_high": zone_high,
        "ideal_entry": _sf(zone.get("anchor")),
        "zone_distance_percent": round(distance, 4),
        "structure_2h": d2h,
        "structure_1h": d1h,
        "structure_15m": str(s15.get("direction") or ""),
        "structure_5m": str(s5.get("direction") or ""),
        "extension_atr_2h": round(_sf(s2h.get("extension_atr")), 3),
        "extension_atr_1h": round(_sf(s1h.get("extension_atr")), 3),
        "volume_ratio_15m": round(_sf(s15.get("volume_ratio")), 3),
        "volume_ratio_5m": round(_sf(s5.get("volume_ratio")), 3),
        "market_regime": context.regime,
        "market_label": strategy.market_label(context),
        "market_preferred_direction": context.preferred_direction,
        "market_breadth_5m": context.breadth_5m,
        "shadow_only": True,
    }
    result.update(risk)
    return result, "OK"


def format_preparation(plan: Mapping[str, Any]) -> str:
    direction = str(plan.get("direction") or "")
    icon = "🟢" if direction == "LONG" else "🔴"
    return (
        f"🧭 2H SWING HAZIRLIĞI | {plan.get('symbol')}\n"
        f"{icon} {direction}\n"
        f"🌍 Piyasa: {plan.get('market_label', 'KARIŞIK')}\n"
        f"📍 Swing izleme bölgesi: {_sf(plan.get('zone_low')):.10g} - {_sf(plan.get('zone_high')):.10g}\n"
        f"💵 Mevcut: {_sf(plan.get('current_price')):.10g}\n"
        f"🛑 Yapısal geçersizlik: {_sf(plan.get('sl')):.10g}\n"
        f"🎯 Referans TP1: {_sf(plan.get('tp1')):.10g} | TP2: {_sf(plan.get('tp2')):.10g} | TP3: {_sf(plan.get('tp3')):.10g}\n"
        f"📊 2H/1H: {plan.get('structure_2h')}/{plan.get('structure_1h')} | "
        f"15M: {plan.get('structure_15m')} | 5M: {plan.get('structure_5m')}\n"
        f"⭐ Swing skoru: {int(_sf(plan.get('score')))}\n"
        "⏳ Erken swing farkındalığıdır; gerçek işlem için mevcut 15M/5M + canlı akış teyidi beklenecek."
    )


def load_state(bot: Any) -> Dict[str, Any]:
    state = bot.load_json_file(STATE_FILE, {"plans": {}})
    if not isinstance(state, dict):
        state = {"plans": {}}
    if not isinstance(state.get("plans"), dict):
        state["plans"] = {}
    return state


def should_emit(state: Dict[str, Any], plan: Mapping[str, Any], now: int) -> bool:
    key = f"{plan.get('symbol')}:{plan.get('direction')}"
    item = (state.get("plans") or {}).get(key) or {}
    last = int(item.get("last_alert_at") or 0)
    return now - last >= REPEAT_SECONDS


def mark_emitted(state: Dict[str, Any], plan: Mapping[str, Any], now: int) -> None:
    key = f"{plan.get('symbol')}:{plan.get('direction')}"
    state.setdefault("plans", {})[key] = {
        "last_alert_at": int(now),
        "updated_at": int(now),
        "direction": plan.get("direction"),
        "score": int(_sf(plan.get("score"))),
        "zone_low": _sf(plan.get("zone_low")),
        "zone_high": _sf(plan.get("zone_high")),
        "status": "SWING_PREP",
    }


def save_state(bot: Any, state: Dict[str, Any]) -> None:
    bot.save_json_file(STATE_FILE, state)


def load_ledger(bot: Any) -> Dict[str, Any]:
    ledger = bot.load_json_file(LEDGER_FILE, {"version": VERSION, "episodes": {}})
    if not isinstance(ledger, dict):
        ledger = {"version": VERSION, "episodes": {}}
    if not isinstance(ledger.get("episodes"), dict):
        ledger["episodes"] = {}
    ledger["version"] = VERSION
    return ledger


def _open_episode(ledger: Mapping[str, Any], symbol: str, direction: str) -> Optional[Dict[str, Any]]:
    candidates = []
    for episode in (ledger.get("episodes") or {}).values():
        if not isinstance(episode, dict) or episode.get("resolved"):
            continue
        if str(episode.get("symbol")) == symbol and str(episode.get("direction")) == direction:
            candidates.append(episode)
    return max(candidates, key=lambda item: int(item.get("first_at") or 0)) if candidates else None


def register_plan(ledger: Dict[str, Any], plan: Mapping[str, Any], now: int, alerted: bool) -> Dict[str, Any]:
    symbol = str(plan.get("symbol") or "")
    direction = str(plan.get("direction") or "")
    existing = _open_episode(ledger, symbol, direction)
    if existing is not None:
        existing["updated_at"] = int(now)
        existing["latest_score"] = int(_sf(plan.get("score")))
        existing["telegram_alert_sent"] = bool(existing.get("telegram_alert_sent") or alerted)
        return existing

    key = f"{symbol}:{direction}:{int(now)}"
    episode = {
        "episode_id": key,
        "symbol": symbol,
        "direction": direction,
        "first_at": int(now),
        "updated_at": int(now),
        "alert_price": _sf(plan.get("current_price")),
        "telegram_alert_sent": bool(alerted),
        "score": int(_sf(plan.get("score"))),
        "zone_low": _sf(plan.get("zone_low")),
        "zone_high": _sf(plan.get("zone_high")),
        "sl": _sf(plan.get("sl")),
        "tp1": _sf(plan.get("tp1")),
        "tp2": _sf(plan.get("tp2")),
        "tp3": _sf(plan.get("tp3")),
        "structure_2h": plan.get("structure_2h"),
        "structure_1h": plan.get("structure_1h"),
        "structure_15m": plan.get("structure_15m"),
        "structure_5m": plan.get("structure_5m"),
        "best_favorable_percent": 0.0,
        "worst_adverse_percent": 0.0,
        "tp1_at": 0,
        "tp2_at": 0,
        "tp3_at": 0,
        "sl_at": 0,
        "first_decisive_event": None,
        "resolved": False,
        "outcome": None,
        "shadow_only": True,
    }
    ledger.setdefault("episodes", {})[key] = episode
    return episode


def update_symbol_market(ledger: Dict[str, Any], symbol: str, current_price: float, df5m: Any, now: int) -> int:
    if current_price <= 0:
        return 0
    high = current_price
    low = current_price
    try:
        if df5m is not None and len(df5m) > 0:
            row = df5m.iloc[-1]
            high = max(high, _sf(row.get("high"), current_price))
            low = min(low, _sf(row.get("low"), current_price))
    except Exception:
        pass

    changed = 0
    for episode in (ledger.get("episodes") or {}).values():
        if not isinstance(episode, dict) or episode.get("resolved") or str(episode.get("symbol")) != symbol:
            continue
        first_at = int(episode.get("first_at") or 0)
        if not first_at or now - first_at > ACTIVE_TRACK_SECONDS:
            continue
        entry = _sf(episode.get("alert_price"))
        direction = str(episode.get("direction") or "")
        if entry <= 0 or direction not in {"LONG", "SHORT"}:
            continue
        if direction == "LONG":
            favorable = max(0.0, (high - entry) / entry * 100.0)
            adverse = max(0.0, (entry - low) / entry * 100.0)
            tp_hits = {
                "tp1": _sf(episode.get("tp1")) > 0 and high >= _sf(episode.get("tp1")),
                "tp2": _sf(episode.get("tp2")) > 0 and high >= _sf(episode.get("tp2")),
                "tp3": _sf(episode.get("tp3")) > 0 and high >= _sf(episode.get("tp3")),
                "sl": _sf(episode.get("sl")) > 0 and low <= _sf(episode.get("sl")),
            }
        else:
            favorable = max(0.0, (entry - low) / entry * 100.0)
            adverse = max(0.0, (high - entry) / entry * 100.0)
            tp_hits = {
                "tp1": _sf(episode.get("tp1")) > 0 and low <= _sf(episode.get("tp1")),
                "tp2": _sf(episode.get("tp2")) > 0 and low <= _sf(episode.get("tp2")),
                "tp3": _sf(episode.get("tp3")) > 0 and low <= _sf(episode.get("tp3")),
                "sl": _sf(episode.get("sl")) > 0 and high >= _sf(episode.get("sl")),
            }
        episode["best_favorable_percent"] = round(max(_sf(episode.get("best_favorable_percent")), favorable), 4)
        episode["worst_adverse_percent"] = round(max(_sf(episode.get("worst_adverse_percent")), adverse), 4)
        for name in ("tp1", "tp2", "tp3", "sl"):
            field = f"{name}_at"
            if tp_hits[name] and not int(episode.get(field) or 0):
                episode[field] = int(now)
        if episode.get("first_decisive_event") is None:
            tp1_hit = bool(tp_hits["tp1"])
            sl_hit = bool(tp_hits["sl"])
            if tp1_hit and sl_hit:
                episode["first_decisive_event"] = "AMBIGUOUS_SAME_BAR"
            elif tp1_hit:
                episode["first_decisive_event"] = "TP1_FIRST"
            elif sl_hit:
                episode["first_decisive_event"] = "SL_FIRST"
        episode["updated_at"] = int(now)
        changed += 1
    return changed


def finalize_expired(ledger: Dict[str, Any], now: int) -> int:
    closed = 0
    for episode in (ledger.get("episodes") or {}).values():
        if not isinstance(episode, dict) or episode.get("resolved"):
            continue
        first_at = int(episode.get("first_at") or 0)
        if not first_at or now - first_at < ACTIVE_TRACK_SECONDS:
            continue
        if int(episode.get("tp3_at") or 0):
            outcome = "TP3_REACHED"
        elif int(episode.get("tp2_at") or 0):
            outcome = "TP2_REACHED"
        elif int(episode.get("tp1_at") or 0):
            outcome = "TP1_REACHED"
        elif int(episode.get("sl_at") or 0):
            outcome = "SL_FIRST_NO_TP"
        else:
            outcome = "TIMEOUT"
        episode["resolved"] = True
        episode["resolved_at"] = int(now)
        episode["outcome"] = outcome
        closed += 1
    return closed


def active_symbols(ledger: Mapping[str, Any], now: int) -> list[str]:
    rows = []
    for episode in (ledger.get("episodes") or {}).values():
        if not isinstance(episode, Mapping) or episode.get("resolved"):
            continue
        if now - int(episode.get("first_at") or 0) > ACTIVE_TRACK_SECONDS:
            continue
        symbol = str(episode.get("symbol") or "")
        if symbol and symbol not in rows:
            rows.append(symbol)
        if len(rows) >= MAX_ACTIVE_PRIORITY:
            break
    return rows


def prioritize_active_symbols(selected: Sequence[str], rows: Sequence[Mapping[str, Any]], ledger: Mapping[str, Any], now: int, max_total: int) -> list[str]:
    available = {str(row.get("symbol") or "") for row in rows if isinstance(row, Mapping)}
    merged = []
    seen = set()
    for symbol in active_symbols(ledger, now) + [str(item) for item in selected]:
        if not symbol or symbol not in available or symbol in seen:
            continue
        seen.add(symbol)
        merged.append(symbol)
        if len(merged) >= max_total:
            break
    return merged


def summary(ledger: Mapping[str, Any], now: int) -> Dict[str, Any]:
    episodes = [item for item in (ledger.get("episodes") or {}).values() if isinstance(item, Mapping)]
    resolved = [item for item in episodes if item.get("resolved")]
    return {
        "version": VERSION,
        "generated_at": int(now),
        "total": len(episodes),
        "open": len(episodes) - len(resolved),
        "resolved": len(resolved),
        "long": sum(1 for item in episodes if item.get("direction") == "LONG"),
        "short": sum(1 for item in episodes if item.get("direction") == "SHORT"),
        "telegram_alert_sent": sum(1 for item in episodes if item.get("telegram_alert_sent")),
        "tp1_first": sum(1 for item in episodes if item.get("first_decisive_event") == "TP1_FIRST"),
        "sl_first": sum(1 for item in episodes if item.get("first_decisive_event") == "SL_FIRST"),
        "tp3_reached": sum(1 for item in episodes if int(item.get("tp3_at") or 0)),
        "avg_best_favorable_percent_resolved": round(
            sum(_sf(item.get("best_favorable_percent")) for item in resolved) / len(resolved), 4
        ) if resolved else 0.0,
        "note": "2H swing preparation is hypothetical opportunity tracking, never realised P&L.",
    }


def save_ledger(bot: Any, ledger: Dict[str, Any], now: int) -> None:
    ledger["version"] = VERSION
    ledger["updated_at"] = int(now)
    episodes = ledger.get("episodes") or {}
    if len(episodes) > MAX_LEDGER_EPISODES:
        ordered = sorted(episodes.items(), key=lambda pair: int((pair[1] or {}).get("first_at") or 0), reverse=True)
        ledger["episodes"] = dict(ordered[:MAX_LEDGER_EPISODES])
    bot.save_json_file(LEDGER_FILE, ledger)
    bot.save_json_file(SUMMARY_FILE, summary(ledger, now))
