from datetime import datetime

import premium_quality_layer as q


def ts(y=2026, m=8, d=24, hh=18, mm=0, ss=0):
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=q.TR_TIMEZONE).timestamp())


def perf_with(direction="LONG", results=None, now=None):
    now = now or ts()
    results = results or []
    rows = []
    base_min = 30
    for i, result in enumerate(results):
        minute = base_min + i * 5
        rows.append({
            "time": f"17:{minute:02d}:00",
            "symbol": f"T{i}",
            "direction": direction,
            "result": result,
            "source": "EARLY_BREAKOUT_ENTRY",
        })
    return {
        "days": {
            q.tr_day_key(now): {
                "closed_history": rows,
                "direction_stops": {
                    direction: sum(result == "SL" for result in results)
                },
            }
        }
    }


def outlook(direction="DOWN", now=None, confidence=80, long=2, short=8):
    now = now or ts()
    return {
        "snapshots": [
            {
                "ts": now - 600,
                "outlook": {
                    "direction_6h": direction,
                    "direction_24h": direction,
                    "confidence_6h": confidence,
                    "confidence_24h": confidence,
                    "long_suitability": long,
                    "short_suitability": short,
                },
            }
        ]
    }


def base(direction="LONG", room=1.3, ext_atr=0.2):
    signal = 1.0
    atr = 0.01
    local_high = signal - ext_atr * atr if direction == "LONG" else signal + 0.05
    local_low = signal + 0.05 if direction == "LONG" else signal + ext_atr * atr
    return {
        "direction": direction,
        "stage": "TRIGGER",
        "score": 96,
        "opposite_score": 50,
        "entry": 1.0,
        "features": {
            "signal_price": signal,
            "atr5": atr,
            "local_high": local_high,
            "local_low": local_low,
            "room_long_r": room,
            "room_short_r": room,
            "volume_ratio": 2.1,
            "volume_wake": 1.4,
        },
        "conditions": {
            "liquidity_sweep": True,
            "structure_hold": True,
        },
    }


def candidate(direction="LONG", flow=57, confirmed=False):
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "score": 100,
        "risk_percent": 1.0,
        "volume_ratio": 2.1,
        "early_breakout_stage": "TRIGGER",
        "early_breakout_base_score": 96,
        "early_breakout_opposite_score": 50,
        "early_breakout_core_count": 7,
        "early_breakout_flow_score": flow,
        "early_breakout_flow_confirmed": confirmed,
        "early_breakout_four_hour_ok": True,
        "early_breakout_exceptional": False,
        "entry_reason": "test",
    }


def test_direction_pause_cluster():
    now = ts()
    performance = perf_with(results=["SL", "SL", "SL", "SL"], now=now)
    health = q.direction_health("LONG", now, performance)
    assert health["mode"] == "PAUSE"


def test_market_block():
    now = ts()
    market = q.market_outlook_context("LONG", now, outlook("DOWN", now))
    assert market["mode"] == "BLOCK"


def test_weak_flow_rejected_by_calibration():
    now = ts()
    allowed, reason, evidence = q.assess_early_candidate(
        candidate(flow=26),
        base(),
        now,
        performance={"days": {}},
        outlook={},
    )
    assert allowed is None
    assert reason == "CALIBRATED_SCORE_LOW"
    assert evidence["calibrated_score"] < q.MIN_EARLY_LIVE_SCORE


def test_strong_flow_kept():
    now = ts()
    allowed, reason, _ = q.assess_early_candidate(
        candidate(flow=57),
        base(),
        now,
        performance={"days": {}},
        outlook={},
    )
    assert reason == "ALLOW"
    assert allowed is not None
    assert allowed["score"] >= q.MIN_EARLY_LIVE_SCORE


def test_room_guard_rejects_late_crowded_entry():
    now = ts()
    allowed, reason, _ = q.assess_early_candidate(
        candidate(),
        base(room=0.4),
        now,
        performance={"days": {}},
        outlook={},
    )
    assert allowed is None
    assert reason == "15M_ROOM_YETERSIZ"


def test_regime_reversal_can_escape_old_outlook():
    now = ts()
    candidate_regime = {
        "symbol": "R",
        "direction": "LONG",
        "score": 99,
        "regime_transition_mode": "TREND_REVERSAL",
    }
    allowed, reason, _ = q.assess_regime_candidate(
        candidate_regime,
        now,
        performance={"days": {}},
        outlook=outlook("DOWN", now),
    )
    assert reason == "ALLOW"
    assert allowed is not None
    assert allowed["score"] == 97
