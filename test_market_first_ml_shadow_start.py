from market_first_ml import FEATURE_NAMES, should_block_live, train_quality_model


def _row(label, opened_at):
    return {
        "label": label,
        "resolved": True,
        "opened_at": opened_at,
        "features": {name: 0.0 for name in FEATURE_NAMES},
    }


def test_small_balanced_pool_starts_shadow_but_never_blocks_live():
    store = {
        "samples": {
            "a": _row(1, 1),
            "b": _row(0, 2),
            "c": _row(1, 3),
            "d": _row(0, 4),
            "e": _row(1, 5),
        }
    }

    bundle = train_quality_model(store)

    assert bundle.mode == "SHADOW"
    assert bundle.model is not None
    assert bundle.labeled_count == 5
    assert bundle.positive_count == 3
    assert bundle.negative_count == 2
    assert should_block_live(0.0, bundle) is False


def test_unbalanced_tiny_pool_stays_collecting():
    store = {
        "samples": {
            "a": _row(1, 1),
            "b": _row(1, 2),
            "c": _row(1, 3),
            "d": _row(0, 4),
            "e": _row(1, 5),
        }
    }

    bundle = train_quality_model(store)

    assert bundle.mode == "COLLECTING"
    assert bundle.model is None
