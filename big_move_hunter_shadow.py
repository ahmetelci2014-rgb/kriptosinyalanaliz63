"""Big Move Hunter shadow metrics.

This module does not send Telegram messages, does not place orders, and does not
change live entry/exit eligibility. It converts the existing multi-day 4H trend
shadow into the user's core research metrics:

- how early the movement was detected versus the estimated 4H trend origin,
- how much of the currently observed trend was still available after detection,
- whether the 4H trend is still strong enough to justify runner research,
- whether the observed move has reached 5/10/20/40 percent classes.

The goal is to learn which profiles resemble GRASS/SPK-style large multi-day
moves before changing live rules.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, Optional

VERSION = "BIG_MOVE_HUNTER_SHADOW_V1_2026_08_24"
MODE = "SHADOW_ONLY_NO_TELEGRAM_NO_ORDERS_NO_LIVE_RULE_MUTATION"
INPUT_FILE = "multi_day_trend_shadow.json"
STATE_FILE = "big_move_hunter_shadow.json"


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


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    folder = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(folder, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=folder,
            prefix=".big_move_hunter.",
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


def directional_percent(direction: str, start: float, end: float) -> float:
    if start <= 0:
        return 0.0
    raw = (end - start) / start * 100.0
    return raw if str(direction or "").upper() == "LONG" else -raw


def capture_stage(delay_percent: float) -> str:
    value = max(0.0, float(delay_percent or 0.0))
    if value <= 2.0:
        return "COK_ERKEN"
    if value <= 5.0:
        return "ERKEN"
    if value <= 10.0:
        return "ORTA"
    if value <= 20.0:
        return "GEC"
    return "COK_GEC"


def move_class(move_percent: float) -> str:
    value = max(0.0, float(move_percent or 0.0))
    if value >= 40.0:
        return "OLAGANUSTU_40P"
    if value >= 20.0:
        return "BUYUK_20P"
    if value >= 10.0:
        return "GUCLU_10P"
    if value >= 5.0:
        return "ANLAMLI_5P"
    return "HENUZ_BUYUK_DEGIL"


def available_share_percent(
    direction: str,
    origin_price: float,
    detection_price: float,
    current_price: float,
) -> float:
    direction = str(direction or "").upper()
    if min(origin_price, detection_price, current_price) <= 0:
        return 0.0
    if direction == "LONG":
        total = current_price - origin_price
        after_detection = current_price - detection_price
    else:
        total = origin_price - current_price
        after_detection = detection_price - current_price
    if total <= 0:
        return 0.0
    return max(0.0, min(100.0, after_detection / total * 100.0))


def hunter_score(
    delay_percent: float,
    trend_status: str,
    base_score: int,
) -> int:
    delay = max(0.0, float(delay_percent or 0.0))
    if delay <= 2:
        early_points = 50
    elif delay <= 5:
        early_points = 42
    elif delay <= 10:
        early_points = 30
    elif delay <= 20:
        early_points = 15
    else:
        early_points = 5

    status = str(trend_status or "").upper()
    trend_points = {
        "4H_TREND_BASLANGIC": 30,
        "4H_DEVAM_GUCLU": 30,
        "4H_DEVAM": 22,
        "4H_ZAYIFLIYOR": 10,
        "4H_BITIS_RISKI": 0,
    }.get(status, 5)

    base = max(0, min(100, int(base_score or 0)))
    base_points = max(0, min(20, int(round((base - 70) / 30 * 20))))
    return max(0, min(100, early_points + trend_points + base_points))


def research_label(score: int, stage: str, trend_status: str) -> str:
    status = str(trend_status or "").upper()
    if (
        score >= 85
        and stage in {"COK_ERKEN", "ERKEN"}
        and status in {"4H_TREND_BASLANGIC", "4H_DEVAM_GUCLU", "4H_DEVAM"}
    ):
        return "BUYUK_HAREKET_ADAYI_GUCLU"
    if score >= 70 and status not in {"4H_BITIS_RISKI"}:
        return "BUYUK_HAREKET_ADAYI_IZLE"
    return "NORMAL_GOLGE"


def evaluate_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(record, dict):
        return None
    symbol = str(record.get("symbol") or "").upper()
    direction = str(record.get("direction") or "").upper()
    origin = record.get("trend_origin") if isinstance(record.get("trend_origin"), dict) else {}
    origin_price = _sf(origin.get("price"), 0.0) or 0.0
    detection_price = _sf(record.get("movement_entry"), 0.0) or 0.0
    current_price = _sf(record.get("current_price"), 0.0) or 0.0
    if not symbol or direction not in {"LONG", "SHORT"}:
        return None
    if min(origin_price, detection_price, current_price) <= 0:
        return None

    delay = max(0.0, directional_percent(direction, origin_price, detection_price))
    total_move = max(0.0, directional_percent(direction, origin_price, current_price))
    after_detection = max(0.0, directional_percent(direction, detection_price, current_price))
    share = available_share_percent(direction, origin_price, detection_price, current_price)
    stage = capture_stage(delay)
    status = str(record.get("status") or "")
    base_score = int(record.get("best_base_score") or record.get("initial_base_score") or 0)
    score = hunter_score(delay, status, base_score)

    return {
        "symbol": symbol,
        "direction": direction,
        "hunter_score": score,
        "research_label": research_label(score, stage, status),
        "capture_stage": stage,
        "detection_delay_from_4h_origin_percent": round(delay, 4),
        "observed_move_from_4h_origin_percent": round(total_move, 4),
        "move_after_detection_percent": round(after_detection, 4),
        "available_share_of_observed_trend_percent": round(share, 2),
        "move_class": move_class(total_move),
        "trend_status": status,
        "trend_score": record.get("score"),
        "trend_confidence": record.get("confidence"),
        "movement_stage": record.get("best_stage") or record.get("initial_stage"),
        "movement_base_score": base_score,
        "origin_price": round(origin_price, 12),
        "detection_price": round(detection_price, 12),
        "current_price": round(current_price, 12),
        "origin_at": origin.get("at"),
        "origin_method": origin.get("method"),
        "trend_life_hours": origin.get("life_hours"),
        "shadow_only": True,
    }


def build_snapshot(
    input_file: str = INPUT_FILE,
    state_file: str = STATE_FILE,
    *,
    now_ts: Optional[int] = None,
) -> Dict[str, Any]:
    now = int(now_ts if now_ts is not None else time.time())
    source = _load(input_file)
    rows = source.get("records") if isinstance(source.get("records"), dict) else {}

    records: Dict[str, Any] = {}
    capture_counter: Counter[str] = Counter()
    move_counter: Counter[str] = Counter()
    label_counter: Counter[str] = Counter()

    for key, record in rows.items():
        result = evaluate_record(record)
        if result is None:
            continue
        result["updated_at"] = now
        records[str(key)] = result
        capture_counter[result["capture_stage"]] += 1
        move_counter[result["move_class"]] += 1
        label_counter[result["research_label"]] += 1

    ranked = sorted(
        records.values(),
        key=lambda row: (
            int(row.get("hunter_score") or 0),
            float(row.get("available_share_of_observed_trend_percent") or 0.0),
        ),
        reverse=True,
    )

    state = {
        "version": VERSION,
        "mode": MODE,
        "updated_at": now,
        "records": records,
        "summary": {
            "tracked": len(records),
            "capture_stages": dict(capture_counter),
            "move_classes": dict(move_counter),
            "research_labels": dict(label_counter),
            "top_candidates": [
                {
                    "symbol": row.get("symbol"),
                    "direction": row.get("direction"),
                    "hunter_score": row.get("hunter_score"),
                    "capture_stage": row.get("capture_stage"),
                    "delay_percent": row.get("detection_delay_from_4h_origin_percent"),
                    "move_class": row.get("move_class"),
                    "trend_status": row.get("trend_status"),
                }
                for row in ranked[:15]
            ],
        },
    }
    _atomic_save(state_file, state)
    return state["summary"]


def main() -> None:
    summary = build_snapshot()
    print("BIG MOVE HUNTER SHADOW:", summary)


if __name__ == "__main__":
    main()
