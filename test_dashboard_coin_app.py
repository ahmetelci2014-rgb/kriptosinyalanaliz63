from __future__ import annotations

import unittest

import dashboard_coin_app as coin


class CoinSummaryTests(unittest.TestCase):
    def test_coin_summary_filters_and_aggregates_real_rows(self):
        data = {
            "open_trades": [
                {"symbol": "BTCUSDT", "direction": "LONG", "entry": 100, "tp1": 110, "tp2": 120, "tp3": 130, "sl": 95, "system": "Premium", "opened_at": 1000, "secret": "no"},
                {"symbol": "ETHUSDT", "direction": "SHORT", "entry": 20, "system": "Scalp"},
            ],
            "recent_results": [
                {"symbol": "BTCUSDT", "direction": "LONG", "outcome": "TP2", "system": "Premium", "net_r": 1.5, "closed_at": 3000},
                {"symbol": "BTCUSDT", "direction": "SHORT", "outcome": "SL", "system": "Premium", "net_r": -1.0, "closed_at": 2000},
                {"symbol": "BTCUSDT", "direction": "LONG", "outcome": "BE", "system": "Scalp", "net_r": 0.0, "closed_at": 1000},
                {"symbol": "ETHUSDT", "direction": "LONG", "outcome": "TP3", "system": "Premium", "net_r": 2.0, "closed_at": 4000},
            ],
        }
        payload = coin.build_coin_summary(data, "btc-usdt")
        self.assertEqual(payload["symbol"], "BTCUSDT")
        self.assertEqual(len(payload["open_trades"]), 1)
        self.assertNotIn("secret", payload["open_trades"][0])
        self.assertEqual(len(payload["results"]), 3)
        self.assertEqual(payload["performance"]["tp"], 1)
        self.assertEqual(payload["performance"]["sl"], 1)
        self.assertEqual(payload["performance"]["be"], 1)
        self.assertEqual(payload["performance"]["tp_rate_percent"], 50.0)
        self.assertAlmostEqual(payload["performance"]["net_r"], 0.5)
        self.assertIn("BTCUSDT", payload["available_symbols"])
        self.assertIn("ETHUSDT", payload["available_symbols"])

    def test_invalid_symbol_is_rejected(self):
        with self.assertRaises(ValueError):
            coin.build_coin_summary({}, "../../etc/passwd")


class CoinPageTests(unittest.TestCase):
    def test_page_contains_live_review_sections(self):
        body = coin.coin_center_page("nonce123", "SOLUSDT")
        self.assertIn("Coin İnceleme Merkezi", body)
        self.assertIn("SOLUSDT", body)
        self.assertIn('id="chart"', body)
        self.assertIn("/api/market/candles", body)
        self.assertIn("/api/market/analysis-score", body)
        self.assertIn("/api/coin-center/summary", body)
        self.assertIn('nonce="nonce123"', body)
        self.assertIn("emir açmaz", body)
        self.assertNotIn("GITHUB_PANEL_TOKEN", body)
        self.assertNotIn("password_hash", body)

    def test_invalid_initial_symbol_falls_back_to_btc(self):
        body = coin.coin_center_page("n", "not a symbol")
        self.assertIn('value="BTCUSDT"', body)

    def test_dashboard_shortcut_is_idempotent(self):
        base = '<a href="/market-center">Piyasayı incele</a><a class="nav-item" href="/market-center"><span>⌁</span><b>Piyasa</b></a>'
        once = coin.enhance_dashboard_shortcuts(base)
        twice = coin.enhance_dashboard_shortcuts(once)
        self.assertIn("Coin Merkezi", once)
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()
