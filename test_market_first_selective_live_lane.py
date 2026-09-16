from copy import deepcopy

from market_first_entry_plan_context_patch import decorate_promoted
from market_first_selective_live_lane_v2 import selective_lane_qualifies


def _profile():
    return {
        "enabled": True,
        "recent_entry_plan": {"samples": 250, "tp_first_rate": 0.824},
        "recent_clean_entry_plan": {"samples": 44, "tp_first_rate": 0.8864},
        "recent_entry_plus_swing": {"samples": 250, "tp_first_rate": 0.876},
        "direction": {
            "SHORT": {"samples": 161, "tp_first_rate": 0.9565},
            "LONG": {"samples": 139, "tp_first_rate": 0.7266},
        },
        "long_run_entry_plan": {"samples": 1389, "tp_first_rate": 0.7142},
        "swing_v2": {"samples": 547, "tp_first_rate": 0.5978},
    }


def _decision(direction="SHORT"):
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "entry_plan_trade": True,
        "score": 96,
        "risk_percent": 0.5,
        "room_r": 4.0,
        "profit_target_r": 4.0,
        "expected_move_percent": 2.1,
        "profit_target_percent": 2.4,
        "volume_ratio_5m": 1.2,
        "volume_ratio_15m": 1.0,
        "extension_atr_5m": 0.6,
        "derivatives_soft_score": 0,
        "market_regime": "BEAR" if direction == "SHORT" else "BULL",
        "market_preferred_direction": direction,
        "structure_5m": direction,
        "structure_15m": direction,
        "structure_1h": direction,
        "taker_available": True,
        "cvd_available": True,
        "book_available": True,
        "taker_imbalance_alignment": 0.35,
        "cvd_ratio": 0.30,
        "cvd_impulse_alignment": 0.05,
        "book_imbalance_alignment": 0.08,
        "direction_engine": {
            "selected_direction": direction,
            "reversal": False,
            "confirmations": 2,
            "confirmation_flags": {"fresh_micro": False},
            "structures": {"5m": direction, "15m": direction, "1h": direction},
            direction.lower(): {
                "taker_alignment": 0.35,
                "cvd_alignment": 0.30,
            },
        },
    }


def test_entry_plan_context_keeps_5m_and_15m_volume():
    promoted = decorate_promoted(
        {"symbol": "TESTUSDT"},
        {"volume_ratio_5m": 1.23, "volume_ratio_15m": 0.94},
    )
    assert promoted["volume_ratio_5m"] == 1.23
    assert promoted["volume_ratio_15m"] == 0.94


def test_strong_directional_short_can_use_selective_lane():
    ok, evidence = selective_lane_qualifies(_decision("SHORT"), _profile())
    assert ok is True
    assert evidence["reason"] == "SELECTIVE_A_PLUS_PLUS_V3"
    assert evidence["direction_rate"] == 0.9565
    assert evidence["flow_path"] == "FLOW_ALIGNED"
    assert evidence["score_path"] == "STANDARD_96_PLUS"


def test_one_confirmation_can_pass_only_for_96_plus_when_cvd_impulse_is_improving():
    decision = deepcopy(_decision("SHORT"))
    decision["direction_engine"]["confirmations"] = 1
    decision["taker_imbalance_alignment"] = -0.05
    decision["cvd_ratio"] = -0.04
    decision["cvd_impulse_alignment"] = 0.42
    decision["book_imbalance_alignment"] = 0.02
    decision["derivatives_soft_score"] = -1
    decision["direction_engine"]["short"]["taker_alignment"] = -0.05
    decision["direction_engine"]["short"]["cvd_alignment"] = -0.04

    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is True
    assert evidence["flow_path"] == "IMPROVING_CVD_IMPULSE"
    assert evidence["score_path"] == "STANDARD_96_PLUS"


def test_borderline_94_95_requires_two_confirmations():
    decision = deepcopy(_decision("SHORT"))
    decision["score"] = 94
    decision["direction_engine"]["confirmations"] = 1
    decision["direction_engine"]["confirmation_flags"]["fresh_micro"] = True

    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is False
    assert evidence["reason"] == "CONFIRMATIONS"
    assert evidence["score_path"] == "BORDERLINE_94_95"
    assert evidence["required_confirmations"] == 2


def test_score_92_momentum_continuation_can_pass():
    decision = deepcopy(_decision("SHORT"))
    decision["score"] = 92
    decision["direction_engine"]["confirmations"] = 2
    decision["move_3m_percent"] = -0.11
    decision["move_5m_percent"] = -0.22
    decision["volume_ratio_5m"] = 1.25
    decision["volume_ratio_15m"] = 1.05

    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is True
    assert evidence["reason"] == "SELECTIVE_MOMENTUM_CONTINUATION_V3"
    assert evidence["score_path"] == "MOMENTUM_CONTINUATION_92_93"
    assert evidence["directional_momentum"] is True
    assert evidence["required_confirmations"] == 2


def test_score_92_without_continuation_momentum_stays_blocked():
    decision = deepcopy(_decision("SHORT"))
    decision["score"] = 92
    decision["direction_engine"]["confirmations"] = 2
    decision["move_3m_percent"] = -0.01
    decision["move_5m_percent"] = -0.02
    decision["breakout_20m"] = False

    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is False
    assert evidence["reason"] == "LOW_SCORE_NO_CONTINUATION"


def test_score_below_92_stays_blocked():
    decision = deepcopy(_decision("SHORT"))
    decision["score"] = 91
    decision["direction_engine"]["confirmations"] = 3
    decision["direction_engine"]["confirmation_flags"]["fresh_micro"] = True

    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is False
    assert evidence["reason"] == "SCORE"


def test_weak_direction_history_cannot_use_lane():
    ok, evidence = selective_lane_qualifies(_decision("LONG"), _profile())
    assert ok is False
    assert evidence["reason"] == "HISTORY_DIRECTION_RATE"


def test_opposite_live_flow_remains_hard_block():
    decision = deepcopy(_decision("SHORT"))
    decision["taker_imbalance_alignment"] = -0.8
    decision["cvd_ratio"] = -0.7
    decision["direction_engine"]["short"]["taker_alignment"] = -0.8
    decision["direction_engine"]["short"]["cvd_alignment"] = -0.7
    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is False
    assert evidence["reason"] == "TAKER_CVD_OPPOSITE"


def test_relaxed_shadow_edge_cannot_use_lane():
    decision = deepcopy(_decision("SHORT"))
    decision["shadow_edge_relaxed_profit_gate"] = True
    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is False
    assert evidence["reason"] == "NO_RELAXED_SHADOW_EDGE"


def test_wide_risk_remains_blocked():
    decision = deepcopy(_decision("SHORT"))
    decision["risk_percent"] = 0.9
    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is False
    assert evidence["reason"] == "RISK"
