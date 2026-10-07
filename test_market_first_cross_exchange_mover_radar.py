from __future__ import annotations

import market_first_cross_exchange_mover_radar as radar


def test_qnt_like_binance_only_mover_is_recorded_but_not_promoted_to_okx():
    snapshot = {
        "QNTUSDT": {
            "symbol": "QNTUSDT",
            "price": 172.5,
            "quote_volume": 1_900_000_000.0,
            "change_24h_percent": 58.1,
        }
    }
    result = radar.analyze_snapshot(
        snapshot,
        previous_prices={},
        okx_symbols={"BTCUSDT", "ETHUSDT"},
        now=123,
    )
    assert result["okx_priority_symbols"] == []
    assert len(result["external_only_movers"]) == 1
    row = result["external_only_movers"][0]
    assert row["symbol"] == "QNTUSDT"
    assert row["direction"] == "LONG"
    assert row["okx_tradable"] is False


def test_binance_mover_that_exists_on_okx_is_prioritized():
    snapshot = {
        "WLDUSDT": {
            "symbol": "WLDUSDT",
            "price": 0.56,
            "quote_volume": 50_000_000.0,
            "change_24h_percent": 9.2,
        },
        "QUIETUSDT": {
            "symbol": "QUIETUSDT",
            "price": 1.0,
            "quote_volume": 50_000_000.0,
            "change_24h_percent": 1.0,
        },
    }
    result = radar.analyze_snapshot(
        snapshot,
        previous_prices={},
        okx_symbols={"WLDUSDT", "BTCUSDT"},
        now=123,
    )
    assert result["okx_priority_symbols"] == ["WLDUSDT"]
    assert [row["symbol"] for row in result["movers"]] == ["WLDUSDT"]


def test_five_minute_sample_move_can_trigger_before_large_24h_move():
    snapshot = {
        "TESTUSDT": {
            "symbol": "TESTUSDT",
            "price": 102.0,
            "quote_volume": 5_000_000.0,
            "change_24h_percent": 3.0,
        }
    }
    result = radar.analyze_snapshot(
        snapshot,
        previous_prices={"TESTUSDT": 100.0},
        okx_symbols={"TESTUSDT"},
        now=123,
    )
    assert result["okx_priority_symbols"] == ["TESTUSDT"]
    assert result["movers"][0]["sample_move_percent"] == 2.0


def test_low_volume_noise_is_ignored():
    snapshot = {
        "NOISEUSDT": {
            "symbol": "NOISEUSDT",
            "price": 2.0,
            "quote_volume": 100_000.0,
            "change_24h_percent": 100.0,
        }
    }
    result = radar.analyze_snapshot(
        snapshot,
        previous_prices={},
        okx_symbols={"NOISEUSDT"},
        now=123,
    )
    assert result["movers"] == []


def test_install_prepends_cross_exchange_priority_without_bypassing_max_scan(monkeypatch):
    class Bot:
        @staticmethod
        def now_ts():
            return 123

    class Runner:
        MAX_DEEP_SCAN = 4
        MAJOR_WEIGHTS = {"BTCUSDT": 1}
        bot = Bot()

        @staticmethod
        def _select_deep_scan(rows, sample_moves, state):
            return ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"]

    runner = Runner()
    previous_installed = radar._INSTALLED
    radar._INSTALLED = False
    monkeypatch.setattr(
        radar,
        "scan",
        lambda okx_symbols, previous_state=None, now=None, opener=None: {
            "version": radar.VERSION,
            "fetch_ok": True,
            "movers": [],
            "okx_priority_symbols": ["QNTUSDT", "BTCUSDT"],
            "external_only_movers": [],
            "previous_prices": {},
        },
    )
    try:
        radar.install(runner)
        state = {}
        selected = runner._select_deep_scan(
            [{"symbol": "QNTUSDT"}, {"symbol": "AAAUSDT"}], {}, state
        )
        assert selected == ["QNTUSDT", "AAAUSDT", "BBBUSDT", "CCCUSDT"]
        assert "cross_exchange_mover" in state
    finally:
        radar._INSTALLED = previous_installed


def test_binance_451_backoff_skips_repeated_network_calls():
    called = False

    def opener(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("network should not be called during 451 backoff")

    result = radar.scan(
        {"BTCUSDT", "ETHUSDT"},
        previous_state={
            "previous_prices": {"BTCUSDT": 100.0},
            "blocked_until": 200,
        },
        now=100,
        opener=opener,
    )

    assert called is False
    assert result["fetch_ok"] is False
    assert result["error"] == "BINANCE_HTTP_451_BACKOFF"
    assert result["blocked_until"] == 200
    assert result["previous_prices"] == {"BTCUSDT": 100.0}
