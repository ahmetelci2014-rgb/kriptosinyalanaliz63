"""Movement Start V3 Shadow — OKX public order-flow learner.

Amaç: V2 5M price-structure adayı oluştuğunda, canlı Premium kararını değiştirmeden
OKX public trades + top-of-book verisiyle gerçek alıcı/satıcı baskısını ölçmek.

Bu modül:
- Telegram göndermez.
- Emir açmaz.
- Premium canlı giriş kurallarını değiştirmez.
- Yalnız V2 PREP/ARMED/TRIGGER adaylarında sınırlı sayıda public REST sorgusu yapar.
- Order-book imbalance, taker trade imbalance, spread ve pressure delta kaydeder.
- 2R/3R/5R ve stop sırasını gölgede izler.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter, defaultdict
from typing import Any, Callable, Dict, Optional, Tuple

import pandas as pd
import requests

VERSION = "MOVEMENT_START_V3_OKX_ORDERFLOW_SHADOW_2026_08_20"
STATE_FILE = "movement_start_v3_orderflow_shadow.json"
MODE = "SHADOW_LEARNING_ONLY_NO_TELEGRAM_NO_ORDERS"

OKX_BASE = "https://www.okx.com"
REQUEST_TIMEOUT = 3.5
MAX_ORDERFLOW_QUERIES_PER_RUN = 10
MIN_PREP_QUERY_SCORE = 72
CONFIRM_SCORE = 65
MAX_TRACK_SECONDS = 180 * 60
DUPLICATE_SECONDS = 45 * 60
KEEP_SECONDS = 14 * 24 * 60 * 60
MAX_RECORDS = 2500
MAX_SNAPSHOTS = 5000

_STATE: Optional[Dict[str, Any]] = None
_STATE_PATH = STATE_FILE
_DIRTY = False
_QUERY_COUNT = 0
_SESSION = requests.Session()


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=".movement_start_v3.", suffix=".tmp", delete=False,
        ) as handle:
            tmp = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        tmp = None
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass


def _default_state() -> Dict[str, Any]:
    return {
        "version": VERSION,
        "mode": MODE,
        "updated_at": 0,
        "records": [],
        "open": {},
        "last_started": {},
        "last_flow": {},
        "snapshots": [],
        "summary": {},
    }


def _load(path: str) -> Dict[str, Any]:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                base = _default_state()
                base.update(data)
                base["version"] = VERSION
                base["mode"] = MODE
                return base
    except Exception:
        pass
    return _default_state()


def begin(path: str = STATE_FILE) -> None:
    global _STATE, _STATE_PATH, _DIRTY, _QUERY_COUNT
    _STATE_PATH = path
    _STATE = _load(path)
    _DIRTY = False
    _QUERY_COUNT = 0


def _state() -> Dict[str, Any]:
    global _STATE
    if _STATE is None:
        begin()
    return _STATE if isinstance(_STATE, dict) else _default_state()


def okx_inst_id(symbol: str) -> str:
    raw = str(symbol or "").upper().strip()
    if raw.endswith("-SWAP") and "-USDT-" in raw:
        return raw
    compact = raw.replace("/", "").replace(":", "").replace("-", "")
    if compact.endswith("USDTUSDT"):
        compact = compact[:-4]
    if not compact.endswith("USDT") or len(compact) <= 4:
        return ""
    base = compact[:-4]
    return f"{base}-USDT-SWAP"


def _get_json(path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    try:
        response = _SESSION.get(OKX_BASE + path, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
        if str(payload.get("code", "0")) != "0":
            return None
        return payload
    except Exception:
        return None


def fetch_order_flow(symbol: str, now_ts: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """OKX public top-5 book + recent taker trades snapshot."""
    inst_id = okx_inst_id(symbol)
    if not inst_id:
        return None
    book_payload = _get_json("/api/v5/market/books", {"instId": inst_id, "sz": "5"})
    trades_payload = _get_json("/api/v5/market/trades", {"instId": inst_id, "limit": "100"})
    if not book_payload or not trades_payload:
        return None
    book_data = book_payload.get("data") or []
    trades = trades_payload.get("data") or []
    if not book_data or not isinstance(trades, list):
        return None

    book = book_data[0]
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    if not bids or not asks:
        return None

    def _qty(levels: Any) -> float:
        return sum(max(0.0, _sf(row[1])) for row in levels if isinstance(row, (list, tuple)) and len(row) >= 2)

    bid_qty = _qty(bids)
    ask_qty = _qty(asks)
    total_qty = bid_qty + ask_qty
    book_imbalance = (bid_qty - ask_qty) / total_qty if total_qty > 0 else 0.0

    bid1 = max(0.0, _sf(bids[0][1]))
    ask1 = max(0.0, _sf(asks[0][1]))
    top_total = bid1 + ask1
    top_imbalance = (bid1 - ask1) / top_total if top_total > 0 else 0.0

    best_bid = _sf(bids[0][0])
    best_ask = _sf(asks[0][0])
    mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else 0.0
    spread_bps = (best_ask - best_bid) / mid * 10000.0 if mid > 0 else 999.0

    buy_notional = sell_notional = 0.0
    buy_count = sell_count = 0
    recent_buy = recent_sell = 0.0
    trade_ts = []
    for idx, trade in enumerate(trades):
        if not isinstance(trade, dict):
            continue
        px = _sf(trade.get("px"))
        sz = max(0.0, _sf(trade.get("sz")))
        notion = px * sz
        side = str(trade.get("side") or "").lower()
        if side == "buy":
            buy_notional += notion
            buy_count += 1
            if idx < 25:
                recent_buy += notion
        elif side == "sell":
            sell_notional += notion
            sell_count += 1
            if idx < 25:
                recent_sell += notion
        ts = int(_sf(trade.get("ts"), 0.0))
        if ts > 0:
            trade_ts.append(ts)

    total_notional = buy_notional + sell_notional
    trade_imbalance = (buy_notional - sell_notional) / total_notional if total_notional > 0 else 0.0
    buy_ratio = buy_notional / total_notional if total_notional > 0 else 0.5
    recent_total = recent_buy + recent_sell
    recent_trade_imbalance = (recent_buy - recent_sell) / recent_total if recent_total > 0 else 0.0
    total_count = buy_count + sell_count
    buy_count_ratio = buy_count / total_count if total_count else 0.5

    span_ms = (max(trade_ts) - min(trade_ts)) if len(trade_ts) >= 2 else 0
    trades_per_second = total_count / (span_ms / 1000.0) if span_ms > 0 else 0.0

    return {
        "captured_at": int(now_ts if now_ts is not None else time.time()),
        "inst_id": inst_id,
        "book_imbalance": round(book_imbalance, 5),
        "top_imbalance": round(top_imbalance, 5),
        "spread_bps": round(spread_bps, 4),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_qty_top5": round(bid_qty, 8),
        "ask_qty_top5": round(ask_qty, 8),
        "trade_imbalance": round(trade_imbalance, 5),
        "recent_trade_imbalance": round(recent_trade_imbalance, 5),
        "buy_ratio": round(buy_ratio, 5),
        "buy_count_ratio": round(buy_count_ratio, 5),
        "trades_count": total_count,
        "trades_per_second": round(trades_per_second, 4),
    }


def score_order_flow(flow: Dict[str, Any], direction: str, previous: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, bool], float]:
    direction = str(direction or "").upper()
    if direction not in {"LONG", "SHORT"}:
        return 0, {}, 0.0
    sign = 1.0 if direction == "LONG" else -1.0
    book = sign * _sf(flow.get("book_imbalance"))
    top = sign * _sf(flow.get("top_imbalance"))
    trades = sign * _sf(flow.get("trade_imbalance"))
    recent = sign * _sf(flow.get("recent_trade_imbalance"))
    count_edge = sign * ((_sf(flow.get("buy_count_ratio"), 0.5) - 0.5) * 2.0)
    spread = _sf(flow.get("spread_bps"), 999.0)

    previous_book = sign * _sf((previous or {}).get("book_imbalance"))
    pressure_delta = book - previous_book if previous else 0.0

    conditions = {
        "book_support": book >= 0.10,
        "top_support": top >= 0.08,
        "trade_support": trades >= 0.10,
        "recent_trade_support": recent >= 0.14,
        "count_support": count_edge >= 0.08,
        "spread_ok": spread <= 12.0,
        "pressure_improving": bool(previous) and pressure_delta >= 0.04,
        "strong_flow": (book >= 0.18 and recent >= 0.18) or (trades >= 0.22 and recent >= 0.22),
    }
    weights = {
        "book_support": 18,
        "top_support": 9,
        "trade_support": 18,
        "recent_trade_support": 19,
        "count_support": 8,
        "spread_ok": 10,
        "pressure_improving": 8,
        "strong_flow": 10,
    }
    score = sum(weight for name, weight in weights.items() if conditions.get(name))
    if spread > 25.0:
        score -= 20
    if book <= -0.15 and recent <= -0.15:
        score -= 25
    return max(0, min(100, int(round(score)))), conditions, round(pressure_delta, 5)


def should_query(base_result: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(base_result, dict):
        return False
    stage = str(base_result.get("stage") or "").upper()
    score = int(_sf(base_result.get("score"), 0.0))
    if stage in {"ARMED", "TRIGGER"}:
        return True
    return stage == "PREP" and score >= MIN_PREP_QUERY_SCORE


def _bar_extremes(df5m: Any, fallback: float) -> Tuple[float, float]:
    try:
        if df5m is None or not hasattr(df5m, "copy"):
            return fallback, fallback
        frame = df5m.copy()
        for col in ("high", "low"):
            frame[col] = pd.to_numeric(frame[col], errors="coerce")
        frame = frame.dropna().reset_index(drop=True)
        if len(frame) < 3:
            return fallback, fallback
        row = frame.iloc[-2]
        return _sf(row["high"], fallback), _sf(row["low"], fallback)
    except Exception:
        return fallback, fallback


def _update_open(symbol: str, df5m: Any, price: float, now: int) -> None:
    global _DIRTY
    state = _state()
    high, low = _bar_extremes(df5m, price)
    for record in state.get("open", {}).values():
        if str(record.get("symbol")) != symbol:
            continue
        entry = _sf(record.get("entry"))
        risk = _sf(record.get("risk_abs"))
        stop = _sf(record.get("stop"))
        if entry <= 0 or risk <= 0:
            continue
        if record.get("direction") == "LONG":
            favorable = max(high, price) - entry
            adverse = entry - min(low, price)
            hit_stop = min(low, price) <= stop
            hit2 = max(high, price) >= _sf(record.get("target_2r"))
            hit3 = max(high, price) >= _sf(record.get("target_3r"))
            hit5 = max(high, price) >= _sf(record.get("target_5r"))
        else:
            favorable = entry - min(low, price)
            adverse = max(high, price) - entry
            hit_stop = max(high, price) >= stop
            hit2 = min(low, price) <= _sf(record.get("target_2r"))
            hit3 = min(low, price) <= _sf(record.get("target_3r"))
            hit5 = min(low, price) <= _sf(record.get("target_5r"))
        record["max_favorable_r"] = round(max(_sf(record.get("max_favorable_r")), favorable / risk), 4)
        record["max_adverse_r"] = round(max(_sf(record.get("max_adverse_r")), adverse / risk), 4)
        record["last_price"] = price
        record["last_updated_at"] = now
        for level, hit in ((2, hit2), (3, hit3), (5, hit5)):
            key = f"hit_{level}r_at"
            if hit and not record.get(key):
                record[key] = now
        if hit_stop and not record.get("stop_hit_at"):
            record["stop_hit_at"] = now
        if not record.get("first_resolution"):
            if hit_stop and hit2:
                record["first_resolution"] = "AMBIGUOUS_SAME_5M_BAR"
            elif hit2:
                record["first_resolution"] = "R2_FIRST"
            elif hit_stop:
                record["first_resolution"] = "STOP_FIRST"
            if record.get("first_resolution"):
                record["first_resolution_at"] = now
        _DIRTY = True


def _finish_due(now: int) -> None:
    global _DIRTY
    state = _state()
    for key, record in list(state.get("open", {}).items()):
        if now - int(record.get("started_at") or 0) < MAX_TRACK_SECONDS:
            continue
        record["closed_at"] = now
        record["status"] = record.get("first_resolution") or "TIMEOUT"
        if record.get("hit_5r_at"):
            record["highest_r_hit"] = 5
        elif record.get("hit_3r_at"):
            record["highest_r_hit"] = 3
        elif record.get("hit_2r_at"):
            record["highest_r_hit"] = 2
        else:
            record["highest_r_hit"] = 0
        state.setdefault("records", []).append(record)
        state["open"].pop(key, None)
        _DIRTY = True


def observe(
    symbol: str,
    base_result: Optional[Dict[str, Any]],
    df5m: Any,
    current_price: Any = None,
    *,
    now_ts: Optional[int] = None,
    fetcher: Optional[Callable[[str, Optional[int]], Optional[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    global _DIRTY, _QUERY_COUNT
    now = int(now_ts if now_ts is not None else time.time())
    symbol = str(symbol or "").upper()
    price = _sf(current_price)
    if price <= 0 and isinstance(base_result, dict):
        price = _sf(base_result.get("entry"))
    if not symbol or price <= 0:
        return None

    _update_open(symbol, df5m, price, now)
    _finish_due(now)
    if not should_query(base_result) or _QUERY_COUNT >= MAX_ORDERFLOW_QUERIES_PER_RUN:
        return None

    _QUERY_COUNT += 1
    flow_fetcher = fetcher or fetch_order_flow
    flow = flow_fetcher(symbol, now)
    if not isinstance(flow, dict):
        return None

    state = _state()
    direction = str(base_result.get("direction") or "").upper()
    previous = state.get("last_flow", {}).get(symbol)
    flow_score, conditions, pressure_delta = score_order_flow(flow, direction, previous)
    confirmed = bool(
        flow_score >= CONFIRM_SCORE
        and conditions.get("spread_ok")
        and (
            (conditions.get("book_support") and conditions.get("recent_trade_support"))
            or conditions.get("strong_flow")
        )
    )
    snapshot = {
        "symbol": symbol,
        "direction": direction,
        "at": now,
        "base_stage": base_result.get("stage"),
        "base_score": base_result.get("score"),
        "orderflow_score": flow_score,
        "orderflow_confirmed": confirmed,
        "pressure_delta": pressure_delta,
        "flow": flow,
        "conditions": conditions,
    }
    state.setdefault("snapshots", []).append(snapshot)
    state.setdefault("last_flow", {})[symbol] = flow
    _DIRTY = True

    key = f"{symbol}_{direction}"
    active = state.get("open", {}).get(key)
    if isinstance(active, dict):
        active["latest_orderflow_score"] = flow_score
        active["latest_orderflow_confirmed"] = confirmed
        active["orderflow_checks"] = int(active.get("orderflow_checks") or 0) + 1
        if flow_score > int(active.get("best_orderflow_score") or 0):
            active["best_orderflow_score"] = flow_score
        if confirmed and not active.get("first_confirmed_at"):
            active["first_confirmed_at"] = now
            active["first_confirmed_minutes"] = round((now - int(active.get("started_at") or now)) / 60.0, 2)
        return {"event": "FLOW_UPDATE", "record": active, "snapshot": snapshot}

    last_started = int(state.get("last_started", {}).get(key) or 0)
    if now - last_started < DUPLICATE_SECONDS:
        return {"event": "SNAPSHOT_ONLY", "snapshot": snapshot}

    entry = _sf(base_result.get("entry"))
    stop = _sf(base_result.get("stop"))
    risk = _sf(base_result.get("risk_abs"))
    if entry <= 0 or stop <= 0 or risk <= 0:
        return {"event": "SNAPSHOT_ONLY", "snapshot": snapshot}

    record = {
        "id": f"{key}_{now}",
        "symbol": symbol,
        "direction": direction,
        "started_at": now,
        "base_stage": base_result.get("stage"),
        "base_score": base_result.get("score"),
        "entry": entry,
        "stop": stop,
        "risk_abs": risk,
        "risk_percent": base_result.get("risk_percent"),
        "target_2r": base_result.get("target_2r"),
        "target_3r": base_result.get("target_3r"),
        "target_5r": base_result.get("target_5r"),
        "initial_orderflow_score": flow_score,
        "best_orderflow_score": flow_score,
        "latest_orderflow_score": flow_score,
        "initial_orderflow_confirmed": confirmed,
        "latest_orderflow_confirmed": confirmed,
        "first_confirmed_at": now if confirmed else None,
        "first_confirmed_minutes": 0.0 if confirmed else None,
        "orderflow_checks": 1,
        "initial_flow": flow,
        "initial_conditions": conditions,
        "max_favorable_r": 0.0,
        "max_adverse_r": 0.0,
        "version": VERSION,
    }
    state.setdefault("open", {})[key] = record
    state.setdefault("last_started", {})[key] = now
    _DIRTY = True
    return {"event": "START", "record": record, "snapshot": snapshot}


def _summarize() -> Dict[str, Any]:
    state = _state()
    records = state.get("records", [])
    status = Counter(str(r.get("status") or "UNKNOWN") for r in records)
    by_stage = defaultdict(lambda: Counter())
    by_direction = defaultdict(lambda: Counter())
    confirmed = 0
    for record in records:
        stage = str(record.get("base_stage") or "UNKNOWN")
        direction = str(record.get("direction") or "UNKNOWN")
        result = str(record.get("status") or "UNKNOWN")
        by_stage[stage][result] += 1
        by_direction[direction][result] += 1
        if record.get("first_confirmed_at"):
            confirmed += 1
    decisive = status.get("R2_FIRST", 0) + status.get("STOP_FIRST", 0)
    return {
        "version": VERSION,
        "mode": MODE,
        "records": len(records),
        "open": len(state.get("open", {})),
        "snapshots": len(state.get("snapshots", [])),
        "queries_this_run": _QUERY_COUNT,
        "confirmed_records": confirmed,
        "r2_first": status.get("R2_FIRST", 0),
        "stop_first": status.get("STOP_FIRST", 0),
        "timeout": status.get("TIMEOUT", 0),
        "ambiguous": status.get("AMBIGUOUS_SAME_5M_BAR", 0),
        "decisive_r2_rate": round(status.get("R2_FIRST", 0) / decisive * 100.0, 2) if decisive else None,
        "by_stage": {k: dict(v) for k, v in by_stage.items()},
        "by_direction": {k: dict(v) for k, v in by_direction.items()},
    }


def finish(now_ts: Optional[int] = None) -> Dict[str, Any]:
    global _DIRTY
    now = int(now_ts if now_ts is not None else time.time())
    _finish_due(now)
    state = _state()
    cutoff = now - KEEP_SECONDS
    state["records"] = [r for r in state.get("records", []) if int(r.get("started_at") or 0) >= cutoff][-MAX_RECORDS:]
    state["snapshots"] = [s for s in state.get("snapshots", []) if int(s.get("at") or 0) >= cutoff][-MAX_SNAPSHOTS:]
    state["last_started"] = {k: v for k, v in state.get("last_started", {}).items() if int(v or 0) >= cutoff}
    state["summary"] = _summarize()
    state["updated_at"] = now
    if _DIRTY or not os.path.exists(_STATE_PATH):
        _atomic_save(_STATE_PATH, state)
        _DIRTY = False
    return state["summary"]
