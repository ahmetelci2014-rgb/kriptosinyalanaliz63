import market_first_balanced_core_guard as guard


def test_fast_entry_cannot_bypass_outer_quality_gates():
    signal = {
        "symbol": "EGLDUSDT",
        "direction": "LONG",
        "entry": 3.943,
        "sl": 3.9166,
        "risk_percent": 0.669,
        "score": 83,
        "fast_entry": True,
        "rr_tp3": 1.5,
    }

    ok, reason, evidence = guard.evaluate_signal(signal)

    assert ok is False
    assert reason == "FAST_BEFORE_PROFIT_QUALITY"
    assert evidence["profit_quality_certified"] is False


def test_fast_entry_can_continue_after_both_certifications():
    signal = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "entry": 10.0,
        "sl": 9.93,
        "risk_percent": 0.7,
        "score": 92,
        "fast_entry": True,
        "profit_quality_version": "PQ",
        "final_execution_gate_version": "FE",
        "final_execution_gate": {"fresh_micro": False},
    }

    ok, reason, _ = guard.evaluate_signal(signal)

    assert ok is True
    assert reason == "OK"


def test_minimum_stop_without_fresh_micro_is_blocked():
    signal = {
        "symbol": "GIGGLEUSDT",
        "direction": "LONG",
        "entry": 34.53,
        "sl": 34.39188,
        "risk_percent": 0.4,
        "score": 94,
        "profit_quality_version": "PQ",
        "final_execution_gate_version": "FE",
        "final_execution_gate": {"fresh_micro": False},
    }

    ok, reason, evidence = guard.evaluate_signal(signal)

    assert ok is False
    assert reason == "MIN_STOP_WITHOUT_FRESH_MICRO"
    assert evidence["risk_percent"] == 0.4


def test_minimum_stop_with_fresh_micro_can_pass():
    signal = {
        "symbol": "TESTUSDT",
        "direction": "SHORT",
        "entry": 100.0,
        "sl": 100.4,
        "risk_percent": 0.4,
        "score": 94,
        "profit_quality_version": "PQ",
        "final_execution_gate_version": "FE",
        "final_execution_gate": {"fresh_micro": True},
    }

    ok, reason, _ = guard.evaluate_signal(signal)

    assert ok is True
    assert reason == "OK"
