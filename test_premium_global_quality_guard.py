import premium_global_quality_guard as guard


class Bot:
    def __init__(self):
        self.is_entry_still_valid = lambda signal, price: (True, "base-ok")


def test_pause_blocks_normal_signal(monkeypatch):
    bot = Bot()
    monkeypatch.setattr(
        guard.quality,
        "direction_health",
        lambda *a, **k: {"mode": "PAUSE"},
    )
    monkeypatch.setattr(
        guard.quality,
        "market_outlook_context",
        lambda *a, **k: {"mode": "NORMAL"},
    )
    monkeypatch.setattr(guard.quality, "_record", lambda *a, **k: None)
    guard.install(bot)
    ok, reason = bot.is_entry_still_valid(
        {
            "symbol": "X",
            "direction": "LONG",
            "source": "15M_ENTRY",
            "score": 100,
        },
        1.0,
    )
    assert not ok
    assert "stop kümesi" in reason


def test_tight_allows_high_score(monkeypatch):
    bot = Bot()
    monkeypatch.setattr(
        guard.quality,
        "direction_health",
        lambda *a, **k: {"mode": "TIGHT"},
    )
    monkeypatch.setattr(
        guard.quality,
        "market_outlook_context",
        lambda *a, **k: {"mode": "NORMAL"},
    )
    monkeypatch.setattr(guard.quality, "_record", lambda *a, **k: None)
    guard.install(bot)
    ok, reason = bot.is_entry_still_valid(
        {
            "symbol": "X",
            "direction": "SHORT",
            "source": "15M_ENTRY",
            "score": 96,
        },
        1.0,
    )
    assert ok
    assert reason == "base-ok"


def test_regime_reversal_can_override_pause(monkeypatch):
    bot = Bot()
    monkeypatch.setattr(
        guard.quality,
        "direction_health",
        lambda *a, **k: {"mode": "PAUSE"},
    )
    monkeypatch.setattr(
        guard.quality,
        "market_outlook_context",
        lambda *a, **k: {"mode": "BLOCK"},
    )
    monkeypatch.setattr(guard.quality, "_record", lambda *a, **k: None)
    guard.install(bot)
    ok, reason = bot.is_entry_still_valid(
        {
            "symbol": "X",
            "direction": "LONG",
            "source": "REGIME_TRANSITION_ENTRY",
            "regime_transition_mode": "TREND_REVERSAL",
            "score": 99,
        },
        1.0,
    )
    assert ok
    assert reason == "base-ok"
