"""96-hour TP3 runner research for multi-day trends.

The real Premium trade still closes at TP3. This shadow module measures whether
price continued in the same direction for 12/24/48/72/96 hours after TP3 and
stores the current 4H trend-life snapshot. No Telegram, no orders, no exit rule
changes.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, List, Optional

import multi_day_trend_shadow as trendlife

VERSION = "TP3_MULTI_DAY_RUNNER_SHADOW_V1_2026_08_24"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_EXIT_MUTATION"
LEDGER_FILE = "trade_ledger.json"
MAX_TRACK_HOURS = 96
RESTORE_MAX_HOURS = 108
MIN_RECHECK_SECONDS = 30 * 60
MAX_TRADES_PER_RUN = 12
CHECKPOINT_HOURS = (12, 24, 48, 72, 96)
FETCH_1H_LIMIT = 180
FETCH_4H_LIMIT = 180
CANDLE_SECONDS = 3600


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        value = float(value)
        return value if math.isfinite(value) else default
    except Exception:
        return default


def _load(path: str) -> Dict[str, Any]:
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
            prefix=".tp3_multi_day.",
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


def _normalize(rows: Any) -> List[Dict[str, float]]:
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


def _directional_percent(direction: str, reference: float, price: float) -> float:
    if reference <= 0:
        return 0.0
    raw = (price - reference) / reference * 100.0
    return raw if str(direction).upper() == "LONG" else -raw


def _directional_r(direction: str, reference: float, price: float, risk: float) -> float:
    if risk <= 0:
        return 0.0
    raw = (price - reference) / risk
    return raw if str(direction).upper() == "LONG" else -raw


def measure_rows(
    trade: Dict[str, Any],
    rows: Any,
    *,
    now_ts: int,
    closed_at: int,
    reference_price: float,
) -> Dict[str, Any]:
    direction = str(trade.get("direction") or "").upper()
    entry = _sf(trade.get("entry"), 0.0) or 0.0
    sl = _sf(trade.get("sl"), 0.0) or 0.0
    risk = abs(entry - sl)
    end_ts = min(int(now_ts), int(closed_at) + MAX_TRACK_HOURS * 3600)
    candles = [
        candle for candle in _normalize(rows)
        if int(candle["time"]) >= int(closed_at)
        and int(candle["time"]) + CANDLE_SECONDS <= end_ts
    ]
    if not candles:
        return {
            "observations": 0,
            "checkpoints": {},
            "max_favorable_percent": 0.0,
            "max_adverse_percent": 0.0,
            "max_favorable_r": 0.0,
            "max_adverse_r": 0.0,
        }

    if direction == "LONG":
        best = max([reference_price] + [row["high"] for row in candles])
        worst = min([reference_price] + [row["low"] for row in candles])
    else:
        best = min([reference_price] + [row["low"] for row in candles])
        worst = max([reference_price] + [row["high"] for row in candles])

    max_favorable_pct = max(0.0, _directional_percent(direction, reference_price, best))
    max_adverse_pct = max(0.0, -_directional_percent(direction, reference_price, worst))
    max_favorable_r = max(0.0, _directional_r(direction, reference_price, best, risk))
    max_adverse_r = max(0.0, -_directional_r(direction, reference_price, worst, risk))

    checkpoints: Dict[str, Any] = {}
    for hour in CHECKPOINT_HOURS:
        target = int(closed_at) + int(hour) * 3600
        if end_ts < target:
            continue
        eligible = [
            row for row in candles
            if int(row["time"]) + CANDLE_SECONDS <= target
        ]
        if not eligible:
            continue
        close = float(eligible[-1]["close"])
        checkpoints[str(hour)] = {
            "hour": int(hour),
            "price": round(close, 12),
            "directional_percent_from_tp3": round(
                _directional_percent(direction, reference_price, close), 4
            ),
            "directional_r_from_tp3": round(
                _directional_r(direction, reference_price, close, risk), 4
            ),
        }

    return {
        "observations": len(candles),
        "best_price": round(float(best), 12),
        "worst_price": round(float(worst), 12),
        "max_favorable_percent": round(float(max_favorable_pct), 4),
        "max_adverse_percent": round(float(max_adverse_pct), 4),
        "max_favorable_r": round(float(max_favorable_r), 4),
        "max_adverse_r": round(float(max_adverse_r), 4),
        "checkpoints": checkpoints,
    }


def _runner_class(extra_r: float, adverse_r: float) -> str:
    extra = float(extra_r or 0.0)
    adverse = float(adverse_r or 0.0)
    if extra >= 1.0 and extra >= adverse * 1.5:
        return "MULTI_DAY_RUNNER_STRONG"
    if extra >= 0.5 and extra >= adverse:
        return "MULTI_DAY_RUNNER_USEFUL"
    if extra >= 0.2:
        return "MULTI_DAY_RUNNER_LIMITED"
    return "MULTI_DAY_RUNNER_NO_EDGE"


def monitor(
    exchange: Any,
    ledger_file: str = LEDGER_FILE,
    *,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    ledger = _load(ledger_file)
    trades = ledger.get("trades") if isinstance(ledger.get("trades"), dict) else {}
    if not trades:
        return {"checked": 0, "changed": 0, "classes": {}}

    candidates: List[Dict[str, Any]] = []
    for trade in trades.values():
        if not isinstance(trade, dict):
            continue
        if str(trade.get("final_result") or "").upper() != "TP3":
            continue
        closed_at = int(trade.get("closed_at") or 0)
        if closed_at <= 0 or now - closed_at > RESTORE_MAX_HOURS * 3600:
            continue
        holder = trade.get("tp3_multi_day_runner_shadow")
        if isinstance(holder, dict):
            if str(holder.get("status") or "").upper() == "COMPLETED_96H":
                continue
            last_checked = int(holder.get("last_checked_at") or 0)
            if last_checked > 0 and now - last_checked < MIN_RECHECK_SECONDS:
                continue
        candidates.append(trade)

    candidates.sort(key=lambda row: int(row.get("closed_at") or 0), reverse=True)
    candidates = candidates[:MAX_TRADES_PER_RUN]

    checked = 0
    changed = 0
    classes: Counter[str] = Counter()

    for trade in candidates:
        symbol = str(trade.get("symbol") or "").upper()
        direction = str(trade.get("direction") or "").upper()
        closed_at = int(trade.get("closed_at") or 0)
        reference = _sf(trade.get("exit_price"), _sf(trade.get("tp3"), 0.0)) or 0.0
        if not symbol or direction not in {"LONG", "SHORT"} or reference <= 0:
            continue

        try:
            rows1 = exchange.fetch_ohlcv(
                _to_okx_symbol(symbol), timeframe="1h", limit=FETCH_1H_LIMIT
            )
            rows4 = exchange.fetch_ohlcv(
                _to_okx_symbol(symbol), timeframe="4h", limit=FETCH_4H_LIMIT
            )
        except Exception as exc:
            print(symbol, "TP3 96H runner veri hatası:", exc)
            continue

        metrics = measure_rows(
            trade,
            rows1,
            now_ts=now,
            closed_at=closed_at,
            reference_price=reference,
        )
        if int(metrics.get("observations") or 0) <= 0:
            continue

        trend_snapshot = None
        f1 = trendlife._frame(rows1)
        f4 = trendlife._frame(rows4)
        if f1 is not None and f4 is not None:
            try:
                trend_snapshot = trendlife.evaluate_frames(
                    direction,
                    f1,
                    f4,
                    float(f1.iloc[-1]["close"]),
                    now_ts=now,
                )
            except Exception:
                trend_snapshot = None

        holder = trade.get("tp3_multi_day_runner_shadow")
        if not isinstance(holder, dict):
            short_shadow = trade.get("tp3_runner_shadow")
            initial_candidate = None
            if isinstance(short_shadow, dict):
                initial_candidate = short_shadow.get("runner_candidate")
            holder = {
                "version": VERSION,
                "mode": MODE,
                "status": "TRACKING",
                "started_at": closed_at,
                "reference_tp3_price": round(reference, 12),
                "runner_candidate_at_tp3": initial_candidate or "UNKNOWN",
                "max_track_hours": MAX_TRACK_HOURS,
                "shadow_only": True,
            }
            trade["tp3_multi_day_runner_shadow"] = holder

        holder["last_checked_at"] = now
        holder["max_favorable_percent"] = metrics.get("max_favorable_percent")
        holder["max_adverse_percent"] = metrics.get("max_adverse_percent")
        holder["max_favorable_r"] = metrics.get("max_favorable_r")
        holder["max_adverse_r"] = metrics.get("max_adverse_r")
        holder["best_price"] = metrics.get("best_price")
        holder["worst_price"] = metrics.get("worst_price")
        holder["checkpoints"] = metrics.get("checkpoints") or {}
        holder["current_4h_trend_life"] = trend_snapshot
        holder["runner_value_class"] = _runner_class(
            float(metrics.get("max_favorable_r") or 0.0),
            float(metrics.get("max_adverse_r") or 0.0),
        )
        if now >= closed_at + MAX_TRACK_HOURS * 3600:
            holder["status"] = "COMPLETED_96H"
            holder["completed_at"] = now
        else:
            holder["status"] = "TRACKING"

        checked += 1
        changed += 1
        classes[holder["runner_value_class"]] += 1
        print(
            "TP3 96H RUNNER SHADOW:",
            symbol,
            direction,
            holder.get("runner_value_class"),
            "extraR=",
            holder.get("max_favorable_r"),
            "adverseR=",
            holder.get("max_adverse_r"),
            "4H=",
            (trend_snapshot or {}).get("status"),
        )

    if changed:
        ledger["tp3_multi_day_runner_shadow_summary"] = {
            "version": VERSION,
            "mode": MODE,
            "updated_at": now,
            "checked": checked,
            "classes": dict(classes),
            "max_track_hours": MAX_TRACK_HOURS,
        }
        _atomic_save(ledger_file, ledger)

    return {
        "checked": checked,
        "changed": changed,
        "classes": dict(classes),
        "version": VERSION,
    }


def main() -> None:
    try:
        import ccxt
        exchange = ccxt.okx({
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        result = monitor(exchange)
        print("TP3 96H runner shadow özet:", result)
    except Exception as exc:
        print("TP3 96H runner shadow ana hata:", exc)


if __name__ == "__main__":
    main()
