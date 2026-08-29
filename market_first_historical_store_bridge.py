"""Worktree bridge for the historical Market First research workflow.

The replay script uses the existing market_first_ml load/save helpers. During a
research run we temporarily merge the separate historical seed file into the
local live-store copy, then split replay rows back out and restore the live file
byte-for-byte. Nothing here runs in the live Market First workflow.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from typing import Any, Dict, Mapping

LIVE_FILE = "market_first_ml_samples.json"
HIST_FILE = "market_first_historical_ml_samples.json"
BACKUP_FILE = ".market_first_ml_samples.live_backup.json"
ABSENT_MARKER = ".market_first_ml_samples.was_absent"
HIST_PREFIX = "MARKET_FIRST_HISTORICAL_ML_SEED"


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {"samples": {}}
    except Exception:
        return {"samples": {}}


def _atomic_write(path: str, data: Mapping[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(path)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(dict(data), handle, ensure_ascii=False, indent=2, sort_keys=True)
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


def prepare() -> Dict[str, int]:
    if os.path.exists(BACKUP_FILE):
        os.remove(BACKUP_FILE)
    if os.path.exists(ABSENT_MARKER):
        os.remove(ABSENT_MARKER)

    if os.path.exists(LIVE_FILE):
        shutil.copy2(LIVE_FILE, BACKUP_FILE)
    else:
        with open(ABSENT_MARKER, "w", encoding="utf-8") as handle:
            handle.write("1\n")

    live = _load(LIVE_FILE)
    history = _load(HIST_FILE)
    live_samples = live.setdefault("samples", {})
    if not isinstance(live_samples, dict):
        live_samples = {}
        live["samples"] = live_samples
    history_samples = history.get("samples", {}) if isinstance(history, Mapping) else {}

    added = 0
    if isinstance(history_samples, Mapping):
        for key, sample in history_samples.items():
            if not isinstance(sample, Mapping):
                continue
            if sample.get("label") not in (0, 1) or not bool(sample.get("resolved")):
                continue
            sample_key = str(key or sample.get("trade_id") or "").strip()
            if not sample_key or sample_key in live_samples:
                continue
            live_samples[sample_key] = dict(sample)
            added += 1

    _atomic_write(LIVE_FILE, live)
    return {"historical_merged_into_local_worktree": added}


def finalize() -> Dict[str, int]:
    combined = _load(LIVE_FILE)
    existing_history = _load(HIST_FILE)
    history_samples = existing_history.setdefault("samples", {})
    if not isinstance(history_samples, dict):
        history_samples = {}
        existing_history["samples"] = history_samples

    extracted = 0
    combined_samples = combined.get("samples", {}) if isinstance(combined, Mapping) else {}
    if isinstance(combined_samples, Mapping):
        for key, sample in combined_samples.items():
            if not isinstance(sample, Mapping):
                continue
            origin = str(sample.get("sample_origin") or "")
            if not origin.startswith(HIST_PREFIX):
                continue
            sample_key = str(key or sample.get("trade_id") or "").strip()
            if not sample_key:
                continue
            history_samples[sample_key] = dict(sample)
            extracted += 1

    existing_history["version"] = "MARKET_FIRST_HISTORICAL_TRAINING_POOL_V1"
    existing_history["sample_count"] = len(history_samples)
    _atomic_write(HIST_FILE, existing_history)

    if os.path.exists(BACKUP_FILE):
        shutil.copy2(BACKUP_FILE, LIVE_FILE)
        os.remove(BACKUP_FILE)
    elif os.path.exists(ABSENT_MARKER):
        if os.path.exists(LIVE_FILE):
            os.remove(LIVE_FILE)
        os.remove(ABSENT_MARKER)

    return {
        "historical_rows_extracted": extracted,
        "historical_pool_size": len(history_samples),
    }


def main() -> None:
    action = str(sys.argv[1] if len(sys.argv) > 1 else "").lower().strip()
    if action == "prepare":
        print(prepare())
        return
    if action == "finalize":
        print(finalize())
        return
    raise SystemExit("usage: python market_first_historical_store_bridge.py [prepare|finalize]")


if __name__ == "__main__":
    main()
