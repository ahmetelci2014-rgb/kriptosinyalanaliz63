import market_structure_early_alerts as alerts


def _event(stage="WATCH", score=70, opposite=40, distance_atr=1.1):
    conditions = {
        "structure_shift": True,
        "zone_touch": True,
        "sweep_reclaim": False,
        "double_extreme": True,
        "trendline_break": True,
        "choch": False,
        "bos": False,
        "volume_wake": False,
        "impulse": False,
        "ema_turn": True,
        "fifteen_not_opposing": True,
    }
    return {
        "event": "NEW" if stage == "WATCH" else "UPGRADE",
        "result": {
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "stage": stage,
            "score": score,
            "opposite_score": opposite,
            "origin_distance_atr": distance_atr,
            "origin_distance_percent": 0.72,
            "conditions": conditions,
        },
        "record": {
            "symbol": "TESTUSDT",
            "direction": "LONG",
            "stage": stage,
            "score": score,
            "opposite_score": opposite,
            "origin": 1.0,
            "entry": 1.0072,
            "origin_distance_atr": distance_atr,
            "origin_distance_percent": 0.72,
            "stop": 0.995,
            "target_2r": 1.0316,
            "started_at": 123456,
            "conditions": conditions,
        },
    }


def test_strong_watch_is_alertable():
    ok, reason = alerts.should_alert(_event())
    assert ok is True
    assert reason == "WATCH_EARLY_STRUCTURE"


def test_weak_watch_is_not_alertable():
    ok, reason = alerts.should_alert(_event(score=58))
    assert ok is False
    assert reason == "WATCH_SCORE_LOW"


def test_far_watch_is_not_alertable():
    ok, reason = alerts.should_alert(_event(distance_atr=2.2))
    assert ok is False
    assert reason == "WATCH_TOO_FAR_FROM_ORIGIN"


def test_ready_is_alertable():
    ok, reason = alerts.should_alert(_event(stage="READY", score=82, opposite=45, distance_atr=2.1))
    assert ok is True
    assert reason == "READY_STRUCTURE_CONFIRMED"


def test_message_is_clearly_not_a_trade_signal():
    text = alerts.build_message(_event())
    assert "İŞLEM DEĞİL" in text
    assert "TESTUSDT" in text
    assert "LONG" in text
    assert "Origin" in text


def test_watch_and_ready_have_different_delivery_keys():
    watch = alerts.delivery_key(_event(stage="WATCH"))
    ready = alerts.delivery_key(_event(stage="READY", score=82))
    assert watch != ready
    assert "TESTUSDT" in watch
