import market_first_historical_replay_v3 as v3


def test_research_risk_keeps_low_room_for_learning():
    plan, reason = v3.research_risk_from_structures(
        "LONG",
        100.0,
        {
            "atr": 1.0,
            "swing_low_12": 99.0,
        },
        {
            "range_high_72": 101.0,
        },
    )
    assert reason == "OK"
    assert plan is not None
    assert plan["risk_percent"] == 1.1
    assert plan["room_r"] < 1.60
    assert plan["tp1"] > 100.0


def test_research_risk_rejects_wide_stop():
    plan, reason = v3.research_risk_from_structures(
        "SHORT",
        100.0,
        {
            "atr": 1.0,
            "swing_high_12": 103.0,
        },
        {
            "range_low_72": 90.0,
        },
    )
    assert plan is None
    assert reason == "RISK_WIDE"


def test_research_risk_enforces_live_minimum_stop_width():
    plan, reason = v3.research_risk_from_structures(
        "SHORT",
        100.0,
        {
            "atr": 0.1,
            "swing_high_12": 100.05,
        },
        {
            "range_low_72": 98.0,
        },
    )
    assert reason == "OK"
    assert plan is not None
    assert plan["risk_percent"] == 0.4
    assert plan["sl"] == 100.4
    assert plan["tp1"] == 99.7
