"""Compact Movement Start shadow state files without touching live trading logic.

Open and recent records stay full fidelity. Older closed records are converted to
small per-trade rows, while bounded rollups preserve counts after the compact
archive itself reaches its cap. V3 keeps only recent full order-flow snapshots
and stores older snapshots as lightweight rows.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Tuple

VERSION = "SHADOW_STATE_COMPACTION_V1_2026_08_23"

PROFILES = {
    "v1": {
        "path": "movement_start_shadow.json",
        "recent_closed": 120,
        "archive_limit": 6000,
    },
    "v2": {
        "path": "movement_start_v2_shadow.json",
        "recent_closed": 200,
        "archive_limit": 6000,
    },
    "v3": {
        "path": "movement_start_v3_orderflow_shadow.json",
        "recent_closed": 200,
        "archive_limit": 6000,
        "recent_snapshots": 300,
        "snapshot_archive_limit": 5000,
    },
}

_RECORD_FIELDS = {
    "v1": (
        "id", "symbol", "direction", "stage", "max_stage", "score",
        "opposite_score", "created_at", "last_seen_at", "entry",
        "max_favorable_percent", "max_adverse_percent", "success_reached_at",
        "fail_reached_at", "outcome", "closed_at", "duration_minutes",
    ),
    "v2": (
        "id", "symbol", "direction", "initial_stage", "best_stage",
        "initial_score", "best_score", "opposite_score", "started_at",
        "entry", "stop", "risk_percent", "max_favorable_r",
        "max_adverse_r", "hit_2r_at", "hit_3r_at", "hit_5r_at",
        "stop_hit_at", "first_resolution", "first_resolution_at",
        "closed_at", "status", "highest_r_hit",
    ),
    "v3": (
        "id", "symbol", "direction", "started_at", "base_stage",
        "base_score", "entry", "stop", "risk_percent",
        "initial_orderflow_score", "best_orderflow_score",
        "latest_orderflow_score", "initial_orderflow_confirmed",
        "latest_orderflow_confirmed", "first_confirmed_at",
        "first_confirmed_minutes", "orderflow_checks", "max_favorable_r",
        "max_adverse_r", "hit_2r_at", "hit_3r_at", "hit_5r_at",
        "stop_hit_at", "first_resolution", "first_resolution_at",
        "closed_at", "status", "highest_r_hit",
    ),
}


def _atomic_save(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=".shadow_compact.", suffix=".tmp", delete=False,
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


def _timestamp(kind: str, row: Dict[str, Any]) -> int:
    key = "created_at" if kind == "v1" else "started_at"
    try:
        return int(row.get(key) or row.get("closed_at") or 0)
    except Exception:
        return 0


def _compact_record(kind: str, row: Dict[str, Any]) -> Dict[str, Any]:
    return {key: row.get(key) for key in _RECORD_FIELDS[kind] if key in row}


def _snapshot_compact(row: Dict[str, Any]) -> Dict[str, Any]:
    flow = row.get("flow") if isinstance(row.get("flow"), dict) else {}
    compact = {
        key: row.get(key)
        for key in (
            "symbol", "direction", "at", "base_stage", "base_score",
            "orderflow_score", "orderflow_confirmed", "pressure_delta",
        )
        if key in row
    }
    for key in (
        "book_imbalance", "top_imbalance", "spread_bps", "trade_imbalance",
        "recent_trade_imbalance", "buy_ratio", "buy_count_ratio",
        "trades_count", "trades_per_second",
    ):
        if key in flow:
            compact[key] = flow.get(key)
    return compact


def _row_id(row: Dict[str, Any], fallback_prefix: str) -> str:
    rid = row.get("id")
    if rid:
        return str(rid)
    return "|".join(
        str(row.get(key) or "")
        for key in (fallback_prefix, "symbol", "direction", "at", "started_at", "created_at")
    )


def _rollup(existing: Dict[str, Any], rows: Iterable[Dict[str, Any]], kind: str) -> Dict[str, Any]:
    result = dict(existing or {})
    outcomes = Counter(result.get("outcomes") or {})
    directions = Counter(result.get("directions") or {})
    stages = Counter(result.get("stages") or {})
    count = int(result.get("count") or 0)
    for row in rows:
        count += 1
        direction = str(row.get("direction") or "UNKNOWN")
        stage = str(
            row.get("max_stage") or row.get("best_stage") or row.get("base_stage")
            or row.get("stage") or row.get("initial_stage") or "UNKNOWN"
        )
        outcome = str(
            row.get("outcome") or row.get("status") or row.get("first_resolution")
            or "UNKNOWN"
        )
        directions[direction] += 1
        stages[stage] += 1
        outcomes[outcome] += 1
    result.update({
        "count": count,
        "outcomes": dict(outcomes),
        "directions": dict(directions),
        "stages": dict(stages),
        "kind": kind,
    })
    return result


def _bounded_merge(
    existing: Iterable[Dict[str, Any]],
    additions: Iterable[Dict[str, Any]],
    limit: int,
    id_prefix: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for row in list(existing) + list(additions):
        if isinstance(row, dict):
            merged[_row_id(row, id_prefix)] = row
    rows = list(merged.values())
    rows.sort(key=lambda row: int(row.get("created_at") or row.get("started_at") or row.get("at") or 0))
    if len(rows) <= limit:
        return rows, []
    return rows[-limit:], rows[:-limit]


def compact_state(
    data: Dict[str, Any],
    kind: str,
    *,
    recent_closed: int,
    archive_limit: int,
    recent_snapshots: int = 0,
    snapshot_archive_limit: int = 0,
    now_ts: int | None = None,
) -> Dict[str, Any]:
    if kind not in _RECORD_FIELDS:
        raise ValueError(f"unknown shadow kind: {kind}")

    records = [row for row in data.get("records", []) if isinstance(row, dict)]
    open_map = data.get("open") if isinstance(data.get("open"), dict) else {}

    if kind == "v1":
        open_ids = {str(value) for value in open_map.values() if not isinstance(value, dict)}
        active = [row for row in records if str(row.get("id")) in open_ids]
        closed = [row for row in records if str(row.get("id")) not in open_ids]
    else:
        active = []
        closed = records

    closed.sort(key=lambda row: _timestamp(kind, row))
    keep_closed = closed[-max(0, recent_closed):] if recent_closed else []
    archive_new = [_compact_record(kind, row) for row in closed[: max(0, len(closed) - len(keep_closed))]]

    archive, overflow = _bounded_merge(
        data.get("archive_records", []), archive_new, archive_limit, f"record:{kind}"
    )
    data["records"] = active + keep_closed
    data["archive_records"] = archive
    if overflow:
        data["archive_rollup"] = _rollup(data.get("archive_rollup", {}), overflow, kind)

    snapshot_overflow: List[Dict[str, Any]] = []
    if kind == "v3":
        snapshots = [row for row in data.get("snapshots", []) if isinstance(row, dict)]
        snapshots.sort(key=lambda row: int(row.get("at") or 0))
        keep = snapshots[-max(0, recent_snapshots):] if recent_snapshots else []
        old = snapshots[: max(0, len(snapshots) - len(keep))]
        compact_old = [_snapshot_compact(row) for row in old]
        archived_snapshots, snapshot_overflow = _bounded_merge(
            data.get("archive_snapshots", []), compact_old,
            snapshot_archive_limit, "snapshot:v3"
        )
        data["snapshots"] = keep
        data["archive_snapshots"] = archived_snapshots
        if snapshot_overflow:
            prior = dict(data.get("snapshot_rollup") or {})
            prior["count"] = int(prior.get("count") or 0) + len(snapshot_overflow)
            prior["confirmed"] = int(prior.get("confirmed") or 0) + sum(
                1 for row in snapshot_overflow if row.get("orderflow_confirmed")
            )
            data["snapshot_rollup"] = prior

    data["compaction"] = {
        "version": VERSION,
        "compacted_at": int(now_ts if now_ts is not None else time.time()),
        "kind": kind,
        "full_records": len(data.get("records", [])),
        "open_records": len(open_map),
        "archive_records": len(data.get("archive_records", [])),
        "rolled_up_records": int((data.get("archive_rollup") or {}).get("count") or 0),
        "full_snapshots": len(data.get("snapshots", [])) if kind == "v3" else 0,
        "archive_snapshots": len(data.get("archive_snapshots", [])) if kind == "v3" else 0,
        "rolled_up_snapshots": int((data.get("snapshot_rollup") or {}).get("count") or 0),
    }
    return data


def compact_file(kind: str, config: Dict[str, Any]) -> Dict[str, Any]:
    path = str(config["path"])
    if not os.path.exists(path):
        return {"kind": kind, "path": path, "status": "missing"}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        return {"kind": kind, "path": path, "status": "invalid", "error": str(exc)}
    if not isinstance(data, dict):
        return {"kind": kind, "path": path, "status": "invalid"}

    compact_state(
        data,
        kind,
        recent_closed=int(config["recent_closed"]),
        archive_limit=int(config["archive_limit"]),
        recent_snapshots=int(config.get("recent_snapshots") or 0),
        snapshot_archive_limit=int(config.get("snapshot_archive_limit") or 0),
    )
    _atomic_save(path, data)
    result = dict(data.get("compaction") or {})
    result.update({"path": path, "status": "ok"})
    return result


def main() -> None:
    for kind, config in PROFILES.items():
        print("SHADOW COMPACTION:", compact_file(kind, config))


if __name__ == "__main__":
    main()
