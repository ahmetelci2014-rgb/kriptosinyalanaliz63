from __future__ import annotations

import copy

import market_first_profit_survival_gate as gate
import market_first_recovery_evidence_lane as lane


def _profile():
    return {
        "enabled": True,
        "recent_entry_plan": {"samples": 250, "tp_first_rate": 0.884},
        "recent_clean_entry_plan": {"samples": 35, "tp_first_rate": 0.8571},
        "recent_entry_plus_swing": {"samples": 250, "tp_first_rate": 0.94},
        "direction": {
            "LONG": {"samples": 151, "tp_first_rate": 0.8874},
            "SHORT": {"samples": 149, "tp_first_rate": 0.9195},
        },
        "long_run_entry_plan": {"samples": 1396, "tp_first_rate": 0.7113},
        "swing_v2": {"samples": 512, "tp_first_rate": 0.5859},
    }


def _plan(direction="SHORT"):
    return {
        "status": "PREP",
        "symbol": "TESTUSDT",
        "direction": direction,
        "score": 94,
        "structure_5m": direction,
        "structure_15m": direction,
        "structure_1h": direction,
        "market_preferred_direction": direction,
        "market_regime": "BEAR" if direction == "SHORT" else "BULL",
        "zone_distance_percent": 0.35,
        "volume_ratio_5m": 1.6,
        "volume_ratio_15m": 1.3,
        "risk_percent": 0.55,
        "room_r": 3.4,
        "extension_atr_5m": 0.7,
    }


def _lane_evidence(direction="SHORT"):
    return lane._profile_evidence(_profile(), direction)


def _candidate():
    evidence = _lane_evidence("SHORT")
    return {
        "symbol": "TESTUSDT",
        "direction": "SHORT",
        "score": 94,
        "risk_percent": 0.55,
        "expected_move_percent": 2.2,
        "profit_target_r": 3.2,
        "technical_target_r": 3.1,
        "room_r": 3.4,
        "extension_atr_5m": 0.7,
        "structure_5m": "SHORT",
        "structure_15m": "SHORT",
        "structure_1h": "SHORT",
        "market_preferred_direction": "SHORT",
        "market_regime": "BEAR",
        "independent_move": False,
        "breakout_20m": True,
        "volume_ratio_1m": 2.0,
        "move_3m_percent": -0.16,
        "move_5m_percent": -0.24,
        "derivatives_soft_score": 2,
        "cvd_impulse_alignment": 0.25,
        "background_live_bridge": True,
        "recovery_evidence_lane": True,
        "recovery_evidence_version": lane.VERSION,
        "recovery_evidence_evidence": evidence,
        "direction_engine": {
            "selected_direction": "SHORT",
            "confirmations": 4,
            "reversal": False,
            "confirmation_flags": {"fresh_micro": True},
            "structures": {"5m": "SHORT", "15m": "SHORT", "1h": "SHORT"},
            "short": {"taker_alignment": 0.55, "cvd_alignment": 0.52},
        },
    }


def test_strong_history_can_promote_score_94_prep_during_recovery():
    ok, evidence = lane.recovery_background_qualifies(
        _plan(), _profile(), {"mode": "RECOVERY_STRICT", "reason": "POOR_ROLLING_STOP_RATE"}
    )
    assert ok is True
    assert evidence["reason"] == "RECOVERY_EVIDENCE_LANE_A_PLUS_PLUS"
    assert evidence["recent_rate"] >= lane.MIN_RECENT_RATE
    assert evidence["direction_rate"] >= lane.MIN_DIRECTION_RATE


def test_score_93_is_still_blocked():
    plan = _plan()
    plan["score"] = 93
    ok, evidence = lane.recovery_background_qualifies(
        plan, _profile(), {"mode": "RECOVERY_STRICT"}
    )
    assert ok is False
    assert evidence["reason"] == "RECOVERY_SCORE"


def test_risk_above_065_is_still_blocked():
    plan = _plan()
    plan["risk_percent"] = 0.66
    ok, evidence = lane.recovery_background_qualifies(
        plan, _profile(), {"mode": "RECOVERY_STRICT"}
    )
    assert ok is False
    assert evidence["reason"] == "RECOVERY_RISK"


def test_room_below_3r_is_still_blocked():
    plan = _plan()
    plan["room_r"] = 2.99
    ok, evidence = lane.recovery_background_qualifies(
        plan, _profile(), {"mode": "RECOVERY_STRICT"}
    )
    assert ok is False
    assert evidence["reason"] == "RECOVERY_ROOM"


def test_extension_above_09_atr_is_still_blocked():
    plan = _plan()
    plan["extension_atr_5m"] = 0.91
    ok, evidence = lane.recovery_background_qualifies(
        plan, _profile(), {"mode": "RECOVERY_STRICT"}
    )
    assert ok is False
    assert evidence["reason"] == "RECOVERY_EXTENSION"


def test_market_opposite_is_not_given_recovery_exception():
    plan = _plan("SHORT")
    plan["market_preferred_direction"] = "LONG"
    ok, evidence = lane.recovery_background_qualifies(
        plan, _profile(), {"mode": "RECOVERY_STRICT"}
    )
    assert ok is False
    assert evidence["reason"] == "RECOVERY_MARKET_NOT_ALIGNED"


def test_weak_direction_specific_history_blocks_that_side():
    profile = copy.deepcopy(_profile())
    profile["direction"]["SHORT"] = {"samples": 149, "tp_first_rate": 0.70}
    ok, evidence = lane.recovery_background_qualifies(
        _plan("SHORT"), profile, {"mode": "RECOVERY_STRICT"}
    )
    assert ok is False
    assert evidence["reason"] == "RECOVERY_HISTORY_DIRECTION_RATE"


def test_weak_clean_history_blocks_recovery_lane():
    profile = copy.deepcopy(_profile())
    profile["recent_clean_entry_plan"]["tp_first_rate"] = 0.70
    ok, evidence = lane.recovery_background_qualifies(
        _plan(), profile, {"mode": "RECOVERY_STRICT"}
    )
    assert ok is False
    assert evidence["reason"] == "RECOVERY_HISTORY_CLEAN_RATE"


def test_halt_can_never_be_bypassed():
    ok, evidence = lane.recovery_background_qualifies(
        _plan(), _profile(), {"mode": "HALT", "reason": "DAILY_STOP_LIMIT_V2"}
    )
    assert ok is False
    assert evidence["reason"] == "SURVIVAL_HALT"


def test_only_one_recovery_background_promotion_per_run():
    lane._RECOVERY_PROMOTIONS = 0
    health = {"mode": "RECOVERY_STRICT"}
    first, _ = lane.recovery_background_qualifies(
        _plan(), _profile(), health, consume_slot=True
    )
    second, evidence = lane.recovery_background_qualifies(
        _plan(), _profile(), health, consume_slot=True
    )
    assert first is True
    assert second is False
    assert evidence["reason"] == "RECOVERY_EVIDENCE_LANE_LIMIT"
    lane._RECOVERY_PROMOTIONS = 0


def test_final_recovery_gate_accepts_validated_score_94_lane_but_not_plain_score_94():
    candidate = _candidate()
    ok, reason, evidence = lane.recovery_gate_reason(candidate, gate._recovery_reason)
    assert ok is True
    assert reason == "OK_RECOVERY_EVIDENCE_LANE"
    assert evidence["actual_score"] == 94

    plain = _candidate()
    plain.pop("recovery_evidence_lane")
    plain.pop("recovery_evidence_evidence")
    plain.pop("recovery_evidence_version")
    plain.pop("background_live_bridge")
    ok, reason, _ = lane.recovery_gate_reason(plain, gate._recovery_reason)
    assert ok is False
    assert reason == "RECOVERY_SCORE"


def test_lane_still_requires_original_strong_flow_or_fresh_micro():
    candidate = _candidate()
    candidate["direction_engine"]["confirmation_flags"]["fresh_micro"] = False
    candidate["direction_engine"]["short"]["taker_alignment"] = 0.1
    candidate["direction_engine"]["short"]["cvd_alignment"] = 0.1
    candidate["move_3m_percent"] = 0.0
    candidate["move_5m_percent"] = 0.0
    ok, reason, _ = lane.recovery_gate_reason(candidate, gate._recovery_reason)
    assert ok is False
    assert reason == "RECOVERY_NO_FRESH_OR_FLOW"


def test_lane_cannot_use_relaxed_shadow_edge_target():
    candidate = _candidate()
    candidate["shadow_edge_relaxed_profit_gate"] = True
    ok, reason, _ = lane.recovery_gate_reason(candidate, gate._recovery_reason)
    assert ok is False
    assert reason == "RECOVERY_NO_RELAXED_SHADOW_EDGE"


def test_lane_final_gate_rechecks_3r_and_expected_move():
    candidate = _candidate()
    candidate["profit_target_r"] = 2.8
    candidate["technical_target_r"] = 2.8
    candidate["room_r"] = 2.8
    ok, reason, _ = lane.recovery_gate_reason(candidate, gate._recovery_reason)
    assert ok is False
    assert reason == "RECOVERY_EVIDENCE_TARGET_R"

    candidate = _candidate()
    candidate["expected_move_percent"] = 1.70
    ok, reason, _ = lane.recovery_gate_reason(candidate, gate._recovery_reason)
    assert ok is False
    assert reason == "RECOVERY_EVIDENCE_EXPECTED_MOVE"
