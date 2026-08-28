from market_first_derivatives import (
    compact_confirmation,
    enrich_decision,
    fetch_derivatives_snapshot,
)


class FakeExchange:
    has = {
        "fetchOpenInterestHistory": True,
        "fetchFundingRate": True,
        "fetchTrades": True,
    }

    def fetch_open_interest_history(self, symbol, timeframe="5m", limit=5):
        assert timeframe == "5m"
        return [
            {"timestamp": 1_000, "openInterestValue": 100.0},
            {"timestamp": 301_000, "openInterestValue": 101.0},
            {"timestamp": 601_000, "openInterestValue": 102.0},
            {"timestamp": 901_000, "openInterestValue": 104.0},
        ]

    def fetch_funding_rate(self, symbol):
        return {
            "fundingRate": 0.0001,
            "fundingTimestamp": 1_000,
            "nextFundingTimestamp": 14_401_000,  # 4 hours later
        }

    def fetch_trades(self, symbol, limit=120):
        rows = []
        # More aggressive buy quote than sell quote.
        for i in range(8):
            rows.append(
                {
                    "timestamp": 1_000 + i * 1_000,
                    "side": "buy",
                    "amount": 2.0,
                    "price": 10.0,
                }
            )
        for i in range(4):
            rows.append(
                {
                    "timestamp": 10_000 + i * 1_000,
                    "side": "sell",
                    "amount": 1.0,
                    "price": 10.0,
                }
            )
        return rows


def test_derivatives_snapshot_normalizes_and_aligns_long():
    snapshot = fetch_derivatives_snapshot(FakeExchange(), "BTC/USDT:USDT", "LONG")
    assert snapshot.derivatives_available
    assert snapshot.oi_history_available
    assert snapshot.oi_change_5m_percent > 1.9
    assert snapshot.oi_change_15m_percent == 4.0
    assert snapshot.funding_available
    # 0.0001 per 4h -> 0.0002 equivalent per 8h -> 2 bps.
    assert snapshot.funding_rate_8h_bps == 2.0
    assert snapshot.taker_available
    assert snapshot.taker_imbalance > 0.5
    assert snapshot.soft_score > 0

    decision = {"direction": "LONG"}
    enrich_decision(decision, snapshot)
    assert decision["funding_crowding_8h_bps"] == 2.0
    assert decision["taker_imbalance_alignment"] > 0
    text = compact_confirmation(decision)
    assert "OI15" in text
    assert "Taker" in text
    assert "Funding" in text


def test_short_flips_directional_flow_and_funding_alignment():
    snapshot = fetch_derivatives_snapshot(FakeExchange(), "BTC/USDT:USDT", "SHORT")
    decision = {"direction": "SHORT"}
    enrich_decision(decision, snapshot)
    assert decision["funding_crowding_8h_bps"] == -2.0
    assert decision["taker_imbalance_alignment"] < 0


class PartialExchange:
    has = {
        "fetchOpenInterestHistory": False,
        "fetchFundingRate": True,
        "fetchTrades": False,
    }

    def fetch_funding_rate(self, symbol):
        return {"fundingRate": -0.00005}


def test_partial_api_failure_is_not_a_trade_block():
    snapshot = fetch_derivatives_snapshot(PartialExchange(), "X/USDT:USDT", "LONG")
    assert snapshot.derivatives_available
    assert not snapshot.oi_history_available
    assert snapshot.funding_available
    assert not snapshot.taker_available
    assert snapshot.soft_score == 0
    assert snapshot.errors
