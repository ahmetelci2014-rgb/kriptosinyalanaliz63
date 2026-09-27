"""Background candidate tracker for Day Trading Core V1.

This module does not decide or send live trades. It observes setups that reach:
- valid 2H direction
- valid 15M setup

Then it records whether the 5M trigger passed, why it failed when it did not,
and follows the hypothetical trade path with the same R targets / protection
logic as the live Day Trading Core. The purpose is to compare sent and unsent
opportunities without changing the live decision engine.
"""
from __future__ import annotations

from collections import Counter
import json
import os
import tempfile
from typing import Any, Dict, Mapping, Optional, Tuple

import day_trading_core as core

VERSION = "DAY_TRADING_CANDIDATE_V1_2026_09_27"
LEDGER_FILE = "day_trading_candidate_ledger.json"
SUMMARY_FILE = "day_trading_candidate_summary.json"
DIAGNOSTICS_FILE = "day_trading_candidate_diagnostics.json"
LIVE_LEDGER_FILE = core.LEDGER_FILE
MAX_HISTORY = 5000
LIVE_MATCH_WINDOW_SECONDS = 20 * 60


def _atomic_save(path: str, data: Mapping[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(data), handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        if not os.path.exists(path):
            return dict(default)
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else dict(default)
    except Exception:
        return dict(default)


def _default_ledger() -> Dict[str, Any]:
    return {"version": VERSION, "candidates": {}, "updated_at": 0}


def _live_trades(live_ledger: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    rows = live_ledger.get("trades") if isinstance(live_ledger.get("trades"), Mapping) else {}
    return [row for row in rows.values() if isinstance(row, Mapping)]


def _match_live_trade(
    live_ledger: Mapping[str, Any],
    symbol: str,
    direction: str,
    opened_at: int,
) -> Optional[Mapping[str, Any]]:
    best: Optional[Mapping[str, Any]] = None
    best_gap = LIVE_MATCH_WINDOW_SECONDS + 1
    for trade in _live_trades(live_ledger):
        if str(trade.get("symbol")) != symbol or str(trade.get("direction")) != direction:
            continue
        gap = abs(int(core._sf(trade.get("opened_at"))) - opened_at)
        if gap <= LIVE_MATCH_WINDOW_SECONDS and gap < best_gap:
            best = trade
            best_gap = gap
    return best


def _risk_gate_reason(
    direction: str,
    live_price: float,
    signal_close: float,
    raw_stop: float,
) -> Tuple[str, float, float]:
    signal_risk = signal_close - raw_stop if direction == "LONG" else raw_stop - signal_close
    if signal_risk <= 0:
        return "STOP_STRUCTURE", 0.0, 99.0
    drift_r = abs(live_price - signal_close) / signal_risk
    risk_abs = live_price - raw_stop if direction == "LONG" else raw_stop - live_price
    if risk_abs <= 0:
        return "STOP_INVALID", 0.0, drift_r
    risk_percent = risk_abs / live_price * 100.0 if live_price > 0 else 0.0
    if drift_r > core.MAX_LIVE_DRIFT_R:
        return "LIVE_PRICE_DRIFT", risk_percent, drift_r
    if risk_percent < core.MIN_RISK_PERCENT:
        return "RISK_TOO_TIGHT", risk_percent, drift_r
    if risk_percent > core.MAX_RISK_PERCENT:
        return "RISK_TOO_WIDE", risk_percent, drift_r
    return "OK", risk_percent, drift_r


def _build_candidate(
    symbol: str,
    ccxt_symbol: str,
    live_price: float,
    direction: str,
    df15: Any,
    df5: Any,
    trigger_ok: bool,
    trigger_reason: str,
    metrics: Dict[str, Any],
    live_ledger: Mapping[str, Any],
) -> Tuple[Optional[Dict[str, Any]], str]:
    c15 = core._completed(df15)
    c5 = core._completed(df5)
    atr15 = core._sf(c15["atr14"])
    signal_close = core._sf(c5["close"])
    if min(live_price, signal_close, atr15) <= 0:
        return None, "PRICE_DATA"

    recent15 = df15.iloc[-8:-2]
    if recent15.empty:
        return None, "15M_STRUCTURE_HISTORY"

    if direction == "LONG":
        raw_stop = core._sf(recent15["low"].min()) - 0.15 * atr15
        risk_abs = live_price - raw_stop
    else:
        raw_stop = core._sf(recent15["high"].max()) + 0.15 * atr15
        risk_abs = raw_stop - live_price

    if risk_abs <= 0:
        return None, "STOP_INVALID"

    risk_gate, risk_percent, drift_r = _risk_gate_reason(
        direction=direction,
        live_price=live_price,
        signal_close=signal_close,
        raw_stop=raw_stop,
    )
    sign = 1.0 if direction == "LONG" else -1.0
    now = core._now()
    setup_bar_ts = int(core._sf(c15["ts"]))
    candidate_id = f"{symbol}_{direction}_{setup_bar_ts}"

    linked = _match_live_trade(live_ledger, symbol, direction, now)
    telegram_sent = linked is not None
    if telegram_sent:
        not_sent_reason = "SENT"
    elif not trigger_ok:
        not_sent_reason = f"5M_TRIGGER:{trigger_reason}"
    elif risk_gate != "OK":
        not_sent_reason = risk_gate
    else:
        not_sent_reason = "LIVE_LIMIT_COOLDOWN_OR_TIMING"

    candidate = {
        "candidate_id": candidate_id,
        "version": VERSION,
        "symbol": symbol,
        "ccxt_symbol": ccxt_symbol,
        "direction": direction,
        "entry": round(live_price, 12),
        "initial_stop": round(raw_stop, 12),
        "current_stop": round(raw_stop, 12),
        "risk_abs": round(risk_abs, 12),
        "risk_percent": round(risk_percent, 4),
        "drift_r": round(drift_r, 4),
        "tp1": round(live_price + sign * risk_abs * core.TP1_R, 12),
        "tp2": round(live_price + sign * risk_abs * core.TP2_R, 12),
        "tp3": round(live_price + sign * risk_abs * core.TP3_R, 12),
        "tp1_r": core.TP1_R,
        "tp2_r": core.TP2_R,
        "tp3_r": core.TP3_R,
        "opened_at": now,
        "setup_bar_ts": setup_bar_ts,
        "expires_at": now + core.MAX_HOLD_MINUTES * 60,
        "last_check_ms": int(now * 1000),
        "stage": 0,
        "remaining": 1.0,
        "realized_r": 0.0,
        "stop_r": -1.0,
        "status": "OPEN",
        "highest_target": "NONE",
        "trigger_passed": bool(trigger_ok),
        "trigger_reason": trigger_reason,
        "risk_gate_reason": risk_gate,
        "would_pass_entry_filters": bool(trigger_ok and risk_gate == "OK"),
        "telegram_sent": telegram_sent,
        "linked_trade_id": str(linked.get("trade_id")) if linked else None,
        "not_sent_reason": not_sent_reason,
        "events": [{"at": now, "event": "CANDIDATE_OPEN", "price": round(live_price, 12)}],
        "metrics": metrics,
    }
    return candidate, "OK"


def _close_candidate(
    candidate: Dict[str, Any],
    gross_r: float,
    result: str,
    exit_price: float,
    at: int,
) -> None:
    candidate["status"] = "CLOSED"
    candidate["final_result"] = result
    candidate["gross_r"] = round(gross_r, 4)
    candidate["estimated_cost_r"] = core.ESTIMATED_COST_R
    candidate["net_r"] = round(gross_r - core.ESTIMATED_COST_R, 4)
    candidate["exit_price"] = round(exit_price, 12)
    candidate["closed_at"] = at
    candidate.setdefault("events", []).append(
        {"at": at, "event": result, "price": round(exit_price, 12)}
    )


def _aligned_r(candidate: Mapping[str, Any], price: float) -> float:
    entry = core._sf(candidate.get("entry"))
    risk = core._sf(candidate.get("risk_abs"))
    if risk <= 0:
        return 0.0
    raw = (price - entry) / risk
    return raw if candidate.get("direction") == "LONG" else -raw


def _apply_candidate_bar(candidate: Dict[str, Any], bar: Mapping[str, Any]) -> bool:
    high = core._sf(bar.get("high"))
    low = core._sf(bar.get("low"))
    ts = int(core._sf(bar.get("ts")) / 1000)
    direction = str(candidate.get("direction"))
    stage = int(candidate.get("stage") or 0)
    stop = core._sf(candidate.get("current_stop"))

    stop_hit = low <= stop if direction == "LONG" else high >= stop
    # Same conservative rule as live Day Trading: stop wins same-candle ambiguity.
    if stop_hit:
        gross = core._sf(candidate.get("realized_r")) + core._sf(candidate.get("remaining")) * core._sf(candidate.get("stop_r"), -1.0)
        result = "SL" if stage == 0 else f"PROTECTED_AFTER_TP{stage}"
        _close_candidate(candidate, gross, result, stop, ts)
        return True

    target_key = {0: "tp1", 1: "tp2", 2: "tp3"}.get(stage)
    if not target_key:
        return False
    target = core._sf(candidate.get(target_key))
    target_hit = high >= target if direction == "LONG" else low <= target
    if not target_hit:
        return False

    sign = 1.0 if direction == "LONG" else -1.0
    if stage == 0:
        candidate["realized_r"] = round(core._sf(candidate.get("realized_r")) + core.TP1_SIZE * core.TP1_R, 4)
        candidate["remaining"] = round(1.0 - core.TP1_SIZE, 4)
        candidate["stage"] = 1
        candidate["stop_r"] = core.POST_TP1_STOP_R
        candidate["current_stop"] = round(core._sf(candidate["entry"]) + sign * core._sf(candidate["risk_abs"]) * core.POST_TP1_STOP_R, 12)
        candidate["highest_target"] = "TP1"
        event = "TP1"
    elif stage == 1:
        candidate["realized_r"] = round(core._sf(candidate.get("realized_r")) + core.TP2_SIZE * core.TP2_R, 4)
        candidate["remaining"] = round(1.0 - core.TP1_SIZE - core.TP2_SIZE, 4)
        candidate["stage"] = 2
        candidate["stop_r"] = core.POST_TP2_STOP_R
        candidate["current_stop"] = round(core._sf(candidate["entry"]) + sign * core._sf(candidate["risk_abs"]) * core.POST_TP2_STOP_R, 12)
        candidate["highest_target"] = "TP2"
        event = "TP2"
    else:
        candidate["highest_target"] = "TP3"
        gross = core._sf(candidate.get("realized_r")) + core.TP3_SIZE * core.TP3_R
        _close_candidate(candidate, gross, "TP3", target, ts)
        return True

    candidate.setdefault("events", []).append(
        {"at": ts, "event": event, "price": round(target, 12)}
    )
    return False


def _track_open(exchange: Any, ledger: Dict[str, Any], diagnostics: Counter) -> None:
    candidates = ledger.setdefault("candidates", {})
    for candidate_id, candidate in list(candidates.items()):
        if not isinstance(candidate, dict) or candidate.get("status") != "OPEN":
            continue
        try:
            rows = exchange.fetch_ohlcv(str(candidate["ccxt_symbol"]), timeframe="5m", limit=100)
            bars = [
                {"ts": row[0], "open": row[1], "high": row[2], "low": row[3], "close": row[4]}
                for row in rows
                if int(row[0]) > int(candidate.get("last_check_ms") or 0)
            ]
            closed = False
            for bar in bars:
                if _apply_candidate_bar(candidate, bar):
                    closed = True
                    break
                candidate["last_check_ms"] = int(bar["ts"])

            if not closed and core._now() >= int(candidate.get("expires_at") or 0):
                last_price = core._sf(rows[-1][4]) if rows else core._sf(candidate.get("entry"))
                current_r = _aligned_r(candidate, last_price)
                gross = core._sf(candidate.get("realized_r")) + core._sf(candidate.get("remaining")) * current_r
                _close_candidate(candidate, gross, "TIME_EXIT", last_price, core._now())

            candidates[candidate_id] = candidate
        except Exception as exc:
            diagnostics["TRACK_ERROR"] += 1
            print("Candidate tracking error:", candidate_id, exc)


def _reconcile_telegram_links(ledger: Dict[str, Any], live_ledger: Mapping[str, Any]) -> None:
    for candidate in ledger.setdefault("candidates", {}).values():
        if not isinstance(candidate, dict) or candidate.get("telegram_sent"):
            continue
        linked = _match_live_trade(
            live_ledger,
            str(candidate.get("symbol")),
            str(candidate.get("direction")),
            int(core._sf(candidate.get("opened_at"))),
        )
        if linked is None:
            continue
        candidate["telegram_sent"] = True
        candidate["linked_trade_id"] = str(linked.get("trade_id"))
        candidate["not_sent_reason"] = "SENT"


def _prune(ledger: Dict[str, Any]) -> None:
    candidates = ledger.setdefault("candidates", {})
    if len(candidates) <= MAX_HISTORY:
        return
    open_rows = {key: row for key, row in candidates.items() if isinstance(row, Mapping) and row.get("status") == "OPEN"}
    closed_rows = [
        (key, row)
        for key, row in candidates.items()
        if isinstance(row, Mapping) and row.get("status") != "OPEN"
    ]
    closed_rows.sort(key=lambda item: int(core._sf(item[1].get("opened_at"))), reverse=True)
    keep_closed = max(0, MAX_HISTORY - len(open_rows))
    ledger["candidates"] = {**open_rows, **dict(closed_rows[:keep_closed])}


def _build_summary(ledger: Mapping[str, Any]) -> Dict[str, Any]:
    rows_map = ledger.get("candidates") if isinstance(ledger.get("candidates"), Mapping) else {}
    rows = [row for row in rows_map.values() if isinstance(row, Mapping)]
    open_rows = [row for row in rows if row.get("status") == "OPEN"]
    closed = [row for row in rows if row.get("status") == "CLOSED"]
    sent = [row for row in rows if bool(row.get("telegram_sent"))]
    unsent = [row for row in rows if not bool(row.get("telegram_sent"))]
    unsent_closed = [row for row in unsent if row.get("status") == "CLOSED"]
    results = Counter(str(row.get("final_result") or "UNKNOWN") for row in closed)
    unsent_results = Counter(str(row.get("final_result") or "UNKNOWN") for row in unsent_closed)
    net_values = [core._sf(row.get("net_r")) for row in closed]
    unsent_net_values = [core._sf(row.get("net_r")) for row in unsent_closed]
    tp1_or_better = sum(1 for row in rows if str(row.get("highest_target")) in {"TP1", "TP2", "TP3"})
    unsent_tp1_or_better = sum(1 for row in unsent if str(row.get("highest_target")) in {"TP1", "TP2", "TP3"})
    return {
        "version": VERSION,
        "generated_at": core._now(),
        "total_candidates": len(rows),
        "open_candidates": len(open_rows),
        "closed_candidates": len(closed),
        "telegram_linked_candidates": len(sent),
        "unsent_candidates": len(unsent),
        "trigger_passed_candidates": sum(1 for row in rows if bool(row.get("trigger_passed"))),
        "tp1_or_better_candidates": tp1_or_better,
        "closed_results": dict(results),
        "closed_total_net_r": round(sum(net_values), 4),
        "closed_expectancy_net_r": round(sum(net_values) / len(net_values), 4) if net_values else 0.0,
        "unsent_closed_candidates": len(unsent_closed),
        "unsent_tp1_or_better_candidates": unsent_tp1_or_better,
        "unsent_closed_results": dict(unsent_results),
        "unsent_total_net_r": round(sum(unsent_net_values), 4),
        "unsent_expectancy_net_r": round(sum(unsent_net_values) / len(unsent_net_values), 4) if unsent_net_values else 0.0,
        "note": "Observation-only candidate ledger. It never opens exchange orders or changes Day Trading Core admission decisions.",
    }


def run() -> None:
    ledger = _load_json(LEDGER_FILE, _default_ledger())
    live_ledger = _load_json(LIVE_LEDGER_FILE, {"trades": {}})
    ledger["version"] = VERSION
    diagnostics: Counter = Counter()
    exchange = core._get_exchange()

    # First advance previously observed candidates.
    _track_open(exchange, ledger, diagnostics)
    _reconcile_telegram_links(ledger, live_ledger)

    universe = core._load_universe(exchange)
    print(f"DAY TRADING CANDIDATES | scanning {len(universe)} liquid OKX USDT swaps")
    created = 0

    for row in universe:
        symbol = str(row["symbol"])
        ccxt_symbol = str(row["ccxt_symbol"])
        live_price = core._sf(row["last"])
        try:
            df2h = core._fetch_df(exchange, ccxt_symbol, "2h")
            direction, metrics2h = core._direction_2h(df2h)
            if direction is None:
                diagnostics["2H_NO_DIRECTION"] += 1
                continue

            df15 = core._fetch_df(exchange, ccxt_symbol, "15m")
            ok15, reason15, metrics15 = core._setup_15m(df15, direction)
            if not ok15:
                diagnostics[reason15] += 1
                continue

            setup_bar_ts = int(core._sf(core._completed(df15)["ts"]))
            candidate_id = f"{symbol}_{direction}_{setup_bar_ts}"
            if candidate_id in ledger.setdefault("candidates", {}):
                diagnostics["DUPLICATE_SETUP_BAR"] += 1
                continue

            df5 = core._fetch_df(exchange, ccxt_symbol, "5m")
            trigger_ok, trigger_reason, metrics5 = core._trigger_5m(df5, direction)
            metrics = {**metrics2h, **metrics15, **metrics5}
            candidate, reason = _build_candidate(
                symbol=symbol,
                ccxt_symbol=ccxt_symbol,
                live_price=live_price,
                direction=direction,
                df15=df15,
                df5=df5,
                trigger_ok=trigger_ok,
                trigger_reason=trigger_reason,
                metrics=metrics,
                live_ledger=live_ledger,
            )
            if candidate is None:
                diagnostics[f"BUILD:{reason}"] += 1
                continue

            ledger["candidates"][candidate_id] = candidate
            created += 1
            diagnostics["CANDIDATE_RECORDED"] += 1
            if trigger_ok:
                diagnostics["5M_TRIGGER_PASS"] += 1
            else:
                diagnostics[f"5M_TRIGGER_FAIL:{trigger_reason}"] += 1
            print(
                "CANDIDATE:",
                symbol,
                direction,
                "trigger=", trigger_reason,
                "risk_gate=", candidate["risk_gate_reason"],
                "telegram=", candidate["telegram_sent"],
            )
        except Exception as exc:
            diagnostics["DATA_OR_ANALYSIS_ERROR"] += 1
            print("Candidate scan error:", symbol, exc)

    _reconcile_telegram_links(ledger, live_ledger)
    _prune(ledger)
    ledger["updated_at"] = core._now()
    _atomic_save(LEDGER_FILE, ledger)
    summary = _build_summary(ledger)
    _atomic_save(SUMMARY_FILE, summary)
    _atomic_save(
        DIAGNOSTICS_FILE,
        {
            "version": VERSION,
            "generated_at": core._now(),
            "scanned": len(universe),
            "created": created,
            "rejections_and_events": dict(diagnostics.most_common()),
            "summary": summary,
        },
    )
    print("DAY TRADING CANDIDATE SUMMARY:", summary)


if __name__ == "__main__":
    run()
