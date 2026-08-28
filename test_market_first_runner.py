from market_first_runner import _select_deep_scan


def test_deep_scan_keeps_rotation_and_major_coins_out_of_candidate_slots():
    rows = [
        {"symbol": "BTCUSDT", "quote_volume": 1_000_000_000, "change_24h": 1.0},
        {"symbol": "ETHUSDT", "quote_volume": 800_000_000, "change_24h": 1.0},
        {"symbol": "SOLUSDT", "quote_volume": 500_000_000, "change_24h": 1.0},
    ]
    rows.extend(
        {
            "symbol": f"ALT{i}USDT",
            "quote_volume": 10_000_000 - i * 1000,
            "change_24h": float((i % 7) - 3),
        }
        for i in range(50)
    )

    sample_moves = {
        f"ALT{i}USDT": (i - 25) / 10.0
        for i in range(50)
    }
    state = {"rotation_cursor": 0}

    selected = _select_deep_scan(rows, sample_moves, state)

    assert selected
    assert len(selected) <= 40
    assert "BTCUSDT" not in selected
    assert "ETHUSDT" not in selected
    assert "SOLUSDT" not in selected
    assert state["rotation_cursor"] > 0
