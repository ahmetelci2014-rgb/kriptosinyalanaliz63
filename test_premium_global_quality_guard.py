import premium_global_quality_guard as guard


class Bot:
    def __init__(self):
        self.is_entry_still_valid = lambda signal, price: (True, "base-ok")


def _normal_context(monkeypatch):
    monkeypatch.setattr(
        guard.quality,
        "direction_health",
        lambda *a, **k: {"mode": "NORMAL"},
    )
    monkeypatch.setattr(
        guard.quality,
        "market_outlook_context",
        lambda *a, **k: {"mode": "NORMAL"},
    )
    monkeypatch.setattr(
        guard,
        "route_performance_context",
        lambda *a, **k: {"mode": "NORMAL", "reason": "OK"},
    )
    monkeypatch.setattr(guard.quality, "_record", lambda *a, **k: None)


def test_pause_blocks_normal_signal(monkeypatch):
    bot = Bot()
    _normal_context(monkeypatch)
    monkeypatch.setattr(
        guard.quality,
        "direction_health",
        lambda *a, **k: {"mode": "PAUSE"},
    )
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
    _normal_context(monkeypatch)
    monkeypatch.setattr(
        guard.quality,
        "direction_health",
        lambda *a, **k: {"mode": "TIGHT"},
    )
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
    _normal_context(monkeypatch)
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


def test_negative_source_is_quarantined(monkeypatch):
    bot = Bot()
    _normal_context(monkeypatch)
    monkeypatch.setattr(
        guard,
        "route_performance_context",
        lambda *a, **k: {
            "mode": "BLOCK",
            "reason": "SOURCE_NEGATIVE_EDGE",
        },
    )
    guard.install(bot)
    ok, reason = bot.is_entry_still_valid(
        {
            "symbol": "X",
            "direction": "LONG",
            "source": "EARLY_BREAKOUT_ENTRY",
            "score": 99,
            "entry": 100.0,
            "tp1": 101.0,
            "sl": 99.0,
        },
        100.0,
    )
    assert not ok
    assert "performans karantinası" in reason


def test_fast_route_late_entry_is_blocked(monkeypatch):
    bot = Bot()
    _normal_context(monkeypatch)
    guard.install(bot)
    ok, reason = bot.is_entry_still_valid(
        {
            "symbol": "X",
            "direction": "LONG",
            "source": "REGIME_TRANSITION_ENTRY",
            "score": 99,
            "entry": 100.0,
            "tp1": 101.0,
            "sl": 99.0,
        },
        100.31,
    )
    assert not ok
    assert "Giriş geç" in reason


def test_signal_already_toward_stop_is_blocked(monkeypatch):
    bot = Bot()
    _normal_context(monkeypatch)
    guard.install(bot)
    ok, reason = bot.is_entry_still_valid(
        {
            "symbol": "X",
            "direction": "SHORT",
            "source": "15M_ENTRY",
            "score": 99,
            "entry": 100.0,
            "tp1": 99.0,
            "sl": 101.0,
        },
        100.26,
    )
    assert not ok
    assert "Giriş bozuldu" in reason


def test_good_geometry_reaches_base_validator(monkeypatch):
    bot = Bot()
    _normal_context(monkeypatch)
    guard.install(bot)
    ok, reason = bot.is_entry_still_valid(
        {
            "symbol": "X",
            "direction": "LONG",
            "source": "15M_ENTRY",
            "score": 95,
            "entry": 100.0,
            "tp1": 101.0,
            "sl": 99.0,
        },
        100.10,
    )
    assert ok
    assert reason == "base-ok"
