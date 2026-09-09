from __future__ import annotations

import market_first_final_execution_gate as gate


def base_decision(direction="LONG"):
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "expected_move_percent": 1.20,
        "move_3m_percent": 0.0,
        "move_5m_percent": 0.0,
        "breakout_20m": False,
        "taker_available": True,
        "cvd_available": True,
        "book_available": True,
        "derivatives_available": True,
        "taker_imbalance_alignment": 0.30,
        "cvd_ratio": 0.30 if direction == "LONG" else -0.30,
        "cvd_impulse_alignment": 0.0,
        "book_imbalance_alignment": 0.10,
        "derivatives_soft_score": 1,
        "direction_engine": {
            "selected_direction": direction,
            "confirmations": 3,
            "confirmation_flags": {"fresh_micro": False},
            direction.lower(): {
                "taker_alignment": 0.30,
                "cvd_alignment": 0.30,
            },
        },
    }


def test_vana_profile_is_rejected_for_too_small_near_target_before_far_five_percent_target():
    decision = base_decision("LONG")
    decision.update({
        "symbol": "VANAUSDT",
        "expected_move_percent": 0.665,
        "taker_imbalance_alignment": 0.200818,
        "cvd_impulse_alignment": -0.104477,
        "book_imbalance_alignment": -0.128758,
        "derivatives_soft_score": -1,
    })
    decision["direction_engine"]["confirmations"] = 4
    decision["direction_engine"]["long"] = {
        "taker_alignment": 0.200818,
        "cvd_alignment": 0.200818,
    }
    ok, reason, _ = gate._execution_reason(decision)
    assert not ok
    assert reason == "NEAR_TECHNICAL_EXPECTATION_TOO_SMALL"


def test_met_profile_is_rejected_for_insufficient_confirmations_and_opposing_flow():
    decision = base_decision("LONG")
    decision.update({
        "symbol": "METUSDT",
        "expected_move_percent": 1.162,
        "taker_imbalance_alignment": -0.145687,
        "cvd_impulse_alignment": -1.489801,
        "book_imbalance_alignment": 0.033989,
        "derivatives_soft_score": -1,
    })
    decision["direction_engine"]["confirmations"] = 2
    decision["direction_engine"]["long"] = {
        "taker_alignment": -0.145687,
        "cvd_alignment": -0.145687,
    }
    ok, reason, _ = gate._execution_reason(decision)
    assert not ok
    assert reason == "DIRECTION_CONFIRMATIONS_BELOW_3"


def test_zec_profile_passes_with_three_confirmations_and_strong_flow():
    decision = base_decision("LONG")
    decision.update({
        "symbol": "ZECUSDT",
        "expected_move_percent": 0.902,
        "taker_imbalance_alignment": 1.0,
        "cvd_impulse_alignment": 0.0,
        "book_imbalance_alignment": 0.367666,
        "derivatives_soft_score": 1,
    })
    decision["direction_engine"]["long"] = {
        "taker_alignment": 1.0,
        "cvd_alignment": 1.0,
    }
    ok, reason, evidence = gate._execution_reason(decision)
    assert ok
    assert reason == "OK"
    assert evidence["confirmations"] == 3
    assert evidence["fresh_micro"] is False


def test_no_fresh_micro_requires_real_taker_and_cvd_support():
    decision = base_decision("LONG")
    decision["direction_engine"]["long"] = {
        "taker_alignment": 0.14,
        "cvd_alignment": 0.40,
    }
    ok, reason, _ = gate._execution_reason(decision)
    assert not ok
    assert reason == "NO_FRESH_MICRO_TAKER_WEAK"


def test_fresh_micro_can_replace_strong_flow_but_not_strong_opposite_pressure():
    decision = base_decision("LONG")
    decision["move_3m_percent"] = 0.12
    decision["move_5m_percent"] = 0.18
    decision["direction_engine"]["long"] = {
        "taker_alignment": 0.02,
        "cvd_alignment": 0.03,
    }
    decision["derivatives_soft_score"] = -1
    ok, reason, evidence = gate._execution_reason(decision)
    assert ok
    assert reason == "OK"
    assert evidence["fresh_micro"] is True

    decision["book_imbalance_alignment"] = -0.20
    ok, reason, _ = gate._execution_reason(decision)
    assert not ok
    assert reason == "BOOK_PRESSURE_OPPOSITE"


def test_short_side_is_symmetric():
    decision = base_decision("SHORT")
    decision["expected_move_percent"] = 1.10
    decision["cvd_ratio"] = -0.50
    decision["direction_engine"]["short"] = {
        "taker_alignment": 0.50,
        "cvd_alignment": 0.50,
    }
    ok, reason, _ = gate._execution_reason(decision)
    assert ok
    assert reason == "OK"


def test_install_runs_upstream_pipeline_before_execution_gate(monkeypatch):
    decision = base_decision("LONG")
    engine = decision.pop("direction_engine")

    def upstream(value):
        # Mirrors Entry Plan: the Direction Engine is attached inside the
        # upstream decision-to-signal call, not before it.
        value["direction_engine"] = engine
        return {"symbol": value["symbol"], "direction": value["direction"], "score": 94}

    previous_installed = gate._INSTALLED
    gate._INSTALLED = False
    gate._RUN_COUNTS.clear()
    gate._RUN_ACCEPTED.clear()
    monkeypatch.setattr(gate.runner, "decision_to_signal", upstream)
    try:
        gate.install()
        signal = gate.runner.decision_to_signal(decision)
        assert signal is not None
        assert signal["final_execution_gate_version"] == gate.VERSION
        assert signal["final_execution_gate"]["confirmations"] == 3
        assert gate._RUN_COUNTS["OK"] == 1
        assert gate._RUN_COUNTS["ACCEPTED"] == 1
    finally:
        gate._INSTALLED = previous_installed
        gate._RUN_COUNTS.clear()
        gate._RUN_ACCEPTED.clear()
