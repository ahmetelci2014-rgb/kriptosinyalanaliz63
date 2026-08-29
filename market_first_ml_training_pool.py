"""Build an in-memory ML training pool without mixing live state files.

Historical replay samples are stored separately from live Market First trade
samples. The live runner combines them only in memory when fitting/validating the
Random Forest. This prevents GitHub Actions state-write conflicts and keeps the
origin of every training example auditable.
"""
from __future__ import annotations

import copy
import json
from typing import Any, Dict, Mapping

HISTORICAL_SAMPLE_FILE = "market_first_historical_ml_samples.json"


def load_historical_store(filename: str = HISTORICAL_SAMPLE_FILE) -> Dict[str, Any]:
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return {"samples": {}}
    except Exception:
        return {"samples": {}}
    samples = data.get("samples")
    if not isinstance(samples, dict):
        data["samples"] = {}
    return data


def combine_training_store(
    live_store: Mapping[str, Any],
    historical_store: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Merge only resolved labelled historical rows into a copy of live store."""
    combined: Dict[str, Any] = copy.deepcopy(dict(live_store or {}))
    combined_samples = combined.setdefault("samples", {})
    if not isinstance(combined_samples, dict):
        combined_samples = {}
        combined["samples"] = combined_samples

    history = historical_store if historical_store is not None else load_historical_store()
    history_samples = history.get("samples", {}) if isinstance(history, Mapping) else {}
    added = 0
    if isinstance(history_samples, Mapping):
        for key, sample in history_samples.items():
            if not isinstance(sample, Mapping):
                continue
            if sample.get("label") not in (0, 1) or not bool(sample.get("resolved")):
                continue
            sample_key = str(key or sample.get("trade_id") or "").strip()
            if not sample_key or sample_key in combined_samples:
                continue
            combined_samples[sample_key] = copy.deepcopy(dict(sample))
            added += 1

    combined["historical_seed_rows_added"] = added
    return combined
