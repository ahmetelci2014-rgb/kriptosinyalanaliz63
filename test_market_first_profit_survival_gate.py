from __future__ import annotations

import market_first_profit_survival_gate as gate


def _strict_candidate():
    return {
        "symbol": "TESTUSDT",
        "direction": "SHORT",
        "score": 99,
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


def test_bad_recent_real_cohort_enters_recovery():
    daily = {
        "date": "2026-09-12",
        "closed_directional": 9,
        "losses": 7,
        "wins": 2,
        "stop_rate": 7 / 9,
    }
    intraday = {"sl": 0, "consecutive_stops": 0}
    mode, reason = gate._mode_from_health(daily, intraday, "2026-09-13")
    assert mode == "RECOVERY_STRICT"
    assert reason == "CRITICAL_RECENT_STOP_RATE"


def test_four_same_day_stops_halts_new_entries():
    daily = {"date": "2026-09-12", "closed_directional": 0, "stop_rate": 0.0}
    intraday = {"sl": 4, "consecutive_stops": 1}
    mode, reason = gate._mode_from_health(daily, intraday, "2026-09-13")
    assert mode == "HALT"
    assert reason == "DAILY_STOP_LIMIT"


def test_three_consecutive_stops_halts_new_entries():
    daily = {"date": "2026-09-12", "closed_directional": 0, "stop_rate": 0.0}
    intraday = {"sl": 3, "consecutive_stops": 3}
    mode, reason = gate._mode_from_health(daily, intraday, "2026-09-13")
    assert mode == "HALT"
    assert reason == "CONSECUTIVE_STOP_LIMIT"


def test_a_plus_plus_aligned_candidate_survives_recovery():
    ok, reason, evidence = gate._recovery_reason(_strict_candidate())
    assert ok is True
    assert reason == "OK"
    assert evidence["structures_aligned"] is True
    assert evidence["market_aligned"] is True


def test_relaxed_shadow_edge_is_disabled_in_recovery():
    candidate = _strict_candidate()
    candidate["shadow_edge_relaxed_profit_gate"] = True
    ok, reason, _ = gate._recovery_reason(candidate)
    assert ok is False
    assert reason == "RECOVERY_NO_RELAXED_SHADOW_EDGE"


def test_ordinary_countertrend_is_blocked_in_recovery():
    candidate = _strict_candidate()
    candidate["market_preferred_direction"] = "LONG"
    candidate["independent_move"] = False
    ok, reason, _ = gate._recovery_reason(candidate)
    assert ok is False
    assert reason == "RECOVERY_MARKET_OPPOSED"


def test_only_exceptional_independent_countertrend_can_survive():
    candidate = _strict_candidate()
    candidate["market_preferred_direction"] = "LONG"
    candidate["independent_move"] = True
    candidate["breakout_20m"] = True
    candidate["volume_ratio_1m"] = 3.2
    candidate["move_5m_percent"] = -0.30
    ok, reason, _ = gate._recovery_reason(candidate)
    assert ok is True
    assert reason == "OK"
