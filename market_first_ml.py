"""Embedded tree-model quality layer for Market First V5.

This is not a second trading system. Market First remains the hard decision
engine. The model learns only from completed Market First trades and may affect
live selection only after chronological out-of-sample validation passes.

V3 adds derivatives/order-flow observations (OI, normalized funding, taker
imbalance, CVD impulse and near-price order-book context). Old samples remain
valid: missing newer fields are represented by explicit availability flags plus
zero values, so the model cannot mistake missing historical data for a
confirmed derivatives/order-flow signal.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import tempfile
import time
from typing import Any, Dict, Mapping, Optional

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import balanced_accuracy_score, roc_auc_score


MODEL_VERSION = "MARKET_FIRST_RF_QUALITY_V3_ORDERFLOW_2026_08_28"
SAMPLE_FILE = "market_first_ml_samples.json"

MIN_LABELED_SAMPLES = 120
MIN_CLASS_SAMPLES = 25
VALIDATION_FRACTION = 0.25
MIN_VALIDATION_SAMPLES = 24
MIN_VALIDATION_CLASS_SAMPLES = 5
MIN_ROC_AUC = 0.58
MIN_BALANCED_ACCURACY = 0.55
LIVE_BLOCK_PROBABILITY = 0.42
MAX_SAMPLES = 3000

REGIME_VALUE = {
    "SHOCK_DOWN": -3.0,
    "BEAR_STRONG": -2.0,
    "BEAR": -1.0,
    "CHOP": 0.0,
    "BULL": 1.0,
    "BULL_STRONG": 2.0,
    "SHOCK_UP": 3.0,
}

FEATURE_NAMES = (
    "market_regime_alignment",
    "market_score_alignment",
    "market_strength",
    "breadth_5m_alignment",
    "breadth_24h_alignment",
    "major_move_5m_alignment",
    "rule_score",
    "move_1m_alignment",
    "move_3m_alignment",
    "move_5m_alignment",
    "volume_ratio_1m",
    "breakout_20m",
    "relative_strength_5m",
    "extension_atr_5m",
    "structure_5m_alignment",
    "structure_15m_alignment",
    "structure_1h_alignment",
    "independent_move",
    "log10_quote_volume_24h",
    "risk_percent",
    "room_r_capped",
    # Derivatives/order-flow. Availability flags are essential because older
    # samples do not contain all of these observations.
    "derivatives_available",
    "oi_history_available",
    "oi_change_5m_percent",
    "oi_change_15m_percent",
    "funding_available",
    "funding_crowding_8h_bps",
    "taker_available",
    "taker_imbalance_alignment",
    "cvd_available",
    "cvd_impulse_alignment",
    "book_available",
    "book_imbalance_alignment",
    "book_opposing_wall_ratio",
    "derivatives_soft_score",
)


@dataclass
class QualityBundle:
    mode: str
    model: Any = None
    labeled_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    validation_count: int = 0
    metrics: Optional[Dict[str, float]] = None
    feature_importance: Optional[Dict[str, float]] = None
    reason: str = ""


def _sf(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except Exception:
        return default


def _direction_sign(direction: Any) -> float:
    return 1.0 if str(direction or "").upper() == "LONG" else -1.0


def _aligned_structure(value: Any, direction: Any) -> float:
    text = str(value or "").upper()
    wanted = str(direction or "").upper()
    if text == wanted:
        return 1.0
    if text == "NEUTRAL" or not text:
        return 0.0
    return -1.0


def extract_features(decision: Mapping[str, Any], context: Any) -> Dict[str, float]:
    """Use only information available at candidate-decision time."""
    direction = str(decision.get("direction") or "SHORT").upper()
    sign = _direction_sign(direction)
    regime = str(decision.get("market_regime") or getattr(context, "regime", "CHOP"))
    breadth_5m = _sf(
        decision.get("market_breadth_5m"),
        _sf(getattr(context, "breadth_5m", 0.50), 0.50),
    )
    breadth_24h = _sf(getattr(context, "breadth_24h", 0.50), 0.50)
    market_score = _sf(
        decision.get("market_score"),
        _sf(getattr(context, "score", 0.0)),
    )
    market_strength = _sf(
        decision.get("market_strength"),
        _sf(getattr(context, "strength", abs(market_score))),
    )
    major_move = _sf(
        decision.get("major_move_5m_percent"),
        _sf(getattr(context, "major_move_5m_percent", 0.0)),
    )
    quote_volume = max(0.0, _sf(decision.get("quote_volume_24h")))
    room_r = _sf(decision.get("room_r"), 0.0)

    features = {
        "market_regime_alignment": REGIME_VALUE.get(regime, 0.0) * sign,
        "market_score_alignment": market_score * sign,
        "market_strength": market_strength,
        "breadth_5m_alignment": (breadth_5m - 0.50) * 2.0 * sign,
        "breadth_24h_alignment": (breadth_24h - 0.50) * 2.0 * sign,
        "major_move_5m_alignment": major_move * sign,
        "rule_score": _sf(decision.get("score")),
        "move_1m_alignment": _sf(decision.get("move_1m_percent")) * sign,
        "move_3m_alignment": _sf(decision.get("move_3m_percent")) * sign,
        "move_5m_alignment": _sf(decision.get("move_5m_percent")) * sign,
        "volume_ratio_1m": _sf(decision.get("volume_ratio_1m")),
        "breakout_20m": 1.0 if decision.get("breakout_20m") else 0.0,
        "relative_strength_5m": _sf(decision.get("relative_strength_5m")),
        "extension_atr_5m": _sf(decision.get("extension_atr_5m")),
        "structure_5m_alignment": _aligned_structure(decision.get("structure_5m"), direction),
        "structure_15m_alignment": _aligned_structure(decision.get("structure_15m"), direction),
        "structure_1h_alignment": _aligned_structure(decision.get("structure_1h"), direction),
        "independent_move": 1.0 if decision.get("independent_move") else 0.0,
        "log10_quote_volume_24h": math.log10(max(1.0, quote_volume)),
        "risk_percent": _sf(decision.get("risk_percent")),
        "room_r_capped": min(10.0, max(0.0, room_r)),
        "derivatives_available": 1.0 if decision.get("derivatives_available") else 0.0,
        "oi_history_available": 1.0 if decision.get("oi_history_available") else 0.0,
        "oi_change_5m_percent": _sf(decision.get("oi_change_5m_percent")),
        "oi_change_15m_percent": _sf(decision.get("oi_change_15m_percent")),
        "funding_available": 1.0 if decision.get("funding_available") else 0.0,
        # Positive = funding crowding is in candidate direction; the tree learns
        # whether that is supportive or dangerous in a given regime.
        "funding_crowding_8h_bps": _sf(decision.get("funding_crowding_8h_bps")),
        "taker_available": 1.0 if decision.get("taker_available") else 0.0,
        # Positive = aggressive recent flow is aligned with candidate direction.
        "taker_imbalance_alignment": _sf(decision.get("taker_imbalance_alignment")),
        "cvd_available": 1.0 if decision.get("cvd_available") else 0.0,
        # Positive = aggressive pressure strengthened in candidate direction
        # during the latter half of the recent-trade window.
        "cvd_impulse_alignment": _sf(decision.get("cvd_impulse_alignment")),
        "book_available": 1.0 if decision.get("book_available") else 0.0,
        # Positive = visible near-price depth favors candidate direction.
        "book_imbalance_alignment": _sf(decision.get("book_imbalance_alignment")),
        # High means a concentrated visible wall sits on the opposing side.
        "book_opposing_wall_ratio": min(
            20.0,
            max(0.0, _sf(decision.get("book_opposing_wall_ratio"))),
        ),
        "derivatives_soft_score": _sf(decision.get("derivatives_soft_score")),
    }
    return {name: round(_sf(features.get(name)), 8) for name in FEATURE_NAMES}


def empty_store() -> Dict[str, Any]:
    return {"version": MODEL_VERSION, "samples": {}, "last_update": 0}


def load_store(filename: str = SAMPLE_FILE) -> Dict[str, Any]:
    try:
        with open(filename, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            data = empty_store()
    except Exception:
        data = empty_store()
    data.setdefault("version", MODEL_VERSION)
    data.setdefault("samples", {})
    data.setdefault("last_update", 0)
    if not isinstance(data.get("samples"), dict):
        data["samples"] = {}
    return data


def save_store(store: Dict[str, Any], filename: str = SAMPLE_FILE) -> bool:
    store["version"] = MODEL_VERSION
    store["last_update"] = int(time.time())
    _prune_store(store)
    absolute = os.path.abspath(filename)
    directory = os.path.dirname(absolute) or "."
    os.makedirs(directory, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=f".{os.path.basename(filename)}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(store, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, absolute)
        temp_path = None
        return True
    except Exception as exc:
        print("Market First ML sample kaydetme hatası:", exc)
        return False
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except Exception:
                pass


def _prune_store(store: Dict[str, Any]) -> None:
    samples = store.setdefault("samples", {})
    if len(samples) <= MAX_SAMPLES:
        return
    unresolved = [
        (key, item)
        for key, item in samples.items()
        if not bool((item or {}).get("resolved"))
    ]
    resolved = sorted(
        [
            (key, item)
            for key, item in samples.items()
            if bool((item or {}).get("resolved"))
        ],
        key=lambda pair: int((pair[1] or {}).get("opened_at") or 0),
        reverse=True,
    )
    keep_resolved = max(0, MAX_SAMPLES - len(unresolved))
    store["samples"] = dict(unresolved + resolved[:keep_resolved])


def register_trade_sample(
    store: Dict[str, Any],
    trade_id: str,
    signal: Mapping[str, Any],
    features: Mapping[str, Any],
    probability: Optional[float],
    mode: str,
    opened_at: Optional[int] = None,
) -> bool:
    trade_id = str(trade_id or "").strip()
    if not trade_id:
        return False
    samples = store.setdefault("samples", {})
    if trade_id in samples:
        return False
    normalized = {name: _sf(features.get(name)) for name in FEATURE_NAMES}
    samples[trade_id] = {
        "trade_id": trade_id,
        "symbol": str(signal.get("symbol") or ""),
        "direction": str(signal.get("direction") or ""),
        "source": str(signal.get("source") or "MARKET_FIRST_V5"),
        "opened_at": int(opened_at or time.time()),
        "entry": _sf(signal.get("entry")),
        "features": normalized,
        "model_version_at_open": MODEL_VERSION,
        "model_mode_at_open": str(mode or "COLLECTING"),
        "model_probability_at_open": round(float(probability), 6) if probability is not None else None,
        "label": None,
        "resolved": False,
        "ignored_reason": None,
    }
    return True


def reconcile_samples(store: Dict[str, Any], ledger: Mapping[str, Any]) -> int:
    """Target: TP1 reached before a clean pre-TP1 stop."""
    trades = ledger.get("trades", {}) if isinstance(ledger, Mapping) else {}
    if not isinstance(trades, Mapping):
        return 0
    changed = 0
    positive_results = {"TP3", "TP1_SONRASI_BE", "TP2_SONRASI_BE"}
    for trade_id, sample in store.setdefault("samples", {}).items():
        if not isinstance(sample, dict) or sample.get("resolved"):
            continue
        trade = trades.get(trade_id)
        if not isinstance(trade, Mapping):
            continue
        result = str(trade.get("final_result") or "").upper()
        if not result:
            continue
        tp1_hit = bool(trade.get("tp1_hit"))
        if tp1_hit or result in positive_results:
            sample["label"] = 1
            sample["resolved"] = True
        elif result == "SL":
            sample["label"] = 0
            sample["resolved"] = True
        else:
            sample["label"] = None
            sample["resolved"] = True
            sample["ignored_reason"] = "AMBIGUOUS_OR_EXPIRED_BEFORE_TP1"
        sample["resolved_result"] = result
        sample["resolved_at"] = int(trade.get("closed_at") or time.time())
        changed += 1
    return changed


def _labeled_samples(store: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    samples = store.get("samples", {}) if isinstance(store, Mapping) else {}
    rows = [
        item
        for item in samples.values()
        if isinstance(item, Mapping) and item.get("label") in (0, 1)
    ] if isinstance(samples, Mapping) else []
    return sorted(rows, key=lambda item: int(item.get("opened_at") or 0))


def _vector(features: Mapping[str, Any]) -> list[float]:
    return [_sf(features.get(name)) for name in FEATURE_NAMES]


def _new_model() -> RandomForestClassifier:
    return RandomForestClassifier(
        n_estimators=320,
        max_depth=6,
        min_samples_leaf=6,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=1,
    )


def _importance(model: Any) -> Dict[str, float]:
    values = getattr(model, "feature_importances_", None)
    if values is None:
        return {}
    pairs = sorted(
        ((name, float(value)) for name, value in zip(FEATURE_NAMES, values)),
        key=lambda pair: pair[1],
        reverse=True,
    )
    return {name: round(value, 6) for name, value in pairs}


def train_quality_model(store: Mapping[str, Any]) -> QualityBundle:
    rows = _labeled_samples(store)
    labels = [int(item["label"]) for item in rows]
    positive = sum(labels)
    negative = len(labels) - positive

    if len(rows) < MIN_LABELED_SAMPLES or positive < MIN_CLASS_SAMPLES or negative < MIN_CLASS_SAMPLES:
        return QualityBundle(
            mode="COLLECTING",
            labeled_count=len(rows),
            positive_count=positive,
            negative_count=negative,
            reason="Yeterli dengeli Market First sonucu henüz birikmedi.",
        )

    validation_count = max(MIN_VALIDATION_SAMPLES, int(round(len(rows) * VALIDATION_FRACTION)))
    validation_count = min(validation_count, max(1, len(rows) - 40))
    split = len(rows) - validation_count
    train_rows = rows[:split]
    validation_rows = rows[split:]
    train_y = [int(item["label"]) for item in train_rows]
    val_y = [int(item["label"]) for item in validation_rows]

    if (
        len(set(train_y)) < 2
        or len(set(val_y)) < 2
        or val_y.count(1) < MIN_VALIDATION_CLASS_SAMPLES
        or val_y.count(0) < MIN_VALIDATION_CLASS_SAMPLES
    ):
        model = _new_model()
        model.fit([_vector(item.get("features", {})) for item in rows], labels)
        return QualityBundle(
            mode="SHADOW",
            model=model,
            labeled_count=len(rows),
            positive_count=positive,
            negative_count=negative,
            validation_count=len(validation_rows),
            reason="Kronolojik doğrulama penceresinde iki sınıf yeterince dengeli değil.",
            feature_importance=_importance(model),
        )

    eval_model = _new_model()
    eval_model.fit([_vector(item.get("features", {})) for item in train_rows], train_y)
    probabilities = eval_model.predict_proba(
        [_vector(item.get("features", {})) for item in validation_rows]
    )[:, 1]
    predictions = [1 if value >= 0.50 else 0 for value in probabilities]
    auc = float(roc_auc_score(val_y, probabilities))
    balanced = float(balanced_accuracy_score(val_y, predictions))
    metrics = {
        "roc_auc": round(auc, 4),
        "balanced_accuracy": round(balanced, 4),
    }
    mode = "ACTIVE" if auc >= MIN_ROC_AUC and balanced >= MIN_BALANCED_ACCURACY else "SHADOW"

    production = _new_model()
    production.fit([_vector(item.get("features", {})) for item in rows], labels)
    return QualityBundle(
        mode=mode,
        model=production,
        labeled_count=len(rows),
        positive_count=positive,
        negative_count=negative,
        validation_count=len(validation_rows),
        metrics=metrics,
        feature_importance=_importance(production),
        reason=(
            "Kronolojik doğrulama eşiği geçti."
            if mode == "ACTIVE"
            else "Model eğitildi fakat kronolojik doğrulama henüz canlı etki için yeterli değil."
        ),
    )


def score_features(features: Mapping[str, Any], bundle: QualityBundle) -> Optional[float]:
    if bundle.model is None:
        return None
    try:
        probability = float(bundle.model.predict_proba([_vector(features)])[0][1])
        return round(max(0.0, min(1.0, probability)), 6)
    except Exception as exc:
        print("Market First ML skorlama hatası:", exc)
        return None


def should_block_live(probability: Optional[float], bundle: QualityBundle) -> bool:
    return bool(
        bundle.mode == "ACTIVE"
        and probability is not None
        and probability < LIVE_BLOCK_PROBABILITY
    )


def bundle_summary(bundle: QualityBundle) -> Dict[str, Any]:
    importance = bundle.feature_importance or {}
    return {
        "version": MODEL_VERSION,
        "mode": bundle.mode,
        "labeled_count": bundle.labeled_count,
        "positive_count": bundle.positive_count,
        "negative_count": bundle.negative_count,
        "validation_count": bundle.validation_count,
        "metrics": bundle.metrics or {},
        "live_block_probability": LIVE_BLOCK_PROBABILITY,
        "top_feature_importance": dict(list(importance.items())[:8]),
        "reason": bundle.reason,
    }
