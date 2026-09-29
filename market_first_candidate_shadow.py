"""Shadow tracking for Telegram ARKA PLAN ADAYI messages.

This lane starts only after a selective pre-signal candidate was successfully
shown to the user. It never promotes a candidate to a real trade, never sends
Telegram, never writes open_signals/trade_ledger and never places an order.

The purpose is measurement: keep the first displayed watch-zone reference,
track the same candidate silently, and learn whether its final rejection gate
prevented a loss or hid a profitable move.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import market_first_strategy as strategy

VERSION = "MARKET_FIRST_CANDIDATE_SHADOW_V1_2026_09_29"
MODE = "TELEGRAM_BACKGROUND_CANDIDATE_SHADOW_ONLY_NO_LIVE_EFFECT"
LEDGER_FILE = "market_first_candidate_shadow.json"

MAX_OPEN_SHADOW = 48
MAX_FORCED_SYMBOLS = 32
MAX_HOLD_SECONDS = 18 * 60 * 60
MAX_HISTORY_TRADES = 2500

_INSTALLED = False
_VISIBILITY_ATTACHED = False
_LEDGER: Dict[str, Any] = {}


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


def _valid_side(direction: str, entry: float, level: float, kind: str) -> bool:
    if min(entry, level) <= 0:
        return False
    if kind == "SL":
        return level < entry if direction == "LONG" else level > entry
    return level > entry if direction == "LONG" else level < entry


def _zone_entry(decision: Mapping[str, Any]) -> Tuple[float, float, float]:
    low = _sf(decision.get("entry_plan_zone_low"))
    high = _sf(decision.get("entry_plan_zone_high"))
    if low > 0 and high > 0:
        if low > high:
            low, high = high, low
        return low, high, (low + high) / 2.0
    ideal = _sf(decision.get("entry_plan_ideal_entry"))
    current = _sf(decision.get("current_price"))
    entry = ideal if ideal > 0 else current
    return low, high, entry


def _blockers(reason: str) -> list[str]:
    text = str(reason or "").strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    return [item.strip() for item in text.split("+") if item.strip()]


def _derive_geometry(
    decision: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> Tuple[Optional[Dict[str, float]], str]:
    direction = str(decision.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return None, "DIRECTION"

    zone_low, zone_high, entry = _zone_entry(decision)
    if entry <= 0:
        return None, "ENTRY"

    risk_percent = _sf(evidence.get("risk_percent")) or _sf(decision.get("risk_percent"))
    raw_sl = 0.0
    for key in ("sl", "entry_plan_sl", "target_reference_sl"):
        candidate = _sf(decision.get(key))
        if _valid_side(direction, entry, candidate, "SL"):
            raw_sl = candidate
            break
    if raw_sl <= 0:
        if risk_percent <= 0:
            return None, "SL"
        raw_sl = (
            entry * (1.0 - risk_percent / 100.0)
            if direction == "LONG"
            else entry * (1.0 + risk_percent / 100.0)
        )

    risk_abs = entry - raw_sl if direction == "LONG" else raw_sl - entry
    if risk_abs <= 0:
        return None, "RISK"
    actual_risk_percent = risk_abs / entry * 100.0

    refs = [
        _sf(decision.get("target_reference_tp1")),
        _sf(decision.get("target_reference_tp2")),
        _sf(decision.get("target_reference_tp3")),
    ]
    refs_valid = all(_valid_side(direction, entry, value, "TP") for value in refs)
    if refs_valid:
        if direction == "LONG":
            refs_valid = refs[0] < refs[1] < refs[2]
        else:
            refs_valid = refs[0] > refs[1] > refs[2]

    if refs_valid:
        tp1, tp2, tp3 = refs
    else:
        tp1 = entry + risk_abs * strategy.TP1_R if direction == "LONG" else entry - risk_abs * strategy.TP1_R
        tp2 = entry + risk_abs * strategy.TP2_R if direction == "LONG" else entry - risk_abs * strategy.TP2_R
        tp3 = entry + risk_abs * strategy.TP3_R if direction == "LONG" else entry - risk_abs * strategy.TP3_R

    if min(tp1, tp2, tp3) <= 0:
        return None, "TARGET"

    return {
        "zone_low": zone_low,
        "zone_high": zone_high,
        "entry": entry,
        "sl": raw_sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_abs": risk_abs,
        "risk_percent": actual_risk_percent,
    }, "OK"


def _same_open_candidate(ledger: Mapping[str, Any], symbol: str, direction: str) -> bool:
    return any(
        str(row.get("symbol") or "") == symbol
        and str(row.get("direction") or "").upper() == direction
        for row in _open_trades(ledger)
    )


def build_trade(
    decision: Mapping[str, Any],
    reason: str,
    evidence: Mapping[str, Any],
    now: int,
) -> Tuple[Optional[Dict[str, Any]], str]:
    geometry, geometry_reason = _derive_geometry(decision, evidence)
    if not geometry:
        return None, geometry_reason

    symbol = str(decision.get("symbol") or "")
    direction = str(decision.get("direction") or "").upper()
    trade_id = f"{symbol}:{direction}:{int(now)}"
    technical_target = _sf(decision.get("technical_target"))
    expected = _sf(evidence.get("expected_move_percent")) or _sf(decision.get("expected_move_percent"))
    target_r = _sf(evidence.get("technical_target_r")) or _sf(decision.get("technical_target_r"))

    return {
        "trade_id": trade_id,
        "version": VERSION,
        "source": "SELECTIVE_PRE_SIGNAL_TELEGRAM",
        "symbol": symbol,
        "direction": direction,
        "market_label": decision.get("market_label"),
        "market_regime": decision.get("market_regime"),
        "score": _si(evidence.get("score"), _si(decision.get("score"))),
        "rejection_reason": str(reason or ""),
        "blockers": _blockers(reason),
        "zone_low": round(geometry["zone_low"], 12),
        "zone_high": round(geometry["zone_high"], 12),
        "entry": round(geometry["entry"], 12),
        "sl": round(geometry["sl"], 12),
        "tp1": round(geometry["tp1"], 12),
        "tp2": round(geometry["tp2"], 12),
        "tp3": round(geometry["tp3"], 12),
        "technical_target": round(technical_target, 12) if technical_target > 0 else None,
        "risk_abs": round(geometry["risk_abs"], 12),
        "risk_percent": round(geometry["risk_percent"], 4),
        "expected_move_percent": round(expected, 4),
        "technical_target_r": round(target_r, 3),
        "opened_at": int(now),
        "last_checked_at": int(now),
        "last_bar_ts": int(now // 60 * 60),
        "last_price": round(_sf(decision.get("current_price")) or geometry["entry"], 12),
        "status": "OPEN",
        "first_result": None,
        "final_result": None,
        "max_target_hit": 0,
        "tp1_hit_at": 0,
        "tp2_hit_at": 0,
        "tp3_hit_at": 0,
        "sl_hit_at": 0,
        "closed_at": 0,
        "exit_price": None,
        "best_favorable_percent": 0.0,
        "worst_adverse_percent": 0.0,
        "best_favorable_r": 0.0,
        "worst_adverse_r": 0.0,
        "hold_minutes": 0.0,
    }, "OK"


def register_sent_candidate(
    decision: Mapping[str, Any] | None,
    reason: str,
    evidence: Mapping[str, Any] | None = None,
) -> Tuple[bool, str]:
    """Open one shadow record only after its Telegram candidate was sent."""
    if not isinstance(decision, Mapping):
        return False, "NO_DECISION"
    if not _LEDGER:
        return False, "NOT_INSTALLED"

    symbol = str(decision.get("symbol") or "")
    direction = str(decision.get("direction") or "").upper()
    if not symbol or direction not in {"LONG", "SHORT"}:
        return False, "IDENTITY"
    if len(_open_trades(_LEDGER)) >= MAX_OPEN_SHADOW:
        return False, "OPEN_LIMIT"
    if _same_open_candidate(_LEDGER, symbol, direction):
        return False, "SAME_SYMBOL_DIRECTION_OPEN"

    now = _si(getattr(_RUNNER.bot, "now_ts", lambda: 0)()) if _RUNNER is not None else 0
    if now <= 0:
        return False, "TIME"
    trade, build_reason = build_trade(decision, reason, evidence or {}, now)
    if not trade:
        return False, build_reason
    _LEDGER.setdefault("trades", {})[str(trade["trade_id"])] = trade
    _LEDGER["updated_at"] = now
    return True, "OPENED"


def _timestamp_seconds(value: Any) -> int:
    if value is None:
        return 0
    try:
        if hasattr(value, "timestamp"):
            return int(value.timestamp())
        number = float(value)
        if not math.isfinite(number):
            return 0
        if number > 10_000_000_000:
            number /= 1000.0
        return int(number)
    except Exception:
        return 0


def _recent_closed_bars(df1m: Any, trade: Mapping[str, Any], now: int) -> list[Dict[str, float]]:
    if df1m is None or not hasattr(df1m, "iloc") or not hasattr(df1m, "columns"):
        return []
    columns = set(df1m.columns)
    if not {"high", "low", "close"}.issubset(columns) or len(df1m) < 2:
        return []

    closed = df1m.iloc[:-1]
    time_col = next((key for key in ("timestamp", "time", "datetime", "date") if key in columns), None)
    last_bar_ts = _si(trade.get("last_bar_ts"))
    rows: list[Dict[str, float]] = []

    if time_col:
        for _, row in closed.iterrows():
            bar_ts = _timestamp_seconds(row.get(time_col))
            if bar_ts <= last_bar_ts:
                continue
            high = _sf(row.get("high"))
            low = _sf(row.get("low"))
            close = _sf(row.get("close"))
            if min(high, low, close) <= 0:
                continue
            rows.append({"ts": float(bar_ts), "high": high, "low": low, "close": close})
        return rows[-90:]

    elapsed = max(60, int(now) - _si(trade.get("last_checked_at"), int(now)))
    count = max(1, min(89, int(math.ceil(elapsed / 60.0))))
    frame = closed.tail(count)
    synthetic = max(last_bar_ts, int(now) - count * 60)
    for _, row in frame.iterrows():
        high = _sf(row.get("high"))
        low = _sf(row.get("low"))
        close = _sf(row.get("close"))
        if min(high, low, close) <= 0:
            continue
        synthetic += 60
        rows.append({"ts": float(synthetic), "high": high, "low": low, "close": close})
    return rows


def _update_excursion(trade: Dict[str, Any], high: float, low: float) -> None:
    entry = _sf(trade.get("entry"))
    risk = _sf(trade.get("risk_abs"))
    direction = str(trade.get("direction") or "").upper()
    if entry <= 0 or min(high, low) <= 0:
        return
    if direction == "LONG":
        favorable_pct = max(0.0, (high / entry - 1.0) * 100.0)
        adverse_pct = max(0.0, (1.0 - low / entry) * 100.0)
        favorable_r = max(0.0, (high - entry) / risk) if risk > 0 else 0.0
        adverse_r = max(0.0, (entry - low) / risk) if risk > 0 else 0.0
    else:
        favorable_pct = max(0.0, (1.0 - low / entry) * 100.0)
        adverse_pct = max(0.0, (high / entry - 1.0) * 100.0)
        favorable_r = max(0.0, (entry - low) / risk) if risk > 0 else 0.0
        adverse_r = max(0.0, (high - entry) / risk) if risk > 0 else 0.0

    trade["best_favorable_percent"] = round(
        max(_sf(trade.get("best_favorable_percent")), favorable_pct), 4
    )
    trade["worst_adverse_percent"] = round(
        max(_sf(trade.get("worst_adverse_percent")), adverse_pct), 4
    )
    trade["best_favorable_r"] = round(max(_sf(trade.get("best_favorable_r")), favorable_r), 4)
    trade["worst_adverse_r"] = round(max(_sf(trade.get("worst_adverse_r")), adverse_r), 4)


def _hit(direction: str, high: float, low: float, level: float, kind: str) -> bool:
    if kind == "SL":
        return low <= level if direction == "LONG" else high >= level
    return high >= level if direction == "LONG" else low <= level


def _close(trade: Dict[str, Any], result: str, price: float, now: int) -> None:
    trade["status"] = "CLOSED"
    trade["final_result"] = result
    trade["exit_price"] = round(float(price), 12) if price > 0 else None
    trade["closed_at"] = int(now)
    trade["last_checked_at"] = int(now)
    opened = _si(trade.get("opened_at"), int(now))
    trade["hold_minutes"] = round(max(0, int(now) - opened) / 60.0, 2)


def track_trade(trade: Dict[str, Any], df1m: Any, current_price: float, now: int) -> str:
    if str(trade.get("status") or "") != "OPEN":
        return str(trade.get("final_result") or "CLOSED")

    direction = str(trade.get("direction") or "").upper()
    sl = _sf(trade.get("sl"))
    tps = [_sf(trade.get("tp1")), _sf(trade.get("tp2")), _sf(trade.get("tp3"))]
    bars = _recent_closed_bars(df1m, trade, now)

    for bar in bars:
        high = _sf(bar.get("high"))
        low = _sf(bar.get("low"))
        bar_ts = _si(bar.get("ts"), now)
        _update_excursion(trade, high, low)

        current_target = _si(trade.get("max_target_hit"))
        sl_hit = _hit(direction, high, low, sl, "SL")

        # Conservative unseen-bar rule: if the original stop and the next target
        # are both crossed in one unobserved minute, treat the stop as first.
        next_target = tps[current_target] if current_target < 3 else 0.0
        next_hit = next_target > 0 and _hit(direction, high, low, next_target, "TP")
        if sl_hit and next_hit:
            if current_target == 0:
                trade["first_result"] = "SL_FIRST"
                trade["sl_hit_at"] = bar_ts
                _close(trade, "SL_FIRST", sl, bar_ts)
            else:
                trade["sl_hit_at"] = bar_ts
                _close(trade, f"SL_AFTER_TP{current_target}", sl, bar_ts)
            trade["last_bar_ts"] = bar_ts
            return str(trade.get("final_result"))

        if sl_hit:
            if current_target == 0:
                trade["first_result"] = "SL_FIRST"
                result = "SL_FIRST"
            else:
                result = f"SL_AFTER_TP{current_target}"
            trade["sl_hit_at"] = bar_ts
            _close(trade, result, sl, bar_ts)
            trade["last_bar_ts"] = bar_ts
            return result

        # No SL in this minute: record every target reached, in order.
        reached = current_target
        for index in range(current_target, 3):
            if _hit(direction, high, low, tps[index], "TP"):
                reached = index + 1
                trade[f"tp{index + 1}_hit_at"] = bar_ts
            else:
                break
        if reached > current_target:
            trade["max_target_hit"] = reached
            if trade.get("first_result") is None:
                trade["first_result"] = "TP1_FIRST"
            if reached >= 3:
                _close(trade, "TP3", tps[2], bar_ts)
                trade["last_bar_ts"] = bar_ts
                return "TP3"

        trade["last_bar_ts"] = max(_si(trade.get("last_bar_ts")), bar_ts)

    if current_price > 0:
        _update_excursion(trade, current_price, current_price)
        trade["last_price"] = round(current_price, 12)

    opened = _si(trade.get("opened_at"), now)
    trade["last_checked_at"] = int(now)
    trade["hold_minutes"] = round(max(0, int(now) - opened) / 60.0, 2)
    if now - opened >= MAX_HOLD_SECONDS:
        target_hit = _si(trade.get("max_target_hit"))
        result = f"EXPIRED_AFTER_TP{target_hit}" if target_hit > 0 else "EXPIRED"
        _close(trade, result, current_price or _sf(trade.get("entry")), now)
        return result

    return "OPEN"


def priority_symbols(ledger: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[str]:
    available = {
        str(row.get("symbol") or "")
        for row in rows
        if isinstance(row, Mapping) and row.get("symbol")
    }
    opens = sorted(_open_trades(ledger), key=lambda row: _si(row.get("opened_at")))
    return [
        str(row.get("symbol") or "")
        for row in opens
        if str(row.get("symbol") or "") in available
    ][:MAX_FORCED_SYMBOLS]


def _prune(ledger: Dict[str, Any]) -> None:
    trades = ledger.get("trades")
    if not isinstance(trades, dict) or len(trades) <= MAX_HISTORY_TRADES:
        return
    rows = sorted(
        trades.items(),
        key=lambda item: max(_si((item[1] or {}).get("opened_at")), _si((item[1] or {}).get("closed_at"))),
        reverse=True,
    )
    keep_ids = {trade_id for trade_id, _ in rows[:MAX_HISTORY_TRADES]}
    ledger["trades"] = {key: value for key, value in trades.items() if key in keep_ids}


def build_summary(ledger: Mapping[str, Any]) -> Dict[str, Any]:
    trades = ledger.get("trades") if isinstance(ledger, Mapping) else {}
    rows = [row for row in trades.values() if isinstance(row, Mapping)] if isinstance(trades, Mapping) else []
    open_rows = [row for row in rows if str(row.get("status") or "") == "OPEN"]
    closed = [row for row in rows if str(row.get("status") or "") == "CLOSED"]
    tp_first = [row for row in rows if row.get("first_result") == "TP1_FIRST"]
    sl_first = [row for row in rows if row.get("first_result") == "SL_FIRST"]
    decided = len(tp_first) + len(sl_first)

    blocker_stats: dict[str, Dict[str, Any]] = {}
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        blockers = row.get("blockers") if isinstance(row.get("blockers"), list) else []
        for blocker in blockers:
            grouped[str(blocker)].append(row)
    for blocker, values in grouped.items():
        b_tp = sum(1 for row in values if row.get("first_result") == "TP1_FIRST")
        b_sl = sum(1 for row in values if row.get("first_result") == "SL_FIRST")
        b_decided = b_tp + b_sl
        blocker_stats[blocker] = {
            "samples": len(values),
            "decided": b_decided,
            "tp1_first": b_tp,
            "sl_first": b_sl,
            "tp1_first_rate": round(b_tp / b_decided, 4) if b_decided else None,
            "tp3_reached": sum(1 for row in values if _si(row.get("max_target_hit")) >= 3),
            "avg_mfe_percent": round(
                sum(_sf(row.get("best_favorable_percent")) for row in values) / len(values), 4
            ) if values else None,
            "avg_mae_percent": round(
                sum(_sf(row.get("worst_adverse_percent")) for row in values) / len(values), 4
            ) if values else None,
        }

    final_results = Counter(str(row.get("final_result") or "OPEN") for row in rows)
    return {
        "version": VERSION,
        "mode": MODE,
        "total": len(rows),
        "open": len(open_rows),
        "closed": len(closed),
        "decided_tp1_vs_sl": decided,
        "tp1_first": len(tp_first),
        "sl_first": len(sl_first),
        "tp1_first_rate": round(len(tp_first) / decided, 4) if decided else None,
        "tp1_reached": sum(1 for row in rows if _si(row.get("max_target_hit")) >= 1),
        "tp2_reached": sum(1 for row in rows if _si(row.get("max_target_hit")) >= 2),
        "tp3_reached": sum(1 for row in rows if _si(row.get("max_target_hit")) >= 3),
        "final_results": dict(final_results),
        "by_blocker": blocker_stats,
        "note": "Telegram arka plan adaylari icin golge olcum; gercek islem veya gercek PnL degildir.",
    }


_RUNNER: Any = None


def install(runner: Any) -> None:
    global _INSTALLED, _LEDGER, _RUNNER
    if _INSTALLED:
        return
    _INSTALLED = True
    _RUNNER = runner
    _LEDGER = _ensure(runner.bot.load_json_file(LEDGER_FILE, _empty()))

    original_select = runner._select_deep_scan
    original_analyze = runner.analyze_candidate
    original_save_diagnostics = runner._save_diagnostics

    def select_with_candidate_shadow(rows, sample_moves, state):
        selected = list(original_select(rows, sample_moves, state))
        forced = priority_symbols(_LEDGER, rows)
        if not forced:
            return selected
        merged = []
        seen = set()
        for symbol in forced + selected:
            if not symbol or symbol in seen:
                continue
            seen.add(symbol)
            merged.append(symbol)
        # Preserve the size produced by the already-installed tracking layers.
        limit = max(len(selected), len(forced))
        merged = merged[:limit]
        print("ARKA PLAN ADAY SHADOW TAKIP ONCELIGI:", forced)
        return merged

    def analyze_with_candidate_shadow(*args, **kwargs):
        def arg(name: str, index: int, default=None):
            if name in kwargs:
                return kwargs.get(name)
            return args[index] if len(args) > index else default

        symbol = str(arg("symbol", 0, "") or "")
        df1m = arg("df1m", 1)
        current_price = _sf(arg("current_price", 5, 0.0))
        now = runner.bot.now_ts()

        for trade in _open_trades(_LEDGER):
            if str(trade.get("symbol") or "") != symbol:
                continue
            result = track_trade(trade, df1m, current_price, now)
            if result != "OPEN":
                print(
                    "ARKA PLAN ADAY SHADOW SONUC:",
                    symbol,
                    trade.get("direction"),
                    result,
                    "MFE%=", trade.get("best_favorable_percent"),
                    "MAE%=", trade.get("worst_adverse_percent"),
                )
        return original_analyze(*args, **kwargs)

    def save_diagnostics_with_candidate_shadow(*args, **kwargs):
        result = original_save_diagnostics(*args, **kwargs)
        _prune(_LEDGER)
        _LEDGER["summary"] = build_summary(_LEDGER)
        _LEDGER["version"] = VERSION
        _LEDGER["mode"] = MODE
        _LEDGER["updated_at"] = runner.bot.now_ts()
        if runner._is_live_run():
            runner.bot.save_json_file(LEDGER_FILE, _LEDGER)
        print("ARKA PLAN ADAY SHADOW OZET:", _LEDGER["summary"])
        return result

    runner._select_deep_scan = select_with_candidate_shadow
    runner.analyze_candidate = analyze_with_candidate_shadow
    runner._save_diagnostics = save_diagnostics_with_candidate_shadow


def attach_visibility(runner: Any, selective_pre_signal: Any) -> None:
    """Register only candidates whose existing Telegram visibility lane sent successfully."""
    global _VISIBILITY_ATTACHED
    if _VISIBILITY_ATTACHED:
        return
    _VISIBILITY_ATTACHED = True

    original_decision_to_signal = runner.decision_to_signal

    def decision_to_signal_with_candidate_shadow(decision):
        sent_rows = getattr(selective_pre_signal, "_RUN_SENT", [])
        before = len(sent_rows) if isinstance(sent_rows, list) else 0
        signal = original_decision_to_signal(decision)
        sent_rows = getattr(selective_pre_signal, "_RUN_SENT", [])
        after = len(sent_rows) if isinstance(sent_rows, list) else 0

        if isinstance(signal, Mapping) or after <= before or not isinstance(decision, Mapping):
            return signal

        row = sent_rows[-1] if sent_rows and isinstance(sent_rows[-1], Mapping) else {}
        if (
            str(row.get("symbol") or "") != str(decision.get("symbol") or "")
            or str(row.get("direction") or "").upper() != str(decision.get("direction") or "").upper()
        ):
            return signal

        reason = str(row.get("reason") or "")
        opened, open_reason = register_sent_candidate(decision, reason, row)
        if opened:
            print(
                "ARKA PLAN ADAY SHADOW ACILDI:",
                decision.get("symbol"),
                decision.get("direction"),
                reason,
            )
        elif open_reason not in {"SAME_SYMBOL_DIRECTION_OPEN"}:
            print(
                "ARKA PLAN ADAY SHADOW KAYIT ATLANDI:",
                decision.get("symbol"),
                open_reason,
            )
        return signal

    runner.decision_to_signal = decision_to_signal_with_candidate_shadow


def summary() -> Dict[str, Any]:
    ledger_summary = build_summary(_LEDGER) if _LEDGER else {}
    return {
        "version": VERSION,
        "mode": MODE,
        "ledger_file": LEDGER_FILE,
        "max_open_shadow": MAX_OPEN_SHADOW,
        "max_hold_hours": MAX_HOLD_SECONDS // 3600,
        "max_forced_symbols": MAX_FORCED_SYMBOLS,
        "telegram": False,
        "real_trade_promotion": False,
        "open_signal_write": False,
        "trade_ledger_write": False,
        "exchange_orders": False,
        "current": ledger_summary,
    }
