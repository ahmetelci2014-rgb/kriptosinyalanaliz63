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
        "fetchOrderBook": True,
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
        # First half mildly sell-heavy, second half strongly buy-heavy.
        # This makes overall taker flow positive and CVD impulse positive.
        sides = ["buy"] * 4 + ["sell"] * 6 + ["buy"] * 8 + ["sell"] * 2
        for i, side in enumerate(sides):
            rows.append(
                {
                    "timestamp": 1_000 + i * 1_000,
                    "side": side,
                    "amount": 1.0,
                    "price": 10.0,
                }
            )
        return rows

    def fetch_order_book(self, symbol, limit=50):
        return {
            "bids": [
                [9.99, 100.0],
                [9.98, 90.0],
                [9.97, 80.0],
                [9.96, 70.0],
            ],
            "asks": [
                [10.01, 60.0],
                [10.02, 55.0],
                [10.03, 50.0],
                [10.04, 45.0],
            ],
        }


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
    assert snapshot.taker_imbalance > 0.15
    assert snapshot.cvd_available
    assert snapshot.cvd_impulse > 0.7
    assert snapshot.book_available
    assert snapshot.book_imbalance > 0.15
    assert snapshot.book_opposing_wall_ratio < 2.0
    assert snapshot.soft_score > 0

    decision = {"direction": "LONG"}
    enrich_decision(decision, snapshot)
    assert decision["funding_crowding_8h_bps"] == 2.0
    assert decision["taker_imbalance_alignment"] > 0
    assert decision["cvd_impulse_alignment"] > 0
    assert decision["book_imbalance_alignment"] > 0
    text = compact_confirmation(decision)
    assert "OI15" in text
    assert "Taker" in text
    assert "Funding" in text


def test_short_flips_directional_flow_and_book_alignment():
    snapshot = fetch_derivatives_snapshot(FakeExchange(), "BTC/USDT:USDT", "SHORT")
    decision = {"direction": "SHORT"}
    enrich_decision(decision, snapshot)
    assert decision["funding_crowding_8h_bps"] == -2.0
    assert decision["taker_imbalance_alignment"] < 0
    assert decision["cvd_impulse_alignment"] < 0
    assert decision["book_imbalance_alignment"] < 0


class PartialExchange:
    has = {
        "fetchOpenInterestHistory": False,
        "fetchFundingRate": True,
        "fetchTrades": False,
        "fetchOrderBook": False,
    }

    def fetch_funding_rate(self, symbol):
        return {"fundingRate": -0.00005}


def test_partial_api_failure_is_not_a_trade_block():
    snapshot = fetch_derivatives_snapshot(PartialExchange(), "X/USDT:USDT", "LONG")
    assert snapshot.derivatives_available
    assert not snapshot.oi_history_available
    assert snapshot.funding_available
    assert not snapshot.taker_available
    assert not snapshot.cvd_available
    assert not snapshot.book_available
    assert snapshot.soft_score == 0
    assert snapshot.errors
