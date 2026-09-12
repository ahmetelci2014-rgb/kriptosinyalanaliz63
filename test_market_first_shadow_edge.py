from market_first_shadow_edge import build_profile


def _good_payloads():
    opportunity = {
        "entry_plan_clean": {
            "tp1_first": 1031,
            "sl_first": 487,
            "entry_condition_met": 445,
            "entry_signal_sent": 17,
        }
    }
    daily = {
        "date": "2026-09-11",
        "summary": {
            "background_tp_first": 185,
            "background_sl_first": 29,
        },
    }
    swing = {
        "v2_tp1_first": 172,
        "v2_sl_first": 109,
    }
    direction = {
        "reversal_tp1_first": 46,
        "reversal_sl_first": 76,
    }
    return opportunity, daily, swing, direction


def test_background_edge_enables_only_with_strong_evidence_and_conversion_gap():
    profile = build_profile(*_good_payloads())
    assert profile["relaxation_enabled"] is True
    assert profile["entry_plan_tp_first_rate"] > 0.65
    assert profile["recent_background_tp_first_rate"] > 0.80
    assert profile["swing_v2_tp_first_rate"] > 0.60
    assert profile["real_signal_conversion_rate"] < 0.05


def test_background_edge_disables_when_recent_edge_deteriorates():
    opportunity, daily, swing, direction = _good_payloads()
    daily["summary"] = {
        "background_tp_first": 25,
        "background_sl_first": 35,
    }
    profile = build_profile(opportunity, daily, swing, direction)
    assert profile["recent_edge"] is False
    assert profile["relaxation_enabled"] is False


def test_background_edge_disables_after_conversion_recovers():
    opportunity, daily, swing, direction = _good_payloads()
    opportunity["entry_plan_clean"]["entry_signal_sent"] = 100
    profile = build_profile(opportunity, daily, swing, direction)
    assert profile["real_signal_conversion_rate"] > 0.15
    assert profile["conversion_gap"] is False
    assert profile["relaxation_enabled"] is False


def test_reversal_background_is_flagged_when_first_touch_is_negative():
    profile = build_profile(*_good_payloads())
    assert profile["reversal_samples"] == 122
    assert profile["reversal_tp_first_rate"] < 0.40
    assert profile["reversal_negative"] is True
