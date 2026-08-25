"""Prospective false-positive audit for every promoted Big Move candidate.

Unlike the historical replay that starts from known >=10% winners, this shadow
records every live-time Big Move PROMOTE candidate before downstream portfolio,
duplicate or slot gates. It then follows the candidate for 24h and classifies
SL-before-TP1, TP1->BE, TP3 and timeout outcomes.

No Telegram, no exchange orders, no live rule mutation.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

import profitability_engine as profit

VERSION = "BIG_MOVE_FALSE_POSITIVE_SHADOW_V1_2026_08_25"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_RULE_MUTATION"
STATE_FILE = "big_move_false_positive_shadow.json"
TIMEFRAME = "5m"
TIMEFRAME_SECONDS = 5 * 60
MAX_TRACK_HOURS = 24
MAX_TRACK_SECONDS = MAX_TRACK_HOURS * 60 * 60
DEDUP_SECONDS = 60 * 60
MAX_RECORDS = 900
MAX_ACTIVE_PER_RUN = 24
FETCH_LIMIT = 300

_STATE: Dict[str, Any] = {}
_STATE_PATH = STATE_FILE
_DIRTY = False


def _sf(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value in (None, "", "-"):
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=folder,
            prefix=".big_move_false_positive.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        with open(temp_path, "r", encoding="utf-8") as handle:
            json.load(handle)
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def begin(path: str = STATE_FILE) -> None:
    global _STATE, _STATE_PATH, _DIRTY
    _STATE_PATH = path
    data = _load(path)
    data.setdefault("version", VERSION)
    data.setdefault("mode", MODE)
    data.setdefault("records", [])
    data.setdefault("summary", {})
    data["version"] = VERSION
    data["mode"] = MODE
    _STATE = data
    _DIRTY = False


def _state() -> Dict[str, Any]:
    global _STATE
    if not _STATE:
        begin()
    return _STATE


def _record_id(symbol: str, direction: str, started_at: int) -> str:
    return f"{symbol}_{direction}_{started_at}"


def _is_duplicate(symbol: str, direction: str, started_at: int) -> bool:
    for record in reversed((_state().get("records") or [])[-80:]):
        if not isinstance(record, dict):
            continue
        if str(record.get("symbol") or "").upper() != symbol:
            continue
        if str(record.get("direction") or "").upper() != direction:
            continue
        old_at = int(record.get("started_at") or 0)
        if old_at > 0 and abs(started_at - old_at) < DEDUP_SECONDS:
            return True
    return False


def observe(candidate: Dict[str, Any], *, now_value: Optional[int] = None) -> Optional[str]:
    """Persist one promoted Big Move candidate before downstream send gates."""
    global _DIRTY
    if not isinstance(candidate, dict):
        return None
    if str(candidate.get("source") or "").upper() != "BIG_MOVE_ENTRY":
        return None

    symbol = str(candidate.get("symbol") or "").upper()
    direction = str(candidate.get("direction") or "").upper()
    entry = _sf(candidate.get("entry"))
    tp1 = _sf(candidate.get("tp1"))
    tp2 = _sf(candidate.get("tp2"))
    tp3 = _sf(candidate.get("tp3"))
    sl = _sf(candidate.get("sl"))
    started_at = int(now_value if now_value is not None else time.time())

    if (
        not symbol
        or direction not in {"LONG", "SHORT"}
        or any(value is None or value <= 0 for value in (entry, tp1, tp2, tp3, sl))
    ):
        return None
    if _is_duplicate(symbol, direction, started_at):
        return None

    record_id = _record_id(symbol, direction, started_at)
    row = {
        "record_id": record_id,
        "symbol": symbol,
        "direction": direction,
        "source": "BIG_MOVE_ENTRY",
        "candidate_only": True,
        "started_at": started_at,
        "entry": entry,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "sl": sl,
        "score": int(_sf(candidate.get("score"), 0) or 0),
        "risk_percent": _sf(candidate.get("risk_percent")),
        "stage": candidate.get("big_move_stage"),
        "base_score": candidate.get("big_move_base_score"),
        "direction_gap": candidate.get("big_move_direction_gap"),
        "support_1h": candidate.get("big_move_1h_points"),
        "support_4h": candidate.get("big_move_4h_points"),
        "break_extension_atr": candidate.get("big_move_break_extension_atr"),
        "origin_move_percent": candidate.get("big_move_origin_move_percent"),
        "flow_score": candidate.get("big_move_flow_score"),
        "flow_confirmed": bool(candidate.get("big_move_flow_confirmed")),
        "status": "TRACKING",
        "final_result": None,
        "r_result": None,
        "net_r_after_costs": None,
        "tp1_hit": False,
        "tp2_hit": False,
        "tp3_hit": False,
        "tp1_hit_at": 0,
        "resolved_at": 0,
        "last_checked_at": 0,
        "last_price": entry,
        "best_favorable_r": 0.0,
        "worst_adverse_r": 0.0,
    }
    rows = _state().setdefault("records", [])
    rows.append(row)
    if len(rows) > MAX_RECORDS:
        rows[:] = rows[-MAX_RECORDS:]
    _DIRTY = True
    return record_id


def _okx_symbol(symbol: str) -> str:
    base = str(symbol or "").upper()
    if base.endswith("USDT"):
        base = base[:-4]
    return f"{base}/USDT:USDT"


def _fetch(exchange: Any, record: Dict[str, Any], now_value: int) -> List[Dict[str, float]]:
    start = int(record.get("started_at") or 0)
    if start <= 0:
        return []
    end = min(now_value, start + MAX_TRACK_SECONDS)
    try:
        rows = exchange.fetch_ohlcv(
            _okx_symbol(record.get("symbol")),
            timeframe=TIMEFRAME,
            since=max(0, start - TIMEFRAME_SECONDS) * 1000,
            limit=FETCH_LIMIT,
        )
    except Exception as exc:
        print(record.get("symbol"), "Big Move false-positive mum hatası:", exc)
        return []

    result = []
    for item in rows or []:
        try:
            candle_time = int(item[0] / 1000)
            if candle_time + TIMEFRAME_SECONDS <= start:
                continue
            if candle_time > end:
                continue
            result.append(
                {
                    "time": candle_time,
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                }
            )
        except Exception:
            continue
    result.sort(key=lambda item: item["time"])
    return result


def _target_r(record: Dict[str, Any], key: str) -> Optional[float]:
    entry = _sf(record.get("entry"))
    sl = _sf(record.get("sl"))
    target = _sf(record.get(key))
    if None in (entry, sl, target):
        return None
    risk = abs(entry - sl)
    return abs(target - entry) / risk if risk > 0 else None


def _directional_r(record: Dict[str, Any], price: float) -> Optional[float]:
    entry = _sf(record.get("entry"))
    sl = _sf(record.get("sl"))
    if entry is None or sl is None:
        return None
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    if record.get("direction") == "LONG":
        return (price - entry) / risk
    return (entry - price) / risk


def _exit_r(record: Dict[str, Any], final_result: str, last_price: float) -> Optional[float]:
    tp1_r = _target_r(record, "tp1")
    tp3_r = _target_r(record, "tp3")
    if final_result == "SL":
        return -1.0
    if final_result == "TP3":
        if tp1_r is None or tp3_r is None:
            return None
        return 0.50 * tp1_r + 0.50 * tp3_r
    if final_result == "TP1_SONRASI_BE":
        return 0.50 * tp1_r if tp1_r is not None else None
    remaining = _directional_r(record, last_price)
    if remaining is None:
        return None
    if bool(record.get("tp1_hit")) and tp1_r is not None:
        return 0.50 * tp1_r + 0.50 * remaining
    return remaining


def _update_excursion(record: Dict[str, Any], candles: Iterable[Dict[str, float]]) -> None:
    entry = _sf(record.get("entry"))
    sl = _sf(record.get("sl"))
    if entry is None or sl is None:
        return
    risk = abs(entry - sl)
    if risk <= 0:
        return
    rows = list(candles)
    if not rows:
        return
    if record.get("direction") == "LONG":
        best = max([entry] + [row["high"] for row in rows])
        worst = min([entry] + [row["low"] for row in rows])
        favorable = max(0.0, (best - entry) / risk)
        adverse = max(0.0, (entry - worst) / risk)
    else:
        best = min([entry] + [row["low"] for row in rows])
        worst = max([entry] + [row["high"] for row in rows])
        favorable = max(0.0, (entry - best) / risk)
        adverse = max(0.0, (worst - entry) / risk)
    record["best_favorable_r"] = round(favorable, 4)
    record["worst_adverse_r"] = round(adverse, 4)


def _simulate(record: Dict[str, Any], candles: List[Dict[str, float]], now_value: int) -> bool:
    if not candles:
        return False
    direction = str(record.get("direction") or "").upper()
    entry = float(record["entry"])
    tp1 = float(record["tp1"])
    tp2 = float(record["tp2"])
    tp3 = float(record["tp3"])
    sl = float(record["sl"])
    started_at = int(record.get("started_at") or 0)

    tp1_hit = False
    tp2_hit = False
    tp3_hit = False
    tp1_hit_at = 0
    final_result = None
    resolved_at = 0
    last_price = entry

    _update_excursion(record, candles)

    for candle in candles:
        candle_time = int(candle["time"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        last_price = close
        just_hit_tp1 = False

        if direction == "LONG":
            if not tp1_hit:
                if low <= sl and high >= tp1:
                    if close >= entry:
                        tp1_hit = True
                        just_hit_tp1 = True
                        tp1_hit_at = candle_time
                    else:
                        final_result = "SL"
                        resolved_at = candle_time
                        break
                elif low <= sl:
                    final_result = "SL"
                    resolved_at = candle_time
                    break
                elif high >= tp1:
                    tp1_hit = True
                    just_hit_tp1 = True
                    tp1_hit_at = candle_time

            if tp1_hit and not tp2_hit and high >= tp2:
                tp2_hit = True
            if tp1_hit and not tp3_hit and high >= tp3:
                tp3_hit = True
                final_result = "TP3"
                resolved_at = candle_time
                break
            if tp1_hit and not just_hit_tp1 and candle_time > tp1_hit_at and low <= entry:
                final_result = "TP1_SONRASI_BE"
                resolved_at = candle_time
                break

        elif direction == "SHORT":
            if not tp1_hit:
                if high >= sl and low <= tp1:
                    if close <= entry:
                        tp1_hit = True
                        just_hit_tp1 = True
                        tp1_hit_at = candle_time
                    else:
                        final_result = "SL"
                        resolved_at = candle_time
                        break
                elif high >= sl:
                    final_result = "SL"
                    resolved_at = candle_time
                    break
                elif low <= tp1:
                    tp1_hit = True
                    just_hit_tp1 = True
                    tp1_hit_at = candle_time

            if tp1_hit and not tp2_hit and low <= tp2:
                tp2_hit = True
            if tp1_hit and not tp3_hit and low <= tp3:
                tp3_hit = True
                final_result = "TP3"
                resolved_at = candle_time
                break
            if tp1_hit and not just_hit_tp1 and candle_time > tp1_hit_at and high >= entry:
                final_result = "TP1_SONRASI_BE"
                resolved_at = candle_time
                break

    age = max(0, now_value - started_at)
    if final_result is None and age >= MAX_TRACK_SECONDS:
        final_result = "TIMEOUT_24H"
        resolved_at = started_at + MAX_TRACK_SECONDS

    record["tp1_hit"] = tp1_hit
    record["tp2_hit"] = tp2_hit
    record["tp3_hit"] = tp3_hit
    record["tp1_hit_at"] = tp1_hit_at
    record["last_checked_at"] = now_value
    record["last_price"] = round(last_price, 12)

    if final_result is None:
        return True

    record["status"] = "RESOLVED"
    record["final_result"] = final_result
    record["resolved_at"] = resolved_at or now_value
    gross = _exit_r(record, final_result, last_price)
    record["r_result"] = round(gross, 4) if gross is not None else None
    net = profit.net_r(gross, entry, sl) if gross is not None else None
    record["net_r_after_costs"] = round(net, 4) if net is not None else None
    if final_result == "SL":
        record["classification"] = "FALSE_POSITIVE_SL"
    elif final_result == "TP3":
        record["classification"] = "STRONG_SUCCESS_TP3"
    elif final_result == "TP1_SONRASI_BE":
        record["classification"] = "PARTIAL_THEN_BE"
    else:
        record["classification"] = "NO_DECISIVE_MOVE_24H"
    return True


def _make_exchange() -> Any:
    import ccxt
    return ccxt.okx({"enableRateLimit": True, "options": {"defaultType": "swap"}})


def update_outcomes(exchange: Any = None, *, now_value: Optional[int] = None) -> Dict[str, Any]:
    global _DIRTY
    state = _state()
    now_value = int(now_value if now_value is not None else time.time())
    active = [
        record
        for record in state.get("records", [])
        if isinstance(record, dict) and str(record.get("status") or "").upper() == "TRACKING"
    ]
    active.sort(key=lambda row: int(row.get("started_at") or 0))
    if not active:
        return summary(state)
    if exchange is None:
        exchange = _make_exchange()

    processed = 0
    for record in active[:MAX_ACTIVE_PER_RUN]:
        candles = _fetch(exchange, record, now_value)
        if not candles:
            continue
        if _simulate(record, candles, now_value):
            _DIRTY = True
        processed += 1

    if processed:
        print("Big Move false-positive shadow güncellenen aday:", processed)
    return summary(state)


def summary(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    state = state if isinstance(state, dict) else _state()
    records = [row for row in state.get("records", []) if isinstance(row, dict)]
    resolved = [row for row in records if str(row.get("status") or "").upper() == "RESOLVED"]
    tracking = len(records) - len(resolved)
    finals = Counter(str(row.get("final_result") or "") for row in resolved)
    directions = Counter(str(row.get("direction") or "") for row in resolved)
    net_values = [
        float(value)
        for value in (_sf(row.get("net_r_after_costs")) for row in resolved)
        if value is not None
    ]
    false_sl = int(finals.get("SL", 0))
    sample = len(resolved)
    return {
        "version": VERSION,
        "promoted_candidates": len(records),
        "resolved": sample,
        "tracking": tracking,
        "false_positive_sl": false_sl,
        "false_positive_rate_percent": round(false_sl / sample * 100.0, 2) if sample else 0.0,
        "tp3": int(finals.get("TP3", 0)),
        "tp1_then_be": int(finals.get("TP1_SONRASI_BE", 0)),
        "timeout_24h": int(finals.get("TIMEOUT_24H", 0)),
        "net_r_after_costs": round(sum(net_values), 4),
        "avg_net_r_after_costs": round(sum(net_values) / len(net_values), 4) if net_values else None,
        "resolved_by_direction": {
            "LONG": int(directions.get("LONG", 0)),
            "SHORT": int(directions.get("SHORT", 0)),
        },
    }


def finish() -> Dict[str, Any]:
    global _DIRTY
    state = _state()
    state["updated_at"] = int(time.time())
    state["summary"] = summary(state)
    if _DIRTY or not os.path.exists(_STATE_PATH):
        _save(_STATE_PATH, state)
        _DIRTY = False
    return dict(state["summary"])
