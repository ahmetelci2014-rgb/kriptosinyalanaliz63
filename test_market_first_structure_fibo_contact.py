from __future__ import annotations

import pandas as pd

import market_first_structure_fibo_contact as fib


def _plan(direction="LONG"):
    return {
        "status": "PREP",
        "symbol": "TESTUSDT",
        "direction": direction,
        "score": 90,
        "structure_5m": direction,
        "structure_15m": direction,
        "structure_1h": direction,
        "market_preferred_direction": direction,
        "volume_ratio_5m": 1.30,
        "volume_ratio_15m": 1.10,
        "extension_atr_5m": 0.70,
    }


def _impulse(direction="LONG"):
    return {
        "direction": direction,
        "pattern": "HL->HH" if direction == "LONG" else "LH->LL",
        "impulse_low": 100.0,
        "impulse_high": 110.0,
        "impulse_atr": 3.0,
        "age_1h_bars": 4,
    }


def _contact():
    return {
        "contact_age_5m_bars": 0,
        "contact_close": 103.8,
        "contact_strength": "CLOSED_DIRECTIONAL_REACTION",
    }


def _risk(risk=0.55, room=3.8):
    return {
        "sl": 99.0,
        "tp1": 104.0,
        "tp2": 105.0,
        "tp3": 106.0,
        "risk_percent": risk,
        "room_r": room,
    }


def test_long_fibonacci_band_is_618_to_650_retracement():
    zone = fib.fibonacci_zone(_impulse("LONG"))
    assert zone is not None
    assert round(zone["low"], 2) == 103.50
    assert round(zone["high"], 2) == 103.82


def test_short_fibonacci_band_is_symmetric():
    zone = fib.fibonacci_zone(_impulse("SHORT"))
    assert zone is not None
    assert round(zone["low"], 2) == 106.18
    assert round(zone["high"], 2) == 106.50


def test_pivots_require_confirmed_right_hand_bars():
    # The helper intentionally asks for a little more context than the bare
    # mathematical pivot minimum. Keep enough bars while still proving that a
    # late high without two bars on its right cannot be confirmed/repainted in.
    frame = pd.DataFrame({
        "open": [10] * 9,
        "high": [10, 11, 15, 12, 11, 13, 12, 17, 20],
        "low": [9, 8, 7, 8, 9, 8, 9, 8, 7],
        "close": [10] * 9,
        "volume": [100] * 9,
    })
    points = fib._pivot_points(frame)
    # index 2 is confirmed by two bars to its right. The later high at index 7/8
    # cannot become a pivot because two closed right-side bars do not exist.
    assert any(item["type"] == "H" and item["index"] == 2 for item in points)
    assert not any(item["type"] == "H" and item["index"] >= 7 for item in points)


def test_contact_uses_closed_candle_and_ignores_forming_last_bar():
    rows = []
    for i in range(24):
        rows.append({
            "open": 104.5,
            "high": 104.8,
            "low": 104.2,
            "close": 104.4,
            "volume": 100 + i,
        })
    # Second-to-last input candle is the last CLOSED candle after _closed_frame
    # drops the final forming row. It touches the LONG FIB band and closes bullish.
    rows[-1] = {
        "open": 103.55,
        "high": 103.95,
        "low": 103.45,
        "close": 103.85,
        "volume": 180,
    }
    # Forming candle violently breaks down; it must not be used for contact proof.
    rows.append({
        "open": 103.8,
        "high": 103.9,
        "low": 100.0,
        "close": 100.5,
        "volume": 999,
    })
    df = pd.DataFrame(rows)
    zone = {"low": 103.50, "high": 103.82, "mid": 103.66}
    evidence = fib.contact_evidence(df, "LONG", 103.84, zone)
    assert evidence is not None
    assert evidence["contact_strength"] == "CLOSED_DIRECTIONAL_REACTION"


def test_probation_can_promote_only_strict_market_aligned_setup():
    ok, evidence = fib.fibo_contact_qualifies(
        _plan("LONG"),
        _impulse("LONG"),
        _contact(),
        _risk(),
        live_health={"mode": "PROBATION", "reason": "OWN_SAMPLE_WARMUP"},
        survival_health={"mode": "NORMAL", "reason": "OK"},
    )
    assert ok is True
    assert evidence["adjusted_score"] >= fib.PROBATION_MIN_SCORE


def test_halt_cannot_be_bypassed_by_fibonacci_contact():
    ok, evidence = fib.fibo_contact_qualifies(
        _plan("LONG"),
        _impulse("LONG"),
        _contact(),
        _risk(),
        live_health={"mode": "PROBATION"},
        survival_health={"mode": "HALT", "reason": "DAILY_STOP_LIMIT_V2"},
    )
    assert ok is False
    assert evidence["reason"] == "SURVIVAL_HALT"


def test_bad_own_realised_cohort_auto_disables_live_fib_entries():
    trades = {}
    for index in range(8):
        loss = index < 5
        trades[str(index)] = {
            "structure_fibo_contact": True,
            "final_result": "SL" if loss else "TP3",
            "r_result": -1.0 if loss else 1.5,
        }
    health = fib.fib_live_health({"trades": trades})
    assert health["samples"] == 8
    assert health["mode"] == "DISABLED"


def test_positive_realised_cohort_graduates_to_active():
    trades = {}
    for index in range(8):
        loss = index < 3
        trades[str(index)] = {
            "structure_fibo_contact": True,
            "final_result": "SL" if loss else "TP3",
            "r_result": -1.0 if loss else 1.5,
        }
    health = fib.fib_live_health({"trades": trades})
    assert health["samples"] == 8
    assert health["stop_rate"] < fib.MAX_ACTIVE_STOP_RATE
    assert health["average_r"] > fib.MIN_ACTIVE_AVG_R
    assert health["mode"] == "ACTIVE"


def test_loose_risk_is_rejected_even_when_fib_is_clean():
    ok, evidence = fib.fibo_contact_qualifies(
        _plan("LONG"),
        _impulse("LONG"),
        _contact(),
        _risk(risk=0.9, room=4.0),
        live_health={"mode": "ACTIVE"},
        survival_health={"mode": "NORMAL"},
    )
    assert ok is False
    assert evidence["reason"] == "RISK"
