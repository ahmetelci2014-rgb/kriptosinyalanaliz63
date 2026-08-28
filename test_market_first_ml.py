from types import SimpleNamespace

from market_first_ml import (
    FEATURE_NAMES,
    MODEL_VERSION,
    empty_store,
    extract_features,
    reconcile_samples,
    score_features,
    should_block_live,
    train_quality_model,
)


def _feature_row(label: int) -> dict[str, float]:
    base = {name: 0.0 for name in FEATURE_NAMES}
    if label:
        base.update(
            {
                "market_regime_alignment": 2.0,
                "market_score_alignment": 45.0,
                "rule_score": 92.0,
                "move_3m_alignment": 1.25,
                "move_5m_alignment": 1.75,
                "volume_ratio_1m": 2.2,
                "breakout_20m": 1.0,
                "relative_strength_5m": 1.1,
                "extension_atr_5m": 0.75,
                "structure_5m_alignment": 1.0,
                "structure_15m_alignment": 1.0,
                "structure_1h_alignment": 1.0,
                "risk_percent": 0.8,
                "room_r_capped": 3.0,
            }
        )
    else:
        base.update(
            {
                "market_regime_alignment": -1.0,
                "market_score_alignment": -20.0,
                "rule_score": 79.0,
                "move_3m_alignment": 0.65,
                "move_5m_alignment": 0.85,
                "volume_ratio_1m": 0.8,
                "breakout_20m": 0.0,
                "relative_strength_5m": 0.1,
                "extension_atr_5m": 1.45,
                "structure_5m_alignment": 0.0,
                "structure_15m_alignment": -1.0,
                "structure_1h_alignment": -1.0,
                "risk_percent": 1.4,
                "room_r_capped": 1.7,
            }
        )
    return base


def test_extract_features_aligns_short_with_down_market():
    context = SimpleNamespace(
        regime="BEAR_STRONG",
        breadth_5m=0.35,
        breadth_24h=0.40,
        score=-55.0,
        strength=55.0,
        major_move_5m_percent=-0.8,
    )
    decision = {
        "direction": "SHORT",
        "market_regime": "BEAR_STRONG",
        "score": 88,
        "move_1m_percent": -0.4,
        "move_3m_percent": -0.9,
        "move_5m_percent": -1.3,
        "volume_ratio_1m": 1.8,
        "breakout_20m": True,
        "relative_strength_5m": 0.5,
        "extension_atr_5m": 0.9,
        "structure_5m": "SHORT",
        "structure_15m": "SHORT",
        "structure_1h": "SHORT",
        "quote_volume_24h": 20_000_000,
        "risk_percent": 0.9,
        "room_r": 2.5,
    }
    features = extract_features(decision, context)
    assert tuple(features.keys()) == FEATURE_NAMES
    assert features["market_regime_alignment"] > 0
    assert features["market_score_alignment"] > 0
    assert features["major_move_5m_alignment"] > 0
    assert features["move_5m_alignment"] > 0
    assert features["structure_1h_alignment"] == 1.0


def test_small_dataset_stays_collecting():
    store = empty_store()
    for i in range(40):
        label = i % 2
        store["samples"][f"t{i}"] = {
            "opened_at": i,
            "label": label,
            "resolved": True,
            "features": _feature_row(label),
        }
    bundle = train_quality_model(store)
    assert bundle.mode == "COLLECTING"
    assert bundle.model is None


def test_strong_chronological_signal_can_activate():
    store = empty_store()
    # Alternating classes across time keeps both train and future holdout balanced.
    for i in range(160):
        label = i % 2
        store["samples"][f"t{i}"] = {
            "opened_at": 1_700_000_000 + i * 60,
            "label": label,
            "resolved": True,
            "features": _feature_row(label),
        }
    bundle = train_quality_model(store)
    assert bundle.mode == "ACTIVE"
    assert bundle.validation_count >= 24
    assert bundle.metrics["roc_auc"] >= 0.58
    assert bundle.metrics["balanced_accuracy"] >= 0.55
    good_probability = score_features(_feature_row(1), bundle)
    bad_probability = score_features(_feature_row(0), bundle)
    assert good_probability is not None and bad_probability is not None
    assert good_probability > bad_probability
    assert not should_block_live(good_probability, bundle)
    assert should_block_live(bad_probability, bundle)


def test_reconcile_uses_tp1_success_and_clean_sl_failure():
    store = empty_store()
    for trade_id in ("good", "bad", "expired"):
        store["samples"][trade_id] = {
            "trade_id": trade_id,
            "opened_at": 100,
            "label": None,
            "resolved": False,
            "features": _feature_row(1),
        }
    ledger = {
        "trades": {
            "good": {"final_result": "TP1_SONRASI_BE", "tp1_hit": True, "closed_at": 200},
            "bad": {"final_result": "SL", "tp1_hit": False, "closed_at": 210},
            "expired": {"final_result": "EXPIRED", "tp1_hit": False, "closed_at": 220},
        }
    }
    changed = reconcile_samples(store, ledger)
    assert changed == 3
    assert store["samples"]["good"]["label"] == 1
    assert store["samples"]["bad"]["label"] == 0
    assert store["samples"]["expired"]["label"] is None
    assert store["samples"]["expired"]["resolved"] is True
    assert store["samples"]["expired"]["ignored_reason"]
    assert store["version"] == MODEL_VERSION
