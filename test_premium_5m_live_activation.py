from types import SimpleNamespace

import premium_core_only_runner as core
import premium_profit_runner as runner
import strategy


def _early_signal():
    return {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "source": "5M_RADAR",
        "signal_class": "TRADE",
        "score": 99,
        "entry": 100.0,
        "tp1": 100.5,
        "tp2": 101.0,
        "tp3": 101.5,
        "sl": 99.0,
    }


def test_core_live_allowlist_is_only_15m_and_replay_proven_5m():
    assert core.LIVE_SOURCE_ALLOWLIST == frozenset({"15M_ENTRY", "5M_RADAR"})


def test_5m_early_trade_bypasses_pending_but_keeps_validator_and_cost(monkeypatch):
    strategy.ENABLE_5M_EARLY_TRADE = True
    calls = {"validator": 0, "pending": 0}

    def validator(signal, current_price):
        calls["validator"] += 1
        return True, "LIVE_VALIDATOR_OK"

    monkeypatch.setattr(runner.profit, "cost_viability", lambda signal: {"ok": True})

    class Pending:
        def evaluate(self, *args, **kwargs):
            calls["pending"] += 1
            raise AssertionError("5M early entry must not be delayed by pending confirmation")

    gate = SimpleNamespace(profiles={"LONG": {"live_allowed": True}})
    wrapped = runner._make_profit_gate(validator, gate, Pending())
    signal = _early_signal()

    ok, reason = wrapped(signal, 100.0)

    assert ok is True
    assert reason == "Premium V4 güçlü direkt giriş"
    assert calls == {"validator": 1, "pending": 0}
    assert signal["premium_confirmation"]["status"] == "EARLY_5M_DIRECT"


def test_5m_early_trade_still_fails_when_live_validator_rejects(monkeypatch):
    strategy.ENABLE_5M_EARLY_TRADE = True
    monkeypatch.setattr(runner.profit, "cost_viability", lambda signal: {"ok": True})

    class Pending:
        def evaluate(self, signal, current_price, original):
            return False, "LIVE_VALIDATOR_REJECT", None

    gate = SimpleNamespace(profiles={"LONG": {"live_allowed": True}})
    wrapped = runner._make_profit_gate(
        lambda signal, current_price: (False, "LIVE_VALIDATOR_REJECT"),
        gate,
        Pending(),
    )

    ok, reason = wrapped(_early_signal(), 100.0)

    assert ok is False
    assert reason == "LIVE_VALIDATOR_REJECT"


def test_legacy_core_gate_blocks_5m_by_default_and_experimental_routes():
    """The old Premium Core runner is retained only as legacy code.

    Its current fail-closed configuration intentionally has no live 5M
    directions. Simple Core V1 is the live workflow, so this regression test
    must verify the legacy gate as it actually behaves instead of expecting an
    obsolete 5M activation.
    """

    def factory(original, gate, pending_gate):
        def wrapped(signal, current_price):
            return True, "BASE_OK"
        return wrapped

    fake = SimpleNamespace(_make_profit_gate=factory)
    core._install_core_only_source_gate(fake)
    wrapped = fake._make_profit_gate(lambda *_: None, object(), object())

    early = _early_signal()
    ok, reason = wrapped(early, 100.0)
    assert ok is False
    assert reason == "CORE_5M_DIRECTION_BLOCK"

    experimental = _early_signal()
    experimental["source"] = "BIG_MOVE_ENTRY"
    ok, reason = wrapped(experimental, 100.0)
    assert ok is False
    assert reason == "CORE_ONLY_SOURCE_BLOCK:BIG_MOVE_ENTRY"
