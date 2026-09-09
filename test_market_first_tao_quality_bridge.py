from __future__ import annotations

import market_first_profit_quality_v1 as profit_quality
import market_first_tao_quality_bridge as bridge
import market_first_tao_quality_profit_patch as profit_patch


def plan(direction="LONG"):
    long = direction == "LONG"
    return {
        "symbol": "TAOUSDT",
        "direction": direction,
        "status": "PREP",
        "score": 90,
        "current_price": 260.6,
        "zone_low": 259.71,
        "zone_high": 260.23,
        "zone_distance_percent": 0.14,
        "sl": 258.30 if long else 262.90,
        "risk_percent": 0.88,
        "room_r": 5.75,
        "structure_5m": direction,
        "structure_15m": direction,
        "structure_1h": direction,
        "volume_ratio_5m": 0.57,
        "volume_ratio_15m": 0.62,
        "extension_atr_5m": 0.62,
        "market_preferred_direction": None,
    }


def stub_profile_dependencies(monkeypatch, direction="LONG", peak5=4.28, peak15=1.88, micro=(True, "MILD_PULLBACK", {})):
    monkeypatch.setattr(bridge.target_engine, "_two_hour_context", lambda df, price: {"direction": direction})
    monkeypatch.setattr(
        bridge,
        "_recent_volume_peak",
        lambda df, bars: peak5 if bars == bridge.RECENT_VOLUME_BARS_5M else peak15,
    )
    monkeypatch.setattr(bridge, "_micro_state", lambda df, price, side: micro)
    monkeypatch.setattr(
        bridge.profit_quality,
        "_profit_target",
        lambda **kwargs: {
            "profit_target": 269.0 if direction == "LONG" else 252.2,
            "profit_target_percent": 3.22,
            "profit_target_r": 3.65,
            "profit_target_source": "1H direnç" if direction == "LONG" else "1H destek",
            "profit_raw_structure_level": 269.0 if direction == "LONG" else 252.2,
            "profit_raw_structure_percent": 3.22,
            "profit_structure_15m": direction,
            "profit_structure_1h": direction,
            "profit_structure_2h": direction,
        },
    )


def test_tao_like_long_prep_is_promotable(monkeypatch):
    stub_profile_dependencies(monkeypatch, "LONG")
    ok, reason, evidence = bridge.evaluate_profile(
        plan("LONG"), df1m=None, df5m=object(), df15m=object(), df1h=object(), current_price=260.6
    )
    assert ok
    assert reason == "OK"
    assert evidence["structure_2h"] == "LONG"
    assert evidence["recent_peak_volume_5m"] == 4.28
    assert evidence["profit_target_percent"] >= 2.0
    assert evidence["profit_target_r"] >= 2.5


def test_short_profile_is_symmetric(monkeypatch):
    stub_profile_dependencies(monkeypatch, "SHORT")
    p = plan("SHORT")
    p["current_price"] = 260.6
    ok, reason, evidence = bridge.evaluate_profile(
        p, df1m=None, df5m=object(), df15m=object(), df1h=object(), current_price=260.6
    )
    assert ok
    assert reason == "OK"
    assert evidence["structure_2h"] == "SHORT"


def test_bridge_requires_2h_alignment(monkeypatch):
    stub_profile_dependencies(monkeypatch, "SHORT")
    ok, reason, _ = bridge.evaluate_profile(
        plan("LONG"), df1m=None, df5m=object(), df15m=object(), df1h=object(), current_price=260.6
    )
    assert not ok
    assert reason == "2H_ALIGNMENT"


def test_bridge_requires_prior_volume_burst(monkeypatch):
    stub_profile_dependencies(monkeypatch, "LONG", peak5=1.10, peak15=1.88)
    ok, reason, _ = bridge.evaluate_profile(
        plan("LONG"), df1m=None, df5m=object(), df15m=object(), df1h=object(), current_price=260.6
    )
    assert not ok
    assert reason == "NO_RECENT_VOLUME_BURST_5M"


def test_bridge_rejects_strong_opposite_micro(monkeypatch):
    stub_profile_dependencies(monkeypatch, "LONG", micro=(False, "STRONG_OPPOSITE", {"move_5m_percent": -0.75}))
    ok, reason, evidence = bridge.evaluate_profile(
        plan("LONG"), df1m=object(), df5m=object(), df15m=object(), df1h=object(), current_price=260.6
    )
    assert not ok
    assert reason == "MICRO_OPPOSITE"
    assert evidence["micro_state"] == "STRONG_OPPOSITE"


def test_profit_patch_allows_only_tao_volume_exception(monkeypatch):
    # Reset module install state for this isolated regression test.
    monkeypatch.setattr(profit_patch, "_INSTALLED", False)
    monkeypatch.setattr(profit_patch, "_ORIGINAL", None)

    decision = {
        "direction": "LONG",
        "score": 92,
        "risk_percent": 0.88,
        "volume_ratio_5m": 0.57,
        "volume_ratio_1m": 0.57,
        "profit_target_percent": 3.2,
        "profit_target_r": 3.64,
        "market_preferred_direction": None,
        "target_confidence": "YÜKSEK",
        "tao_quality_bridge": True,
        "tao_current_volume_5m": 0.57,
        "tao_recent_peak_volume_5m": 4.28,
        "tao_recent_peak_volume_15m": 1.88,
    }

    baseline_ok, baseline_reason = profit_quality._quality_reason(decision)
    assert not baseline_ok
    assert baseline_reason == "VOLUME_BELOW_0_65"

    original = profit_quality._quality_reason
    profit_patch.install()
    try:
        ok, reason = profit_quality._quality_reason(decision)
        assert ok
        assert reason == "OK"

        non_tao = dict(decision)
        non_tao.pop("tao_quality_bridge")
        ok2, reason2 = profit_quality._quality_reason(non_tao)
        assert not ok2
        assert reason2 == "VOLUME_BELOW_0_65"
    finally:
        monkeypatch.setattr(profit_quality, "_quality_reason", original)


def test_summary_documents_single_final_message_and_no_orders():
    info = bridge.summary()
    assert info["symmetric"] == "LONG_SHORT"
    assert "2H+1H+15M+5M" in info["timeframes"]
    assert info["telegram"] == "SINGLE_FINAL_QUALITY_TRADE_ONLY"
    assert info["exchange_orders"] is False
