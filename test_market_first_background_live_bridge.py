from __future__ import annotations

import market_first_background_live_bridge as bridge


def _history(wins=12, losses=4, direction="LONG"):
    episodes = {}
    for index in range(wins):
        episodes[f"w{index}"] = {
            "episode_id": f"w{index}",
            "direction": direction,
            "sources": ["ENTRY_PLAN", "SWING_2H"],
            "first_touch": "TP_FIRST",
            "tp_first": True,
            "opposite_direction_seen": False,
        }
    for index in range(losses):
        episodes[f"l{index}"] = {
            "episode_id": f"l{index}",
            "direction": direction,
            "sources": ["ENTRY_PLAN"],
            "first_touch": "SL_FIRST",
            "tp_first": False,
            "opposite_direction_seen": False,
        }
    return {"episodes": episodes}


def _profile():
    return bridge.build_profile(
        _history(),
        {
            "entry_plan_clean": {
                "tp1_first": 1019,
                "sl_first": 438,
            }
        },
        {
            "v2_tp1_first": 254,
            "v2_sl_first": 177,
        },
    )


def _plan():
    return {
        "status": "PREP",
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "score": 96,
        "structure_5m": "LONG",
        "structure_15m": "LONG",
        "structure_1h": "LONG",
        "market_preferred_direction": "LONG",
        "zone_distance_percent": 0.85,
        "volume_ratio_5m": 1.35,
        "volume_ratio_15m": 1.10,
        "risk_percent": 0.55,
        "room_r": 3.4,
        "extension_atr_5m": 0.80,
    }


def test_background_profile_enables_with_recent_and_long_run_edge():
    profile = _profile()
    assert profile["enabled"] is True
    assert profile["recent_entry_plan"]["samples"] == 16
    assert profile["recent_entry_plan"]["tp_first_rate"] == 0.75
    assert profile["long_run_entry_plan"]["tp_first_rate"] > 0.69
    assert profile["swing_v2"]["tp_first_rate"] > 0.58


def test_validated_background_edge_can_promote_a_plus_plus_prep_in_normal_mode():
    ok, evidence = bridge.background_promotion_qualifies(
        _plan(),
        _profile(),
        {"mode": "NORMAL", "reason": "OK"},
    )
    assert ok is True
    assert evidence["reason"] == "BACKGROUND_EDGE_A_PLUS_PLUS"


def test_recovery_mode_disables_relaxed_background_promotion():
    ok, evidence = bridge.background_promotion_qualifies(
        _plan(),
        _profile(),
        {"mode": "RECOVERY_STRICT", "reason": "POOR_ROLLING_STOP_RATE"},
    )
    assert ok is False
    assert evidence["reason"] == "SURVIVAL_RECOVERY_STRICT"


def test_weak_recent_background_does_not_enable_bridge():
    profile = bridge.build_profile(
        _history(wins=6, losses=10),
        {"entry_plan_clean": {"tp1_first": 1019, "sl_first": 438}},
        {"v2_tp1_first": 254, "v2_sl_first": 177},
    )
    assert profile["enabled"] is False
    ok, evidence = bridge.background_promotion_qualifies(
        _plan(), profile, {"mode": "NORMAL", "reason": "OK"}
    )
    assert ok is False
    assert evidence["reason"] == "BACKGROUND_EDGE_NOT_VALIDATED"


def test_directional_background_weakness_blocks_that_side():
    history = _history(wins=12, losses=4, direction="LONG")
    # Add a poor SHORT cohort while keeping global ENTRY_PLAN evidence positive.
    for index in range(2):
        history["episodes"][f"sw{index}"] = {
            "episode_id": f"sw{index}",
            "direction": "SHORT",
            "sources": ["ENTRY_PLAN"],
            "first_touch": "TP_FIRST",
            "tp_first": True,
            "opposite_direction_seen": False,
        }
    for index in range(6):
        history["episodes"][f"sl{index}"] = {
            "episode_id": f"sl{index}",
            "direction": "SHORT",
            "sources": ["ENTRY_PLAN"],
            "first_touch": "SL_FIRST",
            "tp_first": False,
            "opposite_direction_seen": False,
        }
    profile = bridge.build_profile(
        history,
        {"entry_plan_clean": {"tp1_first": 1019, "sl_first": 438}},
        {"v2_tp1_first": 254, "v2_sl_first": 177},
    )
    plan = _plan()
    plan["direction"] = "SHORT"
    plan["structure_5m"] = "SHORT"
    plan["structure_15m"] = "SHORT"
    plan["structure_1h"] = "SHORT"
    plan["market_preferred_direction"] = "SHORT"
    ok, evidence = bridge.background_promotion_qualifies(
        plan, profile, {"mode": "NORMAL", "reason": "OK"}
    )
    assert ok is False
    assert evidence["reason"] == "DIRECTIONAL_BACKGROUND_EDGE_WEAK"


def test_bridge_never_promotes_loose_risk_geometry():
    plan = _plan()
    plan["risk_percent"] = 0.9
    ok, evidence = bridge.background_promotion_qualifies(
        plan, _profile(), {"mode": "NORMAL", "reason": "OK"}
    )
    assert ok is False
    assert evidence["reason"] == "RISK"
