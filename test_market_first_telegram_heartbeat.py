from market_first_telegram_heartbeat import build_message


def test_heartbeat_uses_latest_run_selection_not_cumulative_delivery_count():
    diag = {
        "market": {"regime": "BEAR_STRONG", "preferred_direction": "SHORT"},
        "top_decisions": [{"symbol": "PROSUSDT", "direction": "SHORT", "score": 96}],
    }
    result = build_message(
        {"status": "HEALTHY"},
        diag,
        {"last_run": {"trades_selected": 0}},
    )
    assert "Son taramada seçilen işlem: 0" in result
    assert "PROSUSDT SHORT (96)" in result
    assert "gönderim onayı değildir" in result
    assert "gerçek işlem sinyali: 0" not in result.lower()


def test_heartbeat_missing_runner_state_does_not_claim_zero():
    result = build_message({"status": "UNKNOWN"}, {}, {})
    assert "Son taramada seçilen işlem: bilinmiyor" in result
    assert "uygun yakın aday yok" in result
    assert "Bu bir işlem sinyali değildir" in result
