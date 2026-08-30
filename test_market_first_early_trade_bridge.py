from unittest.mock import patch

import market_first_early_trade_bridge as bridge
import market_first_strategy as strategy


def _context(preferred="LONG", regime="BULL"):
    return strategy.MarketContext(
        regime=regime,
        preferred_direction=preferred,
        score=25.0 if preferred == "LONG" else -25.0,
        strength=25.0,
        breadth_5m=0.55,
        breadth_24h=0.55,
        major_move_5m_percent=0.05,
        allow_countertrend=True,
        majors={},
    )


def _structure(direction, *, atr=0.5, volume=0.8):
    return {
        "direction": direction,
        "atr": atr,
        "extension_atr": 0.8,
        "volume_ratio": volume,
        "swing_low_12": 100.20,
        "swing_high_12": 101.80,
        "range_low_72": 96.0,
        "range_high_72": 106.0,
    }


def test_active_alert_is_forced_back_into_deep_scan():
    now = 1_000_000
    rows = [{"symbol": "AAAUSDT"}, {"symbol": "BICOUSDT"}, {"symbol": "CCCUSDT"}]
    state = {
        "active_alerts": {
            "BICOUSDT:LONG": {
                "symbol": "BICOUSDT",
                "direction": "LONG",
                "status": "CONTINUE",
                "first_at": now - 10 * 60,
                "alert_price": 100.0,
                "score": 70,
            }
        }
    }
    selected, alerts, summary = bridge.prioritize_active_alerts(
        ["AAAUSDT", "CCCUSDT"], rows, state, max_total=2, now=now
    )
    assert selected[0] == "BICOUSDT"
    assert "BICOUSDT" in alerts
    assert len(selected) == 2
    assert summary["active_followthrough_count"] == 1


def test_continuing_alert_can_be_promoted_even_if_current_acceleration_faded():
    now = 1_000_000
    alert = {
        "symbol": "BICOUSDT",
        "direction": "LONG",
        "status": "CONTINUE",
        "first_at": now - 12 * 60,
        "alert_price": 100.0,
        "score": 70,
    }
    s5 = _structure("LONG")
    s15 = _structure("LONG")
    s1h = _structure("LONG")
    with patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]), patch.object(
        strategy, "_acceleration", return_value=None
    ):
        decision, reason, diag = bridge.promote_active_alert(
            None,
            "NO_ACCELERATION",
            alert,
            symbol="BICOUSDT",
            df1m=object(),
            df5m=object(),
            df15m=object(),
            df1h=object(),
            current_price=101.10,
            quote_volume_24h=5_000_000,
            context=_context(),
            now=now,
        )
    assert reason == "OK"
    assert diag["promoted"] is True
    assert decision["trade_eligible"] is True
    assert decision["followthrough_confirmed"] is True
    assert decision["stage"] == "READY"
    assert 0.4 <= decision["risk_percent"] <= 1.8


def test_followthrough_does_not_chase_after_late_threshold():
    now = 1_000_000
    alert = {
        "symbol": "BICOUSDT",
        "direction": "LONG",
        "status": "CONTINUE",
        "first_at": now - 10 * 60,
        "alert_price": 100.0,
        "score": 75,
    }
    decision, reason, diag = bridge.promote_active_alert(
        None,
        "NO_ACCELERATION",
        alert,
        symbol="BICOUSDT",
        df1m=object(),
        df5m=object(),
        df15m=object(),
        df1h=object(),
        current_price=102.50,
        quote_volume_24h=5_000_000,
        context=_context(),
        now=now,
    )
    assert decision is None
    assert diag["promoted"] is False
    assert diag["reason"] == "FOLLOW_TOO_LATE"


def test_strong_opposite_market_still_blocks_followthrough():
    now = 1_000_000
    alert = {
        "symbol": "BICOUSDT",
        "direction": "LONG",
        "status": "CONTINUE",
        "first_at": now - 8 * 60,
        "alert_price": 100.0,
        "score": 74,
    }
    s5 = _structure("LONG")
    s15 = _structure("LONG")
    s1h = _structure("LONG")
    context = strategy.MarketContext(
        regime="BEAR_STRONG",
        preferred_direction="SHORT",
        score=-50.0,
        strength=50.0,
        breadth_5m=0.30,
        breadth_24h=0.30,
        major_move_5m_percent=-0.30,
        allow_countertrend=False,
        majors={},
    )
    with patch.object(strategy, "_structure", side_effect=[s5, s15, s1h]):
        decision, reason, diag = bridge.promote_active_alert(
            None,
            "NO_ACCELERATION",
            alert,
            symbol="BICOUSDT",
            df1m=object(),
            df5m=object(),
            df15m=object(),
            df1h=object(),
            current_price=101.00,
            quote_volume_24h=5_000_000,
            context=context,
            now=now,
        )
    assert decision is None
    assert diag["promoted"] is False
    assert diag["reason"] == "FOLLOW_MARKET_OPPOSED"


def test_followthrough_signal_is_marked_as_confirmation_entry():
    base_signal = {"symbol": "BICOUSDT", "direction": "LONG", "entry_type": "MARKET_FIRST"}
    decision = {
        "followthrough_confirmed": True,
        "followthrough_favorable_percent": 1.1,
        "followthrough_alert_score": 70,
    }
    decorated = bridge.decorate_followthrough_signal(base_signal, decision)
    assert decorated["entry_type"] == "MARKET_FIRST_FOLLOWTHROUGH"
    assert decorated["followthrough_confirmed"] is True
