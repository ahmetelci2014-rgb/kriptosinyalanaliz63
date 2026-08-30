"""Build one auditable in-memory ML quality pool for Market First.

Three sources may contribute resolved labels without being mixed on disk:
- real Market First trades,
- no-lookahead historical replay samples,
- resolved EARLY alert episodes.

EARLY episodes are converted only from the snapshot captured at alert time. Their
future path is used only for the label, so no future information leaks into the
feature vector. Legacy adopted alerts that do not contain a full momentum/
structure snapshot are deliberately skipped.
"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any, Dict, Mapping, Optional

from market_first_ml import MODEL_VERSION, extract_features

HISTORICAL_SAMPLE_FILE = "market_first_historical_ml_samples.json"
EARLY_LEDGER_FILE = "market_first_early_ledger.json"

EARLY_REQUIRED_INITIAL_FIELDS = (
    "score",
    "move_3m_percent",
    "move_5m_percent",
    "volume_ratio_1m",
    "structure_5m",
    "structure_15m",
    "structure_1h",
)


def _load_json(filename: str, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else copy.deepcopy(default)
    except Exception:
        return copy.deepcopy(default)


def load_historical_store(filename: str = HISTORICAL_SAMPLE_FILE) -> Dict[str, Any]:
    data = _load_json(filename, {"samples": {}})
    if not isinstance(data.get("samples"), dict):
        data["samples"] = {}
    return data


def load_early_ledger(filename: str = EARLY_LEDGER_FILE) -> Dict[str, Any]:
    data = _load_json(filename, {"episodes": {}})
    if not isinstance(data.get("episodes"), dict):
        data["episodes"] = {}
    return data


def _early_label(episode: Mapping[str, Any]) -> Optional[int]:
    if not bool(episode.get("resolved")):
        return None
    explicit = episode.get("quality_label")
    if explicit in (0, 1):
        return int(explicit)
    outcome = str(episode.get("outcome") or "").upper()
    if outcome in {"GOOD_MOVE", "STRONG_MOVE"}:
        return 1
    # For opportunity learning, a resolved episode that never became a clean
    # useful move is a negative example. BAD_MOVE and MIXED are both useful here.
    if outcome in {"BAD_MOVE", "MIXED"}:
        return 0
    return None


def _early_sample(episode: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    label = _early_label(episode)
    initial = episode.get("initial")
    if label not in (0, 1) or not isinstance(initial, Mapping):
        return None
    if any(field not in initial for field in EARLY_REQUIRED_INITIAL_FIELDS):
        return None

    symbol = str(episode.get("symbol") or "").upper().strip()
    direction = str(episode.get("direction") or "").upper().strip()
    first_at = int(episode.get("first_at") or 0)
    alert_price = float(episode.get("alert_price") or 0.0)
    if not symbol or direction not in {"LONG", "SHORT"} or first_at <= 0 or alert_price <= 0:
        return None

    decision = dict(initial)
    decision.update({
        "symbol": symbol,
        "direction": direction,
        "stage": "EARLY",
        "current_price": alert_price,
    })

    # Preserve explicit availability flags from the live snapshot. For older
    # snapshots that did not store flags, infer availability only from the
    # presence of the corresponding observation.
    if "derivatives_available" not in decision:
        decision["derivatives_available"] = any(
            key in initial
            for key in ("oi_change_15m_percent", "funding_rate_8h_bps", "taker_imbalance_alignment")
        )
    if "oi_history_available" not in decision:
        decision["oi_history_available"] = "oi_change_15m_percent" in initial
    if "funding_available" not in decision:
        decision["funding_available"] = "funding_rate_8h_bps" in initial
    if "funding_crowding_8h_bps" not in decision and "funding_rate_8h_bps" in initial:
        decision["funding_crowding_8h_bps"] = initial.get("funding_rate_8h_bps")
    if "taker_available" not in decision:
        decision["taker_available"] = "taker_imbalance_alignment" in initial

    context = SimpleNamespace(
        regime=str(initial.get("market_regime") or "CHOP"),
        score=float(initial.get("market_score") or 0.0),
        strength=float(initial.get("market_strength") or abs(float(initial.get("market_score") or 0.0))),
        breadth_5m=float(initial.get("market_breadth_5m") if initial.get("market_breadth_5m") is not None else 0.50),
        breadth_24h=float(initial.get("market_breadth_24h") if initial.get("market_breadth_24h") is not None else 0.50),
        major_move_5m_percent=float(initial.get("major_move_5m_percent") or 0.0),
    )
    features = extract_features(decision, context)
    eid = str(episode.get("episode_id") or f"{symbol}:{direction}:{first_at}")
    trade_id = f"EARLY:{eid}"
    return {
        "trade_id": trade_id,
        "symbol": symbol,
        "direction": direction,
        "source": "MARKET_FIRST_EARLY_LEDGER",
        "sample_origin": "MARKET_FIRST_EARLY_LEDGER_V2_ML_READY",
        "opened_at": first_at,
        "entry": alert_price,
        "features": features,
        "model_version_at_open": MODEL_VERSION,
        "model_mode_at_open": "EARLY_OUTCOME_SEED",
        "model_probability_at_open": None,
        "label": label,
        "resolved": True,
        "resolved_at": int(episode.get("closed_at") or episode.get("updated_at") or first_at),
        "resolved_result": str(episode.get("outcome") or "EARLY_RESOLVED"),
        "label_target": "EARLY_CLEAN_FAVORABLE_MOVE",
        "best_favorable_percent": float(episode.get("best_favorable_percent") or 0.0),
        "worst_adverse_percent": float(episode.get("worst_adverse_percent") or 0.0),
        "final_directional_percent": float(episode.get("final_directional_percent") or 0.0),
        "ignored_reason": None,
    }


def combine_training_store(
    live_store: Mapping[str, Any],
    historical_store: Mapping[str, Any] | None = None,
    early_ledger: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Merge resolved historical and EARLY labels into a copy of live samples."""
    combined: Dict[str, Any] = copy.deepcopy(dict(live_store or {}))
    combined_samples = combined.setdefault("samples", {})
    if not isinstance(combined_samples, dict):
        combined_samples = {}
        combined["samples"] = combined_samples

    history = historical_store if historical_store is not None else load_historical_store()
    history_samples = history.get("samples", {}) if isinstance(history, Mapping) else {}
    historical_added = 0
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
            historical_added += 1

    ledger = early_ledger if early_ledger is not None else load_early_ledger()
    episodes = ledger.get("episodes", {}) if isinstance(ledger, Mapping) else {}
    early_added = 0
    early_skipped_incomplete = 0
    if isinstance(episodes, Mapping):
        for episode in episodes.values():
            if not isinstance(episode, Mapping):
                continue
            sample = _early_sample(episode)
            if sample is None:
                if _early_label(episode) in (0, 1):
                    early_skipped_incomplete += 1
                continue
            sample_key = str(sample.get("trade_id") or "").strip()
            if not sample_key or sample_key in combined_samples:
                continue
            combined_samples[sample_key] = sample
            early_added += 1

    combined["historical_seed_rows_added"] = historical_added
    combined["early_episode_rows_added"] = early_added
    combined["early_episode_rows_skipped_incomplete"] = early_skipped_incomplete
    return combined
