"""Shadow-only TP3 runner research.

TP3 remains the real final exit. This module does NOT send Telegram messages,
does NOT open/close orders, and does NOT mutate TP/SL/BE rules.

It freezes the latest open-position continuation snapshot at TP3 and measures
how much extra movement happened after TP3 for 15/30/60/120/240/360 minutes.
The goal is to learn whether a future small runner position would add value.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, List, Optional

VERSION = "TP3_RUNNER_SHADOW_V1_2026_08_24"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_EXIT_MUTATION"
LEDGER_FILE = "trade_ledger.json"
TIMEFRAME = "5m"
CANDLE_SECONDS = 5 * 60
CHECKPOINT_MINUTES = (15, 30, 60, 120, 240, 360)
MAX_TRACK_MINUTES = 360
RESTORE_MAX_HOURS = 12
FETCH_LIMIT = 100
MAX_TRADES_PER_RUN = 12


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _load_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".tp3_runner_shadow.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
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


def _to_okx_symbol(symbol: str) -> str:
    raw = str(symbol or "").upper().strip()
    base = raw[:-4] if raw.endswith("USDT") else raw
    return f"{base}/USDT:USDT"


def classify_runner_candidate(snapshot: Any) -> str:
    if not isinstance(snapshot, dict):
        return "NO_SNAPSHOT"
    status = str(snapshot.get("status") or "").upper()
    score = int(_sf(snapshot.get("score"), 0) or 0)
    confidence = str(snapshot.get("confidence") or "").upper()
    if status == "DEVAM_GUCLU" and score >= 80 and confidence in {"YUKSEK", "ORTA"}:
        return "RUNNER_STRONG_SHADOW"
    if status == "DEVAM" and score >= 64 and confidence == "YUKSEK":
        return "RUNNER_POSSIBLE_SHADOW"
    return "NO_RUNNER_SHADOW"


def classify_extension(max_favorable_r: float) -> str:
    value = float(max_favorable_r or 0.0)
    if value >= 1.00:
        return "STRONG_EXTENSION"
    if value >= 0.50:
        return "USEFUL_EXTENSION"
    if value >= 0.20:
        return "LIMITED_EXTENSION"
    return "NO_MEANINGFUL_EXTENSION"


def _frozen_continuation(trade: Dict[str, Any]) -> Dict[str, Any]:
    snap = trade.get("continuation_shadow")
    if not isinstance(snap, dict):
        return {
            "available": False,
            "runner_candidate": "NO_SNAPSHOT",
        }
    return {
        "available": True,
        "version": snap.get("version"),
        "updated_at": int(snap.get("updated_at") or 0),
        "status": snap.get("status"),
        "score": snap.get("score"),
        "confidence": snap.get("confidence"),
        "action_shadow": snap.get("action_shadow"),
        "move_from_entry_percent": snap.get("move_from_entry_percent"),
        "trend_origin": snap.get("trend_origin"),
        "remaining_move_shadow": snap.get("remaining_move_shadow"),
        "metrics": snap.get("metrics"),
        "runner_candidate": classify_runner_candidate(snap),
    }


def _normalize_rows(rows: Any) -> List[Dict[str, float]]:
    result: List[Dict[str, float]] = []
    if not isinstance(rows, list):
        return result
    for row in rows:
        try:
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            result.append({
                "time": int(float(row[0]) / 1000),
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
            })
        except Exception:
            continue
    return result


def _directional_r(direction: str, reference: float, price: float, risk: float) -> float:
    if risk <= 0:
        return 0.0
    raw = (price - reference) / risk
    return raw if str(direction).upper() == "LONG" else -raw


def evaluate_rows(
    trade: Dict[str, Any],
    rows: Any,
    *,
    now_ts: int,
    started_at: int,
    reference_price: float,
) -> Dict[str, Any]:
    direction = str(trade.get("direction") or "").upper()
    entry = _sf(trade.get("entry"), 0.0) or 0.0
    sl = _sf(trade.get("sl"), 0.0) or 0.0
    risk = abs(entry - sl)
    candles = _normalize_rows(rows)
    tracking_end = min(int(now_ts), int(started_at) + MAX_TRACK_MINUTES * 60)

    # Conservative 5M measurement: ignore the partial candle containing TP3.
    relevant = [
        candle for candle in candles
        if int(candle["time"]) >= int(started_at)
        and int(candle["time"]) + CANDLE_SECONDS <= tracking_end
    ]

    if not relevant:
        return {
            "observations": 0,
            "max_favorable_r": 0.0,
            "max_adverse_r": 0.0,
            "max_favorable_percent": 0.0,
            "max_adverse_percent": 0.0,
            "checkpoints": {},
        }

    if direction == "LONG":
        best = max([reference_price] + [c["high"] for c in relevant])
        worst = min([reference_price] + [c["low"] for c in relevant])
        favorable_pct = max(0.0, (best - reference_price) / reference_price * 100)
        adverse_pct = max(0.0, (reference_price - worst) / reference_price * 100)
    else:
        best = min([reference_price] + [c["low"] for c in relevant])
        worst = max([reference_price] + [c["high"] for c in relevant])
        favorable_pct = max(0.0, (reference_price - best) / reference_price * 100)
        adverse_pct = max(0.0, (worst - reference_price) / reference_price * 100)

    favorable_r = max(0.0, _directional_r(direction, reference_price, best, risk))
    adverse_r = max(0.0, -_directional_r(direction, reference_price, worst, risk))

    checkpoints: Dict[str, Any] = {}
    for minute in CHECKPOINT_MINUTES:
        target_end = int(started_at) + int(minute) * 60
        if tracking_end < target_end:
            continue
        eligible = [
            candle for candle in relevant
            if int(candle["time"]) + CANDLE_SECONDS <= target_end
        ]
        if not eligible:
            continue
        close = float(eligible[-1]["close"])
        checkpoints[str(minute)] = {
            "minute": int(minute),
            "price": round(close, 12),
            "directional_r_from_tp3": round(
                _directional_r(direction, reference_price, close, risk), 4
            ),
            "directional_percent_from_tp3": round(
                ((close - reference_price) / reference_price * 100)
                * (1 if direction == "LONG" else -1),
                4,
            ),
        }

    return {
        "observations": len(relevant),
        "best_price": round(float(best), 12),
        "worst_price": round(float(worst), 12),
        "max_favorable_r": round(float(favorable_r), 4),
        "max_adverse_r": round(float(adverse_r), 4),
        "max_favorable_percent": round(float(favorable_pct), 4),
        "max_adverse_percent": round(float(adverse_pct), 4),
        "checkpoints": checkpoints,
    }


def monitor_tp3_runners(
    exchange: Any,
    ledger_file: str = LEDGER_FILE,
    *,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    ledger = _load_json(ledger_file)
    trades = ledger.get("trades") if isinstance(ledger.get("trades"), dict) else {}
    if not trades:
        return {"checked": 0, "changed": 0, "candidates": {}}

    candidates = []
    for trade in trades.values():
        if not isinstance(trade, dict):
            continue
        if str(trade.get("final_result") or "").upper() != "TP3":
            continue
        closed_at = int(trade.get("closed_at") or 0)
        if closed_at <= 0:
            continue
        existing = trade.get("tp3_runner_shadow")
        if isinstance(existing, dict) and str(existing.get("status") or "").upper() == "COMPLETED":
            continue
        if not isinstance(existing, dict) and now - closed_at > RESTORE_MAX_HOURS * 3600:
            continue
        candidates.append(trade)

    candidates.sort(key=lambda item: int(item.get("closed_at") or 0), reverse=True)
    candidates = candidates[:MAX_TRADES_PER_RUN]

    checked = 0
    changed = 0
    candidate_counter: Counter[str] = Counter()
    extension_counter: Counter[str] = Counter()

    for trade in candidates:
        symbol = str(trade.get("symbol") or "").upper()
        direction = str(trade.get("direction") or "").upper()
        closed_at = int(trade.get("closed_at") or 0)
        reference = _sf(trade.get("exit_price"), _sf(trade.get("tp3"), 0.0)) or 0.0
        if not symbol or direction not in {"LONG", "SHORT"} or reference <= 0:
            continue

        holder = trade.get("tp3_runner_shadow")
        if not isinstance(holder, dict):
            frozen = _frozen_continuation(trade)
            holder = {
                "version": VERSION,
                "mode": MODE,
                "status": "TRACKING",
                "started_at": closed_at,
                "reference_tp3_price": round(reference, 12),
                "timeframe": TIMEFRAME,
                "max_minutes": MAX_TRACK_MINUTES,
                "continuation_at_tp3": frozen,
                "runner_candidate": frozen.get("runner_candidate"),
                "checkpoints": {},
                "max_favorable_r": 0.0,
                "max_adverse_r": 0.0,
                "max_favorable_percent": 0.0,
                "max_adverse_percent": 0.0,
                "extension_class": "PENDING",
                "shadow_only": True,
            }
            trade["tp3_runner_shadow"] = holder
            changed += 1

        candidate_counter[str(holder.get("runner_candidate") or "UNKNOWN")] += 1

        try:
            rows = exchange.fetch_ohlcv(
                _to_okx_symbol(symbol),
                timeframe=TIMEFRAME,
                since=max(0, closed_at - 10 * 60) * 1000,
                limit=FETCH_LIMIT,
            )
        except Exception as exc:
            print(symbol, "TP3 runner veri hatası:", exc)
            continue

        metrics = evaluate_rows(
            trade,
            rows,
            now_ts=now,
            started_at=closed_at,
            reference_price=reference,
        )
        if int(metrics.get("observations") or 0) <= 0:
            continue

        checked += 1
        holder["last_checked_at"] = now
        holder["best_price"] = metrics.get("best_price")
        holder["worst_price"] = metrics.get("worst_price")
        holder["max_favorable_r"] = metrics.get("max_favorable_r")
        holder["max_adverse_r"] = metrics.get("max_adverse_r")
        holder["max_favorable_percent"] = metrics.get("max_favorable_percent")
        holder["max_adverse_percent"] = metrics.get("max_adverse_percent")
        holder["checkpoints"] = metrics.get("checkpoints") or {}
        extension_class = classify_extension(float(metrics.get("max_favorable_r") or 0.0))
        holder["extension_class"] = extension_class
        extension_counter[extension_class] += 1

        if now >= closed_at + MAX_TRACK_MINUTES * 60:
            holder["status"] = "COMPLETED"
            holder["completed_at"] = now
        else:
            holder["status"] = "TRACKING"
        changed += 1

        print(
            "TP3 RUNNER SHADOW:",
            symbol,
            direction,
            holder.get("runner_candidate"),
            "extraR=",
            holder.get("max_favorable_r"),
            "adverseR=",
            holder.get("max_adverse_r"),
            "class=",
            holder.get("extension_class"),
        )

    if changed:
        ledger["tp3_runner_shadow_summary"] = {
            "version": VERSION,
            "mode": MODE,
            "updated_at": now,
            "checked": checked,
            "candidate_classes": dict(candidate_counter),
            "extension_classes": dict(extension_counter),
        }
        _atomic_save(ledger_file, ledger)

    return {
        "checked": checked,
        "changed": changed,
        "candidate_classes": dict(candidate_counter),
        "extension_classes": dict(extension_counter),
        "version": VERSION,
    }


def main() -> None:
    try:
        import ccxt
        exchange = ccxt.okx({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        summary = monitor_tp3_runners(exchange)
        print("TP3 runner shadow özet:", summary)
    except Exception as exc:
        print("TP3 runner shadow ana hata:", exc)


if __name__ == "__main__":
    main()
