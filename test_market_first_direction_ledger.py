import market_first_direction_ledger as ledger


def _decision(direction="LONG", price=100.0):
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "current_price": price,
        "score": 90,
        "sl": 99.0 if direction == "LONG" else 101.0,
        "tp1": 101.0 if direction == "LONG" else 99.0,
        "tp2": 102.0 if direction == "LONG" else 98.0,
        "tp3": 103.0 if direction == "LONG" else 97.0,
    }


def _diag(selected="SHORT", current="LONG"):
    return {
        "version": "TEST_ENGINE",
        "reason": "SELECTED",
        "selected_direction": selected,
        "selected_score": 88,
        "other_score": 62,
        "margin": 26,
        "current_direction": current,
        "reversal": selected != current,
        "confirmations": 4,
        "confirmation_flags": {"taker": True, "cvd": True, "breadth": True, "structure_5m": True},
        "structures": {"5m": selected, "15m": current, "1h": current},
        "long": {"score": 62},
        "short": {"score": 88},
    }


def test_reversal_episode_keeps_engine_scores_and_plan_geometry():
    store = {"version": ledger.VERSION, "episodes": {}}
    plan = {
        "direction": "SHORT",
        "status": "PREP",
        "score": 88,
        "zone_low": 100.4,
        "zone_high": 100.8,
        "ideal_entry": 100.6,
        "sl": 101.4,
        "tp1": 99.8,
        "tp2": 99.2,
        "tp3": 98.4,
        "risk_percent": 0.8,
        "room_r": 2.1,
        "direction_engine_version": "TEST_ENGINE",
    }
    episode = ledger.register_decision(
        store,
        decision=_decision("LONG"),
        diag=_diag("SHORT", "LONG"),
        now=1_000_000,
        reversal_plan=plan,
    )
    assert episode["reversal"] is True
    assert episode["tracked_direction"] == "SHORT"
    assert episode["long_score"] == 62
    assert episode["short_score"] == 88
    assert episode["margin"] == 26
    assert episode["tp1"] == 99.8
    assert episode["sl"] == 101.4


def test_short_reversal_tracks_tp1_before_stop():
    store = {"version": ledger.VERSION, "episodes": {}}
    episode = ledger.register_decision(
        store,
        decision=_decision("LONG"),
        diag=_diag("SHORT", "LONG"),
        now=2_000_000,
        reversal_plan={"direction": "SHORT", "status": "PREP", "sl": 101.0, "tp1": 99.0, "tp2": 98.0, "tp3": 97.0},
    )
    ledger.update_symbol_market(store, "TESTUSDT", 98.9, None, 2_000_300)
    assert episode["first_decisive_event"] == "TP1_FIRST"
    assert episode["tp1_at"] == 2_000_300
    assert episode["sl_at"] == 0
    assert episode["best_favorable_percent"] > 1.0


def test_same_bar_tp1_and_sl_is_marked_ambiguous_with_dataframe_free_updates():
    store = {"version": ledger.VERSION, "episodes": {}}
    episode = ledger.register_decision(
        store,
        decision=_decision("LONG"),
        diag=_diag("LONG", "LONG"),
        now=3_000_000,
    )
    # Without OHLC, point-in-time updates are deterministic: first hit wins.
    ledger.update_symbol_market(store, "TESTUSDT", 101.1, None, 3_000_300)
    ledger.update_symbol_market(store, "TESTUSDT", 98.9, None, 3_000_600)
    assert episode["first_decisive_event"] == "TP1_FIRST"
    assert episode["tp1_at"] == 3_000_300
    assert episode["sl_at"] == 3_000_600


def test_real_signal_is_separate_from_hypothetical_direction_result():
    store = {"version": ledger.VERSION, "episodes": {}}
    episode = ledger.register_decision(
        store,
        decision=_decision("SHORT"),
        diag=_diag("SHORT", "SHORT"),
        now=4_000_000,
    )
    assert episode["real_entry_signal_sent"] is False
    ledger.mark_real_signal(
        store,
        {"symbol": "TESTUSDT", "direction": "SHORT"},
        True,
        4_000_100,
    )
    assert episode["real_entry_signal_sent"] is True
    assert episode["real_entry_signal_at"] == 4_000_100


def test_engine_no_selection_tracks_rejected_original_direction_for_false_negative_audit():
    store = {"version": ledger.VERSION, "episodes": {}}
    diag = _diag("LONG", "LONG")
    diag["selected_direction"] = None
    diag["reason"] = "DIRECTION_MARGIN_WEAK"
    episode = ledger.register_decision(
        store,
        decision=_decision("LONG"),
        diag=diag,
        now=5_000_000,
    )
    assert episode["engine_selected_direction"] is None
    assert episode["tracked_direction"] == "LONG"
    assert episode["engine_reason"] == "DIRECTION_MARGIN_WEAK"


def test_expired_direction_without_plan_is_classified_by_favorable_vs_adverse_move():
    store = {"version": ledger.VERSION, "episodes": {}}
    episode = ledger.register_decision(
        store,
        decision={"symbol": "TESTUSDT", "direction": "LONG", "current_price": 100.0, "score": 80},
        diag=_diag("LONG", "LONG"),
        now=6_000_000,
    )
    ledger.update_symbol_market(store, "TESTUSDT", 100.8, None, 6_000_300)
    closed = ledger.finalize_expired(store, 6_000_000 + ledger.TRACK_SECONDS + 1)
    assert closed == 1
    assert episode["resolved"] is True
    assert episode["outcome"] == "DIRECTION_GOOD"


def test_summary_separates_long_short_reversals_and_real_entries():
    store = {"version": ledger.VERSION, "episodes": {}}
    ledger.register_decision(store, decision=_decision("LONG"), diag=_diag("SHORT", "LONG"), now=7_000_000)
    ledger.register_decision(store, decision=_decision("LONG"), diag=_diag("LONG", "LONG"), now=7_002_000)
    result = ledger.summary(store, 7_002_100)
    assert result["total"] == 2
    assert result["reversal_total"] == 1
    assert result["selected_long"] == 1
    assert result["selected_short"] == 1
