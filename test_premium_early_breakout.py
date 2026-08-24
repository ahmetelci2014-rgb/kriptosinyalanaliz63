from __future__ import annotations

import premium_early_breakout as early


def base(stage="PREP", score=75, opposite=35, four=False, volume=1.60, wake=1.40):
    return {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "stage": stage,
        "score": score,
        "opposite_score": opposite,
        "entry": 1.0,
        "stop": 0.992,
        "risk_abs": 0.008,
        "risk_percent": 0.8,
        "features": {"volume_ratio": volume, "volume_wake": wake},
        "conditions": {
            "squeeze": True,
            "structure_hold": True,
            "internal_break": stage == "TRIGGER",
            "volume_wake": wake >= 1.08,
            "volume_confirm": volume >= 1.20,
            "ema_turn": True,
            "rsi_turn": True,
            "close_power": True,
            "fifteen_not_opposing": True,
            "one_hour_not_opposing": True,
            "four_hour_not_opposing": four,
            "risk_quality": True,
            "structure_room": False,
        },
    }


def setup_function():
    early.begin("/tmp/early_breakout_test_state.json")


def test_nes_like_prep_promotes_without_4h():
    sig = early.analyze_live_candidate(
        "TESTUSDT", base(), 1.0, allow_extra_flow=False, now_ts=1000
    )
    assert sig is not None
    assert sig["source"] == early.SOURCE
    assert sig["score"] >= 91
    assert sig["early_breakout_exceptional"] is True


def test_weak_prep_rejected():
    sig = early.analyze_live_candidate(
        "TESTUSDT",
        base(volume=1.05, wake=1.0),
        1.0,
        allow_extra_flow=False,
        now_ts=1000,
    )
    assert sig is None


def test_armed_confirmed_flow_promotes():
    snap = {
        "orderflow_score": 78,
        "orderflow_confirmed": True,
        "flow": {"spread_bps": 4},
        "conditions": {"spread_ok": True},
    }
    sig = early.analyze_live_candidate(
        "TESTUSDT",
        base(stage="ARMED", score=78, opposite=50, four=True),
        1.0,
        snap,
        allow_extra_flow=False,
        now_ts=1000,
    )
    assert sig is not None
    assert sig["early_breakout_flow_confirmed"] is True


def test_bad_flow_vetoes_armed():
    snap = {
        "orderflow_score": 5,
        "orderflow_confirmed": False,
        "flow": {"spread_bps": 4},
        "conditions": {"spread_ok": True},
    }
    sig = early.analyze_live_candidate(
        "TESTUSDT",
        base(stage="ARMED", score=84, opposite=40, four=True),
        1.0,
        snap,
        allow_extra_flow=False,
        now_ts=1000,
    )
    assert sig is None


def test_trigger_promotes_when_structure_strong():
    sig = early.analyze_live_candidate(
        "TESTUSDT",
        base(stage="TRIGGER", score=92, opposite=50, four=True),
        1.0,
        allow_extra_flow=False,
        now_ts=1000,
    )
    assert sig is not None


def test_late_move_rejected():
    sig = early.analyze_live_candidate(
        "TESTUSDT",
        base(stage="TRIGGER", score=95, opposite=40, four=True),
        1.01,
        allow_extra_flow=False,
        now_ts=1000,
    )
    assert sig is None


def test_direct_gate_requires_cost_and_entry():
    sig = early.analyze_live_candidate(
        "TESTUSDT", base(), 1.0, allow_extra_flow=False, now_ts=1000
    )

    class Profit:
        @staticmethod
        def cost_viability(signal):
            return {"ok": True}

    assert early.strong_direct_allowed(sig, 1.0, lambda s, p: (True, "ok"), Profit)
    assert not early.strong_direct_allowed(sig, 1.0, lambda s, p: (False, "bad"), Profit)


def test_message_identifies_early_route():
    sig = early.analyze_live_candidate(
        "TESTUSDT", base(), 1.0, allow_extra_flow=False, now_ts=1000
    )
    builder = early.make_trade_message_builder(lambda *a, **k: "legacy")
    text = builder(sig, current_price=1.0, portfolio_risk={})
    assert text.startswith("✅ İŞLEM GİRİŞİ — PREMIUM ERKEN HAREKET")
    assert "V2" in text
