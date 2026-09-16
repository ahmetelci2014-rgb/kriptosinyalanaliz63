from __future__ import annotations

import market_first_profit_survival_v2 as v2


def _daily_report(date="2026-09-13"):
    rows = []
    rows.extend({"result": "TP3"} for _ in range(4))
    rows.extend({"result": "STOP"} for _ in range(3))
    return {"date": date, "real_trades": rows}


def test_two_day_memory_keeps_recovery_after_one_better_day():
    performance = {
        "days": {
            "2026-09-12": {"tp1": 2, "sl": 7},
            "2026-09-13": {"tp1": 4, "sl": 3},
        }
    }
    rolling = v2.rolling_completed_health(
        performance,
        _daily_report(),
        "2026-09-14",
    )
    assert rolling["wins"] == 6
    assert rolling["losses"] == 10
    assert rolling["closed_directional"] == 16
    assert abs(rolling["stop_rate"] - 0.625) < 1e-9
    assert rolling["latest_date"] == "2026-09-13"
    assert rolling["latest_age_days"] == 1

    daily = {
        "date": "2026-09-13",
        "closed_directional": 7,
        "wins": 4,
        "losses": 3,
        "stop_rate": 3 / 7,
    }
    mode, reason = v2.mode_from_health(
        daily,
        rolling,
        {"sl": 0, "direct_stops": 0, "consecutive_stops": 0},
        "2026-09-14",
    )
    assert mode == "RECOVERY_STRICT"
    assert reason == "POOR_ROLLING_STOP_RATE"


def test_two_same_day_stops_tighten_to_recovery():
    mode, reason = v2.mode_from_health(
        {}, {},
        {"sl": 2, "direct_stops": 2, "consecutive_stops": 2},
        "2026-09-14",
    )
    assert mode == "RECOVERY_STRICT"
    assert reason == "INTRADAY_STOP_WARNING"


def test_three_same_day_stops_halt_new_entries():
    mode, reason = v2.mode_from_health(
        {}, {},
        {"sl": 3, "direct_stops": 3, "consecutive_stops": 1},
        "2026-09-14",
    )
    assert mode == "HALT"
    assert reason == "DAILY_STOP_LIMIT_V2"


def test_healthy_rolling_cohort_can_return_normal():
    rolling = {
        "closed_directional": 12,
        "wins": 9,
        "losses": 3,
        "stop_rate": 0.25,
        "latest_date": "2026-09-13",
        "latest_age_days": 1,
    }
    daily = {
        "date": "2026-09-13",
        "closed_directional": 7,
        "stop_rate": 2 / 7,
    }
    mode, reason = v2.mode_from_health(
        daily,
        rolling,
        {"sl": 0, "direct_stops": 0, "consecutive_stops": 0},
        "2026-09-14",
    )
    assert mode == "NORMAL"
    assert reason == "OK"


def test_stale_bad_rolling_cohort_does_not_lock_quiet_day():
    rolling = {
        "closed_directional": 11,
        "wins": 4,
        "losses": 7,
        "stop_rate": 7 / 11,
        "latest_date": "2026-09-14",
        "latest_age_days": 2,
    }
    daily = {
        "date": "2026-09-15",
        "closed_directional": 0,
        "wins": 0,
        "losses": 0,
        "stop_rate": 0.0,
    }
    mode, reason = v2.mode_from_health(
        daily,
        rolling,
        {"sl": 1, "direct_stops": 0, "consecutive_stops": 1},
        "2026-09-16",
    )
    assert mode == "NORMAL"
    assert reason == "OK"


def test_stale_history_never_disables_same_day_stop_brake():
    rolling = {
        "closed_directional": 11,
        "wins": 4,
        "losses": 7,
        "stop_rate": 7 / 11,
        "latest_date": "2026-09-14",
        "latest_age_days": 2,
    }
    mode, reason = v2.mode_from_health(
        {},
        rolling,
        {"sl": 2, "direct_stops": 2, "consecutive_stops": 2},
        "2026-09-16",
    )
    assert mode == "RECOVERY_STRICT"
    assert reason == "INTRADAY_STOP_WARNING"
