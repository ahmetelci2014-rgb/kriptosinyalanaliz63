import json

import market_first_pre_send_guard as guard


def test_broad_major_reversal_blocks_long():
    result = guard.evaluate_pre_send_market(
        "LONG",
        {"BTCUSDT": -0.80, "ETHUSDT": -0.55, "SOLUSDT": -0.20},
    )
    assert result["blocked"] is True
    assert result["reason"] == "FRESH_MAJOR_REVERSAL"
    assert result["directional_alignment_percent"] < -0.45
    assert result["opposing_major_count"] >= 2


def test_broad_major_rally_blocks_short():
    result = guard.evaluate_pre_send_market(
        "SHORT",
        {"BTCUSDT": 0.75, "ETHUSDT": 0.45, "SOLUSDT": 0.30},
    )
    assert result["blocked"] is True
    assert result["directional_alignment_percent"] < 0


def test_normal_pullback_does_not_cancel_trade():
    result = guard.evaluate_pre_send_market(
        "LONG",
        {"BTCUSDT": -0.18, "ETHUSDT": -0.12, "SOLUSDT": 0.05},
    )
    assert result["blocked"] is False
    assert result["reason"] == "MARKET_STILL_ACCEPTABLE"


def test_insufficient_major_data_fails_open():
    result = guard.evaluate_pre_send_market("LONG", {"BTCUSDT": -1.20})
    assert result["blocked"] is False
    assert result["reason"] == "INSUFFICIENT_FRESH_MAJOR_DATA"


def test_btc_shock_can_block_with_weighted_confirmation():
    result = guard.evaluate_pre_send_market(
        "LONG",
        {"BTCUSDT": -0.85, "ETHUSDT": -0.18, "SOLUSDT": -0.10},
    )
    assert result["blocked"] is True
    assert result["reason"] == "FRESH_BTC_SHOCK"


def test_shadow_rejection_is_audited_without_entering_ml(tmp_path, monkeypatch):
    shadow_file = tmp_path / "guard-shadow.json"
    monkeypatch.setattr(guard, "SHADOW_FILE", str(shadow_file))
    now = 1_800_000_000
    signal = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 101.0,
        "tp2": 102.0,
        "tp3": 103.0,
        "score": 90,
        "market_regime": "BULL",
        "market_label": "YUKARI",
    }
    decision = guard.evaluate_pre_send_market(
        "LONG",
        {"BTCUSDT": -0.80, "ETHUSDT": -0.55, "SOLUSDT": -0.20},
    )
    guard.register_shadow_rejection(signal, decision, now)
    assert shadow_file.exists()

    summary = guard.update_shadow_results(
        {"TESTUSDT": {"price": 101.2}},
        now + 300,
    )
    assert summary["OBSERVED_TP1"] == 1
    assert summary["OBSERVED_SL"] == 0
    assert summary["observed_tp1_rate"] == 1.0

    saved = json.loads(shadow_file.read_text(encoding="utf-8"))
    item = next(iter(saved["items"].values()))
    assert item["status"] == "OBSERVED_TP1"
    assert "guard" in item
