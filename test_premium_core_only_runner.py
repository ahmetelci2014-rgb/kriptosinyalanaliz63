from types import SimpleNamespace

import premium_core_only_runner as core


def _runner_with_legacy(result=(True, "LEGACY_OK")):
    calls = []

    def factory(original, gate, pending_gate):
        def wrapped(signal, current_price):
            calls.append((signal.get("source"), current_price))
            return result

        return wrapped

    runner = SimpleNamespace(_make_profit_gate=factory)
    return runner, calls


def test_only_15m_entry_reaches_existing_live_gates():
    runner, calls = _runner_with_legacy()
    core._install_core_only_source_gate(runner)
    wrapped = runner._make_profit_gate(lambda *_: None, object(), object())

    signal = {"symbol": "BTCUSDT", "direction": "LONG", "source": "15M_ENTRY"}
    ok, reason = wrapped(signal, 100.0)

    assert ok is True
    assert reason == "LEGACY_OK"
    assert calls == [("15M_ENTRY", 100.0)]
    assert signal["core_only_live_gate"]["decision"] == "ALLOW"


def test_5m_long_is_quarantined_by_default(monkeypatch):
    monkeypatch.delenv("PREMIUM_5M_LIVE_SYMBOLS", raising=False)
    monkeypatch.delenv("PREMIUM_5M_LIVE_DIRECTIONS", raising=False)
    runner, calls = _runner_with_legacy()
    core._install_core_only_source_gate(runner)
    wrapped = runner._make_profit_gate(lambda *_: None, object(), object())

    signal = {"symbol": "BTCUSDT", "direction": "LONG", "source": "5M_RADAR"}
    ok, reason = wrapped(signal, 100.0)

    assert ok is False
    assert reason == "CORE_5M_DIRECTION_BLOCK:LONG"
    assert calls == []
    assert signal["core_only_live_gate"]["decision"] == "BLOCK_5M_DIRECTION_QUARANTINE"
    assert "BTCUSDT" in signal["core_only_live_gate"]["allowed_5m_symbols"]
    assert signal["core_only_live_gate"]["allowed_5m_directions"] == []


def test_5m_short_is_quarantined_by_default(monkeypatch):
    monkeypatch.delenv("PREMIUM_5M_LIVE_SYMBOLS", raising=False)
    monkeypatch.delenv("PREMIUM_5M_LIVE_DIRECTIONS", raising=False)
    runner, calls = _runner_with_legacy()
    core._install_core_only_source_gate(runner)
    wrapped = runner._make_profit_gate(lambda *_: None, object(), object())

    signal = {"symbol": "BTCUSDT", "direction": "SHORT", "source": "5M_RADAR"}
    ok, reason = wrapped(signal, 100.0)

    assert ok is False
    assert reason == "CORE_5M_DIRECTION_BLOCK:SHORT"
    assert calls == []
    assert signal["core_only_live_gate"]["decision"] == "BLOCK_5M_DIRECTION_QUARANTINE"
    assert signal["core_only_live_gate"]["allowed_5m_directions"] == []


def test_non_replay_5m_symbol_is_blocked_before_direction_gate(monkeypatch):
    monkeypatch.delenv("PREMIUM_5M_LIVE_SYMBOLS", raising=False)
    monkeypatch.delenv("PREMIUM_5M_LIVE_DIRECTIONS", raising=False)
    runner, calls = _runner_with_legacy()
    core._install_core_only_source_gate(runner)
    wrapped = runner._make_profit_gate(lambda *_: None, object(), object())

    signal = {"symbol": "TESTUSDT", "direction": "LONG", "source": "5M_RADAR"}
    ok, reason = wrapped(signal, 1.0)

    assert ok is False
    assert reason == "CORE_5M_REPLAY_UNIVERSE_BLOCK:TESTUSDT"
    assert calls == []
    assert signal["core_only_live_gate"]["decision"] == "BLOCK_5M_UNVALIDATED_UNIVERSE"
    assert signal["core_only_live_gate"]["allowed_5m_symbols"] == sorted(
        core.REPLAY_PROVEN_5M_SYMBOLS
    )


def test_5m_direction_can_be_reenabled_only_by_explicit_validated_override(monkeypatch):
    monkeypatch.setenv("PREMIUM_5M_LIVE_SYMBOLS", "btcusdt;ethusdt")
    monkeypatch.setenv("PREMIUM_5M_LIVE_DIRECTIONS", "long")
    runner, calls = _runner_with_legacy()
    core._install_core_only_source_gate(runner)
    wrapped = runner._make_profit_gate(lambda *_: None, object(), object())

    allowed = {"symbol": "BTCUSDT", "direction": "LONG", "source": "5M_RADAR"}
    ok, reason = wrapped(allowed, 10.0)
    assert ok is True
    assert reason == "LEGACY_OK"

    blocked_direction = {"symbol": "ETHUSDT", "direction": "SHORT", "source": "5M_RADAR"}
    ok, reason = wrapped(blocked_direction, 10.0)
    assert ok is False
    assert reason == "CORE_5M_DIRECTION_BLOCK:SHORT"

    blocked_symbol = {"symbol": "SOLUSDT", "direction": "LONG", "source": "5M_RADAR"}
    ok, reason = wrapped(blocked_symbol, 10.0)
    assert ok is False
    assert reason == "CORE_5M_REPLAY_UNIVERSE_BLOCK:SOLUSDT"

    assert calls == [("5M_RADAR", 10.0)]


def test_experimental_sources_are_blocked_before_direct_or_legacy_gate():
    for source in (
        "BIG_MOVE_ENTRY",
        "EARLY_BREAKOUT_ENTRY",
        "REGIME_TRANSITION_ENTRY",
        "TREND_CONTINUATION_ENTRY",
        "YOUNG_COIN_ENTRY",
        "NEW_COIN_ENTRY",
    ):
        runner, calls = _runner_with_legacy()
        core._install_core_only_source_gate(runner)
        wrapped = runner._make_profit_gate(lambda *_: None, object(), object())
        signal = {"symbol": "TESTUSDT", "direction": "SHORT", "source": source}

        ok, reason = wrapped(signal, 1.0)

        assert ok is False
        assert reason == f"CORE_ONLY_SOURCE_BLOCK:{source}"
        assert calls == []
        assert signal["core_only_live_gate"]["decision"] == "BLOCK"


def test_missing_source_fails_closed():
    runner, calls = _runner_with_legacy()
    core._install_core_only_source_gate(runner)
    wrapped = runner._make_profit_gate(lambda *_: None, object(), object())
    signal = {"symbol": "TESTUSDT", "direction": "LONG"}

    ok, reason = wrapped(signal, 1.0)

    assert ok is False
    assert reason == "CORE_ONLY_SOURCE_BLOCK:UNKNOWN"
    assert calls == []


def test_existing_core_rejection_is_preserved():
    runner, calls = _runner_with_legacy((False, "ENTRY_TOO_LATE"))
    core._install_core_only_source_gate(runner)
    wrapped = runner._make_profit_gate(lambda *_: None, object(), object())
    signal = {"symbol": "ETHUSDT", "direction": "SHORT", "source": "15m_entry"}

    ok, reason = wrapped(signal, 200.0)

    assert ok is False
    assert reason == "ENTRY_TOO_LATE"
    assert calls == [("15m_entry", 200.0)]
    assert signal["core_only_live_gate"]["decision"] == "CORE_REJECTED_BY_EXISTING_GATES"
