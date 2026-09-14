from __future__ import annotations

import market_first_profit_lock as lock


def _long_signal():
    return {
        "direction": "LONG",
        "entry": 100.0,
        "sl": 99.0,
        "tp1": 103.0,
        "tp1_hit": False,
    }


def _short_signal():
    return {
        "direction": "SHORT",
        "entry": 100.0,
        "sl": 101.0,
        "tp1": 97.0,
        "tp1_hit": False,
    }


def test_long_requires_1_5r_and_strong_close():
    signal = _long_signal()
    assert lock.candle_can_arm(
        signal,
        {"high": 101.6, "low": 100.2, "close": 100.8},
    ) is True
    assert lock.candle_can_arm(
        signal,
        {"high": 101.6, "low": 100.2, "close": 100.2},
    ) is False


def test_trigger_candle_with_tp1_is_left_to_existing_lifecycle():
    signal = _long_signal()
    assert lock.candle_can_arm(
        signal,
        {"high": 103.1, "low": 100.2, "close": 102.0},
    ) is False


def test_trigger_candle_with_original_sl_is_ambiguous_and_not_armed():
    signal = _long_signal()
    assert lock.candle_can_arm(
        signal,
        {"high": 101.7, "low": 98.9, "close": 100.8},
    ) is False


def test_short_is_symmetric():
    signal = _short_signal()
    assert lock.candle_can_arm(
        signal,
        {"high": 99.8, "low": 98.4, "close": 99.2},
    ) is True


def test_protected_return_to_entry_be_before_tp1():
    signal = _long_signal()
    assert lock.protected_candle_outcome(
        signal,
        {"high": 101.2, "low": 99.9},
    ) == "BE"


def test_same_candle_tp1_and_entry_is_not_guessed():
    signal = _long_signal()
    assert lock.protected_candle_outcome(
        signal,
        {"high": 103.2, "low": 99.9},
    ) == "AMBIGUOUS"


def test_directional_r_uses_original_stop_distance():
    assert lock.directional_r("LONG", 100.0, 99.0, 101.5) == 1.5
    assert lock.directional_r("SHORT", 100.0, 101.0, 98.5) == 1.5
