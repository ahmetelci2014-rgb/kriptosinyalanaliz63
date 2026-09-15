import market_first_fast_common_gate as common_gate
import market_first_runner as runner


def _fast_signal(score=82):
    return {
        "symbol": "BICOUSDT",
        "direction": "SHORT",
        "score": score,
        "fast_entry": True,
        "entry": 0.018780,
        "sl": 0.019019,
        "tp1": 0.018648,
        "tp2": 0.018541,
        "tp3": 0.018421,
    }


def test_fast_inner_send_is_blocked_until_outer_common_chain_returns(monkeypatch):
    calls = {"real_send": 0, "premature_result": None}

    def real_send(exchange, signal, ml_store):
        calls["real_send"] += 1
        return True

    def legacy_fast_decision(decision):
        signal = _fast_signal()
        # Reproduce the old market_first_live fast path: it tries to send from
        # inside decision_to_signal before later common gates have returned.
        calls["premature_result"] = runner._send_trade(object(), signal, {})
        if calls["premature_result"]:
            return None
        return signal

    monkeypatch.setattr(common_gate, "_INSTALLED", False)
    common_gate._RUN_COUNTS.clear()
    monkeypatch.setattr(runner, "decision_to_signal", legacy_fast_decision)
    monkeypatch.setattr(runner, "_send_trade", real_send)

    common_gate.install()
    approved = runner.decision_to_signal({"fast_entry": True})

    assert calls["premature_result"] is False
    assert calls["real_send"] == 0
    assert approved[common_gate.APPROVAL_FIELD] is True

    assert runner._send_trade(object(), approved, {}) is True
    assert calls["real_send"] == 1


def test_fast_signal_rejected_by_common_chain_never_gets_approval_or_send(monkeypatch):
    calls = {"real_send": 0}

    def real_send(exchange, signal, ml_store):
        calls["real_send"] += 1
        return True

    def common_chain_rejects(decision):
        signal = _fast_signal()
        # Premature inner send is blocked, then a later quality/survival gate
        # rejects the candidate by returning None.
        assert runner._send_trade(object(), signal, {}) is False
        return None

    monkeypatch.setattr(common_gate, "_INSTALLED", False)
    common_gate._RUN_COUNTS.clear()
    monkeypatch.setattr(runner, "decision_to_signal", common_chain_rejects)
    monkeypatch.setattr(runner, "_send_trade", real_send)

    common_gate.install()
    assert runner.decision_to_signal({"fast_entry": True}) is None
    assert calls["real_send"] == 0


def test_non_fast_signals_are_unchanged(monkeypatch):
    calls = {"real_send": 0}
    normal = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "score": 96,
        "fast_entry": False,
    }

    def real_send(exchange, signal, ml_store):
        calls["real_send"] += 1
        return True

    monkeypatch.setattr(common_gate, "_INSTALLED", False)
    common_gate._RUN_COUNTS.clear()
    monkeypatch.setattr(runner, "decision_to_signal", lambda decision: dict(normal))
    monkeypatch.setattr(runner, "_send_trade", real_send)

    common_gate.install()
    result = runner.decision_to_signal({})

    assert result == normal
    assert common_gate.APPROVAL_FIELD not in result
    assert runner._send_trade(object(), result, {}) is True
    assert calls["real_send"] == 1
