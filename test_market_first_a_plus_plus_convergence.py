import market_first_a_plus_plus_convergence as gate


def base_decision():
    return {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "stage": "READY",
        "entry_plan_trade": True,
        "entry_plan_version": "ENTRY_PLAN_V1",
        "room_r": 4.2,
        "structure_15m": "LONG",
        "structure_1h": "LONG",
        "context_2h_direction": "LONG",
        "context_2h_alignment": "ALIGNED",
        "context_2h_1h_alignment": "ALIGNED",
        "context_2h_extension_atr": 0.85,
        "move_3m_percent": 0.18,
        "move_5m_percent": 0.28,
        "breakout_20m": False,
    }


def base_signal():
    return {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "entry": 100.0,
        "sl": 99.2,
        "risk_percent": 0.8,
        "high_profit_low_sl_version": "HP",
        "high_profit_low_sl_grade": "A+",
        "final_execution_gate_version": "FE",
        "final_execution_gate": {"fresh_micro": True},
    }


def test_a_plus_plus_accepts_full_convergence():
    ok, reason, evidence = gate.evaluate_signal(base_decision(), base_signal())
    assert ok is True
    assert reason == "A_PLUS_PLUS"
    assert evidence["structure_2h"] == "LONG"
    assert evidence["structure_1h"] == "LONG"
    assert evidence["structure_15m"] == "LONG"
    assert evidence["fresh_micro"] is True
    assert evidence["room_r"] == 4.2


def test_a_plus_plus_requires_real_entry_plan():
    decision = base_decision()
    decision["entry_plan_trade"] = False
    ok, reason, _ = gate.evaluate_signal(decision, base_signal())
    assert ok is False
    assert reason == "A_PLUS_PLUS_NOT_ENTRY_PLAN"


def test_a_plus_plus_requires_structural_room():
    decision = base_decision()
    decision["room_r"] = 2.6
    ok, reason, evidence = gate.evaluate_signal(decision, base_signal())
    assert ok is False
    assert reason == "A_PLUS_PLUS_ROOM_BELOW_3R"
    assert evidence["room_r"] == 2.6


def test_a_plus_plus_rejects_neutral_or_opposite_2h():
    decision = base_decision()
    decision["context_2h_direction"] = "NEUTRAL"
    decision["context_2h_alignment"] = "NEUTRAL_2H"
    ok, reason, _ = gate.evaluate_signal(decision, base_signal())
    assert ok is False
    assert reason == "A_PLUS_PLUS_2H_NOT_ALIGNED"

    decision = base_decision()
    decision["context_2h_direction"] = "SHORT"
    decision["context_2h_alignment"] = "OPPOSED"
    ok, reason, _ = gate.evaluate_signal(decision, base_signal())
    assert ok is False
    assert reason == "A_PLUS_PLUS_2H_NOT_ALIGNED"


def test_a_plus_plus_rejects_2h_overextension():
    decision = base_decision()
    decision["context_2h_extension_atr"] = 1.7
    ok, reason, evidence = gate.evaluate_signal(decision, base_signal())
    assert ok is False
    assert reason == "A_PLUS_PLUS_2H_EXTENDED"
    assert evidence["extension_atr_2h"] == 1.7


def test_a_plus_plus_requires_fresh_micro():
    signal = base_signal()
    signal["final_execution_gate"] = {"fresh_micro": False}
    decision = base_decision()
    decision["move_3m_percent"] = 0.0
    decision["move_5m_percent"] = 0.0
    ok, reason, evidence = gate.evaluate_signal(decision, signal)
    assert ok is False
    assert reason == "A_PLUS_PLUS_NO_FRESH_MICRO"
    assert evidence["micro_source"] == "NONE"


def test_a_plus_plus_requires_upstream_a_plus():
    signal = base_signal()
    signal.pop("high_profit_low_sl_version")
    signal.pop("high_profit_low_sl_grade")
    ok, reason, _ = gate.evaluate_signal(base_decision(), signal)
    assert ok is False
    assert reason == "A_PLUS_NOT_CERTIFIED"
