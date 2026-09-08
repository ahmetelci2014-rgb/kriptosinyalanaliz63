"""Observe Market First trigger cadence without changing scheduling or trading.

The live workflow is externally dispatched. This module records the interval
between successful workflow starts so scheduler drift (for example an intended
5-minute trigger actually arriving every 15 minutes) becomes visible in repo
state. It never sends Telegram messages, changes strategy thresholds or opens
exchange orders.
"""
from __future__ import annotations

import json
import math
import os
import statistics
import time
from typing import Any, Mapping

VERSION = "MARKET_FIRST_SCHEDULER_HEALTH_V1_2026_09_08"
STATE_FILE = "market_first_scheduler_health.json"
EXPECTED_SECONDS = 300
LATE_AFTER_SECONDS = 480
SEVERE_AFTER_SECONDS = 900
MAX_INTERVALS = 48


def _si(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _save(path: str, payload: Mapping[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(dict(payload), handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def _finite_intervals(values: Any) -> list[int]:
    if not isinstance(values, list):
        return []
    result: list[int] = []
    for value in values:
        seconds = _si(value)
        if 0 < seconds <= 86400 and math.isfinite(float(seconds)):
            result.append(seconds)
    return result[-MAX_INTERVALS:]


def classify_interval(seconds: int | None) -> str:
    if seconds is None or seconds <= 0:
        return "BASELINE"
    if seconds <= LATE_AFTER_SECONDS:
        return "HEALTHY"
    if seconds < SEVERE_AFTER_SECONDS:
        return "DELAYED"
    return "SEVERELY_DELAYED"


def build_payload(
    previous: Mapping[str, Any] | None,
    now: int,
    *,
    run_id: str = "",
    run_number: str = "",
    event_name: str = "",
    sha: str = "",
) -> dict[str, Any]:
    previous = previous if isinstance(previous, Mapping) else {}
    last_at = _si(previous.get("last_run_at"))
    interval = now - last_at if last_at > 0 and now > last_at else None

    intervals = _finite_intervals(previous.get("intervals_seconds"))
    if interval is not None:
        intervals.append(int(interval))
        intervals = intervals[-MAX_INTERVALS:]

    median_seconds = round(float(statistics.median(intervals)), 1) if intervals else None
    average_seconds = round(sum(intervals) / len(intervals), 1) if intervals else None
    delayed_count = sum(1 for value in intervals if value > LATE_AFTER_SECONDS)

    return {
        "version": VERSION,
        "expected_seconds": EXPECTED_SECONDS,
        "late_after_seconds": LATE_AFTER_SECONDS,
        "status": classify_interval(interval),
        "last_run_at": int(now),
        "previous_run_at": last_at or None,
        "last_interval_seconds": int(interval) if interval is not None else None,
        "last_interval_minutes": round(interval / 60.0, 2) if interval is not None else None,
        "intervals_seconds": intervals,
        "observed_count": len(intervals),
        "observed_median_seconds": median_seconds,
        "observed_average_seconds": average_seconds,
        "delayed_count": delayed_count,
        "delayed_rate": round(delayed_count / len(intervals), 4) if intervals else 0.0,
        "run": {
            "id": str(run_id or ""),
            "number": str(run_number or ""),
            "event": str(event_name or ""),
            "sha": str(sha or ""),
        },
        "note": (
            "Observational only. A delayed cadence does not change live strategy; "
            "it flags that the external trigger is arriving slower than the intended 5-minute cadence."
        ),
    }


def run(path: str = STATE_FILE, now: int | None = None, env: Mapping[str, str] | None = None) -> dict[str, Any]:
    environment = env if isinstance(env, Mapping) else os.environ
    timestamp = int(now if now is not None else time.time())
    payload = build_payload(
        _load(path),
        timestamp,
        run_id=str(environment.get("GITHUB_RUN_ID", "")),
        run_number=str(environment.get("GITHUB_RUN_NUMBER", "")),
        event_name=str(environment.get("GITHUB_EVENT_NAME", "")),
        sha=str(environment.get("GITHUB_SHA", "")),
    )
    _save(path, payload)
    print(
        "SCHEDULER HEALTH | status=", payload.get("status"),
        "| interval_min=", payload.get("last_interval_minutes"),
        "| median_sec=", payload.get("observed_median_seconds"),
        "| expected_sec=", EXPECTED_SECONDS,
    )
    return payload


if __name__ == "__main__":
    run()
