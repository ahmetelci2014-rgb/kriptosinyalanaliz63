from __future__ import annotations

import market_first_audit_layer as audit


class DummyExchange:
    def __init__(self, markets=None, book=None):
        self._markets = markets or {}
        self._book = book or {}

    def load_markets(self):
        return self._markets

    def fetch_order_book(self, symbol, limit=30):
        return self._book


def test_strict_crypto_universe_filters_rows(monkeypatch):
    markets = {
        "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "swap": True},
        "AAPL/USDT:USDT": {"symbol": "AAPL/USDT:USDT", "swap": True},
    }
    exchange = DummyExchange(markets=markets)

    monkeypatch.setattr(audit.universe_guard, "refresh_account_tradable_futures_from_env", lambda: False)
    monkeypatch.setattr(
        audit.universe_guard,
        "filter_crypto_markets",
        lambda incoming: ({"BTC/USDT:USDT": incoming["BTC/USDT:USDT"]}, [{"reason": "RWA"}]),
    )
    monkeypatch.setattr(
        audit.universe_guard,
        "market_bot_symbol",
        lambda market, key="": "BTCUSDT" if str(market.get("symbol", "")).startswith("BTC") else "",
    )
    monkeypatch.setattr(audit.universe_guard, "ACCOUNT_ALLOWLIST_ACTIVE", False)

    rows = [
        {"symbol": "BTCUSDT", "quote_volume": 10_000_000},
        {"symbol": "AAPLUSDT", "quote_volume": 10_000_000},
    ]
    universe = {row["symbol"]: dict(row) for row in rows}

    kept_rows, kept_universe, summary = audit.strict_crypto_universe(exchange, rows, universe)

    assert [row["symbol"] for row in kept_rows] == ["BTCUSDT"]
    assert set(kept_universe) == {"BTCUSDT"}
    assert summary["excluded"] == 1
    assert audit.LIVE_CCXT_SYMBOLS["BTCUSDT"] == "BTC/USDT:USDT"


def test_deep_scan_prioritizes_fresh_moderate_movers():
    rows = [
        {"symbol": "FASTUSDT", "quote_volume": 3_000_000},
        {"symbol": "LATEUSDT", "quote_volume": 9_000_000},
        {"symbol": "THINUSDT", "quote_volume": 20_000},
    ]
    sample_moves = {"FASTUSDT": 0.55, "LATEUSDT": 4.8, "THINUSDT": 0.7}

    def original_selector(filtered_rows, moves, state):
        return ["LATEUSDT"]

    selected = audit.select_deep_scan(rows, sample_moves, {}, original_selector)

    assert selected[0] == "FASTUSDT"
    assert "LATEUSDT" in selected
    assert "THINUSDT" not in selected


def test_late_rescue_only_surfaces_fresh_breakout_as_early():
    decision = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "stage": "LATE",
        "score": 72,
        "move_3m_percent": 0.75,
        "move_5m_percent": 1.10,
        "breakout_20m": True,
        "extension_atr_5m": 3.2,
        "alert_eligible": False,
        "trade_eligible": False,
    }
    revised, reason = audit.revise_late_decision(decision, "OK")

    assert reason == "OK"
    assert revised["stage"] == "EARLY"
    assert revised["late_rescued"] is True
    assert revised["trade_eligible"] is False


def test_hard_late_move_is_not_rescued():
    decision = {
        "symbol": "TESTUSDT",
        "direction": "LONG",
        "stage": "LATE",
        "score": 90,
        "move_3m_percent": 3.5,
        "move_5m_percent": 4.8,
        "breakout_20m": True,
        "extension_atr_5m": 3.0,
    }
    revised, _ = audit.revise_late_decision(decision, "OK")
    assert revised["stage"] == "LATE"


def test_liquidity_guard_blocks_wide_spread():
    audit.LIVE_CCXT_SYMBOLS.clear()
    audit.LIVE_CCXT_SYMBOLS["TESTUSDT"] = "TEST/USDT:USDT"
    exchange = DummyExchange(
        book={
            "bids": [[100.0, 500.0], [99.9, 500.0]],
            "asks": [[100.6, 500.0], [100.7, 500.0]],
        }
    )
    result = audit.evaluate_liquidity(exchange, "TESTUSDT")
    assert result["available"] is True
    assert result["blocked"] is True
    assert result["reason"] == "SPREAD_TOO_WIDE"


def test_liquidity_guard_accepts_tight_deep_book():
    audit.LIVE_CCXT_SYMBOLS.clear()
    audit.LIVE_CCXT_SYMBOLS["TESTUSDT"] = "TEST/USDT:USDT"
    exchange = DummyExchange(
        book={
            "bids": [[100.00, 200.0], [99.95, 200.0]],
            "asks": [[100.02, 200.0], [100.07, 200.0]],
        }
    )
    result = audit.evaluate_liquidity(exchange, "TESTUSDT")
    assert result["available"] is True
    assert result["blocked"] is False
    assert result["reason"] == "LIQUIDITY_OK"


def test_ml_reconciliation_uses_net_r_after_costs():
    store = {
        "samples": {
            "trade1": {"resolved": False},
            "trade2": {"resolved": False},
        }
    }
    ledger = {
        "trades": {
            "trade1": {
                "final_result": "TP1_SONRASI_BE",
                "status": "CLOSED",
                "net_r_after_costs": -0.02,
                "r_result": 0.10,
                "closed_at": 10,
                "tp1_hit": True,
            },
            "trade2": {
                "final_result": "TP3",
                "status": "CLOSED",
                "net_r_after_costs": 0.70,
                "r_result": 1.0,
                "closed_at": 20,
                "tp1_hit": True,
            },
        }
    }

    changed = audit.reconcile_samples_net_r(store, ledger)

    assert changed == 2
    assert store["samples"]["trade1"]["label"] == 0
    assert store["samples"]["trade2"]["label"] == 1
    assert store["samples"]["trade1"]["label_target"] == "NET_R_AFTER_COSTS_POSITIVE"
