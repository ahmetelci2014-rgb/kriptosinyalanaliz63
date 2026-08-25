import smart_entry_historical_runner as runner


def test_default_symbols_are_major_usdt(monkeypatch):
    monkeypatch.delenv("SMART_ENTRY_SYMBOLS", raising=False)
    symbols = runner._symbols()
    assert "BTCUSDT" in symbols
    assert "ETHUSDT" in symbols
    assert all(symbol.endswith("USDT") for symbol in symbols)


def test_symbol_input_normalizes(monkeypatch):
    monkeypatch.setenv("SMART_ENTRY_SYMBOLS", "btc, ETH/USDT:USDT;solusdt")
    assert runner._symbols() == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
