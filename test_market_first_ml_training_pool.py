from market_first_ml_training_pool import combine_training_store


def _sample(label, resolved=True, opened_at=1):
    return {
        "label": label,
        "resolved": resolved,
        "opened_at": opened_at,
        "features": {},
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

    combined = combine_training_store(live, history)

    assert set(combined["samples"]) == {"live-1", "hist-good"}
    assert combined["historical_seed_rows_added"] == 1
    assert set(live["samples"]) == {"live-1"}


def test_live_sample_wins_on_duplicate_id():
    live = {"samples": {"same": _sample(1, True, 10)}}
    history = {"samples": {"same": _sample(0, True, 5)}}

    combined = combine_training_store(live, history)

    assert combined["samples"]["same"]["label"] == 1
    assert combined["historical_seed_rows_added"] == 0
