import unittest

from dashboard_market_app import (
    OKXMarketOverviewClient,
    market_center_page,
    market_context,
    normalize_market_symbols,
    select_market_symbols,
)


class FakeOverviewClient(OKXMarketOverviewClient):
    def _all_tickers(self):
        return [
            {
                "instId": "BTC-USDT-SWAP",
                "last": "65000",
                "open24h": "64000",
                "high24h": "66000",
                "low24h": "63000",
                "volCcy24h": "123456",
                "ts": "1786720000000",
            },
            {
                "instId": "ADA-USDT-SWAP",
                "last": "0.45",
                "open24h": "0.50",
                "high24h": "0.51",
                "low24h": "0.44",
                "volCcy24h": "987654",
                "ts": "1786720000000",
            },
            {
                "instId": "BTC-USD-SWAP",
                "last": "65000",
                "open24h": "64000",
            },
        ]


class MarketCenterTests(unittest.TestCase):
    def test_normalize_market_symbols_deduplicates_and_rejects_invalid(self):
        self.assertEqual(
            normalize_market_symbols(["btcusdt", "BTC-USDT", "ADAUSDT", "../bad", "ADAUSDT"]),
            ["BTCUSDT", "ADAUSDT"],
        )

    def test_select_market_symbols_includes_open_and_recent_trades(self):
        data = {
            "open_trades": [{"symbol": "MOVEUSDT"}],
            "recent_results": [{"symbol": "MASKUSDT"}],
        }
        symbols = select_market_symbols(data)
        self.assertIn("BTCUSDT", symbols)
        self.assertIn("MOVEUSDT", symbols)
        self.assertIn("MASKUSDT", symbols)

    def test_market_context_prefers_open_trade(self):
        data = {
            "open_trades": [
                {"symbol": "ADAUSDT", "direction": "LONG", "system_label": "Premium MTF"}
            ],
            "recent_results": [
                {"symbol": "ADAUSDT", "direction": "SHORT", "system_label": "Scalp", "outcome": "TP1"},
                {"symbol": "BTCUSDT", "direction": "LONG", "system_label": "Premium MTF", "outcome": "TP2"},
            ],
        }
        context = market_context(data)
        self.assertEqual(context["ADAUSDT"]["kind"], "OPEN")
        self.assertEqual(context["ADAUSDT"]["direction"], "LONG")
        self.assertEqual(context["BTCUSDT"]["kind"], "RECENT")
        self.assertEqual(context["BTCUSDT"]["outcome"], "TP2")

    def test_overview_parses_usdt_swaps_and_preserves_requested_order(self):
        client = FakeOverviewClient()
        payload = client.get_overview(["ADAUSDT", "BTCUSDT", "SOLUSDT"])
        self.assertEqual([row["symbol"] for row in payload["items"]], ["ADAUSDT", "BTCUSDT"])
        self.assertEqual(payload["missing"], ["SOLUSDT"])
        self.assertAlmostEqual(payload["items"][0]["change_24h_pct"], -10.0, places=3)
        self.assertGreater(payload["items"][1]["change_24h_pct"], 1.5)

    def test_market_page_has_overview_and_candle_endpoints(self):
        body = market_center_page("nonce-test")
        self.assertIn("Canlı Piyasa Merkezi", body)
        self.assertIn("/api/market/overview", body)
        self.assertIn("/api/market/candles", body)
        self.assertIn('nonce="nonce-test"', body)
        self.assertIn("Emir açmaz", body)


if __name__ == "__main__":
    unittest.main()
