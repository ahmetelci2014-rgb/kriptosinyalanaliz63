import crypto_universe_guard as guard
import market_structure_shadow_runner as runner


class FakeExchange:
    def __init__(self):
        self._markets = {
            "BTC/USDT:USDT": {
                "symbol": "BTC/USDT:USDT",
                "id": "BTC-USDT-SWAP",
                "base": "BTC",
                "quote": "USDT",
                "settle": "USDT",
                "swap": True,
                "active": True,
                "info": {"instCategory": "1", "groupId": "4", "state": "live"},
            },
            "TSM/USDT:USDT": {
                "symbol": "TSM/USDT:USDT",
                "id": "TSM-USDT-SWAP",
                "base": "TSM",
                "quote": "USDT",
                "settle": "USDT",
                "swap": True,
                "active": True,
                "info": {"instCategory": "3", "groupId": "6", "state": "live"},
            },
            "RKLB/USDT:USDT": {
                "symbol": "RKLB/USDT:USDT",
                "id": "RKLB-USDT-SWAP",
                "base": "RKLB",
                "quote": "USDT",
                "settle": "USDT",
                "swap": True,
                "active": True,
                "info": {"instCategory": "3", "groupId": "6", "state": "live"},
            },
            "MU/USDT:USDT": {
                "symbol": "MU/USDT:USDT",
                "id": "MU-USDT-SWAP",
                "base": "MU",
                "quote": "USDT",
                "settle": "USDT",
                "swap": True,
                "active": True,
                "info": {"instCategory": "3", "groupId": "6", "state": "live"},
            },
            "LITE/USDT:USDT": {
                "symbol": "LITE/USDT:USDT",
                "id": "LITE-USDT-SWAP",
                "base": "LITE",
                "quote": "USDT",
                "settle": "USDT",
                "swap": True,
                "active": True,
                "info": {"instCategory": "3", "groupId": "6", "state": "live"},
            },
        }
        self._tickers = {
            symbol: {"last": 100.0, "quoteVolume": 5_000_000.0}
            for symbol in self._markets
        }

    def load_markets(self):
        return self._markets

    def fetch_tickers(self):
        return self._tickers


def test_market_structure_universe_is_crypto_only(monkeypatch):
    monkeypatch.delenv("OKX_API_KEY", raising=False)
    monkeypatch.delenv("OKX_SECRET_KEY", raising=False)
    monkeypatch.delenv("OKX_PASSPHRASE", raising=False)
    guard.clear_account_tradable_futures()
    guard.VERIFIED_LIVE_FUTURES_SYMBOLS.clear()

    universe = runner.build_universe(FakeExchange())
    symbols = [row[1] for row in universe]

    assert symbols == ["BTCUSDT"]
    assert "TSMUSDT" not in symbols
    assert "RKLBUSDT" not in symbols
    assert "MUUSDT" not in symbols
    assert "LITEUSDT" not in symbols
