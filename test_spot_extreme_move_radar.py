from spot_extreme_move_radar import (
    _active_usdt_spots,
    classify_setup,
    should_alert,
)


def test_spot_only_market_is_kept_even_without_perpetual():
    markets = {
        "LSK/USDT": {
            "active": True,
            "spot": True,
            "swap": False,
            "contract": False,
            "base": "LSK",
            "quote": "USDT",
            "symbol": "LSK/USDT",
        },
        "BTC/USDT": {
            "active": True,
            "spot": True,
            "swap": False,
            "contract": False,
            "base": "BTC",
            "quote": "USDT",
            "symbol": "BTC/USDT",
        },
        "BTC/USDT:USDT": {
            "active": True,
            "spot": False,
            "swap": True,
            "contract": True,
            "linear": True,
            "base": "BTC",
            "quote": "USDT",
            "settle": "USDT",
            "expiry": None,
            "symbol": "BTC/USDT:USDT",
        },
    }
    spots = _active_usdt_spots(markets)
    assert spots["LSKUSDT"]["perpetual_available"] is False
    assert spots["BTCUSDT"]["perpetual_available"] is True


def test_lsk_like_early_ignition_qualifies():
    setup = classify_setup(
        sample_move_percent=2.4,
        change_24h_percent=11.0,
        move_1m_percent=0.55,
        move_5m_percent=3.1,
        move_15m_percent=6.2,
        volume_ratio_5m=3.4,
    )
    assert setup is not None
    assert setup["direction"] == "PUMP"
    assert setup["phase"] == "EARLY"


def test_extreme_move_escalates_phase():
    setup = classify_setup(
        sample_move_percent=5.0,
        change_24h_percent=42.0,
        move_1m_percent=1.1,
        move_5m_percent=6.0,
        move_15m_percent=15.0,
        volume_ratio_5m=5.0,
    )
    assert setup is not None
    assert setup["phase"] == "EXTREME"


def test_large_24h_move_without_current_confirmation_is_rejected():
    setup = classify_setup(
        sample_move_percent=0.2,
        change_24h_percent=90.0,
        move_1m_percent=-1.0,
        move_5m_percent=-2.0,
        move_15m_percent=4.0,
        volume_ratio_5m=4.0,
    )
    assert setup is None


def test_extreme_escalation_can_bypass_cooldown_once():
    state = {
        "alerts": {
            "LSKUSDT:PUMP": {
                "at": 1000,
                "phase": "EARLY",
            }
        }
    }
    assert should_alert(
        state,
        "LSKUSDT",
        {"direction": "PUMP", "phase": "EXTREME"},
        1100,
    ) is True


def test_same_phase_respects_cooldown():
    state = {
        "alerts": {
            "LSKUSDT:PUMP": {
                "at": 1000,
                "phase": "EARLY",
            }
        }
    }
    assert should_alert(
        state,
        "LSKUSDT",
        {"direction": "PUMP", "phase": "EARLY"},
        1100,
    ) is False
