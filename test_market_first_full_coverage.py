from market_first_full_coverage import expand_full_universe_coverage


def _row(symbol, price=1.0):
    return {"symbol": symbol, "price": price}


def test_preserves_priority_and_fills_rotation_capacity():
    rows = [_row(f"C{i:03d}USDT") for i in range(20)]
    selected = ["C019USDT", "C018USDT", "C017USDT"]
    state = {}

    deep, summary = expand_full_universe_coverage(
        rows,
        state,
        selected,
        max_total=8,
        priority_slots=3,
    )

    assert deep[:3] == selected
    assert len(deep) == 8
    assert len(set(deep)) == 8
    assert summary["priority_kept"] == 3
    assert summary["coverage_added"] == 5


def test_rotation_eventually_covers_entire_universe():
    rows = [_row(f"C{i:03d}USDT") for i in range(23)]
    state = {}
    seen = set()

    for _ in range(6):
        deep, _ = expand_full_universe_coverage(
            rows,
            state,
            ["C022USDT", "C021USDT"],
            max_total=7,
            priority_slots=2,
        )
        seen.update(deep)

    assert seen == {row["symbol"] for row in rows}


def test_major_coins_are_not_duplicated_into_altcoin_rotation():
    rows = [
        _row("BTCUSDT"),
        _row("ETHUSDT"),
        _row("SOLUSDT"),
        _row("AUSDT"),
        _row("BUSDT"),
    ]
    deep, summary = expand_full_universe_coverage(
        rows,
        {},
        [],
        max_total=10,
        priority_slots=0,
    )

    assert deep == ["AUSDT", "BUSDT"]
    assert summary["universe_count"] == 2


def test_small_universe_is_fully_deep_scanned_in_one_run():
    rows = [_row("AUSDT"), _row("BUSDT"), _row("CUSDT")]
    deep, summary = expand_full_universe_coverage(
        rows,
        {},
        ["BUSDT"],
        max_total=128,
        priority_slots=64,
    )

    assert set(deep) == {"AUSDT", "BUSDT", "CUSDT"}
    assert summary["deep_total"] == 3
