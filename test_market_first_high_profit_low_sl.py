import market_first_high_profit_low_sl as guard


def _base_signal(**overrides):
    signal = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "entry": 100.0,
        "sl": 99.2,
        "risk_percent": 0.8,
        "score": 94,
        "profit_target_percent": 3.0,
        "profit_target_r": 3.75,
        "rr_tp3": 3.75,
        "profit_quality_version": "PQ",
        "final_execution_gate_version": "FE",
        "final_execution_gate": {"fresh_micro": True},
    }
    signal.update(overrides)
    return signal


def _base_decision(**overrides):
    decision = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "score": 94,
        "risk_percent": 0.8,
        "profit_target_percent": 3.0,
        "profit_target_r": 3.75,
        "profit_structure_2h": "LONG",
        "extension_atr_5m": 0.65,
        "direction_engine": {"confirmation_flags": {"fresh_micro": True}},
    }
    decision.update(overrides)
    return decision


def test_a_plus_signal_passes():
    ok, reason, evidence = guard.evaluate_signal(_base_decision(), _base_signal())

    assert ok is True
    assert reason == "A_PLUS"
    assert evidence["target_percent"] == 3.0
    assert evidence["target_r"] == 3.75
    assert evidence["fresh_micro"] is True


def test_no_fresh_micro_is_blocked():
    signal = _base_signal(final_execution_gate={"fresh_micro": False})
    decision = _base_decision(direction_engine={"confirmation_flags": {"fresh_micro": False}})

    ok, reason, evidence = guard.evaluate_signal(decision, signal)

    assert ok is False
    assert reason == "A_PLUS_NO_FRESH_MICRO"
    assert evidence["fresh_micro"] is False


def test_opposite_2h_structure_is_blocked():
    ok, reason, evidence = guard.evaluate_signal(
        _base_decision(profit_structure_2h="SHORT"),
        _base_signal(),
    )

    assert ok is False
    assert reason == "A_PLUS_2H_OPPOSITE"
    assert evidence["structure_2h"] == "SHORT"


def test_target_percent_below_high_profit_floor_is_blocked():
    ok, reason, _ = guard.evaluate_signal(
        _base_decision(profit_target_percent=2.2),
        _base_signal(profit_target_percent=2.2),
    )

    assert ok is False
    assert reason == "A_PLUS_TARGET_BELOW_2_5P"


def test_target_r_below_three_is_blocked():
    ok, reason, _ = guard.evaluate_signal(
        _base_decision(profit_target_r=2.8),
        _base_signal(profit_target_r=2.8, rr_tp3=2.8),
    )

    assert ok is False
    assert reason == "A_PLUS_TARGET_R_BELOW_3"


def test_extended_entry_is_blocked():
    ok, reason, evidence = guard.evaluate_signal(
        _base_decision(extension_atr_5m=1.15),
        _base_signal(),
    )

    assert ok is False
    assert reason == "A_PLUS_ENTRY_EXTENDED"
    assert evidence["extension_atr_5m"] == 1.15


def test_score_below_a_plus_floor_is_blocked():
    ok, reason, _ = guard.evaluate_signal(
        _base_decision(score=89),
        _base_signal(score=89),
    )

    assert ok is False
    assert reason == "A_PLUS_SCORE_BELOW_90"
