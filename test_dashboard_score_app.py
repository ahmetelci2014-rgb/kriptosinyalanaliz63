import unittest

from dashboard_score_app import compute_analysis_score, score_dashboard_page


def candles_up(count=90, start=100.0, volume=100.0, spike=180.0):
    rows = []
    price = start
    for index in range(count):
        delta = 1.0 if index % 2 == 0 else -0.4
        previous = price
        price = max(1.0, price + delta)
        rows.append(
            {
                "ts": 1700000000 + index * 900,
                "open": previous,
                "high": max(previous, price) + 0.2,
                "low": min(previous, price) - 0.2,
                "close": price,
                "volume": spike if index == count - 1 else volume,
            }
        )
    return rows


def candles_down(count=90, start=160.0, volume=100.0, spike=180.0):
    rows = []
    price = start
    for index in range(count):
        delta = -1.0 if index % 2 == 0 else 0.4
        previous = price
        price = max(1.0, price + delta)
        rows.append(
            {
                "ts": 1700000000 + index * 900,
                "open": previous,
                "high": max(previous, price) + 0.2,
                "low": min(previous, price) - 0.2,
                "close": price,
                "volume": spike if index == count - 1 else volume,
            }
        )
    return rows


class DashboardScoreV27Tests(unittest.TestCase):
    def test_aligned_uptrend_gets_high_technical_alignment(self):
        result = compute_analysis_score(candles_up(), candles_up(start=200), 5.5)
        self.assertEqual(result["direction"], "YUKARI")
        self.assertGreaterEqual(result["score"], 80)
        self.assertEqual(result["components"]["trend"], 40)
        self.assertGreaterEqual(result["components"]["volume"], 16)
        self.assertIn("başarı ihtimali", result["note"].lower())

    def test_mixed_timeframes_score_lower_than_aligned(self):
        aligned = compute_analysis_score(candles_up(), candles_up(start=200), 4.0)
        mixed = compute_analysis_score(candles_up(), candles_down(start=200), 1.0)
        self.assertLess(mixed["score"], aligned["score"])
        self.assertEqual(mixed["components"]["trend"], 10)

    def test_aligned_downtrend_has_down_direction(self):
        result = compute_analysis_score(candles_down(), candles_down(start=240), -5.0)
        self.assertEqual(result["direction"], "AŞAĞI")
        self.assertGreaterEqual(result["components"]["trend"], 40)
        self.assertGreaterEqual(result["score"], 70)

    def test_page_keeps_v26_layers_and_adds_score_controls(self):
        body = score_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('id="page-opportunities"', body)
        self.assertIn('id="page-watchlist"', body)
        self.assertIn('id="scoreLoadAllBtn"', body)
        self.assertIn("/api/market/analysis-score", body)
        self.assertIn("İnceleme Skoru", body)
        self.assertIn("başarı ihtimali değildir", body)
        self.assertIn("score-chip", body)
        self.assertIn('id="soundToggle"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn('nonce="nonce-test"', body)

    def test_admin_keeps_system_and_user_management(self):
        body = score_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-admin"},
            "nonce-admin",
        )
        self.assertIn('data-view="system"', body)
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)


if __name__ == "__main__":
    unittest.main()
