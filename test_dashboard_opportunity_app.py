import unittest

import dashboard_market_app as market
from dashboard_opportunity_app import build_opportunity_payload, opportunity_dashboard_page


class FakeOverviewClient(market.OKXMarketOverviewClient):
    def __init__(self, rows):
        super().__init__(cache_seconds=20)
        self.rows = rows

    def _all_tickers(self):
        return list(self.rows)


def ticker(base, last, open24h, volume):
    return {
        "instId": f"{base}-USDT-SWAP",
        "last": str(last),
        "open24h": str(open24h),
        "high24h": str(max(last, open24h) * 1.02),
        "low24h": str(min(last, open24h) * 0.98),
        "volCcy24h": str(volume),
        "ts": "1786730000000",
    }


class DashboardOpportunityV26Tests(unittest.TestCase):
    def test_opportunity_payload_groups_liquid_market_and_active_signal(self):
        rows = [
            ticker("BTC", 110, 100, 1000),
            ticker("ETH", 90, 100, 2000),
            ticker("SOL", 105, 100, 500),
            ticker("XRP", 101, 100, 3000),
        ]
        data = {
            "open_trades": [
                {"symbol": "BTCUSDT", "direction": "LONG", "system_label": "Premium"}
            ],
            "recent_results": [],
        }
        payload = build_opportunity_payload(
            FakeOverviewClient(rows), data, liquid_limit=20, per_group=3
        )

        self.assertEqual(payload["groups"]["rising"][0]["symbol"], "BTCUSDT")
        self.assertEqual(payload["groups"]["falling"][0]["symbol"], "ETHUSDT")
        self.assertEqual(payload["groups"]["active"][0]["symbol"], "BTCUSDT")
        self.assertEqual(payload["groups"]["active"][0]["kind"], "OPEN")
        self.assertEqual(payload["groups"]["active"][0]["direction"], "LONG")
        self.assertGreater(payload["groups"]["volume"][0]["turnover_24h_estimate"], 0)
        self.assertEqual(payload["summary"]["up"], 3)
        self.assertEqual(payload["summary"]["down"], 1)
        self.assertEqual(payload["summary"]["active_signals"], 1)
        self.assertEqual(payload["source"], "OKX_PUBLIC_NO_API_KEY")

    def test_member_page_keeps_existing_layers_and_adds_opportunity_center(self):
        body = opportunity_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('id="page-opportunities"', body)
        self.assertIn('data-view="opportunities"', body)
        self.assertIn("24s Yükselen Momentum", body)
        self.assertIn("24s Düşen Momentum", body)
        self.assertIn("Yaklaşık Hacim Liderleri", body)
        self.assertIn("Bizim Sistemde Aktif", body)
        self.assertIn("/api/market/opportunities", body)
        self.assertIn('id="page-watchlist"', body)
        self.assertIn('id="soundToggle"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn('nonce="nonce-test"', body)

    def test_admin_keeps_system_and_management_links(self):
        body = opportunity_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-admin"},
            "nonce-admin",
        )
        self.assertIn('data-view="system"', body)
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)
        self.assertIn('href="/market-center"', body)


if __name__ == "__main__":
    unittest.main()
