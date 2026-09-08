"""PRE-ENTRY shadow lane for Market First V5.

Purpose
-------
Measure whether selected high-quality preparations should be entered slightly
before the current live ENTRY condition.  This module is deliberately shadow
only:

- no Telegram messages,
- no exchange orders,
- no open_signals / portfolio-risk mutation,
- no live threshold or decision change.

A virtual position is opened only when the ordinary entry-plan engine still says
PREP, but the setup is already close to its zone with strong 15m+1h alignment,
acceptable 5m structure/volume and normal risk geometry.  The experiment then
asks one narrow question: from that earlier price, was TP1 reached before SL?

Open virtual positions are forced into the existing deep-scan quota so a slow
rotation cannot hide their outcome.  No extra scan slots are added.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any, Dict, Mapping, Optional, Sequence

import market_first_entry_plan as entry_plan
import market_first_strategy as strategy

VERSION = "MARKET_FIRST_PRE_ENTRY_SHADOW_V1_2026_09_08"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_EFFECT"
LEDGER_FILE = "market_first_pre_entry_shadow.json"

MAX_OPEN_SHADOW = 8
MAX_FORCED_SYMBOLS = 8
MAX_HOLD_SECONDS = 3 * 60 * 60
RECENT_SYMBOL_COOLDOWN_SECONDS = 2 * 60 * 60

MIN_SCORE = 80
MAX_ZONE_DISTANCE_PERCENT = 0.25
MIN_VOLUME_RATIO_5M = 0.35
MAX_RISK_PERCENT = 1.45
MIN_ROOM_R = 1.45
MAX_EXTENSION_ATR = 1.25

# Same conservative assumed round-trip friction used by the autonomous research
# stack: 0.05% taker + 0.02% slippage per side = 0.14% round trip.
ASSUMED_TAKER_FEE_PER_SIDE_PERCENT = 0.05
ASSUMED_SLIPPAGE_PER_SIDE_PERCENT = 0.02
ROUND_TRIP_COST_PERCENT = 2.0 * (
    ASSUMED_TAKER_FEE_PER_SIDE_PERCENT + ASSUMED_SLIPPAGE_PER_SIDE_PERCENT
)

_INSTALLED = False


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _empty() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "trades": {},
        "summary": {},
        "updated_at": 0,
    }


def _ensure(data: Any) -> Dict[str, Any]:
    ledger = data if isinstance(data, dict) else {}
    ledger.setdefault("version", VERSION)
    ledger.setdefault("mode", MODE)
    ledger.setdefault("trades", {})
    ledger.setdefault("summary", {})
    if not isinstance(ledger.get("trades"), dict):
        ledger["trades"] = {}
    return ledger


def _open_trades(ledger: Mapping[str, Any]) -> list[Dict[str, Any]]:
    trades = ledger.get("trades") if isinstance(ledger, Mapping) else None
    if not isinstance(trades, Mapping):
        return []
    return [
        row for row in trades.values()
        if isinstance(row, dict) and str(row.get("status") or "") == "OPEN"
    ]


def _last_symbol_trade(ledger: Mapping[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    trades = ledger.get("trades") if isinstance(ledger, Mapping) else None
    if not isinstance(trades, Mapping):
        return None
    rows = [
        row for row in trades.values()
        if isinstance(row, dict) and str(row.get("symbol") or "") == str(symbol)
    ]
    if not rows:
        return None
    rows.sort(key=lambda row: max(_si(row.get("opened_at")), _si(row.get("closed_at"))))
    return rows[-1]


def can_open(ledger: Mapping[str, Any], symbol: str, now: int) -> tuple[bool, str]:
    opened = _open_trades(ledger)
    if len(opened) >= MAX_OPEN_SHADOW:
        return False, "SHADOW_OPEN_LIMIT"
    if any(str(row.get("symbol") or "") == symbol for row in opened):
        return False, "SAME_SYMBOL_OPEN"
    last = _last_symbol_trade(ledger, symbol)
    if last:
        reference = max(_si(last.get("closed_at")), _si(last.get("opened_at")))
        if reference > 0 and now - reference < RECENT_SYMBOL_COOLDOWN_SECONDS:
            return False, "RECENT_SYMBOL_COOLDOWN"
    return True, "OK"


def profile_reason(plan: Mapping[str, Any]) -> str:
    direction = str(plan.get("direction") or "").upper()
    s5 = str(plan.get("structure_5m") or "").upper()
    volume = _sf(plan.get("volume_ratio_5m"))
    distance = _sf(plan.get("zone_distance_percent"), 999.0)
    reasons = []
    if s5 == "NEUTRAL":
        reasons.append("5M_NEUTRAL")
    if MIN_VOLUME_RATIO_5M <= volume < float(entry_plan.MIN_ENTRY_VOLUME_RATIO):
        reasons.append("VOLUME_EARLY")
    if distance > 0:
        reasons.append("NEAR_ZONE")
    return "+".join(reasons) if reasons else f"EARLY_{direction}"


def qualifies(plan: Mapping[str, Any] | None) -> tuple[bool, str]:
    if not isinstance(plan, Mapping):
        return False, "NO_PLAN"
    if str(plan.get("status") or "").upper() != "PREP":
        return False, "NOT_PREP"

    direction = str(plan.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return False, "DIRECTION"
    if str(plan.get("structure_15m") or "").upper() != direction:
        return False, "15M_ALIGNMENT"
    if str(plan.get("structure_1h") or "").upper() != direction:
        return False, "1H_ALIGNMENT"

    s5 = str(plan.get("structure_5m") or "").upper()
    if s5 not in {direction, "NEUTRAL"}:
        return False, "5M_OPPOSITE"
    if _si(plan.get("score")) < MIN_SCORE:
        return False, "SCORE"
    if _sf(plan.get("zone_distance_percent"), 999.0) > MAX_ZONE_DISTANCE_PERCENT:
        return False, "ZONE_DISTANCE"
    if _sf(plan.get("volume_ratio_5m")) < MIN_VOLUME_RATIO_5M:
        return False, "VOLUME"
    if _sf(plan.get("risk_percent"), 999.0) > MAX_RISK_PERCENT:
        return False, "RISK"
    if _sf(plan.get("room_r")) < MIN_ROOM_R:
        return False, "ROOM"
    if _sf(plan.get("extension_atr_5m"), 999.0) > MAX_EXTENSION_ATR:
        return False, "EXTENSION"
    return True, "OK"


def build_trade(plan: Mapping[str, Any], now: int) -> tuple[Optional[Dict[str, Any]], str]:
    direction = str(plan.get("direction") or "").upper()
    entry = _sf(plan.get("current_price"))
    raw_sl = _sf(plan.get("sl"))
    if entry <= 0 or raw_sl <= 0:
        return None, "PRICE_GEOMETRY"

    risk = entry - raw_sl if direction == "LONG" else raw_sl - entry
    if risk <= 0:
        return None, "SL_SIDE"
    actual_risk_percent = risk / entry * 100.0
    if actual_risk_percent > MAX_RISK_PERCENT:
        return None, "ACTUAL_RISK"

    tp1 = entry + risk * strategy.TP1_R if direction == "LONG" else entry - risk * strategy.TP1_R
    tp2 = entry + risk * strategy.TP2_R if direction == "LONG" else entry - risk * strategy.TP2_R
    tp3 = entry + risk * strategy.TP3_R if direction == "LONG" else entry - risk * strategy.TP3_R
    if min(tp1, tp2, tp3) <= 0:
        return None, "TARGET_GEOMETRY"

    cost_r = ROUND_TRIP_COST_PERCENT / actual_risk_percent if actual_risk_percent > 0 else 0.0
    symbol = str(plan.get("symbol") or "")
    trade_id = f"{symbol}:{direction}:{now}"
    return {
        "trade_id": trade_id,
        "version": VERSION,
        "symbol": symbol,
        "direction": direction,
        "profile": profile_reason(plan),
        "score": _si(plan.get("score")),
        "entry": round(entry, 12),
        "sl": round(raw_sl, 12),
        "tp1": round(tp1, 12),
        "tp2": round(tp2, 12),
        "tp3": round(tp3, 12),
        "risk_abs": round(risk, 12),
        "risk_percent": round(actual_risk_percent, 4),
        "cost_r": round(cost_r, 4),
        "zone_low": _sf(plan.get("zone_low")),
        "zone_high": _sf(plan.get("zone_high")),
        "zone_distance_percent": round(_sf(plan.get("zone_distance_percent")), 4),
        "structure_5m": plan.get("structure_5m"),
        "structure_15m": plan.get("structure_15m"),
        "structure_1h": plan.get("structure_1h"),
        "volume_ratio_5m": round(_sf(plan.get("volume_ratio_5m")), 3),
        "volume_ratio_15m": round(_sf(plan.get("volume_ratio_15m")), 3),
        "extension_atr_5m": round(_sf(plan.get("extension_atr_5m")), 3),
        "room_r": round(_sf(plan.get("room_r")), 2),
        "market_regime": plan.get("market_regime"),
        "opened_at": int(now),
        "last_checked_at": int(now),
        "status": "OPEN",
        "first_result": None,
        "closed_at": 0,
        "exit_price": None,
        "gross_r": None,
        "net_r": None,
        "best_favorable_r": 0.0,
        "worst_adverse_r": 0.0,
    }, "OK"


def open_virtual(ledger: Dict[str, Any], plan: Mapping[str, Any], now: int) -> tuple[bool, str]:
    ok, reason = qualifies(plan)
    if not ok:
        return False, reason
    symbol = str(plan.get("symbol") or "")
    allowed, reason = can_open(ledger, symbol, now)
    if not allowed:
        return False, reason
    trade, reason = build_trade(plan, now)
    if not trade:
        return False, reason
    ledger.setdefault("trades", {})[str(trade["trade_id"])] = trade
    return True, "OPENED"


def _close(trade: Dict[str, Any], result: str, price: float, now: int, gross_r: float) -> None:
    cost_r = _sf(trade.get("cost_r"))
    trade["status"] = "CLOSED"
    trade["first_result"] = result
    trade["exit_price"] = round(float(price), 12)
    trade["closed_at"] = int(now)
    trade["last_checked_at"] = int(now)
    trade["gross_r"] = round(float(gross_r), 4)
    trade["net_r"] = round(float(gross_r) - cost_r, 4)


def _update_excursion(trade: Dict[str, Any], high: float, low: float) -> None:
    entry = _sf(trade.get("entry"))
    risk = _sf(trade.get("risk_abs"))
    direction = str(trade.get("direction") or "")
    if entry <= 0 or risk <= 0:
        return
    if direction == "LONG":
        favorable = max(0.0, (high - entry) / risk)
        adverse = max(0.0, (entry - low) / risk)
    else:
        favorable = max(0.0, (entry - low) / risk)
        adverse = max(0.0, (high - entry) / risk)
    trade["best_favorable_r"] = round(max(_sf(trade.get("best_favorable_r")), favorable), 4)
    trade["worst_adverse_r"] = round(max(_sf(trade.get("worst_adverse_r")), adverse), 4)


def _recent_closed_bars(df5m: Any, elapsed_seconds: int) -> list[Dict[str, float]]:
    if df5m is None or not hasattr(df5m, "iloc") or not hasattr(df5m, "columns"):
        return []
    if not {"high", "low", "close"}.issubset(set(df5m.columns)) or len(df5m) < 2:
        return []
    # The final CCXT candle can still be forming.  Use enough closed 5m bars to
    # cover time since the previous check, capped for defensive API/data handling.
    count = max(1, min(12, int(math.ceil(max(1, elapsed_seconds) / 300.0))))
    frame = df5m.iloc[:-1].tail(count)
    rows = []
    for _, row in frame.iterrows():
        high = _sf(row.get("high"))
        low = _sf(row.get("low"))
        close = _sf(row.get("close"))
        if min(high, low, close) <= 0:
            continue
        rows.append({"high": high, "low": low, "close": close})
    return rows


def track_virtual(trade: Dict[str, Any], df5m: Any, current_price: float, now: int) -> str:
    if str(trade.get("status") or "") != "OPEN":
        return "CLOSED"
    last_checked = _si(trade.get("last_checked_at"), _si(trade.get("opened_at"), now))
    elapsed = max(1, now - last_checked)
    direction = str(trade.get("direction") or "")
    sl = _sf(trade.get("sl"))
    tp1 = _sf(trade.get("tp1"))
    bars = _recent_closed_bars(df5m, elapsed)

    for bar in bars:
        high = _sf(bar.get("high"))
        low = _sf(bar.get("low"))
        _update_excursion(trade, high, low)
        # Conservative ambiguity rule: if SL and TP1 are both inside the same
        # unseen 5m bar, count SL first.  This avoids flattering the shadow lane.
        if direction == "LONG":
            if low <= sl:
                _close(trade, "SL_FIRST", sl, now, -1.0)
                return "SL_FIRST"
            if high >= tp1:
                _close(trade, "TP1_FIRST", tp1, now, strategy.TP1_R)
                return "TP1_FIRST"
        else:
            if high >= sl:
                _close(trade, "SL_FIRST", sl, now, -1.0)
                return "SL_FIRST"
            if low <= tp1:
                _close(trade, "TP1_FIRST", tp1, now, strategy.TP1_R)
                return "TP1_FIRST"

    opened = _si(trade.get("opened_at"), now)
    if now - opened >= MAX_HOLD_SECONDS:
        entry = _sf(trade.get("entry"))
        risk = _sf(trade.get("risk_abs"))
        if entry > 0 and risk > 0 and current_price > 0:
            gross = (
                (current_price - entry) / risk
                if direction == "LONG"
                else (entry - current_price) / risk
            )
        else:
            gross = 0.0
        _close(trade, "EXPIRED", current_price or entry, now, gross)
        return "EXPIRED"

    trade["last_checked_at"] = int(now)
    return "OPEN"


def priority_symbols(ledger: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[str]:
    available = {
        str(row.get("symbol") or "")
        for row in rows
        if isinstance(row, Mapping) and row.get("symbol")
    }
    open_rows = sorted(_open_trades(ledger), key=lambda row: _si(row.get("opened_at")))
    return [
        str(row.get("symbol") or "")
        for row in open_rows
        if str(row.get("symbol") or "") in available
    ][:MAX_FORCED_SYMBOLS]


def build_summary(ledger: Mapping[str, Any]) -> Dict[str, Any]:
    trades = ledger.get("trades") if isinstance(ledger, Mapping) else {}
    rows = [row for row in trades.values() if isinstance(row, Mapping)] if isinstance(trades, Mapping) else []
    closed = [row for row in rows if str(row.get("status") or "") == "CLOSED"]
    tp1 = [row for row in closed if row.get("first_result") == "TP1_FIRST"]
    sl = [row for row in closed if row.get("first_result") == "SL_FIRST"]
    expired = [row for row in closed if row.get("first_result") == "EXPIRED"]
    decided = len(tp1) + len(sl)
    gross_total = sum(_sf(row.get("gross_r")) for row in closed)
    net_total = sum(_sf(row.get("net_r")) for row in closed)

    by_profile: Dict[str, Dict[str, Any]] = {}
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in closed:
        grouped[str(row.get("profile") or "UNKNOWN")].append(row)
    for key, values in grouped.items():
        profile_tp1 = sum(1 for row in values if row.get("first_result") == "TP1_FIRST")
        profile_sl = sum(1 for row in values if row.get("first_result") == "SL_FIRST")
        profile_decided = profile_tp1 + profile_sl
        by_profile[key] = {
            "closed": len(values),
            "tp1_first": profile_tp1,
            "sl_first": profile_sl,
            "tp1_first_rate": round(profile_tp1 / profile_decided, 4) if profile_decided else None,
            "net_r": round(sum(_sf(row.get("net_r")) for row in values), 4),
        }

    results = Counter(str(row.get("first_result") or "OPEN") for row in rows)
    return {
        "version": VERSION,
        "mode": MODE,
        "total": len(rows),
        "open": len(_open_trades(ledger)),
        "closed": len(closed),
        "decided_tp1_vs_sl": decided,
        "tp1_first": len(tp1),
        "sl_first": len(sl),
        "expired": len(expired),
        "tp1_first_rate": round(len(tp1) / decided, 4) if decided else None,
        "gross_r": round(gross_total, 4),
        "estimated_net_r": round(net_total, 4),
        "avg_estimated_net_r": round(net_total / len(closed), 4) if closed else None,
        "results": dict(results),
        "by_profile": by_profile,
        "note": "Shadow evidence only; not realised PnL and not a live-entry rule.",
    }


def install(runner: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    ledger = _ensure(runner.bot.load_json_file(LEDGER_FILE, _empty()))
    original_select = runner._select_deep_scan
    original_analyze = runner.analyze_candidate
    original_save_diagnostics = runner._save_diagnostics

    def select_with_shadow_priority(rows, sample_moves, state):
        selected = list(original_select(rows, sample_moves, state))
        forced = priority_symbols(ledger, rows)
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
        print("PRE-ENTRY SHADOW TAKİP ÖNCELİĞİ:", forced)
        return merged

    def analyze_with_shadow(*args, **kwargs):
        decision, reason = original_analyze(*args, **kwargs)

        def arg(name: str, index: int, default=None):
            if name in kwargs:
                return kwargs.get(name)
            return args[index] if len(args) > index else default

        symbol = str(arg("symbol", 0, "") or "")
        df5m = arg("df5m", 2)
        current_price = _sf(arg("current_price", 5, 0.0))
        now = runner.bot.now_ts()

        for trade in _open_trades(ledger):
            if str(trade.get("symbol") or "") != symbol:
                continue
            result = track_virtual(trade, df5m, current_price, now)
            if result != "OPEN":
                print("PRE-ENTRY SHADOW SONUÇ:", symbol, trade.get("direction"), result, "netR=", trade.get("net_r"))
            break

        # Never create a PRE-ENTRY shadow when the ordinary live path is already
        # trade-ready.  The experiment is specifically about setups that are still PREP.
        if isinstance(decision, Mapping) and bool(decision.get("trade_eligible")):
            return decision, reason

        context = arg("context", 7)
        if not symbol or current_price <= 0 or context is None:
            return decision, reason

        try:
            plan, _ = entry_plan.evaluate_entry_plan(
                symbol=symbol,
                df5m=df5m,
                df15m=arg("df15m", 3),
                df1h=arg("df1h", 4),
                current_price=current_price,
                quote_volume_24h=_sf(arg("quote_volume_24h", 6, 0.0)),
                context=context,
            )
            opened, open_reason = open_virtual(ledger, plan, now)
            if opened:
                print(
                    "PRE-ENTRY SHADOW AÇILDI:", symbol, plan.get("direction"),
                    "score=", plan.get("score"),
                    "zoneDist=", plan.get("zone_distance_percent"),
                    "vol5=", plan.get("volume_ratio_5m"),
                    "profile=", profile_reason(plan),
                )
            elif open_reason not in {"NOT_PREP", "NO_PLAN", "SAME_SYMBOL_OPEN", "RECENT_SYMBOL_COOLDOWN"}:
                pass
        except Exception as exc:
            print("PRE-ENTRY SHADOW değerlendirme hatası:", symbol, type(exc).__name__, exc)

        return decision, reason

    def save_diagnostics_with_shadow(*args, **kwargs):
        result = original_save_diagnostics(*args, **kwargs)
        ledger["summary"] = build_summary(ledger)
        ledger["version"] = VERSION
        ledger["mode"] = MODE
        ledger["updated_at"] = runner.bot.now_ts()
        if runner._is_live_run():
            runner.bot.save_json_file(LEDGER_FILE, ledger)
        print("PRE-ENTRY SHADOW ÖZET:", ledger["summary"])
        return result

    runner._select_deep_scan = select_with_shadow_priority
    runner.analyze_candidate = analyze_with_shadow
    runner._save_diagnostics = save_diagnostics_with_shadow


def summary() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "min_score": MIN_SCORE,
        "max_zone_distance_percent": MAX_ZONE_DISTANCE_PERCENT,
        "min_volume_ratio_5m": MIN_VOLUME_RATIO_5M,
        "max_risk_percent": MAX_RISK_PERCENT,
        "min_room_r": MIN_ROOM_R,
        "max_extension_atr": MAX_EXTENSION_ATR,
        "max_open_shadow": MAX_OPEN_SHADOW,
        "max_hold_minutes": MAX_HOLD_SECONDS // 60,
        "round_trip_cost_percent": ROUND_TRIP_COST_PERCENT,
        "live_effect": False,
    }
