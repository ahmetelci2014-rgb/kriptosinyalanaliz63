from market_first_ml_training_pool import combine_training_store


def _sample(label, resolved=True, opened_at=1):
    return {
        "label": label,
        "resolved": resolved,
        "opened_at": opened_at,
        "features": {},
    }


def _early_episode(
    *,
    eid="XUSDT:LONG:100",
    direction="LONG",
    outcome="GOOD_MOVE",
    label=1,
    complete=True,
):
    initial = {
        "score": 68,
        "market_regime": "CHOP",
        "market_score": 5.0,
        "market_strength": 5.0,
        "market_breadth_5m": 0.52,
        "market_breadth_24h": 0.55,
        "major_move_5m_percent": 0.05,
        "move_1m_percent": 0.30 if direction == "LONG" else -0.30,
        "move_3m_percent": 0.70 if direction == "LONG" else -0.70,
        "move_5m_percent": 1.10 if direction == "LONG" else -1.10,
        "volume_ratio_1m": 1.25,
        "breakout_20m": True,
        "relative_strength_5m": 0.8,
        "extension_atr_5m": 0.7,
        "structure_5m": direction,
        "structure_15m": direction,
        "structure_1h": "NEUTRAL",
        "quote_volume_24h": 2_000_000,
    }
    if not complete:
        initial.pop("move_3m_percent")
    return {
        "episode_id": eid,
        "symbol": "XUSDT",
        "direction": direction,
        "first_at": 100,
        "alert_price": 10.0,
        "initial": initial,
        "resolved": True,
        "quality_label": label,
        "outcome": outcome,
        "closed_at": 200,
        "best_favorable_percent": 2.0 if label == 1 else 0.3,
        "worst_adverse_percent": 0.2 if label == 1 else 1.0,
        "final_directional_percent": 1.0 if label == 1 else -0.5,
    }


def test_combines_only_resolved_labeled_history_without_mutating_live():
    live = {"samples": {"live-1": _sample(1, True, 10)}}
    history = {
        "samples": {
            "hist-good": _sample(0, True, 5),
            "hist-unresolved": _sample(1, False, 6),
            "hist-ambiguous": _sample(None, True, 7),
        }
    }

    combined = combine_training_store(live, history, {"episodes": {}})

    assert set(combined["samples"]) == {"live-1", "hist-good"}
    assert combined["historical_seed_rows_added"] == 1
    assert combined["early_episode_rows_added"] == 0
    assert set(live["samples"]) == {"live-1"}


def test_live_sample_wins_on_duplicate_id():
    live = {"samples": {"same": _sample(1, True, 10)}}
    history = {"samples": {"same": _sample(0, True, 5)}}

    combined = combine_training_store(live, history, {"episodes": {}})

    assert combined["samples"]["same"]["label"] == 1
    assert combined["historical_seed_rows_added"] == 0


def test_resolved_ml_ready_early_episode_is_added_with_direction_normalized_features():
    early = _early_episode(direction="SHORT", eid="XUSDT:SHORT:100")
    combined = combine_training_store(
        {"samples": {}},
        {"samples": {}},
        {"episodes": {early["episode_id"]: early}},
    )

    assert combined["early_episode_rows_added"] == 1
    row = combined["samples"]["EARLY:XUSDT:SHORT:100"]
    assert row["label"] == 1
    assert row["source"] == "MARKET_FIRST_EARLY_LEDGER"
    # SHORT's negative raw move is favorable, so the model sees positive alignment.
    assert row["features"]["move_5m_alignment"] > 0


def test_mixed_episode_becomes_negative_but_legacy_incomplete_snapshot_is_skipped():
    mixed = _early_episode(
        eid="XUSDT:LONG:101",
        outcome="MIXED",
        label=None,
    )
    incomplete = _early_episode(eid="XUSDT:LONG:102", complete=False)
    ledger = {
        "episodes": {
            mixed["episode_id"]: mixed,
            incomplete["episode_id"]: incomplete,
        }
    }

    combined = combine_training_store({"samples": {}}, {"samples": {}}, ledger)

    assert combined["early_episode_rows_added"] == 1
    assert combined["early_episode_rows_skipped_incomplete"] == 1
    assert combined["samples"]["EARLY:XUSDT:LONG:101"]["label"] == 0
