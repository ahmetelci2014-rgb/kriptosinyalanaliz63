import pandas as pd

import market_first_opportunity_ledger as ledger


def _plan(direction="LONG", symbol="TESTUSDT", price=100.0):
    if direction == "LONG":
        sl, tp1, tp2, tp3 = 98.0, 102.0, 104.0, 106.0
        zone_low, zone_high = 99.0, 100.0
    else:
        sl, tp1, tp2, tp3 = 102.0, 98.0, 96.0, 94.0
        zone_low, zone_high = 100.0, 101.0
    return {
        "symbol": symbol,
        "direction": direction,
        "status": "PREP",
        "score": 90,
        "current_price": price,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "ideal_entry": 100.0,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_percent": 2.0,
        "room_r": 3.0,
        "structure_5m": direction,
        "structure_15m": direction,
        "structure_1h": direction,
        "volume_ratio_5m": 1.0,
    }


def _bar(high, low):
    return pd.DataFrame([{"high": high, "low": low}])


def test_long_prep_records_tp1_before_real_entry_signal():
    book = ledger.empty_ledger()
    plan = _plan("LONG")
    episode, created = ledger.register_or_refresh_plan(book, plan, 1_000)
    assert created is True
    ledger.mark_prep_sent(book, plan, 1_000)

    ledger.update_symbol_market(book, "TESTUSDT", 102.2, _bar(102.5, 99.4), 1_300)
    assert episode["tp1_at"] == 1_300
    assert episode["first_decisive_event"] == "TP1_FIRST"
    assert episode["tp1_before_entry_signal"] is True
    assert episode["telegram_prep_sent"] is True

    ledger.mark_entry_condition(book, {**plan, "status": "ENTRY"}, 1_350, promoted=True)
    signal = {"symbol": "TESTUSDT", "direction": "LONG", "entry": 102.3, "sent_price": 102.3}
    ledger.mark_entry_send_result(book, signal, True, 1_360)
    assert episode["entry_signal_sent"] is True
    assert episode["entry_signal_sent_at"] > episode["tp1_at"]
    assert ledger.ledger_summary(book)["tp1_before_entry_signal"] == 1


def test_short_prep_is_symmetric():
    book = ledger.empty_ledger()
    plan = _plan("SHORT")
    episode, _ = ledger.register_or_refresh_plan(book, plan, 2_000)
    ledger.update_symbol_market(book, "TESTUSDT", 97.7, _bar(100.4, 97.5), 2_300)
    assert episode["tp1_at"] == 2_300
    assert episode["first_decisive_event"] == "TP1_FIRST"
    assert episode["best_favorable_percent"] > 0
    assert episode["tp1_before_entry_signal"] is True


def test_stop_first_then_recovery_is_not_hidden():
    book = ledger.empty_ledger()
    plan = _plan("LONG")
    episode, _ = ledger.register_or_refresh_plan(book, plan, 3_000)

    ledger.update_symbol_market(book, "TESTUSDT", 98.5, _bar(100.2, 97.8), 3_300)
    assert episode["first_decisive_event"] == "SL_FIRST"
    assert episode["sl_at"] == 3_300

    ledger.update_symbol_market(book, "TESTUSDT", 102.2, _bar(102.4, 98.5), 3_600)
    assert episode["tp1_at"] == 3_600
    assert ledger.ledger_summary(book)["stop_then_recovery"] == 1

    ledger.finalize_expired(book, 3_000 + ledger.TRACK_WINDOW_SECONDS)
    assert episode["resolved"] is True
    assert episode["outcome"] == "SL_FIRST_THEN_TP1_RECOVERY"


def test_same_bar_tp_and_stop_is_ambiguous():
    book = ledger.empty_ledger()
    plan = _plan("LONG")
    episode, _ = ledger.register_or_refresh_plan(book, plan, 4_000)
    ledger.update_symbol_market(book, "TESTUSDT", 100.0, _bar(102.5, 97.5), 4_300)
    assert episode["first_decisive_event"] == "AMBIGUOUS_SAME_5M_BAR"


def test_active_tracking_symbols_are_forced_ahead_of_rotation():
    book = ledger.empty_ledger()
    plan = _plan("LONG", symbol="TRACKUSDT")
    ledger.register_or_refresh_plan(book, plan, 5_000)
    rows = [{"symbol": "AUSDT"}, {"symbol": "BUSDT"}, {"symbol": "TRACKUSDT"}]
    selected = ["AUSDT", "BUSDT"]
    merged = ledger.prioritize_tracking_symbols(
        selected,
        rows,
        book,
        {"plans": {}},
        now=5_100,
        max_total=2,
    )
    assert merged[0] == "TRACKUSDT"
    assert len(merged) == 2
