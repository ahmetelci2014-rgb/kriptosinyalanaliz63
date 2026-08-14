import unittest

from dashboard_filter_app import (
    filter_dashboard_page,
    filter_requires_score,
    normalize_filter_key,
    normalize_sort_key,
    sort_requires_score,
)


class DashboardFilterV28Tests(unittest.TestCase):
    def test_filter_and_sort_keys_are_safe(self):
        self.assertEqual(normalize_filter_key("score80"), "score80")
        self.assertEqual(normalize_filter_key("UNKNOWN"), "all")
        self.assertEqual(normalize_sort_key("volume"), "volume")
        self.assertEqual(normalize_sort_key("oops"), "default")
        self.assertTrue(filter_requires_score("score80"))
        self.assertTrue(filter_requires_score("up"))
        self.assertFalse(filter_requires_score("active"))
        self.assertTrue(sort_requires_score("score"))
        self.assertFalse(sort_requires_score("change"))

    def test_member_page_adds_filter_toolbar_and_keeps_existing_layers(self):
        body = filter_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn('id="oppFilterBar"', body)
        self.assertIn('id="oppFilterSearch"', body)
        self.assertIn('data-filter="score80"', body)
        self.assertIn('data-filter="up"', body)
        self.assertIn('data-filter="down"', body)
        self.assertIn('data-filter="active"', body)
        self.assertIn('data-filter="volume"', body)
        self.assertIn('id="oppSort"', body)
        self.assertIn('value="score"', body)
        self.assertIn('value="change"', body)
        self.assertIn('value="volume"', body)
        self.assertIn("/api/market/analysis-score", body)
        self.assertIn('id="page-opportunities"', body)
        self.assertIn('id="page-watchlist"', body)
        self.assertIn('id="soundToggle"', body)
        self.assertIn('id="notifyDrawer"', body)
        self.assertIn('id="focusDrawer"', body)
        self.assertIn('nonce="nonce-test"', body)

    def test_page_keeps_score_disclaimer(self):
        body = filter_dashboard_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            "nonce-test",
        )
        self.assertIn("başarı olasılığı değildir", body)
        self.assertIn("80+ skor", body)
        self.assertIn("Hacim 1.5x+", body)

    def test_admin_keeps_management_and_advanced_links(self):
        body = filter_dashboard_page(
            {"username": "ahmet", "role": "ADMIN", "csrf": "csrf-admin"},
            "nonce-admin",
        )
        self.assertIn('href="/admin/users"', body)
        self.assertIn('href="/advanced"', body)
        self.assertIn('data-view="system"', body)


if __name__ == "__main__":
    unittest.main()
