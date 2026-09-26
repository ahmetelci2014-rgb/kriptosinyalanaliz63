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


def test_fast_entry_cannot_bypass_high_profit_low_sl_gate():
    signal = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "entry": 10.0,
        "sl": 9.93,
        "risk_percent": 0.7,
        "score": 94,
        "fast_entry": True,
        "profit_quality_version": "PQ",
        "final_execution_gate_version": "FE",
        "final_execution_gate": {"fresh_micro": True},
    }

    ok, reason, evidence = guard.evaluate_signal(signal)

    assert ok is False
    assert reason == "HIGH_PROFIT_LOW_SL_NOT_CERTIFIED"
    assert evidence["high_profit_low_sl_certified"] is False


def test_fast_entry_can_continue_after_all_certifications():
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
        "final_execution_gate": {"fresh_micro": True},
        "high_profit_low_sl_version": "HP",
        "high_profit_low_sl_grade": "A+",
    }

    ok, reason, _ = guard.evaluate_signal(signal)

    assert ok is True
    assert reason == "OK"


def test_minimum_stop_without_fresh_micro_is_blocked_before_a_plus_check():
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


def test_minimum_stop_with_fresh_micro_and_a_plus_can_pass():
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
        "high_profit_low_sl_version": "HP",
        "high_profit_low_sl_grade": "A+",
    }

    ok, reason, _ = guard.evaluate_signal(signal)

    assert ok is True
    assert reason == "OK"


def test_non_fast_real_send_also_requires_a_plus():
    signal = {
        "symbol": "TESTUSDT",
        "direction": "SHORT",
        "entry": 100.0,
        "sl": 100.8,
        "risk_percent": 0.8,
        "score": 94,
        "profit_quality_version": "PQ",
        "final_execution_gate_version": "FE",
        "final_execution_gate": {"fresh_micro": True},
    }

    ok, reason, _ = guard.evaluate_signal(signal)

    assert ok is False
    assert reason == "HIGH_PROFIT_LOW_SL_NOT_CERTIFIED"
