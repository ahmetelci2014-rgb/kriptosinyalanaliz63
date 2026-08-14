import unittest

from dashboard_focus_app import focus_dashboard_page
from dashboard_focus_market_app import market_page_with_selection


class FocusDashboardTests(unittest.TestCase):
    def test_focus_page_adds_quick_coin_drawer_without_removing_v2_navigation(self):
        session = {"username": "testuye", "role": "MEMBER", "csrf": "csrf-test"}
        body = focus_dashboard_page(session, "nonce-test")
        self.assertIn("Coin İncele", body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn("RSI 14", body)
        self.assertIn("EMA 20 / 50", body)
        self.assertIn("Hacim", body)
        self.assertIn("Bizim sistemdeki durum", body)
        self.assertIn("/api/market/candles", body)
        self.assertIn("/api/market/overview", body)
        self.assertIn("/api/dashboard", body)
        self.assertIn("localStorage", body)
        self.assertIn("Ana Sayfa", body)
        self.assertIn('nonce="nonce-test"', body)

    def test_market_page_opens_requested_symbol_and_bar(self):
        body = market_page_with_selection("nonce-x", "adausdt", "4H")
        self.assertIn('id="symbolInput" value="ADAUSDT"', body)
        self.assertIn("$('barSelect').value='4H'", body)
        self.assertIn("loadOverview('ADAUSDT').then(()=>loadChart('ADAUSDT'))", body)

    def test_market_page_falls_back_on_invalid_parameters(self):
        body = market_page_with_selection("nonce-x", "../bad", "99m")
        self.assertIn('id="symbolInput" value="BTCUSDT"', body)
        self.assertIn("$('barSelect').value='15m'", body)


if __name__ == "__main__":
    unittest.main()
