from __future__ import annotations

from types import SimpleNamespace

import coin_analyzer as analyzer


def _signal(direction="LONG", source="15M_ENTRY", leverage="3x"):
    return {
        "symbol": "TESTUSDT",
        "direction": direction,
        "source": source,
        "signal_class": "TRADE",
        "entry": 100.0,
        "tp1": 101.0 if direction == "LONG" else 99.0,
        "tp2": 102.0 if direction == "LONG" else 98.0,
        "tp3": 103.0 if direction == "LONG" else 97.0,
        "sl": 99.0 if direction == "LONG" else 101.0,
        "risk_percent": 1.0,
        "rr_tp1": 1.0,
        "rr_tp2": 2.0,
        "rr_tp3": 3.0,
        "score": 99,
        "quality": "A+ ANA",
        "leverage": leverage,
    }


def test_normalize_symbol_variants():
    assert analyzer.normalize_symbol("btc") == "BTCUSDT"
    assert analyzer.normalize_symbol("BTC/USDT:USDT") == "BTCUSDT"
    assert analyzer.normalize_symbol("btc-usdt") == "BTCUSDT"


def test_no_legacy_standalone_score_constants():
    assert not hasattr(analyzer, "MIN_TRADE_SCORE")
    assert "PREMIUM_MICROSCOPE" in analyzer.VERSION


def test_recent_stop_blocks_before_candidate():
    decision, reason = analyzer._decision(
        None,
        recent_stop_blocked=True,
        recent_closed_blocked=False,
        direction_allowed=True,
        entry_ok=True,
        entry_reason="ok",
        portfolio={"hard_block": False},
        duplicate=False,
        open_capacity_blocked=False,
    )
    assert decision == "BEKLE"
    assert "stop" in reason.lower()


def test_recent_closed_blocks_without_reversal_exception():
    decision, reason = analyzer._decision(
        _signal(),
        recent_stop_blocked=False,
        recent_closed_blocked=True,
        direction_allowed=True,
        entry_ok=True,
        entry_reason="ok",
        portfolio={"hard_block": False},
        duplicate=False,
        open_capacity_blocked=False,
    )
    assert decision == "BEKLE"
    assert "cooldown" in reason.lower()


def test_portfolio_hard_block_prevents_trade():
    decision, _ = analyzer._decision(
        _signal(),
        recent_stop_blocked=False,
        recent_closed_blocked=False,
        direction_allowed=True,
        entry_ok=True,
        entry_reason="ok",
        portfolio={"hard_block": True, "block_reason": "limit"},
        duplicate=False,
        open_capacity_blocked=False,
    )
    assert decision == "BEKLE"


def test_all_live_coin_gates_allow_direction():
    decision, _ = analyzer._decision(
        _signal("SHORT"),
        recent_stop_blocked=False,
        recent_closed_blocked=False,
        direction_allowed=True,
        entry_ok=True,
        entry_reason="Premium V3 teyitli",
        portfolio={"hard_block": False},
        duplicate=False,
        open_capacity_blocked=False,
    )
    assert decision == "SHORT"


def test_contextual_leverage_never_raises_core():
    assert analyzer._contextual_leverage(
        "2x",
        portfolio={"hard_block": False, "has_soft_warning": False},
        derivatives={"funding": 0.0, "funding_threshold": 0.0005},
        orderflow={"queried": False},
        direction="LONG",
    ) == "2x"


def test_contextual_leverage_caps_crowded_funding():
    assert analyzer._contextual_leverage(
        "3x",
        portfolio={"hard_block": False, "has_soft_warning": False},
        derivatives={"funding": 0.001, "funding_threshold": 0.0005},
        orderflow={"queried": False},
        direction="LONG",
    ) == "1x"


def test_orderflow_does_not_query_without_v2_candidate(monkeypatch):
    called = {"fetch": False}

    def fake_fetch(_symbol):
        called["fetch"] = True
        return {}

    monkeypatch.setattr(analyzer.movement_v3, "fetch_order_flow", fake_fetch)
    result = analyzer._orderflow_context("TESTUSDT", None)
    assert result["queried"] is False
    assert called["fetch"] is False


def test_live_prefilter_blocks_recent_stop(monkeypatch):
    monkeypatch.setattr(analyzer.bot, "has_recent_stop", lambda _symbol: True)
    result = analyzer._live_prefilters("TESTUSDT")
    assert result["recent_stop_blocked"] is True
    assert result["recent_closed_blocked"] is False


def test_live_prefilter_uses_reversal_aware_closed_filter(monkeypatch):
    monkeypatch.setattr(analyzer.bot, "has_recent_stop", lambda _symbol: False)
    monkeypatch.setattr(
        analyzer.reversal,
        "make_recent_closed_prefilter",
        lambda _bot, _original: (lambda _symbol: False),
    )
    result = analyzer._live_prefilters("TESTUSDT")
    assert result["recent_stop_blocked"] is False
    assert result["recent_closed_blocked"] is False


def test_premium_base_candidate_prefers_classic_then_continuation(monkeypatch):
    classic = _signal("LONG", "15M_ENTRY")
    monkeypatch.setattr(analyzer.bot, "analyze_mtf_trade", lambda *args, **kwargs: classic)
    monkeypatch.setattr(
        analyzer.continuation,
        "analyze_continuation",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    result = analyzer._premium_base_candidate(
        None,
        "TESTUSDT",
        None,
        None,
        None,
        100.0,
        SimpleNamespace(),
    )
    assert result is classic


def test_premium_base_candidate_uses_continuation_when_classic_missing(monkeypatch):
    cont = _signal("LONG", analyzer.continuation.SOURCE)
    monkeypatch.setattr(analyzer.bot, "analyze_mtf_trade", lambda *args, **kwargs: None)
    monkeypatch.setattr(analyzer.continuation, "analyze_continuation", lambda *args, **kwargs: cont)
    result = analyzer._premium_base_candidate(
        None,
        "TESTUSDT",
        None,
        None,
        None,
        100.0,
        SimpleNamespace(),
    )
    assert result is cont
