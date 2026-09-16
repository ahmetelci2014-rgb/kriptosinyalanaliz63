from copy import deepcopy

from market_first_entry_plan_context_patch import decorate_promoted
from market_first_selective_live_lane import selective_lane_qualifies


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
    assert evidence["reason"] == "SELECTIVE_A_PLUS_PLUS"
    assert evidence["direction_rate"] == 0.9565


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


def test_wide_risk_remains_blocked():
    decision = deepcopy(_decision("SHORT"))
    decision["risk_percent"] = 0.9
    ok, evidence = selective_lane_qualifies(decision, _profile())
    assert ok is False
    assert evidence["reason"] == "RISK"
