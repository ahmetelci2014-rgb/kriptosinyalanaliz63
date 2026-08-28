from all_market_opportunity_prefilter import build_scan_universe


class FakeExchange:
    def __init__(self, fail_tickers=False):
        self.fail_tickers = fail_tickers
        self.markets = {
            "BTC/USDT:USDT": {
                "symbol": "BTC/USDT:USDT",
                "base": "BTC",
                "quote": "USDT",
                "settle": "USDT",
                "swap": True,
                "active": True,
            },
            "MOVE/USDT:USDT": {
                "symbol": "MOVE/USDT:USDT",
                "base": "MOVE",
                "quote": "USDT",
                "settle": "USDT",
                "swap": True,
                "active": True,
            },
            "THIN/USDT:USDT": {
                "symbol": "THIN/USDT:USDT",
                "base": "THIN",
                "quote": "USDT",
                "settle": "USDT",
                "swap": True,
                "active": True,
            },
            "SPOT/USDT": {
                "symbol": "SPOT/USDT",
                "base": "SPOT",
                "quote": "USDT",
                "spot": True,
                "swap": False,
                "active": True,
            },
        }

    def load_markets(self):
        return self.markets

    def fetch_tickers(self):
        if self.fail_tickers:
            raise RuntimeError("ticker unavailable")
        return {
            "MOVE/USDT:USDT": {
                "last": 1.08,
                "open": 1.0,
                "percentage": 8.0,
                "quoteVolume": 250_000,
            },
            "THIN/USDT:USDT": {
                "last": 2.5,
                "open": 1.0,
                "percentage": 150.0,
                "quoteVolume": 50_000,
            },
        }

    def fetch_ohlcv(self, symbol, timeframe="5m", limit=24):
        assert symbol == "MOVE/USDT:USDT"
        rows = []
        price = 100.0
        for idx in range(20):
            rows.append([idx, price, price + 0.1, price - 0.1, price, 100.0])
        rows.extend(
            [
                [20, 100.0, 100.4, 99.9, 100.35, 320.0],
                [21, 100.35, 100.9, 100.3, 100.85, 330.0],
                [22, 100.85, 101.5, 100.8, 101.45, 340.0],
                # still-forming candle: must be ignored by the prefilter
                [23, 101.45, 105.0, 99.0, 102.0, 9999.0],
            ]
        )
        return rows


def test_core_universe_is_preserved_and_active_excluded_mover_is_promoted():
    universe, meta = build_scan_universe(FakeExchange(), ["BTCUSDT"])

    assert universe[0] == "BTCUSDT"
    assert "MOVEUSDT" in universe
    assert "THINUSDT" not in universe
    assert meta["core_count"] == 1
    assert meta["active_usdt_swap_count"] == 3
    assert meta["excluded_count"] == 2
    assert meta["promoted_extra_count"] == 1
    assert meta["promoted_extras"][0]["symbol"] == "MOVEUSDT"


def test_prefilter_fails_open_to_existing_core_universe_when_tickers_fail():
    universe, meta = build_scan_universe(FakeExchange(fail_tickers=True), ["BTCUSDT"])

    assert universe == ["BTCUSDT"]
    assert meta["promoted_extra_count"] == 0
    assert any(item.startswith("fetch_tickers:") for item in meta["errors"])
