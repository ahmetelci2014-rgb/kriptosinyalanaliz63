from market_first_new_listings import prioritize_new_listings


def _row(symbol, volume=500_000):
    return {"symbol": symbol, "quote_volume": volume}


def test_new_symbol_gets_priority_and_stays_prioritized():
    now = 1_000_000
    state = {
        "previous_prices": {"BTCUSDT": 100.0, "OLDUSDT": 1.0},
        "new_listing_tracker_bootstrapped_at": now - 60,
        "new_listing_first_seen": {},
    }
    rows = [_row("BTCUSDT"), _row("OLDUSDT"), _row("NEWUSDT", 150_000)]

    selected, summary = prioritize_new_listings(
        rows,
        state,
        ["OLDUSDT", "BTCUSDT"],
        max_total=4,
        now=now,
    )

    assert selected[0] == "NEWUSDT"
    assert summary["discovered_now"] == ["NEWUSDT"]
    assert summary["active_priority"] == ["NEWUSDT"]

    # On the next run previous_prices already contains it, but the 24h tracker
    # keeps the contract in the priority lane.
    state["previous_prices"]["NEWUSDT"] = 2.0
    selected2, summary2 = prioritize_new_listings(
        rows,
        state,
        ["OLDUSDT", "BTCUSDT"],
        max_total=4,
        now=now + 3600,
    )
    assert selected2[0] == "NEWUSDT"
    assert summary2["discovered_now"] == []
    assert summary2["active_priority"] == ["NEWUSDT"]


def test_fresh_state_bootstraps_without_marking_everything_new():
    state = {"previous_prices": {}}
    rows = [_row("AUSDT"), _row("BUSDT")]

    selected, summary = prioritize_new_listings(
        rows,
        state,
        ["AUSDT", "BUSDT"],
        max_total=4,
        now=2_000_000,
    )

    assert selected == ["AUSDT", "BUSDT"]
    assert summary["discovered_now"] == []
    assert summary["active_priority"] == []
    assert state["new_listing_tracker_bootstrapped_at"] == 2_000_000


def test_too_illiquid_new_symbol_is_not_forced_into_deep_scan():
    state = {
        "previous_prices": {"OLDUSDT": 1.0},
        "new_listing_tracker_bootstrapped_at": 1,
        "new_listing_first_seen": {},
    }
    rows = [_row("OLDUSDT"), _row("TINYUSDT", 20_000)]

    selected, summary = prioritize_new_listings(
        rows,
        state,
        ["OLDUSDT"],
        max_total=4,
        now=3_000_000,
    )

    assert "TINYUSDT" not in selected
    assert summary["discovered_now"] == ["TINYUSDT"]
    assert summary["active_priority"] == []


def test_priority_expires_after_24_hours():
    first_seen = 4_000_000
    state = {
        "previous_prices": {"NEWUSDT": 1.0},
        "new_listing_tracker_bootstrapped_at": first_seen,
        "new_listing_first_seen": {"NEWUSDT": first_seen},
    }
    rows = [_row("NEWUSDT", 500_000)]

    selected, summary = prioritize_new_listings(
        rows,
        state,
        [],
        max_total=4,
        now=first_seen + 25 * 3600,
    )

    assert selected == []
    assert summary["active_priority"] == []
