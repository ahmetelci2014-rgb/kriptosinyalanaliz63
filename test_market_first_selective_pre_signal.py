import market_first_selective_pre_signal as lane


def _base_decision():
    return {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "stage": "READY",
        "trade_eligible": True,
        "entry_plan_trade": True,
        "entry_plan_ideal_entry": 10.0,
        "entry_plan_zone_low": 9.98,
        "entry_plan_zone_high": 10.02,
        "current_price": 10.0,
        "score": 94,
        "risk_percent": 0.80,
        "expected_move_percent": 2.40,
        "technical_target_r": 4.0,
        "profit_target_percent": 0.0,
        "profit_target_r": 0.0,
        "volume_ratio_5m": 1.10,
        "volume_ratio_1m": 1.10,
        "market_preferred_direction": "LONG",
        "market_label": "YUKARI",
        "structure_15m": "LONG",
        "structure_1h": "LONG",
        "target_confidence": "YÜKSEK",
        "taker_available": True,
        "cvd_available": True,
        "book_available": True,
        "derivatives_available": True,
        "taker_imbalance_alignment": 0.30,
        "cvd_ratio": 0.30,
        "cvd_impulse_alignment": 0.0,
        "book_imbalance_alignment": 0.0,
        "derivatives_soft_score": 1,
        "direction_engine": {
            "selected_direction": "LONG",
            "selected_score": 96,
            "other_score": 10,
            "margin": 86,
            "confirmations": 3,
            "confirmation_flags": {
                "fresh_micro": True,
            },
            "long": {
                "taker_alignment": 0.30,
                "cvd_alignment": 0.30,
            },
        },
    }


def test_strong_entry_with_one_soft_profit_block_becomes_pre_signal():
    decision = _base_decision()

    ok, reason, evidence = lane.evaluate_near_signal(decision)

    assert ok is True
    assert reason == "EXPECTED_MOVE_BELOW_2P"
    assert evidence["score"] == 94
    assert evidence["technical_target_r"] == 4.0


def test_market_opposite_never_becomes_pre_signal():
    decision = _base_decision()
    decision["market_preferred_direction"] = "SHORT"

    ok, reason, _ = lane.evaluate_near_signal(decision)

    assert ok is False
    assert reason == "MARKET_OPPOSITE"


def test_soft_execution_wait_can_be_pre_signal_when_profit_quality_passes():
    decision = _base_decision()
    decision["profit_target_percent"] = 2.4
    decision["profit_target_r"] = 4.0
    decision["direction_engine"]["confirmation_flags"]["fresh_micro"] = False
    decision["taker_imbalance_alignment"] = 0.05
    decision["direction_engine"]["long"]["taker_alignment"] = 0.05

    ok, reason, _ = lane.evaluate_near_signal(decision)

    assert ok is True
    assert reason == "NO_FRESH_MICRO_TAKER_WEAK"


def test_small_expected_move_is_not_shown():
    decision = _base_decision()
    decision["expected_move_percent"] = 0.80
    decision["technical_target_r"] = 1.3

    ok, reason, _ = lane.evaluate_near_signal(decision)

    assert ok is False
    assert reason == "EXPECTED_MOVE"
