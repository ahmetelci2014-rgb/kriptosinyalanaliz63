import unittest

from dashboard_commercial_app import (
    PLAN_ADMIN,
    PLAN_FREE,
    PLAN_PREMIUM,
    VERSION,
    _plan_from_raw,
    build_public_summary,
    free_member_page,
    public_home_page,
    register_page,
)


class DashboardCommercialV30Tests(unittest.TestCase):
    def test_version(self):
        self.assertIn("V3_0_COMMERCIAL", VERSION)

    def test_legacy_member_stays_premium_when_not_expired(self):
        self.assertEqual(
            _plan_from_raw({"role": "MEMBER", "expires_at": None}, now=1_000),
            PLAN_PREMIUM,
        )
        self.assertEqual(
            _plan_from_raw({"role": "MEMBER", "expires_at": 2_000}, now=1_000),
            PLAN_PREMIUM,
        )

    def test_expired_premium_downgrades_to_free(self):
        self.assertEqual(
            _plan_from_raw(
                {"role": "MEMBER", "plan": "PREMIUM", "expires_at": 900},
                now=1_000,
            ),
            PLAN_FREE,
        )
        self.assertEqual(
            _plan_from_raw({"role": "MEMBER", "expires_at": 900}, now=1_000),
            PLAN_FREE,
        )

    def test_explicit_free_and_admin(self):
        self.assertEqual(
            _plan_from_raw({"role": "MEMBER", "plan": "FREE"}, now=1_000),
            PLAN_FREE,
        )
        self.assertEqual(
            _plan_from_raw({"role": "ADMIN", "plan": "FREE"}, now=1_000),
            PLAN_ADMIN,
        )

    def test_public_summary_is_aggregate_only(self):
        data = {
            "open_trades": [
                {"symbol": "BTCUSDT", "entry": 123, "tp1": 130, "sl": 120},
                {"symbol": "ETHUSDT", "entry": 456, "tp1": 470, "sl": 440},
            ],
            "recent_results": [
                {"symbol": "SECRET1USDT", "outcome": "TP3"},
                {"symbol": "SECRET2USDT", "outcome": "SL"},
                {"symbol": "SECRET3USDT", "outcome": "BE"},
            ],
            "health": {"overall": "GREEN"},
        }
        result = build_public_summary(data)
        self.assertEqual(result["open_count"], 2)
        self.assertEqual(result["tp_count"], 1)
        self.assertEqual(result["sl_count"], 1)
        self.assertEqual(result["be_count"], 1)
        self.assertEqual(result["tp_rate_percent"], 50.0)
        text = repr(result)
        self.assertNotIn("BTCUSDT", text)
        self.assertNotIn("SECRET1USDT", text)
        self.assertNotIn("entry", text.lower())
        self.assertNotIn("tp1", text.lower())

    def test_public_home_is_open_product_page(self):
        body = public_home_page("nonce-test")
        self.assertIn("Önce sistemi gör, sonra karar ver", body)
        self.assertIn("Ücretsiz başla", body)
        self.assertIn("/api/public/summary", body)
        self.assertIn("FREE", body)
        self.assertIn("PREMIUM", body)
        self.assertIn('nonce="nonce-test"', body)
        self.assertNotIn("PANEL_PASSWORD", body)
        self.assertNotIn("GITHUB_PANEL_TOKEN", body)

    def test_free_page_does_not_render_live_signal_levels(self):
        body = free_member_page(
            {"username": "uye", "role": "MEMBER", "csrf": "csrf-test"},
            {"plan": "FREE", "expires_at": None},
            "nonce-test",
        )
        self.assertIn("FREE · uye", body)
        self.assertIn("Premium araçlar", body)
        self.assertIn("/api/public/summary", body)
        self.assertIn("/api/market/overview", body)
        self.assertNotIn("/api/dashboard", body)
        self.assertNotIn("/api/market/analysis-score", body)
        self.assertIn('nonce="nonce-test"', body)

    def test_register_page_uses_csrf_and_minimum_password(self):
        body = register_page("csrf-register")
        self.assertIn('name="csrf" value="csrf-register"', body)
        self.assertIn('minlength="10"', body)
        self.assertIn('action="/register"', body)


if __name__ == "__main__":
    unittest.main()
