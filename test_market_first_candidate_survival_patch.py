from __future__ import annotations

import market_first_candidate_survival_patch as patch


def _candidate(score=99):
    return {
        "symbol": "TESTUSDT",
        "direction": "SHORT",
        "score": score,
        "risk_percent": 0.55,
        "expected_move_percent": 2.4,
        "profit_target_r": 3.2,
        "technical_target_r": 3.0,
        "room_r": 3.4,
        "structure_5m": "SHORT",
        "structure_15m": "SHORT",
        "structure_1h": "SHORT",
        "market_preferred_direction": "SHORT",
        "independent_move": False,
        "breakout_20m": True,
        "volume_ratio_1m": 3.0,
        "move_3m_percent": -0.16,
        "move_5m_percent": -0.24,
        "derivatives_soft_score": 3,
        "cvd_impulse_alignment": 0.4,
        "direction_engine": {
            "selected_direction": "SHORT",
            "confirmations": 4,
            "reversal": False,
            "confirmation_flags": {"fresh_micro": True},
            "structures": {"5m": "SHORT", "15m": "SHORT", "1h": "SHORT"},
            "short": {"taker_alignment": 0.55, "cvd_alignment": 0.52},
        },
    }


def test_halt_hides_all_candidate_alerts():
    ok, reason = patch.survival_allows_candidate(
        _candidate(),
        {"mode": "HALT", "reason": "DAILY_STOP_LIMIT_V2"},
    )
    assert ok is False
    assert reason.startswith("SURVIVAL_HALT")


def test_recovery_hides_candidate_that_is_not_a_plus_plus():
    ok, reason = patch.survival_allows_candidate(
        _candidate(score=94),
        {"mode": "RECOVERY_STRICT", "reason": "POOR_ROLLING_STOP_RATE"},
    )
    assert ok is False
    assert "RECOVERY_SCORE" in reason


def test_recovery_can_show_candidate_that_meets_same_a_plus_plus_rules():
    ok, reason = patch.survival_allows_candidate(
        _candidate(score=99),
        {"mode": "RECOVERY_STRICT", "reason": "POOR_ROLLING_STOP_RATE"},
    )
    assert ok is True
    assert reason == "OK"


def test_normal_mode_does_not_add_extra_block():
    ok, reason = patch.survival_allows_candidate(
        _candidate(),
        {"mode": "NORMAL", "reason": "OK"},
    )
    assert ok is True
    assert reason == "OK"
