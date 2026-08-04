"""Portfolio Risk kararlarının sonradan piyasa sonucunu ölçen gölge izleyici.

Bu modül canlı sinyal kararını değiştirmez, Telegram mesajı göndermez ve emir açmaz.
`portfolio_risk_shadow.json` içindeki BLOCK/ALLOW kararlarını okur; OKX 5M
mumlarıyla 60/240/720/1440 dakikalık yönsel hareket, MFE/MAE ve ortak
%0.5/%1.0 eşiklerinde önce olumlu mu olumsuz mu hareket görüldüğünü ayrı
`portfolio_risk_outcomes.json` dosyasına kaydeder.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple



SOURCE_FILE = "portfolio_risk_shadow.json"
OUTCOME_FILE = "portfolio_risk_outcomes.json"
TIMEFRAME = "5m"
CANDLE_MS = 5 * 60 * 1000
CHECKPOINT_MINUTES: Tuple[int, ...] = (60, 240, 720, 1440)
THRESHOLDS_PERCENT: Tuple[float, ...] = (0.5, 1.0)
MAX_SOURCE_RECORDS = 5000
MAX_OUTCOME_RECORDS = 5000


def now_ts() -> int:
    return int(time.time())


def safe_float(value: Any, default: Optional[float] = 0.0) -> Optional[float]:
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (TypeError, ValueError):
        return default


def normalize_symbol(symbol: Any) -> str:
    value = str(symbol or "").upper().strip()
    if "/" in value:
        base, remainder = value.split("/", 1)
        quote = remainder.split(":", 1)[0]
        value = base + quote
    return (
        value.replace("-", "")
        .replace("_", "")
        .replace(":", "")
        .replace("/", "")
        .replace(" ", "")
    )


def load_json(filename: str) -> Dict[str, Any]:
    if not os.path.exists(filename):
        return {}
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
        return loaded if isinstance(loaded, dict) else {}
    except Exception as exc:
        print(f"{filename} okuma hatası: {exc}")
        return {}


def save_json_atomically(filename: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(filename)) or "."
    os.makedirs(directory, exist_ok=True)
    temporary_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(filename)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = handle.name
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        with open(temporary_path, "r", encoding="utf-8") as verify_handle:
            verified = json.load(verify_handle)
        if not isinstance(verified, dict):
            raise ValueError("Yazılan sonuç dosyasının kökü sözlük değil.")

        os.replace(temporary_path, filename)
        temporary_path = None
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.remove(temporary_path)


def directional_return_percent(direction: str, reference: float, price: float) -> float:
    if reference <= 0:
        return 0.0
    if str(direction).upper() == "SHORT":
        return round((reference - price) / reference * 100.0, 4)
    return round((price - reference) / reference * 100.0, 4)


def candle_excursions_percent(
    direction: str,
    reference: float,
    high: float,
    low: float,
) -> Tuple[float, float]:
    if reference <= 0:
        return 0.0, 0.0
    if str(direction).upper() == "SHORT":
        favorable = (reference - low) / reference * 100.0
        adverse = (high - reference) / reference * 100.0
    else:
        favorable = (high - reference) / reference * 100.0
        adverse = (reference - low) / reference * 100.0
    return round(max(0.0, favorable), 4), round(max(0.0, adverse), 4)


def first_threshold_event(
    candles: Sequence[Sequence[float]],
    direction: str,
    reference: float,
    threshold_percent: float,
) -> Dict[str, Any]:
    for candle in candles:
        timestamp = int(candle[0])
        high = float(candle[2])
        low = float(candle[3])
        favorable, adverse = candle_excursions_percent(direction, reference, high, low)
        favorable_hit = favorable >= threshold_percent
        adverse_hit = adverse >= threshold_percent
        if favorable_hit and adverse_hit:
            return {"event": "AMBIGUOUS_SAME_CANDLE", "at_ms": timestamp}
        if favorable_hit:
            return {"event": "FAVORABLE_FIRST", "at_ms": timestamp}
        if adverse_hit:
            return {"event": "ADVERSE_FIRST", "at_ms": timestamp}
    return {"event": "NONE", "at_ms": 0}


def select_reference_candle(
    candles: Sequence[Sequence[float]],
    recorded_at_ms: int,
) -> Optional[Sequence[float]]:
    closed_before = [
        candle for candle in candles
        if int(candle[0]) + CANDLE_MS <= recorded_at_ms
    ]
    if closed_before:
        return closed_before[-1]
    return candles[0] if candles else None


def analyze_window(
    candles: Sequence[Sequence[float]],
    direction: str,
    reference_price: float,
) -> Dict[str, Any]:
    if not candles:
        return {}

    max_favorable = 0.0
    max_adverse = 0.0
    for candle in candles:
        favorable, adverse = candle_excursions_percent(
            direction,
            reference_price,
            float(candle[2]),
            float(candle[3]),
        )
        max_favorable = max(max_favorable, favorable)
        max_adverse = max(max_adverse, adverse)

    final_close = float(candles[-1][4])
    result: Dict[str, Any] = {
        "checked_candle_ms": int(candles[-1][0]),
        "close_price": final_close,
        "directional_return_percent": directional_return_percent(
            direction, reference_price, final_close
        ),
        "max_favorable_percent": round(max_favorable, 4),
        "max_adverse_percent": round(max_adverse, 4),
    }
    for threshold in THRESHOLDS_PERCENT:
        key = str(threshold).replace(".", "_")
        result[f"first_{key}_percent"] = first_threshold_event(
            candles, direction, reference_price, threshold
        )
    return result


def analyze_record_from_candles(
    source_record: Dict[str, Any],
    candles: Sequence[Sequence[float]],
    current_ts: int,
) -> Dict[str, Any]:
    recorded_at = int(safe_float(source_record.get("recorded_at"), 0) or 0)
    recorded_at_ms = recorded_at * 1000
    direction = str(source_record.get("direction") or "").upper()
    reference_candle = select_reference_candle(candles, recorded_at_ms)

    base = {
        "identity": source_record.get("identity"),
        "recorded_at": recorded_at,
        "decision": source_record.get("decision"),
        "would_block": bool(source_record.get("would_block")),
        "block_code": source_record.get("block_code"),
        "block_reason": source_record.get("block_reason"),
        "bot": source_record.get("bot"),
        "symbol": normalize_symbol(source_record.get("symbol")),
        "direction": direction,
        "open_signal_count": source_record.get("open_signal_count"),
        "total_risk_before": source_record.get("total_risk_before"),
        "direction_risk_before": source_record.get("direction_risk_before"),
        "data_status": "PENDING",
        "reference_price": None,
        "reference_candle_ms": 0,
        "checkpoints": {},
        "latest_analysis": {},
        "completed": False,
        "last_checked_at": current_ts,
    }

    if not reference_candle:
        base["data_status"] = "NO_REFERENCE_CANDLE"
        return base

    reference_price = float(reference_candle[4])
    reference_candle_ms = int(reference_candle[0])
    future_candles = [
        candle for candle in candles
        if int(candle[0]) > reference_candle_ms
        and int(candle[0]) + CANDLE_MS <= current_ts * 1000
    ]
    base["reference_price"] = reference_price
    base["reference_candle_ms"] = reference_candle_ms

    if not future_candles:
        base["data_status"] = "WAITING_FOR_CLOSED_CANDLE"
        return base

    available_minutes = max(0, int((current_ts - recorded_at) / 60))
    for minutes in CHECKPOINT_MINUTES:
        if available_minutes < minutes:
            continue
        cutoff_ms = recorded_at_ms + minutes * 60 * 1000
        window = [candle for candle in future_candles if int(candle[0]) < cutoff_ms]
        if window:
            base["checkpoints"][str(minutes)] = analyze_window(
                window, direction, reference_price
            )

    base["latest_analysis"] = analyze_window(
        future_candles, direction, reference_price
    )
    base["available_minutes"] = available_minutes
    base["completed"] = available_minutes >= max(CHECKPOINT_MINUTES)
    base["data_status"] = "COMPLETE" if base["completed"] else "TRACKING"
    return base


def build_market_map(exchange: Any) -> Dict[str, str]:
    markets = exchange.load_markets()
    mapping: Dict[str, str] = {}
    for market in markets.values():
        if not isinstance(market, dict):
            continue
        if not market.get("swap") or str(market.get("quote") or "").upper() != "USDT":
            continue
        key = normalize_symbol(f"{market.get('base', '')}{market.get('quote', '')}")
        if key:
            mapping[key] = str(market.get("symbol"))
    return mapping


def fetch_candles_for_record(
    exchange: Any,
    market_symbol: str,
    recorded_at: int,
    current_ts: int,
) -> List[List[float]]:
    start_ms = max(0, recorded_at * 1000 - 2 * CANDLE_MS)
    end_ms = min(current_ts * 1000, recorded_at * 1000 + 1450 * 60 * 1000)
    candles: List[List[float]] = []
    since = start_ms

    while since < end_ms and len(candles) < 320:
        batch = exchange.fetch_ohlcv(market_symbol, TIMEFRAME, since=since, limit=100)
        if not batch:
            break
        for candle in batch:
            if int(candle[0]) > end_ms:
                break
            if not candles or int(candle[0]) > int(candles[-1][0]):
                candles.append(candle)
        next_since = int(batch[-1][0]) + CANDLE_MS
        if next_since <= since:
            break
        since = next_since
        if int(batch[-1][0]) >= end_ms:
            break

    return candles


def average(values: Iterable[Any]) -> Optional[float]:
    valid = [safe_float(value, None) for value in values]
    valid = [value for value in valid if value is not None]
    if not valid:
        return None
    return round(sum(valid) / len(valid), 4)


def summarize_records(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    record_list = [record for record in records if isinstance(record, dict)]
    summary: Dict[str, Any] = {
        "tracked_records": len(record_list),
        "tracking_records": sum(1 for r in record_list if r.get("data_status") == "TRACKING"),
        "completed_records": sum(1 for r in record_list if r.get("completed")),
        "data_error_records": sum(
            1 for r in record_list
            if r.get("data_status") in {"NO_REFERENCE_CANDLE", "MARKET_NOT_FOUND", "FETCH_ERROR"}
        ),
        "by_decision": {},
        "by_block_code": {},
    }

    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    block_groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in record_list:
        groups[str(record.get("decision") or "UNKNOWN")].append(record)
        block_groups[str(record.get("block_code") or "ALLOW")].append(record)

    def group_stats(items: List[Dict[str, Any]]) -> Dict[str, Any]:
        stats: Dict[str, Any] = {"records": len(items)}
        for checkpoint in CHECKPOINT_MINUTES:
            key = str(checkpoint)
            checkpoints = [
                item.get("checkpoints", {}).get(key)
                for item in items
                if isinstance(item.get("checkpoints", {}).get(key), dict)
            ]
            if not checkpoints:
                continue
            stats[f"records_{key}m"] = len(checkpoints)
            stats[f"avg_return_{key}m_percent"] = average(
                item.get("directional_return_percent") for item in checkpoints
            )
            stats[f"avg_mfe_{key}m_percent"] = average(
                item.get("max_favorable_percent") for item in checkpoints
            )
            stats[f"avg_mae_{key}m_percent"] = average(
                item.get("max_adverse_percent") for item in checkpoints
            )
        latest = [item.get("latest_analysis") or {} for item in items]
        for threshold in THRESHOLDS_PERCENT:
            threshold_key = str(threshold).replace(".", "_")
            event_key = f"first_{threshold_key}_percent"
            events = [
                analysis.get(event_key, {}).get("event")
                for analysis in latest
                if isinstance(analysis.get(event_key), dict)
            ]
            stats[f"first_{threshold_key}_favorable"] = events.count("FAVORABLE_FIRST")
            stats[f"first_{threshold_key}_adverse"] = events.count("ADVERSE_FIRST")
            stats[f"first_{threshold_key}_ambiguous"] = events.count("AMBIGUOUS_SAME_CANDLE")
            stats[f"first_{threshold_key}_none"] = events.count("NONE")
        return stats

    summary["by_decision"] = {
        name: group_stats(items) for name, items in sorted(groups.items())
    }
    summary["by_block_code"] = {
        name: group_stats(items) for name, items in sorted(block_groups.items())
    }
    return summary


def run_tracker(
    source_file: str = SOURCE_FILE,
    outcome_file: str = OUTCOME_FILE,
    exchange: Any = None,
    current_ts: Optional[int] = None,
) -> Dict[str, Any]:
    current_ts = int(current_ts or now_ts())
    source = load_json(source_file)
    source_records = source.get("records")
    if not isinstance(source_records, list):
        source_records = []
    source_records = source_records[-MAX_SOURCE_RECORDS:]

    previous = load_json(outcome_file)
    previous_records = previous.get("records")
    if not isinstance(previous_records, dict):
        previous_records = {}

    if exchange is None:
        import ccxt
        exchange = ccxt.okx({"enableRateLimit": True})
    market_map = build_market_map(exchange)
    output_records: Dict[str, Dict[str, Any]] = {}

    for source_record in source_records:
        if not isinstance(source_record, dict):
            continue
        identity = str(source_record.get("identity") or "")
        symbol = normalize_symbol(source_record.get("symbol"))
        direction = str(source_record.get("direction") or "").upper()
        recorded_at = int(safe_float(source_record.get("recorded_at"), 0) or 0)
        if not identity or not symbol or direction not in {"LONG", "SHORT"} or recorded_at <= 0:
            continue

        existing = previous_records.get(identity)
        if isinstance(existing, dict) and existing.get("completed"):
            output_records[identity] = existing
            continue

        market_symbol = market_map.get(symbol)
        if not market_symbol:
            output_records[identity] = {
                **source_record,
                "symbol": symbol,
                "data_status": "MARKET_NOT_FOUND",
                "completed": False,
                "last_checked_at": current_ts,
            }
            continue

        try:
            candles = fetch_candles_for_record(
                exchange, market_symbol, recorded_at, current_ts
            )
            output_records[identity] = analyze_record_from_candles(
                source_record, candles, current_ts
            )
        except Exception as exc:
            print(f"{symbol} sonuç takip hatası: {exc}")
            fallback = dict(existing) if isinstance(existing, dict) else dict(source_record)
            fallback.update({
                "symbol": symbol,
                "data_status": "FETCH_ERROR",
                "fetch_error": str(exc)[:300],
                "completed": False,
                "last_checked_at": current_ts,
            })
            output_records[identity] = fallback

    if len(output_records) > MAX_OUTCOME_RECORDS:
        ordered = sorted(
            output_records.values(),
            key=lambda item: int(safe_float(item.get("recorded_at"), 0) or 0),
        )[-MAX_OUTCOME_RECORDS:]
        output_records = {str(item.get("identity")): item for item in ordered}

    result = {
        "version": "PORTFOLIO_RISK_OUTCOME_SHADOW_V1_2026_08_04",
        "mode": "SHADOW_ANALYSIS_ONLY_NO_SIGNAL_CHANGE_NO_ORDERS",
        "source_file": source_file,
        "timeframe": TIMEFRAME,
        "checkpoints_minutes": list(CHECKPOINT_MINUTES),
        "thresholds_percent": list(THRESHOLDS_PERCENT),
        "records": output_records,
        "summary": {
            "source_records": len(source_records),
            **summarize_records(output_records.values()),
        },
        "last_update": current_ts,
    }
    save_json_atomically(outcome_file, result)
    return result


def main() -> None:
    result = run_tracker()
    summary = result.get("summary", {})
    print(
        "Portfolio Risk Outcome Shadow |",
        f"kaynak={summary.get('source_records', 0)}",
        f"takip={summary.get('tracked_records', 0)}",
        f"tamamlanan={summary.get('completed_records', 0)}",
        f"hata={summary.get('data_error_records', 0)}",
    )


if __name__ == "__main__":
    main()
